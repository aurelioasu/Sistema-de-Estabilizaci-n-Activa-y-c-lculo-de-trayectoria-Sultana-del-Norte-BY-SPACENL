from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class GeocodedLocation:
    name: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float


class LocationService:
    """Geocodes a human-readable launch site through Open-Meteo's free endpoint."""

    async def resolve(self, query: str) -> GeocodedLocation:
        cleaned = query.strip()
        if not cleaned:
            raise ValueError("Escribe una ubicación, por ejemplo: Guadalupe, Nuevo León")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": cleaned, "count": 1, "language": "es", "format": "json"})
            response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            raise ValueError(f"No se encontró la ubicación: {cleaned}")
        result = results[0]
        labels = [result.get("name", cleaned), result.get("admin1"), result.get("country")]
        return GeocodedLocation(
            name=", ".join(label for label in labels if label),
            latitude_deg=float(result["latitude"]), longitude_deg=float(result["longitude"]),
            elevation_m=float(result.get("elevation") or 0.0),
        )


class ElevationService:
    """Looks up terrain elevation in metres above mean sea level."""

    async def fetch(self, latitude_deg: float, longitude_deg: float) -> float:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.open-meteo.com/v1/elevation",
                params={"latitude": latitude_deg, "longitude": longitude_deg},
            )
            response.raise_for_status()
        elevations = response.json().get("elevation", [])
        if not elevations:
            raise ValueError("El servicio de elevacion no devolvio una altitud")
        return float(elevations[0])
