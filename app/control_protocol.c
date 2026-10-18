#include "control_protocol.h"

#include <stddef.h>
#include <stdatomic.h>

uint32_t control_protocol_checksum(const control_mailbox_t *request)
{
    uint32_t crc = UINT32_MAX;
    uint32_t words[7];
    if (request == NULL) {
        return 0U;
    }
    words[0] = request->magic;
    words[1] = request->version;
    words[2] = request->request_seq;
    words[3] = request->opcode;
    words[4] = (uint32_t)request->arg0;
    words[5] = (uint32_t)request->arg1;
    words[6] = request->reserved;
    for (size_t word = 0U; word < 7U; ++word) {
        for (unsigned byte = 0U; byte < 4U; ++byte) {
            crc ^= (words[word] >> (8U * byte)) & 0xFFU;
            for (unsigned bit = 0U; bit < 8U; ++bit) {
                crc = (crc >> 1U) ^ (UINT32_C(0xEDB88320) & (0U - (crc & 1U)));
            }
        }
    }
    return ~crc;
}

void control_protocol_init(control_protocol_t *protocol)
{
    if (protocol != NULL) {
        *protocol = (control_protocol_t){0U, CONTROL_OK, 0U};
    }
}

bool control_protocol_lease_expired(const control_protocol_t *protocol, bool any_armed,
                                    uint32_t now_ms, uint32_t lease_ms)
{
    return any_armed && (protocol == NULL || lease_ms == 0U ||
                         now_ms - protocol->last_valid_ms > lease_ms);
}

static control_result_t validate(const control_mailbox_t *request)
{
    if (request->magic != CONTROL_MAILBOX_MAGIC ||
        request->version != CONTROL_PROTOCOL_VERSION ||
        request->arg1 != 0 || request->reserved != 0U) {
        return CONTROL_BAD_PROTOCOL;
    }
    if (request->checksum != control_protocol_checksum(request)) {
        return CONTROL_BAD_CHECKSUM;
    }
    switch (request->opcode) {
    case CONTROL_STOP:
    case CONTROL_HEARTBEAT:
        return request->arg0 == 0 ? CONTROL_OK : CONTROL_BAD_VALUE;
    case CONTROL_SET_MODE:
    case CONTROL_SET_BENCH_LOCK:
        return request->arg0 == 0 || request->arg0 == 1 ? CONTROL_OK : CONTROL_BAD_VALUE;
    case CONTROL_SET_MARGIN:
        return request->arg0 >= 500 && request->arg0 <= 20000 ? CONTROL_OK : CONTROL_BAD_VALUE;
    case CONTROL_ARM:
        return request->arg0 >= 1 && request->arg0 <= 7 ? CONTROL_OK : CONTROL_BAD_VALUE;
    case CONTROL_COMMISSION_PREPARE:
        return request->arg0 == 15000 ? CONTROL_OK : CONTROL_BAD_VALUE;
    case CONTROL_COMMISSION_TEST:
        return request->arg0 >= 0 && request->arg0 <= 4 ? CONTROL_OK : CONTROL_BAD_VALUE;
    default:
        return CONTROL_BAD_OPCODE;
    }
}

bool control_protocol_poll(control_protocol_t *protocol,
                           volatile const control_mailbox_t *mailbox,
                           control_apply_fn apply, void *context, uint32_t now_ms)
{
    control_mailbox_t request;
    uint32_t sequence;
    if (protocol == NULL || mailbox == NULL) {
        return false;
    }
    sequence = mailbox->request_seq;
    if (sequence == 0U || sequence == protocol->ack_seq ||
        (protocol->ack_seq != 0U && sequence - protocol->ack_seq >= UINT32_C(0x80000000))) {
        return false;
    }
    atomic_signal_fence(memory_order_seq_cst);
    request = *mailbox;
    atomic_signal_fence(memory_order_seq_cst);
    if (mailbox->request_seq != sequence || request.request_seq != sequence) {
        return false;
    }
    protocol->result = validate(&request);
    if (protocol->result == CONTROL_OK) {
        protocol->last_valid_ms = now_ms;
        protocol->result = apply != NULL ? apply(context, request.opcode, request.arg0, now_ms) :
                                         CONTROL_INTERLOCK;
    }
    protocol->ack_seq = sequence;
    return true;
}
