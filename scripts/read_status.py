"""Read live bench telemetry over Pico SWD without halting or resetting the MCU."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
INVALID = -(2**31)


def decode(data: bytes) -> dict:
    if len(data) not in (128, 160):
        raise ValueError(f"Expected 128 or 160 bytes, received {len(data)}")
    words = struct.unpack(f"<{len(data) // 4}I", data)
    signed = struct.unpack(f"<{len(data) // 4}i", data)
    if words[0] != 0x54454331 or words[2] not in (1, 2):
        raise ValueError("Bench firmware telemetry is absent or has a different schema")
    if words[2] == 2 and len(data) != 160:
        raise ValueError("Schema 2 telemetry is truncated")
    if words[1] & 1:
        raise ValueError("Telemetry was being updated; retry the snapshot")

    def value(index: int):
        return None if signed[index] == INVALID else signed[index] / 1000

    result = {
        "schema": words[2], "sequence": words[1], "uptime_ms": words[3],
        "outputs_locked_off": bool(words[4]), "valid_flags": words[5],
        "bench_lock_supported": words[2] == 2 and bool(words[5] & 0x40),
        "commissioning_supported": words[2] == 2 and bool(words[5] & 0x80),
        "sht31_errors": words[6], "ads1220_errors": words[7],
        "ambient_c": value(8), "rh_percent": value(9),
        "dewpoint_c": value(10), "target_c": value(11),
        "plate_c": [value(i) for i in range(12, 15)],
        "ntc_adc_raw": list(signed[15:18]),
        "current_adc_raw": list(words[18:21]),
        "current_a_nominal": [value(i) for i in range(21, 24)],
        "applied_duty": [words[i] / 1000 for i in range(24, 27)],
        "active_faults": list(words[27:30]),
        "loop_count": words[30], "button_pressed": bool(words[31]),
    }
    if words[2] == 2:
        result.update({
            "mode": words[32],
            "mode_name": {0: "regulated", 1: "unregulated"}.get(words[32], "unknown"),
            "margin_c": value(33), "armed_mask": words[34],
            "desired_duty": [words[i] / 1000 for i in range(35, 38)],
            "command_ack_seq": words[38], "command_result": words[39],
            "command_result_name": {
                0: "OK", 1: "BAD_PROTOCOL", 2: "BAD_CHECKSUM", 3: "BAD_OPCODE",
                4: "BAD_VALUE", 5: "BENCH_LOCKED", 6: "INTERLOCK",
            }.get(words[39], "UNKNOWN"),
        })
    return result


def consistent_snapshot(first: bytes, second: bytes) -> dict:
    if len(first) not in (128, 160) or len(first) != len(second) or first[4:8] != second[4:8]:
        raise ValueError("Snapshot sequence changed")
    # The first payload lies between the two sequence reads. The second
    # payload can be torn by a publication after its own sequence was read.
    return decode(first)


def consistent_adc_diagnostics(first: bytes, second: bytes) -> dict:
    if len(first) != 32 or len(second) != 32 or first[4:8] != second[4:8]:
        raise ValueError("ADC diagnostic snapshot changed or is incomplete")
    words = struct.unpack("<8I", first)
    if words[0] != 0x54454344 or words[2] != 1 or words[1] & 1:
        raise ValueError("ADC diagnostic identity or sequence is invalid")
    if words[4] > 4095 or words[6] & ~3:
        raise ValueError("ADC diagnostic values are invalid")
    vdda_mv = struct.unpack_from("<i", first, 20)[0]
    return {
        "schema": words[2], "sequence": words[1], "uptime_ms": words[3],
        "valid": words[6] == 3, "acquisition_ok": bool(words[6] & 1),
        "reference_plausible": bool(words[6] & 2), "vrefint_adc_raw": words[4],
        "estimated_vdda_v": None if vdda_mv == INVALID else vdda_mv / 1000,
        "adc_errors": words[7],
        "reference_basis": "Nominal 1.20 V VREFINT; estimate only, not calibrated",
    }


def consistent_limit_configuration(first: bytes, second: bytes) -> dict:
    if len(first) != 32 or len(second) != 32 or first[4:8] != second[4:8]:
        raise ValueError("Current-limit snapshot changed or is incomplete")
    words = struct.unpack("<8I", first)
    if words[0] != 0x5445434C or words[2] != 1 or words[1] & 1:
        raise ValueError("Current-limit identity or sequence is invalid")
    if words[4] > 50000 or words[5] > 9000 or words[6] > 9000 or words[7] & ~7:
        raise ValueError("Current-limit policy is outside supported ranges")
    if any(words[7] & (1 << index) and words[4 + index] == 0 for index in range(3)):
        raise ValueError("A commissioned current limit cannot be zero")
    return {
        "valid": True, "schema": words[2], "sequence": words[1], "uptime_ms": words[3],
        "current_limits_a": [value / 1000 for value in words[4:7]],
        "current_limits_commissioned_mask": words[7],
    }


def consistent_commissioning(first: bytes, second: bytes) -> dict:
    if len(first) not in (80, 104) or len(first) != len(second) or first[4:8] != second[4:8]:
        raise ValueError("Commissioning snapshot changed or is incomplete")
    words = struct.unpack(f"<{len(first) // 4}I", first)
    signed = struct.unpack(f"<{len(first) // 4}i", first)
    if (words[0] != 0x5445434D or words[2] not in (1, 2) or words[1] & 1 or
            len(first) != {1: 80, 2: 104}[words[2]]):
        raise ValueError("Commissioning identity or sequence is invalid")
    if words[4] > 6 or words[5] & ~0x3F or words[12] > 9:
        raise ValueError("Commissioning state, flags, or fault code is invalid")
    if any(words[index] > 4095 for index in (7, 8, 9, 18)):
        raise ValueError("Commissioning ADC values are invalid")
    # An idle block may contain its zero-initialized values before preparation.
    # Every active or finished attempt must identify the fixed test profile.
    if words[6] not in ((0, 15000) if words[4] == 0 else (15000,)):
        raise ValueError("Commissioning test profile is invalid")
    extension = dict.fromkeys(("kind", "control_target_c", "guard_floor_c", "control_duty",
                               "control_state", "control_state_name", "plate_c"))
    if words[2] == 2:
        kind = words[20]
        if kind > 6 or words[23] > 417 or words[24] > 2:
            raise ValueError("Commissioning control fields are invalid")
        if signed[21] != (27000 if kind in (5, 6) else INVALID):
            raise ValueError("Commissioning control target is invalid")
        if kind < 5 and (words[23] != 0 or words[24] != 0):
            raise ValueError("Inactive commissioning control must report zero duty")
        extension.update({
            "kind": kind,
            "control_target_c": None if signed[21] == INVALID else signed[21] / 1000,
            "guard_floor_c": None if signed[22] == INVALID else signed[22] / 1000,
            "control_duty": words[23] / 1000, "control_state": words[24],
            "control_state_name": {0: "inactive", 1: "cooling", 2: "holding"}[words[24]],
            "plate_c": None if signed[25] == INVALID else signed[25] / 1000,
        })
    if words[4] == 0:
        valid_timing = (words[13] in (0, 1000) and words[14] in (0, 1000) and
                        words[15] in (0, 20) and extension["kind"] in (None, 0))
    elif words[2] == 2:
        kind = words[20]
        expected_timing = ((1000, 1000, 20) if kind <= 2 else
                           (30000, 5000, 12) if kind <= 4 else (300000, 5000, 12))
        valid_timing = ((words[13], words[14], words[15]) == expected_timing and
                        (kind <= 2 or words[4] in (4, 5, 6)))
    else:
        allowed_timings = (((1000, 1000, 20), (30000, 5000, 12))
                           if words[4] in (4, 5, 6) else ((1000, 1000, 20),))
        valid_timing = (words[13], words[14], words[15]) in allowed_timings
    if not valid_timing:
        raise ValueError("Commissioning test timing profile is invalid")
    min_plate_mc = signed[17]
    return {
        "valid": True, "schema": words[2], "sequence": words[1], "uptime_ms": words[3],
        "state": words[4], "state_name": {
            0: "idle", 1: "prepared", 2: "trip_test", 3: "trip_passed",
            4: "running", 5: "complete", 6: "fault",
        }[words[4]],
        "flags": words[5], "zero_valid": bool(words[5] & 1),
        "trip_verified": bool(words[5] & 2), "selftest_passed": bool(words[5] & 4),
        "drive": bool(words[5] & 8), "nominal_gain": bool(words[5] & 16),
        "stage_prepared": bool(words[5] & 32), "trip_a": words[6] / 1000,
        "baseline_adc": words[7], "peak_adc": words[8], "last_adc": words[9],
        "sample_count": words[10], "pulses": words[11], "fault_code": words[12],
        "duration_ms": words[13], "on_us": words[14], "period_ms": words[15],
        "elapsed_ms": words[16],
        "min_plate_c": None if min_plate_mc == INVALID else min_plate_mc / 1000,
        "trip_adc": words[18], "trip_timer_us": words[19],
        **extension,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="Select a specific CMSIS-DAP probe by serial number")
    parser.add_argument("--openocd")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--from-file", type=Path, help="Decode a saved telemetry snapshot offline")
    args = parser.parse_args()
    if args.from_file:
        result = decode(args.from_file.read_bytes())
    else:
        try:
            from .device_link import DeviceLink
        except ImportError:
            from device_link import DeviceLink
        try:
            result = DeviceLink(ROOT, args.serial, args.openocd).read_status()
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
    rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
