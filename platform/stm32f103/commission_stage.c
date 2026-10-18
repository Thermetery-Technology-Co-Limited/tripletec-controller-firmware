#include "commission_stage.h"
#include "board.h"

/* STM32F103 RM0008: ADC regular continuous conversion, analog watchdog, and
 * TIM2 CH2 PWM mode 2 in one-pulse mode. This does not regulate peak current:
 * an ADC watchdog interrupt shuts down a detected excursion, while TIM2
 * independently ends every pulse even if the CPU stops executing.
 */
#ifdef COMMISSION_STAGE_TEST
/* Host tests supply register storage and interrupt masking; production has
 * no indirection in the watchdog shutdown path.
 */
extern volatile uint32_t *commission_stage_test_reg(uintptr_t address);
extern volatile uint8_t *commission_stage_test_reg8(uintptr_t address);
extern uint32_t commission_stage_test_irq_save(void);
extern void commission_stage_test_irq_restore(uint32_t mask);
#define REG32(a) (*commission_stage_test_reg(a))
#define REG8(a) (*commission_stage_test_reg8(a))
#else
#define REG32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define REG8(a) (*(volatile uint8_t *)(uintptr_t)(a))
#endif
#define BIT(n) (UINT32_C(1) << (n))
#define RCC_CFGR REG32(0x40021004u)
#define RCC_APB2ENR REG32(0x40021018u)
#define RCC_APB1ENR REG32(0x4002101cu)
#define AFIO_MAPR REG32(0x40010004u)
#define GPIOA_CRL REG32(0x40010800u)
#define GPIOA_IDR REG32(0x40010808u)
#define GPIOA_BSRR REG32(0x40010810u)
#define ADC1_SR REG32(0x40012400u)
#define ADC1_CR1 REG32(0x40012404u)
#define ADC1_CR2 REG32(0x40012408u)
#define ADC1_SMPR1 REG32(0x4001240cu)
#define ADC1_SMPR2 REG32(0x40012410u)
#define ADC1_HTR REG32(0x40012424u)
#define ADC1_LTR REG32(0x40012428u)
#define ADC1_SQR1 REG32(0x4001242cu)
#define ADC1_SQR2 REG32(0x40012430u)
#define ADC1_SQR3 REG32(0x40012434u)
#define ADC1_DR REG32(0x4001244cu)
#define TIM2_CR1 REG32(0x40000000u)
#define TIM2_CR2 REG32(0x40000004u)
#define TIM2_SMCR REG32(0x40000008u)
#define TIM2_DIER REG32(0x4000000cu)
#define TIM2_SR REG32(0x40000010u)
#define TIM2_EGR REG32(0x40000014u)
#define TIM2_CCMR1 REG32(0x40000018u)
#define TIM2_CCMR2 REG32(0x4000001cu)
#define TIM2_CCER REG32(0x40000020u)
#define TIM2_CNT REG32(0x40000024u)
#define TIM2_PSC REG32(0x40000028u)
#define TIM2_ARR REG32(0x4000002cu)
#define TIM2_CCR2 REG32(0x40000038u)
#define DBGMCU_CR REG32(0xe0042004u)
#define NVIC_ISER0 REG32(0xe000e100u)
#define NVIC_ICER0 REG32(0xe000e180u)
#define NVIC_ICPR0 REG32(0xe000e280u)
#define NVIC_IPR18 REG8(0xe000e412u)
#define OUTPUT_MASK (BIT(1) | BIT(2) | BIT(3))
#define ADC_IRQ BIT(18)
#define ADC_AWD BIT(0)
#define ADC_EOC BIT(1)
#define ADC_MONITOR (BIT(23) | BIT(9) | BIT(6) | BIT(5) | 10u)
#define ADC_RUN (BIT(20) | (7u << 17) | BIT(1) | BIT(0))

typedef struct {
    uint32_t cr1, cr2, smpr1, smpr2, htr, ltr, sqr1, sqr2, sqr3;
    uint32_t gpioa_crl, afio_mapr, debug, apb1, apb2;
    uint8_t irq_priority;
    bool irq_enabled;
} saved_registers_t;

static saved_registers_t saved;
static bool saved_valid;
static volatile bool monitor_active;
static volatile bool watchdog_seen;
static volatile commission_stage_status_t state;
static uint16_t zero_adc, low_adc, high_adc;
static bool drive_pin;
static volatile uint32_t last_sample_ms;
static uint32_t last_pulse_ms, last_pulse_samples;
static uint32_t minimum_period_ms;
static bool pulse_started;

static uint32_t irq_save(void)
{
#ifdef COMMISSION_STAGE_TEST
    return commission_stage_test_irq_save();
#else
    uint32_t mask;
    __asm volatile("mrs %0, primask\n\tcpsid i" : "=r"(mask) :: "memory");
    return mask;
#endif
}

static void irq_restore(uint32_t mask)
{
#ifdef COMMISSION_STAGE_TEST
    commission_stage_test_irq_restore(mask);
#else
    __asm volatile("msr primask, %0" :: "r"(mask) : "memory");
#endif
}

static void adc_start(void)
{
    ADC1_CR2 = ADC_RUN;
    /* ADON was cleared to terminate the previous regular sequence. Wait
     * longer than the ADC power-up stabilization time before SWSTART.
     */
    for (volatile uint32_t delay = 0u; delay < 72u; ++delay) {
        __asm volatile("nop");
    }
    ADC1_CR2 |= BIT(22);
}

/* Also used as the first action on a watchdog trip: remove timer ownership
 * before touching the GPIO mode, and preload all three gate outputs LOW.
 */
static void gates_off(void)
{
    TIM2_CCER = 0u;
    TIM2_CR1 = 0u;
    GPIOA_BSRR = OUTPUT_MASK << 16;
    GPIOA_CRL = (GPIOA_CRL & ~UINT32_C(0x0000fff0)) | UINT32_C(0x00002220);
}

static bool clock_valid(void)
{
    /* HSE 12 MHz x6; AHB /1, APB2 /1, APB1 /2, ADC /6. A timer on
     * divided APB1 receives 72 MHz, so PSC=71 gives exactly 1 MHz nominal.
     */
    return board_core_clock_hz() == 72000000u &&
           (RCC_CFGR & UINT32_C(0x003ffffc)) == UINT32_C(0x00118408);
}

void ADC1_2_IRQHandler(void)
{
    const uint32_t flags = ADC1_SR;
    if (!monitor_active) {
        ADC1_SR = 0u;
        return;
    }
    const bool watchdog = (flags & ADC_AWD) != 0u;
    if (watchdog) {
        gates_off();
        watchdog_seen = true;
    }
    const uint32_t sample = ADC1_DR & UINT32_C(0x0fff);
    const bool invalid = sample == 0u || sample == 4095u ||
                         sample <= low_adc || sample >= high_adc;
    if (invalid && !watchdog) {
        gates_off();
    }
    if ((watchdog || invalid) && !state.tripped) {
        state.tripped = true;
        state.trip_adc = sample;
        state.trip_timer_us = TIM2_CNT;
        /* Keep EOC sampling to observe return to zero without an AWD storm. */
        ADC1_CR1 &= ~BIT(6);
    }
    if ((flags & ADC_EOC) != 0u) {
        state.last_adc = sample;
        if (sample > state.peak_adc) {
            state.peak_adc = sample;
        }
        ++state.samples;
        last_sample_ms = board_millis();
    }
    /* DR read clears EOC; writing zero clears only AWD. Avoid clearing an
     * EOC that could have arrived after the DR read.
     */
    ADC1_SR = ~ADC_AWD;
}

void commission_stage_snapshot(commission_stage_status_t *status)
{
    if (status == NULL) {
        return;
    }
    const uint32_t mask = irq_save();
    *status = state;
    status->pin_low = (GPIOA_IDR & OUTPUT_MASK) == 0u;
    status->timer_running = (TIM2_CR1 & BIT(0)) != 0u;
    irq_restore(mask);
}

void commission_stage_stop(void)
{
    const uint32_t mask = irq_save();
    /* GPIO/TIM2 clocks already run after board_init, but make stop useful
     * before any successful prepare as well. Output latches stay LOW.
     */
    RCC_APB2ENR |= BIT(2);
    RCC_APB1ENR |= BIT(0);
    gates_off();
    state.prepared = false;
    monitor_active = false;
    if (saved_valid) {
        NVIC_ICER0 = ADC_IRQ;
        ADC1_CR1 = 0u;
        ADC1_CR2 = 0u;
        (void)ADC1_DR;
        ADC1_SR = 0u;
        NVIC_ICPR0 = ADC_IRQ;
        ADC1_SMPR1 = saved.smpr1;
        ADC1_SMPR2 = saved.smpr2;
        ADC1_HTR = saved.htr;
        ADC1_LTR = saved.ltr;
        ADC1_SQR1 = saved.sqr1;
        ADC1_SQR2 = saved.sqr2;
        ADC1_SQR3 = saved.sqr3;
        ADC1_CR1 = saved.cr1;
        /* Do not replay an old software conversion start. */
        ADC1_CR2 = saved.cr2 & ~BIT(22);
        GPIOA_CRL = (saved.gpioa_crl & ~UINT32_C(0x0000fff0)) |
                    UINT32_C(0x00002220);
        /* SWJ_CFG is write-only/undefined on read: explicitly retain SWD. */
        AFIO_MAPR = (saved.afio_mapr & ~UINT32_C(0x07000000)) | BIT(25);
        DBGMCU_CR = saved.debug;
        NVIC_IPR18 = saved.irq_priority;
        if (saved.irq_enabled) {
            NVIC_ISER0 = ADC_IRQ;
        }
        /* Keep the GPIO and TIM2 clocks available for subsequent safe-off. */
        RCC_APB1ENR = saved.apb1 | BIT(0);
        RCC_APB2ENR = saved.apb2 | BIT(2);
        saved_valid = false;
    }
    irq_restore(mask);
}

bool commission_stage_prepare(uint16_t baseline, uint16_t delta, bool drive)
{
    if (state.prepared || saved_valid || !clock_valid() || delta == 0u ||
        baseline <= delta || (uint32_t)baseline + delta >= 4095u) {
        return false;
    }
    const uint32_t mask = irq_save();
    /* The self-test must be allowed to execute the real interrupt handler. */
    if (mask != 0u || (ADC1_CR2 & BIT(8)) != 0u ||
        (ADC1_CR1 & (UINT32_C(0x000f0000) | BIT(5) | BIT(6) | BIT(7))) != 0u) {
        irq_restore(mask);
        return false;
    }
    saved = (saved_registers_t){
        ADC1_CR1, ADC1_CR2, ADC1_SMPR1, ADC1_SMPR2, ADC1_HTR, ADC1_LTR,
        ADC1_SQR1, ADC1_SQR2, ADC1_SQR3, GPIOA_CRL, AFIO_MAPR, DBGMCU_CR,
        RCC_APB1ENR, RCC_APB2ENR, NVIC_IPR18, (NVIC_ISER0 & ADC_IRQ) != 0u
    };
    saved_valid = true;
    RCC_APB1ENR |= BIT(0);
    RCC_APB2ENR |= BIT(0) | BIT(2) | BIT(9);
    gates_off();
    state = (commission_stage_status_t){0};
    watchdog_seen = false;
    zero_adc = baseline;
    low_adc = (uint16_t)(baseline - delta);
    high_adc = (uint16_t)(baseline + delta);
    drive_pin = drive;
    pulse_started = false;
    minimum_period_ms = 20u;
    last_pulse_samples = 0u;
    DBGMCU_CR &= ~BIT(11); /* TIM2 must finish even when the CPU is halted. */
    AFIO_MAPR = (saved.afio_mapr & ~UINT32_C(0x07000300)) | BIT(25);
    TIM2_CR2 = 0u;
    TIM2_SMCR = 0u;
    TIM2_DIER = 0u;
    TIM2_CCMR1 = 7u << 12; /* CH2 positive PWM2: LOW while CNT < CCR2. */
    TIM2_CCMR2 = 0u;
    TIM2_PSC = 71u;
    TIM2_ARR = 1000u;
    TIM2_CCR2 = 1u;
    TIM2_EGR = BIT(0);
    TIM2_CNT = 0u;
    TIM2_SR = 0u;
    ADC1_CR1 = 0u;
    ADC1_CR2 = 0u;
    (void)ADC1_DR;
    ADC1_SR = 0u;
    ADC1_SMPR1 = (ADC1_SMPR1 & ~7u) | 7u; /* CH10: 239.5 cycles. */
    ADC1_SQR1 = 0u;
    ADC1_SQR2 = 0u;
    ADC1_SQR3 = 10u;
    ADC1_LTR = 0u;
    ADC1_HTR = 1u; /* Deliberately trip on the zero-current midrail reading. */
    monitor_active = true;
    NVIC_ICER0 = ADC_IRQ;
    NVIC_ICPR0 = ADC_IRQ;
    NVIC_IPR18 = 0u; /* Highest configurable interrupt priority. */
    ADC1_CR1 = ADC_MONITOR;
    NVIC_ISER0 = ADC_IRQ;
    adc_start();
    irq_restore(mask);

    const uint32_t start = board_millis();
    uint32_t polls = 200000u;
    while (!state.tripped && polls-- != 0u &&
           (uint32_t)(board_millis() - start) < 5u) {
        __asm volatile("nop");
    }
    const uint32_t finish_mask = irq_save();
    const bool passed = watchdog_seen && state.tripped && state.samples != 0u &&
                        (GPIOA_IDR & OUTPUT_MASK) == 0u &&
                        (TIM2_CR1 & BIT(0)) == 0u &&
                        state.trip_adc > 1u && state.trip_adc < 4095u;
    ADC1_CR1 = 0u;
    ADC1_CR2 = 0u;
    ADC1_SR = 0u;
    NVIC_ICPR0 = ADC_IRQ;
    if (!passed) {
        irq_restore(finish_mask);
        commission_stage_stop();
        return false;
    }
    state = (commission_stage_status_t){.prepared = true, .selftest_passed = true};
    last_sample_ms = board_millis();
    ADC1_HTR = high_adc;
    ADC1_LTR = low_adc;
    ADC1_CR1 = ADC_MONITOR;
    adc_start();
    irq_restore(finish_mask);
    return true;
}

bool commission_stage_set_period(uint32_t period_ms)
{
    const uint32_t mask = irq_save();
    const bool valid = mask == 0u && (period_ms == 5u || period_ms == 20u) &&
                       state.prepared && state.selftest_passed && !state.tripped &&
                       !pulse_started && (GPIOA_IDR & OUTPUT_MASK) == 0u &&
                       (TIM2_CR1 & BIT(0)) == 0u;
    if (valid) {
        minimum_period_ms = period_ms;
    }
    irq_restore(mask);
    return valid;
}

bool commission_stage_pulse(void)
{
    const uint32_t mask = irq_save();
    const uint32_t now = board_millis();
    const uint32_t sample = state.last_adc;
    const uint32_t distance = sample > zero_adc ? sample - zero_adc : zero_adc - sample;
    if (mask != 0u || !state.prepared || state.tripped || !state.selftest_passed ||
        !clock_valid() || state.samples < 3u || state.samples == last_pulse_samples ||
        (uint32_t)(now - last_sample_ms) > 2u || distance > 49u ||
        (ADC1_SR & ADC_AWD) != 0u || (TIM2_CR1 & BIT(0)) != 0u ||
        (GPIOA_IDR & OUTPUT_MASK) != 0u ||
        (pulse_started && (uint32_t)(now - last_pulse_ms) < minimum_period_ms)) {
        irq_restore(mask);
        return false;
    }
    TIM2_CCER = 0u;
    TIM2_CR1 = BIT(3); /* One pulse mode; CEN is set only at the final write. */
    TIM2_CNT = 0u;
    TIM2_SR = 0u;
    GPIOA_BSRR = OUTPUT_MASK << 16;
    GPIOA_CRL = (GPIOA_CRL & ~UINT32_C(0x0000fff0)) |
                (drive_pin ? UINT32_C(0x000022a0) : UINT32_C(0x00002220));
    TIM2_CCER = BIT(4); /* Only CH2; CH3/CH4 remain disabled. */
    if ((ADC1_SR & ADC_AWD) != 0u) {
        gates_off();
        irq_restore(mask);
        return false;
    }
    last_pulse_samples = state.samples;
    last_pulse_ms = now;
    pulse_started = true;
    TIM2_CR1 = BIT(3) | BIT(0);
    irq_restore(mask);
    return true;
}
