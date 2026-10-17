"""Bounded, serialized Pico SWD transport; never halts, resets or writes PWM."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import zlib

try:
    from .read_status import consistent_snapshot
except ImportError:
    from read_status import consistent_snapshot

ROOT = Path(__file__).resolve().parents[1]
MAILBOX_MAGIC = 0x54454343
PROTOCOL_VERSION = 1
MAILBOX_SIZE = 32
OPCODES = {"stop": 1, "mode": 2, "margin": 3, "arm": 4, "heartbeat": 5}
PROBE_MUTEX = threading.RLock()
SUBPROCESS_TIMEOUT = 5.0
ACK_TIMEOUT = 3.0


def command_packet(sequence: int, opcode: int, value: int) -> bytes:
    """Body has an invalid zero commit; its CRC covers the eventual sequence."""
    if not 0 < sequence <= 0xFFFFFFFF:
        raise ValueError("Command sequence must be a nonzero uint32")
    words = [MAILBOX_MAGIC, PROTOCOL_VERSION, sequence, opcode, value & 0xFFFFFFFF, 0, 0]
    checksum = zlib.crc32(struct.pack("<7I", *words)) & 0xFFFFFFFF
    words[2] = 0
    return struct.pack("<8I", *words, checksum)


def _ram_region(address: object, length: int) -> int:
    if type(address) is not int or address % 4 or not 0x20000000 <= address <= 0x2000C000 - length:
        raise RuntimeError("Symbol metadata is outside aligned STM32F103RC SRAM; rebuild firmware")
    return address


def _tcl_path(path: Path) -> str:
    text = path.as_posix()
    if any(character in text for character in "{}\r\n\x00"):
        raise RuntimeError("The temporary file path contains unsupported Tcl characters")
    return "{" + text + "}"


class DeviceLink:
    def __init__(self, root: Path | None = None, serial: str | None = None,
                 openocd: str | None = None):
        self.root = Path(root) if root is not None else ROOT
        self.serial = serial
        self.openocd = str(openocd) if openocd else shutil.which("openocd")
        if serial and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", serial):
            raise ValueError("Invalid CMSIS-DAP serial number")

    def _metadata(self) -> dict:
        try:
            metadata = json.loads((self.root / "build/stm32/telemetry-symbol.json").read_text())
            size = metadata["size"]
            if type(size) is not int or size not in (128, 160):
                raise RuntimeError("Unsupported telemetry size; rebuild firmware")
            _ram_region(metadata["address"], size)
            return metadata
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RuntimeError("Firmware symbol metadata missing or invalid; build firmware first") from error

    def _base_commands(self) -> list[str]:
        commands = ["cmsis-dap backend usb_bulk"]
        if self.serial:
            commands.append(f"adapter serial {self.serial}")
        return commands + [
            "transport select swd", "adapter speed 1000", "reset_config none",
            "gdb port disabled", "tcl port disabled", "telnet port disabled",
            "swd newdap status cpu", "dap create status.dap -chain-position status.cpu",
            "target create status.mem mem_ap -dap status.dap -ap-num 0", "init", "poll off",
        ]

    def _run(self, commands: list[str]) -> None:
        if not self.openocd:
            raise RuntimeError("OpenOCD missing; select its executable in the connection settings")
        arguments = [self.openocd, "-f", "interface/cmsis-dap.cfg"]
        for item in self._base_commands() + commands + ["shutdown"]:
            arguments.extend(["-c", item])
        options = {"capture_output": True, "text": True, "timeout": SUBPROCESS_TIMEOUT}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            process = subprocess.run(arguments, **options)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("SWD operation timed out; check target power and the Pico connection") from error
        except OSError as error:
            raise RuntimeError(f"Could not start OpenOCD: {error}") from error
        log = self.root / "build/stm32/last-status-openocd.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(process.stdout + process.stderr, encoding="utf-8")
        if process.returncode:
            detail = (process.stderr or process.stdout).strip()[-1200:]
            raise RuntimeError(f"SWD operation failed: {detail}")

    def _read(self, metadata: dict, mailbox: bool = False) -> tuple[dict, bytes | None]:
        with tempfile.TemporaryDirectory(prefix="tripletec-status-") as temporary:
            directory = Path(temporary)
            snapshots = [directory / f"status{i}.bin" for i in range(4)]
            commands = [f"dump_image {_tcl_path(path)} {metadata['address']:#x} {metadata['size']}"
                        for path in snapshots]
            mailbox_path = directory / "mailbox.bin"
            if mailbox:
                address = _ram_region(metadata.get("mailbox_address"), MAILBOX_SIZE)
                if metadata.get("mailbox_size") != MAILBOX_SIZE:
                    raise RuntimeError("Mailbox metadata is absent or obsolete; rebuild firmware")
                commands.append(f"dump_image {_tcl_path(mailbox_path)} {address:#x} {MAILBOX_SIZE}")
            self._run(commands)
            try:
                for first, second in zip(snapshots, snapshots[1:]):
                    try:
                        status = consistent_snapshot(first.read_bytes(), second.read_bytes())
                        return status, mailbox_path.read_bytes() if mailbox else None
                    except ValueError:
                        continue
            except OSError as error:
                raise RuntimeError("OpenOCD did not produce a complete memory snapshot") from error
            raise RuntimeError("No consistent telemetry snapshot; retry the connection")

    def read_status(self) -> dict:
        with PROBE_MUTEX:
            status = self._read(self._metadata())[0]
            if status["schema"] == 2 and status["armed_mask"]:
                return self.command("heartbeat")
            return status

    def command(self, name: str, value: int = 0) -> dict:
        if name not in OPCODES or type(value) is not int:
            raise RuntimeError("Unsupported controller command")
        if ((name in ("stop", "heartbeat") and value != 0) or
                (name == "mode" and value not in (0, 1)) or
                (name == "margin" and not 500 <= value <= 20000) or
                (name == "arm" and not 1 <= value <= 7)):
            raise RuntimeError("Command value is outside its allowed range")
        with PROBE_MUTEX:
            metadata = self._metadata()
            if metadata.get("schema") != 2 or metadata["size"] != 160:
                raise RuntimeError("This firmware supports read-only status; rebuild and load schema 2")
            status, mailbox = self._read(metadata, mailbox=True)
            if status["schema"] != 2:
                raise RuntimeError("Connected firmware has read-only schema 1; load the matching image")
            if mailbox is None or len(mailbox) != MAILBOX_SIZE:
                raise RuntimeError("Command mailbox snapshot is incomplete")
            magic, version = struct.unpack_from("<2I", mailbox)
            if magic != MAILBOX_MAGIC or version != PROTOCOL_VERSION:
                raise RuntimeError("Command mailbox does not match this firmware; no command was written")
            address = _ram_region(metadata["mailbox_address"], MAILBOX_SIZE)
            sequence = (status["command_ack_seq"] + 1) & 0xFFFFFFFF or 1
            with tempfile.TemporaryDirectory(prefix="tripletec-command-") as temporary:
                packet = Path(temporary) / "command.bin"
                packet.write_bytes(command_packet(sequence, OPCODES[name], value))
                self._run([
                    f"mww {address + 8:#x} 0",  # invalidate before changing any body words
                    f"load_image {_tcl_path(packet)} {address:#x} bin",
                    f"mww {address + 8:#x} {sequence:#x}",  # atomic sequence is the final write
                ])
            deadline = time.monotonic() + ACK_TIMEOUT
            while time.monotonic() < deadline:
                acknowledged = self._read(metadata)[0]
                if acknowledged.get("command_ack_seq") == sequence:
                    if acknowledged["command_result"] != 0:
                        reason = acknowledged.get("command_result_name", "UNKNOWN")
                        raise RuntimeError(f"Controller rejected {name}: {reason}")
                    if name == "mode" and acknowledged["mode"] != value:
                        raise RuntimeError("Controller ACK did not echo the requested mode")
                    if name == "margin" and abs(acknowledged["margin_c"] - value / 1000) > 0.0005:
                        raise RuntimeError("Controller ACK did not echo the requested margin")
                    if name == "stop" and acknowledged["armed_mask"] != 0:
                        raise RuntimeError("Controller ACK did not confirm stopped channels")
                    return acknowledged
                time.sleep(0.05)
            raise RuntimeError("Controller did not acknowledge the command; reconnect and check status")
