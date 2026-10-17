#include "ads1220.h"

#include <string.h>

/* TI ADS1220, serial commands, configuration registers and data format:
 * https://www.ti.com/lit/ds/symlink/ads1220.pdf
 * A ready edge is required; also wait 55 ms so a stale/stuck-low DRDY cannot
 * cause an immediate read of the previous channel's conversion. */
#define ADS1220_MIN_WAIT_MS 55U
#define ADS1220_TIMEOUT_MS 150U

bool ads1220_build_config(size_t channel, uint8_t registers[4])
{
    static const uint8_t mux[ADS1220_CHANNEL_COUNT] = {2U, 4U, 5U};
    if (registers == NULL || channel >= ADS1220_CHANNEL_COUNT) {
        return false;
    }
    registers[0] = (uint8_t)((unsigned)mux[channel] << 4U) | 0x01U;
    registers[1] = 0x00U;
    registers[2] = 0x00U;
    registers[3] = 0x00U;
    return true;
}

bool ads1220_reg_command(bool write, unsigned first, unsigned count, uint8_t *command)
{
    if (command == NULL || first > 3U || count == 0U || count > 4U ||
        first + count > 4U) {
        return false;
    }
    *command = (uint8_t)((write ? 0x40U : 0x20U) | (first << 2U) | (count - 1U));
    return true;
}

bool ads1220_config_matches(const uint8_t registers[4], size_t channel)
{
    uint8_t expected[4];
    return registers != NULL && ads1220_build_config(channel, expected) &&
           memcmp(registers, expected, sizeof(expected)) == 0;
}

bool ads1220_decode24(const uint8_t raw[3], int32_t *sample)
{
    uint32_t word;
    if (sample == NULL) {
        return false;
    }
    *sample = 0;
    if (raw == NULL) {
        return false;
    }
    word = ((uint32_t)raw[0] << 16U) | ((uint32_t)raw[1] << 8U) | raw[2];
    /* Subtraction is defined for every 24-bit input; no implementation-defined
     * uint32->int32 sign-extension cast or signed shifts are needed. */
    *sample = (int32_t)(word & 0x7FFFFFU);
    if ((word & 0x800000U) != 0U) {
        *sample -= 8388608;
    }
    return true;
}

bool ads1220_init(ads1220_t *d, const ads1220_bus_t *bus)
{
    if (d == NULL) {
        return false;
    }
    memset(d, 0, sizeof(*d));
    if (bus == NULL || bus->transfer == NULL || bus->data_ready == NULL) {
        return false;
    }
    d->bus = *bus;
    d->initialized = true;
    return true;
}

bool ads1220_read_config(ads1220_t *d, uint8_t registers[4])
{
    uint8_t tx[5] = {0x23U, 0U, 0U, 0U, 0U};
    uint8_t rx[5];
    if (d == NULL || !d->initialized || registers == NULL ||
        !d->bus.transfer(d->bus.context, tx, rx, sizeof(tx))) {
        return false;
    }
    memcpy(registers, rx + 1, 4U);
    return true;
}

bool ads1220_start(ads1220_t *d, size_t channel, uint32_t now_ms)
{
    uint8_t tx[5] = {0x43U, 0U, 0U, 0U, 0U};
    uint8_t rx[5];
    uint8_t registers[4];
    const uint8_t start = ADS1220_CMD_START;
    if (d == NULL || !d->initialized || d->busy || channel >= ADS1220_CHANNEL_COUNT) {
        return false;
    }
    d->valid = false;
    d->configuration_verified = false;
    if (!ads1220_build_config(channel, tx + 1) ||
        !d->bus.transfer(d->bus.context, tx, rx, sizeof(tx)) ||
        !ads1220_read_config(d, registers) ||
        !ads1220_config_matches(registers, channel) ||
        !d->bus.transfer(d->bus.context, &start, rx, 1U) ||
        d->bus.data_ready(d->bus.context)) {
        return false;
    }
    d->configuration_verified = true;
    d->channel = channel;
    d->started_ms = now_ms;
    d->busy = true;
    return true;
}

ads1220_status_t ads1220_poll(ads1220_t *d, uint32_t now_ms)
{
    const uint8_t tx[4] = {ADS1220_CMD_RDATA, 0U, 0U, 0U};
    uint8_t rx[4];
    uint8_t registers[4];
    uint32_t elapsed;
    if (d == NULL || !d->initialized) {
        return ADS1220_ERROR;
    }
    if (!d->busy) {
        return ADS1220_IDLE;
    }
    elapsed = now_ms - d->started_ms;
    if (elapsed > ADS1220_TIMEOUT_MS) {
        d->busy = false;
        d->valid = false;
        return ADS1220_ERROR;
    }
    if (elapsed < ADS1220_MIN_WAIT_MS || !d->bus.data_ready(d->bus.context)) {
        return ADS1220_WAIT;
    }
    d->busy = false;
    /* Read sample before RREG: any SPI clock deasserts the DRDY pin. */
    if (!d->bus.transfer(d->bus.context, tx, rx, sizeof(tx)) ||
        !ads1220_decode24(rx + 1, &d->raw) ||
        !ads1220_read_config(d, registers) ||
        !ads1220_config_matches(registers, d->channel)) {
        d->valid = false;
        d->configuration_verified = false;
        return ADS1220_ERROR;
    }
    d->valid = true;
    d->sample_timestamp_ms = d->started_ms;
    return ADS1220_READY;
}
