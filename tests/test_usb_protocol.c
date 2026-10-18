#include "usb_protocol.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#define CAPTURE_COUNT 32U

typedef struct {
    bool accepting;
    unsigned apply_count;
    unsigned snapshot_count;
    unsigned enqueue_count;
    unsigned response_count;
    uint8_t last_opcode;
    int32_t last_value;
    uint32_t last_sequence;
    control_result_t result;
    uint8_t response[CAPTURE_COUNT][USB_PROTOCOL_RESPONSE_SIZE];
} fixture_t;

static uint32_t get_u32(const uint8_t *bytes)
{
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8U) |
           ((uint32_t)bytes[2] << 16U) | ((uint32_t)bytes[3] << 24U);
}

static void put_u32(uint8_t *bytes, uint32_t value)
{
    for (unsigned i = 0U; i < 4U; ++i) {
        bytes[i] = (uint8_t)(value >> (8U * i));
    }
}

static control_result_t apply(void *context, uint8_t opcode, int32_t value, uint32_t sequence)
{
    fixture_t *fixture = context;
    ++fixture->apply_count;
    fixture->last_opcode = opcode;
    fixture->last_value = value;
    fixture->last_sequence = sequence;
    return fixture->result;
}

static void snapshot(void *context, uint8_t payload[USB_PROTOCOL_PAYLOAD_SIZE])
{
    fixture_t *fixture = context;
    ++fixture->snapshot_count;
    for (size_t i = 0U; i < USB_PROTOCOL_PAYLOAD_SIZE; ++i) {
        payload[i] = (uint8_t)(i ^ 0xA5U);
    }
    /* These demonstrate that the ACK snapshot is taken after apply. */
    put_u32(payload, fixture->apply_count);
    put_u32(payload + 4U, fixture->snapshot_count);
}

static bool enqueue(void *context, const uint8_t *bytes, size_t length)
{
    fixture_t *fixture = context;
    assert(length == USB_PROTOCOL_RESPONSE_SIZE);
    ++fixture->enqueue_count;
    if (!fixture->accepting) {
        return false;
    }
    assert(fixture->response_count < CAPTURE_COUNT);
    memcpy(fixture->response[fixture->response_count++], bytes, length);
    return true;
}

static void initialize(usb_protocol_t *protocol, fixture_t *fixture)
{
    memset(fixture, 0, sizeof(*fixture));
    fixture->accepting = true;
    fixture->result = CONTROL_OK;
    usb_protocol_init(protocol, apply, snapshot, enqueue, fixture);
}

static void update_crc(uint8_t request[USB_PROTOCOL_REQUEST_SIZE])
{
    put_u32(request + 16U, usb_protocol_crc32(request, 16U));
}

static void request(uint8_t bytes[USB_PROTOCOL_REQUEST_SIZE], uint32_t sequence,
                     uint8_t opcode, int32_t value)
{
    memset(bytes, 0, USB_PROTOCOL_REQUEST_SIZE);
    memcpy(bytes, "TECQ", 4U);
    bytes[4] = USB_PROTOCOL_VERSION;
    bytes[5] = opcode;
    put_u32(bytes + 8U, sequence);
    put_u32(bytes + 12U, (uint32_t)value);
    update_crc(bytes);
}

static void check_response(const fixture_t *fixture, unsigned index, uint32_t sequence,
                            control_result_t result)
{
    const uint8_t *bytes;
    assert(index < fixture->response_count);
    bytes = fixture->response[index];
    assert(memcmp(bytes, "TECR", 4U) == 0);
    assert(bytes[4] == 1U);
    assert(bytes[5] == (uint8_t)result);
    assert(bytes[6] == 224U && bytes[7] == 0U);
    assert(get_u32(bytes + 8U) == sequence);
    assert(get_u32(bytes + 236U) == usb_protocol_crc32(bytes, 236U));
    for (size_t i = 8U; i < USB_PROTOCOL_PAYLOAD_SIZE; ++i) {
        assert(bytes[12U + i] == (uint8_t)(i ^ 0xA5U));
    }
}

static void test_crc_and_fragmentation(void)
{
    usb_protocol_t protocol;
    fixture_t fixture;
    uint8_t bytes[USB_PROTOCOL_REQUEST_SIZE];
    assert(usb_protocol_crc32((const uint8_t *)"123456789", 9U) == UINT32_C(0xCBF43926));
    assert(usb_protocol_crc32(NULL, 0U) == 0U);
    for (size_t split = 0U; split <= USB_PROTOCOL_REQUEST_SIZE; ++split) {
        initialize(&protocol, &fixture);
        request(bytes, 1U, CONTROL_SET_MARGIN, 3000);
        assert(usb_protocol_feed(&protocol, bytes, split) == split);
        assert(fixture.apply_count == (split == USB_PROTOCOL_REQUEST_SIZE ? 1U : 0U));
        assert(usb_protocol_feed(&protocol, bytes + split, sizeof(bytes) - split) == sizeof(bytes) - split);
        assert(fixture.apply_count == 1U && fixture.snapshot_count == 1U);
        assert(fixture.last_opcode == CONTROL_SET_MARGIN && fixture.last_value == 3000);
        assert(fixture.last_sequence == 1U);
        check_response(&fixture, 0U, 1U, CONTROL_OK);
        assert(get_u32(fixture.response[0] + 12U) == 1U);
    }
    initialize(&protocol, &fixture);
    request(bytes, 123U, USB_PROTOCOL_STATUS, 0);
    for (size_t i = 0U; i < sizeof(bytes); ++i) {
        assert(usb_protocol_feed(&protocol, bytes + i, 1U) == 1U);
    }
    assert(fixture.apply_count == 0U && fixture.snapshot_count == 1U);
    check_response(&fixture, 0U, 123U, CONTROL_OK);
}

static void test_coalesced_requests_and_result(void)
{
    usb_protocol_t protocol;
    fixture_t fixture;
    uint8_t stream[3U * USB_PROTOCOL_REQUEST_SIZE];
    initialize(&protocol, &fixture);
    request(stream, 3U, CONTROL_STOP, 0);
    request(stream + 20U, 4U, CONTROL_SET_MODE, 1);
    request(stream + 40U, 5U, CONTROL_HEARTBEAT, 0);
    assert(usb_protocol_feed(&protocol, stream, sizeof(stream)) == sizeof(stream));
    assert(fixture.apply_count == 3U && fixture.response_count == 3U);
    for (unsigned i = 0U; i < 3U; ++i) {
        check_response(&fixture, i, 3U + i, CONTROL_OK);
        assert(get_u32(fixture.response[i] + 12U) == i + 1U);
    }
    fixture.result = CONTROL_BENCH_LOCKED;
    request(stream, 6U, CONTROL_ARM, 7);
    assert(usb_protocol_feed(&protocol, stream, 20U) == 20U);
    check_response(&fixture, 3U, 6U, CONTROL_BENCH_LOCKED);
    assert(fixture.apply_count == 4U);
    fixture.result = (control_result_t)255;
    request(stream, 7U, CONTROL_ARM, 1);
    assert(usb_protocol_feed(&protocol, stream, 20U) == 20U);
    check_response(&fixture, 4U, 7U, CONTROL_INTERLOCK);
}

static void test_validation_never_applies(void)
{
    static const struct { uint8_t opcode; int32_t value; control_result_t result; } cases[] = {
        {USB_PROTOCOL_STATUS, 1, CONTROL_BAD_VALUE},
        {CONTROL_STOP, 1, CONTROL_BAD_VALUE},
        {CONTROL_HEARTBEAT, -1, CONTROL_BAD_VALUE},
        {CONTROL_SET_MODE, 2, CONTROL_BAD_VALUE},
        {CONTROL_SET_MARGIN, 499, CONTROL_BAD_VALUE},
        {CONTROL_SET_MARGIN, 20001, CONTROL_BAD_VALUE},
        {CONTROL_SET_MARGIN, INT32_MIN, CONTROL_BAD_VALUE},
        {CONTROL_ARM, 0, CONTROL_BAD_VALUE},
        {CONTROL_ARM, 8, CONTROL_BAD_VALUE},
        {255U, 0, CONTROL_BAD_OPCODE}
    };
    usb_protocol_t protocol;
    fixture_t fixture;
    uint8_t bytes[USB_PROTOCOL_REQUEST_SIZE];
    initialize(&protocol, &fixture);
    for (unsigned i = 0U; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        request(bytes, i + 1U, cases[i].opcode, cases[i].value);
        assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
        check_response(&fixture, i, i + 1U, cases[i].result);
    }
    assert(fixture.apply_count == 0U);
    for (unsigned index = 4U; index <= 7U; ++index) {
        if (index == 5U) {
            continue;
        }
        request(bytes, 20U + index, CONTROL_STOP, 0);
        bytes[index] = 2U;
        update_crc(bytes);
        assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
        check_response(&fixture, fixture.response_count - 1U, 20U + index, CONTROL_BAD_PROTOCOL);
    }
    assert(fixture.apply_count == 0U);
    request(bytes, 0U, CONTROL_STOP, 0);
    unsigned responses = fixture.response_count;
    assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
    assert(fixture.response_count == responses && fixture.apply_count == 0U);
}

static void test_valid_value_boundaries(void)
{
    static const struct { uint8_t opcode; int32_t value; } cases[] = {
        {CONTROL_SET_MODE, 0}, {CONTROL_SET_MODE, 1},
        {CONTROL_SET_MARGIN, 500}, {CONTROL_SET_MARGIN, 20000},
        {CONTROL_ARM, 1}, {CONTROL_ARM, 7}, {CONTROL_STOP, 0}, {CONTROL_HEARTBEAT, 0}
    };
    usb_protocol_t protocol;
    fixture_t fixture;
    uint8_t bytes[USB_PROTOCOL_REQUEST_SIZE];
    initialize(&protocol, &fixture);
    for (unsigned i = 0U; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        request(bytes, i + 1U, cases[i].opcode, cases[i].value);
        assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
        check_response(&fixture, i, i + 1U, CONTROL_OK);
        assert(fixture.last_value == cases[i].value);
    }
    assert(fixture.apply_count == 8U);
}

static void test_noise_corruption_and_resynchronization(void)
{
    usb_protocol_t protocol;
    fixture_t fixture;
    uint8_t bytes[USB_PROTOCOL_REQUEST_SIZE];
    uint8_t noise[4096];
    uint8_t damaged[USB_PROTOCOL_REQUEST_SIZE];
    initialize(&protocol, &fixture);
    memset(noise, 'T', sizeof(noise));
    assert(usb_protocol_feed(&protocol, noise, sizeof(noise)) == sizeof(noise));
    assert(protocol.input_used < USB_PROTOCOL_REQUEST_SIZE && fixture.apply_count == 0U);
    request(damaged, 1U, CONTROL_ARM, 7);
    damaged[16] ^= 1U;
    assert(usb_protocol_feed(&protocol, damaged, sizeof(damaged)) == sizeof(damaged));
    assert(fixture.response_count == 0U && fixture.apply_count == 0U);
    request(bytes, 2U, CONTROL_STOP, 0);
    assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
    assert(fixture.apply_count == 1U);
    check_response(&fixture, 0U, 2U, CONTROL_OK);

    /* A new complete frame starts inside an unfinished, CRC-invalid frame. */
    request(damaged, 3U, CONTROL_ARM, 7);
    assert(usb_protocol_feed(&protocol, damaged, 10U) == 10U);
    request(bytes, 4U, CONTROL_STOP, 0);
    assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
    assert(fixture.apply_count == 2U);
    check_response(&fixture, 1U, 4U, CONTROL_OK);
    assert(fixture.last_opcode == CONTROL_STOP);

    /* Random noise is consumed in bounded memory; an intact following frame
     * is still usable even if the noise ended with a partial magic prefix. */
    uint32_t random_state = UINT32_C(0x31415926);
    for (unsigned pass = 0U; pass < 64U; ++pass) {
        for (size_t i = 0U; i < sizeof(noise); ++i) {
            random_state = random_state * UINT32_C(1664525) + UINT32_C(1013904223);
            noise[i] = (uint8_t)(random_state >> 24U);
        }
        assert(usb_protocol_feed(&protocol, noise, sizeof(noise)) == sizeof(noise));
        assert(protocol.input_used < USB_PROTOCOL_REQUEST_SIZE);
    }
    assert(fixture.apply_count == 2U);
    assert(usb_protocol_feed(&protocol, (const uint8_t *)"TEC", 3U) == 3U);
    request(bytes, 5U, CONTROL_STOP, 0);
    assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
    assert(fixture.apply_count == 3U);
    check_response(&fixture, 2U, 5U, CONTROL_OK);
}

static void test_replay_conflict_stale_and_wrap(void)
{
    usb_protocol_t protocol;
    fixture_t fixture;
    uint8_t bytes[USB_PROTOCOL_REQUEST_SIZE];
    uint8_t original[USB_PROTOCOL_REQUEST_SIZE];
    initialize(&protocol, &fixture);
    request(original, 100U, CONTROL_SET_MODE, 1);
    assert(usb_protocol_feed(&protocol, original, sizeof(original)) == sizeof(original));
    assert(usb_protocol_feed(&protocol, original, sizeof(original)) == sizeof(original));
    assert(fixture.apply_count == 1U && fixture.snapshot_count == 1U);
    assert(memcmp(fixture.response[0], fixture.response[1], USB_PROTOCOL_RESPONSE_SIZE) == 0);
    request(bytes, 100U, CONTROL_ARM, 7);
    assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
    check_response(&fixture, 2U, 100U, CONTROL_BAD_PROTOCOL);
    request(bytes, 99U, CONTROL_STOP, 0);
    assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
    check_response(&fixture, 3U, 99U, CONTROL_BAD_PROTOCOL);
    request(bytes, 100U + UINT32_C(0x80000000), CONTROL_STOP, 0);
    assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
    check_response(&fixture, 4U, 100U + UINT32_C(0x80000000), CONTROL_BAD_PROTOCOL);
    assert(fixture.apply_count == 1U && protocol.last_sequence == 100U);
    assert(usb_protocol_feed(&protocol, original, sizeof(original)) == sizeof(original));
    assert(memcmp(fixture.response[0], fixture.response[5], USB_PROTOCOL_RESPONSE_SIZE) == 0);
    assert(fixture.apply_count == 1U);

    initialize(&protocol, &fixture);
    const uint32_t sequence[] = {UINT32_MAX - 1U, UINT32_MAX, 1U, 2U};
    for (unsigned i = 0U; i < sizeof(sequence) / sizeof(sequence[0]); ++i) {
        request(bytes, sequence[i], CONTROL_STOP, 0);
        assert(usb_protocol_feed(&protocol, bytes, sizeof(bytes)) == sizeof(bytes));
        check_response(&fixture, i, sequence[i], CONTROL_OK);
    }
    assert(fixture.apply_count == 4U && protocol.last_sequence == 2U);
}

static void test_backpressure_and_missing_callbacks(void)
{
    usb_protocol_t protocol;
    fixture_t fixture;
    uint8_t stream[2U * USB_PROTOCOL_REQUEST_SIZE];
    initialize(&protocol, &fixture);
    fixture.accepting = false;
    request(stream, 1U, CONTROL_STOP, 0);
    request(stream + 20U, 2U, CONTROL_SET_MODE, 1);
    assert(usb_protocol_feed(&protocol, stream, sizeof(stream)) == 20U);
    assert(fixture.apply_count == 1U && fixture.response_count == 0U);
    assert(!usb_protocol_poll(&protocol));
    assert(usb_protocol_feed(&protocol, stream + 20U, 20U) == 0U);
    assert(fixture.apply_count == 1U && fixture.snapshot_count == 1U);
    fixture.accepting = true;
    assert(usb_protocol_poll(&protocol));
    assert(fixture.response_count == 1U);
    check_response(&fixture, 0U, 1U, CONTROL_OK);
    assert(usb_protocol_feed(&protocol, stream + 20U, 20U) == 20U);
    assert(fixture.apply_count == 2U);
    check_response(&fixture, 1U, 2U, CONTROL_OK);
    assert(!usb_protocol_poll(NULL));
    assert(usb_protocol_feed(NULL, stream, sizeof(stream)) == 0U);
    assert(usb_protocol_feed(&protocol, NULL, 1U) == 0U);
    assert(usb_protocol_feed(&protocol, NULL, 0U) == 0U);
    usb_protocol_init(NULL, NULL, NULL, NULL, NULL);

    usb_protocol_init(&protocol, NULL, snapshot, enqueue, &fixture);
    request(stream, 3U, CONTROL_STOP, 0);
    assert(usb_protocol_feed(&protocol, stream, 20U) == 20U);
    check_response(&fixture, 2U, 3U, CONTROL_INTERLOCK);
    assert(fixture.apply_count == 2U);
    usb_protocol_init(&protocol, NULL, NULL, enqueue, &fixture);
    request(stream, 4U, USB_PROTOCOL_STATUS, 0);
    assert(usb_protocol_feed(&protocol, stream, 20U) == 20U);
    assert(fixture.response[3][5] == CONTROL_INTERLOCK);
    for (size_t i = 12U; i < 236U; ++i) {
        assert(fixture.response[3][i] == 0U);
    }
    assert(get_u32(fixture.response[3] + 236U) == usb_protocol_crc32(fixture.response[3], 236U));
}

int main(void)
{
    test_crc_and_fragmentation();
    test_coalesced_requests_and_result();
    test_validation_never_applies();
    test_valid_value_boundaries();
    test_noise_corruption_and_resynchronization();
    test_replay_conflict_stale_and_wrap();
    test_backpressure_and_missing_callbacks();
    puts("USB protocol tests passed");
    return 0;
}
