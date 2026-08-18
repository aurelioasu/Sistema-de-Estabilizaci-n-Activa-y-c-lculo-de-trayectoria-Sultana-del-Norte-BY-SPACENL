"""Lightweight GUI bootstrap that displays the splash before heavy imports."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from app.runtime import application_root


SPLASH_MINIMUM_DURATION_MS = 3000
SPLASH_FADE_DURATION_MS = 450
WINDOW_FADE_DURATION_MS = 300
SPLASH_IMAGE = Path("data/assets/space_nl_splash.png")
APP_ICON = Path("data/assets/space_nl.ico")


def splash_image_path() -> Path:
    """Return the splash asset in either the source tree or frozen bundle."""
    return application_root() / SPLASH_IMAGE


def app_icon_path() -> Path:
    """Return the multi-resolution Windows icon bundled with the app."""
    return application_root() / APP_ICON


def main() -> int:
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(app_icon_path())))
    base_font = app.font()
    if base_font.pointSize() <= 0:
        base_font.setPointSize(9)
        app.setFont(base_font)

    pixmap = QPixmap(str(splash_image_path()))
    if pixmap.isNull():
        raise RuntimeError(f"No se pudo cargar la imagen de inicio: {splash_image_path()}")

    splash = QSplashScreen(
        pixmap,
        Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
    )
    splash.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    splash.show()
    app.processEvents()

    # Bridge PyInstaller's early extraction splash to the Qt splash without a
    # flash of the desktop between the two windows.
    try:
        import pyi_splash
    except ImportError:
        pass
    else:
        pyi_splash.close()

    state: dict[str, object] = {"splash": splash}

    def launch_application() -> None:
        # Import the application only after the splash is already visible so
        # optional 3D/CFD dependencies cannot delay the first visual feedback.
        try:
            from app.main import MainWindow, SPACE_NL_STYLE

            app.setStyleSheet(SPACE_NL_STYLE)
            window = MainWindow()
            state["window"] = window
            window.setWindowOpacity(0.0)
            window.show()

            splash_fade = QPropertyAnimation(splash, b"windowOpacity", app)
            splash_fade.setDuration(SPLASH_FADE_DURATION_MS)
            splash_fade.setStartValue(1.0)
            splash_fade.setEndValue(0.0)
            splash_fade.setEasingCurve(QEasingCurve.Type.InOutCubic)
            state["splash_fade"] = splash_fade

            def reveal_window() -> None:
                splash.close()
                window.raise_()
                window.activateWindow()
                window_fade = QPropertyAnimation(window, b"windowOpacity", app)
                window_fade.setDuration(WINDOW_FADE_DURATION_MS)
                window_fade.setStartValue(0.0)
                window_fade.setEndValue(1.0)
                window_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
                state["window_fade"] = window_fade
                window_fade.start()

            splash_fade.finished.connect(reveal_window)
            splash_fade.start()
        except Exception as exc:
            splash.close()
            QMessageBox.critical(None, "Error de inicio", f"No se pudo iniciar Sultana del Norte:\n\n{exc}")
            app.quit()

    QTimer.singleShot(SPLASH_MINIMUM_DURATION_MS, launch_application)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
