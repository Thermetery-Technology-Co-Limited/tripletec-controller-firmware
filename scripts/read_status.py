"""Read live bench telemetry over Pico SWD without halting or resetting the MCU."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
INVALID = -(2**31)


def decode(data: bytes) -> dict:
    if len(data) != 128:
        raise ValueError(f"Expected 128 bytes, received {len(data)}")
    words = struct.unpack("<32I", data)
    signed = struct.unpack("<32i", data)
    if words[0] != 0x54454331 or words[2] != 1:
        raise ValueError("Bench firmware telemetry is absent or has a different schema")
    if words[1] & 1:
        raise ValueError("Telemetry was being updated; retry the snapshot")

    def value(index: int):
        return None if signed[index] == INVALID else signed[index] / 1000

    return {
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


def consistent_snapshot(first: bytes, second: bytes) -> dict:
    if len(first) != 128 or len(second) != 128 or first[4:8] != second[4:8]:
        raise ValueError("Snapshot sequence changed")
    # The first payload lies between the two sequence reads. The second
    # payload can be torn by a publication after its own sequence was read.
    return decode(first)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default="E66598541B21AE38")
    parser.add_argument("--openocd", default=shutil.which("openocd"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--from-file", type=Path, help="Decode a saved 128-byte snapshot offline")
    args = parser.parse_args()
    if args.from_file:
        result = decode(args.from_file.read_bytes())
    else:
        if not args.openocd:
            raise SystemExit("OpenOCD missing; supply --openocd.")
        symbol = json.loads((ROOT / "build/stm32/telemetry-symbol.json").read_text())
        if symbol["size"] != 128:
            raise SystemExit("Rebuild firmware: unexpected telemetry size")
        commands = ["cmsis-dap backend usb_bulk", f"adapter serial {args.serial}",
                    "transport select swd", "adapter speed 1000", "reset_config none",
                    "gdb port disabled", "tcl port disabled", "telnet port disabled",
                    "swd newdap status cpu", "dap create status.dap -chain-position status.cpu",
                    "target create status.mem mem_ap -dap status.dap -ap-num 0", "init", "poll off"]
        with tempfile.TemporaryDirectory(prefix="tripletec-status-") as temp:
            snapshots = [Path(temp) / f"status{i}.bin" for i in range(4)]
            for snapshot in snapshots:
                commands.append(f"dump_image {{{snapshot.as_posix()}}} {symbol['address']:#x} 128")
            commands.append("shutdown")
            command = [args.openocd, "-f", "interface/cmsis-dap.cfg"]
            for item in commands:
                command.extend(["-c", item])
            process = subprocess.run(command, capture_output=True, text=True, timeout=30)
            log = ROOT / "build/stm32/last-status-openocd.log"
            log.write_text(process.stdout + process.stderr, encoding="utf-8")
            if process.returncode:
                raise SystemExit(f"SWD read failed; see {log}\n{process.stderr}")
            result = None
            for first, second in zip(snapshots, snapshots[1:]):
                a, b = first.read_bytes(), second.read_bytes()
                # The first payload lies between the two sequence reads.
                # Matching even sequence words validate that first payload;
                # an update can still begin after the second sequence read.
                try:
                    result = consistent_snapshot(a, b)
                    break
                except ValueError:
                    pass
            if result is None:
                raise SystemExit("No consistent snapshot; run again.")
    rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
