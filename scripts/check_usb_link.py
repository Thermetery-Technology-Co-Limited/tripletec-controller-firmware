"""Check the direct STM32 USB-C link without any Pico/SWD fallback.

The command check requires the output-locked bench image and restores regulated
mode, a 3 degree margin and stopped state. Close the GUI before running it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from .usb_link import UsbLink, list_usb_devices


def require_bench(status: dict) -> None:
    if (status.get("outputs_locked_off") is not True or status.get("armed_mask") != 0 or
            status.get("applied_duty") != [0, 0, 0] or
            status.get("current_limits_a") != [0, 9, 9] or
            status.get("current_limits_commissioned_mask") != 0):
        raise RuntimeError("This check requires locked, stopped bench firmware with uncommissioned 9A XT60 trips")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List direct STM32 USB devices without opening them")
    parser.add_argument("--port", help="COM port when multiple devices are present")
    parser.add_argument("--serial-number", help="Select the STM32 UID serial number")
    parser.add_argument("--status-only", action="store_true", help="Read fresh status without changing settings")
    parser.add_argument("--output", type=Path, default=Path("build/gui/usb-link-check.json"))
    args = parser.parse_args()
    if args.list:
        print(json.dumps(list_usb_devices(), indent=2))
        return
    observations = {}
    link = UsbLink(port=args.port, serial_number=args.serial_number)
    try:
        initial = link.read_status()
        require_bench(initial)
        observations["initial"] = initial
        time.sleep(0.3)
        fresh = link.read_status()
        require_bench(fresh)
        if fresh["sequence"] == initial["sequence"] or fresh["uptime_ms"] == initial["uptime_ms"]:
            raise RuntimeError("USB status is not advancing")
        observations["fresh"] = fresh
        if not args.status_only:
            try:
                unregulated = link.command("mode", 1)
                require_bench(unregulated)
                if unregulated["target_c"] is not None:
                    raise RuntimeError("Unregulated mode did not bypass the target")
                observations["unregulated"] = unregulated
                try:
                    link.command("arm", 7)
                except RuntimeError as error:
                    if "BENCH_LOCKED" not in str(error):
                        raise
                    observations["arm_rejection"] = str(error)
                else:
                    raise RuntimeError("Bench firmware unexpectedly accepted ARM")
                require_bench(link.command("stop"))
                require_bench(link.command("margin", 4500))
                regulated = link.command("mode", 0)
                require_bench(regulated)
                if (regulated["dewpoint_c"] is None or regulated["target_c"] is None or
                        abs(regulated["target_c"] - max(0, regulated["dewpoint_c"] + 4.5)) > 0.003):
                    raise RuntimeError("USB regulated target does not match the selected margin")
                observations["regulated_margin_4_5"] = regulated
            finally:
                # No transport fallback and no retry of an uncertain command.
                # On a lost link this raises; the bench image remains locked.
                require_bench(link.command("stop"))
                require_bench(link.command("mode", 0))
                restored = link.command("margin", 3000)
                require_bench(restored)
                observations["restored"] = restored
    finally:
        link.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(observations, indent=2) + "\n", encoding="utf-8")
    print("Direct STM32 USB check passed; no Pico/SWD fallback was used.")
    print(args.output)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
