import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import device_link


class FakeOpenOCD:
    def __init__(self):
        self.words = [0] * 40
        self.words[:5] = [0x54454331, 2, 2, 1000, 1]
        self.words[32:40] = [0, 3000, 0, 0, 0, 0, 0, 0]
        self.mailbox = struct.pack("<8I", device_link.MAILBOX_MAGIC, 1, 0, 0, 0, 0, 0, 0)
        self.adc_words = [0x54454344, 4, 1, 1000, 1489, 3300, 3, 0]
        self.limit_words = [0x5445434C, 4, 1, 1000, 0, 9000, 9000, 0]
        self.commission_words = [0x5445434D, 4, 1, 1000, 0, 16, 0, 0, 0, 0,
                                 0, 0, 0, 1000, 1000, 20, 0, 0x80000000, 0, 0]
        self.commands = []
        self.options = []
        self.reject = 0

    def __call__(self, arguments, **options):
        self.options.append(options)
        commands = [arguments[index + 1] for index, item in enumerate(arguments) if item == "-c"]
        self.commands.extend(commands)
        for command in commands:
            match = re.fullmatch(r"dump_image \{(.+)\} (0x[0-9a-f]+) (\d+)", command)
            if match:
                path, address, size = match.groups()
                address = int(address, 16)
                if address == 0x20000200:
                    data = self.mailbox
                elif address == 0x20000300:
                    data = struct.pack("<8I", *self.adc_words)
                elif address == 0x20000400:
                    data = struct.pack("<8I", *self.limit_words)
                elif address == 0x20000500:
                    data = struct.pack("<20I", *self.commission_words)
                else:
                    data = struct.pack("<40I", *self.words)
                Path(path).write_bytes(data[:int(size)])
            match = re.fullmatch(r"load_image \{(.+)\} 0x20000200 bin", command)
            if match:
                self.mailbox = Path(match[1]).read_bytes()
            match = re.fullmatch(r"mww 0x20000208 (0x[0-9a-f]+|0)", command)
            if match:
                sequence = int(match[1], 0)
                mailbox = list(struct.unpack("<8I", self.mailbox))
                mailbox[2] = sequence
                self.mailbox = struct.pack("<8I", *mailbox)
                if sequence:
                    assert zlib.crc32(self.mailbox[:28]) & 0xFFFFFFFF == mailbox[7]
                    self.words[38:40] = [sequence, self.reject]
                    if not self.reject:
                        if mailbox[3] == 2:
                            self.words[32] = mailbox[4]
                        elif mailbox[3] == 3:
                            self.words[33] = mailbox[4]
                        elif mailbox[3] == 1:
                            self.words[34] = 0
                        elif mailbox[3] == 6:
                            self.words[4] = mailbox[4]
                            self.words[34] = 0
                            self.words[24:27] = [0, 0, 0]
                        elif mailbox[3] == 7:
                            self.commission_words[4:11] = [1, 21, 15000, 2040, 2049, 2040, 128]
                            self.limit_words[4] = 15000
                        elif mailbox[3] == 8:
                            self.commission_words[4:12] = [3 if mailbox[4] == 1 else 5,
                                                          21 if mailbox[4] in (0, 4) else 23 if mailbox[4] == 1 else 31,
                                                          15000, 2040, 2321, 2040, 256, 1]
                            self.commission_words[13] = 30000 if mailbox[4] in (3, 4) else 1000
                            self.commission_words[14] = 5000 if mailbox[4] in (3, 4) else 1000
                            self.commission_words[15] = 12 if mailbox[4] in (3, 4) else 20
                            self.commission_words[16] = self.commission_words[13] if mailbox[4] in (2, 3, 4) else 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class DeviceLinkTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        build = self.root / "build/stm32"
        build.mkdir(parents=True)
        self.metadata = build / "telemetry-symbol.json"
        self.metadata.write_text(json.dumps({"address": 0x20000000, "size": 160, "schema": 2,
                                             "mailbox_address": 0x20000200, "mailbox_size": 32}))
        self.link = device_link.DeviceLink(self.root, "PROBE123", "openocd-mocked")
        self.fake = FakeOpenOCD()

    def test_read_is_mem_ap_only_and_bounded(self):
        with patch.object(device_link.subprocess, "run", self.fake):
            status = self.link.read_status()
            self.assertEqual(status["margin_c"], 3)
            self.assertNotIn("adc_diagnostics", status)
            self.assertNotIn("current_limits_a", status)
        self.assertIn("target create status.mem mem_ap -dap status.dap -ap-num 0", self.fake.commands)
        self.assertFalse(any(re.search(r"\b(halt|reset|program)\b", command) for command in self.fake.commands
                             if not command.startswith("reset_config")))
        self.assertEqual(self.fake.options[0]["timeout"], 5)
        if sys.platform == "win32":
            self.assertEqual(self.fake.options[0]["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_optional_adc_metadata_reads_separate_diagnostic_block(self):
        metadata = json.loads(self.metadata.read_text())
        metadata.update(adc_diagnostics_address=0x20000300, adc_diagnostics_size=32)
        self.metadata.write_text(json.dumps(metadata))
        with patch.object(device_link.subprocess, "run", self.fake):
            result = self.link.read_status()
        self.assertEqual(result["margin_c"], 3)
        diagnostic = result["adc_diagnostics"]
        self.assertTrue(diagnostic["valid"])
        self.assertTrue(diagnostic["acquisition_ok"])
        self.assertTrue(diagnostic["reference_plausible"])
        self.assertEqual(diagnostic["vrefint_adc_raw"], 1489)
        self.assertEqual(diagnostic["estimated_vdda_v"], 3.3)
        self.assertEqual(diagnostic["adc_errors"], 0)
        dumps = [command for command in self.fake.commands
                 if command.startswith("dump_image") and command.endswith("0x20000300 32")]
        self.assertEqual(len(dumps), 4)
        self.assertFalse(any(command.startswith(("mww", "load_image")) for command in self.fake.commands))

    def test_invalid_adc_identity_preserves_status_and_stop(self):
        metadata = json.loads(self.metadata.read_text())
        metadata.update(adc_diagnostics_address=0x20000300, adc_diagnostics_size=32)
        self.metadata.write_text(json.dumps(metadata))
        for index, bad_value in ((0, 0), (2, 2)):
            with self.subTest(word=index):
                self.fake = FakeOpenOCD()
                self.fake.adc_words[index] = bad_value
                with patch.object(device_link.subprocess, "run", self.fake):
                    result = self.link.read_status()
                    self.assertEqual(result["schema"], 2)
                    self.assertEqual(result["margin_c"], 3)
                    self.assertFalse(result["adc_diagnostics"]["valid"])
                    self.assertIn("ADC", result["adc_diagnostics"]["error"])
                    self.fake.words[34] = 7
                    stopped = self.link.command("stop")
                self.assertEqual(stopped["armed_mask"], 0)
                self.assertEqual(stopped["command_ack_seq"], 1)
                self.assertFalse(stopped["adc_diagnostics"]["valid"])
                self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3], 1)

    def test_bad_adc_address_prevents_probe_access(self):
        metadata = json.loads(self.metadata.read_text())
        for address in (0x1FFFFFFC, 0x2000BFE4, 0x2000C000, 0x40000000, 0x20000301):
            with self.subTest(address=hex(address)):
                self.metadata.write_text(json.dumps({**metadata,
                    "adc_diagnostics_address": address, "adc_diagnostics_size": 32}))
                with patch.object(device_link.subprocess, "run", self.fake):
                    with self.assertRaisesRegex(RuntimeError, "SRAM"):
                        self.link.read_status()
                    with self.assertRaisesRegex(RuntimeError, "SRAM"):
                        self.link.command("stop")
        self.assertEqual(self.fake.commands, [])

    def test_mode_command_commits_sequence_last_and_checks_ack(self):
        with patch.object(device_link.subprocess, "run", self.fake):
            result = self.link.command("mode", 1)
        self.assertEqual(result["mode_name"], "unregulated")
        self.assertEqual(result["command_ack_seq"], 1)
        writes = [command for command in self.fake.commands if command.startswith(("mww", "load_image"))]
        self.assertEqual(writes[0], "mww 0x20000208 0")
        self.assertTrue(writes[1].startswith("load_image"))
        self.assertEqual(writes[2], "mww 0x20000208 0x1")

    def test_optional_limit_metadata_reports_actual_uncommissioned_thresholds(self):
        metadata = json.loads(self.metadata.read_text())
        metadata.update(limit_configuration_address=0x20000400, limit_configuration_size=32)
        self.metadata.write_text(json.dumps(metadata))
        with patch.object(device_link.subprocess, "run", self.fake):
            result = self.link.read_status()
        self.assertEqual(result["current_limits_a"], [0, 9, 9])
        self.assertEqual(result["current_limits_commissioned_mask"], 0)
        self.assertTrue(result["current_limit_policy"]["valid"])
        dumps = [command for command in self.fake.commands
                 if command.startswith("dump_image") and command.endswith("0x20000400 32")]
        self.assertEqual(len(dumps), 4)
        self.assertFalse(any(command.startswith(("mww", "load_image")) for command in self.fake.commands))

    def test_bad_limit_policy_disables_metadata_without_blocking_stop(self):
        metadata = json.loads(self.metadata.read_text())
        metadata.update(limit_configuration_address=0x20000400, limit_configuration_size=32)
        self.metadata.write_text(json.dumps(metadata))
        for index, value in ((0, 0), (2, 2), (3, 99999), (5, 10000), (7, 1)):
            with self.subTest(index=index, value=value):
                self.fake = FakeOpenOCD()
                self.fake.limit_words[index] = value
                with patch.object(device_link.subprocess, "run", self.fake):
                    result = self.link.read_status()
                    self.assertNotIn("current_limits_a", result)
                    self.assertFalse(result["current_limit_policy"]["valid"])
                    stopped = self.link.command("stop")
                    self.assertEqual(stopped["armed_mask"], 0)
                    self.assertNotIn("current_limits_a", stopped)

    def test_bad_limit_address_prevents_probe_access(self):
        metadata = json.loads(self.metadata.read_text())
        metadata.update(limit_configuration_address=0x40000000, limit_configuration_size=32)
        self.metadata.write_text(json.dumps(metadata))
        with patch.object(device_link.subprocess, "run", self.fake):
            with self.assertRaisesRegex(RuntimeError, "SRAM"):
                self.link.read_status()
        self.assertEqual(self.fake.commands, [])

    def test_margin_stop_and_locked_arm(self):
        with patch.object(device_link.subprocess, "run", self.fake):
            self.assertEqual(self.link.command("margin", 4500)["margin_c"], 4.5)
            self.assertEqual(self.link.command("stop")["armed_mask"], 0)
            self.fake.reject = 5
            with self.assertRaisesRegex(RuntimeError, "BENCH_LOCKED"):
                self.link.command("arm", 7)

    def test_bench_lock_both_transitions_stop_without_arming_or_changing_policy(self):
        self.fake.words[5] |= 0x40
        original_limits = self.fake.limit_words.copy()
        for value in (0, 1):
            with self.subTest(value=value):
                self.fake.words[34] = 7
                self.fake.words[24:27] = [100, 200, 300]
                with patch.object(device_link.subprocess, "run", self.fake):
                    result = self.link.command("bench_lock", value)
                self.assertIs(result["outputs_locked_off"], bool(value))
                self.assertTrue(result["bench_lock_supported"])
                self.assertEqual(result["armed_mask"], 0)
                self.assertEqual(result["applied_duty"], [0, 0, 0])
                self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3:5], (6, value))
        self.assertEqual(self.fake.limit_words, original_limits)
        self.assertEqual(sum(command.startswith("load_image") for command in self.fake.commands), 2)

    def test_bench_lock_without_capability_writes_nothing(self):
        with patch.object(device_link.subprocess, "run", self.fake):
            with self.assertRaisesRegex(RuntimeError, "does not support changing"):
                self.link.command("bench_lock", 0)
        self.assertFalse(any(command.startswith(("mww", "load_image")) for command in self.fake.commands))

    def test_bench_lock_rejects_non_integer_and_out_of_range_before_probe_access(self):
        for value in (-1, 2, True, False, 0.0, "0", None):
            with self.subTest(value=value), patch.object(device_link.subprocess, "run", self.fake):
                with self.assertRaises(RuntimeError):
                    self.link.command("bench_lock", value)
        self.assertEqual(self.fake.commands, [])

    def test_bench_lock_ack_requires_lock_echo_and_stopped_outputs_for_both_states(self):
        before = {"schema": 2, "valid_flags": 0x40, "command_ack_seq": 0}
        for value in (0, 1):
            for wrong in ({"outputs_locked_off": not bool(value)}, {"armed_mask": 1},
                          {"applied_duty": [0, 0.001, 0]}, {"valid_flags": 0},
                          {"applied_duty": []}):
                ack = {"schema": 2, "valid_flags": 0x40, "command_ack_seq": 1,
                       "command_result": 0, "outputs_locked_off": bool(value),
                       "armed_mask": 0, "applied_duty": [0, 0, 0], **wrong}
                with self.subTest(value=value, wrong=wrong), patch.object(self.link, "_read",
                        side_effect=[(before, self.fake.mailbox), (ack, None)]), patch.object(self.link, "_run") as run:
                    with self.assertRaises(RuntimeError):
                        self.link.command("bench_lock", value)
                    self.assertEqual(run.call_count, 1)

    def test_bench_lock_uncertain_write_is_never_replayed(self):
        before = {"schema": 2, "valid_flags": 0x40, "command_ack_seq": 0}
        with patch.object(self.link, "_read", return_value=(before, self.fake.mailbox)), \
                patch.object(self.link, "_run", side_effect=RuntimeError("uncertain")) as run:
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                self.link.command("bench_lock", 0)
        self.assertEqual(run.call_count, 1)

    def test_wrong_mailbox_identity_prevents_every_write(self):
        self.fake.mailbox = bytes(32)
        with patch.object(device_link.subprocess, "run", self.fake):
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                self.link.command("mode", 1)
        self.assertFalse(any(command.startswith(("mww", "load_image")) for command in self.fake.commands))

    def test_legacy_schema_is_read_only(self):
        self.fake.words[2] = 1
        with patch.object(device_link.subprocess, "run", self.fake):
            self.assertEqual(self.link.read_status()["schema"], 1)
            with self.assertRaisesRegex(RuntimeError, "read-only schema 1"):
                self.link.command("stop")
        self.assertFalse(any(command.startswith(("mww", "load_image")) for command in self.fake.commands))

    def test_bad_inputs_and_addresses_prevent_probe_access(self):
        with patch.object(device_link.subprocess, "run", self.fake):
            for name, value in (("mode", 2), ("margin", 499), ("arm", 8), ("stop", 1)):
                with self.assertRaises(RuntimeError):
                    self.link.command(name, value)
            self.metadata.write_text(json.dumps({"address": 0x40000000, "size": 160}))
            with self.assertRaisesRegex(RuntimeError, "SRAM"):
                self.link.read_status()
        self.assertEqual(self.fake.commands, [])

    def test_timeout_is_friendly_and_does_not_retry_uncertain_write(self):
        with patch.object(device_link.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("openocd", 5)) as run:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.link.read_status()
        self.assertEqual(run.call_count, 1)

    def test_armed_status_poll_refreshes_lease(self):
        self.fake.words[34] = 1
        with patch.object(device_link.subprocess, "run", self.fake):
            result = self.link.read_status()
        self.assertEqual(result["command_ack_seq"], 1)
        self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3], 5)

    def test_wrong_echo_is_rejected_even_with_success_ack(self):
        with patch.object(self.link, "_read", side_effect=[
            ({"schema": 2, "command_ack_seq": 0}, self.fake.mailbox),
            ({"schema": 2, "command_ack_seq": 1, "command_result": 0, "mode": 0}, None),
        ]), patch.object(self.link, "_run"):
            with self.assertRaisesRegex(RuntimeError, "echo"):
                self.link.command("mode", 1)

    def enable_commissioning(self):
        metadata = json.loads(self.metadata.read_text())
        metadata.update(commissioning_address=0x20000500, commissioning_size=80)
        self.metadata.write_text(json.dumps(metadata))
        self.fake.words[5] = 0xE7
        self.fake.words[11:13] = [22000, 27000]
        self.link._record_commissioning = lambda status: None

    def test_optional_commissioning_reads_four_validated_snapshots(self):
        self.enable_commissioning()
        with patch.object(device_link.subprocess, "run", self.fake):
            result = self.link.read_status()
        self.assertTrue(result["commissioning"]["valid"])
        self.assertEqual(result["commissioning"]["state"], 0)
        self.assertIsNone(result["commissioning"]["min_plate_c"])
        self.assertEqual(sum(command.endswith("0x20000500 80") for command in self.fake.commands), 4)
        self.assertFalse(any(command.startswith(("mww", "load_image")) for command in self.fake.commands))

    def test_legacy_debug_pulse_cannot_bypass_commissioning_firmware(self):
        self.enable_commissioning()
        with patch.object(device_link.subprocess, "run", self.fake), \
                patch("scripts.tec1_pulse.run_tec1_pulse") as pulse:
            with self.assertRaisesRegex(RuntimeError, "current-protected commissioning"):
                self.link.test_tec1_pulse()
            pulse.assert_not_called()
        self.assertFalse(any(command.startswith(("mww", "load_image")) for command in self.fake.commands))

    def test_commissioning_prepare_verify_and_run_are_separate_acknowledged_commands(self):
        self.enable_commissioning()
        with patch.object(device_link.subprocess, "run", self.fake):
            result = self.link.command("commission_prepare", 15000)
            self.assertTrue(result["commissioning"]["zero_valid"])
            self.assertTrue(result["commissioning"]["selftest_passed"])
            self.assertFalse(result["commissioning"]["trip_verified"])
            self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3:5], (7, 15000))
            before = len(self.fake.commands)
            self.link.read_status()
            self.assertFalse(any(command.startswith(("mww", "load_image"))
                                 for command in self.fake.commands[before:]))
            result = self.link.command("commission_test", 1)
            self.assertTrue(result["commissioning"]["trip_verified"])
            self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3:5], (8, 1))
            result = self.link.command("commission_test", 2)
            self.assertEqual(result["commissioning"]["state"], 5)
            self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3:5], (8, 2))
        self.assertEqual(sum(command.startswith("load_image") for command in self.fake.commands), 3)
        self.assertEqual(self.fake.words[34], 0)
        self.assertEqual(self.fake.limit_words[7], 0)

    def test_commissioning_preflight_requires_capability_zero_and_verified_trip(self):
        self.enable_commissioning()
        for name, value, changes in (
                ("commission_prepare", 15000, {5: 0x67}),
                ("commission_prepare", 15000, {4: 0}),
                ("commission_prepare", 15000, {32: 1}),
                ("commission_prepare", 15000, {33: 2500}),
                ("commission_prepare", 15000, {12: 23000}),
                ("commission_test", 1, {}),
                ("commission_test", 2, {})):
            saved = self.fake.words.copy()
            for index, value_changed in changes.items():
                self.fake.words[index] = value_changed
            with self.subTest(name=name, value=value, changes=changes), \
                    patch.object(device_link.subprocess, "run", self.fake):
                with self.assertRaises(RuntimeError):
                    self.link.command(name, value)
            self.fake.words = saved
        self.assertFalse(any(command.startswith(("mww", "load_image")) for command in self.fake.commands))

    def test_bad_commissioning_values_prevent_probe_access(self):
        for name, value in (("commission_prepare", 0), ("commission_prepare", 14999),
                            ("commission_prepare", True), ("commission_test", -1),
                            ("commission_test", 5), ("commission_test", False)):
            with self.subTest(name=name, value=value), patch.object(device_link.subprocess, "run", self.fake):
                with self.assertRaises(RuntimeError):
                    self.link.command(name, value)
        self.assertEqual(self.fake.commands, [])

    def test_dry_run_command_does_not_verify_the_real_shutdown_path(self):
        self.enable_commissioning()
        with patch.object(device_link.subprocess, "run", self.fake):
            self.link.command("commission_prepare", 15000)
            result = self.link.command("commission_test", 0)
            self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3:5], (8, 0))
            self.assertFalse(result["commissioning"]["trip_verified"])
            with self.assertRaisesRegex(RuntimeError, "Verify"):
                self.link.command("commission_test", 2)

    def test_thirty_second_observation_uses_five_of_twelve_ms_pwm_after_trip_verification(self):
        self.enable_commissioning()
        with patch.object(device_link.subprocess, "run", self.fake):
            self.link.command("commission_prepare", 15000)
            with self.assertRaisesRegex(RuntimeError, "Verify"):
                self.link.command("commission_test", 3)
            self.link.command("commission_test", 1)
            result = self.link.command("commission_test", 3)
            self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3:5], (8, 3))
            self.assertEqual(result["commissioning"]["duration_ms"], 30000)
            self.assertEqual(result["commissioning"]["period_ms"], 12)
            self.assertEqual(result["commissioning"]["on_us"], 5000)
            self.assertTrue(result["commissioning"]["drive"])
            # A completed observation may be requested again only by another command.
            before = sum(command.startswith("load_image") for command in self.fake.commands)
            self.link.read_status()
            self.assertEqual(sum(command.startswith("load_image") for command in self.fake.commands), before)
            result = self.link.command("commission_test", 3)
            self.assertEqual(result["commissioning"]["duration_ms"], 30000)

    def test_thirty_second_observation_rejects_incomplete_protection_prerequisites_without_writes(self):
        self.enable_commissioning()
        with patch.object(device_link.subprocess, "run", self.fake):
            self.link.command("commission_prepare", 15000)
            self.link.command("commission_test", 1)
            self.link.command("commission_test", 2)
            for index, bad in ((4, 2), (4, 4), (5, 29), (5, 30), (5, 27), (13, 999)):
                saved = self.fake.commission_words.copy()
                self.fake.commission_words[index] = bad
                start = len(self.fake.commands)
                with self.subTest(index=index, bad=bad), self.assertRaises(RuntimeError):
                    self.link.command("commission_test", 3)
                self.assertFalse(any(command.startswith(("mww", "load_image"))
                                     for command in self.fake.commands[start:]))
                self.fake.commission_words = saved

    def test_hidden_long_dry_run_does_not_require_or_claim_trip_verification(self):
        self.enable_commissioning()
        with patch.object(device_link.subprocess, "run", self.fake):
            with self.assertRaisesRegex(RuntimeError, "Prepare"):
                self.link.command("commission_test", 4)
            self.link.command("commission_prepare", 15000)
            result = self.link.command("commission_test", 4)
            self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3:5], (8, 4))
            self.assertEqual(result["commissioning"]["duration_ms"], 30000)
            self.assertEqual(result["commissioning"]["on_us"], 5000)
            self.assertEqual(result["commissioning"]["period_ms"], 12)
            self.assertFalse(result["commissioning"]["trip_verified"])
            self.assertFalse(result["commissioning"]["drive"])
            with self.assertRaisesRegex(RuntimeError, "Verify"):
                self.link.command("commission_test", 3)

    def test_bad_commissioning_block_preserves_stop_but_blocks_commissioning(self):
        self.enable_commissioning()
        for index, bad in ((0, 0), (1, 3), (3, 100000), (4, 7), (5, 128), (7, 4096)):
            saved = self.fake.commission_words.copy()
            self.fake.commission_words[index] = bad
            with self.subTest(index=index), patch.object(device_link.subprocess, "run", self.fake):
                result = self.link.read_status()
                self.assertFalse(result["commissioning"]["valid"])
                with self.assertRaisesRegex(RuntimeError, "commissioning status"):
                    self.link.command("commission_prepare", 15000)
                result = self.link.command("stop")
                self.assertEqual(result["armed_mask"], 0)
                self.assertFalse(result["commissioning"]["valid"])
            self.fake.commission_words = saved

    def test_bad_commissioning_address_or_size_prevents_probe_access(self):
        metadata = json.loads(self.metadata.read_text())
        for address, size in ((0x40000000, 80), (0x2000BFB4, 80),
                              (0x20000501, 80), (0x20000500, 76)):
            self.metadata.write_text(json.dumps({**metadata, "commissioning_address": address,
                                                 "commissioning_size": size}))
            with self.subTest(address=address, size=size), patch.object(device_link.subprocess, "run", self.fake):
                with self.assertRaises(RuntimeError):
                    self.link.read_status()
        self.assertEqual(self.fake.commands, [])

    def test_active_commissioning_status_refreshes_lease_without_retrying_test(self):
        self.enable_commissioning()
        self.fake.commission_words[6] = 15000
        for state in (2, 4):
            self.fake.commission_words[4] = state
            with self.subTest(state=state), patch.object(device_link.subprocess, "run", self.fake):
                result = self.link.read_status()
                self.assertEqual(result["commissioning"]["state"], state)
                self.assertEqual(struct.unpack("<8I", self.fake.mailbox)[3:5], (5, 0))

    def test_uncertain_commissioning_write_is_not_replayed(self):
        self.enable_commissioning()
        with patch.object(device_link.subprocess, "run", self.fake):
            status = self.link.read_status()
        with patch.object(self.link, "_read", return_value=(status, self.fake.mailbox)), \
                patch.object(self.link, "_run", side_effect=RuntimeError("uncertain")) as run:
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                self.link.command("commission_prepare", 15000)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
