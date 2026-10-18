#include "board.h"

/* STM32F103 register layout and I2C receive sequences: ST RM0008, rev. 21.
 * ADS1220 SPI mode 1: TI SBAS501C, section 8.5.1.
 * No vendor startup, HAL, dynamic allocation, or external oscillator required.
 */
#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define BIT(n) (UINT32_C(1) << (n))
#define RCC_CR       REG32(0x40021000u)
#define RCC_CFGR     REG32(0x40021004u)
#define RCC_APB2RSTR REG32(0x4002100cu)
#define RCC_APB1RSTR REG32(0x40021010u)
#define RCC_APB2ENR  REG32(0x40021018u)
#define RCC_APB1ENR  REG32(0x4002101cu)
#define RCC_CSR      REG32(0x40021024u)
#define FLASH_ACR    REG32(0x40022000u)
#define GPIOA_CRL    REG32(0x40010800u)
#define GPIOA_CRH    REG32(0x40010804u)
#define GPIOA_BSRR   REG32(0x40010810u)
#define GPIOB_CRL    REG32(0x40010c00u)
#define GPIOB_IDR    REG32(0x40010c08u)
#define GPIOB_BSRR   REG32(0x40010c10u)
#define GPIOC_CRL    REG32(0x40011000u)
#define GPIOC_IDR    REG32(0x40011008u)
#define GPIOC_BSRR   REG32(0x40011010u)
#define TIM2_CR1     REG32(0x40000000u)
#define TIM2_CCER    REG32(0x40000020u)
#define USART1_SR    REG32(0x40013800u)
#define USART1_DR    REG32(0x40013804u)
#define USART1_BRR   REG32(0x40013808u)
#define USART1_CR1   REG32(0x4001380cu)
#define USART1_CR2   REG32(0x40013810u)
#define USART1_CR3   REG32(0x40013814u)
#define I2C1_CR1     REG32(0x40005400u)
#define I2C1_CR2     REG32(0x40005404u)
#define I2C1_OAR1    REG32(0x40005408u)
#define I2C1_DR      REG32(0x40005410u)
#define I2C1_SR1     REG32(0x40005414u)
#define I2C1_SR2     REG32(0x40005418u)
#define I2C1_CCR     REG32(0x4000541cu)
#define I2C1_TRISE   REG32(0x40005420u)
#define SPI1_CR1     REG32(0x40013000u)
#define SPI1_CR2     REG32(0x40013004u)
#define SPI1_SR      REG32(0x40013008u)
#define SPI1_DR8     (*(volatile uint8_t *)(uintptr_t)0x4001300cu)
#define ADC1_SR      REG32(0x40012400u)
#define ADC1_CR1     REG32(0x40012404u)
#define ADC1_CR2     REG32(0x40012408u)
#define ADC1_SMPR1   REG32(0x4001240cu)
#define ADC1_SQR1    REG32(0x4001242cu)
#define ADC1_SQR2    REG32(0x40012430u)
#define ADC1_SQR3    REG32(0x40012434u)
#define ADC1_DR      REG32(0x4001244cu)
#define IWDG_KR      REG32(0x40003000u)
#define IWDG_PR      REG32(0x40003004u)
#define IWDG_RLR     REG32(0x40003008u)
#define IWDG_SR      REG32(0x4000300cu)
#define SYST_CSR     REG32(0xe000e010u)
#define SYST_RVR     REG32(0xe000e014u)
#define SYST_CVR     REG32(0xe000e018u)
#define SCB_VTOR     REG32(0xe000ed08u)

#define I2C_PE BIT(0)
#define I2C_START BIT(8)
#define I2C_STOP BIT(9)
#define I2C_ACK BIT(10)
#define I2C_POS BIT(11)
#define I2C_SB BIT(0)
#define I2C_ADDR BIT(1)
#define I2C_BTF BIT(2)
#define I2C_RXNE BIT(6)
#define I2C_TXE BIT(7)
#define I2C_ERRORS (BIT(8) | BIT(9) | BIT(10) | BIT(11) | BIT(14))

static volatile uint32_t milliseconds;
static bool adc_ready;

/* The iteration limit also terminates waits if SysTick is unavailable.
 * Normal peripheral waits allow interrupts and use elapsed milliseconds,
 * including rollover. The final I2C receive byte has a separate short bounded
 * poll while interrupts are masked to satisfy the ES0340 silicon workaround.
 */
typedef struct {
    uint32_t started;
    uint32_t timeout;
    uint32_t remaining_polls;
} deadline_t;

static deadline_t deadline_begin(uint32_t timeout_ms)
{
    deadline_t deadline;
    if (timeout_ms > 1000u) {
        timeout_ms = 1000u;
    }
    deadline.started = board_millis();
    deadline.timeout = timeout_ms;
    deadline.remaining_polls = (timeout_ms + 1u) * 8000u;
    return deadline;
}

static bool deadline_expired(deadline_t *deadline)
{
    if (deadline->remaining_polls == 0u) {
        return true;
    }
    --deadline->remaining_polls;
    return (uint32_t)(board_millis() - deadline->started) >= deadline->timeout;
}

static bool wait_bits(volatile uint32_t *reg, uint32_t mask, bool set,
                      deadline_t *deadline)
{
    while (((*reg & mask) != 0u) != set) {
        if (deadline_expired(deadline)) {
            return false;
        }
    }
    return true;
}

static uint32_t irq_save(void)
{
    uint32_t saved;
    __asm volatile("mrs %0, primask\n\tcpsid i" : "=r"(saved) :: "memory");
    return saved;
}

static void irq_restore(uint32_t saved)
{
    __asm volatile("msr primask, %0" :: "r"(saved) : "memory");
}

void board_early_safe_init(void)
{
    RCC_APB2ENR |= BIT(2); /* GPIOA clock. */
    RCC_APB1ENR |= BIT(0); /* TIM2 clock only to explicitly disable outputs. */
    (void)RCC_APB2ENR;
    (void)RCC_APB1ENR;
    GPIOA_BSRR = (BIT(1) | BIT(2) | BIT(3)) << 16;
    TIM2_CCER = 0u;
    TIM2_CR1 = 0u;
    /* Load LOW before changing PA1/2/3 to 2 MHz push-pull GPIO. */
    GPIOA_CRL = (GPIOA_CRL & ~UINT32_C(0x0000fff0)) | UINT32_C(0x00002220);
}

void board_outputs_force_off(void)
{
    board_early_safe_init();
}

static void i2c_configure(void)
{
    I2C1_CR1 = 0u;
    I2C1_CR2 = 8u;      /* APB1 in MHz. */
    I2C1_OAR1 = BIT(14); /* RM0008 requires bit 14 to remain set. */
    I2C1_CCR = 40u;     /* 8 MHz / (2 * 100 kHz). */
    I2C1_TRISE = 9u;    /* Standard mode: APB1 MHz + 1. */
    I2C1_CR1 = I2C_PE;
}

static void i2c_abort(void)
{
    if ((I2C1_SR2 & BIT(0)) != 0u) {
        I2C1_CR1 |= I2C_STOP;
    }
    /* Reset the peripheral rather than wait for a stuck external bus. */
    RCC_APB1RSTR |= BIT(21);
    RCC_APB1RSTR &= ~BIT(21);
    i2c_configure();
}

void board_init(void)
{
    deadline_t deadline;
    board_early_safe_init();

    RCC_CR |= BIT(0); /* HSI enabled, preserve factory trim. */
    for (uint32_t polls = 0u; (RCC_CR & BIT(1)) == 0u; ++polls) {
        if (polls == 1000000u) {
            for (;;) { /* Clock unavailable; outputs remain LOW. */ }
        }
    }
    RCC_CFGR &= ~UINT32_C(3); /* SYSCLK = HSI. */
    for (uint32_t polls = 0u; (RCC_CFGR & UINT32_C(0x0c)) != 0u; ++polls) {
        if (polls == 1000000u) {
            for (;;) { }
        }
    }
    RCC_CFGR = 0u; /* AHB/APB1/APB2 = HSI, ADC clock = HSI/2. */
    RCC_CR &= ~(BIT(16) | BIT(18) | BIT(24)); /* HSE/CSS/PLL unused. */
    FLASH_ACR = BIT(4); /* 0 wait states at 8 MHz; prefetch enabled. */

    RCC_APB2ENR |= BIT(0) | BIT(2) | BIT(3) | BIT(4) | BIT(9) | BIT(12) | BIT(14);
    RCC_APB1ENR |= BIT(21);
    (void)RCC_APB2ENR;
    (void)RCC_APB1ENR;

    /* PA4 CS high before output mode; PA5 SCLK low before alternate mode. */
    GPIOA_BSRR = BIT(4) | (BIT(5) << 16);
    GPIOA_CRL = (GPIOA_CRL & UINT32_C(0x0000ffff)) | UINT32_C(0xb4b20000);
    /* PA9 USART1 TX alternate push-pull, PA10 RX floating input. */
    GPIOA_CRH = (GPIOA_CRH & ~UINT32_C(0x00000ff0)) | UINT32_C(0x000004b0);
    /* PB0 button pull-up; PB6/PB7 I2C alternate open-drain. */
    GPIOB_BSRR = BIT(0) | BIT(6) | BIT(7);
    GPIOB_CRL = (GPIOB_CRL & ~UINT32_C(0xff00000f)) | UINT32_C(0xff000008);
    /* PC0/1/2 analog, PC4 ADS1220 DRDY input with pull-up. */
    GPIOC_BSRR = BIT(4);
    GPIOC_CRL = (GPIOC_CRL & ~UINT32_C(0x000f0fff)) | UINT32_C(0x00080000);

    milliseconds = 0u;
    SCB_VTOR = UINT32_C(0x08000000);
    SYST_CSR = 0u;
    SYST_RVR = BOARD_CORE_CLOCK_HZ / 1000u - 1u;
    SYST_CVR = 0u;
    SYST_CSR = BIT(2) | BIT(1) | BIT(0);

    USART1_CR1 = 0u;
    USART1_CR2 = 0u;
    USART1_CR3 = 0u;
    USART1_BRR = (BOARD_CORE_CLOCK_HZ + BOARD_UART_BAUD / 2u) / BOARD_UART_BAUD;
    USART1_CR1 = BIT(13) | BIT(3) | BIT(2);

    SPI1_CR1 = 0u;
    SPI1_CR2 = 0u;
    SPI1_CR1 = BIT(0) | BIT(2) | (4u << 3) | BIT(6) | BIT(8) | BIT(9);
    i2c_configure();

    ADC1_CR1 = 0u;
    /* Channels 10/11/12 and internal channel 17: 239.5 cycles at 4 MHz,
     * or 59.875 us. ST F103xC/D/E datasheet Table 13 requires up to 17.1 us
     * when sampling VREFINT. RM0008 maps that reference to ADC1_IN17.
     */
    ADC1_SMPR1 = 7u | (7u << 3) | (7u << 6) | (7u << 21);
    ADC1_SQR1 = 0u;
    ADC1_SQR2 = 0u;
    ADC1_SQR3 = 10u;
    ADC1_CR2 = BIT(0) | (7u << 17) | BIT(20) | BIT(23);
    /* TSVREFE enables both internal reference/temperature paths. Allow 1 ms
     * before calibration/conversion, also exceeding the 10 us internal sensor
     * startup maximum. board_delay_ms retains a finite poll fallback.
     */
    board_delay_ms(1u);
    ADC1_CR2 |= BIT(3); /* Reset calibration. */
    deadline = deadline_begin(20u);
    adc_ready = wait_bits(&ADC1_CR2, BIT(3), false, &deadline);
    if (adc_ready) {
        ADC1_CR2 |= BIT(2);
        deadline = deadline_begin(20u);
        adc_ready = wait_bits(&ADC1_CR2, BIT(2), false, &deadline);
    }
    board_outputs_force_off();
}

void SysTick_Handler(void)
{
    ++milliseconds;
}

uint32_t board_millis(void)
{
    return milliseconds;
}

void board_delay_ms(uint32_t duration_ms)
{
    while (duration_ms != 0u) {
        const uint32_t chunk = duration_ms > 1000u ? 1000u : duration_ms;
        deadline_t deadline = deadline_begin(chunk);
        /* Busy-wait so the poll guard still works if SysTick stops. Sleeping
         * with WFI here could otherwise block indefinitely without an IRQ.
         */
        while (!deadline_expired(&deadline)) {
            __asm volatile("nop");
        }
        duration_ms -= chunk;
    }
}

void board_uart_write_bytes(const uint8_t *data, size_t length)
{
    if (data == NULL) {
        return;
    }
    for (size_t i = 0u; i < length; ++i) {
        deadline_t deadline = deadline_begin(20u);
        if (!wait_bits(&USART1_SR, BIT(7), true, &deadline)) {
            return;
        }
        USART1_DR = data[i];
    }
}

void board_uart_write(const char *text)
{
    if (text == NULL) {
        return;
    }
    while (*text != '\0') {
        board_uart_write_bytes((const uint8_t *)text, 1u);
        ++text;
    }
}

bool board_uart_read_nonblocking(uint8_t *received)
{
    const uint32_t status = USART1_SR;
    if ((status & (BIT(5) | UINT32_C(0x0f))) == 0u) {
        return false;
    }
    /* Reading SR then DR also clears any line/error/overrun status. */
    const uint8_t value = (uint8_t)USART1_DR;
    if ((status & UINT32_C(0x0f)) != 0u || received == NULL) {
        return false;
    }
    *received = value;
    return (status & BIT(5)) != 0u;
}

static bool i2c_wait(uint32_t flag, deadline_t *deadline)
{
    for (;;) {
        const uint32_t status = I2C1_SR1;
        if ((status & I2C_ERRORS) != 0u) {
            return false;
        }
        if ((status & flag) != 0u) {
            return true;
        }
        if (deadline_expired(deadline)) {
            return false;
        }
    }
}

static bool i2c_wait_final_btf(void)
{
    /* ES0340 section 2.9.2 requires masking from read N-2 through STOP/read N-1.
     * At 100 kHz the last non-stretched byte takes about 90 us. A fixed bound
     * of 1024 register polls allows that byte but limits a stuck/stretched bus
     * to a few milliseconds at HSI 8 MHz, independently of masked SysTick.
     */
    for (uint32_t polls = 0u; polls < 1024u; ++polls) {
        const uint32_t status = I2C1_SR1;
        if ((status & I2C_ERRORS) != 0u) {
            return false;
        }
        if ((status & I2C_BTF) != 0u) {
            return true;
        }
    }
    return false;
}

static void i2c_clear_address(void)
{
    (void)I2C1_SR1;
    (void)I2C1_SR2;
}

static bool i2c_address(uint8_t address, bool read, deadline_t *deadline)
{
    if (!wait_bits(&I2C1_SR2, BIT(1), false, deadline)) {
        return false;
    }
    I2C1_SR1 &= ~I2C_ERRORS;
    I2C1_CR1 = I2C_PE | I2C_ACK;
    I2C1_CR1 |= I2C_START;
    if (!i2c_wait(I2C_SB, deadline)) {
        return false;
    }
    I2C1_DR = ((uint32_t)address << 1) | (read ? 1u : 0u);
    return i2c_wait(I2C_ADDR, deadline);
}

bool board_i2c_write(uint8_t address, const uint8_t *data, size_t length,
                     uint32_t timeout_ms)
{
    deadline_t deadline = deadline_begin(timeout_ms);
    if (address > 0x7fu || (data == NULL && length != 0u)) {
        return false;
    }
    if (!i2c_address(address, false, &deadline)) {
        goto failure;
    }
    i2c_clear_address();
    for (size_t i = 0u; i < length; ++i) {
        if (!i2c_wait(I2C_TXE, &deadline)) {
            goto failure;
        }
        I2C1_DR = data[i];
    }
    if (length != 0u && !i2c_wait(I2C_BTF, &deadline)) {
        goto failure;
    }
    I2C1_CR1 |= I2C_STOP;
    if (!wait_bits(&I2C1_SR2, BIT(1), false, &deadline)) {
        goto failure;
    }
    return true;
failure:
    i2c_abort();
    return false;
}

bool board_i2c_read(uint8_t address, uint8_t *data, size_t length,
                    uint32_t timeout_ms)
{
    deadline_t deadline = deadline_begin(timeout_ms);
    uint32_t saved;
    if (address > 0x7fu || data == NULL || length == 0u) {
        return false;
    }
    if (!i2c_address(address, true, &deadline)) {
        goto failure;
    }
    if (length == 1u) {
        /* RM0008 EV6_1: NACK before clearing ADDR, then STOP immediately. */
        I2C1_CR1 &= ~I2C_ACK;
        saved = irq_save();
        i2c_clear_address();
        I2C1_CR1 |= I2C_STOP;
        irq_restore(saved);
        if (!i2c_wait(I2C_RXNE, &deadline)) {
            goto failure;
        }
        *data = (uint8_t)I2C1_DR;
    } else if (length == 2u) {
        /* POS makes the NACK apply to the second byte. ADDR stretches SCL. */
        I2C1_CR1 |= I2C_POS;
        saved = irq_save();
        i2c_clear_address();
        I2C1_CR1 &= ~I2C_ACK;
        irq_restore(saved);
        if (!i2c_wait(I2C_BTF, &deadline)) {
            goto failure;
        }
        saved = irq_save();
        I2C1_CR1 |= I2C_STOP;
        data[0] = (uint8_t)I2C1_DR;
        data[1] = (uint8_t)I2C1_DR;
        irq_restore(saved);
    } else {
        i2c_clear_address();
        while (length > 3u) {
            if (!i2c_wait(I2C_RXNE, &deadline)) {
                goto failure;
            }
            *data++ = (uint8_t)I2C1_DR;
            --length;
        }
        /* Final three bytes: BTF stretches SCL while both receive slots full.
         * Keep the complete read N-2 / STOP / read N-1 sequence uninterruptible
         * as required by the ES0340 section 2.9.2 workaround.
         */
        if (!i2c_wait(I2C_BTF, &deadline)) {
            goto failure;
        }
        saved = irq_save();
        I2C1_CR1 &= ~I2C_ACK;
        *data++ = (uint8_t)I2C1_DR;
        if (!i2c_wait_final_btf()) {
            irq_restore(saved);
            goto failure;
        }
        I2C1_CR1 |= I2C_STOP;
        *data++ = (uint8_t)I2C1_DR;
        *data = (uint8_t)I2C1_DR;
        irq_restore(saved);
    }
    if (!wait_bits(&I2C1_SR2, BIT(1), false, &deadline)) {
        goto failure;
    }
    I2C1_CR1 &= ~(I2C_POS | I2C_ACK);
    return true;
failure:
    i2c_abort();
    return false;
}

bool board_spi_transfer(const uint8_t *transmit, uint8_t *receive, size_t length,
                        uint32_t timeout_ms)
{
    deadline_t deadline = deadline_begin(timeout_ms);
    for (size_t i = 0u; i < length; ++i) {
        uint8_t value;
        if (!wait_bits(&SPI1_SR, BIT(1), true, &deadline)) {
            return false;
        }
        SPI1_DR8 = transmit != NULL ? transmit[i] : 0xffu;
        if (!wait_bits(&SPI1_SR, BIT(0), true, &deadline)) {
            return false;
        }
        value = SPI1_DR8;
        if (receive != NULL) {
            receive[i] = value;
        }
    }
    return wait_bits(&SPI1_SR, BIT(7), false, &deadline);
}

void board_ads1220_cs(bool selected)
{
    GPIOA_BSRR = selected ? (BIT(4) << 16) : BIT(4);
}

bool board_ads1220_data_ready(void)
{
    return (GPIOC_IDR & BIT(4)) == 0u;
}

bool board_button_pressed(void)
{
    return (GPIOB_IDR & BIT(0)) == 0u;
}

static bool adc_read_channel(uint32_t channel, uint16_t *sample, deadline_t *deadline)
{
    /* Discard any completed conversion left by an earlier timed-out call.
     * A regular single conversion then owns EOC and DR until it is consumed.
     */
    (void)ADC1_DR;
    ADC1_SR = 0u;
    ADC1_SQR3 = channel;
    ADC1_CR2 |= BIT(22);
    if (!wait_bits(&ADC1_SR, BIT(1), true, deadline)) {
        /* Do not relabel a late conversion as another channel on the next
         * call. Initialization after reset is required following a timeout.
         */
        adc_ready = false;
        return false;
    }
    *sample = (uint16_t)(ADC1_DR & UINT32_C(0x0fff));
    return true;
}

bool board_current_read_raw(uint16_t samples[3], uint32_t timeout_ms)
{
    deadline_t deadline = deadline_begin(timeout_ms);
    uint16_t result[3];
    if (!adc_ready || samples == NULL) {
        return false;
    }
    for (uint32_t i = 0u; i < 3u; ++i) {
        if (!adc_read_channel(10u + i, &result[i], &deadline)) {
            return false;
        }
    }
    for (uint32_t i = 0u; i < 3u; ++i) {
        samples[i] = result[i];
    }
    return true;
}

bool board_adc_read_diagnostics(board_adc_diagnostics_t *samples, uint32_t timeout_ms)
{
    deadline_t deadline = deadline_begin(timeout_ms);
    board_adc_diagnostics_t result = {0};
    if (samples == NULL) {
        return false;
    }
    *samples = result;
    if (!adc_ready) {
        return false;
    }
    for (uint32_t i = 0u; i < 3u; ++i) {
        if (!adc_read_channel(10u + i, &result.current_raw[i], &deadline)) {
            return false;
        }
    }
    if (!adc_read_channel(17u, &result.vrefint_raw, &deadline)) {
        return false;
    }
    *samples = result;
    return true;
}

void board_watchdog_start(void)
{
    deadline_t deadline = deadline_begin(100u);
    RCC_CSR |= BIT(0);
    (void)wait_bits(&RCC_CSR, BIT(1), true, &deadline);
    IWDG_KR = 0x5555u;
    IWDG_PR = 6u; /* /256 */
    IWDG_RLR = 624u;
    deadline = deadline_begin(100u);
    (void)wait_bits(&IWDG_SR, BIT(0) | BIT(1), false, &deadline);
    IWDG_KR = 0xaaaau;
    IWDG_KR = 0xccccu;
}

void board_watchdog_feed(void)
{
    IWDG_KR = 0xaaaau;
}
