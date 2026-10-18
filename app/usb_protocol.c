#include "usb_protocol.h"

static uint32_t read_u32(const uint8_t *bytes)
{
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8U) |
           ((uint32_t)bytes[2] << 16U) | ((uint32_t)bytes[3] << 24U);
}

static void write_u32(uint8_t *bytes, uint32_t value)
{
    for (unsigned i = 0U; i < 4U; ++i) {
        bytes[i] = (uint8_t)(value >> (8U * i));
    }
}

static void copy_bytes(uint8_t *destination, const uint8_t *source, size_t length)
{
    for (size_t i = 0U; i < length; ++i) {
        destination[i] = source[i];
    }
}

static bool equal_bytes(const uint8_t *left, const uint8_t *right, size_t length)
{
    for (size_t i = 0U; i < length; ++i) {
        if (left[i] != right[i]) {
            return false;
        }
    }
    return true;
}

uint32_t usb_protocol_crc32(const uint8_t *bytes, size_t length)
{
    uint32_t crc = UINT32_MAX;
    if (bytes == NULL && length != 0U) {
        return 0U;
    }
    for (size_t i = 0U; i < length; ++i) {
        crc ^= bytes[i];
        for (unsigned bit = 0U; bit < 8U; ++bit) {
            crc = (crc >> 1U) ^ (UINT32_C(0xEDB88320) & (0U - (crc & 1U)));
        }
    }
    return ~crc;
}

void usb_protocol_init(usb_protocol_t *protocol, usb_protocol_apply_fn apply,
                        usb_protocol_snapshot_fn snapshot,
                        usb_protocol_enqueue_fn enqueue, void *context)
{
    if (protocol != NULL) {
        *protocol = (usb_protocol_t){0};
        protocol->apply = apply;
        protocol->snapshot = snapshot;
        protocol->enqueue = enqueue;
        protocol->context = context;
    }
}

bool usb_protocol_poll(usb_protocol_t *protocol)
{
    if (protocol == NULL) {
        return false;
    }
    if (protocol->response_pending && protocol->enqueue != NULL &&
        protocol->enqueue(protocol->context, protocol->response, USB_PROTOCOL_RESPONSE_SIZE)) {
        protocol->response_pending = false;
    }
    return !protocol->response_pending;
}

static control_result_t validate(const uint8_t *request, int32_t value)
{
    if (request[4] != USB_PROTOCOL_VERSION || request[6] != 0U || request[7] != 0U) {
        return CONTROL_BAD_PROTOCOL;
    }
    switch (request[5]) {
    case USB_PROTOCOL_STATUS:
    case CONTROL_STOP:
    case CONTROL_HEARTBEAT:
        return value == 0 ? CONTROL_OK : CONTROL_BAD_VALUE;
    case CONTROL_SET_MODE:
        return value == 0 || value == 1 ? CONTROL_OK : CONTROL_BAD_VALUE;
    case CONTROL_SET_MARGIN:
        return value >= 500 && value <= 20000 ? CONTROL_OK : CONTROL_BAD_VALUE;
    case CONTROL_ARM:
        return value >= 1 && value <= 7 ? CONTROL_OK : CONTROL_BAD_VALUE;
    default:
        return CONTROL_BAD_OPCODE;
    }
}

static void make_response(usb_protocol_t *protocol, uint32_t sequence, control_result_t result)
{
    uint8_t *response = protocol->response;
    response[0] = 'T';
    response[1] = 'E';
    response[2] = 'C';
    response[3] = 'R';
    response[4] = USB_PROTOCOL_VERSION;
    response[5] = (uint8_t)result;
    response[6] = (uint8_t)USB_PROTOCOL_PAYLOAD_SIZE;
    response[7] = (uint8_t)(USB_PROTOCOL_PAYLOAD_SIZE >> 8U);
    write_u32(response + 8U, sequence);
    /* A missing snapshot callback is an integration error, but still produce
     * a deterministic error packet instead of exposing stale RAM contents.
     */
    for (size_t i = 12U; i < USB_PROTOCOL_RESPONSE_SIZE - 4U; ++i) {
        response[i] = 0U;
    }
    if (protocol->snapshot != NULL) {
        protocol->snapshot(protocol->context, response + 12U);
    } else {
        response[5] = (uint8_t)CONTROL_INTERLOCK;
    }
    write_u32(response + USB_PROTOCOL_RESPONSE_SIZE - 4U,
              usb_protocol_crc32(response, USB_PROTOCOL_RESPONSE_SIZE - 4U));
    protocol->response_pending = true;
}

static void process_request(usb_protocol_t *protocol)
{
    const uint8_t *request = protocol->input;
    uint32_t sequence = read_u32(request + 8U);
    uint32_t unsigned_value = read_u32(request + 12U);
    int32_t value = unsigned_value <= INT32_MAX ? (int32_t)unsigned_value :
                    -1 - (int32_t)(UINT32_MAX - unsigned_value);
    control_result_t result;
    if (sequence == 0U) {
        return;
    }
    if (sequence == protocol->last_sequence) {
        if (equal_bytes(request, protocol->last_request, USB_PROTOCOL_REQUEST_SIZE)) {
            copy_bytes(protocol->response, protocol->last_response, USB_PROTOCOL_RESPONSE_SIZE);
            protocol->response_pending = true;
        } else {
            make_response(protocol, sequence, CONTROL_BAD_PROTOCOL);
        }
        return;
    }
    if (protocol->last_sequence != 0U &&
        sequence - protocol->last_sequence >= UINT32_C(0x80000000)) {
        make_response(protocol, sequence, CONTROL_BAD_PROTOCOL);
        return;
    }
    result = validate(request, value);
    if (result == CONTROL_OK && request[5] != USB_PROTOCOL_STATUS) {
        result = protocol->apply != NULL ?
                 protocol->apply(protocol->context, request[5], value, sequence) : CONTROL_INTERLOCK;
        if ((unsigned)result > (unsigned)CONTROL_INTERLOCK) {
            result = CONTROL_INTERLOCK;
        }
    }
    make_response(protocol, sequence, result);
    protocol->last_sequence = sequence;
    copy_bytes(protocol->last_request, request, USB_PROTOCOL_REQUEST_SIZE);
    copy_bytes(protocol->last_response, protocol->response, USB_PROTOCOL_RESPONSE_SIZE);
}

static bool starts_with_magic(const uint8_t *bytes)
{
    return bytes[0] == 'T' && bytes[1] == 'E' && bytes[2] == 'C' && bytes[3] == 'Q';
}

static void discard_byte(usb_protocol_t *protocol)
{
    for (size_t i = 1U; i < protocol->input_used; ++i) {
        protocol->input[i - 1U] = protocol->input[i];
    }
    --protocol->input_used;
}

size_t usb_protocol_feed(usb_protocol_t *protocol, const uint8_t *bytes, size_t length)
{
    size_t consumed = 0U;
    if (protocol == NULL || (bytes == NULL && length != 0U) || !usb_protocol_poll(protocol)) {
        return 0U;
    }
    while (consumed < length) {
        protocol->input[protocol->input_used++] = bytes[consumed++];
        while (protocol->input_used >= 4U && !starts_with_magic(protocol->input)) {
            discard_byte(protocol);
        }
        if (protocol->input_used == USB_PROTOCOL_REQUEST_SIZE) {
            if (read_u32(protocol->input + 16U) != usb_protocol_crc32(protocol->input, 16U)) {
                /* Preserve a possible new magic inside a corrupted frame. */
                discard_byte(protocol);
                while (protocol->input_used >= 4U && !starts_with_magic(protocol->input)) {
                    discard_byte(protocol);
                }
                continue;
            }
            process_request(protocol);
            protocol->input_used = 0U;
            if (!usb_protocol_poll(protocol)) {
                break;
            }
        }
    }
    return consumed;
}
