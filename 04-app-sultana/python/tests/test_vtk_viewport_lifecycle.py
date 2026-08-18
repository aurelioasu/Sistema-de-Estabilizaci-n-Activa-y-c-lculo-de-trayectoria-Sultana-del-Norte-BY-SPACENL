from __future__ import annotations

import inspect

from app.ui.cfd_viewport import CfdViewport
from app.ui.rocket_viewport import RocketViewport


class FakePlotter:
    def __init__(self) -> None:
        self.suppress_rendering = False
        self.render_count = 0
        self.close_count = 0

    def render(self) -> None:
        self.render_count += 1

    def close(self) -> None:
        self.close_count += 1


class FakeTimer:
    def __init__(self) -> None:
        self.active = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False


def test_cfd_renderer_and_animation_are_suspended_while_hidden() -> None:
    viewport = CfdViewport.__new__(CfdViewport)
    viewport._plotter = FakePlotter()
    viewport._flow_timer = FakeTimer()
    viewport._render_enabled = False
    viewport._widget_visible = False
    viewport._closed = False
    viewport._render_pending = False

    viewport._render_scene()
    assert viewport._plotter.render_count == 0
    viewport.set_rendering_enabled(True)
    assert viewport._plotter.suppress_rendering is True
    assert viewport._flow_timer.active is False

    viewport._widget_visible = True
    viewport._sync_render_state()
    viewport._render_scene()
    assert viewport._plotter.render_count == 1
    assert viewport._flow_timer.active is True

    plotter = viewport._plotter
    viewport.shutdown()
    assert plotter.close_count == 1
    assert viewport._flow_timer.active is False
    assert viewport._plotter is None


def test_both_qtinteractors_disable_implicit_auto_rendering() -> None:
    assert "auto_update=False" in inspect.getsource(CfdViewport.__init__)
    assert "auto_update=False" in inspect.getsource(RocketViewport.__init__)
    assert "suppress_rendering" in inspect.getsource(CfdViewport._sync_render_state)
    assert "suppress_rendering" in inspect.getsource(RocketViewport._sync_render_suppression)
