# Checks before connecting a TEC

Status on 2026-10-18: **not commissioned; retain the compile-time output lock**. TECs remain disconnected; thermistors on CN2/CN3 are connected and reporting temperatures, with CN1 open. The operator has a multimeter and confirms the external 12V PSU is fixed-output, with no adjustable constant-current limit. No oscilloscope or adjustable current-limited bench supply is available.

The selected software trip thresholds are now unset/9A/9A. Channels 2/3 reject configuration above 9A and trip inclusively at a sampled magnitude of 9A in both modes. Every current-limit commissioning flag remains false. This does not limit instantaneous current, change current acquisition speed, validate the power path or enable an output. A roughly 27A full-voltage on-pulse remains outside the XT60 sensors' measurement range even at low average duty.

## Current blockers

Latest update: the operator identified and secured a loose 10-pin connection. The subsequent 60-snapshot check over 34.25 seconds observed no near-ground current-signal dropouts; nominal mean voltages were 1.6455V, 0.3341V and 0.3348V. All acquisition error counters stayed zero. This supports the connector as the cause of the missing supply, without proving long-term mechanical retention. Channels 2/3 still failed the conservative stability screen: spans were 88.6mV and 66.1mV. Investigate noise and establish stable baselines before calibration; do not treat offset adjustment as a remedy for noise. Thermistors, load/power-path compatibility and active switching validation remain outstanding. Evidence: `validation/secured-harness-check-2026-10-18.json`.

Earlier observations: the external current-sense signals briefly recovered to nominal zero-current levels after the board was moved, then returned near ground with the 10-pin cable reportedly connected and untouched. A 60-snapshot observation covering 34.25 seconds found means of approximately 0.0154V, 0.0094V and 0.0144V. The internal-reference ADC check passed throughout; its error count stayed zero. That temporary recovery alone did not resolve the issue; the loose connector was subsequently identified as described above.

The operator reports that the 12V XT60 cooler assembly drew approximately 320W when directly connected to an external PSU. At 12V that is approximately 26.7A. The drawn XT60 channels 2/3 use ACS725-10AU sensors with a specified 0..10A measurement range. They cannot be assumed to monitor or protect this full load. Connector type does not establish the channel's safe rating. Channel 1's wider current-sensor range also does not establish the ratings of its fuse, PCB, wiring or thermal design. Confirm actual supply voltage, load identity and fitted parts during the engineer's power-path review.

The fixed-output PSU does not provide the missing current regulation. A firmware trip, reduced PWM duty or faster ADC acquisition alone cannot guarantee a 9A peak ceiling on this direct-switching stage. Before energizing the reported load, establish appropriately rated hardware current regulation/limiting for each XT60 channel and verify its transient behavior, sensor calibration and shutdown on a dummy load. A shared supply's total-current limit does not by itself establish independent per-channel limits; a fuse does not regulate operating current. Any future firmware current governor must be designed around the verified power stage and PWM-synchronized acquisition. Keep the output lock until this hardware and switching validation is complete.

## Work possible with the multimeter and locked firmware

1. Remove all power (12V, USB and any supply through the Pico) before inspection, resistance or continuity measurements. Mount the boards on rigid insulating supports and inspect undersides, contacts and solder joints. Locate the intermittent sensor supply/signal fault; do not bypass a short with a supply jumper.
2. Verify the actual harness against net names. Power CN1 pin10 is 3.3V and pin9 is signal GND; MCU CN4 uses pins1/2 for those nets. Connector numbering is reversed between drawings. Verify each sensor pin8 VCC and pin5 GND, plus current-output continuity to the corresponding MCU input. Use one known ground reference for voltage comparisons.
3. Recheck rails with TECs disconnected and outputs locked. A static 3.3V reading at the connector is insufficient if sensor VCC or the zero-current signals disappear. Require reproducible correct measurements and a sustained diagnostic log after the physical cause has been corrected. Do not calibrate missing or unstable signals into zero.
4. Connect the specified 100k/B3950 thermistors. Confirm channel identity by gently warming each probe and compare room-temperature readings with an independent reference. Verify invalid acquisition when disconnected. A short-input acquisition test can be arranged using a fitted link while all power is off, then observed after restoring power with outputs locked. Remove the link with power off before restoring the probe. These checks validate acquisition/fault reporting, not an energized shutdown.
5. With no TECs attached, measure each MOSFET gate relative to its own source: the static off-state should be near 0V. Confirm driver supply and power polarity. A multimeter cannot rule out short startup/reset pulses or verify switching transitions. An unloaded floating drain/TEC-negative terminal does not prove the transistor is on or off.

## Measurements requiring bench equipment

- Obtain an adjustable current-limited supply and a suitably rated resistive dummy load, plus an oscilloscope or an engineer able to perform the switching tests. Do not use a TEC as the first test load.
- Verify physical gate-to-source and drain behavior during power-up/down in both supply orders, reset, MCU power loss, control-link loss and STOP. The design includes 12V UCC27517 drivers U2/U6/U7 between MCU PWM signals and the power-board gate nets; it is not a direct 3.3V MOSFET drive.
- In a separately reviewed, bounded test configuration, switch one dummy-load channel at a time. Check actual current, gate amplitude/edges, drain voltage, ringing, transistor temperature, current measurement zero/gain/polarity and physical shutdown behavior. The current firmware cannot perform active output tests.
- Establish suitable current limits from the exact TEC, fuse, wiring, connector, PCB and thermal ratings. This stage directly switches 12V; a lower duty setting does not guarantee a low peak current during each on-pulse. Validate current sampling against the PWM waveform rather than assuming a slowly sampled average detects peaks. Include external electrical fault protection.
- Use appropriately rated current-measurement equipment. Do not place a multimeter's 10A current input in series with the approximately 27A cooler assembly.

## Thermal prerequisites for the first TEC test

Confirm module model, hot/cold orientation and electrical polarity. Install the manufacturer-specified heatsink/cold-plate interfaces, mounting pressure and fan/pump before energizing. The hot side must reject both transported heat and electrical input power. Provide independent hot-side temperature monitoring and an overtemperature cutoff; the current firmware's three thermistor channels measure cold plates and no fan/pump feedback is implemented.

After the preceding checks pass, start one assembled channel under controlled current in regulated mode. Characterize thermal lag, shutdown response and undershoot before assessing the dew-point margin. Full-power unregulated mode comes after electrical and thermal commissioning. The 3°C margin alone is not a condensation guarantee.

## Firmware protection limits

The current image rejects every ARM request, keeps `hardware_verified=false`, forces PA1/PA2/PA3 LOW before C initialization and throughout the loop, and leaves TIM2 disabled. These protections currently prevent commanded power output. Register reads confirm the static state; they do not establish MOSFET health or capture transient waveforms.

The control core has current/freshness/temperature/configuration trips and disarms on faults, but current is acquired every 250ms. The host lease is 2s and the watchdog approximately 4s. These are not fast short-circuit protection. Current calibration flags and all limit-commissioning flags remain false; selected limits are unset/9A/9A. PI gains, duty ceiling and temperature bounds remain provisional. A future enabled firmware requires a separate commissioning review, physically validated shutdown, suitable load limits and explicit thermal protection. It must not be enabled simply because a diagnostic report looks plausible.

## Evidence and sources

- Latest summary: `validation/pre-tec-readiness-2026-10-18.json`. Complete local samples remain in `build/pre-tec-soak-2026-10-18.json`.
- The latest static register check again read GPIOA_CRL=`b4b22224`, GPIOA_ODR=`0000a010`, TIM2_CR1=`0`, TIM2_CCER=`0`. The GUI was restarted after exclusive probe access.
- [Allegro ACS725 datasheet](https://www.allegromicro.com/-/media/files/datasheets/acs725-datasheet.pdf): selection guide and 10AU characteristics specify channel measurement ranges.
- [TI UCC27517 datasheet](https://www.ti.com/lit/ds/symlink/ucc27517.pdf): driver behavior and ratings; implementation still requires physical validation.
- [Laird thermoelectric handbook](https://lairdthermal.com/sites/default/files/ckfinder/files/resources/Handbooks/Thermoelectric-Handbook-1120.pdf): module ratings, heat rejection and assembly considerations. The installed module manufacturer's requirements govern its assembly.
