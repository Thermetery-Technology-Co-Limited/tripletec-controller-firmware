"""Operator-triggered, hardware-timed TEC1 switching diagnostic over Pico SWD.

No firmware is flashed and no normal ARM interlock is changed. The CPU pauses
at its idle-loop watchdog function while TIM2 emits one 1ms pulse. PWM2/OPM
returns the pin LOW even if the host stops. ADC1/DMA capture is timer-triggered.
Only the explicit GUI action calls this; connecting/polling never calls it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time

from .device_link import PROBE_MUTEX, _tcl_path

IMAGE_SHA256 = "23702a03ff4ed2bb82ad66e9e5dd98205bd18d6609957049cf60f8600b80a626"
IMAGE_BYTES = 27796
IDLE_ADDRESS = 0x080024E8  # board_watchdog_feed in the exact image above.
BUFFER_ADDRESS = 0x2000B000  # >40KiB above static data, >2KiB below required MSP.
CAPTURE_BYTES = 256
PULSE_US = 1000


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_preflight(status: dict) -> None:
    try:
        good = (
            status["schema"] == 2 and status["outputs_locked_off"] is True
            and status["armed_mask"] == 0 and status["applied_duty"] == [0, 0, 0]
            and status["mode"] == 0 and _finite(status["margin_c"])
            and status["margin_c"] >= 3 and status["valid_flags"] & 0x27 == 0x27
            and _finite(status["ambient_c"]) and _finite(status["target_c"])
            and len(status["plate_c"]) == 3 and _finite(status["plate_c"][0])
            and 20 <= status["plate_c"][0] <= 45
            and status["plate_c"][0] > status["target_c"] + 1
            and _finite(status["current_a_nominal"][0])
            and abs(status["current_a_nominal"][0]) <= 1.5
            and type(status["current_adc_raw"][0]) is int
            and 1975 <= status["current_adc_raw"][0] <= 2120
            and status["adc_diagnostics"]["valid"] is True
            and status["current_limit_policy"]["valid"] is True
            and status["current_limits_a"] == [0, 9, 9]
            and status["current_limits_commissioned_mask"] == 0
            and type(status["uptime_ms"]) is int
        )
        age = (status["uptime_ms"] - status["adc_diagnostics"]["uptime_ms"]) & 0xFFFFFFFF
        age = min(age, (-age) & 0xFFFFFFFF)
        good = good and age <= 1000
    except (KeyError, TypeError, IndexError, ValueError):
        good = False
    if not good:
        raise RuntimeError("TEC1 test requires fresh stopped bench readings, a near-zero current baseline, and plate > target +1°C.")


def decode_capture(raw: bytes, baseline_adc: int) -> dict:
    if len(raw) != CAPTURE_BYTES or type(baseline_adc) is not int or not 1975 <= baseline_adc <= 2120:
        raise RuntimeError("TEC1 ADC capture or zero baseline is invalid")
    samples = struct.unpack("<128H", raw)
    if any(not 0 < value < 4095 for value in samples):
        raise RuntimeError("TEC1 current capture reached an ADC rail; no repeat was issued")
    return {"peak_adc": max(samples), "min_adc": min(samples), "sample_count": len(samples),
            "baseline_adc": baseline_adc,
            "peak_current_a_nominal": (max(samples) - baseline_adc) * 3.3 / 4095 / 0.0264,
            "samples_adc": list(samples)}


def make_pulse_script(context: dict) -> str:
    """Build a fixed timer transaction; fire=False exercises it with GPIO LOW."""
    backup = _tcl_path(Path(context["backup_path"]))
    capture = _tcl_path(Path(context["capture_path"]))
    recovery = _tcl_path(Path(context["recovery_path"]))
    state = _tcl_path(Path(context["state_path"]))
    old_debug = int(context["old_debug"])
    fire = bool(context["fire"])
    # Register values are captured only at the idle breakpoint, where no ADC
    # polling function is suspended. Recovery is written before enabling a pin.
    registers = [0x40021014, 0x40021018, 0x4002101C, 0x40010004, 0x40010800,
                 0x40012404, 0x40012408, 0x4001240C, 0x4001242C, 0x40012430, 0x40012434,
                 0x40020008, 0x4002000C, 0x40020010, 0x40020014]
    save_lines = "\n".join(f"set saved({address:#x}) [rd {address:#x}]" for address in registers)
    restore_order = [0x40012404, 0x4001240C, 0x4001242C, 0x40012430, 0x40012434,
                     0x40012408, 0x4002000C, 0x40020010, 0x40020014, 0x40020008,
                     0x40010004, 0x40010800, 0x40021014, 0x40021018, 0x4002101C]
    restore_lines = "\n".join(f'puts $recovery_file "mww {address:#x} $saved({address:#x})"'
                              for address in restore_order)
    enable = "mww 0x40010800 [expr {($saved(0x40010800) & ~0xf0) | 0xa0}]" if fire else "# Dry run: PA1 remains GPIO LOW; timer output is not connected to it."
    return f"""
    proc rd {{address}} {{ return [lindex [read_memory $address 32 1] 0] }}
set idle {IDLE_ADDRESS:#x}
set recovery_ready 0
set idle_bp 0
set outcome [catch {{
    halt
    bp $idle 2 hw
    set idle_bp 1
    resume
    wait_halt 1000
    rbp $idle
    set idle_bp 0
    set pc [dict get [get_reg {{pc}}] pc]
    set sp [dict get [get_reg {{msp}}] msp]
    if {{($pc & 0xfffffffe) != $idle || $sp < 0x2000b800 || $sp > 0x2000c000}} {{error "CPU idle/stack checkpoint failed"}}
    {save_lines}
    if {{[rd 0x40021004] != 0x0011840a || ([rd 0x40000000] & 1) || [rd 0x40000020] != 0 || ([rd 0x40010808] & 14) || ([rd 0x4001080c] & 14)}} {{error "Clock/output preflight failed"}}
    if {{($saved(0x40020008) & 1) || ($saved(0x40012408) & 2)}} {{error "ADC/DMA already active"}}
    dump_image {backup} {BUFFER_ADDRESS:#x} {CAPTURE_BYTES}
    set recovery_file [open {recovery} w]
    puts $recovery_file "halt"
    puts $recovery_file "mww 0x40000020 0"
    puts $recovery_file "mww 0x40000000 0"
    puts $recovery_file "mww 0x40010810 0x000e0000"
    puts $recovery_file "mww 0x40010800 [expr {{($saved(0x40010800) & ~0xfff0) | 0x2220}}]"
    puts $recovery_file "mww 0x40012408 0"
    puts $recovery_file "mww 0x40020008 0"
    puts $recovery_file "load_image {backup} {BUFFER_ADDRESS:#x} bin"
    puts $recovery_file "mww 0x40012400 0"
    puts $recovery_file "mww 0x40020004 15"
    {restore_lines}
    puts $recovery_file "mww 0xe0042004 {old_debug}"
    puts $recovery_file "mww 0x40003000 0xaaaa"
    puts $recovery_file "resume"
    close $recovery_file
    set recovery_ready 1
    # Keep watchdog frozen while CPU is halted, but TIM2 MUST keep running.
    mww 0xe0042004 [expr {{({old_debug} | 0x100) & ~0x800}}]
    mww 0x40021018 [expr {{$saved(0x40021018) | 5}}]
    mww 0x4002101c [expr {{$saved(0x4002101c) | 1}}]
    mww 0x40021014 [expr {{$saved(0x40021014) | 1}}]
    # Explicitly retain SWD while selecting default TIM2 pin mapping.
    mww 0x40010004 [expr {{($saved(0x40010004) & ~0x07000300) | 0x02000000}}]
    mww 0x40000000 0
    mww 0x40000004 0
    mww 0x40000008 0
    mww 0x4000000c 0
    mww 0x40000020 0
    mww 0x40000018 0x7000
    mww 0x4000001c 0
    mww 0x40000028 71
    mww 0x4000002c 1000
    mww 0x40000038 1
    mww 0x40000014 1
    mww 0x40000010 0
    mww 0x40000024 0
    mww 0x40000000 8
    # ADC1 channel10, 239.5-cycle sample; trigger TIM2_CC2 then continuous.
    mww 0x40012408 0
    mww 0x40012404 0
    mww 0x4001242c 0
    mww 0x40012430 0
    mww 0x40012434 10
    mww 0x40012400 0
    mww 0x40020008 0
    mww 0x40020004 15
    mww 0x4002000c 128
    mww 0x40020010 0x4001244c
    mww 0x40020014 {BUFFER_ADDRESS:#x}
    mww 0x40020008 0x2581
    mww 0x40012408 0x00960103
    sleep 1
    if {{[rd 0x40000024] != 0 || ([rd 0x40000000] & 1) || ([rd 0xe0042004] & 0x800)}} {{error "Timer setup failed"}}
    mww 0x40000020 0x10
    {enable}
    # This is the sole launch write. No loop retries or re-arms it.
    mww 0x40000000 9
    sleep 5
    set cr1 [rd 0x40000000]
    set sr [rd 0x40000010]
    set cnt [rd 0x40000024]
    set remaining [rd 0x4002000c]
    set dma_status [rd 0x40020000]
    set gpio [rd 0x40010808]
    set state_file [open {state} w]
    puts $state_file "$cr1 $sr $cnt $remaining $dma_status $gpio"
    close $state_file
    dump_image {capture} {BUFFER_ADDRESS:#x} {CAPTURE_BYTES}
    if {{($cr1 & 1) || !($sr & 1) || $cnt != 0 || $remaining != 0 || !($dma_status & 2) || ($dma_status & 8) || ($gpio & 14)}} {{error "Timer/ADC completion failed: CR1=$cr1 SR=$sr CNT=$cnt DMA_remaining=$remaining DMA_status=$dma_status GPIO=$gpio"}}
}} detail]
if {{$idle_bp}} {{catch {{rbp $idle}}}}
if {{$recovery_ready}} {{
    set cleanup_failed [catch "source {recovery}" cleanup_detail]
}} else {{
    set cleanup_failed [catch {{
        mww 0x40000020 0
        mww 0x40000000 0
        mww 0x40010810 0x000e0000
        mww 0xe0042004 {old_debug}
        resume
    }} cleanup_detail]
}}
if {{$cleanup_failed}} {{error "TEC1 cleanup failed: $cleanup_detail"}}
if {{$outcome}} {{error $detail}}
shutdown
"""


def _cpu_run(link, script: Path) -> str:
    args = [link.openocd, "-f", "interface/cmsis-dap.cfg", "-c", "cmsis-dap backend usb_bulk"]
    if link.serial:
        args += ["-c", f"adapter serial {link.serial}"]
    for command in ("transport select swd", "gdb port disabled", "tcl port disabled", "telnet port disabled"):
        args += ["-c", command]
    args += ["-f", "target/stm32f1x.cfg", "-c", "reset_config none", "-c", "init", "-f", str(script)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=12,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    log = result.stdout + result.stderr
    if result.returncode:
        raise RuntimeError(log[-1600:])
    return log


def run_tec1_pulse(link, *, fire: bool = True) -> dict:
    """The real pulse is called only by the operator's GUI button; never retry."""
    with PROBE_MUTEX, tempfile.TemporaryDirectory(prefix="tec1-pulse-") as temporary:
        directory = Path(temporary)
        metadata = link._metadata()
        if metadata.get("firmware_sha256") != IMAGE_SHA256:
            raise RuntimeError("TEC1 test requires the exact validated 0.3.2 bench image")
        before = link._read(metadata)[0]
        validate_preflight(before)
        time.sleep(0.3)
        fresh = link._read(metadata)[0]
        validate_preflight(fresh)
        if fresh["sequence"] == before["sequence"] or fresh["uptime_ms"] == before["uptime_ms"]:
            raise RuntimeError("Telemetry is not advancing; TEC1 test was not started")
        baseline = round((before["current_adc_raw"][0] + fresh["current_adc_raw"][0]) / 2)
        if abs(before["current_adc_raw"][0] - fresh["current_adc_raw"][0]) > 20:
            raise RuntimeError("TEC1 current baseline is unstable; test was not started")
        flash, debug = directory / "flash.bin", directory / "debug.bin"
        link._run([f"dump_image {_tcl_path(flash)} 0x08000000 {IMAGE_BYTES}",
                   f"dump_image {_tcl_path(debug)} 0xe0042004 4"])
        if hashlib.sha256(flash.read_bytes()).hexdigest() != IMAGE_SHA256:
            raise RuntimeError("Connected firmware differs from the expected bench image; no pulse issued")
        context = {"backup_path": directory / "ram-before.bin", "capture_path": directory / "capture.bin",
                   "recovery_path": directory / "recover.tcl", "state_path": directory / "state.txt",
                   "old_debug": struct.unpack("<I", debug.read_bytes())[0], "fire": fire}
        script = directory / "pulse.tcl"
        script.write_text(make_pulse_script(context), encoding="utf-8")
        try:
            log = _cpu_run(link, script)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            # Recovery contains no launch write. An uncertain test is NEVER retried.
            recovery = Path(context["recovery_path"])
            if recovery.exists():
                recovery.write_text(recovery.read_text() + "\nshutdown\n", encoding="utf-8")
                try:
                    _cpu_run(link, recovery)
                except Exception as cleanup_error:
                    raise RuntimeError(f"TEC1 test uncertain; cleanup also failed: {cleanup_error}") from error
            raise RuntimeError(f"TEC1 test failed; no repeat issued: {error}") from error
        capture = decode_capture(Path(context["capture_path"]).read_bytes(), baseline)
        time.sleep(0.35)  # Require resumed acquisition, not just retained RAM.
        post = link._read(metadata)[0]
        validate_preflight(post)
        if post["uptime_ms"] == fresh["uptime_ms"] or post["sequence"] == fresh["sequence"]:
            raise RuntimeError("TEC1 test ended but resumed telemetry was not confirmed; no repeat issued")
        cr1, sr, cnt, remaining, dma_status, gpio = (
            int(word, 0) for word in Path(context["state_path"]).read_text().split())
        capture.update({"timer_complete": not cr1 & 1 and bool(sr & 1) and cnt == 0,
                        "elapsed_nominal_us": PULSE_US, "dry_run": not fire,
                        "gpio_low": gpio & 14 == 0, "dma_remaining": remaining,
                        "dma_status": dma_status})
        # Save evidence under user-writable LocalAppData when packaged.
        evidence_root = Path(os.environ.get("LOCALAPPDATA", str(link.root / "build"))) / "TripleTECControl" / "tests"
        evidence_root.mkdir(parents=True, exist_ok=True)
        stamp = time.time_ns()
        report_path = evidence_root / f"tec1-{'pulse' if fire else 'dry-run'}-{stamp}.json"
        report_path.write_text(json.dumps({"before": fresh, "pulse_test": capture, "after": post}, indent=2) + "\n", encoding="utf-8")
        report_path.with_suffix(".log").write_text(log, encoding="utf-8")
        post["pulse_test"] = capture
        return post
