#include <stdint.h>
#include "board.h"

extern uint32_t _estack;
extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;
extern int main(void);

void Reset_Handler(void);
void Default_Handler(void);
void SysTick_Handler(void);
void USB_HP_CAN1_TX_IRQHandler(void);
void USB_LP_CAN1_RX0_IRQHandler(void);
void ADC1_2_IRQHandler(void);

typedef void (*handler_t)(void);

/* STM32F103 high-density: 16 core vectors plus 60 peripheral IRQs. */
__attribute__((section(".isr_vector"), used))
const handler_t vector_table[76] = {
    [0] = (handler_t)&_estack,
    [1] = Reset_Handler,
    [2] = Default_Handler,  /* NMI */
    [3] = Default_Handler,  /* HardFault */
    [4] = Default_Handler,  /* MemManage */
    [5] = Default_Handler,  /* BusFault */
    [6] = Default_Handler,  /* UsageFault */
    [11] = Default_Handler, /* SVCall */
    [12] = Default_Handler, /* DebugMonitor */
    [14] = Default_Handler, /* PendSV */
    [15] = SysTick_Handler,
    [16 ... 33] = Default_Handler,
    [34] = ADC1_2_IRQHandler, /* external IRQ18 */
    [35] = USB_HP_CAN1_TX_IRQHandler, /* external IRQ19 */
    [36] = USB_LP_CAN1_RX0_IRQHandler, /* external IRQ20 */
    [37 ... 75] = Default_Handler
};

void Reset_Handler(void)
{
    uint32_t *destination;
    const uint32_t *source;

    /* This runs before .data copy/.bss clear and uses no C runtime state. */
    board_early_safe_init();
    source = &_sidata;
    for (destination = &_sdata; destination < &_edata; ++destination) {
        *destination = *source++;
    }
    for (destination = &_sbss; destination < &_ebss; ++destination) {
        *destination = 0u;
    }
    (void)main();
    Default_Handler();
}

void Default_Handler(void)
{
    board_outputs_force_off();
    for (;;) {
        /* Once main starts IWDG, an unexpected interrupt/fault resets safely. */
        __asm volatile("wfi");
    }
}
