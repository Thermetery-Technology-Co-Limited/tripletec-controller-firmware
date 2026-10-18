# XT60 9A software-trip policy — 2026-10-18

Version 0.2.2 selects 9A software current-trip thresholds for channels 2 and 3. Channel 1 remains unset. The limits are separate from commissioning: every limit-commissioning flag remains false, current measurements remain uncalibrated, and the physical bench output lock remains enabled.

## Behavior

- Configuring either XT60 threshold above 9A is rejected. Lower thresholds remain supported by the core.
- A measured magnitude at or above the selected threshold stops the affected channel, zeros duty/integral, records a latched fault and disarms it. This applies in regulated and unregulated modes. Recovery does not automatically rearm it.
- Selecting 9A does not commission the protection. Arming requires a positive commissioned threshold, calibrated valid/fresh measurements and the other interlocks. The bench application still rejects every ARM command unconditionally.
- Mode and margin changes preserve the selected thresholds and their uncommissioned state.
- A separately sequenced 32-byte firmware block reports the actual selected limits and commissioning mask. The desktop displays them beside the measured currents; legacy/malformed/stale policy remains unavailable and cannot authorize Start. Invalid policy data does not prevent STOP.

## Validation

Five native C test executables and 55 Python tests passed. Cortex-M3 compilation passed with warnings treated as errors. Source and packaged desktop smoke tests passed.

The native tests exercise both XT60 channels and both modes at positive and negative thresholds, exact 9A, adjacent representable values below/above 9A, lower 4.5A limits, channel isolation, latched disarm/manual rearm, uncommissioned defaults and rejected configuration above 9A. Host tests cover torn/malformed/unsafe current-policy blocks, stale policy, metadata address validation, STOP availability and GUI Start inhibits.

The image was programmed through Pico CMSIS-DAP and flash verification returned **Verified OK**. Live mode/margin/STOP/ARM-rejection checks passed. Initial, unregulated, changed-margin and restored snapshots all reported `[0,9,9]` amperes with commissioning mask `0`. The final state is regulated, dew point +3°C, stopped, all outputs locked off.

Independent register reads after those commands: GPIOA_CRL=`b4b22224`, GPIOA_ODR=`0000a010`, TIM2_CR1=`0`, TIM2_CCER=`0`. PA1/PA2/PA3 remain LOW GPIO outputs and TIM2 remains disabled.

## Physical limits

This implements sampled software shutdown, not a constant-current output. Current acquisition is still every 250ms, and calibration/noise investigation is outstanding. The circuit directly switches full supply voltage; a lower PWM duty does not guarantee a peak below 9A. The reported 320W electrical input at 12V is approximately 26.7A, beyond the XT60 channels' 0..10A measurement range. The software threshold does not resolve that hardware/load incompatibility, establish fault-clearing energy or justify connecting the TEC.

No TEC, dummy-load switching, real overcurrent or powered shutdown test was performed. The full commissioning requirements in [COMMISSIONING.md](COMMISSIONING.md) remain applicable.

Evidence: `validation/xt60-limit-flash-verify.log`, `validation/xt60-limit-command-check.json`, `validation/xt60-limit-status.json`, `validation/xt60-limit-output-registers.log`, `validation/xt60-limit-artifacts.json`. The prior full flash image is retained outside the repository as `../hardware/bench-before-xt60-limits-2026-10-18.bin`.
