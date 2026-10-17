#include "controller.h"

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static void configured(tec_config_t *cfg)
{
    size_t i;
    tec_config_defaults(cfg);
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        cfg->current_limit_a[i] = 5.0f;
    }
}

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
        in.plate[i] = (tec_plate_sample_t) {30.0f, true, now};
        in.current[i] = (tec_current_sample_t) {1.0f, true, true, now};
    }
    return in;
}

static void freshen(tec_inputs_t *in, uint32_t now)
{
    size_t i;
    in->ambient_timestamp_ms = now;
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        in->plate[i].timestamp_ms = now;
        in->current[i].timestamp_ms = now;
    }
}

static void arm_all(tec_controller_t *c)
{
    size_t i;
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(tec_controller_set_armed(c, i, true));
    }
}

static void maximum_coldness_crosses_dewpoint_and_zero(void)
{
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in = good_inputs(0U);
    tec_outputs_t out;
    unsigned n;
    float previous = 0.0f;
    configured(&cfg);
    assert(cfg.max_duty == 0.5f);
    assert(tec_controller_init(&c, &cfg));
    assert(c.mode == TEC_MODE_REGULATED);
    assert(tec_controller_set_mode(&c, TEC_MODE_UNREGULATED));
    arm_all(&c);
    tec_controller_step(&c, &in, 0U, &out);
    assert(out.channel[0].duty == 0.0f);
    for (n = 1U; n <= 150U; ++n) {
        freshen(&in, n * 100U);
        in.plate[0].temperature_c = n < 20U ? 30.0f : (n < 40U ? 10.0f : -5.0f);
        tec_controller_step(&c, &in, n * 100U, &out);
        assert(isnan(out.target_c));
        assert(out.channel[0].armed && out.channel[0].active_faults == 0U);
        assert(!out.channel[0].guard_inhibited);
        assert(out.channel[0].duty >= previous);
        assert(out.channel[0].duty - previous <= 0.01001f);
        assert(out.channel[0].duty <= 1.0f);
        assert(c.channel[0].integral == 0.0f);
        previous = out.channel[0].duty;
    }
    assert(out.channel[0].duty == 1.0f); /* Unregulated maximum is100%, not50%. */
    assert(in.plate[0].temperature_c < 0.0f && in.plate[0].temperature_c < out.dewpoint_c);
    /* Explicit STOP zeroes stored demand immediately, with no downward slew. */
    assert(tec_controller_set_armed(&c, 0U, false));
    assert(c.channel[0].duty == 0.0f && !c.channel[0].armed);
    freshen(&in, 15100U);
    tec_controller_step(&c, &in, 15100U, &out);
    assert(out.channel[0].duty == 0.0f);
    assert(out.channel[1].duty == 1.0f);
}

static void mode_changes_disarm_every_channel(void)
{
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in = good_inputs(0U);
    tec_outputs_t out;
    size_t i;
    float previous;
    configured(&cfg);
    assert(tec_controller_init(&c, &cfg));
    tec_controller_step(&c, &in, 0U, &out);
    arm_all(&c);
    freshen(&in, 100U);
    tec_controller_step(&c, &in, 100U, &out);
    previous = c.channel[0].duty;
    assert(previous > 0.0f);
    assert(tec_controller_set_mode(&c, TEC_MODE_REGULATED));
    assert(c.channel[0].armed && c.channel[0].duty == previous);
    assert(!tec_controller_set_mode(&c, (tec_mode_t)2));
    assert(c.mode == TEC_MODE_REGULATED && c.channel[0].armed);
    assert(tec_controller_set_mode(&c, TEC_MODE_UNREGULATED));
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(!c.channel[i].armed && c.channel[i].duty == 0.0f && c.channel[i].integral == 0.0f);
    }
    freshen(&in, 200U);
    tec_controller_step(&c, &in, 200U, &out);
    assert(out.channel[0].duty == 0.0f);
    arm_all(&c);
    freshen(&in, 300U);
    tec_controller_step(&c, &in, 300U, &out);
    assert(out.channel[0].duty > 0.0f);
    assert(tec_controller_set_mode(&c, TEC_MODE_REGULATED));
    assert(!c.channel[0].armed && c.channel[0].duty == 0.0f);
    assert(!tec_controller_set_mode(NULL, TEC_MODE_UNREGULATED));
}

static void current_interlocks_apply_in_both_modes(void)
{
    const uint32_t fault_flags[] = {
        TEC_FAULT_CURRENT_INVALID, TEC_FAULT_CURRENT_UNVERIFIED,
        TEC_FAULT_CURRENT_STALE, TEC_FAULT_CURRENT_OVERLIMIT,
        TEC_FAULT_CURRENT_OVERLIMIT, TEC_FAULT_CURRENT_INVALID,
        TEC_FAULT_CURRENT_INVALID
    };
    tec_mode_t mode;
    size_t fault;
    for (mode = TEC_MODE_REGULATED; mode <= TEC_MODE_UNREGULATED;
         mode = (tec_mode_t)((int)mode + 1)) {
        for (fault = 0U; fault < sizeof(fault_flags) / sizeof(fault_flags[0]); ++fault) {
            tec_controller_t c;
            tec_config_t cfg;
            tec_inputs_t in = good_inputs(0U);
            tec_outputs_t out;
            configured(&cfg);
            cfg.sample_timeout_ms = 150U;
            assert(tec_controller_init(&c, &cfg));
            assert(tec_controller_set_mode(&c, mode));
            tec_controller_step(&c, &in, 0U, &out);
            arm_all(&c);
            freshen(&in, 100U);
            tec_controller_step(&c, &in, 100U, &out);
            assert(out.channel[1].duty > 0.0f);
            freshen(&in, 200U);
            switch (fault) {
            case 0U: in.current[1].valid = false; break;
            case 1U: in.current[1].calibrated = false; break;
            case 2U: in.current[1].timestamp_ms = 0U; break;
            case 3U: in.current[1].amperes = 5.01f; break;
            case 4U: in.current[1].amperes = -5.01f; break;
            case 5U: in.current[1].amperes = NAN; break;
            default: in.current[1].amperes = INFINITY; break;
            }
            tec_controller_step(&c, &in, 200U, &out);
            assert(out.channel[1].duty == 0.0f && !out.channel[1].armed);
            assert((out.channel[1].latched_faults & fault_flags[fault]) != 0U);
            assert(out.channel[0].duty > 0.0f && out.channel[2].duty > 0.0f);
            in = good_inputs(300U);
            tec_controller_step(&c, &in, 300U, &out);
            assert(out.channel[1].duty == 0.0f && !out.channel[1].armed);
            assert(out.channel[1].active_faults == 0U);
            assert(tec_controller_set_armed(&c, 1U, true));
            freshen(&in, 400U);
            tec_controller_step(&c, &in, 400U, &out);
            assert(out.channel[1].duty > 0.0f && out.channel[1].latched_faults == 0U);
        }
    }
}

static void defaults_cannot_arm_and_limits_validate(void)
{
    tec_controller_t c;
    tec_config_t cfg;
    tec_inputs_t in = good_inputs(0U);
    tec_outputs_t out;
    size_t i;
    tec_config_defaults(&cfg);
    assert(tec_controller_init(&c, &cfg));
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(cfg.current_limit_a[i] == 0.0f);
        assert(!tec_controller_set_armed(&c, i, true));
    }
    tec_controller_step(&c, &in, 0U, &out);
    assert(out.channel[0].active_faults & TEC_FAULT_CURRENT_UNCOMMISSIONED);
    assert(out.channel[0].duty == 0.0f);
    assert(tec_controller_set_mode(&c, TEC_MODE_UNREGULATED));
    assert(!tec_controller_set_armed(&c, 0U, true));
    configured(&cfg);
    cfg.current_limit_a[0] = 50.01f;
    assert(!tec_controller_init(&c, &cfg));
    configured(&cfg);
    cfg.current_limit_a[1] = 10.01f;
    assert(!tec_controller_init(&c, &cfg));
    configured(&cfg);
    cfg.current_limit_a[2] = -1.0f;
    assert(!tec_controller_init(&c, &cfg));
    configured(&cfg);
    cfg.current_limit_a[0] = NAN;
    assert(!tec_controller_init(&c, &cfg));
}

static void unregulated_retains_other_sensor_and_timing_stops(void)
{
    unsigned fault;
    for (fault = 0U; fault < 8U; ++fault) {
        tec_controller_t c;
        tec_config_t cfg;
        tec_inputs_t in = good_inputs(0U);
        tec_outputs_t out;
        uint32_t now = 100U;
        configured(&cfg);
        assert(tec_controller_init(&c, &cfg));
        assert(tec_controller_set_mode(&c, TEC_MODE_UNREGULATED));
        tec_controller_step(&c, &in, 0U, &out);
        arm_all(&c);
        freshen(&in, now);
        switch (fault) {
        case 0U: in.hardware_verified = false; break;
        case 1U: in.ambient_c = NAN; break;
        case 2U: in.rh_percent = 101.0f; break;
        case 3U: in.plate[0].temperature_c = cfg.plate_min_c - 0.01f; break;
        case 4U: in.plate[0].temperature_c = cfg.plate_max_c + 0.01f; break;
        case 5U: in.plate[0].temperature_c = cfg.plate_min_c; break;
        case 6U: in.plate[0].temperature_c = cfg.plate_max_c; break;
        default: now = cfg.max_step_ms + 1U; freshen(&in, now); break;
        }
        tec_controller_step(&c, &in, now, &out);
        assert(out.channel[0].duty == 0.0f && !out.channel[0].armed);
        assert(out.channel[0].latched_faults != 0U);
    }
    /* A raw valid ambient sample can have a dew point outside the fit: the
     * unregulated mode does not depend on that condensation calculation. */
    {
        tec_controller_t c;
        tec_config_t cfg;
        tec_inputs_t in = good_inputs(0U);
        tec_outputs_t out;
        configured(&cfg);
        assert(tec_controller_init(&c, &cfg));
        assert(tec_controller_set_mode(&c, TEC_MODE_UNREGULATED));
        in.ambient_c = -40.0f;
        in.rh_percent = 10.0f;
        tec_controller_step(&c, &in, 0U, &out);
        arm_all(&c);
        freshen(&in, 100U);
        tec_controller_step(&c, &in, 100U, &out);
        assert(out.channel[0].duty > 0.0f && isnan(out.dewpoint_c));
    }
}

int main(void)
{
    maximum_coldness_crosses_dewpoint_and_zero();
    mode_changes_disarm_every_channel();
    current_interlocks_apply_in_both_modes();
    defaults_cannot_arm_and_limits_validate();
    unregulated_retains_other_sensor_and_timing_stops();
    puts("mode/current interlock tests passed");
    return 0;
}
