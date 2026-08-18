"""Install the pinned Go toolchain locally and build the native Kutta app.

Nothing is installed system-wide.  The compiler, module cache and resulting
Windows executable all stay below the CANSAT project directory.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "kutta"
TOOLS = ROOT / "tools"
GO_ROOT = TOOLS / "go"
GO_EXE = GO_ROOT / "bin" / "go.exe"
GO_VERSION = "1.26.5"
GO_ARCHIVE = f"go{GO_VERSION}.windows-amd64.zip"
GO_URL = f"https://go.dev/dl/{GO_ARCHIVE}"
GO_SHA256 = "97e6b2a833b6d89f9ff17d25419ac0a7e3b482a044e9ab18cdef834bd834fd38"
OUTPUT = TOOLS / "kutta" / "kutta.exe"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install_go() -> None:
    if GO_EXE.is_file():
        return
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("La distribución actual de CANSAT requiere Windows x64.")
    if GO_ROOT.exists():
        raise RuntimeError(
            f"La instalación local de Go está incompleta: {GO_ROOT}. "
            "Elimina solamente esa carpeta y vuelve a ejecutar este script."
        )

    downloads = ROOT / "build" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / GO_ARCHIVE
    if not archive.is_file() or _sha256(archive) != GO_SHA256:
        print(f">>> Descargando Go {GO_VERSION} dentro del proyecto...", flush=True)
        urllib.request.urlretrieve(GO_URL, archive)
    actual_hash = _sha256(archive)
    if actual_hash != GO_SHA256:
        raise RuntimeError(
            f"La suma SHA-256 de {archive.name} no coincide: {actual_hash}."
        )

    TOOLS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="go-install-", dir=TOOLS) as temporary:
        temporary_path = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(temporary_path)
        extracted = temporary_path / "go"
        if not (extracted / "bin" / "go.exe").is_file():
            raise RuntimeError("El archivo oficial de Go no contiene bin/go.exe.")
        extracted.replace(GO_ROOT)
    print(f"[OK] Go {GO_VERSION} instalado localmente en {GO_ROOT}")


def build_kutta(*, allow_install: bool) -> None:
    if not SOURCE.is_dir() or not (SOURCE / "go.mod").is_file():
        raise RuntimeError(f"No se encontró el código de Kutta en {SOURCE}.")
    if not GO_EXE.is_file():
        if not allow_install:
            if OUTPUT.is_file():
                print(f"[OK] Se reutiliza el túnel nativo existente: {OUTPUT}")
                return
            raise RuntimeError(
                "Go no está instalado en tools/go y no existe tools/kutta/kutta.exe. "
                "Ejecuta una vez sin --no-install."
            )
        install_go()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "CGO_ENABLED": "0",
            "GOARCH": "amd64",
            "GOOS": "windows",
            "GOROOT": str(GO_ROOT),
            "GOPATH": str(TOOLS / "gopath"),
            "GOMODCACHE": str(TOOLS / "gopath" / "pkg" / "mod"),
            "GOCACHE": str(ROOT / "build" / "go-cache"),
            "GOTOOLCHAIN": "local",
            "GOWORK": "off",
            "PATH": os.pathsep.join((str(GO_ROOT / "bin"), environment.get("PATH", ""))),
        }
    )
    command = [
        str(GO_EXE),
        "build",
        "-trimpath",
        "-ldflags=-s -w -H=windowsgui",
        "-o",
        str(OUTPUT),
        ".",
    ]
    print(f">>> Compilando la aplicación nativa Kutta: {OUTPUT}", flush=True)
    subprocess.run(command, cwd=SOURCE, env=environment, check=True)
    subprocess.run([str(GO_EXE), "version", "-m", str(OUTPUT)], env=environment, check=True)
    print(f"[OK] Túnel de viento nativo compilado: {OUTPUT}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compilar Kutta dentro del proyecto CANSAT")
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="No descargar Go; reutilizar la instalación o el binario local existente",
    )
    args = parser.parse_args()
    try:
        build_kutta(allow_install=not args.no_install)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[ERROR] No se pudo preparar Kutta: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
