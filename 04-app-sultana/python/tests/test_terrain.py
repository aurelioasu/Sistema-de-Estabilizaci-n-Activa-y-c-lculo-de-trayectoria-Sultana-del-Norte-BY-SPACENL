from app.services.terrain import TerrainMapService


def test_terrain_detail_tracks_the_interactive_map_zoom() -> None:
    assert TerrainMapService.map_zoom(10) == 13
    assert TerrainMapService.map_zoom(15.4) == 15
    assert TerrainMapService.map_zoom(22) == 18
    assert TerrainMapService.tile_count_for_zoom(14) == 3
    assert TerrainMapService.tile_count_for_zoom(15) == 5


def test_web_mercator_tile_position_is_finite_and_in_range() -> None:
    x, y = TerrainMapService._tile_position(25.67678, -100.25646, 13)
    assert 0.0 <= x < 2**13
    assert 0.0 <= y < 2**13
