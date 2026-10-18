#ifndef TRIPLETEC_COMMISSION_STAGE_H
#define TRIPLETEC_COMMISSION_STAGE_H

#include <stdbool.h>
#include <stdint.h>

/* A bounded TEC1 commissioning path, separate from normal channel control.
 * The caller must suspend board ADC reads until stop, and supervise the
 * temperature, command lease and overall test duration. This layer enforces
 * the selected pulse width and minimum period for its three fixed profiles.
 */
typedef struct {
    bool prepared;
    bool tripped;
    bool pin_low;
    bool timer_running;
    bool selftest_passed;
    uint32_t samples;
    uint32_t peak_adc;
    uint32_t last_adc;
    uint32_t trip_adc;
    uint32_t trip_timer_us;
} commission_stage_status_t;

/* Samples at or beyond baseline +/- delta trip. drive=false keeps PA1 GPIO LOW even while
 * TIM2 runs. Preparation only samples with all three gate pins LOW, including
 * its deliberately forced analog-watchdog interrupt self-test.
 */
bool commission_stage_prepare(uint16_t baseline, uint16_t delta, bool drive);
/* Whitelisted (on_us, period_ms): (1000,20), (1000,5), (5000,12).
 * Only change while prepared, stopped and before the first pulse. Preparation
 * always resets the profile to (1000,20); no profile persists across tests.
 */
bool commission_stage_set_profile(uint32_t on_us, uint32_t period_ms);
/* Preparation defaults to 20 ms. Only 5/20 ms can be selected, while stopped
 * and before the first pulse; the period cannot change during a test.
 */
bool commission_stage_set_period(uint32_t period_ms);
bool commission_stage_pulse(void);
void commission_stage_stop(void);
void commission_stage_snapshot(commission_stage_status_t *status);
void ADC1_2_IRQHandler(void);

#endif
