#ifndef TEC_CONTROL_PROTOCOL_H
#define TEC_CONTROL_PROTOCOL_H

#include <stdbool.h>
#include <stdint.h>

#define CONTROL_MAILBOX_MAGIC UINT32_C(0x54454343)
#define CONTROL_PROTOCOL_VERSION 1U

typedef enum {
    CONTROL_STOP = 1U,
    CONTROL_SET_MODE = 2U,
    CONTROL_SET_MARGIN = 3U,
    CONTROL_ARM = 4U,
    CONTROL_HEARTBEAT = 5U
} control_opcode_t;

typedef enum {
    CONTROL_OK = 0U,
    CONTROL_BAD_PROTOCOL = 1U,
    CONTROL_BAD_CHECKSUM = 2U,
    CONTROL_BAD_OPCODE = 3U,
    CONTROL_BAD_VALUE = 4U,
    CONTROL_BENCH_LOCKED = 5U,
    CONTROL_INTERLOCK = 6U
} control_result_t;

/* Eight little-endian 32-bit words. Host invalidates request_seq to zero,
 * writes the body, then commits the nonzero sequence at offset 8 LAST.
 * checksum is CRC32/ISO-HDLC over the first 28 bytes, including committed seq.
 * arg1 and reserved are zero in version 1. Sequence increments modulo uint32,
 * skipping zero; stale and duplicate requests never apply again.
 */
typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t request_seq;
    uint32_t opcode;
    int32_t arg0;
    int32_t arg1;
    uint32_t reserved;
    uint32_t checksum;
} control_mailbox_t;

typedef struct {
    uint32_t ack_seq;
    control_result_t result;
    uint32_t last_valid_ms;
} control_protocol_t;

typedef control_result_t (*control_apply_fn)(void *context, uint32_t opcode,
                                            int32_t value, uint32_t now_ms);

_Static_assert(sizeof(control_mailbox_t) == 32U, "command mailbox layout changed");
uint32_t control_protocol_checksum(const control_mailbox_t *request);
void control_protocol_init(control_protocol_t *protocol);
bool control_protocol_lease_expired(const control_protocol_t *protocol, bool any_armed,
                                    uint32_t now_ms, uint32_t lease_ms);
/* true means a stable new request was acknowledged, including rejected ones. */
bool control_protocol_poll(control_protocol_t *protocol,
                           volatile const control_mailbox_t *mailbox,
                           control_apply_fn apply, void *context, uint32_t now_ms);

#endif
