# TripleTECDriver firmware

Firmware and a desktop GUI for the STM32F103RCT6 cooler controller. Regulated mode uses **target = max(0°C, dew point + 3°C)** by default. Unregulated mode requests maximum cooling and deliberately bypasses the dew-point target. The current bench image measures and reports sensors while holding all three TEC control pins LOW; selecting a mode cannot enable the power stage.

## Desktop GUI

```powershell
python -m gui --connect
```

The dashboard shows ambient temperature/RH, dew point, regulated target, plate temperatures, nominal currents, and requested versus applied duty. Select **Regulated** or **Unregulated (100%)**, then apply the setting. Changing modes disarms all channels. The margin is adjustable from 0.5°C to 20°C in regulated mode. **Stop** is always available while connected; **Start** is disabled by the firmware's bench output lock.

Unregulated mode bypasses condensation protection and the regulated 0°C target floor. With commissioned hardware it ramps toward 100% duty without PI regulation; fresh sensor readings, valid calibrated currents, commissioned per-channel current limits, hardware verification, and operating-temperature limits still apply. It can cause condensation. The current bench firmware cannot energize TECs in either mode.

`python -m gui --demo` runs an explicitly labeled simulation without touching hardware. Live monitoring and commands use the existing Pico SWD connection; no UART wiring is required. The GUI waits for firmware acknowledgement before showing a change as applied, and keeps hardware work outside the UI thread.

To build a Windows executable with Python/Tk bundled:

```powershell
python scripts/build.py all
python scripts/package_gui.py
```

Launch `build/gui/TripleTEC-Control.exe`. OpenOCD must be installed and available on PATH. The executable includes the matching firmware symbol metadata; rebuild it after changing the firmware memory layout. `--smoke-test` checks the window can be created and closed without hardware access.

Use one host process per Pico probe. Close the GUI before flashing or running the diagnostic CLI. `python scripts/check_gui_link.py` performs an output-locked command check and restores regulated mode with a 3°C margin. Test evidence is recorded in [VALIDATION_GUI.md](VALIDATION_GUI.md).

### Powered board, TECs disconnected

Version 0.2.1 adds an independent internal-reference ADC check and a read-only no-load report:

```powershell
python -m scripts.check_no_load --serial E66598541B21AE38 --output validation/powered-no-load.json
```

It collects 12 snapshots, checks telemetry progress, output lock/zero duty, sensor errors, thermistor presence and nominal zero-current baselines. Exit 1 means checks remain; exit 2 means incomplete acquisition. It never changes settings, sends a heartbeat, resets the MCU or enables outputs. Missing thermistors are expected at this assembly stage and are reported as unresolved checks.

The current board has healthy internal-reference readings, but all three external current-sense inputs remain near ground. These are rejected inputs, not zero-current calibration values. The GUI shows unavailable current readings and the missing sensor details. See [VALIDATION_NO_LOAD.md](VALIDATION_NO_LOAD.md) for evidence and the next physical measurements.

## Build and test

Python 3 and `arm-none-eabi-gcc` are required. Native C tests also require a host GCC, such as MinGW-w64 on Windows. Put the compilers on PATH or provide their paths with `--cc` and `--arm-cc`.

From this directory:

```powershell
python scripts/build.py all
python scripts/read_status.py
```

Individual targets are `host` and `firmware`. Override compilers with `--cc` or `--arm-cc`. Do not add `-ffast-math`: fault handling depends on standard NaN/Inf semantics.

The image is `build/stm32/tripletec-bench.elf`, with matching `.bin`, `.hex`, map, and telemetry-symbol JSON. Build output is ignored by Git. The source uses C11, newlib/libm, and a small register-level board layer; there is no external HAL dependency or RTOS.

## What is implemented

- Three-channel controller, independent of the hardware drivers, with regulated and unregulated modes and a configurable margin initialized to 3°C.
- Magnus dew point over the liquid-water curve. Rejects RH outside `(0,100]`, nonfinite inputs, and ambient/dew-point values outside −45…60°C. A 0°C target floor is retained until frost protection is developed.
- PI demand, a 50% provisional duty cap, upward slew limiting, immediate reductions, anti-windup, and a 0.2°C restart hysteresis. Gains and limits are placeholders for powered commissioning.
- Explicit per-channel arming and a separate hardware-verified gate. Sensor, freshness, configuration, timing, arithmetic and current faults stop and disarm affected channels. Current limits default to zero (uncommissioned), so arming is refused until limits are set and measurements are calibrated. Recovery does not automatically rearm channels. Reaching the regulated target is a normal, automatically recovering cooling inhibit.
- SHT31 I²C measurement with both CRC checks and bounded bus operations.
- ADS1220 SPI configuration/readback verification and three-channel single-shot acquisition, with signed 24-bit decoding and DRDY timeout handling.
- Nominal 100kΩ/B3950 thermistor conversion, open/short/range detection, and nominal per-channel ACS725 current conversion.
- Safe pin initialization before C runtime startup, watchdog, fault handler, and read-only live telemetry through the existing Pico SWD connection.
- A CRC-checked RAM command mailbox with sequence/acknowledgement handling for mode, margin, stop, arm and heartbeat commands. The bench image accepts settings but rejects arm commands. A two-second host lease disarms any future enabled channels after loss of communication.

The bench application always sets `hardware_verified=false`, never arms the controller, never configures PWM alternate-function outputs, and repeatedly forces PA1/PA2/PA3 LOW. The control library is exercised with simulated inputs in native tests; simulated measurements are never substituted into live board readings.

## Board connections

| Function | STM32 pin / interface |
|---|---|
| PWM1 / PWM2 / PWM3 | PA1 / PA2 / PA3; future TIM2 CH2 / CH3 / CH4 |
| Ambient SHT31 | I2C1 PB6 SCL, PB7 SDA; address 0x44 |
| ADS1220 CS / CLK / MISO / MOSI | SPI1 PA4 / PA5 / PA6 / PA7; mode 1 |
| ADS1220 DRDY | PC4, active LOW |
| Current sense 1 / 2 / 3 | ADC1 PC0 / PC1 / PC2 |
| Diagnostic UART | USART1 PA9 TX, PA10 RX, 115200 8N1, 3.3V logic |
| Button | PB0, active LOW |
| Debug | PA13 SWDIO, PA14 SWCLK through Pico CMSIS-DAP |

The board has a 12MHz crystal; this first image deliberately runs on the internal 8MHz HSI. It does not depend on the crystal or USB clock. HSI tolerance applies to UART baud accuracy.

The three 8-pin connectors on the power board supply 12V. The separate MCU/power-board harness is 10 pins. USB powers the MCU-side 3.3V system; no 12V input is needed for the onboard SHT31 and ADS1220. Thermistor measurements require the thermistors to be physically connected to their two-pin headers.

The output stage is a low-side NMOS switch per channel, supporting cooling in the installed TEC polarity. It is not a reversing bridge. Channel 1 uses ACS725-50AB (nominal 26.4mV/A, zero at half supply); channels 2/3 use ACS725-10AU (264mV/A, zero at one-tenth supply).

## Diagnostics

`scripts/read_status.py` uses generic OpenOCD MEM-AP reads, without a CPU halt/reset. It validates a sequence counter to reject torn snapshots, then reports JSON. It requires the ELF/symbol file matching the programmed firmware. `--output path.json` saves a measurement. OpenOCD selects an attached CMSIS-DAP probe; use `--serial` to select a specific probe when needed.

UART prints status every second and accepts `status` and `help`. Missing values use `INT32_MIN`; temperatures are printed in milli-degrees, RH in milli-percent. Pico's COM3 belongs to the probe UART bridge: MCU USART1 data only reaches it if the Pico UART pins are actually wired to the MCU header. SWD telemetry requires no UART wiring.

The MCU USB schematic has no explicit 1.5kΩ D+ pullup, so direct USB CDC is not included in this image.

Telemetry `valid_flags`: bit 0 fresh ambient sample; bit 1 verified ADS configuration; bits 2/3/4 fresh plate temperatures; bit 5 valid current ADC acquisition. These indicate acquisition validity, not calibration or permission to cool. `active_faults=2` is the intentional hardware gate in the bench image; other bits are defined in `core/controller.h`. Applied duty is always zero.

An optional, independently sequenced 32-byte ADC diagnostic block reports ADC1 channel 17 VREFINT, acquisition errors and an estimated VDDA using nominal 1.20V. Its symbol is included in the generated metadata; the main telemetry remains schema 2. The estimate is not a calibrated supply measurement and does not sense the external 12V rail. A failed or implausible internal-reference acquisition invalidates current readings. Main telemetry and this diagnostic block may come from adjacent publications; each includes its own sequence and uptime.

## Limits before powered operation

This stage cannot establish TEC polarity, cooling power, current limits, heatsink capacity, thermal lag, or loop gains. Software current trips are implemented and tested, but the actual current shutdown and PWM power stage are not commissioned. Current values and thermistor temperatures use nominal 3.3V and component constants; measure AVCC, current zero offsets, resistor ratios, and the actual thermistor R–T characteristics before claiming temperature accuracy. The thermistor range note on the drawing does not exactly match a nominal B3950 curve.

The 3°C offset is the requested initial setting, not a validated condensation guarantee. Sensor placement, accuracy, gradients, and thermal inertia must be measured. Turning cooling off does not actively warm an already cold plate. Final powered tests must verify undershoot and humidity-step behavior before removing the output lock.

## Source references

- Design originals are retained separately from this firmware repository: `SCH_TripleTECDriver_MCU_2026-10-17.pdf` and `SCH_TripleTECDriver_Power_2026-10-17.pdf`. In the original local workspace they are in the sibling `hardware/` directory.
- [Sensirion humidity equations, Table 1 and Eq. 12](https://sensirion.com/en/media/documents/8AB2AD38/61642ADD/Sensirion_AppNotes_Humidity_Sensors_Introduction_to_Relative_Humidit.pdf).
- [SHT3x-DIS datasheet](https://sensirion.com/media/documents/213E6A3B/63A5A569/Datasheet_SHT3x_DIS.pdf).
- [TI ADS1220 datasheet](https://www.ti.com/lit/ds/symlink/ads1220.pdf).
- [Allegro ACS725 datasheet](https://www.allegromicro.com/-/media/files/datasheets/acs725-datasheet.pdf).
- [ST RM0008](https://www.st.com/resource/en/reference_manual/rm0008-stm32f101xx-stm32f102xx-stm32f103xx-stm32f105xx-and-stm32f107xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf) and [high-density device errata ES0340](https://www.st.com/resource/en/errata_sheet/cd00197763-stm32f101xcde-and-stm32f103xcde-highdensity-device-limitations-stmicroelectronics.pdf).

## Copyright

Copyright (c) 2026 Thermetery Technology Co., Limited 賽摩泰科技（中國香港）有限公司. All rights reserved. See [COPYRIGHT](COPYRIGHT).

