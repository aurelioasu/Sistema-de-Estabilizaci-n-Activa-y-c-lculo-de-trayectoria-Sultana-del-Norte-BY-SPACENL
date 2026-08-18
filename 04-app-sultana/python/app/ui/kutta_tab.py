"""Native Windows host for the Kutta 2D wind-tunnel executable."""
from __future__ import annotations

import ctypes
import sys
import time
from collections import deque
from ctypes import wintypes
from pathlib import Path
from statistics import median

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.runtime import application_root


KUTTA_MEMORY_GROWTH_BYTES = 512 * 1024 * 1024
KUTTA_MEMORY_WARMUP_SECONDS = 45.0
KUTTA_MEMORY_GROWTH_WINDOW_SECONDS = 30.0
KUTTA_MEMORY_GROWTH_RATE_BYTES_PER_SECOND = 4 * 1024 * 1024
KUTTA_SYSTEM_AVAILABLE_FLOOR_BYTES = 2 * 1024 * 1024 * 1024
KUTTA_RECOVERY_COOLDOWN_SECONDS = 10 * 60.0
KUTTA_MAINTENANCE_SETTLE_SECONDS = 15.0
KUTTA_OUTPUT_LIMIT_CHARS = 64 * 1024
KUTTA_AUTO_RESTART_DELAY_MS = 750
KUTTA_GO_MEMORY_LIMIT = "384MiB"
KUTTA_GO_GC_PERCENT = "75"


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def process_private_bytes(pid: int) -> int | None:
    """Return committed private bytes for a Windows process, if available."""
    if sys.platform != "win32" or pid <= 0:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCountersEx),
        wintypes.DWORD,
    )
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    # Read-only access to memory counters for our child process.
    handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return None
    try:
        counters = _ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PrivateUsage)
    finally:
        kernel32.CloseHandle(handle)


def system_available_memory_bytes() -> int | None:
    """Return the smaller physical/commit reserve reported by Windows."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalMemoryStatusEx.argtypes = (ctypes.POINTER(_MemoryStatusEx),)
    kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(min(status.ullAvailPhys, status.ullAvailPageFile))


def sustained_memory_growth(
    samples: list[tuple[float, int]],
    *,
    baseline_bytes: int,
    available_system_bytes: int | None,
) -> bool:
    """Identify a real leak trend, excluding normal GPU warm-up reservations."""
    if len(samples) < 8 or baseline_bytes <= 0:
        return False
    first_t, first_bytes = samples[0]
    last_t, last_bytes = samples[-1]
    elapsed = last_t - first_t
    if elapsed < KUTTA_MEMORY_GROWTH_WINDOW_SECONDS:
        return False
    if last_bytes-baseline_bytes < KUTTA_MEMORY_GROWTH_BYTES:
        return False
    if (last_bytes-first_bytes) / max(elapsed, 1.0) < KUTTA_MEMORY_GROWTH_RATE_BYTES_PER_SECOND:
        return False
    increases = sum(b > a + 1024 * 1024 for (_, a), (_, b) in zip(samples, samples[1:]))
    if increases < max(3, (len(samples) - 1) * 2 // 3):
        return False
    system_low = (
        available_system_bytes is not None
        and available_system_bytes < KUTTA_SYSTEM_AVAILABLE_FLOOR_BYTES
    )
    # A large private reservation is normal for some Direct3D drivers and is
    # not, by itself, evidence that Windows is in danger.  Recovery is allowed
    # only when the sustained process trend coincides with real system memory
    # pressure.  This is the key distinction that prevents the old 1 GiB loop.
    return system_low


def kutta_executable(root: Path | None = None) -> Path:
    """Return the bundled native executable in source and frozen layouts."""
    base = Path(root) if root is not None else application_root()
    return base / "tools" / "kutta" / "kutta.exe"


def kutta_session_file() -> Path:
    """Return the per-user checkpoint used across renderer recoveries."""
    return Path.home() / ".sultana" / "kutta-session.json"


def clear_kutta_session(session_file: Path | None = None) -> bool:
    """Delete only the known checkpoint for an explicit manual reset."""
    checkpoint = session_file if session_file is not None else kutta_session_file()
    try:
        checkpoint.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def kutta_arguments(
    parent_hwnd: int | None = None,
    *,
    session_file: Path | None = None,
    degraded: bool = False,
) -> list[str]:
    """Arguments for the embedded desktop build (there is no web runtime)."""
    arguments = ["-tps", "30"]
    if parent_hwnd:
        arguments.extend(("-parent-hwnd", str(parent_hwnd)))
    checkpoint = session_file if session_file is not None else kutta_session_file()
    arguments.extend(("-session-state", str(checkpoint)))
    if degraded:
        arguments.append("-degraded")
    return arguments


def kutta_process_environment() -> QProcessEnvironment:
    """Return a bounded Go runtime environment while preserving system paths."""
    environment = QProcessEnvironment.systemEnvironment()
    environment.insert("GOMEMLIMIT", KUTTA_GO_MEMORY_LIMIT)
    environment.insert("GOGC", KUTTA_GO_GC_PERCENT)
    return environment


class _NativeHost(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(720, 460)
        self.setStyleSheet("background: #050505;")

class KuttaTab(QWidget):
    """Launch Kutta as a child process and embed its HWND into this tab."""

    _POLL_INTERVAL_MS = 100
    _MAX_POLLS = 120
    _MEMORY_POLL_INTERVAL_MS = 2000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hwnd = 0
        self._polls = 0
        self._stopping = False
        self._stop_reason = ""
        self._process_output = ""
        self._auto_restart_pending = False
        self._memory_recovery_count = 0
        self._last_memory_mib = 0.0
        self._memory_started_at = 0.0
        self._memory_baseline_bytes = 0
        self._memory_samples: deque[tuple[float, int]] = deque(maxlen=24)
        self._maintenance_pending_until = 0.0
        self._maintenance_attempted = False
        self._degraded_mode = False
        self._last_recovery_at = 0.0

        title = QLabel("Túnel de viento 2D · ejecución nativa local")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffae65;")
        self.restart_button = QPushButton("Reiniciar túnel")
        self.restart_button.clicked.connect(self.restart)
        toolbar = QHBoxLayout()
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        toolbar.addWidget(self.restart_button)

        self.host = _NativeHost(self)
        self.status = QLabel("El túnel se iniciará al abrir esta pestaña.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #cfc4ba; padding: 4px 2px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(toolbar)
        layout.addWidget(self.host, 1)
        layout.addWidget(self.status)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.started.connect(self._process_started)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.readyReadStandardOutput.connect(self._read_process_output)

        self._window_timer = QTimer(self)
        self._window_timer.setInterval(self._POLL_INTERVAL_MS)
        self._window_timer.timeout.connect(self._embedding_timeout_tick)

        self._memory_timer = QTimer(self)
        self._memory_timer.setInterval(self._MEMORY_POLL_INTERVAL_MS)
        self._memory_timer.timeout.connect(self._check_memory)

        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.setInterval(KUTTA_AUTO_RESTART_DELAY_MS)
        self._restart_timer.timeout.connect(self.start)

    def start(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        if sys.platform != "win32":
            self.status.setText("La pestaña integrada de Kutta requiere Windows.")
            self.restart_button.setEnabled(False)
            return
        executable = kutta_executable()
        if not executable.is_file():
            self.status.setText(
                "No se encontró tools/kutta/kutta.exe. Ejecuta 'python build_kutta.py' "
                "desde la carpeta CANSAT."
            )
            return

        self._stopping = False
        self._stop_reason = ""
        self._auto_restart_pending = False
        self._hwnd = 0
        self._polls = 0
        self._process_output = ""
        self._memory_started_at = time.monotonic()
        self._memory_baseline_bytes = 0
        self._memory_samples.clear()
        self._maintenance_pending_until = 0.0
        self._maintenance_attempted = False
        self.status.setText("Iniciando el túnel de viento nativo…")
        self.restart_button.setEnabled(False)
        self.process.setWorkingDirectory(str(application_root()))
        self.process.setProgram(str(executable))
        self.process.setArguments(
            kutta_arguments(int(self.host.winId()), degraded=self._degraded_mode)
        )
        self.process.setProcessEnvironment(kutta_process_environment())
        self.process.start()

    def restart(self) -> None:
        """Manually restart from the original NACA tunnel defaults.

        Automatic memory recovery calls ``start`` directly and deliberately
        leaves this checkpoint intact, so it restores the current CANSAT/NACA
        work.  The user-facing button is the one explicit reset action.
        """
        self._memory_recovery_count = 0
        self._degraded_mode = False
        self._last_recovery_at = 0.0
        self.stop()
        if not clear_kutta_session():
            self.status.setText(
                "No se pudo borrar el estado anterior; el túnel conservará la sesión actual."
            )
            self.restart_button.setEnabled(True)
            return
        self._restart_timer.start(0)

    def stop(self, *, preserve_auto_restart: bool = False) -> None:
        self._window_timer.stop()
        self._memory_timer.stop()
        if not preserve_auto_restart:
            self._auto_restart_pending = False
            self._restart_timer.stop()
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._hwnd = 0
            return
        self._stopping = True
        if self._hwnd:
            _post_close(self._hwnd)
        if not self.process.waitForFinished(1500):
            self.process.terminate()
        if not self.process.waitForFinished(1000):
            self.process.kill()
            self.process.waitForFinished(1000)
        self._hwnd = 0

    def _process_started(self) -> None:
        self._window_timer.start()
        self._memory_timer.start()

    def _process_finished(self, exit_code: int, _exit_status: object) -> None:
        self._window_timer.stop()
        self._memory_timer.stop()
        self._hwnd = 0
        if self._auto_restart_pending:
            self._auto_restart_pending = False
            self._memory_recovery_count += 1
            self.restart_button.setEnabled(False)
            self.status.setText(
                "Kutta liberó su memoria de forma controlada "
                f"({self._last_memory_mib:.0f} MiB). Reiniciando automáticamente… "
                f"Recuperaciones realizadas: {self._memory_recovery_count}."
            )
            self._restart_timer.start()
            return
        self.restart_button.setEnabled(True)
        if self._stopping:
            self.status.setText(self._stop_reason or "Túnel de viento detenido.")
        else:
            self._read_process_output()
            output = self._process_output.strip()
            detail = f" · {output[-300:]}" if output else ""
            self.status.setText(f"Kutta terminó con código {exit_code}{detail}")

    def _process_error(self, _error: object) -> None:
        self._window_timer.stop()
        self._memory_timer.stop()
        self.restart_button.setEnabled(True)
        self.status.setText(f"No se pudo iniciar Kutta: {self.process.errorString()}")

    def _check_memory(self) -> None:
        """Act only on sustained growth after the renderer warm-up."""
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._memory_timer.stop()
            return
        if self._auto_restart_pending:
            return
        private_bytes = process_private_bytes(int(self.process.processId()))
        if private_bytes is None:
            return
        now = time.monotonic()
        self._memory_samples.append((now, private_bytes))
        while self._memory_samples and now-self._memory_samples[0][0] > 36.0:
            self._memory_samples.popleft()
        if now-self._memory_started_at < KUTTA_MEMORY_WARMUP_SECONDS:
            self._memory_baseline_bytes = int(median(value for _, value in self._memory_samples))
            available = system_available_memory_bytes()
            if (
                available is not None
                and available < KUTTA_SYSTEM_AVAILABLE_FLOOR_BYTES
                and not self._maintenance_attempted
            ):
                # Protect a genuinely constrained machine during warm-up, but
                # never kill/restart the renderer while its normal GPU reserve
                # is still being established.
                self._maintenance_attempted = True
                self._maintenance_pending_until = now + KUTTA_MAINTENANCE_SETTLE_SECONDS
                self.process.write(b"MAINTENANCE\n")
                self.status.setText(
                    "Windows reportó poca memoria disponible durante el calentamiento. "
                    "Kutta redujo la carga gráfica sin reiniciar ni cambiar la escena."
                )
            return
        if now < self._maintenance_pending_until:
            return
        if self._memory_baseline_bytes <= 0:
            self._memory_baseline_bytes = int(median(value for _, value in self._memory_samples))
        available = system_available_memory_bytes()
        if not sustained_memory_growth(
            list(self._memory_samples),
            baseline_bytes=self._memory_baseline_bytes,
            available_system_bytes=available,
        ):
            return

        used_mib = private_bytes / (1024 * 1024)
        self._last_memory_mib = used_mib
        if not self._maintenance_attempted:
            self._maintenance_attempted = True
            self._maintenance_pending_until = now + KUTTA_MAINTENANCE_SETTLE_SECONDS
            self.process.write(b"MAINTENANCE\n")
            self.status.setText(
                f"Kutta detectó crecimiento sostenido ({used_mib:.0f} MiB). "
                "Liberando texturas y activando visualización reducida sin reiniciar…"
            )
            return

        if now-self._last_recovery_at < KUTTA_RECOVERY_COOLDOWN_SECONDS:
            self._degraded_mode = True
            self._maintenance_pending_until = now + KUTTA_MAINTENANCE_SETTLE_SECONDS
            self.process.write(b"MAINTENANCE\n")
            self._memory_baseline_bytes = private_bytes
            self._memory_samples.clear()
            self.status.setText(
                "Kutta permanece en modo visual reducido; se evitó otro reinicio "
                "durante el periodo de seguridad de diez minutos."
            )
            return

        self._degraded_mode = True
        self._last_recovery_at = now
        self._auto_restart_pending = True
        self._stop_reason = (
            "Kutta confirmó crecimiento sostenido después del mantenimiento: "
            f"{used_mib:.0f} MiB. Se restaurará la misma sesión en modo reducido."
        )
        self.stop(preserve_auto_restart=True)

    def _embedding_timeout_tick(self) -> None:
        self._polls += 1
        if self._polls >= self._MAX_POLLS:
            self._window_timer.stop()
            self.status.setText("Kutta inició, pero su ventana nativa no apareció en 12 segundos.")
            self.restart_button.setEnabled(True)

    def _read_process_output(self) -> None:
        chunk = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if not chunk:
            return
        self._process_output += chunk
        if len(self._process_output) > KUTTA_OUTPUT_LIMIT_CHARS:
            self._process_output = self._process_output[-KUTTA_OUTPUT_LIMIT_CHARS:]
        if "KUTTA_MAINTENANCE_DONE" in chunk:
            self._maintenance_pending_until = 0.0
            self._memory_started_at = time.monotonic()
            current = process_private_bytes(int(self.process.processId()))
            if current is not None:
                self._memory_baseline_bytes = current
            self._memory_samples.clear()
            self.status.setText(
                "Mantenimiento gráfico completado sin reiniciar. "
                "La escena y los controles se conservaron."
            )
        marker = "KUTTA_EMBEDDED hwnd="
        if marker not in self._process_output or self._hwnd:
            return
        try:
            self._hwnd = int(self._process_output.split(marker, 1)[1].split()[0])
        except (IndexError, ValueError):
            return
        self._window_timer.stop()
        self.restart_button.setEnabled(True)
        self.status.setText(
            "Kutta está integrado como aplicación de escritorio. Teclas: ↑/↓ ángulo, "
            "Tab perfil, V campo, S líneas, Espacio pausa y E editor. "
            f"Recuperaciones automáticas de memoria: {self._memory_recovery_count}."
        )

    def closeEvent(self, event: object) -> None:
        self.stop()
        super().closeEvent(event)


def _user32() -> ctypes.WinDLL:
    if sys.platform != "win32":
        raise OSError("Win32 no está disponible en este sistema.")
    return ctypes.WinDLL("user32", use_last_error=True)


def _post_close(hwnd: int) -> None:
    if sys.platform == "win32":
        user32 = _user32()
        user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
