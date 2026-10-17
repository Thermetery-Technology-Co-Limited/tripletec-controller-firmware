#ifndef TEC_CURRENT_H
#define TEC_CURRENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    float supply_v;
    float zero_fraction;
    float sensitivity_at_3v3_v_per_a;
    float sensitivity_ratiometry_coefficient;
    float minimum_a;
    float maximum_a;
    bool calibrated;
} current_config_t;

typedef struct {
    float amperes;
    bool within_nominal_range;
    bool nominal_only;
} current_result_t;

/* ch0 ACS725-50AB (+/-50 A), ch1/ch2 ACS725-10AU (0..10 A).
 * Defaults are nominal telemetry conversion, NOT commissioned current limits. */
bool current_config_defaults(size_t channel, current_config_t *config);
bool current_from_voltage(float voltage_v, const current_config_t *config,
                          current_result_t *result);
bool current_from_adc12(uint16_t code, float adc_reference_v,
                        const current_config_t *config, current_result_t *result);

#endif
