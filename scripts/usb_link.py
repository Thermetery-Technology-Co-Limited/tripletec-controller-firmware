"""Bounded STM32 USB CDC transport; never replays an uncertain command."""
from __future__ import annotations

import secrets
import struct
import threading
import time
import zlib

from .read_status import decode, consistent_adc_diagnostics, consistent_limit_configuration
from .device_link import require_bench_lock_support, validate_bench_lock_ack

USB_VID = 0xCAFE
USB_PID = 0x4001
VERSION = 1
REQUEST_MAGIC = b"TECQ"
RESPONSE_MAGIC = b"TECR"
PAYLOAD_SIZE = 224
REQUEST = struct.Struct("<4sBBHIi")
RESPONSE = struct.Struct("<4sBBHI")
READ_TIMEOUT = 3.0
WRITE_TIMEOUT = 1.0
SESSION_SETTLE_SECONDS = 0.05
OPCODES = {"status": 0, "stop": 1, "mode": 2, "margin": 3, "arm": 4, "heartbeat": 5,
           "bench_lock": 6}
RESULT_NAMES = {0: "OK", 1: "BAD_PROTOCOL", 2: "BAD_CHECKSUM", 3: "BAD_OPCODE",
                4: "BAD_VALUE", 5: "BENCH_LOCKED", 6: "INTERLOCK"}


def _serial_module():
    try:
        import serial
        return serial
    except ImportError as error:
        raise RuntimeError("USB CDC needs pyserial. Install it with python -m pip install pyserial.") from error


def list_usb_devices() -> list[dict]:
    """Only list our development CDC identity; never open unrelated serial ports."""
    _serial_module()
    from serial.tools import list_ports
    return sorted(({
        "device": port.device, "description": port.description or port.device,
        "serial_number": port.serial_number or "", "vid": port.vid, "pid": port.pid,
    } for port in list_ports.comports() if port.vid == USB_VID and port.pid == USB_PID),
                  key=lambda item: item["device"])


def request_packet(sequence: int, opcode: int, value: int = 0) -> bytes:
    if type(sequence) is not int or not 0 < sequence <= 0xFFFFFFFF:
        raise ValueError("USB request sequence must be a nonzero uint32")
    if type(opcode) is not int or not 0 <= opcode <= 255:
        raise ValueError("USB opcode must be a uint8")
    if type(value) is not int or not -(2**31) <= value < 2**31:
        raise ValueError("USB request value must be an int32")
    body = REQUEST.pack(REQUEST_MAGIC, VERSION, opcode, 0, sequence, value)
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def decode_payload(payload: bytes) -> dict:
    if len(payload) != PAYLOAD_SIZE:
        raise ValueError("USB telemetry payload is incomplete")
    status = decode(payload[:160])
    if status["schema"] != 2:
        raise ValueError("USB control requires schema 2 telemetry")
    try:
        status["adc_diagnostics"] = consistent_adc_diagnostics(payload[160:192], payload[160:192])
    except ValueError as error:
        status["adc_diagnostics"] = {"valid": False, "error": str(error)}
    try:
        policy = consistent_limit_configuration(payload[192:224], payload[192:224])
        age = min((status["uptime_ms"] - policy["uptime_ms"]) & 0xFFFFFFFF,
                  (policy["uptime_ms"] - status["uptime_ms"]) & 0xFFFFFFFF)
        if age > 2000:
            raise ValueError("Current-limit policy is stale")
        status["current_limit_policy"] = policy
        status["current_limits_a"] = policy["current_limits_a"]
        status["current_limits_commissioned_mask"] = policy["current_limits_commissioned_mask"]
    except ValueError as error:
        # Keep monitoring and STOP usable. Missing flattened policy fields make
        # the presentation model refuse Start, rather than invent safe limits.
        status["current_limit_policy"] = {"valid": False, "error": str(error)}
    return status


class UsbLink:
    def __init__(self, port: str | None = None, serial_number: str | None = None):
        candidates = list_usb_devices()
        if serial_number:
            candidates = [item for item in candidates if item["serial_number"] == serial_number]
        if port:
            candidates = [item for item in candidates if item["device"] == port]
        if not candidates:
            raise RuntimeError("STM32 USB CDC was not found. Connect the controller USB-C using a data cable.")
        if len(candidates) != 1:
            raise RuntimeError("Multiple STM32 USB devices found. Select a COM port in Connection settings.")
        self.device = candidates[0]
        self.port = self.device["device"]
        self.lock = threading.RLock()
        self.sequence = secrets.randbits(32) or 1
        self.stream = None
        try:
            # Start with DTR low before opening the handle. A deliberate edge
            # resets firmware's replay/session state even when a platform's
            # serial driver preserves line state after the previous close.
            self.stream = _serial_module().Serial(port=None, baudrate=115200,
                                                  timeout=0.1, write_timeout=WRITE_TIMEOUT)
            self.stream.dtr = False
            self.stream.port = self.port
            self.stream.open()
            time.sleep(SESSION_SETTLE_SECONDS)
            self.stream.dtr = True
            time.sleep(SESSION_SETTLE_SECONDS)
        except Exception as error:
            self.close()
            raise RuntimeError(f"Cannot open {self.port}: {error}") from error

    def close(self):
        with self.lock:
            stream, self.stream = self.stream, None
            if stream is not None:
                try:
                    # A closed session disarms the firmware and discards its
                    # fragments. Driver/device errors during unplug are benign.
                    stream.dtr = False
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass

    def _read_exact(self, length: int, deadline: float) -> bytes:
        data = bytearray()
        while len(data) < length:
            if time.monotonic() >= deadline:
                raise RuntimeError("USB response timed out. Reconnect and check status before retrying a command.")
            block = self.stream.read(length - len(data))
            if block:
                data.extend(block)
        return bytes(data)

    def _exchange(self, opcode: int, value: int = 0) -> tuple[int, dict]:
        if self.stream is None:
            raise RuntimeError("USB device is disconnected. Select Connect to reopen it.")
        self.sequence = (self.sequence + 1) & 0xFFFFFFFF or 1
        sequence = self.sequence
        packet = request_packet(sequence, opcode, value)
        try:
            # Discard bytes left by an earlier session; a request is sent once.
            self.stream.reset_input_buffer()
            if self.stream.write(packet) != len(packet):
                raise RuntimeError("USB request write was incomplete; its outcome is uncertain. Reconnect and check status.")
            deadline = time.monotonic() + READ_TIMEOUT
            discarded = 0
            while time.monotonic() < deadline:
                prefix = self._read_exact(4, deadline)
                while prefix != RESPONSE_MAGIC:
                    prefix = prefix[1:] + self._read_exact(1, deadline)
                    discarded += 1
                    if discarded > 4096:
                        raise RuntimeError("USB stream contains no valid controller response")
                header = prefix + self._read_exact(RESPONSE.size - 4, deadline)
                _, version, result, size, reply_sequence = RESPONSE.unpack(header)
                if version != VERSION or size != PAYLOAD_SIZE:
                    raise RuntimeError("USB response uses an unsupported protocol or payload size")
                payload = self._read_exact(size, deadline)
                checksum, = struct.unpack("<I", self._read_exact(4, deadline))
                if checksum != zlib.crc32(header + payload) & 0xFFFFFFFF:
                    raise RuntimeError("USB response checksum failed; no state was accepted. Reconnect and check status.")
                if reply_sequence != sequence:
                    discarded += len(header) + size + 4
                    if discarded > 4096:
                        raise RuntimeError("USB stream contains only stale responses")
                    continue
                status = decode_payload(payload)
                status.update(transport="usb", usb_port=self.port,
                              usb_serial=self.device["serial_number"])
                return result, status
            raise RuntimeError("USB response did not echo the request sequence")
        except Exception as error:
            # Never reuse a stream with uncertain framing or silently resend an
            # operation. The next connection is an explicit, status-only action.
            self.close()
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError(f"USB transaction failed: {error}. Reconnect and check status.") from error

    def read_status(self) -> dict:
        with self.lock:
            result, status = self._exchange(OPCODES["status"])
            if result != 0:
                raise RuntimeError(f"Controller rejected status: {RESULT_NAMES.get(result, 'UNKNOWN')}")
            if status["armed_mask"]:
                return self.command("heartbeat")
            return status

    def command(self, name: str, value: int = 0) -> dict:
        if name not in OPCODES or name == "status" or type(value) is not int:
            raise RuntimeError("Unsupported controller command")
        if ((name in ("stop", "heartbeat") and value != 0) or
                (name == "mode" and value not in (0, 1)) or
                (name == "bench_lock" and value not in (0, 1)) or
                (name == "margin" and not 500 <= value <= 20000) or
                (name == "arm" and not 1 <= value <= 7)):
            raise RuntimeError("Command value is outside its allowed range")
        with self.lock:
            if name == "bench_lock":
                result, before = self._exchange(OPCODES["status"])
                if result != 0:
                    raise RuntimeError(f"Controller rejected status: {RESULT_NAMES.get(result, 'UNKNOWN')}")
                require_bench_lock_support(before)
            result, status = self._exchange(OPCODES[name], value)
            if result != 0:
                raise RuntimeError(f"Controller rejected {name}: {RESULT_NAMES.get(result, 'UNKNOWN')}")
            if name == "mode" and status["mode"] != value:
                raise RuntimeError("Controller ACK did not echo the requested mode")
            if name == "margin" and (status["margin_c"] is None or
                                      abs(status["margin_c"] - value / 1000) > 0.0005):
                raise RuntimeError("Controller ACK did not echo the requested margin")
            if name == "stop" and (status["armed_mask"] or any(status["applied_duty"])):
                raise RuntimeError("Controller ACK did not confirm stopped outputs")
            if name == "bench_lock":
                validate_bench_lock_ack(status, value)
            return status
