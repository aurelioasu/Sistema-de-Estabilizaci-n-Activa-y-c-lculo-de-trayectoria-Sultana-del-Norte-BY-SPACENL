from app.ui.map_widget import MapWidget


def test_map_reports_zoom_and_dispersion_card_can_be_minimized() -> None:
    page = MapWidget._html()
    assert "bridge.update_map_center(c.lat, c.lng, map.getZoom())" in page
    assert "dispersionPopupContent" in page
    assert "is-minimized" in page
    assert "Minimizar tarjeta" in page


def test_interactive_map_defines_distinct_dispersion_overlays() -> None:
    page = MapWidget._html()
    assert "window.setFlight" in page
    assert "window.setDispersion" in page
    assert "dispersion-fill" in page
    assert "Centro estimado de dispersión" in page
    assert "El área sombreada contiene aproximadamente 95%" in page
