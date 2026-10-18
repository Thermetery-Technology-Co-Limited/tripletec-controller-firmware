from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui import app


def bench_status(**changes):
    result = {
        "schema": 2, "outputs_locked_off": True, "armed_mask": 0,
        "ambient_c": 27.0, "rh_percent": 60.0, "dewpoint_c": 18.6,
        "target_c": 21.6, "plate_c": [26.0, None, None],
        "current_a_nominal": [0.0, None, None], "applied_duty": [0, 0, 0],
        "desired_duty": [0, 0, 0], "active_faults": [2 | 2048 | 4096, 16, 16],
        "valid_flags": 0x27,
    }
    result.update(changes)
    return result


class FakeLink:
    def __init__(self):
        self.closed = False
        self.commands = []
        self.status_calls = 0
        self.pulse_calls = 0

    def read_status(self):
        self.status_calls += 1
        return {"schema": 2, "outputs_locked_off": True}

    def command(self, name, value):
        self.commands.append((name, value))
        return {"schema": 2, "outputs_locked_off": True}

    def test_tec1_pulse(self):
        self.pulse_calls += 1
        return bench_status(pulse_test={"peak_current_a_nominal": 26.4,
                                       "timer_complete": True,
                                       "elapsed_nominal_us": 1001})

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

    def connect(self, transport="usb"):
        self.open_link.return_value = self.link, transport
        self.worker.submit(0, "connect", {"transport": transport})
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

    def test_tec1_operator_action_calls_swd_pulse_once_without_arm(self):
        self.connect("swd")
        self.worker.submit(0, "tec1_test")
        generation, kind, (origin, result) = self.event()
        self.assertEqual((generation, kind, origin), (0, "status", "tec1_test"))
        self.assertEqual(result["transport"], "swd")
        self.assertEqual(result["pulse_test"]["peak_current_a_nominal"], 26.4)
        self.assertEqual(self.link.pulse_calls, 1)
        self.assertEqual(self.link.commands, [])
        self.worker.submit(0, "poll")
        self.assertEqual(self.event()[1], "status")
        self.assertEqual(self.link.pulse_calls, 1)

    def test_tec1_never_uses_usb_or_disconnected_link(self):
        self.worker.submit(0, "tec1_test")
        self.assertEqual(self.event()[1], "error")
        self.connect("usb")
        self.worker.submit(0, "tec1_test")
        self.assertEqual(self.event()[1], "error")
        self.assertEqual(self.link.pulse_calls, 0)
        self.assertEqual(self.open_link.call_count, 1)

    def test_uncertain_tec1_pulse_is_not_retried_or_switched(self):
        self.connect("swd")
        with patch.object(self.link, "test_tec1_pulse", side_effect=RuntimeError("uncertain")) as pulse:
            self.worker.submit(0, "tec1_test")
            self.assertEqual(self.event(), (0, "error", ("tec1_test", "uncertain")))
            self.worker.submit(0, "poll")
            self.assertEqual(self.event()[1], "status")
            pulse.assert_called_once_with()
        self.assertEqual(self.open_link.call_count, 1)
        self.assertEqual(self.link.commands, [])

    def test_cancelled_tec1_action_cannot_fire_after_stop(self):
        self.connect("swd")
        self.worker.invalidate(1)
        self.worker.submit(0, "tec1_test")
        self.worker.submit(1, "command", [("stop", 0)], priority=0)
        self.assertEqual(self.event()[1], "status")
        self.assertEqual(self.link.pulse_calls, 0)
        self.assertEqual(self.link.commands, [("stop", 0)])


class Tec1PulseControlsTest(unittest.TestCase):
    def reason(self, data=None, **changes):
        options = {"connected": True, "transport": "swd", "now": 101.0}
        options.update(changes)
        sample = app.Snapshot.from_status(bench_status() if data is None else data, 100.0)
        return app.tec1_test_block_reason(sample, **options)

    def test_expected_bench_inhibits_allow_test_without_enabling_normal_start(self):
        sample = app.Snapshot.from_status(bench_status(), 100.0)
        self.assertIsNone(self.reason())
        self.assertIsNotNone(sample.start_block_reason(True, 101.0))

    def test_test_requires_fresh_live_swd_and_no_pending_command(self):
        for changes in ({"connected": False}, {"transport": "usb"}, {"demo": True},
                        {"now": 104.0}, {"command_pending": True},
                        {"last_error": "lost connection"}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(self.reason(**changes))

    def test_test_requires_stopped_locked_bench_image_and_probe_margin(self):
        for changes in ({"schema": 1}, {"outputs_locked_off": False}, {"armed_mask": 1},
                        {"applied_duty": [0.1, 0, 0]}, {"desired_duty": [0, 0.1, 0]},
                        {"plate_c": [None, None, None]}, {"plate_c": [22.6, None, None]},
                        {"target_c": None}, {"ambient_c": None}, {"rh_percent": None},
                        {"current_a_nominal": [None, None, None]}, {"valid_flags": 0x23}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(self.reason(bench_status(**changes)))

    def test_every_unexpected_tec1_fault_blocks_test(self):
        for fault in (1, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 8192, 1 << 16):
            with self.subTest(fault=fault):
                self.assertIsNotNone(self.reason(bench_status(active_faults=[6150 | fault, 16, 16])))

    def test_explicit_operator_action_submits_once_and_double_click_is_ignored(self):
        dashboard = app.Dashboard.__new__(app.Dashboard)
        dashboard.snapshot = app.Snapshot.from_status(bench_status(), 100.0)
        dashboard.connected = True
        dashboard.active_transport = "swd"
        dashboard.command_pending = False
        dashboard.last_error = None
        dashboard.generation = 3
        dashboard.worker = Mock()
        dashboard.tec1_test_result = Mock()
        dashboard.message = Mock()
        dashboard._is_demo = Mock(return_value=False)
        dashboard._render_controls = Mock()
        with patch.object(app.time, "monotonic", return_value=101.0):
            dashboard.test_tec1()
            dashboard.test_tec1()
        dashboard.worker.submit.assert_called_once_with(3, "tec1_test", priority=2)
        self.assertTrue(dashboard.command_pending)

    def test_result_stays_explicit_about_nominal_current_and_timer_evidence(self):
        result = {"pulse_test": {"peak_current_a_nominal": 26.4,
                                  "timer_complete": True, "elapsed_nominal_us": 1001}}
        text = app.tec1_test_result_text(result)
        self.assertIn("timer complete", text)
        self.assertIn("26.40 A nominal, uncalibrated", text)
        self.assertIn("uncommissioned", text)
        result["pulse_test"]["timer_complete"] = False
        self.assertIn("timer completion not confirmed", app.tec1_test_result_text(result))
        for invalid in ({}, {"timer_complete": 1},
                        {"timer_complete": True, "peak_current_a_nominal": float("nan"),
                         "elapsed_nominal_us": 1000}):
            with self.subTest(result=invalid), self.assertRaises(ValueError):
                app.tec1_test_result_text({"pulse_test": invalid})


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
