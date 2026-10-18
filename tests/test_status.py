import importlib.util
from pathlib import Path
import struct
import unittest

source = Path(__file__).resolve().parents[1] / "scripts" / "read_status.py"
spec = importlib.util.spec_from_file_location("read_status", source)
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


def frame(sequence=2, ambient=25000, schema=1):
    words = [0] * (40 if schema == 2 else 32)
    words[0:5] = [0x54454331, sequence, schema, 1000, 1]
    words[8:12] = [ambient, 50000, 13852, 16852]
    words[12] = 0x80000000
    if schema == 2:
        words[32:40] = [1, 3500, 0, 0, 0, 0, 17, 5]
    return struct.pack(f"<{len(words)}I", *words)


def commissioning_frame(**overrides):
    words = [0x5445434D, 2, 1, 1000, 4, 0x3F, 15000, 2048, 2400, 2200,
             128, 20, 0, 1000, 1000, 20, 400, 27300, 2539, 37]
    fields = ("magic", "sequence", "schema", "uptime_ms", "state", "flags", "trip_ma",
              "baseline_adc", "peak_adc", "last_adc", "sample_count", "pulses", "fault_code",
              "duration_ms", "on_us", "period_ms", "elapsed_ms", "min_plate_mc",
              "trip_adc", "trip_timer_us")
    for field, value in overrides.items():
        words[fields.index(field)] = value & 0xFFFFFFFF
    return struct.pack("<20I", *words)


def commissioning_v2_frame(**overrides):
    extension_fields = ("kind", "control_target_mc", "guard_floor_mc", "control_duty_milli",
                        "control_state", "plate_mc")
    extension_values = [2, status.INVALID, 22000, 0, 0, 27300]
    base_overrides = {name: value for name, value in overrides.items() if name not in extension_fields}
    words = list(struct.unpack("<20I", commissioning_frame(**base_overrides)))
    words[2] = 2
    for field, value in overrides.items():
        if field in extension_fields:
            extension_values[extension_fields.index(field)] = value
    return struct.pack("<26I", *(words + [value & 0xFFFFFFFF for value in extension_values]))


class StatusTest(unittest.TestCase):
    def test_units_and_invalid_sentinel(self):
        result = status.decode(frame())
        self.assertEqual(result["ambient_c"], 25.0)
        self.assertEqual(result["rh_percent"], 50.0)
        self.assertEqual(result["target_c"], 16.852)
        self.assertIsNone(result["plate_c"][0])
        self.assertTrue(result["outputs_locked_off"])

    def test_validate_first_payload_not_second(self):
        first = frame(2, 25000)
        second = frame(2, 99000)  # writer began after second's sequence read
        self.assertEqual(status.consistent_snapshot(first, second)["ambient_c"], 25.0)

    def test_schema_two_mode_settings_and_rejected_command(self):
        result = status.decode(frame(schema=2))
        self.assertEqual(result["mode"], 1)
        self.assertEqual(result["mode_name"], "unregulated")
        self.assertEqual(result["margin_c"], 3.5)
        self.assertEqual(result["armed_mask"], 0)
        self.assertEqual(result["desired_duty"], [0, 0, 0])
        self.assertEqual(result["command_ack_seq"], 17)
        self.assertEqual(result["command_result_name"], "BENCH_LOCKED")
        with self.assertRaises(ValueError):
            status.decode(frame(schema=2)[:128])

    def test_schema_two_seqlock_uses_first_payload(self):
        self.assertEqual(status.consistent_snapshot(frame(2, 25000, 2),
                                                   frame(2, 99000, 2))["ambient_c"], 25)

    def test_bench_lock_capability_requires_schema_two_and_explicit_flag(self):
        for schema in (1, 2):
            for flags in (0, 63, 64, 127):
                with self.subTest(schema=schema, flags=flags):
                    data = bytearray(frame(schema=schema))
                    struct.pack_into("<I", data, 20, flags)
                    self.assertIs(status.decode(data)["bench_lock_supported"],
                                  schema == 2 and bool(flags & 64))

    def test_commissioning_capability_requires_schema_two_and_explicit_flag(self):
        for schema in (1, 2):
            for flags in (0, 127, 128, 255):
                with self.subTest(schema=schema, flags=flags):
                    data = bytearray(frame(schema=schema))
                    struct.pack_into("<I", data, 20, flags)
                    self.assertIs(status.decode(data)["commissioning_supported"],
                                  schema == 2 and bool(flags & 128))

    def test_reject_publication_in_progress(self):
        with self.assertRaises(ValueError):
            status.consistent_snapshot(frame(3), frame(3))

    def test_reject_changed_sequence(self):
        with self.assertRaises(ValueError):
            status.consistent_snapshot(frame(2), frame(4))

    def test_reject_wrong_image_or_truncation(self):
        with self.assertRaises(ValueError):
            status.decode(bytes(128))
        with self.assertRaises(ValueError):
            status.decode(bytes(127))

    def test_adc_reference_diagnostic_uses_first_consistent_payload(self):
        first = struct.pack("<8I", 0x54454344, 2, 1, 1000, 1489, 3300, 3, 0)
        later = struct.pack("<8I", 0x54454344, 2, 1, 1001, 10, 49000, 1, 1)
        result = status.consistent_adc_diagnostics(first, later)
        self.assertTrue(result["valid"])
        self.assertEqual(result["vrefint_adc_raw"], 1489)
        self.assertEqual(result["estimated_vdda_v"], 3.3)
        self.assertEqual(result["adc_errors"], 0)

    def test_adc_diagnostics_invalid_acquisition_and_torn_data(self):
        invalid = struct.pack("<8I", 0x54454344, 4, 1, 1000, 0, 0x80000000, 0, 1)
        result = status.consistent_adc_diagnostics(invalid, invalid)
        self.assertFalse(result["valid"])
        self.assertIsNone(result["estimated_vdda_v"])
        for other in (bytes(32), invalid[:28],
                      struct.pack("<8I", 0x54454344, 6, 1, 1000, 0, 0, 0, 0)):
            with self.subTest(other=other), self.assertRaises(ValueError):
                status.consistent_adc_diagnostics(invalid, other)

    def test_limit_configuration_uses_first_consistent_snapshot(self):
        first = struct.pack("<8I", 0x5445434C, 2, 1, 1000, 0, 9000, 9000, 0)
        second = struct.pack("<8I", 0x5445434C, 2, 1, 1000, 5000, 4500, 4500, 7)
        result = status.consistent_limit_configuration(first, second)
        self.assertEqual(result["current_limits_a"], [0, 9, 9])
        self.assertEqual(result["current_limits_commissioned_mask"], 0)

    def test_limit_configuration_rejects_torn_or_unsafe_fields(self):
        words = [0x5445434C, 2, 1, 1000, 0, 9000, 9000, 0]
        first = struct.pack("<8I", *words)
        for index, value in ((0, 0), (1, 3), (2, 2), (4, 50001), (5, 9001), (6, 10000), (7, 8), (7, 1)):
            changed = words.copy()
            changed[index] = value
            payload = struct.pack("<8I", *changed)
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                status.consistent_limit_configuration(payload, payload)
        with self.assertRaises(ValueError):
            status.consistent_limit_configuration(first, first[:28])
        changed = words.copy()
        changed[1] = 4
        with self.assertRaises(ValueError):
            status.consistent_limit_configuration(first, struct.pack("<8I", *changed))

    def test_commissioning_uses_first_consistent_payload_and_converts_units(self):
        first = commissioning_frame()
        second = commissioning_frame(peak_adc=4095, min_plate_mc=-1000)
        result = status.consistent_commissioning(first, second)
        self.assertTrue(result["valid"])
        self.assertEqual(result["schema"], 1)
        self.assertEqual(result["sequence"], 2)
        self.assertEqual(result["uptime_ms"], 1000)
        self.assertEqual(result["state"], 4)
        self.assertEqual(result["state_name"], "running")
        self.assertEqual(result["flags"], 0x3F)
        for flag in ("zero_valid", "trip_verified", "selftest_passed", "drive",
                     "nominal_gain", "stage_prepared"):
            self.assertTrue(result[flag], flag)
        self.assertEqual(result["trip_a"], 15)
        self.assertEqual(result["baseline_adc"], 2048)
        self.assertEqual(result["peak_adc"], 2400)
        self.assertEqual(result["last_adc"], 2200)
        self.assertEqual(result["sample_count"], 128)
        self.assertEqual(result["pulses"], 20)
        self.assertEqual(result["fault_code"], 0)
        self.assertEqual(result["duration_ms"], 1000)
        self.assertEqual(result["on_us"], 1000)
        self.assertEqual(result["period_ms"], 20)
        self.assertEqual(result["elapsed_ms"], 400)
        self.assertEqual(result["min_plate_c"], 27.3)
        self.assertEqual(result["trip_adc"], 2539)
        self.assertEqual(result["trip_timer_us"], 37)
        for field in ("kind", "control_target_c", "guard_floor_c", "control_duty",
                      "control_state", "control_state_name", "plate_c"):
            self.assertIsNone(result[field], field)

    def test_commissioning_flag_bits_and_state_names(self):
        flags = ("zero_valid", "trip_verified", "selftest_passed", "drive",
                 "nominal_gain", "stage_prepared")
        for bit, expected in enumerate(flags):
            payload = commissioning_frame(flags=1 << bit)
            result = status.consistent_commissioning(payload, payload)
            for flag in flags:
                self.assertIs(result[flag], flag == expected)
        for state, name in enumerate(("idle", "prepared", "trip_test", "trip_passed",
                                      "running", "complete", "fault")):
            payload = commissioning_frame(state=state)
            self.assertEqual(status.consistent_commissioning(payload, payload)["state_name"], name)

    def test_commissioning_signed_temperature_and_unavailable_sentinel(self):
        for milli_c, expected in ((-1234, -1.234), (0, 0), (status.INVALID, None)):
            payload = commissioning_frame(min_plate_mc=milli_c)
            self.assertEqual(status.consistent_commissioning(payload, payload)["min_plate_c"], expected)

    def test_commissioning_accepts_idle_uninitialized_profile(self):
        payload = commissioning_frame(state=0, flags=0, trip_ma=0, duration_ms=0,
                                      on_us=0, period_ms=0, min_plate_mc=status.INVALID)
        result = status.consistent_commissioning(payload, payload)
        self.assertEqual(result["trip_a"], 0)
        self.assertFalse(result["drive"])
        for field in ("trip_ma", "duration_ms", "on_us", "period_ms"):
            changed = commissioning_frame(state=0, **{field: 1})
            with self.subTest(field=field), self.assertRaises(ValueError):
                status.consistent_commissioning(changed, changed)

    def test_commissioning_allows_thirty_second_five_twelfths_profile_only_during_or_after_run(self):
        for state in (4, 5, 6):
            with self.subTest(state=state):
                payload = commissioning_frame(state=state, duration_ms=30000, on_us=5000, period_ms=12)
                result = status.consistent_commissioning(payload, payload)
                self.assertEqual(result["duration_ms"], 30000)
                self.assertEqual(result["on_us"], 5000)
                self.assertEqual(result["period_ms"], 12)
        for state in (0, 1, 2, 3):
            payload = commissioning_frame(state=state, duration_ms=30000, on_us=5000, period_ms=12)
            with self.subTest(state=state), self.assertRaises(ValueError):
                status.consistent_commissioning(payload, payload)

    def test_commissioning_observation_keeps_fixed_trip_and_pulse_profile(self):
        for state in (4, 5, 6):
            for changes in ({"duration_ms": 29999}, {"duration_ms": 30001},
                            {"trip_ma": 14999}, {"on_us": 5001}, {"period_ms": 13}):
                payload = commissioning_frame(**{"state": state, "duration_ms": 30000,
                                                 "on_us": 5000, "period_ms": 12, **changes})
                with self.subTest(state=state, changes=changes), self.assertRaises(ValueError):
                    status.consistent_commissioning(payload, payload)

    def test_commissioning_rejects_retired_duration_and_mixed_timing_profiles(self):
        for state in range(7):
            for duration, on, period in ((5000, 1000, 20), (5000, 1000, 5),
                                        (10000, 1000, 5), (10000, 1000, 20), (1000, 1000, 5),
                                        (30000, 1000, 12), (30000, 5000, 20),
                                        (1000, 5000, 12), (1000, 5000, 20)):
                payload = commissioning_frame(state=state, duration_ms=duration, on_us=on, period_ms=period)
                with self.subTest(state=state, duration=duration, on=on, period=period), self.assertRaises(ValueError):
                    status.consistent_commissioning(payload, payload)

    def test_commissioning_rejects_invalid_identity_states_and_fields(self):
        invalid_fields = {
            "magic": 0, "sequence": 3, "schema": 2, "state": 7, "flags": 64,
            "baseline_adc": 4096, "peak_adc": 4096, "last_adc": 4096,
            "trip_adc": 4096, "fault_code": 10, "trip_ma": 14999,
            "duration_ms": 999, "on_us": 999, "period_ms": 19,
        }
        for field, value in invalid_fields.items():
            payload = commissioning_frame(**{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                status.consistent_commissioning(payload, payload)
        for state in range(1, 7):
            for field in ("trip_ma", "duration_ms", "on_us", "period_ms"):
                payload = commissioning_frame(state=state, **{field: 0})
                with self.subTest(state=state, field=field), self.assertRaises(ValueError):
                    status.consistent_commissioning(payload, payload)

    def test_commissioning_rejects_changed_sequence_or_wrong_lengths(self):
        first = commissioning_frame()
        with self.assertRaises(ValueError):
            status.consistent_commissioning(first, commissioning_frame(sequence=4))
        for size in (0, 76, 79, 81, 84):
            other = (first + bytes(4))[:size]
            for snapshots in ((first, other), (other, first), (other, other)):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    status.consistent_commissioning(*snapshots)

    def test_commissioning_schema_two_decodes_hold_control_and_first_payload(self):
        first = commissioning_v2_frame(kind=5, duration_ms=300000, on_us=5000, period_ms=12,
                                       control_target_mc=27000, guard_floor_mc=22345,
                                       control_duty_milli=417, control_state=1, plate_mc=27654)
        second = commissioning_v2_frame(kind=99, plate_mc=100000)
        result = status.consistent_commissioning(first, second)
        self.assertEqual(result["schema"], 2)
        self.assertEqual(result["kind"], 5)
        self.assertEqual(result["duration_ms"], 300000)
        self.assertEqual(result["control_target_c"], 27)
        self.assertEqual(result["guard_floor_c"], 22.345)
        self.assertEqual(result["control_duty"], 0.417)
        self.assertEqual(result["control_state"], 1)
        self.assertEqual(result["control_state_name"], "cooling")
        self.assertEqual(result["plate_c"], 27.654)

    def test_commissioning_schema_two_temperature_sentinels_and_control_states(self):
        for control_state, name in enumerate(("inactive", "cooling", "holding")):
            payload = commissioning_v2_frame(kind=6, duration_ms=300000, on_us=5000, period_ms=12,
                control_target_mc=27000, guard_floor_mc=-1234, plate_mc=status.INVALID,
                control_state=control_state)
            result = status.consistent_commissioning(payload, payload)
            self.assertEqual(result["control_state_name"], name)
            self.assertEqual(result["guard_floor_c"], -1.234)
            self.assertIsNone(result["plate_c"])
        payload = commissioning_v2_frame(guard_floor_mc=status.INVALID, plate_mc=-500)
        result = status.consistent_commissioning(payload, payload)
        self.assertIsNone(result["control_target_c"])
        self.assertIsNone(result["guard_floor_c"])
        self.assertEqual(result["plate_c"], -0.5)

    def test_commissioning_schema_two_validates_exact_kind_profiles(self):
        for kind in range(7):
            expected_timing = ((1000, 1000, 20) if kind <= 2 else
                               (30000, 5000, 12) if kind <= 4 else (300000, 5000, 12))
            expected_target = 27000 if kind >= 5 else status.INVALID
            for state in range(1, 7):
                payload = commissioning_v2_frame(kind=kind, state=state,
                    duration_ms=expected_timing[0], on_us=expected_timing[1],
                    period_ms=expected_timing[2], control_target_mc=expected_target)
                with self.subTest(kind=kind, state=state):
                    if kind <= 2 or state in (4, 5, 6):
                        self.assertEqual(status.consistent_commissioning(payload, payload)["kind"], kind)
                    else:
                        with self.assertRaises(ValueError):
                            status.consistent_commissioning(payload, payload)
            for field in ("duration_ms", "on_us", "period_ms"):
                values = dict(zip(("duration_ms", "on_us", "period_ms"), expected_timing))
                values[field] += 1
                payload = commissioning_v2_frame(kind=kind, control_target_mc=expected_target, **values)
                with self.subTest(kind=kind, field=field), self.assertRaises(ValueError):
                    status.consistent_commissioning(payload, payload)

    def test_commissioning_schema_two_accepts_idle_zero_and_prepared_profile(self):
        idle = commissioning_v2_frame(state=0, kind=0, flags=0, trip_ma=0, duration_ms=0,
                                      on_us=0, period_ms=0, plate_mc=status.INVALID)
        self.assertEqual(status.consistent_commissioning(idle, idle)["kind"], 0)
        prepared = commissioning_v2_frame(state=1, kind=0)
        self.assertEqual(status.consistent_commissioning(prepared, prepared)["state_name"], "prepared")

    def test_commissioning_schema_two_rejects_control_values_and_profile_mismatches(self):
        for changes in ({"kind": 7}, {"control_target_mc": 27000}, {"control_state": 3},
                        {"control_duty_milli": 418}, {"control_state": 1}, {"control_duty_milli": 1},
                        {"duration_ms": 30000, "on_us": 5000, "period_ms": 12}):
            payload = commissioning_v2_frame(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                status.consistent_commissioning(payload, payload)
        for kind in (5, 6):
            for changes in ({"control_target_mc": status.INVALID}, {"control_target_mc": 26999},
                            {"control_duty_milli": 418}, {"control_state": 3},
                            {"duration_ms": 30000}, {"on_us": 1000}, {"period_ms": 20}):
                payload = commissioning_v2_frame(**{"kind": kind, "duration_ms": 300000,
                    "on_us": 5000, "period_ms": 12, "control_target_mc": 27000, **changes})
                with self.subTest(kind=kind, changes=changes), self.assertRaises(ValueError):
                    status.consistent_commissioning(payload, payload)

    def test_commissioning_schema_two_rejects_torn_truncated_and_schema_length_mismatches(self):
        payload = commissioning_v2_frame()
        for other in (payload[:80], payload[:100], payload + bytes(4), commissioning_v2_frame(sequence=4)):
            with self.subTest(length=len(other)), self.assertRaises(ValueError):
                status.consistent_commissioning(payload, other)
        for schema, data in ((1, payload), (2, commissioning_frame()), (3, payload)):
            changed = bytearray(data)
            struct.pack_into("<I", changed, 8, schema)
            with self.subTest(schema=schema, length=len(data)), self.assertRaises(ValueError):
                status.consistent_commissioning(changed, changed)


if __name__ == "__main__":
    unittest.main()
