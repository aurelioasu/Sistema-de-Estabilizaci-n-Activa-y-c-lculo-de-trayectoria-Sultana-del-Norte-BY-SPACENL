from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

import httpx


@dataclass(frozen=True)
class WeatherProfile:
    source: str
    surface_temperature_k: float
    surface_pressure_pa: float
    humidity_ratio: float
    mean_wind_enu_mps: tuple[float, float, float]
    rain_rate_mm_h: float
    raw: dict[str, Any]
    rain_cd_delta: float = 0.0
    wind_gust_mps: float = 0.0
    # (altitude AGL m, temperature K, pressure Pa, relative humidity,
    #  wind ENU m/s).  Filled from Open-Meteo pressure-level fields when the
    # selected weather model provides them.
    atmosphere_profile: tuple[tuple[float, float, float, float, tuple[float, float, float]], ...] = ()


class WeatherService:
    """Optional Open-Meteo enrichment; callers retain local data on failure."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def fetch(self, latitude_deg: float, longitude_deg: float, when: datetime | date) -> WeatherProfile:
        requested = when if isinstance(when, datetime) else datetime.combine(when, time.min)
        pressure_levels = (1000, 975, 950, 925, 900, 850, 800, 700)
        surface_fields = (
            "temperature_2m,relative_humidity_2m,dew_point_2m,surface_pressure,pressure_msl,"
            "wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
            "precipitation,rain,showers,snowfall,cloud_cover,visibility,cape,"
            "vapour_pressure_deficit"
        )
        level_fields = ",".join(
            field
            for level in pressure_levels
            for field in (
                f"temperature_{level}hPa", f"relative_humidity_{level}hPa",
                f"wind_speed_{level}hPa", f"wind_direction_{level}hPa", f"geopotential_height_{level}hPa",
            )
        )
        params = {
            "latitude": latitude_deg, "longitude": longitude_deg,
            "hourly": f"{surface_fields},{level_fields}",
            "start_date": requested.date().isoformat(), "end_date": requested.date().isoformat(), "timezone": "auto",
        }
        endpoint = "https://archive-api.open-meteo.com/v1/archive" if requested.date() < date.today() else "https://api.open-meteo.com/v1/forecast"
        async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
            response = await client.get(endpoint, params=params)
            response.raise_for_status()
        hourly = response.json()["hourly"]
        timestamps = [datetime.fromisoformat(value) for value in hourly["time"]]
        index = min(range(len(timestamps)), key=lambda candidate: abs((timestamps[candidate] - requested).total_seconds()))
        def value(field: str, fallback: float = 0.0) -> float:
            values = hourly.get(field)
            if not values or values[index] is None:
                return fallback
            return float(values[index])

        def wind_enu(speed_kmh: float, direction_from_deg: float) -> tuple[float, float, float]:
            speed = speed_kmh / 3.6
            direction_from = math.radians(direction_from_deg)
            return (-speed * math.sin(direction_from), -speed * math.cos(direction_from), 0.0)

        speed_kmh = value("wind_speed_10m")
        wind_east, wind_north, _ = wind_enu(speed_kmh, value("wind_direction_10m"))
        elevation_msl = float(response.json().get("elevation", 0.0) or 0.0)
        profile_rows = [(0.0, value("temperature_2m") + 273.15, value("surface_pressure") * 100.0,
                         value("relative_humidity_2m") / 100.0, (wind_east, wind_north, 0.0))]
        for level in pressure_levels:
            height = value(f"geopotential_height_{level}hPa", float("nan"))
            temperature = value(f"temperature_{level}hPa", float("nan"))
            if not math.isfinite(height) or not math.isfinite(temperature):
                continue
            altitude_agl = height - elevation_msl
            if altitude_agl <= profile_rows[-1][0] + 1.0:
                continue
            level_wind = wind_enu(value(f"wind_speed_{level}hPa"), value(f"wind_direction_{level}hPa"))
            profile_rows.append((altitude_agl, temperature + 273.15, level * 100.0,
                                 max(0.0, min(1.0, value(f"relative_humidity_{level}hPa") / 100.0)), level_wind))
        return WeatherProfile(
            source="open_meteo", surface_temperature_k=value("temperature_2m") + 273.15,
            surface_pressure_pa=value("surface_pressure") * 100.0,
            humidity_ratio=value("relative_humidity_2m") / 100.0,
            mean_wind_enu_mps=(wind_east, wind_north, 0.0), raw=response.json(),
            rain_rate_mm_h=value("rain"), rain_cd_delta=min(0.15, 0.01 * value("rain")),
            wind_gust_mps=value("wind_gusts_10m") / 3.6,
            atmosphere_profile=tuple(profile_rows),
        )
