/* Host register model exercises the stage's decisions and shutdown ordering.
 * It is not a substitute for measuring real ADC response or gate timing.
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#define COMMISSION_STAGE_TEST
#include "../platform/stm32f103/commission_stage.c"

typedef struct { uintptr_t address; uint32_t value; } test_register_t;
static test_register_t registers[64];
static size_t register_count;
static uint8_t irq_priority;
static uint32_t test_mask, test_ms, test_clock;
static uint16_t test_adc;
static bool in_adc, suppress_adc;

volatile uint32_t *commission_stage_test_reg(uintptr_t address)
{
    for (size_t i = 0u; i < register_count; ++i) {
        if (registers[i].address == address) {
            return &registers[i].value;
        }
    }
    assert(register_count < sizeof(registers) / sizeof(registers[0]));
    registers[register_count].address = address;
    registers[register_count].value = 0u;
    return &registers[register_count++].value;
}

volatile uint8_t *commission_stage_test_reg8(uintptr_t address)
{
    assert(address == 0xe000e412u);
    return &irq_priority;
}

static void conversion(void)
{
    if (test_mask == 0u && monitor_active && !in_adc && !suppress_adc) {
        in_adc = true;
        ADC1_DR = test_adc;
        ADC1_SR = ADC_EOC | ((test_adc < ADC1_LTR || test_adc > ADC1_HTR) ? ADC_AWD : 0u);
        ADC1_2_IRQHandler();
        /* Model SR clear semantics; the C assignment writes ones to preserve
         * flags, which hardware never treats as setting previously clear bits.
         */
        ADC1_SR = 0u;
        in_adc = false;
    }
}

uint32_t commission_stage_test_irq_save(void)
{
    const uint32_t old = test_mask;
    test_mask = 1u;
    return old;
}

void commission_stage_test_irq_restore(uint32_t mask)
{
    test_mask = mask;
    conversion();
}

uint32_t board_millis(void)
{
    conversion();
    return test_ms;
}

uint32_t board_core_clock_hz(void)
{
    return test_clock;
}

static void reset(void)
{
    memset(registers, 0, sizeof(registers));
    register_count = 0u;
    irq_priority = 0x80u;
    test_mask = test_ms = 0u;
    test_clock = 72000000u;
    test_adc = 2040u;
    in_adc = suppress_adc = false;
    saved_valid = monitor_active = false;
    state = (commission_stage_status_t){0};
    ADC1_CR2 = BIT(0) | BIT(20) | (7u << 17) | BIT(23);
    ADC1_SMPR1 = 0x00e001ffu;
    ADC1_SQR3 = 17u;
    GPIOA_CRL = 0x44442224u;
    RCC_CFGR = 0x0011840au;
    RCC_APB2ENR = BIT(2) | BIT(9);
    RCC_APB1ENR = BIT(0);
    DBGMCU_CR = BIT(11) | BIT(8);
}

static void prepare(bool drive)
{
    assert(commission_stage_prepare(2040u, 491u, drive));
    assert(state.prepared && state.selftest_passed && !state.tripped);
    assert(GPIOA_CRL == 0x44442224u);
    assert(TIM2_CCER == 0u && TIM2_CR1 == 0u);
    assert(ADC1_HTR == 2531u && ADC1_LTR == 1549u);
    assert((ADC1_CR1 & (BIT(23) | BIT(9) | BIT(6) | BIT(5))) ==
           (BIT(23) | BIT(9) | BIT(6) | BIT(5)));
    assert(irq_priority == 0u);
    assert((DBGMCU_CR & BIT(11)) == 0u);
    conversion();
    conversion();
}

static void test_real_and_dry_pulse(void)
{
    for (unsigned dry = 0u; dry < 2u; ++dry) {
        reset();
        prepare(dry == 0u);
        assert(commission_stage_pulse());
        assert(TIM2_CR1 == 9u && TIM2_CCER == BIT(4));
        assert(TIM2_PSC == 71u && TIM2_ARR == 1000u && TIM2_CCR2 == 1u);
        assert(TIM2_CCMR1 == (7u << 12));
        assert(((GPIOA_CRL >> 4) & 15u) == (dry ? 2u : 10u));
        assert(((GPIOA_CRL >> 8) & 255u) == 0x22u);
        assert(!commission_stage_pulse()); /* Running timer. */
        TIM2_CR1 &= ~BIT(0); /* Model one-pulse completion. */
        TIM2_CNT = 0u;
        test_ms = 19u;
        conversion();
        assert(!commission_stage_pulse());
        test_ms = 20u;
        conversion();
        assert(commission_stage_pulse());
        commission_stage_stop();
        assert(!state.prepared);
        assert(TIM2_CCER == 0u && TIM2_CR1 == 0u);
        assert(GPIOA_CRL == 0x44442224u);
        assert(ADC1_SQR3 == 17u && ADC1_SMPR1 == 0x00e001ffu);
        assert(irq_priority == 0x80u);
        assert(DBGMCU_CR == (BIT(11) | BIT(8)));
    }
}

static void test_thresholds_and_trip(void)
{
    reset();
    prepare(true);
    test_adc = 1550u;
    conversion();
    assert(!state.tripped);
    test_adc = 2530u;
    conversion();
    assert(!state.tripped); /* Samples strictly inside the limits pass. */
    test_adc = 2040u;
    conversion();
    assert(commission_stage_pulse());
    TIM2_CNT = 237u;
    GPIOA_IDR = BIT(1); /* The model leaves IDR independent of GPIO writes. */
    test_adc = 2531u;
    conversion();
    assert(state.tripped && state.trip_adc == 2531u);
    assert(state.trip_timer_us == 237u);
    assert(TIM2_CCER == 0u && TIM2_CR1 == 0u);
    assert((GPIOA_CRL & 0xfff0u) == 0x2220u);
    assert(GPIOA_BSRR == (OUTPUT_MASK << 16));
    test_adc = 2040u;
    conversion();
    assert(state.last_adc == 2040u && state.tripped);
    assert(!commission_stage_pulse());
    commission_stage_stop();
    assert(state.tripped && state.trip_adc == 2531u);

    reset();
    prepare(false);
    test_adc = 1549u;
    conversion();
    assert(state.tripped && state.trip_adc == 1549u);
}

static void test_interlocks(void)
{
    reset();
    test_clock = 8000000u;
    assert(!commission_stage_prepare(2040u, 491u, true));
    reset();
    assert(!commission_stage_prepare(2040u, 0u, true));
    assert(!commission_stage_prepare(2040u, 2040u, true));
    test_mask = 1u;
    assert(!commission_stage_prepare(2040u, 491u, true));
    assert(test_mask == 1u);

    reset();
    prepare(true);
    suppress_adc = true;
    test_ms = 3u;
    assert(!commission_stage_pulse()); /* Stale ADC. */
    suppress_adc = false;
    test_adc = 2090u;
    conversion();
    assert(!commission_stage_pulse()); /* Above off-state baseline tolerance. */
    test_adc = 2040u;
    conversion();
    RCC_CFGR ^= BIT(14);
    assert(!commission_stage_pulse());
    commission_stage_stop();

    reset();
    suppress_adc = true;
    assert(!commission_stage_prepare(2040u, 491u, false));
    assert(!state.prepared && !state.selftest_passed);
    assert(TIM2_CCER == 0u && TIM2_CR1 == 0u);
    assert((GPIOA_CRL & 0xfff0u) == 0x2220u);
}

static void test_period_selection(void)
{
    reset();
    assert(!commission_stage_set_period(5u));
    prepare(true);
    assert(minimum_period_ms == 20u);
    const uint32_t bad_periods[] = {0u, 1u, 4u, 6u, 19u, 21u, 1000u};
    for (size_t i = 0u; i < sizeof(bad_periods) / sizeof(bad_periods[0]); ++i) {
        assert(!commission_stage_set_period(bad_periods[i]));
    }
    assert(commission_stage_set_period(20u));
    GPIOA_IDR = BIT(1);
    assert(!commission_stage_set_period(5u));
    GPIOA_IDR = 0u;
    TIM2_CR1 = 1u;
    assert(!commission_stage_set_period(5u));
    TIM2_CR1 = 0u;
    state.tripped = true;
    assert(!commission_stage_set_period(5u));
    state.tripped = false;
    assert(commission_stage_set_period(5u));
    assert(commission_stage_pulse());
    assert(TIM2_PSC == 71u && TIM2_ARR == 1000u && TIM2_CCR2 == 1u);
    TIM2_CR1 &= ~BIT(0);
    assert(!commission_stage_set_period(20u)); /* Fixed after first pulse. */
    test_ms = 4u; conversion();
    assert(!commission_stage_pulse());
    test_ms = 5u; conversion();
    assert(commission_stage_pulse());
    commission_stage_stop();
    prepare(true);
    assert(minimum_period_ms == 20u); /* No profile persists across prepare. */
    assert(commission_stage_pulse());
    TIM2_CR1 &= ~BIT(0);
    test_ms = 10u; conversion();
    assert(!commission_stage_pulse());
    test_ms = 25u; conversion();
    assert(commission_stage_pulse());
    commission_stage_stop();
}

static void test_profile_selection(void)
{
    reset();
    assert(!commission_stage_set_profile(5000u, 12u));
    prepare(false);
    const uint32_t invalid[][2] = {
        {0u, 12u}, {1000u, 0u}, {1000u, 12u}, {5000u, 5u}, {5000u, 20u},
        {12000u, 12u}, {10000u, 20u}, {UINT32_MAX, UINT32_MAX}
    };
    for (size_t i = 0u; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        assert(!commission_stage_set_profile(invalid[i][0], invalid[i][1]));
        assert(TIM2_ARR == 1000u && minimum_period_ms == 20u);
    }
    assert(commission_stage_set_profile(1000u, 5u));
    assert(commission_stage_set_profile(1000u, 20u));
    GPIOA_IDR = BIT(1);
    assert(!commission_stage_set_profile(5000u, 12u));
    GPIOA_IDR = 0u;
    state.tripped = true;
    assert(!commission_stage_set_profile(5000u, 12u));
    state.tripped = false;
    TIM2_CR1 = 1u;
    assert(!commission_stage_set_profile(5000u, 12u));
    TIM2_CR1 = 0u;
    assert(commission_stage_set_profile(5000u, 12u));
    assert(TIM2_ARR == 5000u && TIM2_CCR2 == 1u && TIM2_PSC == 71u);
    assert(minimum_period_ms == 12u && pulse_on_us == 5000u);
    assert(commission_stage_pulse());
    assert(TIM2_CR1 == 9u && TIM2_ARR == 5000u);
    assert((GPIOA_CRL & 0xfff0u) == 0x2220u); /* GPIO-LOW dry run. */
    assert(!commission_stage_set_profile(1000u, 20u));
    TIM2_CR1 &= ~BIT(0); /* Model independent one-pulse timer completion. */
    TIM2_CNT = 0u;
    test_ms = 11u; conversion();
    assert(!commission_stage_pulse());
    test_ms = 12u; conversion();
    assert(commission_stage_pulse());
    TIM2_CNT = 2900u;
    test_adc = 2531u; conversion();
    assert(state.tripped && TIM2_CR1 == 0u && TIM2_CCER == 0u);
    assert(state.trip_timer_us == 2900u);
    commission_stage_stop();
    test_adc = 2040u;
    prepare(true);
    assert(TIM2_ARR == 1000u && pulse_on_us == 1000u && minimum_period_ms == 20u);
    commission_stage_stop();
}

int main(void)
{
    test_real_and_dry_pulse();
    test_thresholds_and_trip();
    test_interlocks();
    test_period_selection();
    test_profile_selection();
    puts("commission stage register-model tests passed");
    return 0;
}
