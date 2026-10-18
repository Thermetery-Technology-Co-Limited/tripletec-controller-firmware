#include "commissioning.h"
#include "board.h"
#include "commission_stage.h"
#include "dewpoint.h"

#include <limits.h>
#include <math.h>
#include <string.h>

enum { IDLE, PREPARED, TRIP_TEST, TRIP_PASSED, RUNNING, COMPLETE, FAULT };
enum { ZERO_VALID = 1, TRIP_VERIFIED = 2, SELFTEST = 4, DRIVE = 8, NOMINAL_GAIN = 16 };
static struct {
    uint32_t state, flags, fault, trip_ma, baseline, zero_at, started, last_pulse;
    uint32_t pulses, kind, elapsed, duration, period;
    bool have_pulse;
    float min_plate;
    commission_stage_status_t capture;
} session;

bool commissioning_active(void) { return session.state == TRIP_TEST || session.state == RUNNING; }
bool commissioning_owns_adc(void) { return commissioning_active(); }

static void capture_stage(void)
{
    commission_stage_snapshot(&session.capture);
}

void commissioning_init(void)
{
    memset(&session, 0, sizeof(session));
    session.flags = NOMINAL_GAIN;
    session.duration = 1000U;
    session.period = 20U;
    session.min_plate = NAN;
}

void commissioning_stop(uint32_t reason, uint32_t now)
{
    if (commissioning_active()) {
        session.elapsed = now - session.started;
        capture_stage();
        session.state = reason == 0U ? COMPLETE : FAULT;
        session.fault = reason;
    }
    commission_stage_stop();
    board_outputs_force_off();
}

static uint32_t guard(const tec_inputs_t *in, const tec_controller_t *ctrl,
                       bool locked, uint32_t now, float extra, float *target)
{
    float dew;
    if (in == NULL || ctrl == NULL || !locked || !ctrl->configuration_valid ||
        ctrl->mode != TEC_MODE_REGULATED || !isfinite(ctrl->config.dewpoint_margin_c) ||
        ctrl->config.dewpoint_margin_c < 3.0f) {
        return 1U;
    }
    for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        if (ctrl->channel[i].armed || ctrl->channel[i].duty != 0.0f) { return 1U; }
    }
    if (!in->ambient_valid || !in->plate[0].valid ||
        !isfinite(in->plate[0].temperature_c) ||
        !tec_dewpoint_c(in->ambient_c, in->rh_percent, &dew)) { return 1U; }
    if (now - in->ambient_timestamp_ms > 2000U || now - in->plate[0].timestamp_ms > 400U) {
        return 6U;
    }
    *target = fmaxf(ctrl->config.absolute_min_c, dew + ctrl->config.dewpoint_margin_c);
    if (!isfinite(*target) || in->plate[0].temperature_c < 20.0f ||
        in->plate[0].temperature_c > 45.0f || in->plate[0].temperature_c <= *target + extra) {
        return 5U;
    }
    return 0U;
}

control_result_t commissioning_prepare(const tec_inputs_t *in, const tec_controller_t *ctrl,
                                       bool bench_locked, int32_t trip_ma, uint32_t now)
{
    float target;
    if (trip_ma != 15000) { return CONTROL_BAD_VALUE; }
    if (commissioning_active() || guard(in, ctrl, bench_locked, now, 1.0f, &target)) {
        return CONTROL_INTERLOCK;
    }
    commissioning_stop(8U, now);
    commissioning_init();
    session.trip_ma = 15000U;
    uint32_t sum = 0U, minimum = 4095U, maximum = 0U;
    uint64_t squares = 0U;
    for (unsigned i = 0U; i < 128U; ++i) {
        board_adc_diagnostics_t sample;
        if (!board_adc_read_diagnostics(&sample, 2U) || sample.vrefint_raw == 0U) {
            session.state = FAULT; session.fault = 2U; return CONTROL_INTERLOCK;
        }
        const uint32_t supply_mv = 1200U * 4095U / sample.vrefint_raw;
        const uint32_t code = sample.current_raw[0];
        if (supply_mv < 2800U || supply_mv > 3600U || code < 1998U || code > 2098U) {
            session.state = FAULT; session.fault = 2U; return CONTROL_INTERLOCK;
        }
        sum += code;
        squares += code * code;
        if (code < minimum) { minimum = code; }
        if (code > maximum) { maximum = code; }
    }
    if (maximum - minimum > 20U || 128U * squares - (uint64_t)sum * sum > 25U * 128U * 128U) {
        session.state = FAULT; session.fault = 2U; return CONTROL_INTERLOCK;
    }
    session.baseline = (sum + 64U) / 128U;
    if (!commission_stage_prepare((uint16_t)session.baseline, 491U, false)) {
        commission_stage_stop();
        session.state = FAULT; session.fault = 3U; return CONTROL_INTERLOCK;
    }
    capture_stage();
    commission_stage_stop();
    if (!session.capture.selftest_passed || !session.capture.pin_low) {
        session.state = FAULT; session.fault = 3U; return CONTROL_INTERLOCK;
    }
    session.flags |= ZERO_VALID | SELFTEST;
    session.zero_at = board_millis();
    session.state = PREPARED;
    session.min_plate = in->plate[0].temperature_c;
    return CONTROL_OK;
}

control_result_t commissioning_start(const tec_inputs_t *in, const tec_controller_t *ctrl,
                                     bool bench_locked, int32_t kind, uint32_t now)
{
    float target;
    if (kind < 0 || kind > 3) { return CONTROL_BAD_VALUE; }
    if (commissioning_active() || session.state == FAULT ||
        !(session.flags & ZERO_VALID) || now - session.zero_at > 300000U ||
        (kind >= 2 && !(session.flags & TRIP_VERIFIED)) ||
        guard(in, ctrl, bench_locked, now, 1.0f, &target)) { return CONTROL_INTERLOCK; }
    const uint16_t delta = kind == 1 ? 163U : 491U;
    /* Every activation repeats the OFF-only ADC/watchdog self-test. */
    if (!commission_stage_prepare((uint16_t)session.baseline, delta, kind != 0)) {
        commission_stage_stop();
        session.state = FAULT; session.fault = 3U; return CONTROL_INTERLOCK;
    }
    if (kind == 3 && !commission_stage_set_period(5U)) {
        commission_stage_stop();
        session.state = FAULT; session.fault = 3U; return CONTROL_INTERLOCK;
    }
    session.kind = (uint32_t)kind;
    session.duration = kind == 3 ? 10000U : 1000U;
    session.period = kind == 3 ? 5U : 20U;
    session.flags &= ~DRIVE;
    if (kind != 0) { session.flags |= DRIVE; }
    session.started = board_millis();
    session.last_pulse = session.started;
    session.have_pulse = false;
    session.pulses = 0U;
    session.elapsed = 0U;
    session.fault = 0U;
    session.min_plate = in->plate[0].temperature_c;
    memset(&session.capture, 0, sizeof(session.capture));
    session.state = kind == 1 ? TRIP_TEST : RUNNING;
    return CONTROL_OK;
}

void commissioning_tick(const tec_inputs_t *in, const tec_controller_t *ctrl,
                        bool bench_locked, uint32_t now, uint32_t last_host_ms)
{
    if (!commissioning_active()) { return; }
    session.elapsed = now - session.started;
    capture_stage();
    float target;
    const uint32_t problem = guard(in, ctrl, bench_locked, now, 0.5f, &target);
    if (problem) { commissioning_stop(problem, now); return; }
    session.min_plate = fminf(session.min_plate, in->plate[0].temperature_c);
    if (now - last_host_ms > 1500U) { commissioning_stop(7U, now); return; }
    if (!session.capture.prepared || !session.capture.selftest_passed) {
        commissioning_stop(3U, now); return;
    }
    if (session.kind == 1U) {
        if (session.elapsed >= 10U) {
            const uint32_t difference = session.capture.last_adc > session.baseline ?
                session.capture.last_adc - session.baseline : session.baseline - session.capture.last_adc;
            const bool verified = session.pulses == 1U && session.capture.tripped &&
                session.capture.trip_adc >= session.baseline + 163U &&
                session.capture.trip_adc < 4095U && session.capture.trip_timer_us > 0U &&
                session.capture.trip_timer_us < 1000U && difference <= 20U &&
                session.capture.pin_low && !session.capture.timer_running && session.capture.samples >= 8U;
            commissioning_stop(verified ? 0U : 3U, now);
            if (verified) { session.flags |= TRIP_VERIFIED; session.state = TRIP_PASSED; }
            return;
        }
    } else {
        if (session.capture.tripped) { commissioning_stop(4U, now); return; }
        if (session.elapsed >= session.duration) { commissioning_stop(0U, now); return; }
    }
    if (session.kind == 1U && session.have_pulse) { return; }
    if (!session.have_pulse && session.elapsed < 2U) { return; }
    if (!session.have_pulse || now - session.last_pulse >= session.period) {
        /* No catch-up loop: a delayed main loop never emits a queued burst. */
        if (!commission_stage_pulse()) { commissioning_stop(3U, now); return; }
        session.last_pulse = now;
        session.have_pulse = true;
        ++session.pulses;
    }
}

void commissioning_snapshot(commission_telemetry_t *dest, uint32_t now)
{
    if (dest == NULL) { return; }
    if (commissioning_active()) { capture_stage(); }
    uint32_t flags = session.flags;
    if (now - session.zero_at > 300000U) { flags &= ~(ZERO_VALID | TRIP_VERIFIED); }
    *dest = (commission_telemetry_t) {
        .magic = UINT32_C(0x5445434d), .schema = 1U, .uptime_ms = now,
        .state = session.state, .flags = flags | (session.capture.prepared && commissioning_active() ? 32U : 0U),
        .trip_ma = session.trip_ma, .baseline_adc = session.baseline,
        .peak_adc = session.capture.peak_adc, .last_adc = session.capture.last_adc,
        .sample_count = session.capture.samples, .pulses = session.pulses,
        .fault_code = session.fault, .duration_ms = session.duration, .on_us = 1000U, .period_ms = session.period,
        .elapsed_ms = session.elapsed,
        .min_plate_mc = isfinite(session.min_plate) ? (int32_t)lroundf(session.min_plate * 1000.0f) : INT32_MIN,
        .trip_adc = session.capture.trip_adc, .trip_timer_us = session.capture.trip_timer_us
    };
}
