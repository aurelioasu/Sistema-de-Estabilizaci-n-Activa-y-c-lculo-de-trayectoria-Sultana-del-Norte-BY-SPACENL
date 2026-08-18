from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from app.services.config_loader import load_aero_table, load_mass_curve, load_scenario, motor_options
from app.services.openrocket import OpenRocketImportError, apply_openrocket_model, load_openrocket


ROOT = Path(__file__).resolve().parents[2]


ORK_XML = """<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.10" creator="OpenRocket 24.12">
  <rocket><name>Cohete de prueba</name><subcomponents><stage><subcomponents>
    <nosecone><name>Ojiva</name><overridemass>0.1</overridemass><overridecg>0.1</overridecg>
      <length>0.2</length><shape>ellipsoid</shape><aftradius>0.025</aftradius></nosecone>
    <bodytube><name>Cuerpo</name><overridemass>0.9</overridemass><overridecg>0.3</overridecg>
      <length>0.6</length><radius>0.025</radius><motormount><motor configid="external">
        <manufacturer>AeroTech</manufacturer><designation>H115DM</designation><length>0.2</length>
      </motor></motormount><subcomponents>
        <trapezoidfinset><name>Canards</name><position type="top">0.2</position>
          <overridemass>0.1</overridemass><overridecg>0.05</overridecg><fincount>4</fincount>
          <rootchord>0.1</rootchord><tipchord>0.05</tipchord><sweeplength>0.02</sweeplength><height>0.04</height>
        </trapezoidfinset>
        <parachute><name>Paracaídas</name><position type="top">0.05</position>
          <overridemass>0.1</overridemass><overridecg>0.02</overridecg><packedlength>0.08</packedlength>
          <packedradius>0.02</packedradius><diameter>0.5</diameter><linecount>10</linecount><cd>auto</cd>
        </parachute>
      </subcomponents></bodytube>
  </subcomponents></stage></subcomponents></rocket>
</openrocket>"""


def _ork(tmp_path: Path) -> Path:
    path = tmp_path / "rocket.ork"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("rocket.ork", ORK_XML)
    return path


def test_openrocket_import_extracts_airframe_and_explicitly_reports_ignored_motor(tmp_path: Path) -> None:
    model = load_openrocket(_ork(tmp_path))
    assert model.name == "Cohete de prueba"
    assert model.dry_mass_kg == pytest.approx(1.2)
    assert model.body_length_m == pytest.approx(0.8)
    assert model.diameter_m == pytest.approx(0.05)
    assert model.parachute_area_m2 == pytest.approx(0.19634954)
    assert model.parachute_line_count == 10
    assert model.ignored_motors == ("AeroTech H115DM",)
    assert "Motor ORK ignorado: AeroTech H115DM" in model.summary()


def test_openrocket_airframe_keeps_only_the_three_local_knsb_motor_choices(tmp_path: Path) -> None:
    base = load_scenario(
        ROOT / "configs/vehicle/sultana_4canard.yaml",
        ROOT / "configs/environments/guadalupe_example.yaml",
    )
    base.vehicle["propulsion"]["selected_motor_id"] = "knsb_10cm"
    imported = apply_openrocket_model(base, load_openrocket(_ork(tmp_path)))
    assert [identifier for identifier, _label in motor_options(imported)] == ["knsb_10cm", "knsb_15cm", "knsb_20cm"]
    assert imported.vehicle["mass"]["propellant_mass_kg"] == pytest.approx(0.125)
    assert load_mass_curve(imported)[0][1] == pytest.approx(0.125)
    assert load_mass_curve(imported)[-1][1] == 0.0
    assert len(load_aero_table(imported)) >= 2
    assert "AeroTech" not in str(imported.vehicle["propulsion"])
    assert imported.vehicle["physical_inventory"]["openrocket_import"]["ignored_motors"] == ["AeroTech H115DM"]


def test_invalid_or_missing_openrocket_mass_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.ork"
    invalid.write_text("<openrocket><rocket><name>Vacío</name></rocket></openrocket>", encoding="utf-8")
    with pytest.raises(OpenRocketImportError, match="masas utilizables"):
        load_openrocket(invalid)
