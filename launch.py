"""
Launcher — opens billing_gui.pyw without a visible console window.
Called by run.bat.
"""
import os
import sys
import subprocess
from pathlib import Path

here = Path(__file__).parent
gui  = here / "billing_gui.pyw"

# Prefer pythonw.exe (no console window) next to the current python.exe
pythonw = Path(sys.executable).parent / "pythonw.exe"
exe = str(pythonw) if pythonw.exists() else sys.executable

subprocess.Popen([exe, str(gui)], cwd=str(here))
