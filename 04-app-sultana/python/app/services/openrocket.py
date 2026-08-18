"""Safe, dependency-free import of OpenRocket ``.ork`` vehicle properties.

OpenRocket files are ZIP containers whose main entry is XML.  Propulsion is
deliberately not imported: this application owns a small, audited KNSB motor
catalogue and only uses the airframe, mass and recovery properties from ORK.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET
import zipfile


class OpenRocketImportError(ValueError):
    """Raised when an ORK file cannot provide a usable physical airframe."""


@dataclass(frozen=True)
class _MassElement:
    mass_kg: float
    cg_m: float
    length_m: float
    radius_m: float
    radial_cg_m: float = 0.0


@dataclass(frozen=True)
class _FinSet:
    start_m: float
    root_chord_m: float
    tip_chord_m: float
    span_m: float
    sweep_m: float
    count: int
    body_radius_m: float

    @property
    def area_each_m2(self) -> float:
        return 0.5 * (self.root_chord_m + self.tip_chord_m) * self.span_m

    @property
    def cp_m(self) -> float:
        chord_sum = max(self.root_chord_m + self.tip_chord_m, 1e-9)
        return self.start_m + self.sweep_m / 3.0 * (
            self.root_chord_m + 2.0 * self.tip_chord_m
        ) / chord_sum + (
            self.root_chord_m + self.tip_chord_m
            - self.root_chord_m * self.tip_chord_m / chord_sum
        ) / 6.0


@dataclass(frozen=True)
class OpenRocketModel:
    name: str
    source_path: Path
    dry_mass_kg: float
    cg_dry_m: float
    inertia_dry_kg_m2: tuple[float, float, float]
    diameter_m: float
    body_length_m: float
    cp_m: float
    cd_base: float
    body_cn_alpha_per_rad: float
    canard_area_m2: float
    canard_arm_m: float
    parachute_area_m2: float | None
    parachute_cd: float | None
    parachute_line_count: int | None
    component_count: int
    ignored_motors: tuple[str, ...]

    def summary(self) -> str:
        ignored = ", ".join(self.ignored_motors) if self.ignored_motors else "ninguno declarado"
        return (
            f"{self.name} · masa seca {self.dry_mass_kg:.3f} kg · "
            f"L {self.body_length_m:.3f} m · Ø {self.diameter_m * 1000:.1f} mm · "
            f"CG {self.cg_dry_m:.3f} m · CP {self.cp_m:.3f} m. "
            f"Motor ORK ignorado: {ignored}."
        )


_COMPONENT_TAGS = {
    "nosecone", "bodytube", "transition", "innertube", "tubecoupler",
    "centeringring", "bulkhead", "engineblock", "masscomponent", "parachute",
    "streamer", "shockcord", "trapezoidfinset", "ellipticalfinset", "freeformfinset",
}
_FIN_TAGS = {"trapezoidfinset", "ellipticalfinset", "freeformfinset"}
_MAX_XML_BYTES = 20 * 1024 * 1024


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _child(element: ET.Element, name: str) -> ET.Element | None:
    wanted = name.lower()
    return next((item for item in element if _tag(item) == wanted), None)


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    container = _child(element, name)
    return [] if container is None else list(container)


def _number(element: ET.Element, name: str, default: float = 0.0) -> float:
    item = _child(element, name)
    if item is None or item.text is None:
        return default
    try:
        value = float(item.text.strip())
    except ValueError:
        return default
    return value if math.isfinite(value) else default


def _text(element: ET.Element, name: str, default: str = "") -> str:
    item = _child(element, name)
    return default if item is None or item.text is None else item.text.strip()


def _bool(element: ET.Element, name: str, default: bool = False) -> bool:
    return _text(element, name, str(default)).lower() == "true"


def _xml_from_ork(path: Path) -> bytes:
    if path.suffix.lower() != ".ork" or not path.is_file():
        raise OpenRocketImportError("Selecciona un archivo OpenRocket existente con extensión .ork")
    if path.stat().st_size > _MAX_XML_BYTES:
        raise OpenRocketImportError("El archivo OpenRocket es demasiado grande")
    if zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as archive:
                candidates = [item for item in archive.infolist() if item.filename.lower().endswith((".ork", ".xml"))]
                if not candidates:
                    raise OpenRocketImportError("El contenedor .ork no incluye el XML del cohete")
                entry = candidates[0]
                if entry.file_size > _MAX_XML_BYTES:
                    raise OpenRocketImportError("El XML interno de OpenRocket es demasiado grande")
                data = archive.read(entry)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise OpenRocketImportError(f"No se pudo abrir el contenedor OpenRocket: {exc}") from exc
    else:
        data = path.read_bytes()
    upper = data[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise OpenRocketImportError("El XML OpenRocket contiene declaraciones no permitidas")
    return data


def _component_length(element: ET.Element) -> float:
    tag = _tag(element)
    if tag in _FIN_TAGS:
        return max(_number(element, "rootchord"), 0.0)
    if tag in {"masscomponent", "parachute", "streamer"}:
        return max(_number(element, "packedlength"), _number(element, "length"), 0.0)
    return max(_number(element, "length"), 0.0)


def _component_radius(element: ET.Element, inherited_radius: float) -> float:
    tag = _tag(element)
    if tag == "nosecone":
        return max(_number(element, "aftradius"), inherited_radius)
    if tag == "transition":
        return max(_number(element, "foreradius"), _number(element, "aftradius"), inherited_radius)
    if tag == "bodytube":
        return max(_number(element, "radius"), inherited_radius)
    if tag in {"innertube", "tubecoupler", "engineblock", "centeringring", "bulkhead"}:
        return max(_number(element, "outerradius"), inherited_radius)
    if tag in {"masscomponent", "parachute", "streamer"}:
        return max(_number(element, "packedradius"), inherited_radius * 0.5)
    return inherited_radius


def _material_density(element: ET.Element) -> float:
    material = _child(element, "material")
    if material is None:
        material = _child(element, "linematerial")
    if material is None:
        return 0.0
    try:
        return max(0.0, float(material.attrib.get("density", "0")))
    except ValueError:
        return 0.0


def _estimated_mass(element: ET.Element, length: float, radius: float) -> float:
    override = _child(element, "overridemass")
    if override is not None and override.text:
        try:
            value = float(override.text)
            if math.isfinite(value) and value > 0.0:
                return value
        except ValueError:
            pass
    tag = _tag(element)
    if tag == "masscomponent":
        return max(_number(element, "mass"), 0.0)
    density = _material_density(element)
    if density <= 0.0:
        return 0.0
    thickness = max(_number(element, "thickness"), 0.0)
    if tag == "bodytube":
        inner = max(radius - thickness, 0.0)
        return density * math.pi * (radius * radius - inner * inner) * length
    if tag in {"innertube", "tubecoupler", "engineblock", "centeringring", "bulkhead"}:
        inner = max(radius - thickness, 0.0)
        return density * math.pi * (radius * radius - inner * inner) * length
    if tag == "nosecone":
        # Thin-shell cone/ogive approximation; explicit OpenRocket overrides win.
        slant = math.hypot(length, radius)
        shell_volume = math.pi * radius * slant * thickness
        return density * shell_volume
    if tag == "transition":
        fore, aft = _number(element, "foreradius"), _number(element, "aftradius")
        slant = math.hypot(length, aft - fore)
        return density * math.pi * (fore + aft) * slant * thickness
    if tag in _FIN_TAGS:
        count = max(1, int(round(_number(element, "fincount", _number(element, "instancecount", 1)))))
        root, tip, span = _number(element, "rootchord"), _number(element, "tipchord"), _number(element, "height")
        return density * 0.5 * (root + tip) * span * thickness * count
    if tag == "parachute":
        diameter = _number(element, "diameter")
        return density * math.pi * (diameter * 0.5) ** 2
    if tag == "shockcord":
        return density * length
    return 0.0


def _component_start(
    element: ET.Element, *, default: float, parent_start: float, parent_length: float, length: float,
) -> float:
    position = _child(element, "position")
    if position is None:
        position = _child(element, "axialoffset")
    if position is None or position.text is None:
        return default
    try:
        offset = float(position.text)
    except ValueError:
        return default
    method = (position.attrib.get("type") or position.attrib.get("method") or "").lower()
    if method == "absolute":
        return offset
    if method in {"top", "front"}:
        return parent_start + offset
    if method in {"bottom", "aft", "back"}:
        return parent_start + parent_length - length - offset
    if method in {"middle", "center"}:
        return parent_start + 0.5 * (parent_length - length) + offset
    return default + offset


def load_openrocket(path: str | Path) -> OpenRocketModel:
    """Extract one airframe from an ORK file without importing its motor."""
    source = Path(path).expanduser().resolve()
    try:
        document = ET.fromstring(_xml_from_ork(source))
    except ET.ParseError as exc:
        raise OpenRocketImportError(f"El XML OpenRocket no es válido: {exc}") from exc
    rocket = document if _tag(document) == "rocket" else next((item for item in document.iter() if _tag(item) == "rocket"), None)
    if rocket is None:
        raise OpenRocketImportError("El archivo no contiene un cohete OpenRocket")

    name = _text(rocket, "name", source.stem) or source.stem
    mass_elements: list[_MassElement] = []
    fins: list[_FinSet] = []
    nose_terms: list[tuple[float, float]] = []
    cd_terms: list[float] = []
    radii: list[float] = []
    ends: list[float] = []
    parachutes: list[tuple[float, float | None, int | None]] = []
    component_count = 0

    def process_sequence(
        elements: list[ET.Element], parent_start: float, parent_length: float, inherited_radius: float,
        suppress_mass: bool = False,
    ) -> float:
        nonlocal component_count
        cursor = parent_start
        furthest = parent_start
        for element in elements:
            tag = _tag(element)
            if tag in {"stage", "parallelstage", "boosters"}:
                nested = process_sequence(_children(element, "subcomponents"), cursor, parent_length, inherited_radius, suppress_mass)
                cursor = max(cursor, nested); furthest = max(furthest, nested)
                continue
            length = _component_length(element)
            start = _component_start(
                element, default=cursor, parent_start=parent_start, parent_length=parent_length, length=length,
            )
            radius = _component_radius(element, inherited_radius)
            if tag in _COMPONENT_TAGS:
                component_count += 1
                ends.append(start + length); radii.append(radius)
                override_cd = _child(element, "overridecd")
                if override_cd is not None and override_cd.text:
                    try:
                        value = float(override_cd.text)
                        if math.isfinite(value) and value >= 0.0:
                            cd_terms.append(value)
                    except ValueError:
                        pass
                mass = 0.0 if suppress_mass else _estimated_mass(element, length, radius)
                local_cg = _number(element, "overridecg", 0.5 * length)
                radial_cg = 0.0
                if tag in _FIN_TAGS:
                    radial_cg = inherited_radius + 0.5 * max(_number(element, "height"), 0.0)
                if mass > 0.0:
                    mass_elements.append(_MassElement(mass, start + local_cg, length, radius, radial_cg))
                if tag == "nosecone":
                    shape = _text(element, "shape", "").lower()
                    factor = 2.0 / 3.0 if shape == "conical" else 0.5
                    nose_terms.append((2.0, start + factor * length))
                if tag in _FIN_TAGS:
                    fins.append(_FinSet(
                        start, max(_number(element, "rootchord"), 0.0), max(_number(element, "tipchord"), 0.0),
                        max(_number(element, "height"), 0.0), max(_number(element, "sweeplength"), 0.0),
                        max(1, int(round(_number(element, "fincount", _number(element, "instancecount", 1))))), inherited_radius,
                    ))
                if tag == "parachute":
                    diameter = _number(element, "diameter")
                    cd_text = _text(element, "cd", "auto").lower()
                    try:
                        chute_cd = None if cd_text == "auto" else float(cd_text)
                    except ValueError:
                        chute_cd = None
                    lines = int(round(_number(element, "linecount"))) or None
                    if diameter > 0.0:
                        parachutes.append((math.pi * (diameter * 0.5) ** 2, chute_cd, lines))
            children = _children(element, "subcomponents")
            child_suppressed = suppress_mass or (_bool(element, "overridesubcomponentsmass") and _child(element, "overridemass") is not None)
            if children:
                process_sequence(children, start, length, radius, child_suppressed)
            cursor = max(cursor, start + length)
            furthest = max(furthest, start + length)
        return furthest

    stages = _children(rocket, "subcomponents")
    process_sequence(stages, 0.0, 0.0, 0.0)
    if not mass_elements:
        raise OpenRocketImportError("OpenRocket no contiene masas utilizables; define materiales o masas de componentes")
    dry_mass = sum(item.mass_kg for item in mass_elements)
    cg = sum(item.mass_kg * item.cg_m for item in mass_elements) / dry_mass
    body_length = max(ends, default=0.0)
    diameter = 2.0 * max(radii, default=0.0)
    if body_length <= 0.0 or diameter <= 0.0:
        raise OpenRocketImportError("OpenRocket no contiene longitud y diámetro utilizables")

    ixx = 0.0; transverse = 0.0
    for item in mass_elements:
        if item.radial_cg_m > 0.0:
            local_roll = item.mass_kg * item.radial_cg_m ** 2
        else:
            local_roll = 0.5 * item.mass_kg * item.radius_m ** 2
        local_transverse = item.mass_kg * (3.0 * item.radius_m ** 2 + item.length_m ** 2) / 12.0
        ixx += local_roll
        transverse += local_transverse + item.mass_kg * (item.cg_m - cg) ** 2
    floor = max(dry_mass * diameter * diameter * 1e-5, 1e-8)
    inertia = (max(ixx, floor), max(transverse, floor), max(transverse, floor))

    reference_radius = 0.5 * diameter
    cp_terms = list(nose_terms)
    for fin in fins:
        if fin.span_m <= 0.0 or fin.root_chord_m + fin.tip_chord_m <= 0.0:
            continue
        mid_chord_sweep = fin.sweep_m + 0.5 * (fin.tip_chord_m - fin.root_chord_m)
        mid_chord = math.hypot(fin.span_m, mid_chord_sweep)
        denominator = 1.0 + math.sqrt(1.0 + (2.0 * mid_chord / max(fin.root_chord_m + fin.tip_chord_m, 1e-9)) ** 2)
        cna = 4.0 * fin.count * (fin.span_m / max(diameter, 1e-9)) ** 2 / denominator
        cna *= 1.0 + fin.body_radius_m / max(fin.body_radius_m + fin.span_m, 1e-9)
        cp_terms.append((max(cna, 0.0), fin.cp_m))
    total_cna = sum(term[0] for term in cp_terms)
    cp = sum(cna * position for cna, position in cp_terms) / total_cna if total_cna > 0.0 else 0.75 * body_length
    cp = min(max(cp, 0.0), body_length)
    cd_base = sum(cd_terms) if cd_terms and sum(cd_terms) > 0.0 else 0.55
    forward_fin = min(fins, key=lambda item: item.start_m, default=None)
    canard_area = forward_fin.area_each_m2 if forward_fin and forward_fin.area_each_m2 > 0.0 else math.pi * reference_radius ** 2 * 0.6
    canard_arm = abs((forward_fin.cp_m if forward_fin else cp) - cg)
    chute = max(parachutes, key=lambda item: item[0], default=None)
    motors = []
    for motor in (item for item in rocket.iter() if _tag(item) == "motor"):
        label = " ".join(part for part in (_text(motor, "manufacturer"), _text(motor, "designation")) if part)
        if label and label not in motors:
            motors.append(label)
    return OpenRocketModel(
        name=name, source_path=source, dry_mass_kg=dry_mass, cg_dry_m=cg,
        inertia_dry_kg_m2=inertia, diameter_m=diameter, body_length_m=body_length,
        cp_m=cp, cd_base=max(0.05, min(cd_base, 5.0)),
        body_cn_alpha_per_rad=max(2.0, total_cna), canard_area_m2=max(canard_area, 1e-6),
        canard_arm_m=max(canard_arm, diameter * 0.25),
        parachute_area_m2=None if chute is None else chute[0],
        parachute_cd=None if chute is None else (chute[1] or 1.5),
        parachute_line_count=None if chute is None else chute[2],
        component_count=component_count, ignored_motors=tuple(motors),
    )


def apply_openrocket_model(scenario: object, model: OpenRocketModel) -> object:
    """Return a scenario using ORK airframe properties and the local KNSB motor."""
    from .config_loader import LoadedScenario, selected_motor

    vehicle = copy.deepcopy(scenario.vehicle)
    environment = copy.deepcopy(scenario.environment)
    motor = selected_motor(scenario)
    if motor is None:
        raise OpenRocketImportError("El escenario no contiene uno de los tres motores KNSB permitidos")
    propellant_mass = float(motor["propellant_mass_kg"])
    burn_time = float(motor["burn_time_s"])
    grain_length = float(motor.get("grain_length_m", 0.1))
    propellant_cg = min(max(model.body_length_m - 0.5 * grain_length, 0.0), model.body_length_m)
    wet_mass = model.dry_mass_kg + propellant_mass
    wet_cg = (model.dry_mass_kg * model.cg_dry_m + propellant_mass * propellant_cg) / wet_mass
    prop_radius = max(model.diameter_m * 0.30, 1e-4)
    prop_ixx = 0.5 * propellant_mass * prop_radius ** 2
    prop_iyy = propellant_mass * (3.0 * prop_radius ** 2 + grain_length ** 2) / 12.0
    dry_ixx, dry_iyy, dry_izz = model.inertia_dry_kg_m2
    shift = model.dry_mass_kg * (model.cg_dry_m - wet_cg) ** 2
    wet_inertia = (
        dry_ixx + prop_ixx,
        dry_iyy + shift + prop_iyy + propellant_mass * (propellant_cg - wet_cg) ** 2,
        dry_izz + shift + prop_iyy + propellant_mass * (propellant_cg - wet_cg) ** 2,
    )
    vehicle["vehicle_id"] = f"openrocket-{re.sub(r'[^a-z0-9]+', '-', model.name.lower()).strip('-')[:48] or 'import'}"
    vehicle["geometry"].update({
        "diameter_m": model.diameter_m,
        "reference_area_m2": math.pi * (0.5 * model.diameter_m) ** 2,
        "body_length_m": model.body_length_m,
        "cp_m": model.cp_m,
    })
    vehicle["mass"].update({
        "dry_mass_kg": model.dry_mass_kg,
        "propellant_mass_kg": propellant_mass,
        "cg_dry_m": model.cg_dry_m,
        "cg_wet_m": wet_cg,
        "inertia_dry_kg_m2": list(model.inertia_dry_kg_m2),
        "inertia_wet_kg_m2": list(wet_inertia),
        "mass_curve_inline": [
            [0.0, propellant_mass, wet_cg, *wet_inertia],
            [burn_time, 0.0, model.cg_dry_m, *model.inertia_dry_kg_m2],
        ],
    })
    vehicle["mass"].pop("mass_curve_csv", None)
    vehicle["aerodynamics"].update({
        "cd_base": model.cd_base,
        "body_cn_alpha_per_rad": model.body_cn_alpha_per_rad,
        "canard_area_m2": model.canard_area_m2,
        "canard_arm_m": model.canard_arm_m,
        "aero_curve_inline": [
            [0.0, model.cd_base, model.body_cn_alpha_per_rad],
            [0.3, model.cd_base, model.body_cn_alpha_per_rad],
            [0.8, model.cd_base * 1.08, model.body_cn_alpha_per_rad * 0.98],
            [1.2, model.cd_base * 1.18, model.body_cn_alpha_per_rad * 0.92],
            [2.0, model.cd_base * 1.05, model.body_cn_alpha_per_rad * 0.85],
        ],
    })
    vehicle["aerodynamics"].pop("aero_table_csv", None)
    if model.parachute_area_m2 is not None:
        vehicle["recovery"]["parachute_area_m2"] = model.parachute_area_m2
        vehicle["recovery"]["parachute_cd"] = model.parachute_cd or 1.5
    vehicle.setdefault("physical_inventory", {})["openrocket_import"] = {
        "source": str(model.source_path), "name": model.name,
        "components": model.component_count, "ignored_motors": list(model.ignored_motors),
        "status": "airframe imported; propulsion restricted to local KNSB catalogue",
    }
    if model.parachute_line_count is not None:
        vehicle["physical_inventory"].setdefault("parachute", {})["lines"] = model.parachute_line_count
    vehicle["calibration"]["mass_properties"] = False
    vehicle["calibration"]["aerodynamics"] = False
    return LoadedScenario(
        vehicle, environment, scenario.vehicle_path, scenario.environment_path,
        scenario.parameter_registry_path, scenario.parameter_registry,
    )
