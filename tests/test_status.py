import importlib.util
from pathlib import Path
import struct
import unittest

source = Path(__file__).resolve().parents[1] / "scripts" / "read_status.py"
spec = importlib.util.spec_from_file_location("read_status", source)
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


def frame(sequence=2, ambient=25000):
    words = [0] * 32
    words[0:5] = [0x54454331, sequence, 1, 1000, 1]
    words[8:12] = [ambient, 50000, 13852, 16852]
    words[12] = 0x80000000
    return struct.pack("<32I", *words)


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


if __name__ == "__main__":
    unittest.main()
