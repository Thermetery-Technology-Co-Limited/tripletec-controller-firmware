#include "ntc.h"

#include <math.h>
#include <stddef.h>

void ntc_config_defaults(ntc_config_t *c)
{
    if (c == NULL) {
        return;
    }
    *c = (ntc_config_t) {
        .supply_v = 3.3f,
        .adc_reference_v = 2.048f,
        .pullup_ohm = 100000.0f,
        .resistance_25c_ohm = 100000.0f,
        .beta_k = 3950.0f,
        .minimum_c = -10.0f,
        .maximum_c = 110.0f,
        .rail_margin_v = 0.02f,
        .calibrated = false
    };
}

bool ntc_from_ads1220(int32_t code, const ntc_config_t *c, ntc_result_t *out)
{
    float voltage;
    float resistance;
    float reciprocal_k;
    float temperature;
    if (out == NULL) {
        return false;
    }
    *out = (ntc_result_t) {NAN, NAN, NAN, true};
    if (c == NULL || !isfinite(c->supply_v) || c->supply_v <= 0.0f ||
        !isfinite(c->adc_reference_v) || c->adc_reference_v <= 0.0f ||
        !isfinite(c->pullup_ohm) || c->pullup_ohm <= 0.0f ||
        !isfinite(c->resistance_25c_ohm) || c->resistance_25c_ohm <= 0.0f ||
        !isfinite(c->beta_k) || c->beta_k <= 0.0f ||
        !isfinite(c->minimum_c) || c->minimum_c <= -273.15f ||
        !isfinite(c->maximum_c) || c->minimum_c >= c->maximum_c ||
        !isfinite(c->rail_margin_v) || c->rail_margin_v < 0.0f ||
        c->rail_margin_v >= 0.5f * c->supply_v ||
        code <= -8388608 || code >= 8388607) {
        return false;
    }
    /* The lower thermistor is measured differentially against AIN3 midrail. */
    voltage = (float)code * (c->adc_reference_v / 8388608.0f) + 0.5f * c->supply_v;
    if (!isfinite(voltage) || voltage <= c->rail_margin_v ||
        voltage >= c->supply_v - c->rail_margin_v) {
        return false;
    }
    resistance = c->pullup_ohm * voltage / (c->supply_v - voltage);
    reciprocal_k = 1.0f / 298.15f + logf(resistance / c->resistance_25c_ohm) / c->beta_k;
    if (!isfinite(resistance) || resistance <= 0.0f ||
        !isfinite(reciprocal_k) || reciprocal_k <= 0.0f) {
        return false;
    }
    temperature = 1.0f / reciprocal_k - 273.15f;
    if (!isfinite(temperature) || temperature < c->minimum_c || temperature > c->maximum_c) {
        return false;
    }
    out->temperature_c = temperature;
    out->resistance_ohm = resistance;
    out->divider_v = voltage;
    out->nominal_only = !c->calibrated;
    return true;
}
