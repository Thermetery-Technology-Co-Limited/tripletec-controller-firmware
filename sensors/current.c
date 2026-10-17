#include "current.h"

#include <math.h>

/* Allegro ACS725 selection guide, zero output, sensitivity ratiometry:
 * https://www.allegromicro.com/-/media/files/datasheets/acs725-datasheet.pdf
 * Sens(VCC)=Sens(3.3V)*(1+(VCC-3.3V)*SENS_RAT_COEF/3.3V).
 * Typical coefficient is 1.3, zero-output ratiometry coefficient is 1. */
bool current_config_defaults(size_t channel, current_config_t *c)
{
    if (c == NULL || channel >= 3U) {
        return false;
    }
    *c = (current_config_t) {
        .supply_v = 3.3f,
        .zero_fraction = channel == 0U ? 0.5f : 0.1f,
        .sensitivity_at_3v3_v_per_a = channel == 0U ? 0.0264f : 0.264f,
        .sensitivity_ratiometry_coefficient = 1.3f,
        .minimum_a = channel == 0U ? -50.0f : 0.0f,
        .maximum_a = channel == 0U ? 50.0f : 10.0f,
        .calibrated = false
    };
    return true;
}

bool current_from_voltage(float voltage, const current_config_t *c, current_result_t *out)
{
    float sensitivity;
    float amps;
    if (out == NULL) {
        return false;
    }
    *out = (current_result_t) {NAN, false, true};
    if (c == NULL || !isfinite(c->supply_v) || c->supply_v < 3.0f || c->supply_v > 3.6f ||
        !isfinite(c->zero_fraction) || c->zero_fraction <= 0.0f || c->zero_fraction >= 1.0f ||
        !isfinite(c->sensitivity_at_3v3_v_per_a) || c->sensitivity_at_3v3_v_per_a <= 0.0f ||
        !isfinite(c->sensitivity_ratiometry_coefficient) ||
        c->sensitivity_ratiometry_coefficient <= 0.0f ||
        !isfinite(c->minimum_a) || !isfinite(c->maximum_a) || c->minimum_a >= c->maximum_a ||
        !isfinite(voltage) || voltage <= 0.0f || voltage >= c->supply_v) {
        return false;
    }
    sensitivity = c->sensitivity_at_3v3_v_per_a *
        (1.0f + (c->supply_v - 3.3f) * c->sensitivity_ratiometry_coefficient / 3.3f);
    amps = (voltage - c->zero_fraction * c->supply_v) / sensitivity;
    if (!isfinite(sensitivity) || sensitivity <= 0.0f || !isfinite(amps)) {
        return false;
    }
    out->amperes = amps;
    out->within_nominal_range = amps >= c->minimum_a && amps <= c->maximum_a;
    out->nominal_only = !c->calibrated;
    return true;
}

bool current_from_adc12(uint16_t code, float adc_reference_v,
                        const current_config_t *c, current_result_t *out)
{
    if (out == NULL) {
        return false;
    }
    *out = (current_result_t) {NAN, false, true};
    if (code > 4095U || !isfinite(adc_reference_v) || adc_reference_v <= 0.0f) {
        return false;
    }
    return current_from_voltage((float)code * adc_reference_v / 4095.0f, c, out);
}
