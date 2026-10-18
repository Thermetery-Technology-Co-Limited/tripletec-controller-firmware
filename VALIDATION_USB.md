# Native USB implementation and verification — 2026-10-18

Version 0.3.0 implements STM32 USB CDC control/status, a bounded binary protocol and a USB-first desktop option. The image is flashed and verified; direct USB enumeration and Pico-disconnected operation are **not yet verified**. The operator has replugged a known data-capable USB-C cable. Windows still lists only the Pico's COM3, with no `CAFE:4001` STM32 device or unknown USB device. The schematic omits the required external 1.5kΩ D+ pull-up, and the operator has no resistor available.

## Passed software checks

- Seven native C test executables: dew point, controller, modes/current trips, SWD command protocol, USB protocol, USB descriptors and sensors.
- 81 Python tests, including USB framing/CRC/sequence/partial reads, no replay after uncertain commands, malformed-policy Start inhibits with STOP availability, DTR session sequencing/cleanup, GUI transport selection and worker lifecycle.
- Cortex-M3 build with warnings treated as errors; approximately 27KiB flash and 3.3KiB static RAM, well within the STM32F103RC limits.
- Source GUI and packaged Windows 0.3.0 executable smoke tests.
- All 37 hashes in the pinned third-party source/license manifest verified. The desktop includes pyserial3.5 and its upstream license, along with the existing runtime notices.
- Independent clock/IRQ and application/USB-session reviews found no blocker for the output-locked image. The callback rejects commands after a session-generation change before applying or renewing a lease.

## Passed live checks through the retained Pico

The preceding full flash image was backed up to `../hardware/bench-before-native-usb-2026-10-18.bin`. Programming and flash verification returned **Verified OK**.

The HSE/PLL clock tree is active. RCC_CR=`03035a83`, RCC_CFGR=`0011840a`: 12MHz HSE x6, SYSCLK/APB2 72MHz, APB1 36MHz, ADC 12MHz and USB PLL/1.5=48MHz. FLASH_ACR=`32` includes the two-wait-state setting and prefetch status. SysTick reload is `1193f` (71999), I2C frequency/CCR/TRISE are 36/180/37. A coarse timebase check observed 6.25 firmware seconds over approximately 6.2501 host seconds; this is not precision oscillator metrology.

SHT31, ADS1220 and internal ADC acquisition continue with zero recorded errors in the follow-up sample window. Thermistors 2/3 read approximately 26.4°C and 26.5°C; thermistor 1 remains disconnected. Current zero-noise/calibration and load commissioning remain outstanding.

The live SWD command check passed: mode changes, margin adjustment, STOP, rejected ARM and preserved selected `[0,9,9]` ampere trip settings with commissioning mask0. Final state is regulated, dew point +3°C, stopped. The compile-time output lock, permanent GPIO LOW forcing, current commissioning gates and 9A XT60 ceiling remain in place.

## USB hardware observations and pending test

USB peripheral initialization completes: RCC USB enable is set, USB_CNTR=`dd00`, USB_DADDR=`80`, USB_ISTR=`0` and USB_FNR=`0`. GPIO input reads show D+/D− low. After the data-cable replug, these observations remain and Windows still has no STM32 USB device. They are consistent with an absent attach pull-up; they do not prove end-to-end PCB data continuity. Do not substitute a GPIO pull-up or a resistor to5V.

The next physical work is to verify/fit the 1.5kΩ pull-up to3.3V described in [USB_CONTROL.md](USB_CONTROL.md). Only after enumeration and `python -m scripts.check_usb_link --port COMx` succeed should the Pico be disconnected for the USB-only independence test. No direct USB command acknowledgement, physical USB reconnect behavior or Pico-free run is claimed yet. The desktop is left using Pico SWD while the hardware is incomplete.

## Evidence

Retained under `validation/`: `native-usb-flash-verify.log`, `native-usb-clock-registers.log`, `native-usb-output-registers.log`, `native-usb-swd-status.json`, `native-usb-swd-command-check.json`, `native-usb-sensor-check.json`, `native-usb-enumeration.json`, `native-usb-enumeration-replug.json`, `native-usb-after-replug-swd.json`, `native-usb-after-replug-registers.log`, `native-usb-timebase.json`, and `native-usb-artifacts.json`. Local detailed build logs remain in `build/`.

Firmware SHA-256: `2cc182502a66b3334bc0be4dd66477d84819aa22f980bd1720914721e01058b5`.

Desktop SHA-256: `011a7499447dacad312ba83b6d633b7d943ccf96372330021a6b334142702bcf`.
