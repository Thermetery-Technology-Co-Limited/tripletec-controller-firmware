#include "commissioning.h"
#include "commission_stage.h"
#include "board.h"
#include "dewpoint.h"

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

enum { TEST_IDLE, TEST_PREPARED, TEST_TRIP_TEST, TEST_TRIP_PASSED,
       TEST_RUNNING, TEST_COMPLETE, TEST_FAULT };
enum { TEST_ZERO = 1U, TEST_VERIFIED = 2U, TEST_SELFTEST = 4U, TEST_DRIVE = 8U };

static uint32_t now_ms;
static tec_inputs_t inputs;
static tec_controller_t controller;
static commission_stage_status_t stage;
static unsigned read_calls, off_calls, prepare_calls, stop_calls, pulse_calls;
static unsigned failed_read;
static uint16_t adc_a, adc_b, vref;
static bool alternate, split_samples;
static bool prepare_ok, selftest_ok, pin_ok, pulse_ok;
static uint16_t prepared_baseline, prepared_delta;
static uint32_t prepared_period, prepared_on_us;
static bool prepared_drive;
static uint32_t pulse_times[32768];

bool board_adc_read_diagnostics(board_adc_diagnostics_t *sample, uint32_t timeout_ms)
{
    assert(timeout_ms == 2U);
    const unsigned index = read_calls++;
    if (index == failed_read) { return false; }
    const uint16_t code = split_samples ? (index < 64U ? adc_a : adc_b) :
        (alternate && (index & 1U) ? adc_b : adc_a);
    *sample = (board_adc_diagnostics_t){.current_raw = {code, 1U, 4094U},
                                       .vrefint_raw = vref};
    return true;
}

uint32_t board_millis(void) { return now_ms; }
void board_outputs_force_off(void)
{
    ++off_calls;
    stage.pin_low = true;
    stage.timer_running = false;
}

bool commission_stage_prepare(uint16_t baseline, uint16_t delta, bool drive)
{
    ++prepare_calls;
    prepared_baseline = baseline;
    prepared_delta = delta;
    prepared_drive = drive;
    prepared_period = 20U;
    prepared_on_us = 1000U;
    stage = (commission_stage_status_t){.prepared = prepare_ok,
        .selftest_passed = selftest_ok, .pin_low = pin_ok,
        .samples = 32U, .last_adc = baseline, .peak_adc = baseline};
    return prepare_ok;
}

bool commission_stage_set_profile(uint32_t on_us, uint32_t period_ms)
{
    assert(stage.prepared && !stage.tripped && stage.pin_low && !stage.timer_running);
    assert((on_us == 1000U && (period_ms == 5U || period_ms == 20U)) ||
           (on_us == 5000U && period_ms == 12U));
    prepared_period = period_ms;
    prepared_on_us = on_us;
    return true;
}

bool commission_stage_set_period(uint32_t period_ms)
{
    return commission_stage_set_profile(1000U, period_ms);
}

bool commission_stage_pulse(void)
{
    assert(pulse_calls < sizeof(pulse_times) / sizeof(pulse_times[0]));
    pulse_times[pulse_calls++] = now_ms;
    return pulse_ok;
}

void commission_stage_stop(void)
{
    ++stop_calls;
    stage.prepared = false;
    stage.pin_low = true;
    stage.timer_running = false;
}

void commission_stage_snapshot(commission_stage_status_t *dest) { *dest = stage; }

static commission_telemetry_t snapshot(void)
{
    commission_telemetry_t result;
    memset(&result, 0, sizeof(result));
    commissioning_snapshot(&result, now_ms);
    assert(result.magic == 0x5445434dU && result.schema == 3U);
    assert((result.duration_ms == 1000U && result.on_us == 1000U && result.period_ms == 20U) ||
           (result.duration_ms == 30000U && result.on_us == 5000U && result.period_ms == 12U) ||
           (result.duration_ms == 300000U && result.on_us == 5000U && result.period_ms == 12U));
    return result;
}

static void refresh(uint32_t now)
{
    now_ms = now;
    inputs.ambient_timestamp_ms = now;
    inputs.plate[0].timestamp_ms = now;
}

static void reset(void)
{
    now_ms = 100U;
    memset(&inputs, 0, sizeof(inputs));
    tec_config_t config;
    tec_config_defaults(&config);
    assert(tec_controller_init(&controller, &config));
    inputs.ambient_c = 30.0f;
    inputs.rh_percent = 50.0f;
    inputs.ambient_valid = true;
    inputs.plate[0].valid = true;
    inputs.plate[0].temperature_c = 28.0f;
    refresh(now_ms);
    memset(&stage, 0, sizeof(stage));
    read_calls = off_calls = prepare_calls = stop_calls = pulse_calls = 0U;
    failed_read = 1000U;
    adc_a = adc_b = 2040U;
    vref = 1489U;
    alternate = split_samples = false;
    prepare_ok = selftest_ok = pin_ok = pulse_ok = true;
    commissioning_init();
    assert(snapshot().state == TEST_IDLE);
}

static void prepare(void)
{
    assert(commissioning_prepare(&inputs, &controller, true, 15000, now_ms) == CONTROL_OK);
    const commission_telemetry_t result = snapshot();
    assert(result.state == TEST_PREPARED && result.trip_ma == 15000U);
    assert((result.flags & (TEST_ZERO | TEST_SELFTEST)) == (TEST_ZERO | TEST_SELFTEST));
    assert((result.flags & TEST_VERIFIED) == 0U);
    assert(prepared_delta == 491U && !prepared_drive);
    assert(read_calls == 128U);
    assert(!commissioning_active() && !commissioning_owns_adc());
}

static void start(int kind)
{
    assert(commissioning_start(&inputs, &controller, true, kind, now_ms) == CONTROL_OK);
    assert(commissioning_active() && commissioning_owns_adc());
    assert(prepared_drive == (kind != 0 && kind != 4 && kind != 6));
    assert(prepared_delta == (kind == 1 ? 163U : 491U));
    assert(snapshot().duration_ms == (kind >= 5 ? 300000U : kind >= 3 ? 30000U : 1000U));
    assert(snapshot().period_ms == (kind >= 3 ? 12U : 20U));
    assert(snapshot().on_us == (kind >= 3 ? 5000U : 1000U));
    assert(prepared_period == (kind >= 3 ? 12U : 20U));
    assert(prepared_on_us == (kind >= 3 ? 5000U : 1000U));
}

static void tick(uint32_t now)
{
    refresh(now);
    commissioning_tick(&inputs, &controller, true, now_ms, now_ms);
}

static void assert_normal_policy_unchanged(const tec_config_t *config)
{
    assert(memcmp(config, &controller.config, sizeof(*config)) == 0);
    assert(!inputs.hardware_verified);
    for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(!controller.config.current_limit_commissioned[i]);
        assert(!inputs.current[i].calibrated);
        assert(!controller.channel[i].armed && controller.channel[i].duty == 0.0f);
    }
}

static void test_zero_sampling_and_policy(void)
{
    reset();
    const tec_config_t config = controller.config;
    split_samples = true;
    adc_b = 2041U;
    prepare();
    assert(snapshot().baseline_adc == 2041U && prepared_baseline == 2041U);
    assert_normal_policy_unchanged(&config);
    start(0);
    tick(102U);
    commissioning_stop(0U, now_ms);
    assert_normal_policy_unchanged(&config);

    const uint16_t boundaries[] = {1998U, 2098U};
    for (size_t i = 0U; i < 2U; ++i) {
        reset();
        adc_a = boundaries[i];
        prepare();
        assert(snapshot().baseline_adc == boundaries[i]);
    }
    reset();
    adc_a = 2035U; adc_b = 2045U; alternate = true;
    prepare(); /* Standard deviation exactly five ADC counts is permitted. */
    reset();
    vref = 1365U; /* Nominal reference estimate exactly 3600 mV. */
    prepare();
    reset();
    vref = 1755U; /* Nominal reference estimate exactly 2800 mV. */
    prepare();
}

static void assert_prepare_fault(uint32_t fault)
{
    assert(commissioning_prepare(&inputs, &controller, true, 15000, now_ms) == CONTROL_INTERLOCK);
    assert(snapshot().state == TEST_FAULT && snapshot().fault_code == fault);
    assert(!commissioning_active() && !commissioning_owns_adc());
    assert(pulse_calls == 0U);
}

static void test_bad_zero_and_selftest(void)
{
    const uint16_t bad_codes[] = {0U, 1997U, 2099U, 4095U};
    for (size_t i = 0U; i < sizeof(bad_codes) / sizeof(bad_codes[0]); ++i) {
        reset(); adc_a = bad_codes[i]; assert_prepare_fault(2U);
        assert(prepare_calls == 0U);
    }
    const uint16_t bad_refs[] = {0U, 1364U, 1756U, 4095U};
    for (size_t i = 0U; i < sizeof(bad_refs) / sizeof(bad_refs[0]); ++i) {
        reset(); vref = bad_refs[i]; assert_prepare_fault(2U);
    }
    reset(); adc_a = 2030U; adc_b = 2051U; alternate = true;
    assert_prepare_fault(2U); /* Excessive range. */
    reset(); adc_a = 2034U; adc_b = 2046U; alternate = true;
    assert_prepare_fault(2U); /* Range acceptable, but variance too large. */
    reset(); failed_read = 64U; assert_prepare_fault(2U);
    assert(read_calls == 65U && prepare_calls == 0U);
    reset(); prepare_ok = false; assert_prepare_fault(3U);
    reset(); selftest_ok = false; assert_prepare_fault(3U);
    reset(); pin_ok = false; assert_prepare_fault(3U);
}

static void test_explicit_commands_and_gates(void)
{
    reset();
    assert(commissioning_prepare(&inputs, &controller, true, 14999, now_ms) == CONTROL_BAD_VALUE);
    assert(commissioning_prepare(&inputs, &controller, true, 15001, now_ms) == CONTROL_BAD_VALUE);
    assert(read_calls == 0U);
    assert(commissioning_start(&inputs, &controller, true, 0, now_ms) == CONTROL_INTERLOCK);
    prepare();
    assert(commissioning_start(&inputs, &controller, true, -1, now_ms) == CONTROL_BAD_VALUE);
    assert(commissioning_start(&inputs, &controller, true, 7, now_ms) == CONTROL_BAD_VALUE);
    assert(commissioning_start(&inputs, &controller, true, 2, now_ms) == CONTROL_INTERLOCK);
    assert(commissioning_start(&inputs, &controller, true, 3, now_ms) == CONTROL_INTERLOCK);
    assert(commissioning_start(&inputs, &controller, false, 0, now_ms) == CONTROL_INTERLOCK);
    controller.mode = TEC_MODE_UNREGULATED;
    assert(commissioning_start(&inputs, &controller, true, 0, now_ms) == CONTROL_INTERLOCK);
    controller.mode = TEC_MODE_REGULATED;
    controller.channel[1].armed = true;
    assert(commissioning_start(&inputs, &controller, true, 0, now_ms) == CONTROL_INTERLOCK);
    controller.channel[1].armed = false;
    refresh(300101U);
    assert(commissioning_start(&inputs, &controller, true, 0, now_ms) == CONTROL_INTERLOCK);
}

static void test_dry_run_schedule_and_stop(void)
{
    reset(); prepare(); start(0);
    assert((snapshot().flags & TEST_DRIVE) == 0U);
    tick(101U); assert(pulse_calls == 0U);
    tick(102U); assert(pulse_calls == 1U);
    tick(121U); assert(pulse_calls == 1U);
    tick(122U); assert(pulse_calls == 2U);
    tick(302U); assert(pulse_calls == 3U); /* A late loop emits one pulse. */
    tick(302U); assert(pulse_calls == 3U);
    commissioning_stop(0U, now_ms);
    assert(snapshot().state == TEST_COMPLETE && snapshot().pulses == 3U);
    assert(!commissioning_active() && !commissioning_owns_adc());
    tick(402U); assert(pulse_calls == 3U);

    reset(); prepare(); start(0);
    for (uint32_t at = 102U; at < 1100U; at += 20U) { tick(at); }
    assert(pulse_calls == 50U && commissioning_active());
    tick(1100U);
    assert(snapshot().state == TEST_COMPLETE && snapshot().elapsed_ms == 1000U);
    assert(snapshot().pulses == 50U && !commissioning_active());
    assert(off_calls >= 2U);
    for (unsigned i = 1U; i < pulse_calls; ++i) {
        assert(pulse_times[i] - pulse_times[i - 1U] >= 20U);
    }
    tick(2100U); assert(pulse_calls == 50U);
}

static void make_good_trip(void)
{
    stage.tripped = true;
    stage.trip_adc = prepared_baseline + 163U;
    stage.trip_timer_us = 200U;
    stage.samples = 64U;
    stage.last_adc = prepared_baseline;
    stage.pin_low = true;
    stage.timer_running = false;
}

static void test_trip_verification_and_guarded_run(void)
{
    reset(); prepare(); start(1);
    tick(102U); assert(pulse_calls == 1U);
    make_good_trip();
    tick(109U); assert(pulse_calls == 1U && commissioning_active());
    tick(110U);
    assert(snapshot().state == TEST_TRIP_PASSED);
    assert((snapshot().flags & TEST_VERIFIED) != 0U && !commissioning_active());
    assert(snapshot().trip_adc == 2203U && snapshot().trip_timer_us == 200U);
    start(2);
    assert((snapshot().flags & TEST_DRIVE) != 0U);
    tick(112U); assert(pulse_calls == 2U);
    stage.tripped = true;
    stage.trip_adc = prepared_baseline + 491U;
    stage.trip_timer_us = 300U;
    tick(113U);
    assert(snapshot().state == TEST_FAULT && snapshot().fault_code == 4U);
    stage.tripped = false;
    tick(212U); assert(pulse_calls == 2U); /* Healthy readings never restart. */
    assert(commissioning_start(&inputs, &controller, true, 2, now_ms) == CONTROL_INTERLOCK);
    assert(!inputs.hardware_verified);
    for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(!controller.config.current_limit_commissioned[i] && !inputs.current[i].calibrated);
    }
}

static void test_bad_trip_proof(void)
{
    for (unsigned fault = 0U; fault < 10U; ++fault) {
        reset(); prepare(); start(1); tick(102U); make_good_trip();
        switch (fault) {
        case 0: stage.tripped = false; break;
        case 1: stage.trip_timer_us = 0U; break;
        case 2: stage.trip_timer_us = 1000U; break;
        case 3: stage.last_adc = prepared_baseline + 21U; break;
        case 4: stage.pin_low = false; break;
        case 5: stage.timer_running = true; break;
        case 6: stage.samples = 7U; break;
        case 7: stage.trip_adc = prepared_baseline + 162U; break;
        case 8: stage.trip_adc = 4095U; break;
        default: stage.trip_adc = 0U; break;
        }
        tick(110U);
        assert(snapshot().state == TEST_FAULT && snapshot().fault_code == 3U);
        assert((snapshot().flags & TEST_VERIFIED) == 0U);
        assert(pulse_calls == 1U && !commissioning_active());
    }
}

static void verify_trip(void)
{
    reset(); prepare(); start(1);
    tick(102U);
    make_good_trip();
    tick(110U);
    assert(snapshot().state == TEST_TRIP_PASSED);
    assert((snapshot().flags & TEST_VERIFIED) != 0U);
}

static void complete_one_second(void)
{
    start(2);
    const uint32_t started = now_ms;
    for (uint32_t at = started + 2U; at < started + 1000U; at += 20U) { tick(at); }
    tick(started + 1000U);
    assert(snapshot().state == TEST_COMPLETE && snapshot().pulses == 50U);
    assert(snapshot().elapsed_ms == 1000U && snapshot().duration_ms == 1000U);
    assert(!commissioning_active());
}

static void test_forty_percent_thermal_step(void)
{
    verify_trip();
    const tec_config_t config = controller.config;
    /* The operator can request this profile directly after the trip proof. */
    assert(snapshot().state == TEST_TRIP_PASSED);

    for (unsigned repeat = 0U; repeat < 2U; ++repeat) {
        const unsigned pulses_before = pulse_calls;
        start(3); /* Each 30 s / 5-of-12 ms step needs a new explicit command. */
        assert(commissioning_start(&inputs, &controller, true, 3, now_ms) == CONTROL_INTERLOCK);
        const uint32_t started = now_ms;
        uint32_t heartbeat = started;
        for (uint32_t at = started + 2U; at < started + 30000U; at += 12U) {
            refresh(at);
            if (at - heartbeat >= 1000U) { heartbeat = at; }
            commissioning_tick(&inputs, &controller, true, now_ms, heartbeat);
            assert(commissioning_active());
        }
        assert(pulse_calls - pulses_before == 2500U);
        tick(started + 30000U);
        assert(snapshot().state == TEST_COMPLETE && snapshot().fault_code == 0U);
        assert(snapshot().duration_ms == 30000U && snapshot().elapsed_ms == 30000U);
        assert(snapshot().period_ms == 12U && snapshot().on_us == 5000U);
        assert(snapshot().pulses == 2500U && !commissioning_active());
        tick(started + 30020U);
        assert(pulse_calls - pulses_before == 2500U); /* No automatic repeat. */
        for (unsigned i = pulses_before + 1U; i < pulse_calls; ++i) {
            assert(pulse_times[i] - pulse_times[i - 1U] >= 12U);
        }
        assert_normal_policy_unchanged(&config);
    }

    verify_trip(); complete_one_second(); start(3);
    const uint32_t started = now_ms;
    tick(started + 2U);
    refresh(started + 1501U);
    commissioning_tick(&inputs, &controller, true, now_ms, started);
    assert(snapshot().state == TEST_FAULT && snapshot().fault_code == 7U);
    assert(!commissioning_active()); /* Longer run still requires host leases. */
}

static void test_thirty_second_gpio_low_preflight(void)
{
    reset(); prepare();
    assert((snapshot().flags & TEST_VERIFIED) == 0U);
    assert(commissioning_start(&inputs, &controller, true, 3, now_ms) == CONTROL_INTERLOCK);
    start(4);
    assert(!prepared_drive && (snapshot().flags & TEST_DRIVE) == 0U);
    const uint32_t started = now_ms;
    for (uint32_t at = started + 2U; at < started + 30000U; at += 12U) { tick(at); }
    assert(pulse_calls == 2500U && commissioning_active());
    tick(started + 30000U);
    assert(snapshot().state == TEST_COMPLETE && snapshot().pulses == 2500U);
    assert(!commissioning_active() && !commissioning_owns_adc());
    assert((snapshot().flags & (TEST_VERIFIED | TEST_DRIVE)) == 0U);
    tick(started + 30020U);
    assert(pulse_calls == 2500U);
    assert(commissioning_start(&inputs, &controller, true, 3, now_ms) == CONTROL_INTERLOCK);
}

static void test_zero_evidence_expiry(void)
{
    verify_trip();
    refresh(300100U); /* Exactly 300000 ms after the zero measurement. */
    assert((snapshot().flags & (TEST_ZERO | TEST_VERIFIED)) == (TEST_ZERO | TEST_VERIFIED));
    start(2);
    commissioning_stop(0U, now_ms);

    refresh(300101U);
    assert((snapshot().flags & (TEST_ZERO | TEST_VERIFIED)) == 0U);
    assert(commissioning_start(&inputs, &controller, true, 2, now_ms) == CONTROL_INTERLOCK);
    assert(commissioning_start(&inputs, &controller, true, 3, now_ms) == CONTROL_INTERLOCK);
    assert(commissioning_start(&inputs, &controller, true, 0, now_ms) == CONTROL_INTERLOCK);
}

static void test_live_guards(void)
{
    reset(); prepare(); start(0);
    float dew;
    assert(tec_dewpoint_c(inputs.ambient_c, inputs.rh_percent, &dew));
    const float floor = fmaxf(controller.config.absolute_min_c,
                              dew + controller.config.dewpoint_margin_c);
    inputs.plate[0].temperature_c = floor + 0.501f;
    tick(102U); assert(commissioning_active());
    inputs.plate[0].temperature_c = floor + 0.5f;
    tick(103U);
    assert(snapshot().state == TEST_FAULT && snapshot().fault_code == 5U);
    assert(snapshot().min_plate_mc >= (int32_t)lroundf((floor + 0.5f) * 1000.0f));

    reset(); prepare(); start(0);
    now_ms = 500U;
    inputs.ambient_timestamp_ms = now_ms;
    commissioning_tick(&inputs, &controller, true, now_ms, now_ms);
    assert(commissioning_active()); /* Plate age exactly 400 ms. */
    now_ms = 501U;
    commissioning_tick(&inputs, &controller, true, now_ms, now_ms);
    assert(snapshot().fault_code == 6U && !commissioning_active());

    reset(); refresh(1498U); prepare(); start(0);
    refresh(1500U);
    commissioning_tick(&inputs, &controller, true, now_ms, 0U);
    assert(commissioning_active());
    refresh(1501U);
    commissioning_tick(&inputs, &controller, true, now_ms, 0U);
    assert(snapshot().fault_code == 7U && !commissioning_active());

    for (unsigned problem = 0U; problem < 4U; ++problem) {
        reset(); prepare(); start(0);
        if (problem == 0U) { stage.prepared = false; }
        if (problem == 1U) { stage.selftest_passed = false; }
        if (problem == 2U) { pulse_ok = false; }
        if (problem == 3U) { stage.tripped = true; }
        tick(102U);
        assert(snapshot().state == TEST_FAULT);
        assert(snapshot().fault_code == (problem == 3U ? 4U : 3U));
        tick(122U);
        assert(!commissioning_active());
    }
}

static void select_target(float target)
{
    float dew;
    assert(tec_dewpoint_c(inputs.ambient_c, inputs.rh_percent, &dew));
    controller.config.dewpoint_margin_c = target - dew;
}

static void test_hold27_below_target_and_hysteresis(void)
{
    reset(); select_target(27.0f); prepare();
    inputs.plate[0].temperature_c = 26.5f;
    assert(commissioning_start(&inputs, &controller, true, 5, now_ms) == CONTROL_INTERLOCK);
    start(6);
    assert(!prepared_drive && (snapshot().flags & TEST_DRIVE) == 0U);
    assert(snapshot().kind == 6U && snapshot().control_target_mc == 27000);
    assert(snapshot().control_state == 2U && snapshot().control_duty_milli == 0U);
    assert(snapshot().plate_mc == 26500 && snapshot().guard_floor_mc < 25000);
    tick(1100U);
    assert(pulse_calls == 0U && commissioning_active() && stage.prepared);
    inputs.plate[0].temperature_c = 27.19f;
    tick(1112U);
    assert(snapshot().control_state == 2U && snapshot().control_duty_milli == 0U);
    inputs.plate[0].temperature_c = 27.2f;
    tick(1124U);
    assert(snapshot().control_state == 1U && snapshot().control_duty_milli == 0U);
    tick(2124U);
    assert(snapshot().control_duty_milli <= 50U);
    for (uint32_t at = 2136U; at <= 4136U; at += 12U) { tick(at); }
    assert(pulse_calls > 0U);
    const unsigned before = pulse_calls;
    const unsigned stopped_before = off_calls;
    stage.timer_running = true;
    stage.pin_low = false;
    inputs.plate[0].temperature_c = 27.0f;
    tick(4148U);
    assert(off_calls > stopped_before && !stage.timer_running && stage.pin_low);
    assert(stage.prepared); /* Abort the pulse without losing the ADC monitor. */
    assert(snapshot().control_state == 2U && snapshot().control_duty_milli == 0U);
    assert(pulse_calls == before);
    inputs.plate[0].temperature_c = 27.19f;
    for (uint32_t at = 4160U; at < 5160U; at += 12U) { tick(at); }
    assert(pulse_calls == before); /* Holding cleared accumulated pulse credit. */
}

static void test_hold27_slew_taper_and_no_burst(void)
{
    verify_trip(); select_target(27.0f);
    inputs.plate[0].temperature_c = 31.0f;
    start(5);
    assert(prepared_drive && snapshot().kind == 5U && snapshot().control_target_mc == 27000);
    const tec_config_t config = controller.config;
    const uint32_t started = now_ms;
    uint32_t previous = 0U;
    for (uint32_t at = started + 12U; at <= started + 12000U; at += 12U) {
        tick(at);
        const uint32_t demand = snapshot().control_duty_milli;
        assert(demand <= 417U);
        assert(demand <= previous + 1U); /* <=0.0006 duty per12ms, rounded. */
        assert(demand <= (at - started) / 20U + 1U); /* <=0.05 duty/second overall. */
        previous = demand;
    }
    assert(snapshot().control_duty_milli == 417U);
    const unsigned before_delay = pulse_calls;
    tick(started + 12500U);
    assert(pulse_calls == before_delay + 1U); /* One opportunity after delay. */
    tick(started + 12500U);
    tick(started + 12511U);
    assert(pulse_calls == before_delay + 1U);
    tick(started + 12512U);
    assert(pulse_calls == before_delay + 2U);
    inputs.plate[0].temperature_c = 27.1f;
    tick(started + 12524U);
    assert(snapshot().control_duty_milli <= 21U); /* No wind-up while capped. */
    assert(snapshot().control_state == 1U);
    for (unsigned i = 2U; i < pulse_calls; ++i) {
        assert(pulse_times[i] - pulse_times[i - 1U] >= 12U);
    }
    assert_normal_policy_unchanged(&config);
}

static void test_hold27_five_minute_timeout(void)
{
    reset(); select_target(27.0f); prepare(); start(6);
    const uint32_t started = now_ms;
    for (uint32_t at = started + 12U; at < started + 300000U; at += 12U) { tick(at); }
    assert(commissioning_active() && !prepared_drive);
    assert(pulse_calls > 1000U && pulse_calls <= 25000U);
    tick(started + 300000U);
    assert(snapshot().state == TEST_COMPLETE && snapshot().elapsed_ms == 300000U);
    assert(snapshot().kind == 6U && snapshot().control_target_mc == 27000);
    assert(snapshot().control_state == 0U && snapshot().control_duty_milli == 0U);
    assert(snapshot().guard_floor_mc > 0 && snapshot().plate_mc == 28000);
    const unsigned stopped = pulse_calls;
    tick(started + 300012U);
    assert(pulse_calls == stopped && !commissioning_active());
}

static void test_hold27_dew_and_liveness_guards(void)
{
    reset(); select_target(27.0f); prepare();
    inputs.plate[0].temperature_c = 26.4f;
    start(6);
    assert(snapshot().control_target_mc == 27000 && snapshot().control_state == 2U);
    /* Humidity changes the target itself; it does not reject a fixed27 target. */
    inputs.rh_percent = 70.0f;
    tick(112U);
    assert(commissioning_active() && snapshot().control_target_mc > 27000);
    assert(snapshot().control_state == 2U && snapshot().control_duty_milli == 0U);
    inputs.rh_percent = 40.0f;
    tick(124U);
    assert(commissioning_active() && snapshot().control_target_mc < 27000);
    assert(snapshot().control_state == 1U);
    /* Crossing the actual dew-point cutoff remains a latched fault. */
    inputs.rh_percent = 70.0f;
    tick(136U);
    inputs.plate[0].temperature_c = (float)snapshot().guard_floor_mc / 1000.0f - 0.01f;
    tick(148U);
    assert(snapshot().state == TEST_FAULT && snapshot().fault_code == 5U);

    reset(); select_target(27.0f); prepare();
    inputs.plate[0].temperature_c = 26.5f;
    start(6);
    now_ms = 501U;
    inputs.ambient_timestamp_ms = now_ms;
    commissioning_tick(&inputs, &controller, true, now_ms, now_ms);
    assert(snapshot().state == TEST_FAULT && snapshot().fault_code == 6U);

    reset(); select_target(27.0f); prepare();
    inputs.plate[0].temperature_c = 26.5f;
    start(6);
    const uint32_t started = now_ms;
    refresh(started + 1501U);
    commissioning_tick(&inputs, &controller, true, now_ms, started);
    assert(snapshot().state == TEST_FAULT && snapshot().fault_code == 7U);

    verify_trip(); select_target(27.0f); start(5);
    stage.tripped = true;
    stage.trip_adc = prepared_baseline + 491U;
    tick(now_ms + 12U);
    assert(snapshot().state == TEST_FAULT && snapshot().fault_code == 4U);
    stage.tripped = false;
    const unsigned before = pulse_calls;
    tick(now_ms + 1000U);
    assert(pulse_calls == before && !commissioning_active());
    assert(commissioning_start(&inputs, &controller, true, 5, now_ms) == CONTROL_INTERLOCK);
}

static void test_verification_near_selected_target(void)
{
    reset(); select_target(27.0f);
    inputs.plate[0].temperature_c = 27.0f;
    prepare(); start(1);
    tick(102U); make_good_trip(); tick(110U);
    assert(snapshot().state == TEST_TRIP_PASSED);
    start(5);
    assert(snapshot().control_state == 2U && snapshot().control_duty_milli == 0U);
    const unsigned before = pulse_calls;
    tick(122U);
    assert(commissioning_active() && pulse_calls == before);
}

int main(void)
{
    test_zero_sampling_and_policy();
    test_bad_zero_and_selftest();
    test_explicit_commands_and_gates();
    test_dry_run_schedule_and_stop();
    test_trip_verification_and_guarded_run();
    test_bad_trip_proof();
    test_forty_percent_thermal_step();
    test_thirty_second_gpio_low_preflight();
    test_zero_evidence_expiry();
    test_live_guards();
    test_hold27_below_target_and_hysteresis();
    test_hold27_slew_taper_and_no_burst();
    test_hold27_five_minute_timeout();
    test_hold27_dew_and_liveness_guards();
    test_verification_near_selected_target();
    puts("commissioning engine tests passed");
    return 0;
}
