#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include "tusb.h"

int main(void)
{
    const uint8_t *device = tud_descriptor_device_cb();
    assert(device != NULL && device[0] == 18u && device[1] == TUSB_DESC_DEVICE);
    assert(device[4] == TUSB_CLASS_MISC && device[5] == MISC_SUBCLASS_COMMON);
    assert(device[6] == MISC_PROTOCOL_IAD && device[7] == 64u);
    assert(device[8] == 0xfeu && device[9] == 0xcau);
    assert(device[10] == 0x01u && device[11] == 0x40u);
    assert(device[14] == 1u && device[15] == 2u && device[16] == 3u);
    assert(device[17] == 1u);

    const uint8_t *config = tud_descriptor_configuration_cb(0u);
    assert(config != NULL && config[0] == TUD_CONFIG_DESC_LEN);
    const size_t total = config[2] | (size_t)config[3] << 8;
    assert(total == TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN);
    assert(config[4] == 2u && config[8] == 50u);
    assert(tud_descriptor_configuration_cb(1u) == NULL);
    unsigned interfaces = 0u, endpoints = 0u;
    for (size_t offset = 0u; offset < total;) {
        const uint8_t length = config[offset];
        assert(length >= 2u && offset + length <= total);
        if (config[offset + 1u] == TUSB_DESC_INTERFACE) {
            ++interfaces;
        } else if (config[offset + 1u] == TUSB_DESC_ENDPOINT) {
            const uint8_t address = config[offset + 2u];
            const unsigned packet_size = config[offset + 4u] |
                                         (unsigned)config[offset + 5u] << 8;
            if (address == 0x81u) {
                assert(config[offset + 3u] == TUSB_XFER_INTERRUPT && packet_size == 8u);
            } else {
                assert(address == 0x02u || address == 0x82u);
                assert(config[offset + 3u] == TUSB_XFER_BULK && packet_size == 64u);
            }
            ++endpoints;
        }
        offset += length;
    }
    assert(interfaces == 2u && endpoints == 3u);

    const uint16_t *string = tud_descriptor_string_cb(0u, 0u);
    assert(string[0] == 0x0304u && string[1] == 0x0409u);
    string = tud_descriptor_string_cb(2u, 0x0409u);
    assert(string != NULL && string[0] == 0x0324u); /* 17 ASCII characters. */
    assert(string[1] == 'T' && string[17] == 'l');
    assert(tud_descriptor_string_cb(5u, 0x0409u) == NULL);
    assert(tud_descriptor_string_cb(2u, 0x0411u) == NULL);
    puts("USB CDC descriptors: PASS");
    return 0;
}
