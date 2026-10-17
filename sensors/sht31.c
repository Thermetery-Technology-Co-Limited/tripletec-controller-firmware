#include "sht31.h"

#include <math.h>
#include <string.h>

/* Sensirion SHT3x-DIS: command table, CRC properties and signal conversion.
 * https://admin.sensirion.com/media/documents/213E6A3B/63A5A569/Datasheet_SHT3x_DIS.pdf
 * Maximum high-repeatability conversion duration is 15.5 ms. */
#define SHT31_WAIT_MS 16U
#define SHT31_TIMEOUT_MS 100U

uint8_t sht31_crc8(const uint8_t *data, size_t count)
{
    uint8_t crc = 0xFFU;
    size_t i;
    unsigned bit;
    if (data == NULL && count != 0U) {
        return 0U;
    }
    for (i = 0U; i < count; ++i) {
        crc ^= data[i];
        for (bit = 0U; bit < 8U; ++bit) {
            crc = (uint8_t)((crc & 0x80U) != 0U ?
                            ((unsigned)crc << 1U) ^ 0x31U :
                            (unsigned)crc << 1U);
        }
    }
    return crc;
}

bool sht31_decode(const uint8_t raw[6], float *temperature_c, float *rh_percent)
{
    uint16_t t;
    uint16_t rh;
    if (temperature_c != NULL) {
        *temperature_c = NAN;
    }
    if (rh_percent != NULL) {
        *rh_percent = NAN;
    }
    if (raw == NULL || temperature_c == NULL || rh_percent == NULL ||
        sht31_crc8(raw, 2U) != raw[2] || sht31_crc8(raw + 3, 2U) != raw[5]) {
        return false;
    }
    t = (uint16_t)((uint16_t)raw[0] << 8U) | raw[1];
    rh = (uint16_t)((uint16_t)raw[3] << 8U) | raw[4];
    *temperature_c = -45.0f + 175.0f * (float)t / 65535.0f;
    *rh_percent = 100.0f * (float)rh / 65535.0f;
    /* Sensor conversion can return -45..130 C; the controller applies its
     * narrower ambient/dew-point model limits independently. RH=0 is decoded
     * faithfully, then rejected by the logarithmic dew-point calculation. */
    return true;
}

bool sht31_init(sht31_t *d, const sht31_bus_t *bus, uint8_t address7)
{
    if (d == NULL) {
        return false;
    }
    memset(d, 0, sizeof(*d));
    d->temperature_c = NAN;
    d->rh_percent = NAN;
    if (bus == NULL || bus->write == NULL || bus->read == NULL ||
        (address7 != 0x44U && address7 != 0x45U)) {
        return false;
    }
    d->bus = *bus;
    d->address7 = address7;
    d->initialized = true;
    return true;
}

bool sht31_start(sht31_t *d, uint32_t now_ms)
{
    const uint8_t command[2] = {0x24U, 0x00U};
    if (d == NULL || !d->initialized || d->busy) {
        return false;
    }
    if (!d->bus.write(d->bus.context, d->address7, command, sizeof(command))) {
        d->valid = false;
        return false;
    }
    d->started_ms = now_ms;
    d->busy = true;
    return true;
}

sht31_status_t sht31_poll(sht31_t *d, uint32_t now_ms)
{
    uint8_t raw[6];
    uint32_t elapsed;
    if (d == NULL || !d->initialized) {
        return SHT31_ERROR;
    }
    if (!d->busy) {
        return SHT31_IDLE;
    }
    elapsed = now_ms - d->started_ms;
    if (elapsed < SHT31_WAIT_MS) {
        return SHT31_WAIT;
    }
    d->busy = false;
    if (elapsed > SHT31_TIMEOUT_MS ||
        !d->bus.read(d->bus.context, d->address7, raw, sizeof(raw)) ||
        !sht31_decode(raw, &d->temperature_c, &d->rh_percent)) {
        d->valid = false;
        return SHT31_ERROR;
    }
    d->valid = true;
    /* Conservative age includes measurement and any polling delay. */
    d->sample_timestamp_ms = d->started_ms;
    return SHT31_READY;
}
