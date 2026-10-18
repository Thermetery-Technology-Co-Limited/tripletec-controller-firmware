# Vendored USB dependencies

Only the files needed by the STM32F103RC full-speed CDC ACM device are included.
The vendor files are copied without source edits. Their original notices and
licenses apply; the proprietary application copyright does not replace them.

| Component | Pinned release / upstream commit | Included portion | License |
| --- | --- | --- | --- |
| [TinyUSB](https://github.com/hathach/tinyusb/tree/dae3f9a366bfcddbf9dcf1b48d7500286a849539) | 0.21.0 / `dae3f9a366bfcddbf9dcf1b48d7500286a849539` | Device core, CDC class, no-OS primitives, FIFO, STM32 FSDEV driver | [MIT](tinyusb/LICENSE), with the original additional redistribution/non-endorsement terms retained in `fsdev_stm32.h` |
| [Arm CMSIS-Core](https://github.com/ARM-software/CMSIS_5/tree/2b7495b8535bdcb306dac29b9ded4cfb679d7e5c) | CMSIS 5.9.0 / `2b7495b8535bdcb306dac29b9ded4cfb679d7e5c` | Cortex-M3, GCC compiler, version and MPU headers | [Apache-2.0](cmsis_core/LICENSE.txt) |
| [ST CMSIS STM32F1](https://github.com/STMicroelectronics/cmsis-device-f1/tree/8a76309ed1250d817e9c888c4417171d2ba3ba63) | v4.3.5 / `8a76309ed1250d817e9c888c4417171d2ba3ba63` | STM32F103xE family register definitions and declarations | [Apache-2.0](cmsis_stm32f1/License.md), as supplied in this pinned release |
| [pyserial](https://github.com/pyserial/pyserial/tree/v3.5) | 3.5 / tag `v3.5` | License only; runtime installed with pinned pip dependency for the desktop | [BSD terms and included notices](pyserial/LICENSE.txt) |

`UPSTREAM_SHA256.json` records the SHA-256 of every vendored source/header/license
file. The STM32F103RC (high-density 256 KiB) uses the `STM32F103xE` CMSIS device
selection. No ST HAL, vendor startup, vendor system-clock implementation, USB host
stack, dynamic allocator or RTOS is included.

TinyUSB FSDEV vendor files use the GNU `asm` spelling, so compile vendor C with
`-std=gnu11`; application files can use `-std=c11`. Required includes are:

```
platform/stm32f103
third_party/tinyusb/src
third_party/cmsis_core/Include
third_party/cmsis_stm32f1/Include
```

Six vendor C sources are linked:

```
third_party/tinyusb/src/tusb.c
third_party/tinyusb/src/common/tusb_fifo.c
third_party/tinyusb/src/device/usbd.c
third_party/tinyusb/src/class/cdc/cdc_device.c
third_party/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c
third_party/tinyusb/src/portable/st/stm32_fsdev/fsdev_common.c
```

The application owns the wrapper, descriptors and TinyUSB configuration under
`platform/stm32f103`. This prototype uses TinyUSB example VID `0xCAFE` and CDC PID
`0x4001`; those are development identifiers, not a product VID/PID assignment.
The descriptor serial number derives from the STM32's 96-bit unique identifier.

STM32F103 requires an external 1.5 kOhm pull-up on USB D+ (PA12). It has no
internal USB pull-up, and GPIO pull-ups do not provide an equivalent replacement.
The wrapper therefore makes no claim of electrically disconnecting USB from
software; a fixed external pull-up requires cable replugging for a forced detach.

See ST's [USB hardware guidance (AN4879)](https://www.st.com/content/st_com/en/technical-documents/an4879-introduction-to-usb-hardware-and-pcb-guidelines-using-stm32-mcus-stmicroelectronics_asset/chunk-001-topic-an4879-usb-on-stm32-products.html)
for the STM32F103 external pull-up requirement.
