#ifndef TEC_SHT31_H
#define TEC_SHT31_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

uint8_t sht31_crc8(const uint8_t *data, size_t count);
bool sht31_decode(const uint8_t raw[6], float *temperature_c, float *rh_percent);

typedef struct {
    void *context;
    bool (*write)(void *context, uint8_t address7, const uint8_t *data, size_t count);
    bool (*read)(void *context, uint8_t address7, uint8_t *data, size_t count);
} sht31_bus_t;

typedef enum {
    SHT31_ERROR = -1,
    SHT31_IDLE = 0,
    SHT31_WAIT = 1,
    SHT31_READY = 2
} sht31_status_t;

typedef struct {
    sht31_bus_t bus;
    uint8_t address7;
    bool initialized;
    bool busy;
    bool valid;
    uint32_t started_ms;
    uint32_t sample_timestamp_ms;
    float temperature_c;
    float rh_percent;
} sht31_t;

bool sht31_init(sht31_t *device, const sht31_bus_t *bus, uint8_t address7);
/* High-repeatability single shot, no clock stretching (0x2400).
 * A good previous result remains valid while a normal measurement is pending;
 * any transport/CRC/timeout failure invalidates it immediately. */
bool sht31_start(sht31_t *device, uint32_t now_ms);
sht31_status_t sht31_poll(sht31_t *device, uint32_t now_ms);

#endif
