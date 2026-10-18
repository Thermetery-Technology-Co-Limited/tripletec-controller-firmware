#ifndef TRIPLETEC_TUSB_CONFIG_H
#define TRIPLETEC_TUSB_CONFIG_H

/* STM32F103RC uses the same CMSIS peripheral definition as STM32F103xE. */
#define CFG_TUSB_MCU OPT_MCU_STM32F1
#define CFG_TUSB_OS OPT_OS_NONE
#define CFG_TUSB_DEBUG 0
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#define CFG_TUD_ENABLED 1
#define CFG_TUH_ENABLED 0
#define CFG_TUC_ENABLED 0
#define CFG_TUD_MAX_SPEED OPT_MODE_FULL_SPEED
#define CFG_TUD_ENDPOINT0_SIZE 64
#define CFG_TUD_ENDPPOINT_MAX 3

/* Fixed storage, no RTOS and no allocator. Bound each main-loop service call. */
#define CFG_TUD_TASK_QUEUE_SZ 16
#define CFG_TUD_TASK_EVENTS_PER_RUN 8
#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))
#define CFG_TUSB_MEM_SECTION

#define CFG_TUD_CDC 1
#define CFG_TUD_CDC_NOTIFY 1
#define CFG_TUD_CDC_RX_BUFSIZE 512
#define CFG_TUD_CDC_TX_BUFSIZE 512
#define CFG_TUD_CDC_RX_EPSIZE 64
#define CFG_TUD_CDC_TX_EPSIZE 64
#define CFG_TUD_MSC 0
#define CFG_TUD_HID 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_VENDOR 0

#endif
