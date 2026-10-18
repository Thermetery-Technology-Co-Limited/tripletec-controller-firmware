from copy import deepcopy
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import tec1_pulse as pulse


def ready_status():
    """A current, stopped, regulated bench snapshot; TEC1 remains uncommissioned."""
    return {
        "schema": 2, "sequence": 100, "uptime_ms": 10000,
        "outputs_locked_off": True, "armed_mask": 0,
        "mode": 0, "margin_c": 3.0, "valid_flags": 0x27,
        "ambient_c": 27.0, "rh_percent": 60.0,
        "dewpoint_c": 18.5, "target_c": 21.5,
        "plate_c": [27.0, None, None],
        "applied_duty": [0.0, 0.0, 0.0],
        "desired_duty": [0.0, 0.0, 0.0],
        "current_a_nominal": [0.0, 0.0, 0.0],
        "current_adc_raw": [2048, 409, 409],
        "adc_diagnostics": {
            "valid": True, "schema": 1, "sequence": 80,
            "uptime_ms": 9900, "adc_errors": 0,
            "vrefint_adc_raw": 1490, "estimated_vdda_v": 3.3,
        },
        "current_limit_policy": {
            "valid": True, "schema": 1, "sequence": 80, "uptime_ms": 9900,
            "current_limits_a": [0.0, 9.0, 9.0],
            "current_limits_commissioned_mask": 0,
        },
        "current_limits_a": [0.0, 9.0, 9.0],
        "current_limits_commissioned_mask": 0,
    }


class Tec1PulsePreflightTest(unittest.TestCase):
    def assert_rejected(self, status):
        with self.assertRaises((RuntimeError, ValueError)):
            pulse.validate_preflight(status)

    def test_healthy_snapshot_passes_without_changing_operating_policy(self):
        status = ready_status()
        original = deepcopy(status)
        self.assertIsNone(pulse.validate_preflight(status))
        self.assertEqual(status, original)

    def test_global_output_and_control_interlocks(self):
        for key, bad in (
            ("schema", 1), ("schema", None),
            ("outputs_locked_off", False), ("outputs_locked_off", 1),
            ("armed_mask", 1), ("armed_mask", 7),
            ("applied_duty", [0.001, 0, 0]),
            ("applied_duty", [0, 0.001, 0]),
            ("applied_duty", [0, 0, 0.001]),
            ("applied_duty", []), ("applied_duty", [0, 0]),
            ("mode", 1), ("mode", None),
            ("margin_c", 2.999), ("margin_c", None),
            ("margin_c", math.nan), ("margin_c", math.inf),
        ):
            with self.subTest(key=key, bad=bad):
                status = ready_status()
                status[key] = bad
                self.assert_rejected(status)

    def test_each_required_live_sensor_flag_is_required(self):
        for missing in (0x01, 0x02, 0x04, 0x20):
            with self.subTest(missing=missing):
                status = ready_status()
                status["valid_flags"] &= ~missing
                self.assert_rejected(status)
        status = ready_status()
        status["valid_flags"] = 0x3F  # Other connected channels do not block TEC1.
        self.assertIsNone(pulse.validate_preflight(status))

    def test_missing_nonfinite_or_out_of_range_temperature_rejects(self):
        for key in ("ambient_c", "target_c"):
            for bad in (None, math.nan, math.inf, -math.inf):
                with self.subTest(key=key, bad=bad):
                    status = ready_status()
                    status[key] = bad
                    self.assert_rejected(status)
        for bad in (None, math.nan, math.inf, -math.inf, 19.999, 45.001):
            with self.subTest(plate=bad):
                status = ready_status()
                status["plate_c"][0] = bad
                self.assert_rejected(status)
        for bad in (None, [], [None]):
            with self.subTest(plate_list=bad):
                status = ready_status()
                status["plate_c"] = bad
                self.assert_rejected(status)

    def test_plate_requires_strictly_more_than_one_degree_target_headroom(self):
        for plate in (21.0, 21.5, 22.5):
            with self.subTest(plate=plate):
                status = ready_status()
                status["plate_c"][0] = plate
                self.assert_rejected(status)
        status = ready_status()
        status["plate_c"][0] = 22.501
        self.assertIsNone(pulse.validate_preflight(status))
        for plate, target in ((20.0, 18.0), (45.0, 21.5)):
            status = ready_status()
            status["plate_c"][0] = plate
            status["target_c"] = target
            self.assertIsNone(pulse.validate_preflight(status))

    def test_current_baseline_and_nominal_zero_boundaries(self):
        for raw in (1975, 2120):
            status = ready_status()
            status["current_adc_raw"][0] = raw
            self.assertIsNone(pulse.validate_preflight(status))
        for raw in (0, 1974, 2121, 4095, 4096, None):
            with self.subTest(raw=raw):
                status = ready_status()
                status["current_adc_raw"][0] = raw
                self.assert_rejected(status)
        for current in (-1.5, 1.5):
            status = ready_status()
            status["current_a_nominal"][0] = current
            self.assertIsNone(pulse.validate_preflight(status))
        for current in (-1.50001, 1.50001, None, math.nan, math.inf):
            with self.subTest(current=current):
                status = ready_status()
                status["current_a_nominal"][0] = current
                self.assert_rejected(status)

    def test_internal_adc_diagnostic_must_be_valid_and_recent(self):
        for bad in (None, {}, {"valid": False}, {"valid": True}):
            with self.subTest(diagnostics=bad):
                status = ready_status()
                status["adc_diagnostics"] = bad
                self.assert_rejected(status)
        status = ready_status()
        status["adc_diagnostics"]["uptime_ms"] = 8999
        self.assert_rejected(status)
        status["adc_diagnostics"]["uptime_ms"] = 9000
        self.assertIsNone(pulse.validate_preflight(status))
        status["uptime_ms"] = 50
        status["adc_diagnostics"]["uptime_ms"] = 0xFFFFFFFF
        self.assertIsNone(pulse.validate_preflight(status))

    def test_current_policy_must_remain_the_uncommissioned_zero_nine_nine_policy(self):
        for bad in (None, {}, {"valid": False}):
            with self.subTest(policy=bad):
                status = ready_status()
                status["current_limit_policy"] = bad
                self.assert_rejected(status)
        for limits, mask in (([1, 9, 9], 0), ([0, 8, 9], 0), ([0, 9, 10], 0),
                             ([0, 9], 0), ([0, 9, 9], 1), ([0, 9, 9], 6)):
            with self.subTest(limits=limits, mask=mask):
                status = ready_status()
                status["current_limits_a"] = limits
                status["current_limits_commissioned_mask"] = mask
                status["current_limit_policy"]["current_limits_a"] = limits
                status["current_limit_policy"]["current_limits_commissioned_mask"] = mask
                self.assert_rejected(status)

    def test_required_missing_keys_fail_closed(self):
        for key in ("schema", "outputs_locked_off", "armed_mask", "applied_duty", "mode",
                    "margin_c", "valid_flags", "ambient_c", "target_c", "plate_c",
                    "current_a_nominal", "current_adc_raw", "adc_diagnostics",
                    "current_limit_policy", "uptime_ms"):
            with self.subTest(missing=key):
                status = ready_status()
                del status[key]
                self.assert_rejected(status)


class Tec1PulseCaptureTest(unittest.TestCase):
    def test_decodes_all_128_samples_in_little_endian_and_subtracts_baseline(self):
        samples = [2048] * 128
        samples[5] = 2030
        samples[99] = 3000
        result = pulse.decode_capture(struct.pack("<128H", *samples), 2048)
        self.assertEqual(result["peak_adc"], 3000)
        self.assertEqual(result["min_adc"], 2030)
        self.assertEqual(result["sample_count"], 128)
        self.assertAlmostEqual(result["peak_current_a_nominal"],
                               (3000 - 2048) * 3.3 / 4095 / 0.0264)

    def test_nominal_peak_uses_the_supplied_measured_zero(self):
        raw = struct.pack("<128H", *([2100] * 128))
        result = pulse.decode_capture(raw, 2100)
        self.assertEqual(result["peak_current_a_nominal"], 0.0)
        result = pulse.decode_capture(raw, 2000)
        self.assertAlmostEqual(result["peak_current_a_nominal"], 100 * 3.3 / 4095 / 0.0264)

    def test_requires_exact_capture_size(self):
        for length in (0, 1, 128, 254, 255, 257, 258, 512):
            with self.subTest(length=length), self.assertRaises((RuntimeError, ValueError)):
                pulse.decode_capture(bytes(length), 2048)

    def test_rejects_saturated_or_non_adc_samples_even_when_other_samples_are_healthy(self):
        for sample in (0, 4095, 4096, 65535):
            samples = [2048] * 128
            samples[63] = sample
            with self.subTest(sample=sample), self.assertRaises((RuntimeError, ValueError)):
                pulse.decode_capture(struct.pack("<128H", *samples), 2048)

    def test_baseline_must_be_a_plausible_unsaturated_zero(self):
        raw = struct.pack("<128H", *([2048] * 128))
        for baseline in (0, 1974, 2121, 4095, 4096, -1, None, math.nan):
            with self.subTest(baseline=baseline), self.assertRaises((RuntimeError, ValueError)):
                pulse.decode_capture(raw, baseline)
        for baseline in (1975, 2120):
            result = pulse.decode_capture(raw, baseline)
            self.assertEqual(result["sample_count"], 128)


class Tec1PulseScriptTest(unittest.TestCase):
    def context(self, fire=True):
        directory = Path("C:/tec1 test")
        return {
            "backup_path": directory / "ram-before.bin",
            "capture_path": directory / "capture.bin",
            "recovery_path": directory / "recover.tcl",
            "state_path": directory / "state.txt",
            "old_debug": 0x307, "fire": fire,
        }

    def test_fixed_one_pulse_timer_is_launched_exactly_once(self):
        script = pulse.make_pulse_script(self.context())
        commands = [line.strip() for line in script.splitlines()]
        self.assertEqual(commands.count("mww 0x40000000 9"), 1)
        self.assertIn("mww 0x40000018 0x7000", commands)  # TIM2_CH2 PWM2.
        self.assertIn("mww 0x40000028 71", commands)      # 72MHz /72 = 1MHz.
        self.assertIn("mww 0x4000002c 1000", commands)
        self.assertIn("mww 0x40000038 1", commands)
        self.assertIn("mww 0x40000020 0x10", commands)   # CH2 alone enabled.
        self.assertNotIn("mww 0x40000020 0x110", commands)
        self.assertNotIn("mww 0x40000020 0x1110", commands)
        self.assertIn("set cleanup_failed [catch", script)
        self.assertLess(script.index("close $recovery_file"),
                        script.index("mww 0x40000020 0x10"))
        self.assertLess(script.index("set recovery_ready 1"),
                        script.index("mww 0x40000000 9"))

    def test_dry_run_keeps_pa1_gpio_low_and_never_connects_timer_to_the_pin(self):
        script = pulse.make_pulse_script(self.context(fire=False))
        commands = [line.strip() for line in script.splitlines()]
        # CC2E is required for the internal ADC trigger on the measured board.
        # The dry run's external safety boundary is PA1 staying in GPIO LOW.
        self.assertIn("mww 0x40000020 0x10", commands)
        self.assertNotIn("| 0xa0}", script)  # PA1 alternate-function output enable.
        self.assertEqual(commands.count("mww 0x40000000 9"), 1)
        self.assertIn("mww 0x40000020 0", commands)

    def test_recovery_is_written_before_launch_and_contains_only_shutdown_and_restore(self):
        script = pulse.make_pulse_script(self.context())
        recovery_lines = [line.strip() for line in script.splitlines()
                          if line.strip().startswith("puts $recovery_file")]
        self.assertTrue(recovery_lines)
        recovery_text = "\n".join(recovery_lines)
        self.assertIn('"mww 0x40000020 0"', recovery_text)
        self.assertIn('"mww 0x40000000 0"', recovery_text)
        self.assertIn('"mww 0x40010810 0x000e0000"', recovery_text)
        self.assertIn('"mww 0xe0042004 775"', recovery_text)
        self.assertIn('"resume"', recovery_text)
        self.assertNotIn('"mww 0x40000000 9"', recovery_text)
        self.assertNotIn('"mww 0x40000020 0x10"', recovery_text)
        self.assertLess(script.index("set outcome [catch"),
                        script.index("mww 0x40000000 9"))
        self.assertLess(script.index("set cleanup_failed [catch"),
                        script.index("if {$outcome} {error $detail}"))


class Tec1PulseFailureCleanupTest(unittest.TestCase):
    def prepared_link(self):
        link = Mock()
        link._metadata.return_value = {"firmware_sha256": pulse.IMAGE_SHA256}
        before = ready_status()
        fresh = deepcopy(before)
        fresh["sequence"] += 2
        fresh["uptime_ms"] += 300
        link._read.side_effect = [(before, None), (fresh, None)]

        def dump_files(commands):
            # Supply diagnostic files only. This test never invokes OpenOCD.
            for command in commands:
                match = re.fullmatch(r"dump_image \{([^}]+)\} (0x[0-9a-f]+) (\d+)", command)
                self.assertIsNotNone(match, command)
                path = Path(match.group(1))
                path.write_bytes(struct.pack("<I", 0x307) if path.name == "debug.bin" else b"mock image")

        link._run.side_effect = dump_files
        return link

    def test_uncertain_launch_runs_recovery_only_without_a_second_launch(self):
        for failure in (RuntimeError("lost probe"), subprocess.TimeoutExpired("openocd", 12)):
            with self.subTest(failure=type(failure).__name__):
                link = self.prepared_link()
                called = []

                def fail_launch_then_recover(_link, script):
                    called.append(script.name)
                    if script.name == "pulse.tcl":
                        (script.parent / "recover.tcl").write_text(
                            "halt\nmww 0x40000020 0\nmww 0x40000000 0\nresume\n")
                        raise failure
                    self.assertEqual(script.name, "recover.tcl")
                    recovery = script.read_text()
                    self.assertIn("mww 0x40000020 0", recovery)
                    self.assertNotIn("mww 0x40000000 9", recovery)
                    self.assertTrue(recovery.endswith("shutdown\n"))
                    return "recovered"

                with patch.object(pulse.time, "sleep"), \
                        patch.object(pulse.hashlib, "sha256") as digest, \
                        patch.object(pulse, "_cpu_run", side_effect=fail_launch_then_recover):
                    digest.return_value.hexdigest.return_value = pulse.IMAGE_SHA256
                    with self.assertRaisesRegex(RuntimeError, "no repeat issued"):
                        pulse.run_tec1_pulse(link)
                self.assertEqual(called, ["pulse.tcl", "recover.tcl"])

    def test_no_recovery_file_means_no_retry_of_an_uncertain_launch(self):
        link = self.prepared_link()
        with patch.object(pulse.time, "sleep"), \
                patch.object(pulse.hashlib, "sha256") as digest, \
                patch.object(pulse, "_cpu_run", side_effect=RuntimeError("lost probe")) as run:
            digest.return_value.hexdigest.return_value = pulse.IMAGE_SHA256
            with self.assertRaisesRegex(RuntimeError, "no repeat issued"):
                pulse.run_tec1_pulse(link)
            self.assertEqual(run.call_count, 1)

    def test_failed_recovery_reports_uncertainty_and_stops_after_cleanup_attempt(self):
        link = self.prepared_link()
        called = []

        def fail_both(_link, script):
            called.append(script.name)
            if script.name == "pulse.tcl":
                (script.parent / "recover.tcl").write_text("halt\nmww 0x40000020 0\nresume\n")
            raise RuntimeError("probe unavailable")

        with patch.object(pulse.time, "sleep"), \
                patch.object(pulse.hashlib, "sha256") as digest, \
                patch.object(pulse, "_cpu_run", side_effect=fail_both):
            digest.return_value.hexdigest.return_value = pulse.IMAGE_SHA256
            with self.assertRaisesRegex(RuntimeError, "cleanup also failed"):
                pulse.run_tec1_pulse(link)
        self.assertEqual(called, ["pulse.tcl", "recover.tcl"])

    def test_wrong_flash_image_is_rejected_before_any_cpu_transaction(self):
        link = self.prepared_link()
        with patch.object(pulse.time, "sleep"), patch.object(pulse, "_cpu_run") as run:
            with self.assertRaisesRegex(RuntimeError, "differs from the expected bench image"):
                pulse.run_tec1_pulse(link)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
