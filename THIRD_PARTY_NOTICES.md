# Desktop runtime notices

TripleTEC's original firmware, desktop application, tests, and documentation are covered by `COPYRIGHT`.

The Windows desktop executable also contains Python, Tcl/Tk, and PyInstaller runtime components. Their original licenses and notices are copied from the installed build dependencies into the packaged `licenses/` resources and the adjacent `build/gui/licenses/` directory. They retain their respective ownership and terms.

The packaging script includes Python's complete `LICENSE.txt`, the installed Tcl and Tk `license.terms`, and PyInstaller's complete `COPYING.txt` (including its bootloader exception and runtime-hook terms).

OpenOCD is a separately installed dependency. Its executable and libraries are not bundled in the desktop package.
