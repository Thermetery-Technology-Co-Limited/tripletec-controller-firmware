/* STM32F103RCT6: 256 KiB flash, 48 KiB SRAM. No bootloader offset. */
ENTRY(Reset_Handler)

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 256K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 48K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);
_minimum_stack = 2K;

SECTIONS
{
    .isr_vector :
    {
        . = ALIGN(512);
        KEEP(*(.isr_vector))
    } > FLASH

    .text :
    {
        . = ALIGN(4);
        *(.text .text.*)
        *(.rodata .rodata.*)
        KEEP(*(.init))
        KEEP(*(.fini))
        . = ALIGN(4);
        _etext = .;
    } > FLASH

    .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
    .ARM.exidx :
    {
        __exidx_start = .;
        *(.ARM.exidx* .gnu.linkonce.armexidx.*)
        __exidx_end = .;
    } > FLASH

    _sidata = LOADADDR(.data);
    .data :
    {
        . = ALIGN(4);
        _sdata = .;
        *(.data .data.*)
        . = ALIGN(4);
        _edata = .;
    } > RAM AT > FLASH

    .bss (NOLOAD) :
    {
        . = ALIGN(4);
        _sbss = .;
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
    } > RAM

    .noinit (NOLOAD) :
    {
        . = ALIGN(4);
        *(.noinit .noinit.*)
        . = ALIGN(4);
        _end = .;
        PROVIDE(end = .);
    } > RAM

    /DISCARD/ : { *(.note.GNU-stack) *(.comment) }
    ASSERT(_end + _minimum_stack <= _estack, "RAM overflows reserved stack")
}
