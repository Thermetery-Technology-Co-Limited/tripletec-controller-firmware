# Bench validation — 2026-10-18

This records the unpowered TEC-stage firmware milestone. The 8-pin 12V connectors were reported unplugged by the user. No powered TEC control or thermal-loop commissioning is claimed.

## Automated checks

`python scripts/build.py all` completed successfully:

- Native GCC 16.2.0: dew-point numerical/range tests passed.
- Native GCC 16.2.0: three-channel controller/fault/recovery/timing tests passed.
- Native GCC 16.2.0: SHT31/ADS1220/NTC/current conversion and transport-fault tests passed.
- Five Python telemetry protocol tests passed, including rejection of changed/odd sequence numbers and validation of the first payload across two snapshots.
- ARM GCC 15.2: complete Cortex-M3 build passed with `-Wall -Wextra -Werror`.

An independent source review covered the control core, sensor configuration/decoding, main/board integration, and the telemetry snapshot protocol. Review corrections were applied to the telemetry snapshot selection and STM32 high-density I2C final-three-byte erratum handling before the target build was programmed.

## Flash and live measurements

The original 262,144-byte flash was backed up to `../hardware/original-flash-2026-10-18.bin` and confirmed entirely erased (`0xFF`). Backup SHA-256: `3b874d3ba46c638fc3094f8e92fb744ca974893873f8885f54e23760f9b6311b`.

The bench ELF was programmed through Pico CMSIS-DAP and OpenOCD reported **Verified OK**. It was reset into the application. The image has a compile-time output lock and contains no PWM enable API.

The later live snapshot showed:

| Reading | Result |
|---|---|
| Uptime | 30,000 ms, without watchdog reset |
| Ambient | 25.585°C |
| RH | 62.016% |
| Calculated dew point | 17.766°C |
| Calculated target | 20.766°C |
| SHT31 acquisition errors | 0 |
| ADS1220 configuration/acquisition errors | 0 |
| Thermistors 1/2/3 | All invalid/open; raw codes around 6,745,300 |
| TEC output | All three zero; output lock asserted |

Nominal zero-load current telemetry was approximately −0.076A, 0.023A, and 0.096A. These show the offset/ADC readings; they are uncalibrated and are not a current-limit validation.

Generic MEM-AP register reads while the application ran independently verified:

| Register | Value | Meaning |
|---|---|---|
| GPIOA_CRL, 0x40010800 | 0xB4B22224 | PA1/PA2/PA3 are GPIO push-pull outputs |
| GPIOA_ODR, 0x4001080C | 0x0000A010 | PA1/PA2/PA3 output bits are all LOW |
| TIM2_CR1, 0x40000000 | 0 | Timer stopped |
| TIM2_CCER, 0x40000020 | 0 | Compare outputs disabled |

Logs and snapshots are in `build/stm32/flash-verify.log`, `output-register-verification.log`, and `bench-status-{initial,stable}.json`. A retained copy of the stable snapshot and output verification is in `validation/`.

## Remaining integration

Connect and calibrate the three cold-plate thermistors, then characterize the power stage, current protection, TEC polarity, thermal inertia, heatsinking, PWM frequency and PI gains before developing an enabled image. A 3°C requested margin still needs a measured overshoot/error budget. The bench image remains unable to energize TECs if power connectors are later inserted.
