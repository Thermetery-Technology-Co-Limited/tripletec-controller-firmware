"""Reproducible bench-firmware and native C test builds; Python standard library only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(args: list[str | Path]) -> None:
    command = [str(x) for x in args]
    print("+ " + subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def compiler(name: str, supplied: str | None, fallback: Path | None = None) -> str:
    found = supplied or shutil.which(name)
    if not found and fallback and fallback.is_file():
        found = str(fallback)
    if not found:
        raise SystemExit(f"Missing {name}; supply its path with --cc or --arm-cc.")
    return found


def host(cc: str) -> None:
    output = ROOT / "build" / "host"
    output.mkdir(parents=True, exist_ok=True)
    groups = {
        "dewpoint": ["core/dewpoint.c"],
        "control": ["core/dewpoint.c", "core/controller.c"],
        "modes": ["core/dewpoint.c", "core/controller.c"],
        "protocol": ["app/control_protocol.c"],
        "usb_protocol": ["app/usb_protocol.c"],
        "usb_descriptors": ["platform/stm32f103/usb_descriptors.c"],
        "bench_lock": ["core/dewpoint.c", "core/controller.c"],
        "commissioning": ["app/commissioning.c", "core/dewpoint.c", "core/controller.c"],
        "commission_stage": [],
        "sensors": sorted(str(p.relative_to(ROOT)) for p in (ROOT / "sensors").glob("*.c")),
    }
    for name, sources in groups.items():
        binary = output / (f"test_{name}.exe" if os.name == "nt" else f"test_{name}")
        extra = (["-Iplatform/stm32f103", "-Ithird_party/tinyusb/src", "-DSTM32F103xE"]
                 if name in ("usb_descriptors", "bench_lock", "commissioning", "commission_stage") else [])
        if name == "bench_lock":
            extra += ["-DBENCH_OUTPUT_LOCK=1", "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections"]
        run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-Icore", "-Isensors", "-Iapp", *extra, *sources, f"tests/test_{name}.c", "-lm", "-o", binary])
        run([binary])
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])


def firmware(cc: str) -> None:
    output = ROOT / "build" / "stm32"
    output.mkdir(parents=True, exist_ok=True)
    sources = sorted(p for folder in ("core", "sensors", "app", "platform/stm32f103")
                     for p in (ROOT / folder).glob("*.c"))
    flags = ["-mcpu=cortex-m3", "-mthumb", "-Os", "-g3",
             "-ffunction-sections", "-fdata-sections", "-fno-common", "-ffreestanding",
             "-Wall", "-Wextra", "-Werror", "-DBENCH_OUTPUT_LOCK=1", "-DSTM32F103xE",
             "-Icore", "-Isensors", "-Iapp", "-Iplatform/stm32f103",
             "-Ithird_party/tinyusb/src", "-Ithird_party/cmsis_core/Include",
             "-Ithird_party/cmsis_stm32f1/Include"]
    vendor_sources = [
        "third_party/tinyusb/src/tusb.c", "third_party/tinyusb/src/common/tusb_fifo.c",
        "third_party/tinyusb/src/device/usbd.c", "third_party/tinyusb/src/class/cdc/cdc_device.c",
        "third_party/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c",
        "third_party/tinyusb/src/portable/st/stm32_fsdev/fsdev_common.c",
    ]
    sources.extend(ROOT / name for name in vendor_sources)
    objects = []
    for source in sources:
        obj = output / ("_".join(source.relative_to(ROOT).parts) + ".o")
        dialect = "-std=gnu11" if "third_party" in source.parts else "-std=c11"
        run([cc, *flags, dialect, "-c", source, "-o", obj])
        objects.append(obj)
    elf = output / "tripletec-bench.elf"
    run([cc, "-mcpu=cortex-m3", "-mthumb", "-nostartfiles", "--specs=nano.specs",
         "--specs=nosys.specs", "-Tplatform/stm32f103/stm32f103rc.ld",
         "-Wl,--gc-sections", f"-Wl,-Map={output / 'tripletec-bench.map'}",
         *objects, "-lm", "-o", elf])
    prefix = str(Path(cc).with_name(Path(cc).name.replace("gcc", "{}")))
    run([prefix.format("size"), elf])
    for kind, extension in (("binary", "bin"), ("ihex", "hex")):
        run([prefix.format("objcopy"), "-O", kind, elf, output / f"tripletec-bench.{extension}"])
    symbols = subprocess.check_output([prefix.format("nm"), "-S", str(elf)], text=True)
    addresses = {}
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[3] in ("g_telemetry", "g_command_mailbox", "g_adc_diagnostics", "g_limit_configuration", "g_commissioning"):
            addresses[fields[3]] = (int(fields[0], 16), int(fields[1], 16))
    if set(addresses) != {"g_telemetry", "g_command_mailbox", "g_adc_diagnostics", "g_limit_configuration", "g_commissioning"}:
        raise SystemExit("Telemetry/control mailbox missing from linked image")
    address, size = addresses["g_telemetry"]
    mailbox_address, mailbox_size = addresses["g_command_mailbox"]
    diagnostic_address, diagnostic_size = addresses["g_adc_diagnostics"]
    limit_address, limit_size = addresses["g_limit_configuration"]
    commissioning_address, commissioning_size = addresses["g_commissioning"]
    if size != 160 or mailbox_size != 32 or diagnostic_size != 32 or limit_size != 32 or commissioning_size != 104:
        raise SystemExit("Firmware telemetry/mailbox layout changed; update the host protocol.")
    metadata = {"address": address, "size": size, "schema": 2,
                "mailbox_address": mailbox_address, "mailbox_size": mailbox_size,
                "adc_diagnostics_address": diagnostic_address, "adc_diagnostics_size": diagnostic_size,
                "limit_configuration_address": limit_address, "limit_configuration_size": limit_size,
                "commissioning_address": commissioning_address, "commissioning_size": commissioning_size,
                "elf": elf.name,
                "firmware_sha256": hashlib.sha256((output / "tripletec-bench.bin").read_bytes()).hexdigest()}
    (output / "telemetry-symbol.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("host", "firmware", "all"), default="all", nargs="?")
    parser.add_argument("--cc")
    parser.add_argument("--arm-cc")
    args = parser.parse_args()
    if args.target in ("host", "all"):
        host(compiler("gcc", args.cc, Path.home() / "tools/w64devkit/bin/gcc.exe"))
    if args.target in ("firmware", "all"):
        firmware(compiler("arm-none-eabi-gcc", args.arm_cc))


if __name__ == "__main__":
    main()
