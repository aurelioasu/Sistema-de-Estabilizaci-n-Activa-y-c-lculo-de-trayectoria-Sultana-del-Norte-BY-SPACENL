from pathlib import Path

from app import runtime


def test_application_root_uses_pyinstaller_bundle_directory(monkeypatch, tmp_path: Path) -> None:
    bundle = tmp_path / "_MEI12345"
    monkeypatch.setattr(runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime.sys, "_MEIPASS", str(bundle), raising=False)

    assert runtime.application_root() == bundle.resolve()
