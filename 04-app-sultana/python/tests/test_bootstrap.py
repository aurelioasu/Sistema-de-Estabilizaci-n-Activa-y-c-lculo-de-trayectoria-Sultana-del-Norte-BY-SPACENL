from pathlib import Path

from PIL import Image

from app import bootstrap


def test_splash_asset_is_resolved_inside_application_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bootstrap, "application_root", lambda: tmp_path)

    assert bootstrap.splash_image_path() == tmp_path / "data/assets/space_nl_splash.png"
    assert bootstrap.app_icon_path() == tmp_path / "data/assets/space_nl.ico"
    assert bootstrap.SPLASH_MINIMUM_DURATION_MS == 3000
    assert bootstrap.SPLASH_FADE_DURATION_MS == 450
    assert bootstrap.WINDOW_FADE_DURATION_MS == 300


def test_boot_splash_has_binary_alpha_and_icon_has_windows_sizes() -> None:
    assets = Path(__file__).resolve().parents[2] / "data/assets"
    with Image.open(assets / "space_nl_boot_splash.png").convert("RGBA") as splash:
        populated_alpha_values = {
            value for value, count in enumerate(splash.getchannel("A").histogram()) if count
        }
    with Image.open(assets / "space_nl.ico") as icon:
        icon_sizes = icon.ico.sizes()

    assert populated_alpha_values == {0, 255}
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= icon_sizes
