"""Pure, fail-closed presentation rules shared by the desktop and its tests."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

STALE_SECONDS = 3.0
MODE_LABELS = {0: "Regulated", 1: "Unregulated (100%)"}
CURRENT_INVALID = 512
FAULT_LABELS = (
    (1, "configuration"), (2, "hardware gate"), (4, "ambient sensor"),
    (8, "ambient stale"), (16, "plate sensor"), (32, "plate stale"),
    (64, "timing"), (128, "target range"), (256, "arithmetic"),
    (512, "current sensor"), (1024, "current stale"),
    (2048, "current unverified"), (4096, "current limits not commissioned"),
    (8192, "overcurrent"),
)


def mode_label(mode: int | None) -> str:
    return MODE_LABELS.get(mode, "Unavailable")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _integer(value: Any, minimum: int = 0, maximum: int = 0xFFFFFFFF) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if minimum <= value <= maximum else None


def _triple(data: dict, name: str, *, duty: bool = False) -> tuple:
    values = data.get(name)
    if values is None:
        return (None, None, None)
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError(f"Unsupported {name}: expected three channel values")
    result = tuple(_number(value) for value in values)
    if duty and any(value is not None and not 0 <= value <= 1 for value in result):
        raise ValueError(f"Unsupported {name}: duty must be between 0 and 1")
    return result


@dataclass(frozen=True)
class Snapshot:
    received_at: float
    schema: int
    ambient_c: float | None
    rh_percent: float | None
    dewpoint_c: float | None
    target_c: float | None
    plate_c: tuple
    current_a: tuple
    applied_duty: tuple
    desired_duty: tuple
    faults: tuple
    outputs_locked_off: bool | None
    mode: int | None
    margin_c: float | None
    armed_mask: int | None
    uptime_ms: int | None
    sht31_errors: int | None
    ads1220_errors: int | None
    valid_flags: int | None
    current_limits_a: tuple
    current_limits_commissioned_mask: int | None
    bench_lock_supported: bool | None
    commissioning: dict | None

    @classmethod
    def from_status(cls, data: dict, received_at: float) -> "Snapshot":
        if not isinstance(data, dict):
            raise ValueError("Device returned an unsupported status response")
        schema = _integer(data.get("schema"), 1, 2)
        if schema is None:
            raise ValueError("Unsupported firmware schema; use matching firmware and desktop tools")
        mode = _integer(data.get("mode"), 0, 1) if schema == 2 else None
        if schema == 2 and "mode" in data and mode is None:
            raise ValueError("Unsupported firmware mode; controls remain disabled")
        locked = data.get("outputs_locked_off")
        if locked is not None and not isinstance(locked, bool):
            raise ValueError("Unsupported output-lock field; controls remain disabled")
        raw_faults = data.get("active_faults")
        if raw_faults is None:
            faults = (None, None, None)
        elif isinstance(raw_faults, (list, tuple)) and len(raw_faults) == 3:
            faults = tuple(_integer(value) for value in raw_faults)
        else:
            raise ValueError("Unsupported fault fields: expected three channels")
        current = _triple(data, "current_a_nominal")
        # A nominal conversion is not a measurement when firmware rejects the
        # sensor input (for example an unpowered current-sense output at a rail).
        current = tuple(None if fault is not None and fault & CURRENT_INVALID else value
                        for value, fault in zip(current, faults))
        limits = _triple(data, "current_limits_a")
        if data.get("current_limits_a") is not None and any(
                value is None or not 0 <= value <= maximum
                for value, maximum in zip(limits, (50, 9, 9))):
            raise ValueError("Unsupported current trip settings; controls remain disabled")
        commissioned = _integer(data.get("current_limits_commissioned_mask"), 0, 7)
        if data.get("current_limits_commissioned_mask") is not None and commissioned is None:
            raise ValueError("Unsupported current commissioning state; controls remain disabled")
        capability = data.get("bench_lock_supported")
        if capability is not None and not isinstance(capability, bool):
            raise ValueError("Unsupported bench-lock capability; controls remain disabled")
        valid_flags = _integer(data.get("valid_flags"))
        commissioning = data.get("commissioning")
        if commissioning is not None:
            if not isinstance(commissioning, dict):
                commissioning = {"valid": False, "error": "Unsupported commissioning status"}
            elif commissioning.get("valid") is True and (
                    _integer(commissioning.get("state"), 0, 6) is None or
                    _integer(commissioning.get("flags"), 0, 63) is None or
                    any(type(commissioning.get(name)) is not bool for name in
                        ("zero_valid", "trip_verified", "selftest_passed", "nominal_gain"))):
                commissioning = {"valid": False, "error": "Unsupported commissioning fields"}
        if schema < 2:
            capability = False
        elif valid_flags is not None:
            capability = bool(valid_flags & 0x40) or capability is True
        return cls(
            received_at, schema, _number(data.get("ambient_c")),
            _number(data.get("rh_percent")), _number(data.get("dewpoint_c")),
            _number(data.get("target_c")), _triple(data, "plate_c"),
            current, _triple(data, "applied_duty", duty=True),
            _triple(data, "desired_duty", duty=True), faults, locked, mode,
            _number(data.get("margin_c")) if schema == 2 else None,
            _integer(data.get("armed_mask"), 0, 7) if schema == 2 else None,
            _integer(data.get("uptime_ms")), _integer(data.get("sht31_errors")),
            _integer(data.get("ads1220_errors")), valid_flags,
            limits, commissioned, capability, commissioning,
        )

    def stale(self, now: float) -> bool:
        return now - self.received_at > STALE_SECONDS

    def bench_lock_block_reason(self, connected: bool, now: float, *,
                                command_pending: bool = False,
                                last_error: str | None = None) -> str | None:
        """Relocking needs fresh state, independent of operating interlocks."""
        if not connected:
            return "Connect to a device to change the bench lock."
        if self.stale(now):
            return "Fresh controller readings are required to change the bench lock."
        if self.schema < 2 or self.bench_lock_supported is not True:
            return "This firmware does not report support for changing the bench lock."
        if self.outputs_locked_off is None:
            return "The controller's bench-lock state is unavailable."
        if self.outputs_locked_off and self.commissioning_running:
            return "Stop the TEC1 commissioning test before unlocking the bench lock."
        if command_pending:
            return "Wait for firmware acknowledgement before changing the bench lock."
        if last_error and self.outputs_locked_off:
            return "Restore communication before unlocking the bench lock."
        return None

    @property
    def commissioning_running(self) -> bool:
        return bool(self.commissioning and self.commissioning.get("valid") is True and
                    self.commissioning.get("state") in (2, 4))

    def start_block_reason(self, connected: bool, now: float) -> str | None:
        if not connected:
            return "Connect to a device before starting."
        if self.stale(now):
            return "Readings are stale. Restore the connection before starting."
        if self.schema < 2:
            return "This firmware supports monitoring only. Install command-capable firmware."
        if self.outputs_locked_off is True:
            return "Bench firmware locks every output OFF. Start requires commissioned firmware."
        if self.outputs_locked_off is not False or self.mode is None or self.armed_mask is None:
            return "Output state is unavailable. Start remains disabled."
        if any(fault is None for fault in self.faults):
            return "Fault state is unavailable. Start remains disabled."
        if any(self.faults):
            return "An active trip blocks Start. Resolve the channel faults shown above."
        if any(value is None for value in self.plate_c):
            return "All three plate sensors must provide valid readings before starting."
        if any(value is None for value in self.current_a):
            return "All three current sensors must provide valid readings before starting."
        if self.valid_flags is None or self.valid_flags & 0x3F != 0x3F:
            return "Sensor acquisition is incomplete or stale. Start remains disabled."
        if any(value is None for value in self.current_limits_a):
            return "Current trip settings are unavailable. Start remains disabled."
        if any(value <= 0 for value in self.current_limits_a):
            return "Every channel needs a positive current trip setting before starting."
        if self.current_limits_commissioned_mask != 7:
            return "Current trip settings must be commissioned on all channels before starting."
        return None


def margin_millic(text: str) -> int:
    try:
        value = float(text)
    except (ValueError, TypeError):
        raise ValueError("Enter a dew-point margin from 0.5 to 20 °C") from None
    if not math.isfinite(value) or not 0.5 <= value <= 20.0:
        raise ValueError("Enter a dew-point margin from 0.5 to 20 °C")
    return round(value * 1000)


def format_value(value: float | None, unit: str = "", decimals: int = 1) -> str:
    return "—" if value is None else f"{value:.{decimals}f}{unit}"


def current_trip_label(snapshot: Snapshot | None, index: int) -> str:
    """Describe reported configuration separately from measured current and lock state."""
    limit = snapshot.current_limits_a[index] if snapshot else None
    if limit is None:
        return "SW trip unavailable"
    threshold = f"SW trip {limit:g} A" if limit > 0 else "SW trip unset"
    mask = snapshot.current_limits_commissioned_mask
    if mask is None:
        return threshold + " · commissioning unknown"
    if limit <= 0 or not mask & (1 << index):
        return threshold + " · uncommissioned"
    return threshold


def channel_status(snapshot: Snapshot, index: int, connected: bool, now: float) -> str:
    if not connected:
        return "Disconnected"
    if snapshot.stale(now):
        return "Stale reading"
    fault = snapshot.faults[index]
    if snapshot.outputs_locked_off:
        labels = []
        if snapshot.plate_c[index] is None or (fault is not None and fault & 16):
            labels.append("plate sensor unavailable")
        elif fault is not None and fault & 32:
            labels.append("plate reading stale")
        if snapshot.current_a[index] is None:
            labels.append("current sensor unavailable")
        elif fault is not None and fault & 1024:
            labels.append("current reading stale")
        # Hardware verification and current commissioning remain Start inhibits;
        # avoid repeating those expected bench conditions on every channel.
        expected_or_shown = 2 | 16 | 32 | CURRENT_INVALID | 1024 | 2048 | 4096
        if fault is not None:
            labels.extend(label for bit, label in FAULT_LABELS
                          if fault & bit and not (expected_or_shown & bit))
            known = sum(bit for bit, _ in FAULT_LABELS)
            if fault & ~known:
                labels.append(f"fault 0x{fault & ~known:X}")
        return "Bench locked · " + (", ".join(labels) if labels else "OFF")
    if fault:
        labels = [label for bit, label in FAULT_LABELS if fault & bit]
        known = sum(bit for bit, _ in FAULT_LABELS)
        if fault & ~known:
            labels.append(f"fault 0x{fault & ~known:X}")
        return "Trip · " + ", ".join(labels)
    if snapshot.plate_c[index] is None:
        return "Plate sensor unavailable"
    if snapshot.armed_mask is None:
        return "Arm state unavailable"
    return "Armed" if snapshot.armed_mask & (1 << index) else "Stopped"
