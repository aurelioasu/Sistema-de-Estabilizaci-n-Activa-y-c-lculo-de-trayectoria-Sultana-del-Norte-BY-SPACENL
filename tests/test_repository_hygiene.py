from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPOSITORY_ROOT / "tools" / "repo_audit.py"
REQUIRED_DIRECTORIES = (
    "01-diseno-cad",
    "02-electronica",
    "03-firmware-esp32",
    "04-app-sultana",
    "05-documentacion",
)


def run_audit(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), "--root", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_audit_accepts_minimal_valid_repository(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sultana del Norte\n", encoding="utf-8")
    for directory in REQUIRED_DIRECTORIES:
        (tmp_path / directory).mkdir()

    result = run_audit(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr


def test_audit_rejects_a_missing_required_area(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sultana del Norte\n", encoding="utf-8")
    for directory in REQUIRED_DIRECTORIES[:-1]:
        (tmp_path / directory).mkdir()

    result = run_audit(tmp_path)

    assert result.returncode == 1
    assert "05-documentacion" in result.stdout


def test_audit_rejects_repository_without_readme(tmp_path: Path) -> None:
    for directory in REQUIRED_DIRECTORIES:
        (tmp_path / directory).mkdir()

    result = run_audit(tmp_path)

    assert result.returncode == 1
    assert "README.md" in result.stdout


def test_audit_rejects_file_over_github_limit(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sultana del Norte\n", encoding="utf-8")
    for directory in REQUIRED_DIRECTORIES:
        (tmp_path / directory).mkdir()
    oversized = tmp_path / "video-grande.mp4"
    with oversized.open("wb") as handle:
        handle.seek(100 * 1024 * 1024)
        handle.write(b"x")

    result = run_audit(tmp_path)

    assert result.returncode == 1
    assert "video-grande.mp4" in result.stdout
    assert "100 MiB" in result.stdout


def test_audit_rejects_legacy_project_identity(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sultana del Norte\n", encoding="utf-8")
    for directory in REQUIRED_DIRECTORIES:
        (tmp_path / directory).mkdir()
    legacy_file = tmp_path / "03-firmware-esp32" / "Manual_CanSat.md"
    legacy_file.write_text("Firmware del CANSAT\n", encoding="utf-8")

    result = run_audit(tmp_path)

    assert result.returncode == 1
    assert "Manual_CanSat.md" in result.stdout
    assert "identidad heredada" in result.stdout


def test_audit_rejects_legacy_identity_inside_public_text(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sultana del Norte\n", encoding="utf-8")
    for directory in REQUIRED_DIRECTORIES:
        (tmp_path / directory).mkdir()
    public_file = tmp_path / "03-firmware-esp32" / "guia.md"
    public_file.write_text("Manual público del CanSat\n", encoding="utf-8")

    result = run_audit(tmp_path)

    assert result.returncode == 1
    assert "guia.md" in result.stdout
    assert "contenido público" in result.stdout


def test_audit_rejects_github_token_pattern(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sultana del Norte\n", encoding="utf-8")
    for directory in REQUIRED_DIRECTORIES:
        (tmp_path / directory).mkdir()
    config = tmp_path / "04-app-sultana" / "configuracion.txt"
    config.write_text("TOKEN=ghp_" + "A" * 36 + "\n", encoding="utf-8")

    result = run_audit(tmp_path)

    assert result.returncode == 1
    assert "configuracion.txt" in result.stdout
    assert "posible secreto" in result.stdout


def test_audit_ignores_local_build_artifacts(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sultana del Norte\n", encoding="utf-8")
    for directory in REQUIRED_DIRECTORIES:
        (tmp_path / directory).mkdir()
    generated = tmp_path / "04-app-sultana" / "build" / "CMakeCache.txt"
    generated.parent.mkdir()
    generated.write_text("Ruta local del CanSat\n", encoding="utf-8")

    result = run_audit(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr


def test_audit_ignores_local_distribution_artifacts(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sultana del Norte\n", encoding="utf-8")
    for directory in REQUIRED_DIRECTORIES:
        (tmp_path / directory).mkdir()
    generated = tmp_path / "04-app-sultana" / "output" / "identidad-anterior.txt"
    generated.parent.mkdir()
    generated.write_text("Artefacto local CANSAT\n", encoding="utf-8")

    result = run_audit(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
