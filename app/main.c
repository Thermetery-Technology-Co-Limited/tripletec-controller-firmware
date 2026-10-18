#include "board.h"
#include "controller.h"
#include "telemetry.h"
#include "control_protocol.h"
#include "sht31.h"
#include "ads1220.h"
#include "ntc.h"
#include "current.h"
#include "usb_device.h"
#include "usb_protocol.h"

#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#if !defined(BENCH_OUTPUT_LOCK) || BENCH_OUTPUT_LOCK != 1 || BOARD_TEC_OUTPUTS_LOCKED_OFF != 1
#error This image must hold all TEC outputs off.
#endif

volatile bench_telemetry_t g_telemetry;
volatile adc_diagnostics_telemetry_t g_adc_diagnostics;
volatile limit_configuration_telemetry_t g_limit_configuration;
volatile control_mailbox_t g_command_mailbox = {
    .magic = CONTROL_MAILBOX_MAGIC, .version = CONTROL_PROTOCOL_VERSION
};
static control_protocol_t command_protocol;
static tec_controller_t controller;
static tec_inputs_t inputs;
static tec_outputs_t outputs;
static sht31_t sht;
static ads1220_t ads;
static ntc_config_t ntc_config;
static current_config_t current_config[3];
static int32_t ntc_codes[3];
static uint16_t current_codes[3];
static float current_amperes[3];
static bool current_valid;
static board_adc_diagnostics_t adc_diagnostics;
static bool adc_acquired;
static bool adc_reference_plausible;
static int32_t estimated_vdda_mv = INT32_MIN;
static uint32_t adc_errors;
static uint32_t sht_errors;
static uint32_t ads_errors;
static uint32_t loop_count;
static usb_protocol_t usb_protocol;
static bool usb_enabled;
static bool usb_was_connected;
static uint32_t usb_session_generation;
static uint8_t usb_input[128];
static size_t usb_input_used;
static size_t usb_input_offset;

static bool sht_write(void *context, uint8_t address, const uint8_t *data, size_t count)
{
    (void)context;
    return board_i2c_write(address, data, count, 25U);
}

static bool sht_read(void *context, uint8_t address, uint8_t *data, size_t count)
{
    (void)context;
    return board_i2c_read(address, data, count, 25U);
}

static bool ads_transfer(void *context, const uint8_t *tx, uint8_t *rx, size_t count)
{
    bool ok;
    (void)context;
    board_ads1220_cs(true);
    ok = board_spi_transfer(tx, rx, count, 25U);
    board_ads1220_cs(false);
    return ok;
}

static bool ads_ready(void *context)
{
    (void)context;
    return board_ads1220_data_ready();
}

static int32_t milli(float value)
{
    if (!isfinite(value) || fabsf(value) > 2147483.0f) {
        return INT32_MIN;
    }
    return (int32_t)lroundf(value * 1000.0f);
}

static void publish(uint32_t now)
{
    bench_telemetry_t next = {0};
    adc_diagnostics_telemetry_t diagnostic = {0};
    limit_configuration_telemetry_t limits = {0};
    const uint32_t sequence = g_telemetry.seq;
    next.magic = UINT32_C(0x54454331);
    next.seq = sequence + 1U;
    next.schema = 2U;
    next.uptime_ms = now;
    next.outputs_locked = 1U;
    next.sht_errors = sht_errors;
    next.ads_errors = ads_errors;
    limits.magic = UINT32_C(0x5445434c);
    limits.seq = sequence + 1U;
    limits.schema = 1U;
    limits.uptime_ms = now;
    next.ambient_mc = inputs.ambient_valid ? milli(inputs.ambient_c) : INT32_MIN;
    next.rh_mpercent = inputs.ambient_valid ? milli(inputs.rh_percent) : INT32_MIN;
    next.dewpoint_mc = milli(outputs.dewpoint_c);
    next.target_mc = milli(outputs.target_c);
    if (inputs.ambient_valid && now - inputs.ambient_timestamp_ms <= 2000U) {
        next.valid_flags |= 1U;
    }
    if (ads.configuration_verified) {
        next.valid_flags |= 2U;
    }
    for (size_t i = 0U; i < 3U; ++i) {
        limits.current_limit_ma[i] = (uint32_t)milli(controller.config.current_limit_a[i]);
        if (controller.config.current_limit_commissioned[i]) {
            limits.commissioned_mask |= 1U << i;
        }
        const bool fresh = inputs.plate[i].valid && now - inputs.plate[i].timestamp_ms <= 2000U;
        next.plate_mc[i] = fresh ? milli(inputs.plate[i].temperature_c) : INT32_MIN;
        next.ntc_adc_raw[i] = ntc_codes[i];
        next.current_adc_raw[i] = current_codes[i];
        next.current_ma[i] = current_valid ? milli(current_amperes[i]) : INT32_MIN;
        next.applied_duty_milli[i] = 0U;
        next.active_faults[i] = outputs.channel[i].active_faults;
        next.desired_duty_milli[i] = (uint32_t)milli(outputs.channel[i].duty);
        if (controller.channel[i].armed) {
            next.armed_mask |= 1U << i;
        }
        if (fresh) {
            next.valid_flags |= 1U << (i + 2U);
        }
    }
    if (current_valid) {
        next.valid_flags |= 1U << 5;
    }
    next.loop_count = loop_count;
    next.button_pressed = board_button_pressed() ? 1U : 0U;
    next.mode = (uint32_t)controller.mode;
    next.margin_mc = milli(controller.config.dewpoint_margin_c);
    next.command_ack_seq = command_protocol.ack_seq;
    next.command_result = (uint32_t)command_protocol.result;
    diagnostic.magic = UINT32_C(0x54454344);
    diagnostic.seq = sequence + 1U;
    diagnostic.schema = 1U;
    diagnostic.uptime_ms = now;
    diagnostic.vrefint_adc_raw = adc_diagnostics.vrefint_raw;
    diagnostic.estimated_vdda_mv = estimated_vdda_mv;
    diagnostic.flags = (adc_acquired ? 1U : 0U) | (adc_reference_plausible ? 2U : 0U);
    diagnostic.adc_errors = adc_errors;
    g_telemetry.seq = sequence + 1U;
    g_adc_diagnostics.seq = sequence + 1U;
    g_limit_configuration.seq = sequence + 1U;
    __asm volatile("dmb" ::: "memory");
    g_telemetry = next;
    g_adc_diagnostics = diagnostic;
    g_limit_configuration = limits;
    __asm volatile("dmb" ::: "memory");
    g_telemetry.seq = sequence + 2U;
    g_adc_diagnostics.seq = sequence + 2U;
    g_limit_configuration.seq = sequence + 2U;
}

static void disarm_all(void)
{
    for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        (void)tec_controller_set_armed(&controller, i, false);
    }
    board_outputs_force_off();
}

static control_result_t apply_command(void *context, uint32_t opcode,
                                       int32_t value, uint32_t now_ms)
{
    (void)context;
    (void)now_ms;
    switch (opcode) {
    case CONTROL_STOP:
        disarm_all();
        return CONTROL_OK;
    case CONTROL_HEARTBEAT:
        return CONTROL_OK;
    case CONTROL_SET_MODE:
        disarm_all();
        return tec_controller_set_mode(&controller, (tec_mode_t)value) ?
               CONTROL_OK : CONTROL_INTERLOCK;
    case CONTROL_SET_MARGIN: {
        tec_config_t config = controller.config;
        const tec_mode_t mode = controller.mode;
        config.dewpoint_margin_c = (float)value / 1000.0f;
        disarm_all();
        if (!tec_controller_init(&controller, &config) ||
            !tec_controller_set_mode(&controller, mode)) {
            return CONTROL_INTERLOCK;
        }
        return CONTROL_OK;
    }
    case CONTROL_ARM:
        /* This image has no output-enable path. Future commissioned builds
         * additionally need current, wiring, fresh-sensor and host-link gates.
         */
        disarm_all();
        return CONTROL_BENCH_LOCKED;
    default:
        return CONTROL_BAD_OPCODE;
    }
}

static control_result_t usb_apply(void *context, uint8_t opcode,
                                  int32_t value, uint32_t sequence)
{
    (void)context;
    (void)sequence; /* USB response sequence is independent of the SWD mailbox. */
    if (!usb_device_connected() ||
        usb_device_session_generation() != usb_session_generation) {
        disarm_all();
        return CONTROL_INTERLOCK;
    }
    const uint32_t now = board_millis();
    command_protocol.last_valid_ms = now; /* Only validated non-STATUS requests call this. */
    const control_result_t result = apply_command(NULL, opcode, value, now);
    tec_controller_step(&controller, &inputs, now, &outputs);
    return result;
}

static void copy_volatile_bytes(uint8_t *destination, volatile const void *source, size_t count)
{
    volatile const uint8_t *bytes = source;
    for (size_t i = 0U; i < count; ++i) {
        destination[i] = bytes[i];
    }
}

static void usb_snapshot(void *context, uint8_t payload[USB_PROTOCOL_PAYLOAD_SIZE])
{
    (void)context;
    /* The publisher and this callback both run exclusively in main context.
     * USB interrupts only enqueue stack events; they do not touch controller state.
     */
    publish(board_millis());
    _Static_assert(sizeof(g_telemetry) + sizeof(g_adc_diagnostics) +
                   sizeof(g_limit_configuration) == USB_PROTOCOL_PAYLOAD_SIZE,
                   "USB telemetry frame changed");
    copy_volatile_bytes(payload, &g_telemetry, sizeof(g_telemetry));
    copy_volatile_bytes(payload + sizeof(g_telemetry), &g_adc_diagnostics,
                        sizeof(g_adc_diagnostics));
    copy_volatile_bytes(payload + sizeof(g_telemetry) + sizeof(g_adc_diagnostics),
                        &g_limit_configuration, sizeof(g_limit_configuration));
}

static bool usb_enqueue(void *context, const uint8_t *bytes, size_t length)
{
    (void)context;
    return usb_device_write_packet(bytes, length);
}

static void usb_service(void)
{
    if (!usb_enabled) {
        return;
    }
    usb_device_task();
    const uint32_t generation = usb_device_session_generation();
    const bool connected = usb_device_connected();
    if (generation != usb_session_generation || (usb_was_connected && !connected)) {
        usb_session_generation = generation;
        disarm_all();
        usb_input_used = 0U;
        usb_input_offset = 0U;
        usb_protocol_init(&usb_protocol, usb_apply, usb_snapshot, usb_enqueue, NULL);
        tec_controller_step(&controller, &inputs, board_millis(), &outputs);
        publish(board_millis());
    }
    usb_was_connected = connected;
    if (!connected || !usb_protocol_poll(&usb_protocol)) {
        return;
    }
    if (usb_input_offset == usb_input_used) {
        usb_input_used = usb_device_read(usb_input, sizeof(usb_input));
        usb_input_offset = 0U;
    }
    if (usb_input_offset < usb_input_used) {
        usb_input_offset += usb_protocol_feed(&usb_protocol, usb_input + usb_input_offset,
                                               usb_input_used - usb_input_offset);
    }
}

static void print_status(void)
{
    char text[256];
    (void)snprintf(text, sizeof(text),
        "TEC-BENCH v2 up=%lu lock=1 ambient_mC=%ld RH_mPct=%ld dew_mC=%ld target_mC=%ld "
        "plate_mC=%ld,%ld,%ld valid=0x%lx sht_err=%lu ads_err=%lu PWM=0,0,0\r\n",
        (unsigned long)g_telemetry.uptime_ms, (long)g_telemetry.ambient_mc,
        (long)g_telemetry.rh_mpercent, (long)g_telemetry.dewpoint_mc,
        (long)g_telemetry.target_mc, (long)g_telemetry.plate_mc[0],
        (long)g_telemetry.plate_mc[1], (long)g_telemetry.plate_mc[2],
        (unsigned long)g_telemetry.valid_flags, (unsigned long)sht_errors,
        (unsigned long)ads_errors);
    board_uart_write(text);
}

static void uart_commands(void)
{
    static char command[32];
    static size_t used;
    uint8_t byte;
    /* Bound RX service even if an external peer floods the UART. */
    for (unsigned count = 0U; count < 32U && board_uart_read_nonblocking(&byte); ++count) {
        if (byte == '\r' || byte == '\n') {
            command[used] = '\0';
            if (strcmp(command, "status") == 0) {
                print_status();
            } else if (used != 0U) {
                board_uart_write("Bench commands: status, help. TEC outputs are locked OFF.\r\n");
            }
            used = 0U;
        } else if (byte >= 32U && byte <= 126U && used < sizeof(command) - 1U) {
            command[used++] = (char)byte;
        }
    }
}

int main(void)
{
    tec_config_t config;
    const sht31_bus_t sht_bus = {.context = NULL, .write = sht_write, .read = sht_read};
    const ads1220_bus_t ads_bus = {.context = NULL, .transfer = ads_transfer, .data_ready = ads_ready};
    uint32_t last_sht = 0U, last_control = 0U, last_current = 0U;
    uint32_t last_publish = 0U, last_uart = 0U;
    size_t next_ads_channel = 0U;

    board_init();
    tec_config_defaults(&config);
    (void)tec_controller_init(&controller, &config);
    control_protocol_init(&command_protocol);
    inputs.hardware_verified = false; /* No runtime command can remove bench lock. */
    (void)sht31_init(&sht, &sht_bus, 0x44U);
    (void)ads1220_init(&ads, &ads_bus);
    ntc_config_defaults(&ntc_config);
    for (size_t i = 0U; i < 3U; ++i) {
        (void)current_config_defaults(i, &current_config[i]);
        current_amperes[i] = NAN;
        ntc_codes[i] = INT32_MIN;
    }
    /* Both sensors have been powered throughout USB attachment. This explicit
     * delay also covers their power-up time after a standalone cold start. */
    board_delay_ms(20U);
    board_watchdog_start();
    tec_controller_step(&controller, &inputs, board_millis(), &outputs);
    publish(board_millis());
    usb_protocol_init(&usb_protocol, usb_apply, usb_snapshot, usb_enqueue, NULL);
    usb_enabled = usb_device_init();
    usb_session_generation = usb_device_session_generation();
    board_uart_write("TripleTECDriver bench firmware: dew point +3C; outputs permanently OFF.\r\n");

    for (;;) {
        uint32_t now = board_millis();
        board_outputs_force_off();
        ++loop_count;

        if (!sht.busy && (now - last_sht >= 500U || last_sht == 0U)) {
            last_sht = now;
            if (!sht31_start(&sht, now)) {
                ++sht_errors;
                inputs.ambient_valid = false;
            }
        }
        if (sht.busy) {
            const sht31_status_t status = sht31_poll(&sht, now);
            if (status == SHT31_READY) {
                inputs.ambient_c = sht.temperature_c;
                inputs.rh_percent = sht.rh_percent;
                inputs.ambient_timestamp_ms = sht.sample_timestamp_ms;
                inputs.ambient_valid = true;
            } else if (status == SHT31_ERROR) {
                inputs.ambient_valid = false;
                ++sht_errors;
            }
        }

        if (!ads.busy) {
            if (!ads1220_start(&ads, next_ads_channel, now)) {
                inputs.plate[next_ads_channel].valid = false;
                ++ads_errors;
                next_ads_channel = (next_ads_channel + 1U) % 3U;
            }
        } else {
            const ads1220_status_t status = ads1220_poll(&ads, now);
            if (status == ADS1220_READY) {
                ntc_result_t ntc;
                ntc_codes[ads.channel] = ads.raw;
                inputs.plate[ads.channel].valid = ntc_from_ads1220(ads.raw, &ntc_config, &ntc);
                if (inputs.plate[ads.channel].valid) {
                    inputs.plate[ads.channel].temperature_c = ntc.temperature_c;
                    inputs.plate[ads.channel].timestamp_ms = ads.sample_timestamp_ms;
                }
                next_ads_channel = (ads.channel + 1U) % 3U;
            } else if (status == ADS1220_ERROR) {
                inputs.plate[ads.channel].valid = false;
                ++ads_errors;
                next_ads_channel = (ads.channel + 1U) % 3U;
            }
        }

        now = board_millis();
        if (now - last_current >= 250U) {
            last_current = now;
            adc_acquired = board_adc_read_diagnostics(&adc_diagnostics, 25U);
            estimated_vdda_mv = adc_acquired && adc_diagnostics.vrefint_raw != 0U ?
                (int32_t)((UINT32_C(1200) * 4095U) / adc_diagnostics.vrefint_raw) : INT32_MIN;
            adc_reference_plausible = adc_acquired && estimated_vdda_mv >= 2800 &&
                                      estimated_vdda_mv <= 3600;
            const bool adc_valid = adc_acquired && adc_reference_plausible;
            if (!adc_valid) {
                ++adc_errors;
            }
            current_valid = adc_valid;
            for (size_t i = 0U; i < 3U; ++i) {
                current_codes[i] = adc_acquired ? adc_diagnostics.current_raw[i] : 0U;
                current_result_t current;
                if (adc_valid && current_from_adc12(current_codes[i], 3.3f,
                                                         &current_config[i], &current)) {
                    current_amperes[i] = current.amperes;
                    inputs.current[i].amperes = current.amperes;
                    inputs.current[i].valid = current.within_nominal_range;
                    inputs.current[i].calibrated = !current.nominal_only;
                    inputs.current[i].timestamp_ms = now;
                } else {
                    current_valid = false;
                    current_amperes[i] = NAN;
                    inputs.current[i].valid = false;
                    inputs.current[i].calibrated = false;
                }
            }
        }
        if (control_protocol_poll(&command_protocol, &g_command_mailbox,
                                   apply_command, NULL, now)) {
            tec_controller_step(&controller, &inputs, now, &outputs);
            publish(now); /* ACK describes applied state, never just receipt. */
            last_control = now;
            last_publish = now;
        }
        /* A future commissioned output build must keep receiving validated
         * protocol packets while armed. A read alone does not extend the lease.
         * CRC detects torn writes; this local debug link provides no identity
         * authentication. The bench image still rejects every ARM request.
         */
        bool any_armed = false;
        for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
            any_armed |= controller.channel[i].armed;
        }
        if (control_protocol_lease_expired(&command_protocol, any_armed, now, 2000U)) {
            disarm_all();
            tec_controller_step(&controller, &inputs, now, &outputs);
            publish(now);
            last_control = now;
            last_publish = now;
        }
        if (now - last_control >= 50U) {
            last_control = now;
            tec_controller_step(&controller, &inputs, now, &outputs);
        }
        if (now - last_publish >= 250U) {
            last_publish = now;
            publish(now);
        }
        if (now - last_uart >= 1000U) {
            last_uart = now;
            print_status();
        }
        uart_commands();
        usb_service();
        board_outputs_force_off();
        board_watchdog_feed();
        board_delay_ms(1U);
    }
}
