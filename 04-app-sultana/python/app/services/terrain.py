from __future__ import annotations

import asyncio
import io
import math
from dataclasses import dataclass

import httpx
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class TerrainRaster:
    """A georeferenced satellite mosaic expressed in local ENU metres."""

    pixels_rgb: np.ndarray
    center_east_m: float
    center_north_m: float
    width_m: float
    height_m: float
    attribution: str = "OpenStreetMap"
    # Geographic point used as ENU origin while downloading this mosaic.
    # It lets a map panned away from the launch site remain correctly placed
    # in the flight scene instead of moving the launch marker with its texture.
    reference_latitude_deg: float = 0.0
    reference_longitude_deg: float = 0.0
    zoom: int = 17


class TerrainMapService:
    """Downloads a small public map tile mosaic for the 3D reference view."""

    TILE_SIZE = 256
    MIN_ZOOM = 13
    MAX_ZOOM = 18

    @classmethod
    def map_zoom(cls, requested_zoom: float | int) -> int:
        """Keep raster requests within a useful, polite OpenStreetMap range."""
        return max(cls.MIN_ZOOM, min(cls.MAX_ZOOM, round(float(requested_zoom))))

    @classmethod
    def tile_count_for_zoom(cls, zoom: int) -> int:
        """Use a denser mosaic when the 2D map is zoomed in.

        Five by five tiles give the 3D view 1280 pixels per side at close
        range, while the three by three overview avoids unnecessary requests.
        """
        return 5 if zoom >= 15 else 3

    @staticmethod
    def _tile_position(latitude_deg: float, longitude_deg: float, zoom: int) -> tuple[float, float]:
        scale = 2**zoom
        latitude = math.radians(max(-85.05112878, min(85.05112878, latitude_deg)))
        return ((longitude_deg + 180.0) / 360.0 * scale,
                (1.0 - math.asinh(math.tan(latitude)) / math.pi) / 2.0 * scale)

    async def fetch(
        self, latitude_deg: float, longitude_deg: float, zoom: float | int = 17, tile_count: int | None = None,
    ) -> TerrainRaster:
        zoom = self.map_zoom(zoom)
        tile_count = self.tile_count_for_zoom(zoom) if tile_count is None else max(1, int(tile_count))
        x_float, y_float = self._tile_position(latitude_deg, longitude_deg, zoom)
        x_start, y_start = math.floor(x_float) - tile_count // 2, math.floor(y_float) - tile_count // 2
        tile_limit = 2**zoom
        requests = []
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "SultanaFlightSimulator/0.1"}) as client:
            for tile_y in range(y_start, y_start + tile_count):
                for tile_x in range(x_start, x_start + tile_count):
                    url = f"https://tile.openstreetmap.org/{zoom}/{tile_x % tile_limit}/{tile_y}.png"
                    requests.append(client.get(url))
            responses = await asyncio.gather(*requests)
        canvas = Image.new("RGB", (tile_count * self.TILE_SIZE, tile_count * self.TILE_SIZE))
        for index, response in enumerate(responses):
            response.raise_for_status()
            tile = Image.open(io.BytesIO(response.content)).convert("RGB")
            canvas.paste(tile, ((index % tile_count) * self.TILE_SIZE, (index // tile_count) * self.TILE_SIZE))
        metres_per_pixel = 156543.03392804097 * math.cos(math.radians(latitude_deg)) / (2**zoom)
        image_pixels = tile_count * self.TILE_SIZE
        launch_pixel_x, launch_pixel_y = x_float * self.TILE_SIZE, y_float * self.TILE_SIZE
        center_pixel_x, center_pixel_y = (x_start * self.TILE_SIZE + image_pixels / 2), (y_start * self.TILE_SIZE + image_pixels / 2)
        return TerrainRaster(
            pixels_rgb=np.asarray(canvas),
            center_east_m=(center_pixel_x - launch_pixel_x) * metres_per_pixel,
            center_north_m=-(center_pixel_y - launch_pixel_y) * metres_per_pixel,
            width_m=image_pixels * metres_per_pixel,
            height_m=image_pixels * metres_per_pixel,
            reference_latitude_deg=float(latitude_deg),
            reference_longitude_deg=float(longitude_deg),
            zoom=zoom,
        )
