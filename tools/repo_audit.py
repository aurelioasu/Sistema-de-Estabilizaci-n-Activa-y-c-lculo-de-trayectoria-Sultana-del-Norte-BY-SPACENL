from __future__ import annotations

import argparse
import re
from pathlib import Path


REQUIRED_DIRECTORIES = (
    "01-diseno-cad",
    "02-electronica",
    "03-firmware-esp32",
    "04-app-sultana",
    "05-documentacion",
)
MAX_GIT_FILE_BYTES = 100 * 1024 * 1024
TEXT_EXTENSIONS = {
    ".c",
    ".cmake",
    ".cpp",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ino",
    ".json",
    ".md",
    ".py",
    ".svg",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
IDENTITY_SCAN_EXEMPT_PREFIXES = ("docs/superpowers/", "tests/", "tools/")
IGNORED_DIRECTORY_NAMES = {".git", "build"}
SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def audit_repository(root: Path) -> list[str]:
    errors: list[str] = []
    if not (root / "README.md").is_file():
        errors.append("Falta README.md")
    for directory in REQUIRED_DIRECTORIES:
        if not (root / directory).is_dir():
            errors.append(f"Falta el directorio requerido: {directory}")
    for path in root.rglob("*"):
        relative_path = path.relative_to(root)
        if path.is_file() and not IGNORED_DIRECTORY_NAMES.intersection(relative_path.parts[:-1]):
            relative = relative_path.as_posix()
            if path.stat().st_size > MAX_GIT_FILE_BYTES:
                errors.append(f"{relative} supera el límite de 100 MiB de GitHub")
            if "cansat" in relative.casefold():
                errors.append(f"{relative} conserva identidad heredada CANSAT")
            is_exempt = relative.startswith(IDENTITY_SCAN_EXEMPT_PREFIXES)
            if path.suffix.casefold() in TEXT_EXTENSIONS:
                content = path.read_text(encoding="utf-8", errors="ignore")
                if not is_exempt and "cansat" in content.casefold():
                    errors.append(f"{relative} conserva identidad heredada en contenido público")
                if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                    errors.append(f"{relative} contiene un posible secreto")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita el repositorio Sultana del Norte")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    errors = audit_repository(args.root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("OK: estructura mínima válida")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
