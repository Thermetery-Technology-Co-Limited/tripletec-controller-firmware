# GUI and mode validation — 2026-10-18

Version 0.2.0 adds the desktop wrapper, acknowledged RAM commands, and a tested unregulated control mode. The programmed firmware still has the compile-time bench output lock.

## Passed checks

- Five native C test executables: dew point, regulated control, mode/current interlocks, command protocol, and sensor decoding/acquisition.
- 25 Python unit tests: telemetry decoding, command transport, GUI presentation/Start interlocks, and STOP queue cancellation.
- Complete Cortex-M3 build with `-Wall -Wextra -Werror`.
- Source GUI smoke test and packaged Windows executable smoke test, exit code 0.
- PyInstaller Windows package, including matching firmware metadata and installed Python/Tcl/Tk/PyInstaller license notices.
- Program and flash verification through Pico CMSIS-DAP: **Verified OK**.
- Live command integration: switch to unregulated, observe no temperature target, reject ARM with `BENCH_LOCKED`, STOP, adjust margin to 4.5°C, return to regulated and verify `target=dewpoint+4.5°C`, then restore regulated/3°C/stopped.
- Packaged GUI connected and continuously read live 160-byte telemetry through its bundled backend. No UART connection was needed.

The first integration snapshot read ambient 25.865°C, RH 61.317%, dew point 17.850°C, and regulated target 20.850°C. Both sensor acquisition error counters were zero. All plate thermistors remained open/invalid.

All applied duty values stayed zero. MEM-AP reads after the command checks independently confirmed PA1/PA2/PA3 GPIO output bits LOW, TIM2_CR1=0 and TIM2_CCER=0.

## Mode semantics

Regulated mode uses the dew-point margin and its 0°C minimum target. Unregulated mode deliberately skips both target floors and the PI loop, ramps toward 100% request, and keeps operating-temperature, freshness, calibrated-current, commissioned-current-limit, hardware and timing interlocks. Changing modes disarms every channel. Current limits default to zero, preventing arming until commissioning. Firmware ARM commands remain unconditionally rejected in this bench image.

The firmware's host lease is two seconds while armed. Desktop/backend work is serialized within a process. Use one host process per probe: close the GUI before running diagnostic CLI commands or flashing firmware.

## Artifacts

- Desktop: `build/gui/TripleTEC-Control.exe`, 13,502,630 bytes.
- Desktop SHA-256: `a690deba3a445ff7bcbe806b4b4f1dbfcfbe03a02fd4576a1ffc1355dc76af31`.
- Firmware SHA-256: `41b7df99dabef7539925dd6e3abb14b150c191c208a32fd5bcf13ec21cf2bca2`.
- Retained evidence: `validation/gui-mode-validation.json`, `validation/gui-flash-verify.log`, and `validation/gui-output-lock-registers.log`.

Windows screenshot capture was unavailable during this run, so no pixel-level visual review is claimed. The native window, smoke tests, packaged backend polling and command protocol were exercised. Powered TEC behavior remains outside this bench milestone.
