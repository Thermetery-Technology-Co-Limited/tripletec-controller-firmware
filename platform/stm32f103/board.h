#ifndef TRIPLETEC_BOARD_H
#define TRIPLETEC_BOARD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Bench image: PA1/PA2/PA3 stay GPIO LOW; TIM2 never drives the TECs. */
#define BOARD_CORE_CLOCK_HZ 8000000u
#define BOARD_UART_BAUD 115200u
#define BOARD_TEC_OUTPUTS_LOCKED_OFF 1

/* Called by Reset_Handler before C runtime initialization; no global data used. */
void board_early_safe_init(void);
void board_init(void);
void board_outputs_force_off(void);
uint32_t board_millis(void);
void board_delay_ms(uint32_t duration_ms);

/* USART1: PA9 TX, PA10 RX, 115200 8N1, HSI clock accuracy applies. */
void board_uart_write(const char *text);
void board_uart_write_bytes(const uint8_t *data, size_t length);
bool board_uart_read_nonblocking(uint8_t *received);

/* I2C1: PB6/PB7, 100 kHz. Addresses are unshifted 7-bit values. */
bool board_i2c_write(uint8_t address, const uint8_t *data, size_t length,
                     uint32_t timeout_ms);
bool board_i2c_read(uint8_t address, uint8_t *data, size_t length,
                    uint32_t timeout_ms);

/* SPI1: PA5/PA6/PA7, mode 1, 250 kHz; PA4 is software chip select. */
bool board_spi_transfer(const uint8_t *transmit, uint8_t *receive, size_t length,
                        uint32_t timeout_ms);
void board_ads1220_cs(bool selected);
bool board_ads1220_data_ready(void); /* PC4, active LOW */
bool board_button_pressed(void);    /* PB0, active LOW */

/* Uncalibrated current-monitor voltages: PC0/PC1/PC2, ADC1 channels 10/11/12. */
bool board_current_read_raw(uint16_t samples[3], uint32_t timeout_ms);

typedef struct {
    uint16_t current_raw[3];
    uint16_t vrefint_raw;
} board_adc_diagnostics_t;

/* Same current inputs plus ADC1_IN17 (internal nominal 1.20 V reference).
 * VREFINT is a diagnostic of ADC/reference operation, not a calibrated supply
 * measurement or a measurement of the external 12 V rail. The F103 reference
 * tolerance applies; no per-device factory VREFINT calibration is assumed.
 * All four conversions share the bounded timeout. On failure *samples is zero
 * and must not be treated as a valid zero-current measurement. No output pins
 * are enabled by this function. Calls must be serialized with current reads.
 */
bool board_adc_read_diagnostics(board_adc_diagnostics_t *samples, uint32_t timeout_ms);

/* Approx. 4 s at nominal 40 kHz LSI; actual LSI tolerance changes the period. */
void board_watchdog_start(void);
void board_watchdog_feed(void);

#endif
