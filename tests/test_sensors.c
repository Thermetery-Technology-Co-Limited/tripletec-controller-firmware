#include "sht31.h"
#include "ads1220.h"
#include "ntc.h"
#include "current.h"

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    uint8_t reply[6];
    bool fail_write;
    bool fail_read;
    unsigned writes;
    unsigned reads;
} fake_i2c_t;

static bool fake_i2c_write(void *context, uint8_t address, const uint8_t *data, size_t n)
{
    fake_i2c_t *f = context;
    assert(address == 0x44U && n == 2U);
    assert(data[0] == 0x24U && data[1] == 0x00U);
    ++f->writes;
    return !f->fail_write;
}

static bool fake_i2c_read(void *context, uint8_t address, uint8_t *data, size_t n)
{
    fake_i2c_t *f = context;
    assert(address == 0x44U && n == 6U);
    ++f->reads;
    memcpy(data, f->reply, n);
    return !f->fail_read;
}

static void test_sht31(void)
{
    const uint8_t vector[2] = {0xBEU, 0xEFU};
    uint8_t raw[6] = {0x66U, 0x66U, 0x93U, 0x80U, 0x00U, 0xA2U};
    const uint8_t endpoints[6] = {0U, 0U, 0x81U, 0xFFU, 0xFFU, 0xACU};
    fake_i2c_t fake;
    sht31_bus_t bus = {&fake, fake_i2c_write, fake_i2c_read};
    sht31_t d;
    float t;
    float rh;
    unsigned bit;
    assert(sht31_crc8(vector, 2U) == 0x92U); /* Datasheet vector. */
    assert(sht31_decode(raw, &t, &rh));
    assert(fabsf(t - 25.0f) < 0.001f);
    assert(fabsf(rh - 50.000763f) < 0.001f);
    assert(sht31_decode(endpoints, &t, &rh));
    assert(t == -45.0f && rh == 100.0f);
    for (bit = 0U; bit < 48U; ++bit) {
        raw[bit / 8U] ^= (uint8_t)(1U << (bit % 8U));
        assert(!sht31_decode(raw, &t, &rh));
        assert(isnan(t) && isnan(rh));
        raw[bit / 8U] ^= (uint8_t)(1U << (bit % 8U));
    }
    assert(!sht31_decode(NULL, &t, &rh));
    assert(!sht31_decode(raw, NULL, &rh));
    memset(&fake, 0, sizeof(fake));
    memcpy(fake.reply, raw, sizeof(raw));
    assert(sht31_init(&d, &bus, 0x44U));
    assert(!d.valid);
    assert(sht31_start(&d, UINT32_MAX - 5U));
    assert(!sht31_start(&d, UINT32_MAX - 4U)); /* Never restart pending shot. */
    assert(sht31_poll(&d, 9U) == SHT31_WAIT);
    assert(fake.reads == 0U);
    assert(sht31_poll(&d, 10U) == SHT31_READY);
    assert(d.valid && d.sample_timestamp_ms == UINT32_MAX - 5U);
    assert(fabsf(d.temperature_c - 25.0f) < 0.001f);
    assert(sht31_poll(&d, 11U) == SHT31_IDLE);
    assert(sht31_start(&d, 100U));
    assert(d.valid); /* Previous fresh reading survives normal conversion wait. */
    fake.reply[5] ^= 1U;
    assert(sht31_poll(&d, 116U) == SHT31_ERROR);
    assert(!d.valid);
    fake.reply[5] ^= 1U;
    assert(sht31_start(&d, 200U));
    assert(sht31_poll(&d, 301U) == SHT31_ERROR && !d.valid);
    fake.fail_write = true;
    assert(!sht31_start(&d, 400U) && !d.valid);
    fake.fail_write = false;
    fake.fail_read = true;
    assert(sht31_start(&d, 500U));
    assert(sht31_poll(&d, 516U) == SHT31_ERROR && !d.valid);
    assert(!sht31_init(&d, &bus, 0x46U));
}

typedef struct {
    uint8_t registers[4];
    uint8_t sample[3];
    bool ready;
    bool stuck_low;
    bool fail_transfer;
    bool corrupt_config;
    bool corrupt_after_sample;
    bool sample_read;
    unsigned sample_reads;
} fake_spi_t;

static bool fake_spi_transfer(void *context, const uint8_t *tx, uint8_t *rx, size_t n)
{
    fake_spi_t *f = context;
    memset(rx, 0, n);
    if (f->fail_transfer) {
        return false;
    }
    switch (tx[0]) {
    case 0x43U:
        assert(n == 5U);
        memcpy(f->registers, tx + 1, 4U);
        f->sample_read = false;
        f->ready = false;
        break;
    case 0x23U:
        assert(n == 5U);
        memcpy(rx + 1, f->registers, 4U);
        if (f->corrupt_config || (f->sample_read && f->corrupt_after_sample)) {
            rx[1] ^= 0x01U;
        }
        break;
    case ADS1220_CMD_START:
        assert(n == 1U);
        f->ready = f->stuck_low;
        break;
    case ADS1220_CMD_RDATA:
        assert(n == 4U && f->ready);
        memcpy(rx + 1, f->sample, 3U);
        f->ready = false;
        f->sample_read = true;
        ++f->sample_reads;
        break;
    default:
        assert(false);
    }
    return true;
}

static bool fake_spi_ready(void *context)
{
    fake_spi_t *f = context;
    return f->ready;
}

static void test_ads1220(void)
{
    const uint8_t vectors[][3] = {
        {0x00U, 0x00U, 0x00U}, {0x00U, 0x00U, 0x01U},
        {0x7FU, 0xFFU, 0xFFU}, {0x80U, 0x00U, 0x00U},
        {0xFFU, 0xFFU, 0xFFU}, {0xC0U, 0x00U, 0x00U}
    };
    const int32_t values[] = {0, 1, 8388607, -8388608, -1, -4194304};
    const uint8_t expected_reg0[] = {0x21U, 0x41U, 0x51U};
    uint8_t regs[4];
    uint8_t command;
    size_t i;
    int32_t code;
    fake_spi_t fake;
    ads1220_bus_t bus = {&fake, fake_spi_transfer, fake_spi_ready};
    ads1220_t d;
    for (i = 0U; i < sizeof(values) / sizeof(values[0]); ++i) {
        assert(ads1220_decode24(vectors[i], &code));
        assert(code == values[i]);
    }
    assert(!ads1220_decode24(NULL, &code));
    assert(!ads1220_decode24(vectors[0], NULL));
    assert(ads1220_reg_command(false, 0U, 4U, &command) && command == 0x23U);
    assert(ads1220_reg_command(true, 0U, 4U, &command) && command == 0x43U);
    assert(ads1220_reg_command(false, 1U, 3U, &command) && command == 0x26U);
    assert(!ads1220_reg_command(true, 3U, 2U, &command));
    assert(!ads1220_reg_command(true, 0U, 0U, &command));
    assert(!ads1220_reg_command(true, 4U, 1U, &command));
    for (i = 0U; i < 3U; ++i) {
        assert(ads1220_build_config(i, regs));
        assert(regs[0] == expected_reg0[i]);
        assert(regs[1] == 0U && regs[2] == 0U && regs[3] == 0U);
        assert(ads1220_config_matches(regs, i));
        regs[2] = 0x40U; /* Wrong reference must never silently scale a sample. */
        assert(!ads1220_config_matches(regs, i));
    }
    assert(!ads1220_build_config(3U, regs));
    memset(&fake, 0, sizeof(fake));
    fake.sample[0] = 0xFFU;
    fake.sample[1] = 0xFFU;
    fake.sample[2] = 0xFFU;
    assert(ads1220_init(&d, &bus));
    assert(ads1220_start(&d, 2U, UINT32_MAX - 9U));
    assert(d.configuration_verified && !d.valid);
    assert(!ads1220_start(&d, 0U, 0U));
    fake.ready = true;
    assert(ads1220_poll(&d, 44U) == ADS1220_WAIT); /* Only54ms elapsed. */
    assert(fake.sample_reads == 0U);
    assert(ads1220_poll(&d, 45U) == ADS1220_READY);
    assert(d.valid && d.raw == -1 && d.channel == 2U);
    assert(d.sample_timestamp_ms == UINT32_MAX - 9U);
    assert(ads1220_poll(&d, 46U) == ADS1220_IDLE);
    fake.corrupt_config = true;
    assert(!ads1220_start(&d, 0U, 100U));
    assert(!d.valid && !d.configuration_verified);
    fake.corrupt_config = false;
    fake.stuck_low = true;
    assert(!ads1220_start(&d, 0U, 200U));
    fake.stuck_low = false;
    assert(ads1220_start(&d, 0U, 300U));
    assert(ads1220_poll(&d, 450U) == ADS1220_WAIT);
    assert(ads1220_poll(&d, 451U) == ADS1220_ERROR && !d.valid);
    assert(ads1220_start(&d, 0U, 500U));
    fake.ready = true;
    fake.corrupt_after_sample = true;
    assert(ads1220_poll(&d, 555U) == ADS1220_ERROR);
    assert(!d.valid && !d.configuration_verified);
    fake.corrupt_after_sample = false;
    fake.fail_transfer = true;
    assert(!ads1220_start(&d, 0U, 600U));
}

static void test_ntc(void)
{
    /* Independently calculated nominal B3950 divider points, with signed
     * differential ADC codes rounded once. Quantization tolerance is 0.002 C. */
    const int32_t codes[] = {4768134, 3659681, 0, -3189067, -5877052, -6077377};
    const float temperatures[] = {-9.9f, 0.0f, 25.0f, 50.0f, 100.0f, 109.9f};
    const int32_t invalid[] = {-8388608, 8388607, -6758400, 6758400, 8388608, -8388609};
    ntc_config_t cfg;
    ntc_result_t out;
    size_t i;
    ntc_config_defaults(&cfg);
    assert(cfg.supply_v == 3.3f && !cfg.calibrated);
    for (i = 0U; i < sizeof(codes) / sizeof(codes[0]); ++i) {
        assert(ntc_from_ads1220(codes[i], &cfg, &out));
        assert(fabsf(out.temperature_c - temperatures[i]) < 0.002f);
        assert(out.nominal_only);
    }
    assert(ntc_from_ads1220(0, &cfg, &out));
    assert(fabsf(out.resistance_ohm - 100000.0f) < 0.1f);
    assert(fabsf(out.divider_v - 1.65f) < 0.00001f);
    for (i = 0U; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        assert(!ntc_from_ads1220(invalid[i], &cfg, &out));
        assert(isnan(out.temperature_c));
    }
    /* Explicit operating range rejects an otherwise mathematically valid NTC. */
    assert(!ntc_from_ads1220(5000000, &cfg, &out));
    assert(!ntc_from_ads1220(-6200000, &cfg, &out));
    cfg.supply_v = NAN;
    assert(!ntc_from_ads1220(0, &cfg, &out));
    ntc_config_defaults(&cfg);
    cfg.beta_k = 0.0f;
    assert(!ntc_from_ads1220(0, &cfg, &out));
    ntc_config_defaults(&cfg);
    cfg.calibrated = true;
    assert(ntc_from_ads1220(0, &cfg, &out) && !out.nominal_only);
    assert(!ntc_from_ads1220(0, NULL, &out));
    assert(!ntc_from_ads1220(0, &cfg, NULL));
}

static void test_current(void)
{
    current_config_t cfg;
    current_result_t out;
    assert(current_config_defaults(0U, &cfg));
    assert(current_from_voltage(1.65f, &cfg, &out));
    assert(fabsf(out.amperes) < 0.0001f && out.nominal_only);
    assert(current_from_voltage(1.914f, &cfg, &out));
    assert(fabsf(out.amperes - 10.0f) < 0.0001f);
    assert(current_from_voltage(1.386f, &cfg, &out));
    assert(fabsf(out.amperes + 10.0f) < 0.0001f);
    assert(current_from_voltage(3.1f, &cfg, &out));
    assert(!out.within_nominal_range); /* Do not silently clip telemetry. */
    assert(current_config_defaults(1U, &cfg));
    assert(current_from_voltage(0.33f, &cfg, &out));
    assert(fabsf(out.amperes) < 0.0001f);
    assert(current_from_voltage(1.65f, &cfg, &out));
    assert(fabsf(out.amperes - 5.0f) < 0.0001f);
    assert(current_config_defaults(2U, &cfg));
    assert(current_from_adc12(2048U, 3.3f, &cfg, &out));
    assert(fabsf(out.amperes - 5.001526f) < 0.0001f);
    cfg.supply_v = 3.6f;
    /* Nominal data-sheet ratiometry coefficient1.3: sensitivity=.2952V/A. */
    assert(current_from_voltage(1.836f, &cfg, &out));
    assert(fabsf(out.amperes - 5.0f) < 0.001f);
    assert(!current_from_voltage(0.0f, &cfg, &out));
    assert(!current_from_voltage(3.6f, &cfg, &out));
    assert(!current_from_voltage(NAN, &cfg, &out));
    assert(!current_from_adc12(4096U, 3.3f, &cfg, &out));
    assert(!current_from_adc12(2048U, NAN, &cfg, &out));
    cfg.supply_v = 2.9f;
    assert(!current_from_voltage(1.65f, &cfg, &out));
    assert(!current_config_defaults(3U, &cfg));
}

int main(void)
{
    test_sht31();
    test_ads1220();
    test_ntc();
    test_current();
    puts("sensor tests passed");
    return 0;
}
