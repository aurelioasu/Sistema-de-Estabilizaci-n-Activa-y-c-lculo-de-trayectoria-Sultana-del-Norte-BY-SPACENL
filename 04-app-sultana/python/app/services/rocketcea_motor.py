"""Traceable preliminary RocketCEA reporting for the declared KNSB motor.

This module is deliberately limited to equilibrium chamber/nozzle performance.
It does not create a flight thrust curve: grain port, throat diameter, nozzle
geometry and a static firing are still required for that calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PSI_TO_PA = 6_894.757293168
STANDARD_AMBIENT_PRESSURE_PSI = 14.6959


@dataclass(frozen=True)
class RocketCeaReport:
    motor_id: str
    chamber_pressure_pa: float
    expansion_ratio: float
    sorbitol_mass_fraction: float
    kno3_mass_fraction: float
    fe2o3_mass_fraction: float
    ideal_isp_s: float
    cstar_m_s: float
    chamber_temperature_k: float
    sea_level_cf: float
    status: str


def _catalogue_motor(scenario: object, motor_id: str | None = None) -> dict[str, Any]:
    propulsion = scenario.vehicle["propulsion"]
    wanted = motor_id or str(propulsion.get("selected_motor_id", ""))
    for motor in propulsion.get("motors", []):
        if str(motor.get("id", "")) == wanted:
            return motor
    raise ValueError(f"Motor RocketCEA no configurado: {wanted}")


def _card(propellant_mass_kg: float, catalyst_mass_g: float) -> tuple[str, tuple[float, float, float]]:
    """Build the CEA reactant card from the user's mass composition.

    Sorbitol's condensed-phase heat of formation is -1353.7 kJ/mol (NIST
    SRD 69), or -323.26 cal/mol.  KNO3(a) is a NASA CEA thermo species.
    Fe2O3 is represented as the declared catalyst/inert constituent.
    """
    total_g = max(float(propellant_mass_kg) * 1_000.0, 1e-9)
    fe2o3_g = min(max(float(catalyst_mass_g), 0.0), total_g * 0.99)
    mixture_g = total_g - fe2o3_g
    sorbitol = 0.70 * mixture_g / total_g
    kno3 = 0.30 * mixture_g / total_g
    fe2o3 = fe2o3_g / total_g
    card = f"""
name C6H14O6(S) C 6 H 14 O 6 wt%={100.0 * sorbitol:.9g}
h,cal=-323.26 t(k)=298.15 rho=1.489
name KNO3(a) K 1 N 1 O 3 wt%={100.0 * kno3:.9g}
h,cal=-118069 t(k)=298.15 rho=2.109
name Fe2O3(S) Fe 2 O 3 wt%={100.0 * fe2o3:.9g}
h,cal=-197000 t(k)=298.15 rho=5.24
"""
    return card, (sorbitol, kno3, fe2o3)


def rocketcea_motor_report(scenario: object, motor_id: str | None = None) -> RocketCeaReport:
    """Evaluate one catalogue motor using the declared preliminary CEA inputs."""
    motor = _catalogue_motor(scenario, motor_id)
    model = scenario.vehicle["propulsion"].get("rocketcea", {})
    chamber_pressure_pa = float(model.get("chamber_pressure_pa", 500.0 * PSI_TO_PA))
    expansion_ratio = float(model.get("expansion_ratio", 5.0))
    catalyst_mass_g = float(model.get("catalyst_mass_g", 1.0))
    card, fractions = _card(float(motor["propellant_mass_kg"]), catalyst_mass_g)
    try:
        from rocketcea.cea_obj import CEA_Obj, add_new_propellant
    except (ImportError, OSError) as exc:  # Frozen installs may omit propulsion data files.
        raise RuntimeError("RocketCEA no está instalado; instala el extra de propulsión.") from exc

    # A unique card name prevents a selector change from reusing another
    # motor's catalyst fraction in RocketCEA's process-global card registry.
    name = f"CANSAT_{motor['id']}_{int(round(catalyst_mass_g * 1_000))}mg"
    add_new_propellant(name, card)
    cea = CEA_Obj(propName=name)
    chamber_pressure_psi = chamber_pressure_pa / PSI_TO_PA
    ideal_isp = float(cea.get_Isp(Pc=chamber_pressure_psi, eps=expansion_ratio))
    cstar_m_s = float(cea.get_Cstar(Pc=chamber_pressure_psi)) * 0.3048
    temperature_k = float(cea.get_Tcomb(Pc=chamber_pressure_psi))
    _, sea_level_cf, _ = cea.get_PambCf(Pc=chamber_pressure_psi, eps=expansion_ratio, Pamb=STANDARD_AMBIENT_PRESSURE_PSI)
    return RocketCeaReport(
        motor_id=str(motor["id"]), chamber_pressure_pa=chamber_pressure_pa, expansion_ratio=expansion_ratio,
        sorbitol_mass_fraction=fractions[0], kno3_mass_fraction=fractions[1], fe2o3_mass_fraction=fractions[2],
        ideal_isp_s=ideal_isp, cstar_m_s=cstar_m_s, chamber_temperature_k=temperature_k,
        sea_level_cf=float(sea_level_cf),
        status="CEA de equilibrio preliminar; no sustituye curva de empuje medida ni diseño estructural.",
    )
