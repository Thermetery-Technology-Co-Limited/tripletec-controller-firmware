from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import usb_link
from gui.model import Snapshot


DEVICE = {"device": "COM8", "description": "TripleTEC", "serial_number": "UID123",
          "vid": usb_link.USB_VID, "pid": usb_link.USB_PID}


class FakeSerial:
    def __init__(self):
        self.words = [0] * 40
        self.words[:6] = [0x54454331, 2, 2, 1000, 1, 63]
        self.words[8:15] = [25000, 60000, 17000, 20000, 25000, 25000, 25000]
        self.words[32:40] = [0, 3000, 0, 0, 0, 0, 0, 0]
        self.adc = [0x54454344, 4, 1, 1000, 1489, 3300, 3, 0]
        self.limits = [0x5445434C, 4, 1, 1000, 0, 9000, 9000, 0]
        self.pending = b""
        self.writes = []
        self.closed = False
        self.transform = lambda frame, sequence: frame
        self.short_write = False
        self.reject = 0
        self.is_open = False
        self.port = None
        self.events = []
        self._dtr = None

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        self._dtr = value
        self.events.append(("dtr", value, self.is_open))

    def open(self):
        self.is_open = True
        self.events.append(("open", self.port))

    def reset_input_buffer(self):
        self.pending = b""

    def frame(self, sequence, result=0, version=1, size=224):
        header = usb_link.RESPONSE.pack(b"TECR", version, result, size, sequence)
        payload = struct.pack("<40I", *self.words) + struct.pack("<8I", *self.adc) + struct.pack("<8I", *self.limits)
        body = header + payload
        return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)

    def write(self, packet):
        self.writes.append(packet)
        magic, version, opcode, reserved, sequence, value, checksum = struct.unpack("<4sBBHIiI", packet)
        assert magic == b"TECQ" and version == 1 and reserved == 0
        assert checksum == zlib.crc32(packet[:16]) & 0xFFFFFFFF
        result = self.reject if opcode else 0
        if opcode == 4 and self.words[4]:
            result = 5
        if not result:
            if opcode == 1:
                self.words[34] = 0
                self.words[24:27] = [0, 0, 0]
            elif opcode == 2:
                self.words[32] = value
            elif opcode == 3:
                self.words[33] = value
        self.pending = self.transform(self.frame(sequence, result), sequence)
        return len(packet) - 1 if self.short_write else len(packet)

    def read(self, count):
        # Deliberately fragment every frame, including headers and CRC fields.
        data, self.pending = self.pending[:min(count, 7)], self.pending[min(count, 7):]
        return data

    def close(self):
        self.events.append(("close",))
        self.is_open = False
        self.closed = True


class UsbLinkTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSerial()
        self.devices = patch.object(usb_link, "list_usb_devices", return_value=[DEVICE])
        self.devices.start()
        self.addCleanup(self.devices.stop)
        self.serial = SimpleNamespace(Serial=lambda *args, **kwargs: self.fake)
        self.module = patch.object(usb_link, "_serial_module", return_value=self.serial)
        self.module.start()
        self.addCleanup(self.module.stop)
        self.sleep = patch.object(usb_link.time, "sleep")
        self.mock_sleep = self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.link = usb_link.UsbLink(serial_number="UID123")
        self.addCleanup(self.link.close)

    def test_fragmented_status_preserves_bench_and_current_limits(self):
        status = self.link.read_status()
        self.assertTrue(status["outputs_locked_off"])
        self.assertEqual(status["current_limits_a"], [0, 9, 9])
        self.assertEqual(status["current_limits_commissioned_mask"], 0)
        self.assertTrue(status["adc_diagnostics"]["valid"])
        self.assertEqual(status["usb_serial"], "UID123")
        self.assertEqual(status["transport"], "usb")
        self.assertEqual(len(self.fake.writes), 1)

    def test_open_starts_dtr_low_then_asserts_it_after_settling(self):
        self.assertEqual(self.fake.events, [("dtr", False, False), ("open", "COM8"),
                                            ("dtr", True, True)])
        self.assertEqual(self.mock_sleep.call_count, 2)
        self.mock_sleep.assert_called_with(usb_link.SESSION_SETTLE_SECONDS)
        self.link.close()
        self.assertEqual(self.fake.events[-2:], [("dtr", False, True), ("close",)])

    def test_dtr_open_failure_closes_and_does_not_send_a_request(self):
        class BrokenDtr(FakeSerial):
            @FakeSerial.dtr.setter
            def dtr(self, value):
                if value:
                    raise OSError("DTR failed")
                FakeSerial.dtr.fset(self, value)
        fake = BrokenDtr()
        with patch.object(usb_link, "_serial_module", return_value=SimpleNamespace(Serial=lambda **kw: fake)):
            with self.assertRaisesRegex(RuntimeError, "DTR failed"):
                usb_link.UsbLink()
        self.assertTrue(fake.closed)
        self.assertEqual(fake.writes, [])

    def test_mode_margin_and_stop_validate_echoed_state(self):
        self.assertEqual(self.link.command("mode", 1)["mode"], 1)
        self.assertEqual(self.link.command("margin", 4500)["margin_c"], 4.5)
        self.fake.words[34] = 7
        self.fake.words[24:27] = [100, 100, 100]
        stopped = self.link.command("stop")
        self.assertEqual(stopped["armed_mask"], 0)
        self.assertEqual(stopped["applied_duty"], [0, 0, 0])

    def test_arm_rejection_does_not_disconnect_or_retry(self):
        with self.assertRaisesRegex(RuntimeError, "BENCH_LOCKED"):
            self.link.command("arm", 7)
        self.assertEqual(len(self.fake.writes), 1)
        self.assertFalse(self.fake.closed)
        self.link.command("stop")

    def test_polling_only_sends_heartbeat_when_armed(self):
        self.link.read_status()
        self.assertEqual([packet[5] for packet in self.fake.writes], [0])
        self.fake.words[34] = 1
        self.link.read_status()
        self.assertEqual([packet[5] for packet in self.fake.writes], [0, 0, 5])

    def test_stale_response_is_ignored_without_resending(self):
        self.fake.transform = lambda frame, seq: self.fake.frame(seq - 1) + frame
        self.link.read_status()
        self.assertEqual(len(self.fake.writes), 1)

    def test_leading_garbage_resynchronizes(self):
        self.fake.transform = lambda frame, seq: b"old\x00garbage" + frame
        self.link.read_status()

    def test_bad_crc_closes_and_never_replays_command(self):
        self.fake.transform = lambda frame, seq: frame[:-1] + bytes([frame[-1] ^ 1])
        with self.assertRaisesRegex(RuntimeError, "checksum failed"):
            self.link.command("mode", 1)
        self.assertTrue(self.fake.closed)
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            self.link.command("mode", 1)
        self.assertEqual(len(self.fake.writes), 1)

    def test_truncated_and_only_stale_frames_time_out_without_retry(self):
        for transform in (lambda frame, seq: frame[:37],
                          lambda frame, seq: self.fake.frame(seq - 1)):
            with self.subTest(transform=transform):
                fake = FakeSerial()
                fake.transform = transform
                with patch.object(usb_link, "_serial_module", return_value=SimpleNamespace(Serial=lambda *a, **kw: fake)):
                    link = usb_link.UsbLink()
                with patch.object(usb_link, "READ_TIMEOUT", 0.01):
                    with self.assertRaisesRegex(RuntimeError, "timed out"):
                        link.read_status()
                self.assertTrue(fake.closed)
                self.assertEqual(len(fake.writes), 1)

    def test_unsupported_header_is_rejected(self):
        self.fake.transform = lambda frame, seq: self.fake.frame(seq, version=2)
        with self.assertRaisesRegex(RuntimeError, "unsupported protocol"):
            self.link.read_status()
        self.assertTrue(self.fake.closed)

    def test_success_ack_must_echo_requested_setting(self):
        def wrong_mode(frame, sequence):
            self.fake.words[32] = 0
            return self.fake.frame(sequence)
        self.fake.transform = wrong_mode
        with self.assertRaisesRegex(RuntimeError, "requested mode"):
            self.link.command("mode", 1)

        def missing_margin(frame, sequence):
            self.fake.words[33] = 0x80000000
            return self.fake.frame(sequence)
        self.fake.transform = missing_margin
        with self.assertRaisesRegex(RuntimeError, "requested margin"):
            self.link.command("margin", 4000)

    def test_sequence_wrap_never_uses_zero(self):
        self.link.sequence = 0xFFFFFFFF
        self.link.read_status()
        self.assertEqual(struct.unpack_from("<I", self.fake.writes[0], 8)[0], 1)

    def test_bad_adc_block_preserves_monitoring_and_stop(self):
        self.fake.adc[0] = 0
        self.assertFalse(self.link.read_status()["adc_diagnostics"]["valid"])
        self.assertEqual(self.link.command("stop")["armed_mask"], 0)

    def test_short_write_is_uncertain_and_not_repeated(self):
        self.fake.short_write = True
        with self.assertRaisesRegex(RuntimeError, "outcome is uncertain"):
            self.link.command("stop")
        self.assertTrue(self.fake.closed)
        self.assertEqual(len(self.fake.writes), 1)

    def test_invalid_policy_blocks_start_but_preserves_stop(self):
        self.fake.words[4] = 0
        self.fake.limits[5] = 10000
        status = self.link.read_status()
        self.assertFalse(status["current_limit_policy"]["valid"])
        self.assertNotIn("current_limits_a", status)
        self.assertIn("trip settings are unavailable", Snapshot.from_status(status, 1).start_block_reason(True, 1))
        self.assertEqual(self.link.command("stop")["armed_mask"], 0)

    def test_stale_policy_is_unavailable(self):
        self.fake.limits[3] = 4000
        status = self.link.read_status()
        self.assertFalse(status["current_limit_policy"]["valid"])
        self.assertNotIn("current_limits_a", status)

    def test_odd_telemetry_sequence_is_not_accepted(self):
        self.fake.words[1] = 3
        with self.assertRaisesRegex(RuntimeError, "being updated"):
            self.link.read_status()
        self.assertTrue(self.fake.closed)

    def test_invalid_commands_never_write(self):
        for name, value in (("status", 0), ("arm", 0), ("arm", 8), ("mode", 2),
                            ("margin", 499), ("stop", 1), ("heartbeat", 1), ("mode", True)):
            with self.subTest(name=name, value=value):
                with self.assertRaises(RuntimeError):
                    self.link.command(name, value)
        self.assertEqual(self.fake.writes, [])

    def test_selection_filters_uid_and_port_and_rejects_ambiguity(self):
        with patch.object(usb_link, "list_usb_devices", return_value=[DEVICE, {**DEVICE, "device": "COM9", "serial_number": "UID999"}]):
            with self.assertRaisesRegex(RuntimeError, "Multiple"):
                usb_link.UsbLink()
            selected = usb_link.UsbLink(port="COM9", serial_number="UID999")
            self.assertEqual(selected.port, "COM9")
            selected.close()
            with self.assertRaisesRegex(RuntimeError, "not found"):
                usb_link.UsbLink(port="COM9", serial_number="UID123")


if __name__ == "__main__":
    unittest.main()
