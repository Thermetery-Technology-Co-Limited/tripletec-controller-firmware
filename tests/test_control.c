#include "controller.h"

#include <assert.h>
#include <float.h>
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

static tec_inputs_t good_inputs(uint32_t now)
{
    tec_inputs_t in;
    size_t i;
    memset(&in, 0, sizeof(in));
    in.ambient_c = 25.0f;
    in.rh_percent = 50.0f;
    in.ambient_valid = true;
    in.ambient_timestamp_ms = now;
    in.hardware_verified = true;
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        in.plate[i].temperature_c = 30.0f;
        in.plate[i].valid = true;
        in.plate[i].timestamp_ms = now;
    }
    return in;
}

static void freshen(tec_inputs_t *in, uint32_t now)
{
    size_t i;
    in->ambient_timestamp_ms = now;
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        in->plate[i].timestamp_ms = now;
    }
}

static void arm_all(tec_controller_t *c)
{
    size_t i;
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(tec_controller_set_armed(c, i, true));
    }
}

static void assert_off(const tec_outputs_t *out)
{
    size_t i;
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(out->channel[i].duty == 0.0f);
    }
}

static void missing_hardware_and_explicit_arming(void)
{
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in;
    tec_outputs_t out;
    tec_config_defaults(&cfg);
    assert(cfg.dewpoint_margin_c == 3.0f && cfg.absolute_min_c == 0.0f);
    assert(tec_controller_init(&c, &cfg));
    memset(&in, 0, sizeof(in));
    tec_controller_step(&c, &in, 0U, &out);
    assert_off(&out);
    in = good_inputs(100U);
    tec_controller_step(&c, &in, 100U, &out);
    assert_off(&out);
    arm_all(&c);
    in.hardware_verified = false;
    freshen(&in, 200U);
    tec_controller_step(&c, &in, 200U, &out);
    assert_off(&out);
    assert(!out.channel[0].armed);
    assert(out.channel[0].latched_faults & TEC_FAULT_HARDWARE_GATE);
    in.hardware_verified = true;
    freshen(&in, 300U);
    tec_controller_step(&c, &in, 300U, &out);
    assert_off(&out); /* Hardware recovery does not rearm. */
    arm_all(&c);
    freshen(&in, 400U);
    tec_controller_step(&c, &in, 400U, &out);
    assert(out.channel[0].duty > 0.0f);
    assert(out.channel[0].latched_faults == 0U);
    assert(fabsf(out.target_c - 16.8515836f) < 0.001f);
}

static void independent_plate_fault_and_latch(void)
{
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in = good_inputs(0U);
    tec_outputs_t out;
    tec_config_defaults(&cfg);
    assert(tec_controller_init(&c, &cfg));
    tec_controller_step(&c, &in, 0U, &out);
    arm_all(&c);
    freshen(&in, 100U);
    in.plate[1].valid = false;
    tec_controller_step(&c, &in, 100U, &out);
    assert(out.channel[0].duty > 0.0f && out.channel[2].duty > 0.0f);
    assert(out.channel[1].duty == 0.0f && !out.channel[1].armed);
    assert(out.channel[1].latched_faults & TEC_FAULT_PLATE_INVALID);
    in.plate[1].valid = true;
    freshen(&in, 200U);
    tec_controller_step(&c, &in, 200U, &out);
    assert(out.channel[1].duty == 0.0f && !out.channel[1].armed);
    assert(out.channel[1].active_faults == 0U);
    assert(tec_controller_set_armed(&c, 1U, true));
    freshen(&in, 300U);
    tec_controller_step(&c, &in, 300U, &out);
    assert(out.channel[1].duty > 0.0f);
    assert(out.channel[1].latched_faults == 0U);
}

static void invalid_and_stale_samples(void)
{
    const float bad_rh[] = {0.0f, -1.0f, 100.01f, NAN, INFINITY};
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in;
    tec_outputs_t out;
    size_t i;
    tec_config_defaults(&cfg);
    cfg.sample_timeout_ms = 150U;
    for (i = 0U; i < sizeof(bad_rh) / sizeof(bad_rh[0]); ++i) {
        assert(tec_controller_init(&c, &cfg));
        in = good_inputs(0U);
        tec_controller_step(&c, &in, 0U, &out);
        arm_all(&c);
        in.rh_percent = bad_rh[i];
        freshen(&in, 100U);
        tec_controller_step(&c, &in, 100U, &out);
        assert_off(&out);
        assert(out.channel[0].latched_faults & TEC_FAULT_AMBIENT_INVALID);
    }
    assert(tec_controller_init(&c, &cfg));
    in = good_inputs(0U);
    tec_controller_step(&c, &in, 0U, &out);
    arm_all(&c);
    freshen(&in, 200U);
    in.ambient_timestamp_ms = 0U;
    tec_controller_step(&c, &in, 200U, &out);
    assert_off(&out);
    assert(out.channel[0].latched_faults & TEC_FAULT_AMBIENT_STALE);
    assert(isnan(out.target_c));
    arm_all(&c);
    freshen(&in, 300U);
    in.plate[2].timestamp_ms = 0U;
    tec_controller_step(&c, &in, 300U, &out);
    assert(out.channel[0].duty > 0.0f);
    assert(out.channel[2].duty == 0.0f);
    assert(out.channel[2].latched_faults & TEC_FAULT_PLATE_STALE);
    arm_all(&c);
    freshen(&in, 400U);
    in.ambient_valid = false; /* A new failure never reuses old good data. */
    tec_controller_step(&c, &in, 400U, &out);
    assert_off(&out);
    assert(out.channel[0].latched_faults & TEC_FAULT_AMBIENT_INVALID);
    in.ambient_valid = true;
    arm_all(&c);
    freshen(&in, 500U);
    in.plate[0].temperature_c = NAN;
    in.plate[1].temperature_c = cfg.plate_max_c + 1.0f;
    tec_controller_step(&c, &in, 500U, &out);
    assert(out.channel[0].latched_faults & TEC_FAULT_PLATE_INVALID);
    assert(out.channel[1].latched_faults & TEC_FAULT_PLATE_INVALID);
    assert(out.channel[2].duty > 0.0f);
}

static void guard_humidity_jump_and_absolute_floor(void)
{
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in = good_inputs(0U);
    tec_outputs_t out;
    tec_config_defaults(&cfg);
    cfg.rise_per_second = 10.0f;
    assert(tec_controller_init(&c, &cfg));
    tec_controller_step(&c, &in, 0U, &out);
    arm_all(&c);
    freshen(&in, 100U);
    in.plate[0].temperature_c = 20.0f;
    tec_controller_step(&c, &in, 100U, &out);
    assert(out.channel[0].duty > 0.0f);
    in.rh_percent = 100.0f; /* Dew point jumps to 25; new floor is 28. */
    freshen(&in, 200U);
    tec_controller_step(&c, &in, 200U, &out);
    assert(out.target_c == 28.0f);
    assert(out.channel[0].duty == 0.0f);
    assert(out.channel[0].guard_inhibited && out.channel[0].armed);
    assert(c.channel[0].integral == 0.0f);
    in.plate[0].temperature_c = out.target_c;
    freshen(&in, 300U);
    tec_controller_step(&c, &in, 300U, &out);
    assert(out.channel[0].guard_inhibited && out.channel[0].duty == 0.0f);
    in.plate[0].temperature_c = out.target_c + cfg.restart_hysteresis_c * 0.5f;
    freshen(&in, 350U);
    tec_controller_step(&c, &in, 350U, &out);
    assert(out.channel[0].guard_inhibited && out.channel[0].duty == 0.0f);
    in.plate[0].temperature_c = 30.0f;
    freshen(&in, 400U);
    tec_controller_step(&c, &in, 400U, &out);
    assert(out.channel[0].duty > 0.0f && out.channel[0].armed);
    in.ambient_c = 0.0f;
    in.rh_percent = 50.0f;
    in.plate[0].temperature_c = 0.0f;
    freshen(&in, 500U);
    tec_controller_step(&c, &in, 500U, &out);
    assert(out.target_c == 0.0f);
    assert(out.channel[0].guard_inhibited);
}

static void slew_ceiling_and_antiwindup(void)
{
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in = good_inputs(0U);
    tec_outputs_t out;
    unsigned n;
    float previous = 0.0f;
    tec_config_defaults(&cfg);
    assert(tec_controller_init(&c, &cfg));
    tec_controller_step(&c, &in, 0U, &out);
    arm_all(&c);
    for (n = 1U; n <= 200U; ++n) {
        freshen(&in, n * 100U);
        tec_controller_step(&c, &in, n * 100U, &out);
        assert(out.channel[0].duty <= cfg.max_duty);
        assert(out.channel[0].duty - previous <= 0.01001f);
        assert(out.channel[0].duty >= 0.0f);
        previous = out.channel[0].duty;
    }
    assert(fabsf(out.channel[0].duty - cfg.max_duty) < 0.0001f);
    assert(c.channel[0].integral == 0.0f); /* No saturation windup. */
    in.plate[0].temperature_c = out.target_c + 0.1f;
    freshen(&in, 20100U);
    tec_controller_step(&c, &in, 20100U, &out);
    assert(out.channel[0].duty < 0.02f); /* Reductions are immediate. */
    /* A modest error below either limiter allows integral action. */
    assert(c.channel[0].integral > 0.0f);
}

static void time_wrap_and_long_gap(void)
{
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in = good_inputs(UINT32_MAX - 49U);
    tec_outputs_t out;
    tec_config_defaults(&cfg);
    assert(tec_controller_init(&c, &cfg));
    arm_all(&c);
    tec_controller_step(&c, &in, UINT32_MAX - 49U, &out);
    assert_off(&out); /* First sample has no elapsed interval. */
    freshen(&in, 50U);
    tec_controller_step(&c, &in, 50U, &out);
    assert(out.channel[0].duty > 0.0f);
    assert(out.channel[0].active_faults == 0U);
    freshen(&in, 50U);
    tec_controller_step(&c, &in, 50U, &out);
    assert(out.channel[0].duty <= 0.01001f); /* No rise at zero dt. */
    freshen(&in, 1051U);
    tec_controller_step(&c, &in, 1051U, &out);
    assert_off(&out);
    assert(out.channel[0].latched_faults & TEC_FAULT_TIMING);
    freshen(&in, 1151U);
    tec_controller_step(&c, &in, 1151U, &out);
    assert_off(&out);
    assert(!out.channel[0].armed);
}

static void impossible_target_and_bad_configuration(void)
{
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in = good_inputs(0U);
    tec_outputs_t out;
    tec_config_defaults(&cfg);
    cfg.plate_max_c = 20.0f;
    assert(tec_controller_init(&c, &cfg));
    in.ambient_c = 30.0f;
    in.rh_percent = 100.0f;
    in.plate[0].temperature_c = 19.0f;
    arm_all(&c);
    tec_controller_step(&c, &in, 0U, &out);
    assert_off(&out);
    assert(out.target_c == 33.0f);
    assert(out.channel[0].latched_faults & TEC_FAULT_TARGET_RANGE);
    tec_config_defaults(&cfg);
    cfg.max_duty = NAN;
    assert(!tec_controller_init(&c, &cfg));
    assert(!tec_controller_set_armed(&c, 0U, true));
    tec_controller_step(&c, &in, 100U, &out);
    assert_off(&out);
    assert(out.channel[0].active_faults & TEC_FAULT_CONFIGURATION);
    tec_config_defaults(&cfg);
    cfg.dewpoint_margin_c = 0.0f;
    assert(!tec_controller_init(&c, &cfg));
    tec_config_defaults(&cfg);
    cfg.absolute_min_c = -1.0f;
    assert(!tec_controller_init(&c, &cfg));
    tec_config_defaults(&cfg);
    cfg.kp = FLT_MAX;
    assert(tec_controller_init(&c, &cfg));
    in = good_inputs(0U);
    tec_controller_step(&c, &in, 0U, &out);
    arm_all(&c);
    freshen(&in, 100U);
    tec_controller_step(&c, &in, 100U, &out);
    assert_off(&out);
    assert(out.channel[0].latched_faults & TEC_FAULT_ARITHMETIC);
    assert(!tec_controller_set_armed(&c, TEC_CHANNEL_COUNT, true));
    tec_controller_step(&c, NULL, 200U, &out);
    assert_off(&out);
}

int main(void)
{
    missing_hardware_and_explicit_arming();
    independent_plate_fault_and_latch();
    invalid_and_stale_samples();
    guard_humidity_jump_and_absolute_floor();
    slew_ceiling_and_antiwindup();
    time_wrap_and_long_gap();
    impossible_target_and_bad_configuration();
    puts("control tests passed");
    return 0;
}
