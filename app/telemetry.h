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
    uint32_t outputs_locked;
    uint32_t valid_flags;
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
} bench_telemetry_t;

_Static_assert(sizeof(bench_telemetry_t) == 128U, "telemetry schema changed");
extern volatile bench_telemetry_t g_telemetry;

#endif
