#ifndef TRIPLETEC_USB_DEVICE_H
#define TRIPLETEC_USB_DEVICE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Direct USB-C CDC ACM on PA11/PA12. Requires an accurate 48 MHz USB clock
 * and an external 1.5 kOhm D+ pull-up: STM32F103 has no internal USB pull-up.
 * Initialization never enables the TEC outputs. All APIs run in main context.
 */
bool usb_device_init(void);
void usb_device_task(void); /* Nonblocking; at most eight queued USB events. */
bool usb_device_connected(void); /* Configured, awake, and host DTR asserted. */
/* Changes on reset, unplug, suspend/resume or DTR changes, including changes
 * that occur between successive calls. Reset parser state and disarm whenever
 * this value changes. Aligned 32-bit reads are atomic on Cortex-M3.
 */
uint32_t usb_device_session_generation(void);
size_t usb_device_read(uint8_t *buffer, size_t capacity);
size_t usb_device_write(const uint8_t *buffer, size_t length);

/* Queue the whole response or none. Exactly one main-context writer is
 * required; an oversized/full FIFO returns false and must be retried later.
 */
bool usb_device_write_packet(const uint8_t *buffer, size_t length);

void USB_HP_CAN1_TX_IRQHandler(void);
void USB_LP_CAN1_RX0_IRQHandler(void);

#endif
