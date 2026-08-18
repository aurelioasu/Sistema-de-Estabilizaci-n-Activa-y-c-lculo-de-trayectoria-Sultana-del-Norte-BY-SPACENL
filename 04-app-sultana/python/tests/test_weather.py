import asyncio
from datetime import date, datetime

import httpx
import pytest

from app.services.weather import WeatherService


def test_open_meteo_values_are_converted_to_si_enu() -> None:
    payload = {
        "hourly": {
            "time": ["2026-07-29T15:00"],
            "temperature_2m": [20.0], "surface_pressure": [1000.0],
            "relative_humidity_2m": [50.0], "wind_speed_10m": [36.0], "wind_direction_10m": [90.0], "rain": [1.2],
        }
    }

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    profile = asyncio.run(WeatherService(httpx.MockTransport(responder)).fetch(25.0, -100.0, datetime.combine(date.today(), datetime.min.time()).replace(hour=15)))
    assert profile.source == "open_meteo"
    assert profile.surface_temperature_k == 293.15
    assert profile.surface_pressure_pa == 100000.0
    assert profile.humidity_ratio == 0.5
    assert profile.rain_rate_mm_h == 1.2
    assert profile.mean_wind_enu_mps == pytest.approx((-10.0, 0.0, 0.0))


def test_open_meteo_pressure_levels_create_an_agl_profile_and_gust_value() -> None:
    payload = {
        "elevation": 500.0,
        "hourly": {
            "time": ["2026-07-29T15:00"], "temperature_2m": [20.0], "surface_pressure": [950.0],
            "relative_humidity_2m": [50.0], "wind_speed_10m": [18.0], "wind_direction_10m": [180.0],
            "wind_gusts_10m": [36.0], "rain": [0.0],
            "temperature_950hPa": [18.0], "relative_humidity_950hPa": [45.0],
            "wind_speed_950hPa": [36.0], "wind_direction_950hPa": [90.0], "geopotential_height_950hPa": [900.0],
        },
    }
    profile = asyncio.run(WeatherService(httpx.MockTransport(lambda request: httpx.Response(200, json=payload))).fetch(
        25.0, -100.0, datetime.combine(date.today(), datetime.min.time()).replace(hour=15),
    ))
    assert profile.wind_gust_mps == pytest.approx(10.0)
    assert profile.atmosphere_profile[1][0] == pytest.approx(400.0)
    assert profile.atmosphere_profile[1][4] == pytest.approx((-10.0, 0.0, 0.0))
