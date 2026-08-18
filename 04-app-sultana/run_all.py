"""Instala dependencias, compila el núcleo, ejecuta todas las pruebas y opcionalmente abre la UI.

Uso:
    python run_all.py            # prepara y verifica todo
    python run_all.py --launch   # además abre la aplicación al terminar
    python run_all.py --no-install  # reutiliza el entorno existente
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
# A versioned output folder avoids replacing a .pyd that a currently open UI may lock on Windows.
BUILD = ROOT / "build" / "windows-current"


def run(command: list[str], *, environment: dict[str, str] | None = None, shell: bool = False) -> None:
    printable = command if not shell else command[-1]
    print(f"\n>>> {printable}", flush=True)
    subprocess.run(command if not shell else printable, cwd=ROOT, env=environment, check=True, shell=shell)


def venv_python() -> Path:
    return VENV / "Scripts" / "python.exe" if os.name == "nt" else VENV / "bin" / "python"


def ensure_environment(skip_install: bool) -> Path:
    python = venv_python()
    if not python.exists():
        run([sys.executable, "-m", "venv", str(VENV)])
    if not skip_install:
        run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
        run([str(python), "-m", "pip", "install", "cmake>=3.24", "ninja>=1.11"])
        run([str(python), "-m", "pip", "install", "-e", ".[3d,export,dev]"])
    return python


def build_and_test(python: Path) -> dict[str, str]:
    cmake = VENV / "Scripts" / "cmake.exe" if os.name == "nt" else VENV / "bin" / "cmake"
    ctest = VENV / "Scripts" / "ctest.exe" if os.name == "nt" else VENV / "bin" / "ctest"
    if not cmake.exists():
        raise RuntimeError("CMake no se instaló correctamente en .venv.")
    if os.name == "nt":
        configure = [str(cmake), "-S", str(ROOT), "-B", str(BUILD), "-G", "Visual Studio 17 2022", "-A", "x64", f"-DPython_EXECUTABLE={python}"]
        build = [str(cmake), "--build", str(BUILD), "--config", "Release", "--target", "sultana_core", "sultana_physics_tests", "--parallel", "2"]
        ctest_command = [str(ctest), "--test-dir", str(BUILD), "-C", "Release", "--output-on-failure"]
        module_directory = BUILD / "Release"
    else:
        configure = [str(cmake), "-S", str(ROOT), "-B", str(BUILD), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", f"-DPython_EXECUTABLE={python}"]
        build = [str(cmake), "--build", str(BUILD), "--target", "sultana_core", "sultana_physics_tests", "--parallel", "2"]
        ctest_command = [str(ctest), "--test-dir", str(BUILD), "--output-on-failure"]
        module_directory = BUILD
    run(configure)
    run(build)
    run(ctest_command)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "python"), str(module_directory), environment.get("PYTHONPATH", "")))
    run([str(python), "-m", "pytest", "python/tests", "-q"], environment=environment)
    return environment


def build_kutta(python: Path, skip_install: bool) -> None:
    command = [str(python), str(ROOT / "build_kutta.py")]
    if skip_install:
        command.append("--no-install")
    run(command)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecutor integral del simulador Sultana")
    parser.add_argument("--launch", action="store_true", help="Abrir la UI al superar las pruebas")
    parser.add_argument("--no-install", action="store_true", help="No descargar ni actualizar dependencias")
    args = parser.parse_args()
    try:
        python = ensure_environment(args.no_install)
        build_kutta(python, args.no_install)
        environment = build_and_test(python)
        print("\n[OK] Dependencias instaladas, Kutta y núcleo compilados, y todas las pruebas pasaron.")
        if args.launch:
            print("[CFD] La interfaz abre sin esperar Docker. Inicia Docker Desktop solo antes de ejecutar un caso CFD.")
            run([str(python), "-m", "app.main"], environment=environment)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"\n[ERROR] Fallo la preparacion: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
