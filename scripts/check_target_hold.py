"""Exercise dew-point target feedback with the TEC gate held LOW. Close the GUI first.

This selects commissioning kind 6, which never connects TIM2 to the gate pin.
It does not substitute simulated temperatures into live sensors or run a
powered shutdown proof. Native tests exercise target crossing and expiry.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import tempfile
import time

try:
    from .device_link import DeviceLink, _tcl_path
except ImportError:
    from device_link import DeviceLink, _tcl_path


def require_off(status: dict) -> None:
    if (status.get("outputs_locked_off") is not True or status.get("armed_mask") != 0 or
            status.get("applied_duty") != [0, 0, 0] or status.get("desired_duty") != [0, 0, 0]):
        raise RuntimeError("Hold preflight requires the normal output lock and stopped channels")


def gate_registers(link: DeviceLink) -> dict:
    with tempfile.TemporaryDirectory(prefix="tripletec-hold-off-") as temporary:
        path = Path(temporary) / "gpio.bin"
        link._run([f"dump_image {_tcl_path(path)} 0x40010800 16"])
        crl, _, idr, odr = struct.unpack("<4I", path.read_bytes())
    if crl & 0xFFF0 != 0x2220 or (idr | odr) & 0x0E:
        raise RuntimeError("TEC gate pins are not configured as GPIO LOW")
    return {"crl": crl, "idr": idr, "odr": odr}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--serial")
    parser.add_argument("--output", type=Path, default=Path("build/target-hold-gpio-low-check.json"))
    args = parser.parse_args()
    if not 2 <= args.seconds <= 45:
        parser.error("--seconds must be between 2 and 45")
    link = DeviceLink(serial=args.serial)
    result = {"agent_issued_energized_pulse": False, "requested_kind": 6, "snapshots": []}
    initial = link.read_status()
    require_off(initial)
    if initial.get("commissioning", {}).get("schema") != 3:
        raise RuntimeError("Load dew-point target commissioning firmware before this check")
    result["initial"] = initial
    try:
        require_off(link.command("stop"))
        result["prepared"] = link.command("commission_prepare", 15000)
        require_off(result["prepared"])
        status = link.command("commission_test", 6)
        deadline = time.monotonic() + args.seconds
        while True:
            require_off(status)
            hold = status.get("commissioning", {})
            if (hold.get("valid") is not True or hold.get("kind") != 6 or
                    hold.get("drive") is not False or hold.get("state") != 4 or
                    hold.get("fault_code") != 0 or abs(hold.get("control_target_c", -999) - status["target_c"]) > 0.1):
                raise RuntimeError(f"GPIO-LOW hold session failed: {hold}")
            result["snapshots"].append(status)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.15)
            status = link.read_status()  # Validated heartbeats maintain the host lease.
        result["active_gate_registers"] = gate_registers(link)
    finally:
        result["stopped"] = link.command("stop")
        require_off(result["stopped"])
        result["stopped_gate_registers"] = gate_registers(link)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    final = result["snapshots"][-1]["commissioning"]
    print(json.dumps({"output": str(args.output), "samples": final["sample_count"],
                      "pulses": final["pulses"], "control_state": final["control_state"],
                      "target_c": final["control_target_c"], "drive": final["drive"]}))


if __name__ == "__main__":
    main()
