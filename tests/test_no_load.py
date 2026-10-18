from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import check_no_load as check


def frames(count=3):
    return [{"schema": 2, "sequence": 2 + index * 4, "uptime_ms": 1000 + index * 500,
             "outputs_locked_off": True, "armed_mask": 0,
             "applied_duty": [0, 0, 0], "desired_duty": [0, 0, 0],
             "valid_flags": 0x3F, "plate_c": [25.0, 25.0, 25.0],
             "current_adc_raw": [2048 + index, 410 + index, 409 + index],
             "active_faults": [check.EXPECTED_BENCH_FAULTS] * 3,
             "sht31_errors": 7, "ads1220_errors": 8} for index in range(count)]


class NoLoadTest(unittest.TestCase):
    def test_healthy_nominal_zero_is_only_a_screen(self):
        report = check.summarize(frames())
        self.assertTrue(report["acquisition_ok"])
        self.assertTrue(report["no_load_checks_passed"])
        self.assertEqual(report["blockers"], [])
        self.assertTrue(all(item["plausible_zero"] for item in report["current_baselines"]))
        self.assertAlmostEqual(report["current_baselines"][0]["voltage_v_nominal"]["min"],
                               2048 * 3.3 / 4095)
        self.assertFalse(report["adc_diagnostics"]["acquisition_validated"])
        self.assertTrue(report["firmware_faults"][0]["intentional_bench_inhibits"])

    def test_live_near_ground_is_not_accepted_as_zero_current(self):
        samples = frames()
        for sample in samples:
            sample["current_adc_raw"] = [18, 12, 19]
            sample["valid_flags"] = 0x23
            sample["plate_c"] = [None] * 3
        report = check.summarize(samples)
        self.assertTrue(report["acquisition_ok"])
        self.assertFalse(report["no_load_checks_passed"])
        self.assertEqual([x["classification"] for x in report["current_baselines"]], ["near_rail"] * 3)
        self.assertIn("plate_1_missing_or_invalid", report["blockers"])
        self.assertFalse(any(x["plausible_zero"] for x in report["current_baselines"]))

    def test_baseline_marginal_and_rail_and_span_classification(self):
        self.assertEqual(check.baseline([520, 521], 1)["classification"], "marginal_zero")
        self.assertEqual(check.baseline([800, 801], 1)["classification"], "implausible_zero")
        self.assertEqual(check.baseline([4000, 4090], 0)["classification"], "near_rail")
        self.assertEqual(check.baseline([350, 450], 1)["classification"], "unstable_zero")
        for raw in ([4096], [True], [None], []):
            self.assertEqual(check.baseline(raw, 1)["classification"], "invalid_adc")

    def test_missing_plate_keeps_readiness_false_without_acquisition_failure(self):
        samples = frames()
        samples[-1]["plate_c"][2] = None
        report = check.summarize(samples)
        self.assertTrue(report["acquisition_ok"])
        self.assertFalse(report["no_load_checks_passed"])
        self.assertIn("plate_3_missing_or_invalid", report["blockers"])

    def test_uint32_wrap_is_recognized(self):
        samples = frames()
        for sample, sequence, uptime in zip(samples, (0xFFFFFFFC, 0, 4), (0xFFFFFF00, 244, 744)):
            sample.update(sequence=sequence, uptime_ms=uptime)
        report = check.summarize(samples)
        self.assertTrue(report["no_load_checks_passed"])
        self.assertEqual(report["progress"]["sequence"]["wraps"], 1)
        self.assertEqual(report["progress"]["uptime_ms"]["advance"], 1000)

    def test_reset_and_stale_publications_are_fail_closed(self):
        for key, replacement in (("uptime_ms", 10), ("sequence", 2), ("sequence", 5)):
            samples = frames()
            samples[-1][key] = replacement
            report = check.summarize(samples)
            self.assertFalse(report["no_load_checks_passed"])
            self.assertIn("telemetry_stale_or_reset", report["blockers"])
        samples = frames()
        samples[1] = deepcopy(samples[0])
        self.assertFalse(check.summarize(samples)["no_load_checks_passed"])

    def test_sensor_counter_delta_and_wrap(self):
        samples = frames()
        for sample, value in zip(samples, (0xFFFFFFFE, 0xFFFFFFFF, 1)):
            sample["sht31_errors"] = value
        report = check.summarize(samples)
        self.assertFalse(report["no_load_checks_passed"])
        self.assertEqual(report["sensor_error_counters"]["sht31_errors"]["advance"], 3)
        self.assertTrue(report["sensor_error_counters"]["sht31_errors"]["valid"])
        samples = frames()
        samples[-1]["ads1220_errors"] = 0
        report = check.summarize(samples)
        self.assertFalse(report["sensor_error_counters"]["ads1220_errors"]["valid"])

    def test_output_lock_arm_and_duty_are_checked_for_every_sample(self):
        for key, value in (("outputs_locked_off", False), ("armed_mask", 1),
                           ("armed_mask", False),
                           ("applied_duty", [0, 0.001, 0]), ("desired_duty", [1, 0, 0]),
                           ("applied_duty", [False, 0, 0])):
            samples = frames()
            samples[0][key] = value
            report = check.summarize(samples)
            self.assertFalse(report["outputs_locked_and_zero"])
            self.assertFalse(report["no_load_checks_passed"])

    def test_unexpected_and_unknown_faults_are_reported(self):
        samples = frames()
        samples[0]["active_faults"][0] |= (1 << 9) | (1 << 25)
        report = check.summarize(samples)
        self.assertFalse(report["no_load_checks_passed"])
        self.assertIn("current_invalid", report["firmware_faults"][0]["other_faults"])
        self.assertIn("unknown_bits_0x2000000", report["firmware_faults"][0]["other_faults"])

    def test_optional_internal_reference_diagnostics(self):
        samples = frames()
        for sample in samples:
            sample["adc_diagnostics"] = {"schema": 1, "sequence": sample["sequence"],
                                         "uptime_ms": sample["uptime_ms"] - 1,
                                         "valid": True, "vrefint_adc_raw": 1490,
                                         "estimated_vdda_v": 3.298, "adc_errors": 0}
        report = check.summarize(samples)
        self.assertTrue(report["adc_diagnostics"]["acquisition_validated"])
        self.assertTrue(report["no_load_checks_passed"])
        for key, value in (("valid", False), ("uptime_ms", 0xFFFFFF00), ("adc_errors", 1)):
            changed = deepcopy(samples)
            changed[-1]["adc_diagnostics"][key] = value
            report = check.summarize(changed)
            self.assertFalse(report["adc_diagnostics"]["acquisition_validated"])
            self.assertFalse(report["no_load_checks_passed"])

    def test_adc_block_can_be_newer_and_must_advance(self):
        samples = frames()
        for sample in samples:
            sample["adc_diagnostics"] = {"schema": 1, "sequence": sample["sequence"],
                                         "uptime_ms": sample["uptime_ms"] + 1,
                                         "valid": True, "vrefint_adc_raw": 1490,
                                         "estimated_vdda_v": 3.298, "adc_errors": 0}
        self.assertTrue(check.summarize(samples)["adc_diagnostics"]["acquisition_validated"])
        samples[1]["adc_diagnostics"]["sequence"] = samples[0]["adc_diagnostics"]["sequence"]
        self.assertFalse(check.summarize(samples)["adc_diagnostics"]["acquisition_validated"])

    def test_incomplete_adc_block_is_not_treated_as_legacy(self):
        samples = frames()
        samples[0]["adc_diagnostics"] = {"valid": True}
        report = check.summarize(samples)
        self.assertFalse(report["adc_diagnostics"]["acquisition_validated"])
        self.assertIn("adc_diagnostics_invalid_stale_or_errors", report["blockers"])

    def test_partial_and_empty_acquisition(self):
        report = check.summarize(frames(2), requested_samples=12, acquisition_error="SWD timed out")
        self.assertFalse(report["acquisition_ok"])
        self.assertFalse(report["no_load_checks_passed"])
        self.assertEqual(report["acquisition_error"], "SWD timed out")
        self.assertFalse(check.summarize([])["no_load_checks_passed"])

    def test_read_only_transport_never_heartbeats_armed_status(self):
        link = check.ReadOnlyLink(openocd="unused")
        link._metadata = Mock(return_value={"address": 0x20000000})
        link._read = Mock(return_value=({"armed_mask": 7}, None))
        link.command = Mock(side_effect=AssertionError("No command is permitted"))
        self.assertEqual(link.read_status(), {"armed_mask": 7})
        link._read.assert_called_once_with({"address": 0x20000000})
        link.command.assert_not_called()

    def test_collection_reads_only_and_preserves_failure(self):
        link = Mock()
        link.read_status.side_effect = [frames(1)[0], RuntimeError("probe unavailable")]
        with patch.object(check.time, "sleep") as sleep:
            result, error = check.collect(link, 3, 0.5)
        self.assertEqual(len(result), 1)
        self.assertEqual(error, "probe unavailable")
        sleep.assert_called_once_with(0.5)
        self.assertEqual(link.method_calls, [unittest.mock.call.read_status(), unittest.mock.call.read_status()])


if __name__ == "__main__":
    unittest.main()
