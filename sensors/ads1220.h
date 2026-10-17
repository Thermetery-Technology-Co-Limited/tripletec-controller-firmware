#ifndef TEC_ADS1220_H
#define TEC_ADS1220_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define ADS1220_CHANNEL_COUNT 3U
#define ADS1220_REFERENCE_V 2.048f
#define ADS1220_CMD_RESET 0x06U
#define ADS1220_CMD_START 0x08U
#define ADS1220_CMD_RDATA 0x10U

/* Logical channels 0,1,2 are AIN0-AIN3, AIN1-AIN3, AIN2-AIN3. */
bool ads1220_build_config(size_t channel, uint8_t registers[4]);
bool ads1220_reg_command(bool write, unsigned first_register, unsigned count,
                         uint8_t *command);
bool ads1220_config_matches(const uint8_t registers[4], size_t channel);
bool ads1220_decode24(const uint8_t raw[3], int32_t *sample);

typedef struct {
    void *context;
    /* One complete CS-low transaction, MSB first, SPI mode 1; both buffers
     * are nonnull. Callback must restore CS high even if a transfer fails. */
    bool (*transfer)(void *context, const uint8_t *tx, uint8_t *rx, size_t count);
    bool (*data_ready)(void *context); /* true when dedicated DRDY pin is low */
} ads1220_bus_t;

typedef enum {
    ADS1220_ERROR = -1,
    ADS1220_IDLE = 0,
    ADS1220_WAIT = 1,
    ADS1220_READY = 2
} ads1220_status_t;

typedef struct {
    ads1220_bus_t bus;
    bool initialized;
    bool busy;
    bool valid;
    bool configuration_verified;
    size_t channel;
    int32_t raw;
    uint32_t started_ms;
    uint32_t sample_timestamp_ms;
} ads1220_t;

/* Init only binds callbacks. Board must observe datasheet power-up/reset
 * timing before start; this driver sends no reset and contains no sleeps. */
bool ads1220_init(ads1220_t *device, const ads1220_bus_t *bus);
bool ads1220_read_config(ads1220_t *device, uint8_t registers[4]);
/* Configures gain1, PGA bypass, internal2.048V, normal20SPS, single shot,
 * IDAC/burnout/low-side switch off. Verifies all four configuration bytes. */
bool ads1220_start(ads1220_t *device, size_t channel, uint32_t now_ms);
ads1220_status_t ads1220_poll(ads1220_t *device, uint32_t now_ms);

#endif
