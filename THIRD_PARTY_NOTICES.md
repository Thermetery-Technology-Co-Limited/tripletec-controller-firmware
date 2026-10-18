# Third-party notices

TripleTEC's original firmware, desktop application, tests, and documentation are covered by `COPYRIGHT`.

The Windows desktop executable also contains Python, Tcl/Tk, PyInstaller and pyserial runtime components. Their original licenses and notices are copied from the installed build dependencies into the packaged `licenses/` resources and the adjacent `build/gui/licenses/` directory. They retain their respective ownership and terms.

The packaging script includes Python's complete `LICENSE.txt`, the installed Tcl and Tk `license.terms`, PyInstaller's complete `COPYING.txt` (including its bootloader exception and runtime-hook terms), and pyserial's complete `LICENSE.txt` (BSD terms and included notices).

The firmware links pinned TinyUSB device/CDC sources and uses Arm CMSIS-Core and ST STM32F1 register headers. See [third_party/README.md](third_party/README.md) for exact versions, licenses, original file terms and the SHA-256 manifest. TinyUSB is MIT licensed, with retained additional terms in its STM32 FSDEV header; the pinned CMSIS components supply Apache-2.0 licenses. Their third-party licenses govern those files independently of the proprietary application copyright.

OpenOCD is a separately installed dependency. Its executable and libraries are not bundled in the desktop package.
