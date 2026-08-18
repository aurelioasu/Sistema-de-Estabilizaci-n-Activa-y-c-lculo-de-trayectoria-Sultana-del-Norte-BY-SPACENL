"""Build the single-file Windows executable of Sultana Flight Simulator.

Usage:
    .\\.venv\\Scripts\\python.exe build_exe.py

The result is ``output\\SultanaSimulator.exe``. Qt, VTK, the C++ simulation
module and scenario data are embedded and extracted automatically at launch.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXECUTABLE = ROOT / "output" / "SultanaSimulator.exe"
BUILD = ROOT / "build" / "windows-current" / "Release"


def core_module() -> Path:
    modules = sorted(BUILD.glob("sultana_core*.pyd"))
    if not modules:
        raise RuntimeError(
            "No se encontró sultana_core. Ejecuta primero 'python run_all.py --no-install' para compilar el núcleo."
        )
    return modules[0]


def main() -> int:
    module = core_module()
    kutta = ROOT / "tools" / "kutta" / "kutta.exe"
    if not kutta.is_file():
        raise RuntimeError("No se encontró Kutta. Ejecuta primero 'python build_kutta.py'.")
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onefile",
        "--name", "SultanaSimulator", "--distpath", str(ROOT / "output"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build" / "pyinstaller"),
        "--paths", str(ROOT / "python"), "--paths", str(module.parent),
        "--add-data", f"{ROOT / 'configs'}{';'}configs",
        "--add-data", f"{ROOT / 'data'}{';'}data",
        "--add-binary", f"{module}{';'}.",
        "--add-binary", f"{kutta}{';'}tools/kutta",
        "--add-data", f"{ROOT / 'kutta' / 'LICENSE'}{';'}tools/kutta",
        "--splash", str(ROOT / "data" / "assets" / "space_nl_boot_splash.png"),
        "--splash-center", "primary",
        "--icon", str(ROOT / "data" / "assets" / "space_nl.ico"),
        "--collect-all", "pyvista", "--collect-all", "pyvistaqt", "--collect-all", "vtkmodules",
        "--collect-all", "pyarrow", "--collect-all", "rocketcea",
        str(ROOT / "python" / "app" / "bootstrap.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    if not EXECUTABLE.is_file():
        raise RuntimeError("PyInstaller terminó sin generar el ejecutable esperado.")
    print(f"\n[OK] Ejecutable Ãºnico: {EXECUTABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
