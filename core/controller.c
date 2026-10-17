#include "controller.h"

#include "dewpoint.h"

#include <math.h>
#include <string.h>

static bool config_valid(const tec_config_t *c)
{
    return c != NULL &&
        isfinite(c->dewpoint_margin_c) && c->dewpoint_margin_c > 0.0f &&
        isfinite(c->absolute_min_c) && c->absolute_min_c >= 0.0f &&
        isfinite(c->ambient_min_c) && isfinite(c->ambient_max_c) &&
        c->ambient_min_c >= TEC_DEWPOINT_MIN_C &&
        c->ambient_max_c <= TEC_DEWPOINT_MAX_C &&
        c->ambient_min_c < c->ambient_max_c &&
        isfinite(c->plate_min_c) && isfinite(c->plate_max_c) &&
        c->plate_min_c < c->plate_max_c &&
        c->absolute_min_c >= c->plate_min_c &&
        c->absolute_min_c <= c->plate_max_c &&
        isfinite(c->restart_hysteresis_c) && c->restart_hysteresis_c >= 0.0f &&
        c->sample_timeout_ms > 0U && c->sample_timeout_ms <= INT32_MAX &&
        c->max_step_ms > 0U && c->max_step_ms <= INT32_MAX &&
        isfinite(c->kp) && c->kp >= 0.0f &&
        isfinite(c->ki) && c->ki >= 0.0f &&
        isfinite(c->max_duty) && c->max_duty > 0.0f && c->max_duty <= 1.0f &&
        isfinite(c->rise_per_second) && c->rise_per_second > 0.0f &&
        isfinite(c->current_limit_a[0]) && c->current_limit_a[0] >= 0.0f &&
        c->current_limit_a[0] <= 50.0f &&
        isfinite(c->current_limit_a[1]) && c->current_limit_a[1] >= 0.0f &&
        c->current_limit_a[1] <= 10.0f &&
        isfinite(c->current_limit_a[2]) && c->current_limit_a[2] >= 0.0f &&
        c->current_limit_a[2] <= 10.0f;
}

static float clamp(float v, float lo, float hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

void tec_config_defaults(tec_config_t *c)
{
    if (c == NULL) {
        return;
    }
    *c = (tec_config_t) {
        .dewpoint_margin_c = 3.0f,
        .absolute_min_c = 0.0f,
        .ambient_min_c = TEC_DEWPOINT_MIN_C,
        .ambient_max_c = TEC_DEWPOINT_MAX_C,
        .plate_min_c = -40.0f,
        .plate_max_c = 100.0f,
        .restart_hysteresis_c = 0.2f,
        .sample_timeout_ms = 2000U,
        .max_step_ms = 1000U,
        /* Provisional gains and output cap: characterize the assembled plant
         * before enabling a board build. These are not commissioned values. */
        .kp = 0.10f,
        .ki = 0.02f,
        .max_duty = 0.50f,
        .rise_per_second = 0.10f,
        .current_limit_a = {0.0f, 0.0f, 0.0f}
    };
}

bool tec_controller_init(tec_controller_t *c, const tec_config_t *config)
{
    if (c == NULL) {
        return false;
    }
    memset(c, 0, sizeof(*c));
    if (!config_valid(config)) {
        return false;
    }
    c->config = *config;
    c->mode = TEC_MODE_REGULATED;
    c->configuration_valid = true;
    return true;
}

bool tec_controller_set_armed(tec_controller_t *c, size_t channel, bool armed)
{
    tec_channel_state_t *s;
    if (c == NULL || !c->configuration_valid || channel >= TEC_CHANNEL_COUNT) {
        return false;
    }
    s = &c->channel[channel];
    s->armed = false;
    s->duty = 0.0f;
    s->integral = 0.0f;
    s->guard_wait = true;
    if (armed && (c->config.current_limit_a[channel] <= 0.0f ||
        (c->mode != TEC_MODE_REGULATED && c->mode != TEC_MODE_UNREGULATED))) {
        return false;
    }
    s->armed = armed;
    if (armed) {
        s->latched_faults = TEC_FAULT_NONE;
    }
    return true;
}

bool tec_controller_set_mode(tec_controller_t *c, tec_mode_t mode)
{
    size_t i;
    if (c == NULL || !c->configuration_valid ||
        (mode != TEC_MODE_REGULATED && mode != TEC_MODE_UNREGULATED)) {
        return false;
    }
    if (c->mode != mode) {
        for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
            (void)tec_controller_set_armed(c, i, false);
        }
        c->mode = mode;
    }
    return true;
}

static void stop_channel(tec_channel_state_t *s, uint32_t faults)
{
    s->duty = 0.0f;
    s->integral = 0.0f;
    if (s->armed && faults != TEC_FAULT_NONE) {
        s->latched_faults |= faults;
        s->armed = false;
    }
}

static void copy_channel_output(const tec_channel_state_t *s,
                                tec_channel_output_t *o, uint32_t active_faults)
{
    o->duty = s->duty;
    o->armed = s->armed;
    o->active_faults = active_faults;
    o->latched_faults = s->latched_faults;
}

void tec_controller_step(tec_controller_t *c, const tec_inputs_t *in,
                         uint32_t now_ms, tec_outputs_t *out)
{
    uint32_t common_faults = TEC_FAULT_NONE;
    uint32_t elapsed_ms = 0U;
    float dt = 0.0f;
    float dewpoint = NAN;
    float target = NAN;
    size_t i;
    tec_outputs_t discarded_output;

    if (out == NULL) {
        out = &discarded_output;
    }
    memset(out, 0, sizeof(*out));
    out->dewpoint_c = NAN;
    out->target_c = NAN;
    if (c == NULL) {
        for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
            out->channel[i].active_faults = TEC_FAULT_CONFIGURATION;
        }
        return;
    }
    if (!c->configuration_valid ||
        (c->mode != TEC_MODE_REGULATED && c->mode != TEC_MODE_UNREGULATED)) {
        common_faults |= TEC_FAULT_CONFIGURATION;
    }
    if (c->have_last_step && c->configuration_valid) {
        elapsed_ms = now_ms - c->last_step_ms;
        if (elapsed_ms > c->config.max_step_ms) {
            common_faults |= TEC_FAULT_TIMING;
        } else {
            dt = (float)elapsed_ms * 0.001f;
        }
    }
    c->last_step_ms = now_ms;
    c->have_last_step = true;
    if (in == NULL) {
        common_faults |= TEC_FAULT_AMBIENT_INVALID | TEC_FAULT_HARDWARE_GATE;
    } else if (c->configuration_valid) {
        if (!in->hardware_verified) {
            common_faults |= TEC_FAULT_HARDWARE_GATE;
        }
        if (!in->ambient_valid || !isfinite(in->ambient_c) ||
            in->ambient_c < c->config.ambient_min_c ||
            in->ambient_c > c->config.ambient_max_c ||
            !isfinite(in->rh_percent) || in->rh_percent < 0.0f || in->rh_percent > 100.0f) {
            common_faults |= TEC_FAULT_AMBIENT_INVALID;
        }
        if (!tec_dewpoint_c(in->ambient_c, in->rh_percent, &dewpoint) &&
            c->mode == TEC_MODE_REGULATED) {
            common_faults |= TEC_FAULT_AMBIENT_INVALID;
        }
        if ((uint32_t)(now_ms - in->ambient_timestamp_ms) >
            c->config.sample_timeout_ms) {
            common_faults |= TEC_FAULT_AMBIENT_STALE;
        }
        if ((common_faults & (TEC_FAULT_AMBIENT_INVALID | TEC_FAULT_AMBIENT_STALE)) == 0U) {
            if (c->mode == TEC_MODE_REGULATED) {
                target = fmaxf(dewpoint + c->config.dewpoint_margin_c,
                               c->config.absolute_min_c);
                /* An infeasible condensation floor must disable cooling, never
                 * be clamped downward to an unsafe regulated target. */
                if (!isfinite(target) || target > c->config.plate_max_c) {
                    common_faults |= TEC_FAULT_TARGET_RANGE;
                }
            }
            out->dewpoint_c = dewpoint;
            out->target_c = target;
        }
    }
    for (i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        tec_channel_state_t *s = &c->channel[i];
        tec_channel_output_t *o = &out->channel[i];
        uint32_t faults = common_faults;
        float error;
        float proportional;
        float candidate_integral;
        float requested;
        float rise_limit;
        if (in == NULL || !in->plate[i].valid ||
            !isfinite(in->plate[i].temperature_c) ||
            (c->configuration_valid &&
             (in->plate[i].temperature_c <= c->config.plate_min_c ||
              in->plate[i].temperature_c >= c->config.plate_max_c))) {
            faults |= TEC_FAULT_PLATE_INVALID;
        }
        if (in != NULL && c->configuration_valid &&
            (uint32_t)(now_ms - in->plate[i].timestamp_ms) > c->config.sample_timeout_ms) {
            faults |= TEC_FAULT_PLATE_STALE;
        }
        if (c->configuration_valid && c->config.current_limit_a[i] <= 0.0f) {
            faults |= TEC_FAULT_CURRENT_UNCOMMISSIONED;
        }
        if (in == NULL || !in->current[i].valid || !isfinite(in->current[i].amperes)) {
            faults |= TEC_FAULT_CURRENT_INVALID;
        }
        if (in == NULL || !in->current[i].calibrated) {
            faults |= TEC_FAULT_CURRENT_UNVERIFIED;
        }
        if (in != NULL && c->configuration_valid) {
            if ((uint32_t)(now_ms - in->current[i].timestamp_ms) >
                c->config.sample_timeout_ms) {
                faults |= TEC_FAULT_CURRENT_STALE;
            }
            if (c->config.current_limit_a[i] > 0.0f && isfinite(in->current[i].amperes) &&
                fabsf(in->current[i].amperes) > c->config.current_limit_a[i]) {
                faults |= TEC_FAULT_CURRENT_OVERLIMIT;
            }
        }
        if (faults != TEC_FAULT_NONE || !s->armed) {
            stop_channel(s, faults);
            copy_channel_output(s, o, faults);
            continue;
        }
        if (c->mode == TEC_MODE_UNREGULATED) {
            rise_limit = s->duty + c->config.rise_per_second * dt;
            if (!isfinite(rise_limit)) {
                faults |= TEC_FAULT_ARITHMETIC;
                stop_channel(s, faults);
                copy_channel_output(s, o, faults);
                continue;
            }
            s->integral = 0.0f;
            s->guard_wait = false;
            s->duty = fminf(1.0f, rise_limit);
            copy_channel_output(s, o, TEC_FAULT_NONE);
            continue;
        }
        /* Guard uses the current unfiltered plate sample. Reaching the normal
         * target inhibits cooling without requiring a manual rearm. Turning
         * cooling off cannot actively rewarm a plate or prevent inertia. */
        if (in->plate[i].temperature_c <= target ||
            (s->guard_wait &&
             in->plate[i].temperature_c < target + c->config.restart_hysteresis_c)) {
            stop_channel(s, TEC_FAULT_NONE);
            s->guard_wait = true;
            copy_channel_output(s, o, TEC_FAULT_NONE);
            o->guard_inhibited = true;
            continue;
        }
        s->guard_wait = false;
        error = in->plate[i].temperature_c - target;
        proportional = c->config.kp * error;
        candidate_integral = s->integral + c->config.ki * error * dt;
        requested = proportional + candidate_integral;
        rise_limit = s->duty + c->config.rise_per_second * dt;
        if (!isfinite(proportional) || !isfinite(candidate_integral) ||
            !isfinite(requested) || !isfinite(rise_limit)) {
            faults |= TEC_FAULT_ARITHMETIC;
            stop_channel(s, faults);
            copy_channel_output(s, o, faults);
            continue;
        }
        /* Conditional integration prevents windup at both the duty ceiling
         * and upward slew limiter. Output reductions have no slew limiter. */
        if (requested <= c->config.max_duty && requested <= rise_limit) {
            s->integral = clamp(candidate_integral, 0.0f, c->config.max_duty);
        }
        requested = clamp(proportional + s->integral, 0.0f, c->config.max_duty);
        s->duty = fminf(requested, rise_limit);
        copy_channel_output(s, o, TEC_FAULT_NONE);
    }
}
