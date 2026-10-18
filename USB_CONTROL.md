# Direct USB-C control

The 0.3.0 firmware and desktop implement native STM32 USB CDC ACM. This is a control/status interface, not a firmware updater or DFU bootloader. The Pico remains useful for programming and recovery.

## Required board connection

STM32F103 requires an external **1.5kΩ pull-up from USB D+ to 3.0–3.6V**, normally the board's 3.3V rail. The supplied schematic connects PA12 through R7 (18Ω) to `UD+`, but does not show this pull-up. PA11/R8 is D−. The 5.1kΩ USB-C CC resistors serve a different purpose and do not replace it. Do not connect the D+ pull-up to USB's 5V supply or substitute the MCU's weak GPIO pull-up.

An engineer should confirm the actual board/pad/net and fit the missing component with all power removed, including USB and any supply through the Pico. For product hardware, the attach pull-up must be coordinated with VBUS presence; a controlled pull-up and VBUS detection also permit reliable software detach/reconnect. The prototype has no verified VBUS input or controllable pull-up and does not claim either capability. A fixed pull-up can require a physical cable replug after firmware reset.

The operator has a known data cable but no 1.5kΩ resistor available. After flashing, a known-data-cable replug still produced no STM32 device. USB peripheral/clock checks pass and D+ remains low. This is consistent with a missing pull-up, but does not independently prove the PCB/cable data path. **Do not remove the Pico for the independence test yet.**

## Connect and verify

1. Keep all TECs disconnected and the output-locked firmware in place. Correct/verify the USB hardware first.
2. Attach the controller's USB-C port with a data cable. Windows should enumerate a CDC serial port under the development VID/PID `CAFE:4001`, product `TripleTEC Control`. The STM32's UID supplies the serial descriptor. This development identity needs replacement before product distribution.
3. Close other clients, then run `python -m scripts.check_usb_link --list`. This never scans or opens unrelated COM devices.
4. Run `python -m scripts.check_usb_link --port COMx`. It uses USB exclusively, verifies live status advancement, mode/margin/STOP acknowledgements, rejected ARM, retained 9A XT60 trips, and restores regulated/+3°C/stopped. It never uses Pico/SWD fallback.
5. Launch `python -m gui --transport usb --port COMx --connect`, or select STM32 USB-C in the packaged GUI. The displayed transport must be USB-C.
6. Only after a USB-only command check passes, disconnect the Pico and repeat `python -m scripts.check_usb_link --port COMx --status-only` with the GUI closed, or confirm fresh GUI readings. Recheck STOP/settings through USB with the TEC output lock still in place.

The current run has not reached step 2. Software and fallback checks do not establish physical USB operation or independence from the Pico.

## Application protocol and failure behavior

Requests are 20-byte little-endian frames: `TECQ`, version u8=1, opcode u8, reserved u16=0, nonzero sequence u32, value i32, CRC32 u32 over the first 16 bytes. Opcodes are STATUS=0, STOP=1, mode=2, margin=3, ARM=4 and heartbeat=5, with the same allowed values as the SWD command mailbox.

Responses are 240 bytes: `TECR`, version u8=1, result u8, payload size u16=224, request sequence u32, main telemetry160 + ADC diagnostics32 + selected-current-policy32, then CRC32 over the preceding236 bytes. CRC is ISO-HDLC (`0xEDB88320`, initial/final XOR `0xFFFFFFFF`). USB request sequence and response result are separate from the SWD mailbox acknowledgement fields inside telemetry.

The firmware uses fixed buffers and bounds USB events per main-loop service. It validates CRC, reserved fields, opcodes, values and forward sequence progression before applying commands. An exact replay of the most recent packet returns the cached response without applying it again. Backpressure retains input instead of losing request fragments. STATUS does not renew the two-second host lease; validated control/heartbeat packets do. Session changes, DTR transitions, bus reset and suspend/resume clear parser state and disarm; reconnect never arms outputs. Without verified VBUS sensing, cable removal is not detected independently: bus suspend/session handling and the host lease provide the available loss-of-connection response, which still needs physical verification. The host explicitly toggles DTR to start a fresh session and never retries an uncertain command or falls back to SWD after selecting USB.

Both transports reach the same bench handler: every ARM request is rejected and PA1/PA2/PA3 remain LOW. The 9A XT60 settings remain uncommissioned sampled trips, not hardware current regulation.

## Sources

- [ST STM32F103RC datasheet](https://www.st.com/resource/en/datasheet/stm32f103rc.pdf): USB electrical requirements and clock/peripheral limits.
- [ST AN4879 USB FAQ](https://www.st.com/content/st_com/en/technical-documents/an4879-introduction-to-usb-hardware-and-pcb-guidelines-using-stm32-mcus-stmicroelectronics_asset/chunk-005-topic-an4879-faqs-frequently-asked-questions.html): device-detection pull-up and clock requirements.
- [ST RM0008](https://www.st.com/resource/en/reference_manual/cd00171190-stm32f101-103-105-107-stm32f100-series-armbased-32bit-mcus-stmicroelectronics.pdf): clock tree and USB peripheral.
- [Pinned dependency sources and licenses](third_party/README.md).
