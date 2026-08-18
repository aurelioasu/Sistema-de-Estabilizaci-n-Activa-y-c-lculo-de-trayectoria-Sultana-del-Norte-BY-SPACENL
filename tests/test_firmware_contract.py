from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_ROOT = REPOSITORY_ROOT / "03-firmware-esp32"
SKETCHES = tuple(sorted(FIRMWARE_ROOT.rglob("*.ino")))
FLIGHT_SKETCH = FIRMWARE_ROOT / "control-de-vuelo" / "Control_Vuelo_Sultana" / "Control_Vuelo_Sultana.ino"


def radio_address(sketch: Path) -> str:
    source = sketch.read_text(encoding="utf-8")
    match = re.search(r'(?:RADIO_DIRECCION|DIRECCION_RADIO)\[6\]\s*=\s*"([A-Z0-9]{5})"', source)
    assert match is not None, f"{sketch.name} no declara una dirección de radio de cinco caracteres"
    return match.group(1)


def test_all_radio_endpoints_use_sultana_address() -> None:
    assert len(SKETCHES) == 3

    addresses = {radio_address(sketch) for sketch in SKETCHES}

    assert addresses == {"SNL01"}


def test_flight_safety_interlocks_remain_disabled_by_default() -> None:
    source = FLIGHT_SKETCH.read_text(encoding="utf-8")
    required_interlocks = (
        "PERMITIR_MOVIMIENTO_CANARDS",
        "MEZCLA_Y_SENTIDOS_VALIDADOS",
        "HABILITAR_SALIDA_PARACAIDAS",
    )

    for interlock in required_interlocks:
        declaration = re.search(rf"const bool {interlock}\s*=\s*(true|false)\s*;", source)
        assert declaration is not None, f"Falta el bloqueo de seguridad {interlock}"
        assert declaration.group(1) == "false", f"{interlock} debe iniciar deshabilitado"


def test_flight_arming_command_names_sultana() -> None:
    source = FLIGHT_SKETCH.read_text(encoding="utf-8")

    assert 'strcmp(comando,"ARMAR SULTANA")' in source
