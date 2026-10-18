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
