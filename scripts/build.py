"""Reproducible bench-firmware and native C test builds; Python standard library only."""
from __future__ import annotations

import argparse
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
        "sensors": sorted(str(p.relative_to(ROOT)) for p in (ROOT / "sensors").glob("*.c")),
    }
    for name, sources in groups.items():
        binary = output / (f"test_{name}.exe" if os.name == "nt" else f"test_{name}")
        run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-Icore", "-Isensors", *sources, f"tests/test_{name}.c", "-lm", "-o", binary])
        run([binary])
    run([sys.executable, "tests/test_status.py"])


def firmware(cc: str) -> None:
    output = ROOT / "build" / "stm32"
    output.mkdir(parents=True, exist_ok=True)
    sources = sorted(p for folder in ("core", "sensors", "app", "platform/stm32f103")
                     for p in (ROOT / folder).glob("*.c"))
    flags = ["-mcpu=cortex-m3", "-mthumb", "-std=c11", "-Os", "-g3",
             "-ffunction-sections", "-fdata-sections", "-fno-common", "-ffreestanding",
             "-Wall", "-Wextra", "-Werror", "-DBENCH_OUTPUT_LOCK=1",
             "-Icore", "-Isensors", "-Iapp", "-Iplatform/stm32f103"]
    objects = []
    for source in sources:
        obj = output / ("_".join(source.relative_to(ROOT).parts) + ".o")
        run([cc, *flags, "-c", source, "-o", obj])
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
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[3] == "g_telemetry":
            metadata = {"address": int(fields[0], 16), "size": int(fields[1], 16),
                        "schema": 1, "elf": str(elf)}
            (output / "telemetry-symbol.json").write_text(json.dumps(metadata, indent=2) + "\n")
            break
    else:
        raise SystemExit("g_telemetry missing from linked image")


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
