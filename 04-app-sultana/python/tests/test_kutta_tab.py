"""Tests for the desktop-only Kutta integration."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from collections import deque

from PySide6.QtCore import QProcess

from app.main import MainWindow
from app.ui import kutta_tab
from app.ui.kutta_tab import (
    KUTTA_GO_GC_PERCENT,
    KUTTA_GO_MEMORY_LIMIT,
    KUTTA_MEMORY_GROWTH_BYTES,
    KUTTA_MEMORY_WARMUP_SECONDS,
    KUTTA_OUTPUT_LIMIT_CHARS,
    KuttaTab,
    clear_kutta_session,
    kutta_arguments,
    kutta_executable,
    kutta_process_environment,
    sustained_memory_growth,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_kutta_go_source_is_a_top_level_project_module() -> None:
    assert (PROJECT_ROOT / "kutta" / "go.mod").is_file()
    assert (PROJECT_ROOT / "kutta" / "LICENSE").is_file()
    assert not (PROJECT_ROOT / "third_party" / "kutta").exists()


def test_kutta_executable_is_resolved_inside_application_root(tmp_path: Path) -> None:
    assert kutta_executable(tmp_path) == tmp_path / "tools" / "kutta" / "kutta.exe"


def test_kutta_launch_has_no_browser_or_web_server_arguments(tmp_path: Path) -> None:
    checkpoint = tmp_path / "session.json"
    arguments = kutta_arguments(12345, session_file=checkpoint)
    joined = " ".join(arguments).lower()
    assert arguments == [
        "-tps", "30", "-parent-hwnd", "12345", "-session-state", str(checkpoint)
    ]
    assert "http" not in joined
    assert "browser" not in joined
    assert "wasm" not in joined


def test_degraded_recovery_keeps_a_fluid_tick_rate(tmp_path: Path) -> None:
    arguments = kutta_arguments(
        12345,
        session_file=tmp_path / "session.json",
        degraded=True,
    )
    assert arguments[:2] == ["-tps", "30"]
    assert "-degraded" in arguments


def test_manual_reset_clears_only_its_known_checkpoint(tmp_path: Path) -> None:
    checkpoint = tmp_path / "kutta-session.json"
    checkpoint.write_text('{"version": 1}', encoding="utf-8")
    unrelated = tmp_path / "keep-me.json"
    unrelated.write_text("keep", encoding="utf-8")

    assert clear_kutta_session(checkpoint) is True
    assert not checkpoint.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_kutta_memory_monitor_has_no_absolute_process_limit() -> None:
    assert KUTTA_MEMORY_GROWTH_BYTES == 512 * 1024 * 1024
    assert KUTTA_MEMORY_WARMUP_SECONDS == 45.0
    assert not hasattr(kutta_tab, "KUTTA_MEMORY_LIMIT_BYTES")


def test_kutta_go_runtime_has_an_explicit_heap_budget() -> None:
    environment = kutta_process_environment()
    assert environment.value("GOMEMLIMIT") == KUTTA_GO_MEMORY_LIMIT == "384MiB"
    assert environment.value("GOGC") == KUTTA_GO_GC_PERCENT == "75"


def test_normal_gpu_reservation_does_not_trigger_recovery() -> None:
    gib = 1024 * 1024 * 1024
    samples = [(float(second), gib + (second % 3) * 1024 * 1024) for second in range(0, 34, 2)]
    assert sustained_memory_growth(
        samples,
        baseline_bytes=gib,
        available_system_bytes=1024 * 1024 * 1024,
    ) is False


def test_sustained_growth_requires_pressure_and_crosses_emergency_limit() -> None:
    mib = 1024 * 1024
    samples = [(float(second), (900 + second * 20) * mib) for second in range(0, 34, 2)]
    assert sustained_memory_growth(
        samples,
        baseline_bytes=900 * mib,
        available_system_bytes=1024 * mib,
    ) is True


def test_sustained_gpu_reservation_growth_is_ignored_while_system_is_healthy() -> None:
    mib = 1024 * 1024
    samples = [(float(second), (900 + second * 20) * mib) for second in range(0, 34, 2)]
    assert sustained_memory_growth(
        samples,
        baseline_bytes=900 * mib,
        available_system_bytes=8 * 1024 * mib,
    ) is False


def test_thirty_minutes_of_stable_gpu_reservation_never_looks_like_a_leak() -> None:
    mib = 1024 * 1024
    samples = [
        (float(second), (1180 + (second // 2) % 4) * mib)
        for second in range(0, 30 * 60 + 1, 2)
    ]
    assert sustained_memory_growth(
        samples,
        baseline_bytes=1180 * mib,
        available_system_bytes=900 * mib,
    ) is False


def test_watchdog_never_restarts_during_warmup(monkeypatch) -> None:
    class FakeProcess:
        def state(self):
            return QProcess.ProcessState.Running

        def processId(self):
            return 1234

    class FakeTab:
        process = FakeProcess()
        _memory_timer = SimpleNamespace(stop=lambda: None)
        _stop_reason = ""
        _auto_restart_pending = False
        _memory_started_at = 100.0
        _memory_baseline_bytes = 0
        _memory_samples = deque(maxlen=24)
        _maintenance_pending_until = 0.0
        stopped = False

        def stop(self, *, preserve_auto_restart=False):
            self.stopped = True

    fake = FakeTab()
    monkeypatch.setattr(kutta_tab, "process_private_bytes", lambda _pid: 3 * 1024 * 1024 * 1024)
    monkeypatch.setattr(kutta_tab, "system_available_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr(kutta_tab.time, "monotonic", lambda: 110.0)

    KuttaTab._check_memory(fake)

    assert fake.stopped is False
    assert fake._auto_restart_pending is False


def test_kutta_process_output_is_bounded() -> None:
    class FakeOutput(bytes):
        pass

    class FakeProcess:
        def readAllStandardOutput(self):
            return FakeOutput(b"x" * (KUTTA_OUTPUT_LIMIT_CHARS + 4096))

    fake = SimpleNamespace(
        process=FakeProcess(),
        _process_output="",
        _hwnd=0,
    )
    KuttaTab._read_process_output(fake)
    assert len(fake._process_output) == KUTTA_OUTPUT_LIMIT_CHARS


def test_finished_memory_recovery_schedules_restart() -> None:
    class FakeTimer:
        started = False

        def stop(self):
            pass

        def start(self):
            self.started = True

    class FakeWidget:
        enabled = True

        def setEnabled(self, enabled):
            self.enabled = enabled

    class FakeStatus:
        text = ""

        def setText(self, text):
            self.text = text

    fake = SimpleNamespace(
        _window_timer=FakeTimer(),
        _memory_timer=FakeTimer(),
        _restart_timer=FakeTimer(),
        _hwnd=123,
        _auto_restart_pending=True,
        _memory_recovery_count=0,
        _last_memory_mib=1024.0,
        restart_button=FakeWidget(),
        status=FakeStatus(),
    )
    KuttaTab._process_finished(fake, 0, object())
    assert fake._restart_timer.started is True
    assert fake._memory_recovery_count == 1
    assert "Reiniciando automáticamente" in fake.status.text


def test_repeated_memory_recoveries_do_not_become_one_shot() -> None:
    class FakeTimer:
        starts = 0

        def stop(self):
            pass

        def start(self):
            self.starts += 1

    widget = SimpleNamespace(setEnabled=lambda _enabled: None)
    status = SimpleNamespace(setText=lambda _text: None)
    fake = SimpleNamespace(
        _window_timer=FakeTimer(),
        _memory_timer=FakeTimer(),
        _restart_timer=FakeTimer(),
        _hwnd=123,
        _auto_restart_pending=True,
        _memory_recovery_count=0,
        _last_memory_mib=1024.0,
        restart_button=widget,
        status=status,
    )
    for expected in range(1, 4):
        fake._auto_restart_pending = True
        KuttaTab._process_finished(fake, 0, object())
        assert fake._memory_recovery_count == expected
    assert fake._restart_timer.starts == 3


def test_leaving_tab_cancels_a_scheduled_memory_restart() -> None:
    class FakeTimer:
        stopped = False

        def stop(self):
            self.stopped = True

    fake = SimpleNamespace(
        _window_timer=FakeTimer(),
        _memory_timer=FakeTimer(),
        _restart_timer=FakeTimer(),
        _auto_restart_pending=True,
        process=SimpleNamespace(state=lambda: QProcess.ProcessState.NotRunning),
        _hwnd=123,
    )
    KuttaTab.stop(fake)
    assert fake._auto_restart_pending is False
    assert fake._restart_timer.stopped is True


def test_changing_tabs_stops_hidden_kutta_process() -> None:
    class FakeTunnel:
        starts = 0
        stops = 0

        def start(self):
            self.starts += 1

        def stop(self):
            self.stops += 1

    tunnel = FakeTunnel()
    other = object()
    fake_window = SimpleNamespace(
        wind_tunnel=tunnel,
        tabs=SimpleNamespace(widget=lambda index: tunnel if index == 3 else other),
    )

    MainWindow._tab_changed(fake_window, 3)
    MainWindow._tab_changed(fake_window, 2)

    assert tunnel.starts == 1
    assert tunnel.stops == 1
