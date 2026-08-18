from __future__ import annotations

import json
import re
from typing import Any, Callable

try:
    import serial  # type: ignore[import-not-found]
    from serial.tools import list_ports  # type: ignore[import-not-found]
except ImportError:  # The UI remains usable when the optional runtime is absent.
    serial = None
    list_ports = None


ALIASES = {
    "t": "time_s", "time": "time_s", "tiempo": "time_s",
    "alt": "altitude_agl_m", "altitude": "altitude_agl_m", "altura": "altitude_agl_m",
    "altitude_agl": "altitude_agl_m", "altitud_agl": "altitude_agl_m",
    "altitude_ekf": "estimated_altitude_agl_m", "altitud_ekf": "estimated_altitude_agl_m",
    "q": "dynamic_pressure_pa", "q_pa": "dynamic_pressure_pa", "presion_dinamica": "dynamic_pressure_pa",
    "pitch": "pitch_deg", "yaw": "yaw_deg", "roll": "roll_deg",
    "airspeed": "airspeed_mps", "velocidad": "airspeed_mps", "velocidad_relativa": "airspeed_mps",
    "wind_e": "wind_east_mps", "viento_e": "wind_east_mps",
    "wind_n": "wind_north_mps", "viento_n": "wind_north_mps",
    "drag": "drag_force_n", "arrastre": "drag_force_n",
    "rain_force": "rain_impact_force_n", "fuerza_lluvia": "rain_impact_force_n",
    "canard_lift": "canard_lift_n", "sustentacion_canards": "canard_lift_n",
    "mass": "mass_kg", "masa": "mass_kg", "thrust": "thrust_n", "empuje": "thrust_n",
    "c1": "canard1_deg", "c2": "canard2_deg", "c3": "canard3_deg", "c4": "canard4_deg",
    "estado": "state", "paracaidas": "parachute_deployed",
    "bateria": "battery_v", "rssi": "rssi_dbm",
}


def _normalise_key(key: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9_]+", "_", key.strip().lower()).strip("_")
    return ALIASES.get(key, key)


def _coerce(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = value.strip()
    if value.lower() in {"true", "si", "sí", "yes", "on"}:
        return True
    if value.lower() in {"false", "no", "off"}:
        return False
    try:
        return float(value)
    except ValueError:
        return value


def parse_telemetry_line(line: bytes | str) -> dict[str, Any]:
    """Parse one ESP packet encoded as JSON or comma-separated key/value pairs."""
    text = line.decode("utf-8", errors="replace").strip() if isinstance(line, bytes) else line.strip()
    if not text:
        return {}
    if text.startswith("{"):
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("El paquete JSON debe ser un objeto")
    else:
        raw = {}
        for part in re.split(r"[,;]", text):
            if not part.strip():
                continue
            separator = "=" if "=" in part else ":" if ":" in part else None
            if separator is None:
                raise ValueError(f"Campo sin separador: {part.strip()}")
            key, value = part.split(separator, 1)
            raw[key] = value
    return {_normalise_key(str(key)): _coerce(value) for key, value in raw.items()}


def available_serial_ports() -> list[tuple[str, str]]:
    if list_ports is None:
        return []
    return [(port.device, port.description or port.device) for port in list_ports.comports()]


class SerialTelemetryClient:
    def __init__(self, factory: Callable[..., Any] | None = None) -> None:
        self._factory = factory
        self._port: Any | None = None
        self._buffer = b""

    @property
    def available(self) -> bool:
        return self._factory is not None or serial is not None

    @property
    def is_open(self) -> bool:
        return bool(self._port is not None and getattr(self._port, "is_open", True))

    def open(self, port: str, baudrate: int) -> None:
        self.close()
        factory = self._factory or (serial.Serial if serial is not None else None)
        if factory is None:
            raise RuntimeError("Falta pyserial; instala la dependencia para usar el receptor COM")
        self._port = factory(port=port, baudrate=baudrate, timeout=0)
        self._buffer = b""

    def close(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            finally:
                self._port = None
                self._buffer = b""

    def read_available(self, max_lines: int = 100) -> tuple[list[dict[str, Any]], list[str]]:
        packets: list[dict[str, Any]] = []
        errors: list[str] = []
        if not self.is_open:
            return packets, errors
        waiting = int(getattr(self._port, "in_waiting", 0))
        if waiting > 0:
            if hasattr(self._port, "read"):
                self._buffer += self._port.read(waiting)
            else:  # Test doubles and unusual serial adapters.
                for _ in range(min(waiting, max_lines)):
                    self._buffer += self._port.readline()
        if len(self._buffer) > 1_048_576:
            self._buffer = self._buffer[-65_536:]
            errors.append("Búfer serial desbordado; se descartó un paquete incompleto")
        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop() if lines else b""
        for line in lines[:max_lines]:
            try:
                packet = parse_telemetry_line(line)
                if packet:
                    packets.append(packet)
            except (ValueError, json.JSONDecodeError) as exc:
                errors.append(str(exc))
        if len(lines) > max_lines:
            self._buffer = b"\n".join(lines[max_lines:]) + b"\n" + self._buffer
        return packets, errors
