from __future__ import annotations

import runpy
import re
import tomllib
from pathlib import Path

from tools.repo_audit import audit_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_application_has_only_sultana_project_identity() -> None:
    application_identity_errors = [
        error
        for error in audit_repository(REPOSITORY_ROOT)
        if error.startswith("04-app-sultana/") and "identidad heredada" in error
    ]

    assert application_identity_errors == []


def test_windows_build_uses_public_product_name() -> None:
    build_script = REPOSITORY_ROOT / "04-app-sultana" / "build_exe.py"

    namespace = runpy.run_path(str(build_script), run_name="sultana_build_contract")

    assert namespace["EXECUTABLE"].name == "Sultana-del-Norte.exe"


def test_application_metadata_matches_release_version() -> None:
    application_root = REPOSITORY_ROOT / "04-app-sultana"
    metadata = tomllib.loads((application_root / "pyproject.toml").read_text(encoding="utf-8"))
    cmake = (application_root / "CMakeLists.txt").read_text(encoding="utf-8")
    cmake_version = re.search(r"project\(sultana_flight_simulator VERSION ([0-9.]+)", cmake)

    assert metadata["project"]["version"] == "1.0.0"
    assert cmake_version is not None
    assert cmake_version.group(1) == "1.0.0"
