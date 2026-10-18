import importlib.util
from pathlib import Path
import struct
import unittest

source = Path(__file__).resolve().parents[1] / "scripts" / "read_status.py"
spec = importlib.util.spec_from_file_location("read_status", source)
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


def frame(sequence=2, ambient=25000, schema=1):
    words = [0] * (40 if schema == 2 else 32)
    words[0:5] = [0x54454331, sequence, schema, 1000, 1]
    words[8:12] = [ambient, 50000, 13852, 16852]
    words[12] = 0x80000000
    if schema == 2:
        words[32:40] = [1, 3500, 0, 0, 0, 0, 17, 5]
    return struct.pack(f"<{len(words)}I", *words)


class StatusTest(unittest.TestCase):
    def test_units_and_invalid_sentinel(self):
        result = status.decode(frame())
        self.assertEqual(result["ambient_c"], 25.0)
        self.assertEqual(result["rh_percent"], 50.0)
        self.assertEqual(result["target_c"], 16.852)
        self.assertIsNone(result["plate_c"][0])
        self.assertTrue(result["outputs_locked_off"])

    def test_validate_first_payload_not_second(self):
        first = frame(2, 25000)
        second = frame(2, 99000)  # writer began after second's sequence read
        self.assertEqual(status.consistent_snapshot(first, second)["ambient_c"], 25.0)

    def test_schema_two_mode_settings_and_rejected_command(self):
        result = status.decode(frame(schema=2))
        self.assertEqual(result["mode"], 1)
        self.assertEqual(result["mode_name"], "unregulated")
        self.assertEqual(result["margin_c"], 3.5)
        self.assertEqual(result["armed_mask"], 0)
        self.assertEqual(result["desired_duty"], [0, 0, 0])
        self.assertEqual(result["command_ack_seq"], 17)
        self.assertEqual(result["command_result_name"], "BENCH_LOCKED")
        with self.assertRaises(ValueError):
            status.decode(frame(schema=2)[:128])

    def test_schema_two_seqlock_uses_first_payload(self):
        self.assertEqual(status.consistent_snapshot(frame(2, 25000, 2),
                                                   frame(2, 99000, 2))["ambient_c"], 25)

    def test_reject_publication_in_progress(self):
        with self.assertRaises(ValueError):
            status.consistent_snapshot(frame(3), frame(3))

    def test_reject_changed_sequence(self):
        with self.assertRaises(ValueError):
            status.consistent_snapshot(frame(2), frame(4))

    def test_reject_wrong_image_or_truncation(self):
        with self.assertRaises(ValueError):
            status.decode(bytes(128))
        with self.assertRaises(ValueError):
            status.decode(bytes(127))

    def test_adc_reference_diagnostic_uses_first_consistent_payload(self):
        first = struct.pack("<8I", 0x54454344, 2, 1, 1000, 1489, 3300, 3, 0)
        later = struct.pack("<8I", 0x54454344, 2, 1, 1001, 10, 49000, 1, 1)
        result = status.consistent_adc_diagnostics(first, later)
        self.assertTrue(result["valid"])
        self.assertEqual(result["vrefint_adc_raw"], 1489)
        self.assertEqual(result["estimated_vdda_v"], 3.3)
        self.assertEqual(result["adc_errors"], 0)

    def test_adc_diagnostics_invalid_acquisition_and_torn_data(self):
        invalid = struct.pack("<8I", 0x54454344, 4, 1, 1000, 0, 0x80000000, 0, 1)
        result = status.consistent_adc_diagnostics(invalid, invalid)
        self.assertFalse(result["valid"])
        self.assertIsNone(result["estimated_vdda_v"])
        for other in (bytes(32), invalid[:28],
                      struct.pack("<8I", 0x54454344, 6, 1, 1000, 0, 0, 0, 0)):
            with self.subTest(other=other), self.assertRaises(ValueError):
                status.consistent_adc_diagnostics(invalid, other)


if __name__ == "__main__":
    unittest.main()
