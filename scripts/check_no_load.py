"""Read-only no-load commissioning observations; never enables a TEC output.

Close the GUI and other probe clients first. Expected setup: no TECs attached.
This screens nominal ADC baselines, not calibration, gain, wiring or 12 V power.
Exit 0 means the observed no-load checks passed, never permission to cool.
Exit 1 means follow-up checks remain; exit 2 means acquisition was incomplete.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import time

try:
    from .device_link import DeviceLink, PROBE_MUTEX
except ImportError:
    from device_link import DeviceLink, PROBE_MUTEX

ADC_MAX = 4095
NOMINAL_REFERENCE_V = 3.3
ZERO_FRACTIONS = (0.5, 0.1, 0.1)
FAULT_NAMES = (
    "configuration", "hardware_gate", "ambient_invalid", "ambient_stale",
    "plate_invalid", "plate_stale", "timing", "target_range", "arithmetic",
    "current_invalid", "current_stale", "current_unverified",
    "current_uncommissioned", "current_overlimit",
)
# These intentionally inhibit the bench build and must never be cleared by this check.
EXPECTED_BENCH_FAULTS = (1 << 1) | (1 << 11) | (1 << 12)


class ReadOnlyLink(DeviceLink):
    """Bypass DeviceLink.read_status's optional armed-channel heartbeat write."""

    def read_status(self) -> dict:
        with PROBE_MUTEX:
            return self._read(self._metadata())[0]


def _u32(value) -> bool:
    return type(value) is int and 0 <= value <= 0xFFFFFFFF


def _finite(value) -> bool:
    return (type(value) in (int, float) and math.isfinite(value))


def progression(values: list, *, require_advance: bool = True) -> dict:
    """Recognize ordinary uint32 wraps; backwards/reset intervals stay ambiguous."""
    result = {"valid": True, "first": values[0] if values else None,
              "last": values[-1] if values else None, "advance": 0,
              "wraps": 0, "stalls": 0, "backwards_or_reset": 0}
    if not values or any(not _u32(value) for value in values):
        result["valid"] = False
        return result
    for before, after in zip(values, values[1:]):
        delta = (after - before) & 0xFFFFFFFF
        if delta == 0:
            result["stalls"] += 1
        elif delta >= 0x80000000:
            result["backwards_or_reset"] += 1
        else:
            result["advance"] += delta
            result["wraps"] += int(after < before)
    result["valid"] = (len(values) >= 2 and not result["backwards_or_reset"] and
                       (not require_advance or not result["stalls"]))
    return result


def baseline(raw: list, channel: int) -> dict:
    """A conservative screen using explicit, non-calibration acceptance bands."""
    expected = ADC_MAX * ZERO_FRACTIONS[channel]
    result = {"channel": channel + 1, "expected_zero_raw_nominal": expected,
              "expected_zero_v_nominal": NOMINAL_REFERENCE_V * ZERO_FRACTIONS[channel],
              "classification": "invalid_adc", "plausible_zero": False}
    if not raw or any(type(code) is not int or not 0 <= code <= ADC_MAX for code in raw):
        return result
    lo, hi = min(raw), max(raw)
    mean = statistics.mean(raw)
    span = hi - lo
    deviation = max(abs(lo - expected), abs(hi - expected))
    result.update({"raw": {"min": lo, "mean": mean, "max": hi, "span": span},
                   "voltage_v_nominal": {
                       "min": lo * NOMINAL_REFERENCE_V / ADC_MAX,
                       "mean": mean * NOMINAL_REFERENCE_V / ADC_MAX,
                       "max": hi * NOMINAL_REFERENCE_V / ADC_MAX},
                   "max_deviation_raw": deviation})
    if lo <= ADC_MAX * 0.02 or hi >= ADC_MAX * 0.98:
        label = "near_rail"
    elif deviation > ADC_MAX * 0.05:
        label = "implausible_zero"
    elif span > ADC_MAX * 0.02:
        label = "unstable_zero"
    elif deviation <= ADC_MAX * 0.02:
        label = "plausible_zero"
    else:
        label = "marginal_zero"
    result["classification"] = label
    result["plausible_zero"] = label == "plausible_zero"
    return result


def _triple(sample: dict, key: str) -> list:
    value = sample.get(key)
    return value if isinstance(value, (list, tuple)) and len(value) == 3 else [None] * 3


def _faults(value: int) -> list[str]:
    names = [name for bit, name in enumerate(FAULT_NAMES) if value & (1 << bit)]
    unknown = value & ~((1 << len(FAULT_NAMES)) - 1)
    if unknown:
        names.append(f"unknown_bits_0x{unknown:x}")
    return names


def summarize(samples: list[dict], *, requested_samples: int | None = None,
              acquisition_error: str | None = None) -> dict:
    """Summarize observations without changing settings or inferring physical power."""
    requested_samples = len(samples) if requested_samples is None else requested_samples
    blockers = []
    report = {
        "format": "tripletec-no-load-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(samples), "requested_samples": requested_samples,
        "acquisition_ok": acquisition_error is None and len(samples) == requested_samples and len(samples) >= 2,
        "acquisition_error": acquisition_error,
        "no_load_checks_passed": False,
        "assumptions": [
            "No TEC loads attached is operator-provided setup information, not sensed by firmware.",
            "Current ADC is 12-bit; displayed voltage assumes a nominal 3.3 V ADC reference.",
            "Nominal ACS725 supply is 3.3 V; zero output is 50% of supply on channel 1 and 10% on channels 2/3.",
            "Screening bands are diagnostic choices, not datasheet tolerances: near rail <=2% or >=98% ADC span; plausible zero within 2% span of expected; marginal within 5%; unstable span >2%.",
            "This does not calibrate offsets or sensitivity, verify wiring or current gain, or prove 12 V presence.",
            "Passing no-load checks does not authorize cooling; output lock and commissioning interlocks remain required.",
        ],
        "blockers": blockers,
    }
    if not report["acquisition_ok"]:
        blockers.append("incomplete_acquisition")
    if not samples:
        return report

    sequence = progression([s.get("sequence") for s in samples])
    if any(not _u32(s.get("sequence")) or s["sequence"] & 1 for s in samples):
        sequence["valid"] = False
    uptime = progression([s.get("uptime_ms") for s in samples])
    report["progress"] = {"sequence": sequence, "uptime_ms": uptime}
    if not sequence["valid"] or not uptime["valid"]:
        blockers.append("telemetry_stale_or_reset")
    if any(s.get("schema") != 2 for s in samples):
        blockers.append("unsupported_status_schema")

    output_ok = all(s.get("outputs_locked_off") is True and type(s.get("armed_mask")) is int and
                    s["armed_mask"] == 0 and
                    all(type(duty) in (float, int) and duty == 0 for duty in _triple(s, "applied_duty")) and
                    all(type(duty) in (float, int) and duty == 0 for duty in _triple(s, "desired_duty"))
                    for s in samples)
    report["outputs_locked_and_zero"] = output_ok
    if not output_ok:
        blockers.append("output_lock_or_zero_state_not_confirmed")

    flags = [s.get("valid_flags") for s in samples]
    flags_valid = all(_u32(value) for value in flags)
    report["valid_flags_observed"] = sorted(set(flags)) if flags_valid else flags
    if not flags_valid or any(value & 0x23 != 0x23 for value in flags):
        blockers.append("ambient_ads_or_current_acquisition_not_valid")
    plates = []
    for channel in range(3):
        present = flags_valid and all(value & (1 << (channel + 2)) for value in flags)
        values = [_triple(s, "plate_c")[channel] for s in samples]
        present = bool(present and all(_finite(value) for value in values))
        plates.append({"channel": channel + 1, "valid_in_all_samples": present,
                       "last_c": values[-1]})
        if not present:
            blockers.append(f"plate_{channel + 1}_missing_or_invalid")
    report["plate_sensors"] = plates

    currents = [baseline([_triple(s, "current_adc_raw")[channel] for s in samples], channel)
                for channel in range(3)]
    report["current_baselines"] = currents
    for current in currents:
        if not current["plausible_zero"]:
            blockers.append(f"current_{current['channel']}_{current['classification']}")

    counters = {}
    for key in ("sht31_errors", "ads1220_errors"):
        counts = progression([s.get(key) for s in samples], require_advance=False)
        # Counter decreases at reset cannot be interpreted as an unsigned error delta.
        if not uptime["valid"]:
            counts["valid"] = False
        counters[key] = counts
        if not counts["valid"] or counts["advance"]:
            blockers.append(key + "_advanced_or_ambiguous")
    report["sensor_error_counters"] = counters

    faults = []
    for channel in range(3):
        values = [_triple(s, "active_faults")[channel] for s in samples]
        valid = all(_u32(value) for value in values)
        combined = 0
        if valid:
            for value in values:
                combined |= value
        unexpected = combined & ~EXPECTED_BENCH_FAULTS
        faults.append({"channel": channel + 1, "valid": valid,
                       "observed_masks": sorted(set(values)) if valid else values,
                       "observed_names": _faults(combined),
                       "intentional_bench_inhibits": _faults(combined & EXPECTED_BENCH_FAULTS),
                       "other_faults": _faults(unexpected)})
        if not valid or unexpected:
            blockers.append(f"channel_{channel + 1}_firmware_faults")
    report["firmware_faults"] = faults

    diagnostics = [s.get("adc_diagnostics") for s in samples]
    report["adc_diagnostics"] = {"available": False, "acquisition_validated": False}
    if any(value is not None for value in diagnostics):
        complete = all(isinstance(value, dict) for value in diagnostics)
        diagnostic_sequence = progression([value.get("sequence") if isinstance(value, dict) else None
                                           for value in diagnostics])
        if any(not isinstance(value, dict) or not _u32(value.get("sequence")) or
               value["sequence"] & 1 for value in diagnostics):
            diagnostic_sequence["valid"] = False
        diagnostic_valid = complete and all(
            value.get("schema") == 1 and value.get("valid") is True and
            type(value.get("vrefint_adc_raw")) is int and 0 < value["vrefint_adc_raw"] < ADC_MAX and
            _finite(value.get("estimated_vdda_v")) and value["estimated_vdda_v"] > 0 and
            _u32(value.get("uptime_ms")) and _u32(sample.get("uptime_ms")) and
            # Separate block reads may observe ADC publication just after status.
            min((sample["uptime_ms"] - value["uptime_ms"]) & 0xFFFFFFFF,
                (value["uptime_ms"] - sample["uptime_ms"]) & 0xFFFFFFFF) <= 2000
            for sample, value in zip(samples, diagnostics))
        error_counts = progression([value.get("adc_errors") if isinstance(value, dict) else None
                                    for value in diagnostics], require_advance=False)
        if not uptime["valid"]:
            error_counts["valid"] = False
        diagnostic_valid = (diagnostic_valid and diagnostic_sequence["valid"] and
                            error_counts["valid"] and error_counts["advance"] == 0)
        report["adc_diagnostics"] = {
            "available": complete, "acquisition_validated": diagnostic_valid,
            "last": diagnostics[-1], "sequence_progress": diagnostic_sequence,
            "adc_error_counter": error_counts,
            "vdda_note": "Estimated VDDA uses a nominal 1.2 V internal reference; it is not a calibrated rail measurement.",
        }
        if not diagnostic_valid:
            blockers.append("adc_diagnostics_invalid_stale_or_errors")
    else:
        report["adc_diagnostics"]["note"] = "Legacy status has no internal-reference observation; ADC acquisition is not independently validated."
    report["no_load_checks_passed"] = not blockers
    return report


def collect(link, count: int, interval: float) -> tuple[list[dict], str | None]:
    samples = []
    for index in range(count):
        try:
            sample = link.read_status()
            if not isinstance(sample, dict):
                raise RuntimeError("Device returned an unsupported status response")
            samples.append(sample)
        except (RuntimeError, OSError, ValueError) as error:
            return samples, str(error)
        if index + 1 < count:
            time.sleep(interval)
    return samples, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=12, help="2 to 200 snapshots (default: 12)")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="0.25 to 5 seconds between reads (default: 0.5)")
    parser.add_argument("--serial", help="CMSIS-DAP probe serial number")
    parser.add_argument("--openocd", help="OpenOCD executable")
    parser.add_argument("--output", type=Path, help="Save complete JSON report, including raw samples")
    args = parser.parse_args(argv)
    if not 2 <= args.samples <= 200:
        parser.error("--samples must be between 2 and 200")
    if not math.isfinite(args.interval) or not 0.25 <= args.interval <= 5:
        parser.error("--interval must be between 0.25 and 5 seconds")
    try:
        samples, error = collect(ReadOnlyLink(serial=args.serial, openocd=args.openocd),
                                 args.samples, args.interval)
    except (RuntimeError, ValueError) as problem:
        samples, error = [], str(problem)
    report = summarize(samples, requested_samples=args.samples, acquisition_error=error)
    report["samples"] = samples
    rendered = json.dumps(report, indent=2, allow_nan=False) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 2 if not report["acquisition_ok"] else 0 if report["no_load_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
