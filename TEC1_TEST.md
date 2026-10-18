# Operator-controlled TEC1 switching test

Desktop 0.3.1 adds **Test TEC1 (1 ms)** below channel telemetry. Each click requests one full-voltage pulse on TEC1/XT90, with hardware-timed automatic off and no automatic repeat. This is a switching/current-response test; a 1ms pulse is too short to establish cooling polarity or temperature regulation. The operator controls activation. Normal Start remains locked, and TEC1's operating current threshold stays unset.

Use the existing Pico SWD connection, attached cold-face probe and operating hot-side cooling. The application requires fresh, stopped regulated-bench readings, a plausible near-zero current baseline, and probe 1 more than 1°C above the dew-point +3°C target. It rejects another firmware image. The initial physical check measured 0V across connected TEC1 while stopped, with the computer PSU's 12V present.

The test briefly pauses the CPU at the idle-loop watchdog function of the exact known firmware. It sets TIM2 channel 2 on PA1 to positive PWM2/one-pulse mode: 72MHz timer clock, prescaler 71, CCR2=1, ARR=1000. The nominal high interval is 1000µs after a 1µs delay. TIM2's debug-freeze bit is cleared, so host disconnection or a halted CPU cannot extend the pulse by freezing this timer. Channels 2/3 remain GPIO LOW. Normal firmware is not flashed or modified.

ADC1 channel 10 is triggered by TIM2_CC2, then DMA captures 128 samples including the return toward zero. The result displays the nominal peak current, explicitly uncalibrated. This does not introduce constant-current regulation or commission any current trip.

Cleanup disables the timer output, restores GPIO LOW, ADC/DMA configuration and temporary RAM, and resumes the application. A failed or uncertain action is never automatically retried; recovery contains only shutdown/restore operations. Connecting, polling, opening the GUI, and automated smoke tests never issue the real pulse.

The ordinary duty table describes normal firmware output; the separate test result describes the brief diagnostic pulse. Evidence is saved under `%LOCALAPPDATA%\TripleTECControl\tests` as JSON plus the OpenOCD log.

Verification before delivery: 66 targeted host/GUI tests, source and packaged GUI smoke checks, and a live dry run with PA1 held in GPIO LOW throughout. The dry run verified timer completion, 128 ADC samples, LOW pins and resumed telemetry. The agent did not trigger an energized pulse; that action is left to the operator's GUI button.

The operator subsequently triggered the first pulse. The capture reported timer complete, LOW output pins afterward, 128 current samples returning near baseline, and a nominal uncalibrated peak of 8.58A. Telemetry resumed with probe 1 approximately 27.33°C and normal control still locked. Saved evidence: `validation/tec1-operator-pulse-2026-10-18.json` and `.log`. This result does not establish sustained current or temperature regulation.

Reference: [ST RM0008, timer one-pulse mode](https://www.st.com/resource/en/reference_manual/rm0008-stm32f103xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf). The pin map, debug freeze bit and ADC trigger are also checked against the pinned STM32 headers and the live dry run.
