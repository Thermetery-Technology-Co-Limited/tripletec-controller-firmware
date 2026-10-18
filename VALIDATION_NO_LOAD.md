# Powered no-load diagnostics — 2026-10-18

Version 0.2.1 was programmed and verified through the Pico CMSIS-DAP probe. The operator reports the 8-pin inputs and 10-pin interboard cable connected, with the 12V supply on. No TECs or plate thermistors are attached. Supply presence is operator-provided information; this PCB has no firmware-readable 12V monitor.

Later observations: the current inputs temporarily recovered near nominal zero after the board was moved, then returned near ground with the harness connected and untouched. The fault remains unresolved. See [COMMISSIONING.md](COMMISSIONING.md) and `validation/pre-tec-readiness-2026-10-18.json` for the latest checks and load-rating concerns.

## Findings

All 12 diagnostic snapshots were collected successfully. Firmware sequences and uptime advanced, with no new SHT31, ADS1220 or internal ADC errors. The internal reference read 1498 counts in the final snapshot, corresponding to estimated VDDA 3.280V using the nominal 1.20V reference. This supports ADC/reference operation; it does not validate the external current-sense path or calibrate VDDA.

| Signal | Mean raw ADC | Nominal voltage observed | Expected zero-current voltage |
|---|---:|---:|---:|
| Current channel 1 | 18.17 | 0.0146V | 1.65V |
| Current channel 2 | 12.42 | 0.0100V | 0.33V |
| Current channel 3 | 18.42 | 0.0148V | 0.33V |

Observed external voltages assume a nominal 3.3V ADC reference. All three inputs are near the low rail and outside the nominal sensor current ranges. Do not offset-calibrate these readings into zero. The internal ADC check and the reviewed channel mapping point toward the external sensor supply, signal path, harness or assembly; they do not isolate a particular component.

All three thermistors report open, consistent with the operator's assembly status. The no-load diagnostic correctly exits 1 because current-sense and thermistor checks remain unresolved. Cooling has not been commissioned.

## Completed verification

- Five native C test executables passed; 47 Python tests passed, including read-only diagnostics, optional ADC transport, torn snapshots and unavailable-current GUI presentation.
- Cortex-M3 build passed with warnings treated as errors; flash verification returned **Verified OK**.
- The live command check passed: modes, margin, STOP, rejected ARM, zero duty; restored regulated mode, 3°C margin, stopped.
- Source and packaged GUI smoke tests passed. The Windows executable was rebuilt with matching symbol metadata and version 0.2.1.
- Independent MEM-AP reads after the command check: GPIOA_CRL=`b4b22224`, GPIOA_ODR=`0000a010`, TIM2_CR1=`00000000`, TIM2_CCER=`00000000`. PA1/PA2/PA3 remain LOW GPIO outputs; TIM2 is disabled.

The compile-time output lock, hardware verification gate and unconditional ARM rejection remain in place. No gate pulses or powered-load tests were performed.

## Next physical checks

Keep the TECs disconnected. Measure the power board's sensor 3.3V rail relative to its signal GND. If it is missing, remove power before inspecting or reseating the interboard cable. The schematics number the two 10-pin connectors in opposite directions: MCU CN4 pins 1/2 carry 3.3V/GND, corresponding to power CN1 pins 10/9. Match net names and connector orientation, not identical pin numbers.

If the sensor rail is correct, measure the three sensor outputs at the power board, then at the MCU-side current inputs. Expected nominal outputs with zero current are 1.65V, 0.33V and 0.33V. Compare the physical voltages with the raw ADC report to locate a missing supply, sensor-output or harness path.

After current-sense integrity is restored, connect the three specified plate thermistors and verify plausible ambient-temperature readings. Offset calibration, current gain/limits, gate/drain waveforms, TEC polarity and thermal-loop behavior remain separate commissioning work. Useful gate testing requires physical waveform measurements; timer registers alone cannot verify the MOSFET stage.

## Artifacts and references

- Firmware SHA-256: `f950a2cf7655ea56860b56368bcea3b223671e11777de694e9c747cf969edbf0`.
- Desktop SHA-256: `eecf965c2905aaacb237e8c8f0768b79d55a005811e58793b2677376d9220188`.
- Evidence: `validation/powered-no-load.json`, `validation/powered-no-load-adc.json`, `validation/no-load-flash-verify.log`, `validation/no-load-output-registers.log`, `validation/no-load-command-check.json`.
- The previous full flash image was retained outside the repository as `../hardware/bench-before-no-load-2026-10-18.bin`.
- [ST high-density STM32F103 datasheet](https://www.st.com/resource/en/datasheet/stm32f103zd.pdf) and [RM0008](https://www.st.com/resource/en/reference_manual/rm0008-stm32f103xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf): internal reference characteristics and ADC1 channel 17/TSVREFE operation. Acquisition uses 239.5 cycles at 4MHz, with a bounded initialization settling delay. The nominal reference estimate does not assume a per-device factory VREFINT calibration constant.
