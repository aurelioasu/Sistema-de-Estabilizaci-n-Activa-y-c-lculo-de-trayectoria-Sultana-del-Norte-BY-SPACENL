from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QToolTip, QWidget


class HelpButton(QToolButton):
    """Small contextual-help button that waits before showing its explanation."""

    def __init__(self, text: str, parent: QWidget | None = None, *, delay_ms: int = 3000) -> None:
        super().__init__(parent)
        self._help_text = text
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._show_help)
        self.setText("?")
        self.setAccessibleName("Ayuda contextual")
        self.setCursor(Qt.WhatsThisCursor)
        self.setFixedSize(18, 18)
        self.setAutoRaise(True)
        self.setStyleSheet(
            "QToolButton { border: 1px solid #8a7565; border-radius: 9px; color: #f0a35e; "
            "font-weight: 700; background: #211d1a; padding: 0; }"
            "QToolButton:hover { background: #5a321f; color: white; }"
        )

    def enterEvent(self, event: QEvent) -> None:
        self._timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._timer.stop()
        QToolTip.hideText()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QEvent) -> None:
        self._timer.stop()
        self._show_help()
        super().mousePressEvent(event)

    def _show_help(self) -> None:
        if self.underMouse():
            QToolTip.showText(QCursor.pos() + QPoint(12, 12), self._help_text, self)


class _HelpAnchor(QObject):
    def __init__(self, target: QWidget, button: HelpButton) -> None:
        super().__init__(target)
        self.target = target
        self.button = button
        target.installEventFilter(self)
        self._position()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.target and event.type() in (QEvent.Resize, QEvent.Show):
            self._position()
        return False

    def _position(self) -> None:
        self.button.move(max(2, self.target.width() - self.button.width() - 6), 6)
        self.button.raise_()


def help_label(label: str, explanation: str) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    layout.addWidget(QLabel(label))
    layout.addWidget(HelpButton(explanation))
    layout.addStretch(1)
    return container


def attach_help(target: QWidget, explanation: str) -> HelpButton:
    """Overlay a help badge on graphs/tables without changing their layout."""
    button = HelpButton(explanation, target)
    anchor = _HelpAnchor(target, button)
    target._context_help_button = button  # type: ignore[attr-defined]
    target._context_help_anchor = anchor  # type: ignore[attr-defined]
    button.show()
    return button
