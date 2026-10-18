#ifndef TEC_USB_PROTOCOL_H
#define TEC_USB_PROTOCOL_H

#include "control_protocol.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define USB_PROTOCOL_VERSION 1U
#define USB_PROTOCOL_STATUS 0U
#define USB_PROTOCOL_REQUEST_SIZE 20U
#define USB_PROTOCOL_PAYLOAD_SIZE 224U
#define USB_PROTOCOL_RESPONSE_SIZE 240U

/* All fields are little endian. Request: "TECQ", version u8, opcode u8,
 * reserved u16 (zero), nonzero sequence u32, value i32, CRC32 u32 over bytes
 * 0..15. Response: "TECR", version u8, control_result_t u8, payload size u16
 * (224), echoed sequence u32, payload, CRC32 u32 over bytes 0..235.
 * CRC is CRC32/ISO-HDLC (polynomial 0xEDB88320, init/final XOR 0xFFFFFFFF).
 */
typedef control_result_t (*usb_protocol_apply_fn)(void *context, uint8_t opcode,
                                                 int32_t value, uint32_t sequence);
typedef void (*usb_protocol_snapshot_fn)(void *context,
                                         uint8_t payload[USB_PROTOCOL_PAYLOAD_SIZE]);
/* Must copy the bytes before returning true. False means no bytes accepted;
 * poll retries the entire response, and no subsequent request can apply until
 * the response has been accepted. This callback must never wait for USB I/O.
 */
typedef bool (*usb_protocol_enqueue_fn)(void *context, const uint8_t *bytes,
                                        size_t length);

typedef struct {
    uint8_t input[USB_PROTOCOL_REQUEST_SIZE];
    size_t input_used;
    uint8_t response[USB_PROTOCOL_RESPONSE_SIZE];
    bool response_pending;
    uint8_t last_request[USB_PROTOCOL_REQUEST_SIZE];
    uint8_t last_response[USB_PROTOCOL_RESPONSE_SIZE];
    uint32_t last_sequence;
    usb_protocol_apply_fn apply;
    usb_protocol_snapshot_fn snapshot;
    usb_protocol_enqueue_fn enqueue;
    void *context;
} usb_protocol_t;

uint32_t usb_protocol_crc32(const uint8_t *bytes, size_t length);
void usb_protocol_init(usb_protocol_t *protocol, usb_protocol_apply_fn apply,
                        usb_protocol_snapshot_fn snapshot,
                        usb_protocol_enqueue_fn enqueue, void *context);
/* Returns true if no response is waiting for enqueue, false under backpressure.
 * Call from the main loop, never from an ISR concurrently with feed/poll.
 */
bool usb_protocol_poll(usb_protocol_t *protocol);
/* Returns consumed input length. Retain any unconsumed bytes and feed them
 * after poll succeeds. Arbitrary fragmentation, concatenation and garbage are
 * supported with fixed storage. CRC-corrupt frames and sequence zero are
 * discarded without an ACK or actuation. Valid CRC but invalid version,
 * reserved fields, opcode or value receive an error ACK without actuation.
 * STATUS does not invoke apply, so polling cannot renew a control lease.
 * Exact replay of the most recent request resends its cached response without
 * applying or taking a new snapshot. Other non-forward sequences are rejected.
 */
size_t usb_protocol_feed(usb_protocol_t *protocol, const uint8_t *bytes, size_t length);

#endif
