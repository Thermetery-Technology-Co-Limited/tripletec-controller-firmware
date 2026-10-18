#include "control_protocol.h"

#include <assert.h>
#include <stdio.h>

typedef struct {
    unsigned calls;
    uint32_t opcode;
    int32_t value;
    uint32_t now;
} fake_application_t;

static control_result_t apply(void *context, uint32_t opcode, int32_t value, uint32_t now)
{
    fake_application_t *app = context;
    ++app->calls;
    app->opcode = opcode;
    app->value = value;
    app->now = now;
    return opcode == CONTROL_ARM ? CONTROL_BENCH_LOCKED : CONTROL_OK;
}

static control_mailbox_t packet(uint32_t sequence, uint32_t opcode, int32_t value)
{
    control_mailbox_t request = {CONTROL_MAILBOX_MAGIC, CONTROL_PROTOCOL_VERSION,
                                 sequence, opcode, value, 0, 0U, 0U};
    request.checksum = control_protocol_checksum(&request);
    return request;
}

static void test_bench_lock_values_and_replay(void)
{
    control_protocol_t protocol;
    fake_application_t app = {0};
    const int32_t values[] = {INT32_MIN, -1, 0, 1, 2, INT32_MAX};
    control_protocol_init(&protocol);
    for (unsigned i = 0U; i < sizeof(values) / sizeof(values[0]); ++i) {
        control_mailbox_t request = packet(i + 1U, CONTROL_SET_BENCH_LOCK, values[i]);
        unsigned before = app.calls;
        bool valid = values[i] == 0 || values[i] == 1;
        assert(control_protocol_poll(&protocol, &request, apply, &app, 100U + i));
        assert(protocol.result == (valid ? CONTROL_OK : CONTROL_BAD_VALUE));
        assert(app.calls == before + (valid ? 1U : 0U));
        if (valid) {
            assert(app.opcode == CONTROL_SET_BENCH_LOCK && app.value == values[i]);
        }
        assert(!control_protocol_poll(&protocol, &request, apply, &app, 200U + i));
        assert(app.calls == before + (valid ? 1U : 0U));
    }
}

static void test_commissioning_values(void)
{
    const int32_t candidates[] = {-1, 0, 1, 2, 3, 4, 5, 14999, 15000, 15001, INT32_MAX};
    for (unsigned opcode = CONTROL_COMMISSION_PREPARE; opcode <= CONTROL_COMMISSION_TEST; ++opcode) {
        control_protocol_t protocol;
        fake_application_t app = {0};
        control_protocol_init(&protocol);
        for (size_t i = 0U; i < sizeof(candidates) / sizeof(candidates[0]); ++i) {
            const bool allowed = opcode == CONTROL_COMMISSION_PREPARE ? candidates[i] == 15000 :
                                 candidates[i] >= 0 && candidates[i] <= 4;
            control_mailbox_t request = packet((uint32_t)i + 1U, opcode, candidates[i]);
            const unsigned before = app.calls;
            assert(control_protocol_poll(&protocol, &request, apply, &app, 100U));
            assert(protocol.result == (allowed ? CONTROL_OK : CONTROL_BAD_VALUE));
            assert(app.calls == before + (allowed ? 1U : 0U));
            assert(!control_protocol_poll(&protocol, &request, apply, &app, 101U));
        }
    }
}

int main(void)
{
    test_bench_lock_values_and_replay();
    test_commissioning_values();
    control_protocol_t protocol;
    fake_application_t app = {0};
    control_mailbox_t request = packet(1U, CONTROL_SET_MODE, 1);
    /* Independent Python zlib CRC32 vector, not another copy of the C loop. */
    assert(request.checksum == UINT32_C(0x17752733));
    control_protocol_init(&protocol);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 100U));
    assert(protocol.ack_seq == 1U && protocol.result == CONTROL_OK);
    assert(app.calls == 1U && app.opcode == CONTROL_SET_MODE && app.value == 1);
    assert(app.now == 100U && protocol.last_valid_ms == 100U);
    assert(!control_protocol_poll(&protocol, &request, apply, &app, 101U));
    assert(app.calls == 1U); /* duplicate command cannot reapply */

    request = packet(2U, CONTROL_SET_MARGIN, 3000);
    request.request_seq = 0U; /* partially written/uncommitted frame */
    assert(!control_protocol_poll(&protocol, &request, apply, &app, 110U));
    assert(protocol.ack_seq == 1U && app.calls == 1U);
    request.request_seq = 2U;
    request.arg0 = 4000; /* torn body with old checksum */
    assert(control_protocol_poll(&protocol, &request, apply, &app, 120U));
    assert(protocol.result == CONTROL_BAD_CHECKSUM && app.calls == 1U);
    assert(protocol.last_valid_ms == 100U);

    request = packet(3U, CONTROL_SET_MODE, 2);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 130U));
    assert(protocol.result == CONTROL_BAD_VALUE && app.calls == 1U);
    request = packet(4U, CONTROL_SET_MARGIN, 499);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 140U));
    assert(protocol.result == CONTROL_BAD_VALUE && app.calls == 1U);
    request = packet(5U, CONTROL_SET_MARGIN, 20001);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 150U));
    assert(protocol.result == CONTROL_BAD_VALUE && app.calls == 1U);
    request = packet(6U, CONTROL_STOP, 1);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 160U));
    assert(protocol.result == CONTROL_BAD_VALUE);
    request = packet(7U, 99U, 0);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 170U));
    assert(protocol.result == CONTROL_BAD_OPCODE);
    request = packet(8U, CONTROL_STOP, 0);
    request.version = 9U;
    request.checksum = control_protocol_checksum(&request);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 180U));
    assert(protocol.result == CONTROL_BAD_PROTOCOL);
    request = packet(9U, CONTROL_ARM, 7);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 190U));
    assert(protocol.result == CONTROL_BENCH_LOCKED && app.calls == 2U);
    request = packet(10U, CONTROL_STOP, 0);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 200U));
    assert(protocol.result == CONTROL_OK && app.opcode == CONTROL_STOP);
    request = packet(11U, CONTROL_HEARTBEAT, 0);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 210U));
    assert(protocol.result == CONTROL_OK && protocol.last_valid_ms == 210U);
    request = packet(10U, CONTROL_SET_MODE, 1); /* replay older sequence */
    assert(!control_protocol_poll(&protocol, &request, apply, &app, 220U));
    protocol.ack_seq = UINT32_MAX;
    request = packet(1U, CONTROL_STOP, 0);
    assert(control_protocol_poll(&protocol, &request, apply, &app, 230U));
    assert(protocol.ack_seq == 1U && protocol.result == CONTROL_OK);
    request = packet(2U, CONTROL_STOP, 0);
    assert(control_protocol_poll(&protocol, &request, NULL, NULL, 240U));
    assert(protocol.result == CONTROL_INTERLOCK);
    assert(!control_protocol_lease_expired(&protocol, false, 10000U, 2000U));
    assert(!control_protocol_lease_expired(&protocol, true, 2240U, 2000U));
    assert(control_protocol_lease_expired(&protocol, true, 2241U, 2000U));
    protocol.last_valid_ms = UINT32_MAX - 99U;
    assert(!control_protocol_lease_expired(&protocol, true, 1900U, 2000U));
    assert(control_protocol_lease_expired(&protocol, true, 1901U, 2000U));
    assert(control_protocol_lease_expired(NULL, true, 0U, 2000U));
    assert(!control_protocol_poll(NULL, &request, apply, &app, 0U));
    assert(!control_protocol_poll(&protocol, NULL, apply, &app, 0U));
    puts("command protocol: packet, CRC, commit, replay, validation, bench lock and ACK tests passed");
    return 0;
}
