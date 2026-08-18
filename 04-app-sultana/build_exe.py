"""Build the Windows distributions of Sultana del Norte.

Usage:
    .\\.venv\\Scripts\\python.exe build_exe.py --mode both

The single-file result is ``output\\Sultana-del-Norte.exe``. The portable
result is ``output\\Sultana-del-Norte\\Sultana-del-Norte.exe``. Both include
Qt, VTK, the C++ simulation module, Kutta and scenario data.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
EXECUTABLE = OUTPUT / "Sultana-del-Norte.exe"
PORTABLE_EXECUTABLE = OUTPUT / "Sultana-del-Norte" / "Sultana-del-Norte.exe"
BUILD = ROOT / "build" / "windows-current" / "Release"


def core_module() -> Path:
    modules = sorted(BUILD.glob("sultana_core*.pyd"))
    if not modules:
        raise RuntimeError(
            "No se encontró sultana_core. Ejecuta primero 'python run_all.py --no-install' para compilar el núcleo."
        )
    return modules[0]


def pyinstaller_command(module: Path, kutta: Path, *, mode: str) -> list[str]:
    if mode not in {"onefile", "onedir"}:
        raise ValueError(f"Modo de distribución desconocido: {mode}")
    mode_flag = "--onefile" if mode == "onefile" else "--onedir"
    pyinstaller_work = ROOT / "build" / "pyinstaller" / mode
    return [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", mode_flag,
        "--name", "Sultana-del-Norte", "--distpath", str(ROOT / "output"),
        "--workpath", str(pyinstaller_work),
        "--specpath", str(pyinstaller_work),
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compilar la aplicación Windows Sultana del Norte")
    parser.add_argument(
        "--mode",
        choices=("onefile", "onedir", "both"),
        default="onefile",
        help="Generar un ejecutable único, una carpeta portable o ambas distribuciones",
    )
    args = parser.parse_args(argv)

    module = core_module()
    kutta = ROOT / "tools" / "kutta" / "kutta.exe"
    if not kutta.is_file():
        raise RuntimeError("No se encontró Kutta. Ejecuta primero 'python build_kutta.py'.")

    modes = ("onefile", "onedir") if args.mode == "both" else (args.mode,)
    artifacts = {
        "onefile": (EXECUTABLE, "Ejecutable único"),
        "onedir": (PORTABLE_EXECUTABLE, "Carpeta portable"),
    }
    for mode in modes:
        subprocess.run(pyinstaller_command(module, kutta, mode=mode), cwd=ROOT, check=True)
        artifact, label = artifacts[mode]
        if not artifact.is_file():
            raise RuntimeError(f"PyInstaller terminó sin generar el resultado esperado: {artifact}")
        print(f"\n[OK] {label}: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
