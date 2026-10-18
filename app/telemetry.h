#ifndef TEC_BENCH_TELEMETRY_H
#define TEC_BENCH_TELEMETRY_H

#include <stdint.h>

/* All words are little-endian 32-bit. Temperature/RH/duty units are milli-units.
 * INT32_MIN denotes an unavailable measurement. seq is odd while writing.
 * Read with scripts/read_status.py through the existing Pico SWD probe.
 */
typedef struct {
    uint32_t magic;
    uint32_t seq;
    uint32_t schema;
    uint32_t uptime_ms;
    uint32_t outputs_locked; /* Runtime bench lock only; other interlocks are independent. */
    uint32_t valid_flags; /* bits 0..5 sensor validity; bit 6 runtime bench-lock support. */
    uint32_t sht_errors;
    uint32_t ads_errors;
    int32_t ambient_mc;
    int32_t rh_mpercent;
    int32_t dewpoint_mc;
    int32_t target_mc;
    int32_t plate_mc[3];
    int32_t ntc_adc_raw[3];
    uint32_t current_adc_raw[3];
    int32_t current_ma[3];
    uint32_t applied_duty_milli[3];
    uint32_t active_faults[3];
    uint32_t loop_count;
    uint32_t button_pressed;
    /* Schema 2 appends these words; schema 1's first 128 bytes are stable. */
    uint32_t mode; /* 0 regulated, 1 unregulated */
    int32_t margin_mc;
    uint32_t armed_mask;
    uint32_t desired_duty_milli[3];
    uint32_t command_ack_seq;
    uint32_t command_result;
} bench_telemetry_t;

_Static_assert(sizeof(bench_telemetry_t) == 160U, "telemetry schema changed");
extern volatile bench_telemetry_t g_telemetry;

/* Optional diagnostic block; the command/telemetry schema stays unchanged.
 * VDDA is an estimate from nominal 1.20 V VREFINT, not a calibration. */
typedef struct {
    uint32_t magic;
    uint32_t seq;
    uint32_t schema;
    uint32_t uptime_ms;
    uint32_t vrefint_adc_raw;
    int32_t estimated_vdda_mv;
    uint32_t flags; /* bit 0 acquisition OK; bit 1 estimate in 2.8..3.6 V */
    uint32_t adc_errors;
} adc_diagnostics_telemetry_t;

_Static_assert(sizeof(adc_diagnostics_telemetry_t) == 32U, "ADC diagnostic layout changed");
extern volatile adc_diagnostics_telemetry_t g_adc_diagnostics;

/* Selected software trip thresholds, separate from commissioning approval. */
typedef struct {
    uint32_t magic;
    uint32_t seq;
    uint32_t schema;
    uint32_t uptime_ms;
    uint32_t current_limit_ma[3];
    uint32_t commissioned_mask;
} limit_configuration_telemetry_t;

_Static_assert(sizeof(limit_configuration_telemetry_t) == 32U, "limit configuration layout changed");
extern volatile limit_configuration_telemetry_t g_limit_configuration;

/* SWD commissioning extension. Normal USB/status layouts stay unchanged. */
typedef struct {
    uint32_t magic, seq, schema, uptime_ms;
    uint32_t state, flags, trip_ma, baseline_adc;
    uint32_t peak_adc, last_adc, sample_count, pulses;
    uint32_t fault_code, duration_ms, on_us, period_ms;
    uint32_t elapsed_ms;
    int32_t min_plate_mc;
    uint32_t trip_adc, trip_timer_us;
    /* Schema 3: regulation follows the displayed dew-point target.
     * Duty is the pulse-density request, not the normal-controller duty field. */
    uint32_t kind;
    int32_t control_target_mc, guard_floor_mc;
    uint32_t control_duty_milli, control_state; /* 0 inactive, 1 cooling, 2 holding */
    int32_t plate_mc;
} commission_telemetry_t;
_Static_assert(sizeof(commission_telemetry_t) == 104U, "commission telemetry layout changed");
extern volatile commission_telemetry_t g_commissioning;

#endif
