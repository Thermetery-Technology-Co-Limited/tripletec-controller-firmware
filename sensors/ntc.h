#ifndef TEC_NTC_H
#define TEC_NTC_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    float supply_v;          /* explicit AVCC; defaults to nominal 3.3 V */
    float adc_reference_v;   /* internal ADS1220 nominal 2.048 V */
    float pullup_ohm;
    float resistance_25c_ohm;
    float beta_k;
    float minimum_c;
    float maximum_c;
    float rail_margin_v;
    bool calibrated;        /* caller attests the complete transfer calibration */
} ntc_config_t;

typedef struct {
    float temperature_c;
    float resistance_ohm;
    float divider_v;
    bool nominal_only;
} ntc_result_t;

void ntc_config_defaults(ntc_config_t *config);
/* Schematic topology: 100k pullup to AVCC, 100k/B3950 thermistor to GND,
 * AIN3=AVCC/2 from equal fixed divider. Code is signed24, gain1. This is NOT
 * ratiometric when the ADC uses its internal reference: AVCC must be measured
 * and divider/thermistor tolerances characterized before commissioning. */
bool ntc_from_ads1220(int32_t code, const ntc_config_t *config, ntc_result_t *result);

#endif
