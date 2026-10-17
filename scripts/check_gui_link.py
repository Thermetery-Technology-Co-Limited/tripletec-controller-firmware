"""Exercise GUI commands on output-locked bench firmware and restore defaults.

Close the GUI and other probe clients before running. Refuses an enabled image.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

try:
    from .device_link import DeviceLink
except ImportError:
    from device_link import DeviceLink


def require_locked(status: dict) -> None:
    if status.get("outputs_locked_off") is not True:
        raise RuntimeError("This check requires the output-locked bench image")
    if status.get("armed_mask") != 0 or any(status.get("applied_duty", [1, 1, 1])):
        raise RuntimeError("Bench output or arm state is inconsistent")


def main() -> None:
    link = DeviceLink()
    observations = {}
    initial = link.read_status()
    require_locked(initial)
    if initial.get("schema") != 2:
        raise RuntimeError("Load the command-capable bench image first")
    deadline = time.monotonic() + 5
    while not initial.get("valid_flags", 0) & 1 and time.monotonic() < deadline:
        time.sleep(0.1)
        initial = link.read_status()
    observations["initial"] = initial
    try:
        unregulated = link.command("mode", 1)
        require_locked(unregulated)
        if unregulated["mode"] != 1 or unregulated["target_c"] is not None:
            raise RuntimeError("Unregulated mode did not bypass the temperature target")
        observations["unregulated"] = unregulated
        try:
            link.command("arm", 7)
        except RuntimeError as error:
            if "BENCH_LOCKED" not in str(error):
                raise
            observations["arm_rejection"] = str(error)
        else:
            raise RuntimeError("Bench firmware unexpectedly accepted ARM")
        require_locked(link.command("stop"))
        require_locked(link.command("margin", 4500))
        regulated = link.command("mode", 0)
        require_locked(regulated)
        if (regulated["mode"] != 0 or regulated["margin_c"] != 4.5 or
                regulated["dewpoint_c"] is None or regulated["target_c"] is None or
                abs(regulated["target_c"] - max(0, regulated["dewpoint_c"] + 4.5)) > 0.003):
            raise RuntimeError("Regulated margin/target was not applied correctly")
        observations["regulated_margin_4_5"] = regulated
    finally:
        link.command("stop")
        link.command("mode", 0)
        restored = link.command("margin", 3000)
        require_locked(restored)
        observations["restored"] = restored
    output = Path(__file__).resolve().parents[1] / "build/gui/link-check.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(observations, indent=2) + "\n", encoding="utf-8")
    print("Live GUI protocol checks passed: modes, margin, STOP, rejected ARM, zero outputs.")
    print(output)


if __name__ == "__main__":
    main()
