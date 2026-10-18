# TripleTECDriver firmware

Firmware and a desktop GUI for the STM32F103RCT6 cooler controller. Regulated mode uses **target = max(0°C, dew point + 3°C)** by default. Unregulated mode requests maximum cooling and deliberately bypasses the dew-point target. Normal control remains inhibited; selecting a mode cannot enable the power stage. A separate supervised TEC1 commissioning path can emit bounded, current-monitored pulses.

Version 0.3.0 adds direct USB CDC control and a USB-first desktop connection option. The USB firmware is flashed, the clocks and SWD fallback are verified, but direct USB enumeration remains unverified: no STM32 USB device appears even after a known data-cable replug. The schematic omits the required external D+ pull-up. Keep the Pico connected until the hardware requirement and USB-only tests in [USB_CONTROL.md](USB_CONTROL.md) are complete.

## Desktop GUI

Version 0.4.0 adds **TEC1 commissioning** over Pico SWD. Keep the bench lock ON, regulated mode selected and margin at least 3°C. **Prepare TEC1 / zero current** screens 128 off-state samples, applies a zero-offset adjustment and self-tests the ADC watchdog with the output held LOW. It selects the operator's provisional **15A trip** while retaining nominal gain and false full-commissioning flags. **Verify shutdown (5 A, 1 ms)** requests exactly one bounded pulse with a deliberately lower watchdog threshold; the firmware requires early shutdown and current returning near zero before marking the trip test passed. Only then is **Run TEC1 (15 A trip, 1 s max)** available: 1ms pulses at least 20ms apart, at most 5% duty for one second, with sensor/temperature/link guards and a latched ADC-triggered trip. There is no automatic progression or repeat. STOP cancels the test; normal Start remains inhibited. The legacy CPU-halted debug pulse is unavailable on this image. These tests do not establish calibrated current, continuous-operation readiness or a constant-current ceiling. See [COMMISSIONING.md](COMMISSIONING.md) for evidence.

Version 0.4.1 adds **Observe cooling (20%, 10 s max)** for the operator's 500g copper block. After shutdown verification, this separate button requests 1ms pulses at least 5ms apart for at most 10 seconds, retaining the provisional 15A trip and temperature/link guards. It requires a separate click and never repeats automatically. Zero-offset preparation expires after five minutes; the GUI disables tests when that evidence expires. Active tests poll at 200ms intervals to maintain the 1.5s command lease. Each pulse still has an independent hardware stop. Reflashing clears the preparation and shutdown-verification state.

Version 0.4.2 replaces that observation button with the operator-requested **Observe cooling (41.7%, 30 s max)**: nominal 5ms pulses every 12ms, a 30s maximum and the same provisional 15A trip and temperature/link guards. It requires the shutdown test to pass after reset and a separate operator click. With a nominal 12V supply this has about a 5V time average; the output is pulsed supply voltage, not regulated 5V DC. No TEC-terminal or 12V-rail voltage measurement is present. The backend also supports a GPIO-LOW version of this exact timer/lease profile for preflight; it never connects the timer to PA1.

```powershell
python -m gui --connect
```

The dashboard shows ambient temperature/RH, dew point, regulated target, plate temperatures, nominal currents, and requested versus applied duty. Select **Regulated** or **Unregulated (100%)**, then apply the setting. Changing modes disarms all channels. The margin is adjustable from 0.5°C to 20°C in regulated mode. **Stop** is always available while connected; **Start** is disabled by the firmware's bench output lock.

Desktop 0.3.1 adds the operator-only **Test TEC1 (1 ms)** button over Pico SWD. Each click requests one full-voltage, hardware-timed diagnostic pulse and displays the captured nominal peak current. This separate debug operation can energize TEC1 briefly while normal Start remains locked; it does not enable continuous operation or current regulation. Connecting and polling never trigger it. See [TEC1_TEST.md](TEC1_TEST.md) for prerequisites, evidence and limits.

Version 0.3.2 adds **Unlock bench lock / Lock outputs OFF** beside the normal mode controls. The displayed bench state comes from acknowledged firmware telemetry. Either command disarms all channels and forces the pins LOW before changing the lock state; unlocking never starts a channel or changes a current threshold, calibration, hardware verification or commissioning flag. Reset and USB session changes restore the lock ON. This assembly still rejects normal ARM with `INTERLOCK` when unlocked because hardware/current protection is uncommissioned; continuous PWM is not enabled by this update. The separate 1ms test requires the bench lock ON. Older firmware without the advertised capability cannot toggle the lock. Opening, polling and reconnecting the GUI never submit an unlock.

XT60 channels 2/3 now have a selected **9A software trip threshold**, displayed from firmware beside the measured current. A sample at or above 9A in magnitude trips, zeros demand and disarms that channel in either mode; configuration above 9A is rejected. No automatic rearm follows recovery. Channel 1 remains unset. All current-limit commissioning flags remain false, so selecting 9A does not grant permission to energize. This is a sampled shutdown, not a regulated 9A current source: full-voltage PWM peaks can exceed the threshold before acquisition. The output lock and the 27A load/power-path concerns remain in place.

Unregulated mode bypasses condensation protection and the regulated 0°C target floor. With commissioned hardware it ramps toward 100% duty without PI regulation; fresh sensor readings, valid calibrated currents, commissioned per-channel current limits, hardware verification, and operating-temperature limits still apply. It can cause condensation. The current bench firmware cannot energize TECs in either mode.

`python -m gui --demo` runs an explicitly labeled simulation without touching hardware. The desktop offers Auto (USB first), STM32 USB-C and Pico SWD. Auto selects USB when a matching device is present, otherwise SWD at initial connection; it never switches transport after an uncertain command. Use `python -m gui --transport usb --connect` for a USB-only check, or `--transport swd` for the Pico. Source USB use requires `python -m pip install -r requirements-desktop.txt`; the packaged application includes it. No UART wiring is required. The GUI waits for acknowledgement before showing a change as applied and keeps hardware work outside the UI thread.

To build a Windows executable with Python/Tk bundled:

```powershell
python scripts/build.py all
python scripts/package_gui.py
```

Launch `build/gui/TripleTEC-Control.exe`. OpenOCD must be installed on PATH for Pico SWD; direct USB does not use it. The executable includes matching SWD symbol metadata, so rebuild it after changing the firmware memory layout. `--smoke-test` checks the window can be created and closed without hardware access.

Use one host process per Pico probe. Close the GUI before flashing or running the diagnostic CLI. `python scripts/check_gui_link.py` performs an output-locked command check and restores regulated mode with a 3°C margin. Test evidence is recorded in [VALIDATION_GUI.md](VALIDATION_GUI.md).

Likewise, use one client per USB COM port. `python -m scripts.check_usb_link --list` lists only the STM32 development USB identity. The same command without `--list` performs a USB-only output-locked command check, with no SWD fallback. See [VALIDATION_USB.md](VALIDATION_USB.md) for the current physical verification limits.

### Powered board, TECs disconnected

Version 0.2.1 adds an independent internal-reference ADC check and a read-only no-load report:

```powershell
python -m scripts.check_no_load --serial E66598541B21AE38 --output validation/powered-no-load.json
```

It collects 12 snapshots, checks telemetry progress, output lock/zero duty, sensor errors, thermistor presence and nominal zero-current baselines. Exit 1 means checks remain; exit 2 means incomplete acquisition. It never changes settings, sends a heartbeat, resets the MCU or enables outputs. Missing thermistors are expected at this assembly stage and are reported as unresolved checks.

The current board has healthy internal-reference readings. A loose 10-pin connection caused near-ground external current readings; securing it restored nominal zero-current levels throughout a later 60-snapshot check. Channels 2/3 still need noise investigation and stable baselines before calibration. The GUI shows unavailable readings when firmware rejects a sensor input. See [VALIDATION_NO_LOAD.md](VALIDATION_NO_LOAD.md) and [COMMISSIONING.md](COMMISSIONING.md) for evidence and remaining checks.

## Build and test

Python 3 and `arm-none-eabi-gcc` are required. Native C tests also require a host GCC, such as MinGW-w64 on Windows. Put the compilers on PATH or provide their paths with `--cc` and `--arm-cc`.

From this directory:

```powershell
python scripts/build.py all
python scripts/read_status.py
```

Individual targets are `host` and `firmware`. Override compilers with `--cc` or `--arm-cc`. Do not add `-ffast-math`: fault handling depends on standard NaN/Inf semantics.

The image is `build/stm32/tripletec-bench.elf`, with matching `.bin`, `.hex`, map, and telemetry-symbol JSON. Build output is ignored by Git. Application source uses C11, newlib/libm and a register-level board layer; pinned TinyUSB device/CDC sources use GNU C11, with CMSIS register headers. No HAL, dynamic allocator or RTOS is used. Third-party sources, licenses and hashes are included under [third_party/](third_party/README.md).

## What is implemented

- Three-channel controller, independent of the hardware drivers, with regulated and unregulated modes and a configurable margin initialized to 3°C.
- Magnus dew point over the liquid-water curve. Rejects RH outside `(0,100]`, nonfinite inputs, and ambient/dew-point values outside −45…60°C. A 0°C target floor is retained until frost protection is developed.
- PI demand, a 50% provisional duty cap, upward slew limiting, immediate reductions, anti-windup, and a 0.2°C restart hysteresis. Gains and limits are placeholders for powered commissioning.
- Explicit per-channel arming and a separate hardware-verified gate. Sensor, freshness, configuration, timing, arithmetic and current faults stop and disarm affected channels. Selected current thresholds default to unset/9A/9A; independent commissioning flags default false for every channel, preventing arming. Measurements must also be calibrated. Recovery does not automatically rearm channels. Reaching the regulated target is a normal, automatically recovering cooling inhibit.
- SHT31 I²C measurement with both CRC checks and bounded bus operations.
- ADS1220 SPI configuration/readback verification and three-channel single-shot acquisition, with signed 24-bit decoding and DRDY timeout handling.
- Nominal 100kΩ/B3950 thermistor conversion, open/short/range detection, and nominal per-channel ACS725 current conversion.
- Safe pin initialization before C runtime startup, watchdog, fault handler, and read-only live telemetry through the existing Pico SWD connection.
- A CRC-checked RAM command mailbox with sequence/acknowledgement handling for mode, margin, stop, arm and heartbeat commands. The bench image accepts settings but rejects arm commands. A two-second host lease disarms any future enabled channels after loss of communication.
- Direct USB CDC with bounded packet framing, CRC/sequence validation, fixed buffers, no command replay, and session-change disarming. USB STATUS never renews the host lease. Both USB and SWD reach the same output-locked command handler.

The normal application keeps `hardware_verified=false`, refuses normal ARM and forces PA1/PA2/PA3 LOW outside the supervised commissioning path. Since 0.3.2 its separate software bench-lock flag is operator controlled, but clearing that flag does not bypass the normal hardware/current inhibits. The 0.4.0 commissioning path keeps the CPU running, monitors TEC1 current continuously and uses a hardware one-pulse timer for every activation; TEC2/3 remain GPIO LOW. The control library is exercised with simulated inputs in native tests; simulated measurements are never substituted into live board readings.

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

The 12MHz HSE crystal now feeds PLL x6: SYSCLK/APB2 72MHz, APB1 36MHz, ADC 12MHz and USB 48MHz. SysTick, UART, I2C, SPI and bounded peripheral waits follow the active clocks. If HSE/PLL startup fails, a bounded fallback keeps sensor/SWD operation on 8MHz HSI and refuses USB initialization. HSI tolerance then applies to UART baud accuracy.

The three 8-pin connectors on the power board supply 12V. The separate MCU/power-board harness is 10 pins. USB powers the MCU-side 3.3V system; no 12V input is needed for the onboard SHT31 and ADS1220. Thermistor measurements require the thermistors to be physically connected to their two-pin headers.

The output stage is a low-side NMOS switch per channel, supporting cooling in the installed TEC polarity. It is not a reversing bridge. Channel 1 uses ACS725-50AB (nominal 26.4mV/A, zero at half supply); channels 2/3 use ACS725-10AU (264mV/A, zero at one-tenth supply).

## Diagnostics

`scripts/read_status.py` uses generic OpenOCD MEM-AP reads, without a CPU halt/reset. It validates a sequence counter to reject torn snapshots, then reports JSON. It requires the ELF/symbol file matching the programmed firmware. `--output path.json` saves a measurement. OpenOCD selects an attached CMSIS-DAP probe; use `--serial` to select a specific probe when needed.

UART prints status every second and accepts `status` and `help`. Missing values use `INT32_MIN`; temperatures are printed in milli-degrees, RH in milli-percent. Pico's COM3 belongs to the probe UART bridge: MCU USART1 data only reaches it if the Pico UART pins are actually wired to the MCU header. SWD telemetry requires no UART wiring.

The MCU USB schematic has no explicit 1.5kΩ D+ pull-up. Direct CDC is implemented, but the assembled board must provide that resistor from D+ to 3.3V; GPIO pull-ups are not a substitute. See [USB_CONTROL.md](USB_CONTROL.md). The prototype USB VID/PID is `CAFE:4001`, with a serial descriptor derived from the STM32 UID; a product needs an assigned identity.

Telemetry `valid_flags`: bit 0 fresh ambient sample; bit 1 verified ADS configuration; bits 2/3/4 fresh plate temperatures; bit 5 valid current ADC acquisition. These indicate acquisition validity, not calibration or permission to cool. `active_faults=2` is the intentional hardware gate in the bench image; other bits are defined in `core/controller.h`. Applied duty is always zero.

An optional, independently sequenced 32-byte ADC diagnostic block reports ADC1 channel 17 VREFINT, acquisition errors and an estimated VDDA using nominal 1.20V. Its symbol is included in the generated metadata; the main telemetry remains schema 2. The estimate is not a calibrated supply measurement and does not sense the external 12V rail. A failed or implausible internal-reference acquisition invalidates current readings. Main telemetry and this diagnostic block may come from adjacent publications; each includes its own sequence and uptime.

Another optional 32-byte block reports selected current-trip thresholds and their commissioning mask. The host rejects malformed, out-of-range or stale policy snapshots without blocking STOP. The GUI treats missing or uncommissioned settings as a Start inhibit. Version 0.2.2 includes this block and matching desktop metadata.

## Limits before powered operation

Follow [COMMISSIONING.md](COMMISSIONING.md) before connecting a TEC. The loose harness has been secured and signal recovery observed, but current-sense noise/calibration and thermistor checks remain. The reported 12V/320W load exceeds the XT60 channels' 10A measurement range if operated at that measured power and voltage. A review of actual load/power-path ratings is required. The output lock remains in place.

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

