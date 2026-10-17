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
                data = self.mailbox if int(address, 16) == 0x20000200 else struct.pack("<40I", *self.words)
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
            self.assertEqual(self.link.read_status()["margin_c"], 3)
        self.assertIn("target create status.mem mem_ap -dap status.dap -ap-num 0", self.fake.commands)
        self.assertFalse(any(re.search(r"\b(halt|reset|program)\b", command) for command in self.fake.commands
                             if not command.startswith("reset_config")))
        self.assertEqual(self.fake.options[0]["timeout"], 5)
        if sys.platform == "win32":
            self.assertEqual(self.fake.options[0]["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_mode_command_commits_sequence_last_and_checks_ack(self):
        with patch.object(device_link.subprocess, "run", self.fake):
            result = self.link.command("mode", 1)
        self.assertEqual(result["mode_name"], "unregulated")
        self.assertEqual(result["command_ack_seq"], 1)
        writes = [command for command in self.fake.commands if command.startswith(("mww", "load_image"))]
        self.assertEqual(writes[0], "mww 0x20000208 0")
        self.assertTrue(writes[1].startswith("load_image"))
        self.assertEqual(writes[2], "mww 0x20000208 0x1")

    def test_margin_stop_and_locked_arm(self):
        with patch.object(device_link.subprocess, "run", self.fake):
            self.assertEqual(self.link.command("margin", 4500)["margin_c"], 4.5)
            self.assertEqual(self.link.command("stop")["armed_mask"], 0)
            self.fake.reject = 5
            with self.assertRaisesRegex(RuntimeError, "BENCH_LOCKED"):
                self.link.command("arm", 7)

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


if __name__ == "__main__":
    unittest.main()
