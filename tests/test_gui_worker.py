from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui import app


class FakeLink:
    def __init__(self):
        self.closed = False
        self.commands = []
        self.status_calls = 0

    def read_status(self):
        self.status_calls += 1
        return {"schema": 2, "outputs_locked_off": True}

    def command(self, name, value):
        self.commands.append((name, value))
        return {"schema": 2, "outputs_locked_off": True}

    def close(self):
        self.closed = True


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.worker = app.LinkWorker()
        self.link = FakeLink()
        self.opener = patch.object(app, "open_link", return_value=(self.link, "usb"))
        self.open_link = self.opener.start()
        self.addCleanup(self.opener.stop)
        self.worker.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.worker.shutdown()
        self.worker.join(2)
        self.assertFalse(self.worker.is_alive())

    def event(self):
        return self.worker.events.get(timeout=2)

    def connect(self):
        self.worker.submit(0, "connect", {"transport": "usb"})
        result = self.event()
        self.assertEqual(result[1], "status")

    def test_connect_poll_and_shutdown_close_usb(self):
        self.connect()
        self.worker.submit(0, "poll")
        self.assertEqual(self.event()[2][1]["transport"], "usb")
        self.assertEqual(self.link.status_calls, 2)
        self.worker.shutdown()
        self.worker.join(2)
        self.assertTrue(self.link.closed)

    def test_reconnect_closes_previous_link(self):
        self.connect()
        replacement = FakeLink()
        self.open_link.return_value = replacement, "swd"
        self.worker.submit(0, "connect", {"transport": "swd"})
        self.assertEqual(self.event()[1], "status")
        self.assertTrue(self.link.closed)
        self.assertFalse(replacement.closed)

    def test_cancelled_arm_never_executes_after_stop(self):
        self.connect()
        self.worker.invalidate(1)
        self.worker.submit(0, "command", [("arm", 7)])
        self.worker.submit(1, "command", [("stop", 0)], priority=0)
        self.assertEqual(self.event()[1], "status")
        self.assertEqual(self.link.commands, [("stop", 0)])

    def test_command_error_never_switches_transport(self):
        self.connect()
        with patch.object(self.link, "command", side_effect=RuntimeError("uncertain")):
            self.worker.submit(0, "command", [("mode", 1)])
            self.assertEqual(self.event()[1], "error")
        self.assertEqual(self.open_link.call_count, 1)
        self.worker.submit(0, "command", [("stop", 0)])
        self.assertEqual(self.event()[1], "status")
        self.assertEqual(self.link.commands, [("stop", 0)])


class LinkSelectionTest(unittest.TestCase):
    def test_auto_prefers_usb_and_swd_only_if_no_usb_is_present(self):
        with patch("scripts.usb_link.list_usb_devices", return_value=[{"device": "COM8"}]), \
                patch("scripts.usb_link.UsbLink") as usb, patch("scripts.device_link.DeviceLink") as swd:
            app.open_link({"transport": "auto"})
            usb.assert_called_once()
            swd.assert_not_called()
        with patch("scripts.usb_link.list_usb_devices", return_value=[]), \
                patch("scripts.usb_link.UsbLink") as usb, patch("scripts.device_link.DeviceLink") as swd:
            app.open_link({"transport": "auto", "serial": "PICO"})
            swd.assert_called_once_with(root=None, serial="PICO")
            usb.assert_not_called()

    def test_usb_open_failure_does_not_fall_back_to_swd(self):
        with patch("scripts.usb_link.list_usb_devices", return_value=[{"device": "COM8"}]), \
                patch("scripts.usb_link.UsbLink", side_effect=RuntimeError("cannot open")), \
                patch("scripts.device_link.DeviceLink") as swd:
            with self.assertRaisesRegex(RuntimeError, "cannot open"):
                app.open_link({"transport": "auto"})
            swd.assert_not_called()


if __name__ == "__main__":
    unittest.main()
