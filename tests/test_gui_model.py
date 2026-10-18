import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui.model import Snapshot, channel_status, format_value, margin_millic, mode_label


def status(**changes):
    data = {"schema": 2, "mode": 0, "margin_c": 3.0, "armed_mask": 0,
            "outputs_locked_off": True, "plate_c": [25, None, 27],
            "current_a_nominal": [0, 0, 0], "applied_duty": [0, 0, 0],
            "desired_duty": [0, 0, 0], "active_faults": [2, 18, 2]}
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
                         plate_c=[25, 26, 27], valid_flags=63)
        sample = Snapshot.from_status(healthy, 0)
        self.assertIsNone(sample.start_block_reason(True, 0))
        for update, text in (({"active_faults": None}, "Fault state"),
                             ({"plate_c": [25, None, 27]}, "plate sensors"),
                             ({"current_a_nominal": [0, None, 0]}, "current sensors"),
                             ({"valid_flags": 31}, "acquisition")):
            with self.subTest(update=update):
                sample = Snapshot.from_status({**healthy, **update}, 0)
                self.assertIn(text, sample.start_block_reason(True, 0))

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
