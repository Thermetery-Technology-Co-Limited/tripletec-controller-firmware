import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui.model import (Snapshot, channel_status, current_reading_text, current_trip_label, format_value,
                       margin_millic, mode_label)


def status(**changes):
    data = {"schema": 2, "mode": 0, "margin_c": 3.0, "armed_mask": 0,
            "outputs_locked_off": True, "plate_c": [25, None, 27],
            "current_a_nominal": [0, 0, 0], "applied_duty": [0, 0, 0],
            "desired_duty": [0, 0, 0], "active_faults": [2, 18, 2]}
    data.update(changes)
    return data


def commissioning_status(**changes):
    data = {"valid": True, "schema": 1, "state": 1, "flags": 21,
            "zero_valid": True, "trip_verified": False, "selftest_passed": True,
            "drive": False, "nominal_gain": True, "stage_prepared": False,
            "trip_a": 15, "baseline_adc": 2040, "peak_adc": 2040,
            "sample_count": 128, "pulses": 0, "fault_code": 0}
    data.update(changes)
    return data


class PresentationTests(unittest.TestCase):
    def test_missing_and_nonfinite_readings_are_unknown(self):
        sample = Snapshot.from_status({"schema": 2}, 10.0)
        self.assertEqual(sample.plate_c, (None, None, None))
        self.assertIsNone(sample.outputs_locked_off)
        self.assertIsNotNone(sample.start_block_reason(True, 10.0))
        sample = Snapshot.from_status(status(ambient_c=math.nan, rh_percent="62"), 10.0)
        self.assertIsNone(sample.ambient_c)
        self.assertIsNone(sample.rh_percent)
        self.assertEqual(format_value(None, " °C"), "—")

    def test_lock_and_stale_are_independent_start_inhibits(self):
        sample = Snapshot.from_status(status(), 10.0)
        self.assertIn("Bench firmware", sample.start_block_reason(True, 11.0))
        self.assertIn("stale", sample.start_block_reason(True, 14.0))
        self.assertIn("Connect", sample.start_block_reason(False, 11.0))
        self.assertIn("plate sensor unavailable", channel_status(sample, 1, True, 11.0))
        self.assertEqual(channel_status(sample, 0, True, 14.0), "Stale reading")

    def test_bench_lock_capability_requires_explicit_device_support(self):
        for data, expected in ((status(), None), (status(valid_flags=0x3F), False),
                               (status(valid_flags=0x7F), True),
                               (status(bench_lock_supported=True), True),
                               (status(bench_lock_supported=False), False),
                               (status(schema=1, valid_flags=0x7F), False)):
            with self.subTest(data=data):
                self.assertIs(Snapshot.from_status(data, 10).bench_lock_supported, expected)
        for value in (1, 0, "true", [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Snapshot.from_status(status(bench_lock_supported=value), 10)

    def test_bench_toggle_requires_fresh_supported_known_connected_state(self):
        sample = Snapshot.from_status(status(valid_flags=0x40), 10)
        self.assertIsNone(sample.bench_lock_block_reason(True, 11))
        self.assertIsNotNone(sample.bench_lock_block_reason(False, 11))
        self.assertIsNotNone(sample.bench_lock_block_reason(True, 14))
        self.assertIsNotNone(sample.bench_lock_block_reason(True, 11, command_pending=True))
        self.assertIsNotNone(sample.bench_lock_block_reason(True, 11, last_error="uncertain"))
        for update in ({"valid_flags": 0}, {"schema": 1}, {"outputs_locked_off": None}):
            with self.subTest(update=update):
                sample = Snapshot.from_status(status(**{"valid_flags": 0x40, **update}), 10)
                self.assertIsNotNone(sample.bench_lock_block_reason(True, 11))

    def test_relock_ignores_operating_faults_sensor_availability_and_prior_error(self):
        sample = Snapshot.from_status(status(outputs_locked_off=False, valid_flags=0x40,
                                             active_faults=[0xFFFFFFFF] * 3, plate_c=[None] * 3,
                                             current_a_nominal=[None] * 3,
                                             current_limits_a=[0, 0, 0],
                                             current_limits_commissioned_mask=0), 10)
        self.assertIsNone(sample.bench_lock_block_reason(True, 11, last_error="uncertain"))
        self.assertIsNotNone(sample.start_block_reason(True, 11))

    def test_commissioning_busy_states_block_unlock_but_allow_relocking(self):
        for state in (2, 4):
            with self.subTest(state=state):
                sample = Snapshot.from_status(status(valid_flags=0xE7,
                    commissioning=commissioning_status(state=state)), 10)
                self.assertTrue(sample.commissioning_running)
                self.assertIsNotNone(sample.bench_lock_block_reason(True, 11))
                sample = Snapshot.from_status(status(valid_flags=0xE7, outputs_locked_off=False,
                    commissioning=commissioning_status(state=state)), 10)
                self.assertIsNone(sample.bench_lock_block_reason(True, 11))
        for state in (0, 1, 3, 5, 6):
            sample = Snapshot.from_status(status(valid_flags=0xE7,
                commissioning=commissioning_status(state=state)), 10)
            self.assertFalse(sample.commissioning_running)

    def test_malformed_commissioning_preserves_normal_status_and_stop_capability(self):
        malformed = ([], "invalid", 1, commissioning_status(state=True),
                     commissioning_status(state=7), commissioning_status(flags=64),
                     commissioning_status(zero_valid=1), commissioning_status(trip_verified=None),
                     commissioning_status(selftest_passed="true"), commissioning_status(nominal_gain=1))
        for commissioning in malformed:
            with self.subTest(commissioning=commissioning):
                sample = Snapshot.from_status(status(valid_flags=0xE7,
                    commissioning=commissioning), 10)
                self.assertEqual(sample.schema, 2)
                self.assertTrue(sample.outputs_locked_off)
                self.assertEqual(sample.plate_c, (25, None, 27))
                self.assertIs(sample.commissioning["valid"], False)
                self.assertFalse(sample.commissioning_running)

    def test_schema_two_malformed_hold_control_keeps_normal_status_for_stop(self):
        valid = commissioning_status(schema=2, state=4, kind=5, control_target_c=27,
            guard_floor_c=21.6, control_duty=0.417, control_state=1, plate_c=27.1)
        for changes in ({"kind": 7}, {"kind": True}, {"control_state": 3}, {"control_state": True},
                        {"control_duty": 0.418}, {"control_duty": -0.001}, {"control_duty": True},
                        {"control_duty": math.nan}, {"control_target_c": None},
                        {"control_target_c": 26.999}, {"control_target_c": "27"}):
            with self.subTest(changes=changes):
                sample = Snapshot.from_status(status(valid_flags=0xE7,
                    commissioning={**valid, **changes}), 10)
                self.assertEqual(sample.schema, 2)
                self.assertTrue(sample.outputs_locked_off)
                self.assertEqual(sample.plate_c, (25, None, 27))
                self.assertIs(sample.commissioning["valid"], False)
                self.assertFalse(sample.commissioning_running)

    def test_reported_mode_and_actual_duty_are_distinct(self):
        sample = Snapshot.from_status(status(mode=1, target_c=None, desired_duty=[1, 1, 1]), 0)
        self.assertEqual(mode_label(sample.mode), "Unregulated (100%)")
        self.assertIsNone(sample.target_c)
        self.assertEqual(sample.applied_duty, (0, 0, 0))
        self.assertEqual(sample.desired_duty, (1, 1, 1))

    def test_invalid_current_is_unavailable_even_with_numeric_conversion(self):
        sample = Snapshot.from_status(status(current_a_nominal=[-61.951, -1.213, -1.192],
                                             active_faults=[6706] * 3,
                                             plate_c=[None] * 3), 0)
        self.assertEqual(sample.current_a, (None, None, None))
        for index in range(3):
            text = channel_status(sample, index, True, 0)
            self.assertIn("Bench locked", text)
            self.assertIn("plate sensor unavailable", text)
            self.assertIn("current sensor unavailable", text)
            self.assertNotIn("hardware gate", text)
            self.assertNotIn("unverified", text)
            self.assertNotIn("limits", text)
        self.assertIn("Bench firmware", sample.start_block_reason(True, 0))

    def test_valid_negative_current_is_retained_and_bench_stale_is_visible(self):
        sample = Snapshot.from_status(status(current_a_nominal=[-0.02, 0, 0],
                                             active_faults=[2, 2 | 1024, 2 | 32]), 0)
        self.assertEqual(sample.current_a, (-0.02, 0, 0))
        self.assertEqual(channel_status(sample, 0, True, 0), "Bench locked · OFF")
        self.assertIn("current reading stale", channel_status(sample, 1, True, 0))
        self.assertIn("plate reading stale", channel_status(sample, 2, True, 0))

    def test_unsupported_protocol_and_fields_rejected(self):
        for data in (status(schema=3), status(mode=2), status(mode=True),
                     status(outputs_locked_off="false"), status(applied_duty=[0, 0]),
                     status(desired_duty=[0, 1.1, 0]), status(active_faults=[0])):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Snapshot.from_status(data, 0)

    def test_schema_one_is_monitor_only(self):
        sample = Snapshot.from_status(status(schema=1, outputs_locked_off=False), 0)
        self.assertIsNone(sample.mode)
        self.assertIn("monitoring only", sample.start_block_reason(True, 0))

    def test_unlocked_start_and_trip_labels(self):
        sample = Snapshot.from_status(status(outputs_locked_off=False, armed_mask=1,
                                             active_faults=[0, 16, 1024]), 0)
        self.assertIn("active trip", sample.start_block_reason(True, 0))
        self.assertEqual(channel_status(sample, 0, True, 0), "Armed")
        self.assertIn("plate sensor", channel_status(sample, 1, True, 0))
        self.assertIn("current stale", channel_status(sample, 2, True, 0))

    def test_start_requires_complete_healthy_acquisition(self):
        healthy = status(outputs_locked_off=False, active_faults=[0, 0, 0],
                         plate_c=[25, 26, 27], valid_flags=63,
                         current_limits_a=[40, 9, 9], current_limits_commissioned_mask=7)
        sample = Snapshot.from_status(healthy, 0)
        self.assertIsNone(sample.start_block_reason(True, 0))
        for update, text in (({"active_faults": None}, "Fault state"),
                             ({"plate_c": [25, None, 27]}, "plate sensors"),
                             ({"current_a_nominal": [0, None, 0]}, "current sensors"),
                             ({"valid_flags": 31}, "acquisition")):
            with self.subTest(update=update):
                sample = Snapshot.from_status({**healthy, **update}, 0)
                self.assertIn(text, sample.start_block_reason(True, 0))

    def test_current_trip_settings_are_reported_and_not_inferred(self):
        sample = Snapshot.from_status(status(current_limits_a=[0, 9, 8.5],
                                             current_limits_commissioned_mask=0), 0)
        self.assertEqual(sample.current_limits_a, (0, 9, 8.5))
        self.assertEqual(current_trip_label(sample, 0), "SW trip unset · uncommissioned")
        self.assertEqual(current_trip_label(sample, 1), "SW trip 9 A · uncommissioned")
        self.assertEqual(current_trip_label(sample, 2), "SW trip 8.5 A · uncommissioned")
        self.assertEqual(current_trip_label(Snapshot.from_status(status(), 0), 1), "SW trip unavailable")
        self.assertEqual(current_trip_label(None, 1), "SW trip unavailable")
        sample = Snapshot.from_status(status(current_limits_a=[40, 9, 9]), 0)
        self.assertIn("commissioning unknown", current_trip_label(sample, 1))
        sample = Snapshot.from_status(status(current_limits_a=[40, 9, 9],
                                             current_limits_commissioned_mask=2), 0)
        self.assertEqual(current_trip_label(sample, 1), "SW trip 9 A")
        self.assertIn("uncommissioned", current_trip_label(sample, 2))

    def test_invalid_current_trip_configuration_is_rejected(self):
        for update in ({"current_limits_a": [40, 9]},
                       *({"current_limits_a": values} for values in
                         ([51, 9, 9], [40, 9.01, 9], [40, 9, 10], [40, -1, 9],
                          [40, math.nan, 9], [40, True, 9], [40, None, 9])),
                       *({"current_limits_commissioned_mask": value} for value in (-1, 8, True, "7"))):
            with self.subTest(update=update), self.assertRaises(ValueError):
                Snapshot.from_status(status(**update), 0)

    def test_supervised_trip_status_uses_actual_verification(self):
        for changes, label in (({}, "Trip 15 A · verify shutdown"),
                               ({"trip_verified": True}, "Trip 15 A · verified"),
                               ({"trip_verified": True, "zero_valid": False}, "Trip 15 A · refresh zero"),
                               ({"state": 2, "stage_prepared": True, "drive": True}, "Trip 5 A · verifying"),
                               ({"state": 4, "stage_prepared": True, "drive": True}, "Trip 15 A · active"),
                               ({"state": 4, "stage_prepared": True}, "Trip 15 A · dry run")):
            with self.subTest(changes=changes):
                sample = Snapshot.from_status(status(current_limits_a=[15, 9, 9],
                    current_limits_commissioned_mask=0, commissioning=commissioning_status(**changes)), 0)
                self.assertEqual(current_trip_label(sample, 0), label)
                self.assertIn("uncommissioned", current_trip_label(sample, 1))
                self.assertIsNotNone(sample.start_block_reason(True, 0))

    def test_active_current_uses_fast_adc_peak_not_idle_conversion(self):
        commissioning = commissioning_status(state=4, stage_prepared=True, drive=True,
                                             last_adc=2040, peak_adc=2340)
        sample = Snapshot.from_status(status(current_a_nominal=[0, 0.12, 0],
            commissioning=commissioning), 0)
        self.assertEqual(current_reading_text(sample, 0), "9.16 A peak")
        self.assertEqual(current_reading_text(sample, 1), "0.12 A")
        self.assertEqual(channel_status(sample, 0, True, 0), "Supervised · cooling")
        self.assertEqual(channel_status(sample, 0, True, 10), "Stale reading")
        for changes in ({"zero_valid": False}, {"stage_prepared": False},
                        {"sample_count": 0}, {"peak_adc": 4095}, {"baseline_adc": None}):
            sample = Snapshot.from_status(status(commissioning={**commissioning, **changes}), 0)
            self.assertEqual(current_reading_text(sample, 0), "—")
        sample = Snapshot.from_status(status(current_a_nominal=[0.03, 0, 0],
            commissioning={**commissioning, "state": 5}), 0)
        self.assertEqual(current_reading_text(sample, 0), "0.03 A")

    def test_start_requires_known_positive_commissioned_current_trips_in_both_modes(self):
        healthy = status(outputs_locked_off=False, active_faults=[0, 0, 0],
                         plate_c=[25, 26, 27], valid_flags=63,
                         current_limits_a=[40, 9, 9], current_limits_commissioned_mask=7)
        for mode in (0, 1):
            for update, reason in (({"current_limits_a": None}, "unavailable"),
                                   ({"current_limits_a": [0, 9, 9]}, "positive"),
                                   ({"current_limits_commissioned_mask": None}, "commissioned"),
                                   ({"current_limits_commissioned_mask": 6}, "commissioned")):
                with self.subTest(mode=mode, update=update):
                    sample = Snapshot.from_status({**healthy, "mode": mode, **update}, 0)
                    self.assertIn(reason, sample.start_block_reason(True, 0))
            self.assertIsNone(Snapshot.from_status({**healthy, "mode": mode}, 0).start_block_reason(True, 0))

    def test_margin_bounds_and_units(self):
        self.assertEqual(margin_millic("0.5"), 500)
        self.assertEqual(margin_millic("3.125"), 3125)
        self.assertEqual(margin_millic("20"), 20000)
        for value in ("", "nan", "inf", "0.49", "20.1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                margin_millic(value)

    def test_stop_cancels_older_pending_arm_without_hardware(self):
        from gui.app import LinkWorker
        calls = []

        class FakeLink:
            def command(self, name, value):
                calls.append(name)
                return status()

            def read_status(self):
                calls.append("read")
                return status()

        worker = LinkWorker()
        worker.link = FakeLink()
        worker.submit(0, "command", [("arm", 7)], priority=2)
        worker.invalidate(1)
        worker.submit(1, "command", [("stop", 0)], priority=0)
        worker.submit(1, "poll")
        worker.start()
        self.assertEqual(worker.events.get(timeout=2)[1], "status")
        self.assertEqual(worker.events.get(timeout=2)[1], "status")
        self.assertEqual(calls, ["stop", "read"])


if __name__ == "__main__":
    unittest.main()
