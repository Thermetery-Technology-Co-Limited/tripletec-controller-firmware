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


def commissioning_status(**changes):
    result = {"valid": True, "schema": 1, "state": 1, "flags": 21,
              "zero_valid": True, "trip_verified": False, "selftest_passed": True,
              "drive": False, "nominal_gain": True, "stage_prepared": False,
              "trip_a": 15, "baseline_adc": 2040, "peak_adc": 2040,
              "sample_count": 128, "pulses": 0, "fault_code": 0}
    result.update(changes)
    return result


def commissioning_bench_status(**changes):
    return bench_status(**{"valid_flags": 0xE7, "mode": 0, "margin_c": 3,
                           "commissioning": commissioning_status(), **changes})


def completed_commissioning_status(**changes):
    return commissioning_status(**{"state": 5, "flags": 31, "trip_verified": True,
                                    "drive": True, "pulses": 50, "duration_ms": 1000,
                                    "elapsed_ms": changes.get("duration_ms", 1000),
                                    **changes})


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

    def test_bench_lock_ack_uses_selected_transport_without_arm(self):
        self.connect("swd")
        for value in (0, 1):
            reported = bench_status(outputs_locked_off=bool(value), valid_flags=0x67)
            with patch.object(self.link, "command", return_value=reported) as command:
                self.worker.submit(0, "command", [("bench_lock", value)])
                generation, kind, (origin, result) = self.event()
                self.assertEqual((generation, kind, origin), (0, "status", "command"))
                command.assert_called_once_with("bench_lock", value)
                self.assertIs(result["outputs_locked_off"], bool(value))
                self.assertEqual(result["transport"], "swd")
        self.assertEqual(self.open_link.call_count, 1)
        self.assertEqual(self.link.pulse_calls, 0)

    def test_bench_lock_error_is_not_retried_or_fallen_back(self):
        self.connect("usb")
        with patch.object(self.link, "command", side_effect=RuntimeError("lock rejected")) as command:
            self.worker.submit(0, "command", [("bench_lock", 0)])
            self.assertEqual(self.event(), (0, "error", ("command", "lock rejected")))
            command.assert_called_once_with("bench_lock", 0)
        self.assertEqual(self.open_link.call_count, 1)

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

    def test_commissioning_commands_use_selected_swd_once(self):
        self.connect("swd")
        for command in (("commission_prepare", 15000), ("commission_test", 1),
                        ("commission_test", 2), ("commission_test", 3)):
            with self.subTest(command=command):
                self.worker.submit(0, "command", [command])
                generation, kind, (origin, result) = self.event()
                self.assertEqual((generation, kind, origin), (0, "status", "command"))
                self.assertEqual(result["transport"], "swd")
        self.assertEqual(self.link.commands, [("commission_prepare", 15000),
                                             ("commission_test", 1), ("commission_test", 2),
                                             ("commission_test", 3)])
        self.assertEqual(self.open_link.call_count, 1)
        self.assertEqual(self.link.pulse_calls, 0)

    def test_commissioning_commands_never_touch_usb(self):
        self.connect("usb")
        for command in (("commission_prepare", 15000), ("commission_test", 1),
                        ("commission_test", 2), ("commission_test", 3)):
            with self.subTest(command=command):
                self.worker.submit(0, "command", [command])
                self.assertEqual(self.event()[1], "error")
        self.assertEqual(self.link.commands, [])
        self.assertEqual(self.link.pulse_calls, 0)
        self.assertEqual(self.open_link.call_count, 1)

    def test_uncertain_commissioning_command_has_no_retry_fallback_or_next_step(self):
        self.connect("swd")
        for requested in (("commission_prepare", 15000), ("commission_test", 1),
                          ("commission_test", 2), ("commission_test", 3)):
            with self.subTest(command=requested), patch.object(
                    self.link, "command", side_effect=RuntimeError("uncertain")) as command:
                self.worker.submit(0, "command", [requested])
                self.assertEqual(self.event(), (0, "error", ("command", "uncertain")))
                self.worker.submit(0, "poll")
                self.assertEqual(self.event()[1], "status")
                command.assert_called_once_with(*requested)
        self.assertEqual(self.open_link.call_count, 1)
        self.assertEqual(self.link.pulse_calls, 0)

    def test_connect_and_poll_never_advance_commissioning(self):
        with patch.object(self.link, "read_status", return_value=commissioning_bench_status()):
            self.connect("swd")
        for state in range(7):
            reported = commissioning_bench_status(commissioning=commissioning_status(state=state))
            with patch.object(self.link, "read_status", return_value=reported):
                self.worker.submit(0, "poll")
                self.assertEqual(self.event()[1], "status")
        for duration in (1000, 10000):
            reported = commissioning_bench_status(commissioning=completed_commissioning_status(
                duration_ms=duration))
            with patch.object(self.link, "read_status", return_value=reported):
                self.worker.submit(0, "poll")
                self.assertEqual(self.event()[1], "status")
        self.assertEqual(self.link.commands, [])
        self.assertEqual(self.link.pulse_calls, 0)
        self.assertEqual(self.open_link.call_count, 1)

    def test_stop_cancels_queued_commissioning_commands(self):
        self.connect("swd")
        self.worker.invalidate(1)
        self.worker.submit(0, "command", [("commission_test", 2)])
        self.worker.submit(1, "command", [("stop", 0)], priority=0)
        self.assertEqual(self.event()[1], "status")
        self.assertEqual(self.link.commands, [("stop", 0)])


class BenchLockControlsTest(unittest.TestCase):
    def dashboard(self, **status_changes):
        dashboard = app.Dashboard.__new__(app.Dashboard)
        dashboard.snapshot = app.Snapshot.from_status(bench_status(valid_flags=0x67, **status_changes), 100)
        dashboard.connected = True
        dashboard.command_pending = False
        dashboard.last_error = None
        dashboard.generation = 4
        dashboard.worker = Mock()
        dashboard.message = Mock()
        dashboard._is_demo = Mock(return_value=False)
        dashboard._render_controls = Mock()
        return dashboard

    def test_unlock_click_queues_once_and_does_not_optimistically_change_state(self):
        dashboard = self.dashboard()
        with patch.object(app.time, "monotonic", return_value=101):
            dashboard.toggle_bench_lock()
            dashboard.toggle_bench_lock()
        dashboard.worker.submit.assert_called_once_with(4, "command", [("bench_lock", 0)], 2)
        self.assertTrue(dashboard.command_pending)
        self.assertTrue(dashboard.snapshot.outputs_locked_off)

    def test_relock_click_still_works_with_faults_error_and_dirty_settings(self):
        dashboard = self.dashboard(outputs_locked_off=False, plate_c=[None] * 3,
                                   active_faults=[0xFFFFFFFF] * 3)
        dashboard.last_error = "previous setting rejected"
        dashboard.dirty = True
        with patch.object(app.time, "monotonic", return_value=101):
            dashboard.toggle_bench_lock()
        dashboard.worker.submit.assert_called_once_with(4, "command", [("bench_lock", 1)], 0)
        self.assertFalse(dashboard.snapshot.outputs_locked_off)

    def test_toggle_never_submits_for_unsupported_stale_disconnected_or_demo(self):
        for state in ("unsupported", "stale", "disconnected", "demo", "unknown"):
            with self.subTest(state=state):
                dashboard = self.dashboard()
                if state == "unsupported":
                    dashboard.snapshot = app.Snapshot.from_status(bench_status(), 100)
                elif state == "disconnected":
                    dashboard.connected = False
                elif state == "demo":
                    dashboard._is_demo.return_value = True
                elif state == "unknown":
                    dashboard.snapshot = app.Snapshot.from_status(bench_status(valid_flags=0x67, outputs_locked_off=None), 100)
                with patch.object(app.time, "monotonic", return_value=104 if state == "stale" else 101):
                    dashboard.toggle_bench_lock()
                dashboard.worker.submit.assert_not_called()

    def test_only_acknowledged_status_changes_displayed_lock_state(self):
        dashboard = self.dashboard()
        dashboard.closed = False
        dashboard.poll_pending = False
        dashboard.connecting = False
        dashboard.root = Mock()
        dashboard.worker.events = app.queue.Queue()
        dashboard.dirty = False
        dashboard._render = Mock()
        dashboard._accept_status = lambda result: setattr(
            dashboard, "snapshot", app.Snapshot.from_status(result, 101))
        with patch.object(app.time, "monotonic", return_value=101):
            dashboard.toggle_bench_lock()
        self.assertTrue(dashboard.snapshot.outputs_locked_off)
        dashboard.worker.events.put((4, "status", ("command", bench_status(
            outputs_locked_off=False, valid_flags=0x67))))
        dashboard._drain()
        self.assertFalse(dashboard.snapshot.outputs_locked_off)
        self.assertFalse(dashboard.command_pending)


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

    def test_guarded_commissioning_firmware_blocks_legacy_debug_pulse(self):
        self.assertIn("current-protected commissioning", self.reason(commissioning_bench_status()))

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


class CommissioningControlsTest(unittest.TestCase):
    def test_active_commissioning_poll_cadence_preserves_host_lease(self):
        self.assertEqual(app.poll_interval_ms(None), 1000)
        for state in range(7):
            sample = app.Snapshot.from_status(commissioning_bench_status(
                commissioning=commissioning_status(state=state)), 100)
            self.assertEqual(app.poll_interval_ms(sample), 200 if state in (2, 4) else 1000)

    def test_active_ack_reschedules_slow_poll_without_starting_another_action(self):
        dashboard = self.dashboard()
        dashboard.root = Mock()
        dashboard.mode = Mock()
        dashboard.margin = Mock()
        dashboard.margin_entry = Mock()
        dashboard.history = []
        dashboard._render = Mock()
        dashboard._tick_after_id = "previous-slow-poll"
        reported = commissioning_bench_status(commissioning=commissioning_status(
            state=4, flags=31, trip_verified=True, drive=True))
        with patch.object(app.time, "monotonic", return_value=101):
            dashboard._accept_status(reported)
        dashboard.root.after_cancel.assert_called_once_with("previous-slow-poll")
        dashboard.root.after.assert_called_once_with(200, dashboard._tick)
        dashboard.worker.submit.assert_not_called()

    def reason(self, action="prepare", data=None, **changes):
        options = {"connected": True, "transport": "swd", "now": 101.0}
        options.update(changes)
        sample = app.Snapshot.from_status(commissioning_bench_status() if data is None else data, 100.0)
        return app.commissioning_block_reason(sample, action, **options)

    def dashboard(self, **changes):
        dashboard = app.Dashboard.__new__(app.Dashboard)
        dashboard.snapshot = app.Snapshot.from_status(commissioning_bench_status(**changes), 100.0)
        dashboard.connected = True
        dashboard.active_transport = "swd"
        dashboard.command_pending = False
        dashboard.last_error = None
        dashboard.generation = 4
        dashboard.dirty = False
        dashboard.worker = Mock()
        dashboard.message = Mock()
        dashboard.commission_result = Mock()
        dashboard.commission_reason = Mock()
        dashboard._is_demo = Mock(return_value=False)
        dashboard._render_controls = Mock()
        return dashboard

    def test_prepare_and_verify_allowed_but_run_requires_shutdown_verification(self):
        self.assertIsNone(self.reason("prepare"))
        self.assertIsNone(self.reason("verify"))
        self.assertIsNotNone(self.reason("run"))
        verified = commissioning_bench_status(commissioning=commissioning_status(
            state=3, flags=23, trip_verified=True))
        self.assertIsNone(self.reason("run", verified))

    def test_every_action_requires_supported_fresh_live_swd_state(self):
        for action in ("prepare", "verify", "run", "observe"):
            for option in ({"connected": False}, {"transport": "usb"}, {"demo": True},
                           {"now": 104.0}, {"command_pending": True}, {"last_error": "uncertain"}):
                with self.subTest(action=action, option=option):
                    self.assertIsNotNone(self.reason(action, **option))
            for change in ({"schema": 1}, {"valid_flags": 0x67}, {"mode": 1},
                           {"margin_c": 2.999}, {"margin_c": None}, {"commissioning": None},
                           {"commissioning": {"valid": False}}, {"commissioning": []},
                           {"commissioning": commissioning_status(state=2)},
                           {"commissioning": commissioning_status(state=4)},
                           {"commissioning": commissioning_status(state=7)},
                           {"commissioning": commissioning_status(zero_valid=1)}):
                with self.subTest(action=action, change=change):
                    self.assertIsNotNone(self.reason(action, commissioning_bench_status(**change)))

    def test_verify_and_run_require_zero_and_selftest_but_prepare_can_recover(self):
        for field in ("zero_valid", "selftest_passed"):
            data = commissioning_bench_status(commissioning=commissioning_status(**{field: False}))
            self.assertIsNone(self.reason("prepare", data))
            self.assertIsNotNone(self.reason("verify", data))
            self.assertIsNotNone(self.reason("run", data))

    def test_observation_requires_verified_shutdown_without_a_completed_short_run(self):
        verified = commissioning_status(state=3, flags=23, trip_verified=True)
        data = commissioning_bench_status(commissioning=verified)
        self.assertIsNone(self.reason("observe", data))
        self.assertFalse(verified["drive"])
        self.assertEqual(verified["pulses"], 0)
        for changes in ({"state": 2}, {"state": 4}, {"trip_verified": False},
                        {"zero_valid": False}, {"selftest_passed": False}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(self.reason("observe", commissioning_bench_status(
                    commissioning={**verified, **changes})))

    def test_operator_click_submits_exact_action_once_without_optimistic_state(self):
        for action, command in (("prepare", ("commission_prepare", 15000)),
                                ("verify", ("commission_test", 1)), ("run", ("commission_test", 2)),
                                ("observe", ("commission_test", 3))):
            with self.subTest(action=action):
                commissioned = commissioning_status(state=3, flags=23, trip_verified=True)
                dashboard = self.dashboard(commissioning=commissioned)
                before = dashboard.snapshot
                with patch.object(app.time, "monotonic", return_value=101.0):
                    dashboard.commission_tec1(action)
                    dashboard.commission_tec1(action)
                dashboard.worker.submit.assert_called_once_with(4, "command", [command], 2)
                self.assertTrue(dashboard.command_pending)
                self.assertIs(dashboard.snapshot, before)

    def test_status_acceptance_never_queues_next_action(self):
        dashboard = self.dashboard()
        dashboard.root = Mock()
        dashboard.mode = Mock()
        dashboard.margin = Mock()
        dashboard.margin_entry = Mock()
        dashboard.history = []
        dashboard._render = Mock()
        for state in range(7):
            reported = commissioning_bench_status(commissioning=commissioning_status(state=state))
            with patch.object(app.time, "monotonic", return_value=101.0):
                dashboard._accept_status(reported)
        for duration in (1000, 10000):
            reported = commissioning_bench_status(commissioning=completed_commissioning_status(
                duration_ms=duration))
            with patch.object(app.time, "monotonic", return_value=101.0):
                dashboard._accept_status(reported)
        dashboard.worker.submit.assert_not_called()
        self.assertFalse(dashboard.command_pending)

    def test_malformed_commissioning_disables_tests_while_stop_stays_available(self):
        dashboard = self.dashboard(commissioning="unsupported")
        for name in ("apply_button", "margin_entry", "reported_mode", "reason", "start_button",
                     "stop_button", "commission_stop_button", "bench_lock_state", "bench_lock_button", "test_tec1_button",
                     "note", "note_label"):
            setattr(dashboard, name, Mock())
        dashboard.mode = Mock()
        dashboard.mode.get.return_value = 0
        dashboard.mode_buttons = [Mock(), Mock()]
        dashboard.commission_buttons = {action: Mock() for action in ("prepare", "verify", "run", "observe")}
        with patch.object(app.time, "monotonic", return_value=101.0):
            app.Dashboard._render_controls(dashboard)
        dashboard.stop_button.configure.assert_called_once_with(state="normal")
        dashboard.commission_stop_button.configure.assert_called_once_with(state="normal")
        for button in dashboard.commission_buttons.values():
            button.configure.assert_called_once_with(state="disabled")
        dashboard.worker.submit.assert_not_called()

    def test_running_test_blocks_bench_unlock_and_separate_pulse(self):
        for state in (2, 4):
            dashboard = self.dashboard(commissioning=commissioning_status(state=state))
            with patch.object(app.time, "monotonic", return_value=101.0):
                dashboard.toggle_bench_lock()
                dashboard.commission_tec1("prepare")
            dashboard.worker.submit.assert_not_called()

    def test_result_reports_nominal_gain_peak_and_fault_cause(self):
        text = app.commissioning_result_text(commissioning_status(peak_adc=2321, pulses=5))
        self.assertIn("peak 8.58 A nominal", text)
        self.assertIn("5 pulse(s)", text)
        self.assertIn("full current calibration remains incomplete", text)
        text = app.commissioning_result_text(commissioning_status(state=6, fault_code=4))
        self.assertIn("overcurrent", text)
        self.assertIn("outputs OFF", app.commissioning_result_text(commissioning_status(state=5)))

    def test_result_reports_run_duration_only_for_running_and_finished_attempts(self):
        for state in (4, 5, 6):
            for duration in (1000, 10000):
                text = app.commissioning_result_text(commissioning_status(state=state, duration_ms=duration))
                with self.subTest(state=state, duration=duration):
                    self.assertIn(f"{duration // 1000} s maximum", text)
        for state in (0, 1, 2, 3):
            text = app.commissioning_result_text(commissioning_status(state=state, duration_ms=1000))
            self.assertNotIn("s maximum", text)


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
