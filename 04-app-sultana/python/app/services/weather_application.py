from __future__ import annotations

from typing import Any

import numpy as np

from .weather import WeatherProfile


def apply_weather_profile(config: object, weather_profile: WeatherProfile) -> None:
    """Copy one immutable weather profile into a core configuration.

    Both nominal and Monte Carlo calls use this function before any uncertainty is
    added, so their deterministic starting atmosphere is identical.
    """
    environment = config.environment
    environment.surface_temperature_k = weather_profile.surface_temperature_k
    environment.surface_pressure_pa = weather_profile.surface_pressure_pa
    environment.humidity_ratio = weather_profile.humidity_ratio
    environment.mean_wind_enu_mps = np.asarray(weather_profile.mean_wind_enu_mps, dtype=float)
    environment.rain_rate_mm_h = weather_profile.rain_rate_mm_h
    environment.rain_cd_delta = weather_profile.rain_cd_delta
    # Gusts are not a resolved vortex measurement.  Their excess over the
    # hourly mean sets the amplitude of the deterministic turbulent wind used
    # by the 6-DoF model, preserving a reproducible seed for Monte Carlo.
    mean_speed = float(np.linalg.norm(environment.mean_wind_enu_mps))
    environment.turbulence_intensity_mps = max(
        float(getattr(environment, "turbulence_intensity_mps", 0.0)),
        max(0.0, float(weather_profile.wind_gust_mps) - mean_speed) / (2.0 ** 0.5),
    )
    if weather_profile.atmosphere_profile:
        try:
            import sultana_core
            profile = []
            for altitude, temperature, pressure, humidity, wind in weather_profile.atmosphere_profile:
                point = sultana_core.AtmospherePoint()
                point.altitude_agl_m = float(altitude)
                point.temperature_k = float(temperature)
                point.pressure_pa = float(pressure)
                point.relative_humidity = float(humidity)
                point.wind_enu_mps = np.asarray(wind, dtype=float)
                profile.append(point)
            environment.profile = profile
        except ImportError:
            # The core is required only when a simulation is built. Keeping
            # this module importable makes the weather boundary testable.
            pass


def local_weather_profile(weather: dict[str, Any]) -> WeatherProfile:
    """Build the explicit fallback profile from the scenario's local data."""
    rain = float(weather.get("rain_rate_mm_h", 0.0))
    return WeatherProfile(
        source="local_profile", surface_temperature_k=float(weather["surface_temperature_k"]),
        surface_pressure_pa=float(weather["surface_pressure_pa"]), humidity_ratio=float(weather["humidity_ratio"]),
        mean_wind_enu_mps=tuple(float(value) for value in weather["mean_wind_enu_mps"]),
        rain_rate_mm_h=rain, rain_cd_delta=float(weather.get("rain_cd_delta", min(0.15, 0.01 * rain))), raw={},
        wind_gust_mps=float(weather.get("wind_gust_mps", 0.0)),
    )
