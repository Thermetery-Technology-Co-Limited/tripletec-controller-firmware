#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "tusb.h"

/* TinyUSB example VID/PID, for this bench prototype only. An assigned VID/PID
 * must replace these values before distribution as a commercial product.
 */
#define TRIPLETEC_USB_VID 0xcafeu
#define TRIPLETEC_USB_PID 0x4001u

static const tusb_desc_device_t device_descriptor = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = TRIPLETEC_USB_VID,
    .idProduct = TRIPLETEC_USB_PID,
    .bcdDevice = 0x0501,
    .iManufacturer = 1,
    .iProduct = 2,
    .iSerialNumber = 3,
    .bNumConfigurations = 1
};

/* FSDEV has 512 B PMA: EP0 64 B each direction, notification EP1 8 B,
 * bulk EP2 64 B each direction plus BTABLE. No double buffering needed.
 * Declare up to 100 mA USB consumption for the controller electronics only;
 * TEC power is always external and remains locked off by the bench image.
 */
static const uint8_t configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(1, 2, 0, TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN, 0, 100),
    TUD_CDC_DESCRIPTOR(0, 4, 0x81, 8, 0x02, 0x82, 64)
};

uint8_t const *tud_descriptor_device_cb(void)
{
    return (const uint8_t *)&device_descriptor;
}

uint8_t const *tud_descriptor_configuration_cb(uint8_t index)
{
    return index == 0u ? configuration_descriptor : NULL;
}

static void device_serial(char serial[25])
{
    const volatile uint32_t *uid = (const volatile uint32_t *)(uintptr_t)0x1ffff7e8u;
    static const char hex[] = "0123456789ABCDEF";
    for (unsigned word = 0u; word < 3u; ++word) {
        const uint32_t value = uid[word];
        for (unsigned digit = 0u; digit < 8u; ++digit) {
            serial[word * 8u + digit] = hex[(value >> (28u - digit * 4u)) & 15u];
        }
    }
    serial[24] = '\0';
}

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t language)
{
    static uint16_t descriptor[65];
    const char *text;
    char serial[25];
    size_t count;
    if (index == 0u) {
        descriptor[0] = (uint16_t)((TUSB_DESC_STRING << 8) | 4u);
        descriptor[1] = 0x0409u;
        return descriptor;
    }
    if (language != 0x0409u) {
        return NULL;
    }
    switch (index) {
    case 1u: text = "Thermetery Technology Co., Limited"; break;
    case 2u: text = "TripleTEC Control"; break;
    case 3u: device_serial(serial); text = serial; break;
    case 4u: text = "TEC control"; break;
    default: return NULL;
    }
    count = strlen(text);
    if (count > 64u) {
        count = 64u;
    }
    for (size_t i = 0u; i < count; ++i) {
        descriptor[i + 1u] = (uint8_t)text[i];
    }
    descriptor[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2u * count + 2u));
    return descriptor;
}
