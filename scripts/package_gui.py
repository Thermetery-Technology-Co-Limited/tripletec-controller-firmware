"""Build the Windows GUI executable after building matching STM32 firmware."""
from __future__ import annotations

import json
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def runtime_notices(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    python_root = Path(sys.base_prefix)
    shutil.copyfile(python_root / "LICENSE.txt", destination / "Python-LICENSE.txt")
    distribution = importlib.metadata.distribution("pyinstaller")
    installer_license = next(distribution.locate_file(item) for item in distribution.files
                             if str(item).endswith("licenses/COPYING.txt"))
    shutil.copyfile(installer_license, destination / "PyInstaller-COPYING.txt")
    serial_distribution = importlib.metadata.distribution("pyserial")
    serial_license = next((serial_distribution.locate_file(item) for item in serial_distribution.files
                           if str(item).lower().endswith("license.txt")), None)
    if serial_license is None:
        if serial_distribution.version != "3.5":
            raise SystemExit("pyserial license absent from wheel; provide the matching upstream license before packaging")
        serial_license = ROOT / "third_party/pyserial/LICENSE.txt"
    shutil.copyfile(serial_license, destination / "pyserial-LICENSE.txt")
    for component in ("tcl", "tk"):
        archives = sorted((python_root / "tcl").glob(f"lib{component}*.zip"))
        if archives:
            with zipfile.ZipFile(archives[-1]) as archive:
                content = archive.read(f"{component}_library/license.terms")
        else:
            terms = sorted((python_root / "tcl").glob(f"{component}*/license.terms"))
            if not terms:
                raise SystemExit(f"Installed {component} license.terms missing; include it before packaging.")
            content = terms[-1].read_bytes()
        (destination / f"{component.upper()}-license.terms").write_bytes(content)


def main() -> None:
    metadata = ROOT / "build/stm32/telemetry-symbol.json"
    if not metadata.is_file():
        raise SystemExit("Run python scripts/build.py firmware first.")
    symbol = json.loads(metadata.read_text())
    if symbol.get("schema") != 2 or not symbol.get("mailbox_address"):
        raise SystemExit("Rebuild the GUI-enabled firmware first (telemetry schema 2).")
    output = ROOT / "build/gui"
    work = ROOT / "build/pyinstaller"
    work.mkdir(parents=True, exist_ok=True)
    notices = output / "licenses"
    runtime_notices(notices)
    # The executable bundles Python/Tk and matching symbol metadata. OpenOCD is
    # resolved from the user's PATH or DeviceLink's explicit openocd parameter.
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
               "--windowed", "--name", "TripleTEC-Control", "--paths", str(ROOT),
               "--version-file", str(ROOT / "packaging/windows-version.txt"),
               "--hidden-import", "scripts.device_link", "--hidden-import", "scripts.read_status",
               "--hidden-import", "scripts.usb_link", "--hidden-import", "scripts.tec1_pulse",
               "--hidden-import", "serial.tools.list_ports",
               "--hidden-import", "serial.tools.list_ports_windows",
               "--distpath", str(output), "--workpath", str(work), "--specpath", str(work),
               "--add-data", f"{metadata}:build/stm32", "--add-data", f"{ROOT / 'COPYRIGHT'}:.",
               "--add-data", f"{notices}:licenses",
               "--add-data", f"{ROOT / 'THIRD_PARTY_NOTICES.md'}:.",
               str(ROOT / "desktop_entry.py")]
    subprocess.run(command, cwd=ROOT, check=True)
    print(output / "TripleTEC-Control.exe")


if __name__ == "__main__":
    main()
