#include "usb_device.h"
#include "board.h"
#include "stm32f1xx.h"
#include "tusb.h"
#include "device/dcd.h"

static bool initialized;
static bool host_dtr;
static volatile uint32_t session_generation;
static volatile bool flush_pending;

static void session_changed(void)
{
    const uint32_t saved = __get_PRIMASK();
    __disable_irq();
    ++session_generation;
    flush_pending = true;
    __set_PRIMASK(saved);
}

static void flush_session(void)
{
    /* Only the main loop manipulates class FIFOs. An ISR can request another
     * flush while this one runs; keep that request for the next service call.
     */
    const uint32_t saved = __get_PRIMASK();
    __disable_irq();
    const bool needed = flush_pending;
    flush_pending = false;
    __set_PRIMASK(saved);
    if (needed) {
        tud_cdc_read_flush();
        (void)tud_cdc_write_clear();
    }
}

bool usb_device_init(void)
{
    if (initialized) {
        return true;
    }
    if (!board_usb_clock_ready()) {
        return false;
    }

    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    (void)RCC->APB2ENR;
    /* PA11 USB_DM and PA12 USB_DP: floating inputs. This mask preserves all
     * TEC, UART and ADS1220 GPIO configuration. Do not use GPIO pull-ups as
     * a substitute for the required external USB D+ pull-up resistor.
     */
    GPIOA->CRH = (GPIOA->CRH & ~UINT32_C(0x000ff000)) | UINT32_C(0x00044000);
    RCC->APB1ENR |= RCC_APB1ENR_USBEN;
    (void)RCC->APB1ENR;
    RCC->APB1RSTR |= RCC_APB1RSTR_USBRST;
    RCC->APB1RSTR &= ~RCC_APB1RSTR_USBRST;
    NVIC_ClearPendingIRQ(USB_HP_CAN1_TX_IRQn);
    NVIC_ClearPendingIRQ(USB_LP_CAN1_RX0_IRQn);
    NVIC_SetPriority(USB_HP_CAN1_TX_IRQn, 3u);
    NVIC_SetPriority(USB_LP_CAN1_RX0_IRQn, 3u);
    const tusb_rhport_init_t config = {
        .role = TUSB_ROLE_DEVICE,
        .speed = TUSB_SPEED_FULL
    };
    initialized = tusb_rhport_init(0u, &config);
    if (!initialized) {
        NVIC_DisableIRQ(USB_HP_CAN1_TX_IRQn);
        NVIC_DisableIRQ(USB_LP_CAN1_RX0_IRQn);
        RCC->APB1ENR &= ~RCC_APB1ENR_USBEN;
    }
    return initialized;
}

void usb_device_task(void)
{
    if (initialized) {
        flush_session();
        tud_task_ext(0u, false);
        flush_session();
        if (!usb_device_connected()) {
            /* A closed host must not leave command fragments or queued ACKs
             * for a later session. The application resets its parser too.
             */
            tud_cdc_read_flush();
            (void)tud_cdc_write_clear();
        }
    }
}

bool usb_device_connected(void)
{
    return initialized && !flush_pending && tud_cdc_connected() && !tud_suspended();
}

uint32_t usb_device_session_generation(void)
{
    return session_generation;
}

size_t usb_device_read(uint8_t *buffer, size_t capacity)
{
    if (!buffer || capacity == 0u || !usb_device_connected()) {
        return 0u;
    }
    if (capacity > CFG_TUD_CDC_RX_BUFSIZE) {
        capacity = CFG_TUD_CDC_RX_BUFSIZE;
    }
    return tud_cdc_read(buffer, (uint32_t)capacity);
}

size_t usb_device_write(const uint8_t *buffer, size_t length)
{
    if (!buffer || length == 0u || !usb_device_connected()) {
        return 0u;
    }
    if (length > CFG_TUD_CDC_TX_BUFSIZE) {
        length = CFG_TUD_CDC_TX_BUFSIZE;
    }
    const uint32_t written = tud_cdc_write(buffer, (uint32_t)length);
    (void)tud_cdc_write_flush();
    return written;
}

bool usb_device_write_packet(const uint8_t *buffer, size_t length)
{
    if (!buffer || length == 0u || length > CFG_TUD_CDC_TX_BUFSIZE ||
        !usb_device_connected() || tud_cdc_write_available() < length) {
        return false;
    }
    /* The class FIFO is written only here/from usb_device_write in main;
     * USB interrupts may drain it but cannot reduce the available capacity.
     */
    const uint32_t written = tud_cdc_write(buffer, (uint32_t)length);
    (void)tud_cdc_write_flush();
    return written == length;
}

uint32_t tusb_time_millis_api(void)
{
    return board_millis();
}

void tud_cdc_line_state_cb(uint8_t interface, bool dtr, bool rts)
{
    (void)rts;
    if (interface == 0u && dtr != host_dtr) {
        host_dtr = dtr;
        session_changed();
        flush_session(); /* CDC callback runs in the main-context USB task. */
    }
}

void tud_umount_cb(void)
{
    host_dtr = false;
    session_changed();
}

void tud_suspend_cb(bool remote_wakeup_enabled)
{
    (void)remote_wakeup_enabled;
    session_changed();
}

void tud_resume_cb(void)
{
    session_changed();
}

void tud_event_hook_cb(uint8_t port, uint32_t event, bool in_isr)
{
    (void)port;
    (void)in_isr;
    if (event == DCD_EVENT_BUS_RESET || event == DCD_EVENT_UNPLUGGED) {
        /* This hook can run in IRQ context. Defer all FIFO operations. */
        session_changed();
    }
}

void USB_HP_CAN1_TX_IRQHandler(void)
{
    tud_int_handler(0u);
}

void USB_LP_CAN1_RX0_IRQHandler(void)
{
    tud_int_handler(0u);
}
