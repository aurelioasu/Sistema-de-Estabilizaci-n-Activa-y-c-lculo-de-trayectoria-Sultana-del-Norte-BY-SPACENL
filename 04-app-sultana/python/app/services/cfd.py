"""OpenFOAM/Docker boundary used by the CFD laboratory.

The UI never constructs shell strings.  Every Docker command is an argument list,
and every case remains below ``out/cfd`` so it is safe to remove independently.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import queue
import uuid
import zipfile
from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from app.services.cfd_geometry import NACA_MODEL_STL, PHYSICAL_BODY_LENGTH_M, prepare_snapshot_surface


OPENFOAM_IMAGE = "opencfd/openfoam-default:2512"
# The proxy is used only when Docker Hub's CDN is unavailable.  Its result is
# retagged only after matching this published OpenFOAM v2512 index digest.
OPENFOAM_PROXY_IMAGE = "dockerproxy.net/opencfd/openfoam-default:2512"
OPENFOAM_2512_DIGEST = "sha256:33fb575aa9980d2bc42fd58c75ae698c489293ba30c991380fe3f899c622f319"
SUBPROCESS_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
# The visual model stays untouched; CFD uses the watertight Blender-repaired copy.
PRIMARY_MODEL_STL = NACA_MODEL_STL


@dataclass(frozen=True)
class DockerStatus:
    available: bool
    message: str
    image_present: bool = False
    execution_backend: str = "CPU de respaldo"


@dataclass(frozen=True)
class ExecutionBackend:
    label: str
    uses_gpu: bool
    detail: str


def select_execution_backend(*, cuda_solver_available: bool, gpu_runtime_available: bool, mpi_cores: int) -> ExecutionBackend:
    """Never claim CUDA merely because Docker can expose an NVIDIA device."""
    if cuda_solver_available and gpu_runtime_available:
        return ExecutionBackend("GPU", True, "Solver CFD CUDA compatible verificado y GPU accesible en el contenedor.")
    if mpi_cores > 1:
        return ExecutionBackend("CPU paralelo", False, "OpenFOAM v2512 usa MPI; esta imagen no incluye un solver CUDA compatible con pimpleFoam y malla dinámica.")
    return ExecutionBackend("CPU de respaldo", False, "OpenFOAM se ejecutará en un único proceso CPU.")


class CfdRunFailure(RuntimeError):
    """A solver failure that preserves the actionable phase and log location."""

    def __init__(self, phase: str, detail: str, log_path: Path, last_line: str = "") -> None:
        self.phase = phase
        self.log_path = log_path
        self.last_line = last_line
        lowered = detail.lower()
        self.status = "cancelled" if "cancel" in lowered else "timeout" if "timeout" in lowered else "failed"
        tail = f" Última línea: {last_line[:240]}." if last_line else ""
        super().__init__(f"Fase {phase}: {detail}.{tail} Log: {log_path}")


@dataclass(frozen=True)
class CfdCaseRequest:
    case_name: str
    mode: str  # snapshot | steady | transient
    speed_mps: float
    alpha_deg: float
    beta_deg: float
    rain_rate_mm_h: float
    canard_deg: tuple[float, float, float, float]
    use_multiphase: bool
    # Leave capacity for the desktop UI and Docker/WSL while using the CPU
    # available on a typical development laptop.  The solver is launched with
    # MPI, so this is an actual parallelism setting rather than only a Docker
    # quota.
    cores: int = max(2, min(6, (os.cpu_count() or 4) - 2))
    memory_gb: int = 6
    canard_schedule: tuple[tuple[float, float, float, float, float], ...] = ()
    # Airspeed magnitude from the 6-DoF solution. A transient case uses this
    # table at the inlet instead of confusing ambient wind with vehicle speed.
    speed_schedule: tuple[tuple[float, float], ...] = ()
    # Body-axis relative air velocity from the 6-DoF solution. When available
    # it takes precedence over ``speed_schedule`` so crosswind and attitude
    # reach the OpenFOAM inlet rather than becoming display-only metadata.
    inlet_velocity_schedule: tuple[tuple[float, float, float, float], ...] = ()
    wind_enu_mps: tuple[float, float, float] = (0.0, 0.0, 0.0)
    weather_source: str = ""
    temperature_k: float = 288.15
    pressure_pa: float = 101325.0
    humidity_ratio: float = 0.0
    turbulence_intensity_mps: float = 0.0
    transient_end_time_s: float = 1.5
    transient_write_interval_s: float = 0.05
    execution_scope: str = "full_flight"  # full_flight | motion_interval | steady
    source_time_start_s: float = 0.0
    source_time_end_s: float = 0.0
    # Optional user-provided CAD surface. It is copied into each unique case,
    # so later edits to the source file cannot change an audited result.
    rocket_stl_path: str = ""
    snapshot_source_time_s: float | None = None
    snapshot_reason: str = ""
    wall_time_limit_s: int = 1800
    # Recovery is not part of the closed rocket STL.  Preserve the inflated
    # CdS from the 6-DoF state so its load can be reported separately instead
    # of silently pretending that OpenFOAM resolved a missing parachute.
    recovery_cds_m2: float = 0.0
    # Geometry is centred at x=0 while the calibrated CG is measured from the
    # nose.  This body-axis coordinate is the reference used for moments and
    # for the arrows in the viewport.
    center_of_gravity_body_m: tuple[float, float, float] = (0.0, 0.0, 0.0)


# These boxes are in the tunnel coordinates *after* the supplied OBJ has been
# centred, scaled and rotated.  They bound the four articulated OBJ groups
# (10, 13, 12, 11) with a small mesh-motion clearance.  Keeping a gap around
# the fuselage axes is intentional: a cell may belong to one motion zone only.
# The old broad boxes overlapped by design and made the four body motions
# physically ambiguous.
CANARD_ZONE_BOXES: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...] = (
    ((0.020, -0.020, -0.096), (0.151, 0.020, -0.020)),  # C1, OBJ group 10
    ((0.010, 0.020, -0.020), (0.140, 0.096, 0.020)),   # C2, OBJ group 13
    ((0.020, -0.020, 0.020), (0.151, 0.020, 0.096)),   # C3, OBJ group 12
    ((0.010, -0.096, -0.020), (0.140, -0.020, 0.020)), # C4, OBJ group 11
)


def _boxes_overlap(
    left: tuple[tuple[float, float, float], tuple[float, float, float]],
    right: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> bool:
    """Whether two axis-aligned cell-zone boxes overlap by positive volume."""
    return all(max(left[0][axis], right[0][axis]) < min(left[1][axis], right[1][axis]) for axis in range(3))


def validate_canard_zone_boxes(
    boxes: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...] = CANARD_ZONE_BOXES,
) -> None:
    """Reject invalid or overlapping moving-cell regions before a case exists."""
    if len(boxes) != 4:
        raise ValueError("Se requieren exactamente cuatro zonas de canards")
    for index, (low, high) in enumerate(boxes, start=1):
        if any(low[axis] >= high[axis] for axis in range(3)):
            raise ValueError(f"La zona del canard C{index} no tiene volumen positivo")
    for left_index, left in enumerate(boxes, start=1):
        for right_index, right in enumerate(boxes[left_index:], start=left_index + 1):
            if _boxes_overlap(left, right):
                raise ValueError(f"Las zonas de canards C{left_index} y C{right_index} se superponen")


def _validate_request(request: CfdCaseRequest) -> None:
    if request.mode not in {"snapshot", "steady", "transient"}:
        raise ValueError("El modo CFD debe ser snapshot, estacionario o transitorio")
    if int(request.cores) < 1:
        raise ValueError("El número de subdominios MPI debe ser al menos 1")
    if int(request.memory_gb) < 1:
        raise ValueError("La memoria asignada a OpenFOAM debe ser al menos 1 GB")
    if request.mode == "transient" and float(request.transient_end_time_s) <= 0.0:
        raise ValueError("La duración del caso transitorio debe ser positiva")
    if request.mode == "transient":
        validate_canard_zone_boxes()
    if not 1 <= int(request.wall_time_limit_s) <= 1800:
        raise ValueError("Cada caso CFD debe tener un límite de pared entre 1 y 1800 s")


@dataclass(frozen=True)
class CanardMotionInterval:
    """A bounded, independent CFD window extracted from the PID history."""

    start_s: float
    end_s: float
    canard_indices: tuple[int, ...]

    @property
    def label(self) -> str:
        canards = ", ".join(f"C{index + 1}" for index in self.canard_indices)
        return f"{self.start_s:.2f}–{self.end_s:.2f} s · {canards}"


@dataclass(frozen=True)
class SnapshotSelection:
    source_time_s: float
    reason: str
    index: int


def select_representative_snapshots(samples: tuple[dict[str, object], ...]) -> tuple[SnapshotSelection, ...]:
    """Choose three distinct 6-DoF states for bounded local RANS cases."""
    usable: list[tuple[int, dict[str, object], float, float, float]] = []
    for index, sample in enumerate(samples):
        try:
            time_s = float(sample["time_s"]); q = max(0.0, float(sample.get("dynamic_pressure_pa", 0.0)))
            canards = tuple(float(value) for value in sample.get("canards", (0.0, 0.0, 0.0, 0.0)))
            lateral = abs(float(sample.get("lateral_velocity_mps", 0.0)))
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(time_s) or not math.isfinite(q) or len(canards) != 4:
            continue
        usable.append((index, sample, q, q * sum(abs(value) for value in canards), q * lateral * lateral))
    if len(usable) < 3:
        raise ValueError("Se requieren al menos tres muestras 6-DoF completas para seleccionar snapshots")
    criteria = (("máxima presión dinámica", 2), ("máximo esfuerzo combinado de canards × presión dinámica", 3),
                ("máxima presión dinámica lateral", 4))
    chosen: list[SnapshotSelection] = []; used: set[int] = set()
    for reason, score_index in criteria:
        for row in sorted(usable, key=lambda item: item[score_index], reverse=True):
            if row[0] not in used:
                used.add(row[0]); chosen.append(SnapshotSelection(float(row[1]["time_s"]), reason, row[0])); break
    return tuple(chosen)


def select_flight_phase_snapshots(samples: tuple[dict[str, object], ...]) -> tuple[SnapshotSelection, ...]:
    """Return the five static CFD states that describe one recovered flight.

    The launch and landing samples are authoritative endpoints.  The two
    intermediate states are nearest real telemetry samples to the midpoint of
    their respective flight phases, never interpolated states.
    """
    if len(samples) < 5:
        raise ValueError("La trayectoria 6-DoF debe contener al menos cinco muestras")
    times: list[float] = []
    for sample in samples:
        try:
            time_s = float(sample["time_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("La trayectoria 6-DoF no contiene tiempos de telemetría válidos") from exc
        if not math.isfinite(time_s):
            raise ValueError("La trayectoria 6-DoF contiene un tiempo no finito")
        times.append(time_s)
    if any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("Los tiempos de la trayectoria 6-DoF deben ser estrictamente crecientes")
    parachute_index = next((index for index, sample in enumerate(samples) if bool(sample.get("parachute_deployed", False))), None)
    if parachute_index is None:
        raise ValueError("La trayectoria no desplegó paracaídas; ejecuta una simulación 6-DoF con recuperación activa")
    landing_index = len(samples) - 1
    if parachute_index < 2 or parachute_index > landing_index - 2:
        raise ValueError("No hay muestras suficientes antes y después del despliegue de paracaídas para formar cinco snapshots")

    def closest_index(target_time: float, candidates: range) -> int:
        return min(candidates, key=lambda index: abs(times[index] - target_time))

    ascent_index = closest_index(0.5 * (times[0] + times[parachute_index]), range(1, parachute_index))
    descent_index = closest_index(0.5 * (times[parachute_index] + times[landing_index]), range(parachute_index + 1, landing_index))
    indices = (0, ascent_index, parachute_index, descent_index, landing_index)
    if len(set(indices)) != 5:
        raise ValueError("No se pudieron obtener cinco momentos de vuelo distintos")
    reasons = (
        "despegue", "intermedio entre despegue y paracaídas", "despliegue de paracaídas",
        "intermedio entre paracaídas y aterrizaje", "aterrizaje",
    )
    return tuple(SnapshotSelection(times[index], reason, index) for index, reason in zip(indices, reasons))


@dataclass(frozen=True)
class CfdFrame:
    """One auditable OpenFOAM state used by the detailed-CFD player.

    A frame is deliberately a discrete solver output.  The UI never invents
    intermediate flow fields between these states.
    """

    time_s: float
    canard_deg: tuple[float, float, float, float]
    vtk_path: Path | None = None
    surface_vtk_path: Path | None = None
    force_n: tuple[float, float, float] = (0.0, 0.0, 0.0)
    moment_nm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    pressure_force_n: tuple[float, float, float] | None = None
    viscous_force_n: tuple[float, float, float] | None = None
    inlet_velocity_body_mps: tuple[float, float, float] = (1.0, 0.0, 0.0)
    recovery_force_n: tuple[float, float, float] = (0.0, 0.0, 0.0)
    center_of_gravity_body_m: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def system_force_n(self) -> tuple[float, float, float]:
        return tuple(self.force_n[index] + self.recovery_force_n[index] for index in range(3))


@dataclass(frozen=True)
class CfdResult:
    case_dir: Path
    force_n: tuple[float, float, float]
    moment_nm: tuple[float, float, float]
    pressure_pa: float
    log_path: Path
    vtk_path: Path | None = None
    backend: str = "OpenFOAM"
    execution_backend: str = "CPU paralelo"
    execution_scope: str = "full_flight"
    note: str = ""
    available_fields: tuple[str, ...] = ()
    air_density_kg_m3: float = 1.225
    pressure_force_n: tuple[float, float, float] | None = None
    viscous_force_n: tuple[float, float, float] | None = None
    frames: tuple[CfdFrame, ...] = ()
    status: str = "completed"
    wall_time_s: float = 0.0
    converged: bool = False
    geometry_hash: str = ""
    snapshot_source_time_s: float | None = None
    snapshot_reason: str = ""
    inlet_velocity_body_mps: tuple[float, float, float] = (1.0, 0.0, 0.0)
    recovery_force_n: tuple[float, float, float] = (0.0, 0.0, 0.0)
    center_of_gravity_body_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    convergence_reason: str = ""
    force_stability_ratio: float | None = None
    residual_ratio: float | None = None

    @property
    def is_openfoam(self) -> bool:
        """True only for a completed case whose data came from OpenFOAM."""
        return self.backend == "OpenFOAM"

    @property
    def system_force_n(self) -> tuple[float, float, float]:
        return tuple(self.force_n[index] + self.recovery_force_n[index] for index in range(3))


def docker_status() -> DockerStatus:
    executable = shutil.which("docker")
    if not executable:
        return DockerStatus(False, "Docker Desktop no está instalado.")
    try:
        info = subprocess.run([executable, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=8, creationflags=SUBPROCESS_CREATION_FLAGS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DockerStatus(False, f"No se pudo comprobar Docker: {exc}")
    if info.returncode:
        return DockerStatus(False, "Docker Desktop está instalado, pero su motor no está iniciado.")
    images = subprocess.run([executable, "image", "inspect", OPENFOAM_IMAGE], capture_output=True, text=True, timeout=8, creationflags=SUBPROCESS_CREATION_FLAGS)
    backend = select_execution_backend(cuda_solver_available=False, gpu_runtime_available=False, mpi_cores=CfdCaseRequest.__dataclass_fields__["cores"].default)
    return DockerStatus(True, f"Docker listo (motor {info.stdout.strip()}).", images.returncode == 0, backend.label)


def docker_command(case_dir: Path, request: CfdCaseRequest, *, pull: bool = False, container_name: str | None = None) -> list[str]:
    docker = shutil.which("docker") or "docker"
    if pull:
        return [docker, "pull", OPENFOAM_IMAGE]
    mount = f"{case_dir.resolve()}:/case"
    # The official OpenFOAM image entrypoint resets its shell to /root, which
    # overrides Docker's working directory. Enter the bind mount explicitly so
    # the audited case is always the one being executed on Windows/Docker.
    runner = "cd /case && ./Allrun"
    cores = max(1, int(request.cores))
    command = [docker, "run", "--rm", "--cpus", str(cores), "--memory", f"{request.memory_gb}g", "-e", f"FOAM_NPROCS={cores}"]
    if container_name:
        command.extend(("--name", container_name))
    return command + ["-v", mount, "-w", "/case", OPENFOAM_IMAGE, "bash", "-lc", runner]


def _motion_table(schedule: tuple[tuple[float, float, float, float, float], ...], canard_index: int) -> str:
    """OpenFOAM tabulated6DoFMotion table for one physical canard hinge."""
    rows = _strictly_increasing_time_rows(sorted(schedule, key=lambda row: row[0])) if schedule else (
        (0.0, 0.0, 0.0, 0.0, 0.0), (1.5, 0.0, 0.0, 0.0, 0.0),
    )
    # The OBJ is transformed from source +Z to tunnel +X.  These are the
    # transformed radial servo axes used by CfdViewport, not display axes.
    axis_signs = ((2, -1.0), (1, 1.0), (2, 1.0), (1, -1.0))
    axis, sign = axis_signs[canard_index]
    lines = [str(len(rows)), "("]
    for row in rows:
        # The desktop PID and its CSV are intentionally shown in degrees, but
        # OpenFOAM's tabulated6DoFMotion rotation vector is in radians. Passing
        # 15 directly requested an 859° rotation and collapsed the adaptive
        # time step instead of modelling a 15° canard command.
        angle = sign * math.radians(float(row[canard_index + 1]))
        rotation = [0.0, 0.0, 0.0]; rotation[axis] = angle
        lines.append(f"({float(row[0]):.9g} ((0 0 0) ({rotation[0]:.9g} {rotation[1]:.9g} {rotation[2]:.9g})))")
    return "\n".join((*lines, ")", ""))


def moist_air_density(temperature_k: float, pressure_pa: float, relative_humidity: float) -> float:
    """Return moist-air density from the UI weather fields.

    ``humidity_ratio`` is a historical field name in the scenario format, but
    its UI and weather provider both supply *relative humidity* in [0, 1].
    Treating (for example) 0.79 as a mass mixing ratio reduced density by a
    non-physical 32 %.  Use partial pressures instead.
    """
    temperature = max(float(temperature_k), 150.0)
    pressure = max(float(pressure_pa), 1_000.0)
    rh = min(1.0, max(0.0, float(relative_humidity)))
    celsius = temperature - 273.15
    # Buck equation, adequate for the near-surface atmospheric conditions
    # accepted by the laboratory UI.
    saturation_pressure = 611.21 * math.exp((18.678 - celsius / 234.5) * (celsius / (257.14 + celsius)))
    vapour_pressure = min(rh * saturation_pressure, pressure * 0.99)
    dry_pressure = pressure - vapour_pressure
    return max(0.2, dry_pressure / (287.05 * temperature) + vapour_pressure / (461.495 * temperature))


def detect_canard_motion_intervals(
    schedule: tuple[tuple[float, float, float, float, float], ...], *,
    threshold_deg: float = 0.05, merge_gap_s: float = 0.25, margin_s: float = 0.30,
) -> tuple[CanardMotionInterval, ...]:
    """Find and group meaningful PID canard movements into CFD-ready windows."""
    ordered = tuple(sorted(schedule, key=lambda row: row[0]))
    if len(ordered) < 2:
        return ()
    raw: list[CanardMotionInterval] = []
    threshold = max(0.0, float(threshold_deg))
    for previous, current in zip(ordered, ordered[1:]):
        moved = tuple(index for index in range(4) if abs(current[index + 1] - previous[index + 1]) >= threshold)
        if moved:
            raw.append(CanardMotionInterval(float(previous[0]), float(current[0]), moved))
    if not raw:
        return ()
    grouped: list[CanardMotionInterval] = []
    for interval in raw:
        if grouped and interval.start_s <= grouped[-1].end_s + merge_gap_s:
            previous = grouped[-1]
            grouped[-1] = CanardMotionInterval(
                previous.start_s, max(previous.end_s, interval.end_s),
                tuple(sorted(set(previous.canard_indices).union(interval.canard_indices))),
            )
        else:
            grouped.append(interval)
    first_time, last_time = float(ordered[0][0]), float(ordered[-1][0])
    padded: list[CanardMotionInterval] = []
    for interval in grouped:
        expanded = CanardMotionInterval(max(first_time, interval.start_s - margin_s), min(last_time, interval.end_s + margin_s), interval.canard_indices)
        if padded and expanded.start_s <= padded[-1].end_s:
            previous = padded[-1]
            padded[-1] = CanardMotionInterval(
                previous.start_s, max(previous.end_s, expanded.end_s),
                tuple(sorted(set(previous.canard_indices).union(expanded.canard_indices))),
            )
        else:
            padded.append(expanded)
    return tuple(padded)


def _strictly_increasing_time_rows(rows: list[tuple[object, ...]], *, tolerance_s: float = 1e-9) -> tuple[tuple[object, ...], ...]:
    """Normalise tabulated-function keys required by OpenFOAM.

    Floating-point endpoint interpolation can otherwise append a timestamp
    that is equal to the final sampled row at display precision (for example,
    two ``11.701`` rows).  OpenFOAM Function1 tables require strictly
    increasing keys, so retain the explicit endpoint and replace its near-
    duplicate rather than shortening the requested interval.
    """
    normalised: list[tuple[object, ...]] = []
    for row in rows:
        current = (float(row[0]), *row[1:])
        if normalised and current[0] <= normalised[-1][0] + tolerance_s:
            if abs(current[0] - normalised[-1][0]) <= tolerance_s:
                normalised[-1] = current
            continue
        normalised.append(current)
    return tuple(normalised)


def schedule_for_motion_interval(
    schedule: tuple[tuple[float, float, float, float, float], ...], interval: CanardMotionInterval,
) -> tuple[tuple[float, float, float, float, float], ...]:
    """Extract one interval and shift it to local CFD time zero."""
    ordered = tuple(sorted(schedule, key=lambda row: row[0]))
    if not ordered:
        return ()
    start, end = interval.start_s, interval.end_s
    def values_at(time_s: float) -> tuple[float, float, float, float]:
        if time_s <= ordered[0][0]:
            return tuple(float(value) for value in ordered[0][1:])
        if time_s >= ordered[-1][0]:
            return tuple(float(value) for value in ordered[-1][1:])
        for low, high in zip(ordered, ordered[1:]):
            if low[0] <= time_s <= high[0]:
                ratio = (time_s - low[0]) / max(high[0] - low[0], 1e-12)
                return tuple(float(low[index] + ratio * (high[index] - low[index])) for index in range(1, 5))
        return tuple(float(value) for value in ordered[-1][1:])
    selected = [(start, *values_at(start))]
    selected.extend(row for row in ordered if start < row[0] < end)
    selected.append((end, *values_at(end)))
    return _strictly_increasing_time_rows([
        (float(row[0] - start), *(float(value) for value in row[1:])) for row in selected
    ])


def speed_schedule_for_motion_interval(
    schedule: tuple[tuple[float, float], ...], interval: CanardMotionInterval,
) -> tuple[tuple[float, float], ...]:
    """Extract an airspeed history for one motion case and shift it to zero.

    The vehicle's airspeed and the ambient wind are different quantities.  The
    former belongs at the CFD inlet; the latter remains meteorological context
    for the coupled 6-DoF trajectory.  Endpoint interpolation keeps a local
    canard case continuous even when its requested interval starts between
    6-DoF samples.
    """
    ordered = tuple(sorted((float(time_s), max(0.0, float(speed_mps))) for time_s, speed_mps in schedule))
    if not ordered:
        return ()
    start, end = float(interval.start_s), float(interval.end_s)

    def value_at(time_s: float) -> float:
        if time_s <= ordered[0][0]:
            return ordered[0][1]
        if time_s >= ordered[-1][0]:
            return ordered[-1][1]
        for low, high in zip(ordered, ordered[1:]):
            if low[0] <= time_s <= high[0]:
                fraction = (time_s - low[0]) / max(high[0] - low[0], 1e-12)
                return low[1] + fraction * (high[1] - low[1])
        return ordered[-1][1]

    selected = [(start, value_at(start))]
    selected.extend(row for row in ordered if start < row[0] < end)
    selected.append((end, value_at(end)))
    return _strictly_increasing_time_rows([(time_s - start, speed_mps) for time_s, speed_mps in selected])


def vector_schedule_for_motion_interval(
    schedule: tuple[tuple[float, float, float, float], ...], interval: CanardMotionInterval,
) -> tuple[tuple[float, float, float, float], ...]:
    """Interpolate a body-axis inlet-velocity history onto a local CFD window."""
    ordered = tuple(sorted((float(row[0]), *(float(value) for value in row[1:4])) for row in schedule))
    if not ordered:
        return ()
    start, end = float(interval.start_s), float(interval.end_s)

    def value_at(time_s: float) -> tuple[float, float, float]:
        if time_s <= ordered[0][0]: return ordered[0][1:]
        if time_s >= ordered[-1][0]: return ordered[-1][1:]
        for low, high in zip(ordered, ordered[1:]):
            if low[0] <= time_s <= high[0]:
                fraction = (time_s - low[0]) / max(high[0] - low[0], 1e-12)
                return tuple(low[index] + fraction * (high[index] - low[index]) for index in range(1, 4))
        return ordered[-1][1:]

    selected = [(start, *value_at(start))]
    selected.extend(row for row in ordered if start < row[0] < end)
    selected.append((end, *value_at(end)))
    return _strictly_increasing_time_rows([(row[0] - start, *row[1:]) for row in selected])


def _foam_case_files(request: CfdCaseRequest) -> dict[str, str]:
    # These dictionaries intentionally form a small, inspectable OpenFOAM case.
    # The transient template uses dynamic overset/multiphase solver names available
    # in the selected OpenCFD image; users retain the generated case for audit.
    # A single snappyHexMesh domain cannot form a valid overset interface by
    # itself.  Use OpenFOAM's real deforming dynamic mesh for the four small
    # canard rotations; this keeps the transient physically solved instead of
    # producing an all-hole overset field.
    solver = "overInterDyMFoam" if request.use_multiphase else ("pimpleFoam" if request.mode == "transient" else "simpleFoam")
    steady = request.mode != "transient"
    # Five hundred SIMPLE iterations produced visibly drifting forces in the
    # landing case.  Keep enough pseudo-time for the force history itself to
    # settle; the 30 minute watchdog remains the hard upper bound.
    end_time = f"{max(float(request.transient_end_time_s), 0.002):.9g}" if not steady else "1500"
    # The refined boundary layer requires a small initial transient step.  The
    # adaptive CFL limiter may reduce it further when a canard changes angle.
    delta_t = "0.0001" if not steady else "1"
    mesh_cells = "66 36 36"
    surface_refinement = "(4 5)"
    surface_layers = 4
    max_courant = "0.5" if request.mode == "transient" else "1"
    max_delta_t = "0.0002" if request.mode == "transient" else "1"
    write_interval = f"{max(float(request.transient_write_interval_s), 0.002):.9g}" if not steady else "50"
    rain_note = "multiphase rain enabled" if request.use_multiphase else "single phase air"
    alpha, beta = math.radians(request.alpha_deg), math.radians(request.beta_deg)
    def inlet_at(speed_mps: float) -> tuple[float, float, float]:
        return (
            speed_mps * math.cos(alpha) * math.cos(beta),
            speed_mps * math.sin(beta),
            speed_mps * math.sin(alpha),
        )
    inlet = inlet_at(request.speed_mps)
    inlet_text = " ".join(f"{value:.9g}" for value in inlet)
    if request.inlet_velocity_schedule:
        inlet_rows = _strictly_increasing_time_rows(sorted(
            (float(time_s), (float(x), float(y), float(z)))
            for time_s, x, y, z in request.inlet_velocity_schedule
        ))
        inlet_table = "\n".join(
            f"({time_s:.9g} ({vector[0]:.9g} {vector[1]:.9g} {vector[2]:.9g}))"
            for time_s, vector in inlet_rows
        )
        initial_vector = inlet_rows[0][1]
        inlet_boundary = (
            "type uniformFixedValue; uniformValue table\n(\n" + inlet_table
            + f"\n); value uniform ({initial_vector[0]:.9g} {initial_vector[1]:.9g} {initial_vector[2]:.9g});"
        )
        inlet_text = " ".join(f"{value:.9g}" for value in initial_vector)
    elif request.mode == "transient" and request.speed_schedule:
        inlet_rows = _strictly_increasing_time_rows(sorted(
            (float(time_s), max(0.0, float(speed))) for time_s, speed in request.speed_schedule
        ))
        inlet_table = "\n".join(
            f"({time_s:.9g} ({' '.join(f'{component:.9g}' for component in inlet_at(speed))}))"
            for time_s, speed in inlet_rows
        )
        inlet_boundary = f"type uniformFixedValue; uniformValue table\n(\n{inlet_table}\n); value uniform ({' '.join(f'{component:.9g}' for component in inlet_at(inlet_rows[0][1]))});"
        inlet_text = " ".join(f"{value:.9g}" for value in inlet_at(inlet_rows[0][1]))
    else:
        inlet_boundary = f"type fixedValue; value uniform ({inlet_text});"
    # OpenFOAM stores p as kinematic pressure. Keep the density used for force
    # integration in the audited case so the viewer can report p in Pa.
    temperature = max(float(request.temperature_k), 150.0)
    air_density = moist_air_density(temperature, request.pressure_pa, request.humidity_ratio)
    viscosity = 1.716e-5 * (temperature / 273.15) ** 1.5 * (273.15 + 111.0) / (temperature + 111.0)
    kinematic_viscosity = viscosity / air_density
    turbulence_velocity = max(0.0, float(request.turbulence_intensity_mps))
    if request.inlet_velocity_schedule:
        _, first_x, first_y, first_z = min(request.inlet_velocity_schedule, key=lambda row: row[0])
        inlet_reference_speed = math.sqrt(first_x * first_x + first_y * first_y + first_z * first_z)
    elif request.speed_schedule:
        inlet_reference_speed = min(request.speed_schedule, key=lambda row: row[0])[1]
    else:
        inlet_reference_speed = float(request.speed_mps)
    raw_inlet_turbulence = turbulence_velocity / max(float(inlet_reference_speed), 0.2)
    # Open-Meteo's gust-minus-mean estimate is not a direct turbulence probe.
    # Treat it as an input estimate but keep the free-stream RANS intensity in
    # the physically useful 1-10% range.  Crucially, k must be derived from the
    # *applied* intensity; the previous code capped the audited percentage but
    # still built k from the uncapped gust velocity.
    inlet_turbulence = min(0.10, max(0.01, raw_inlet_turbulence))
    applied_turbulence_velocity = inlet_turbulence * max(float(inlet_reference_speed), 0.2)
    turbulent_k = 1.5 * applied_turbulence_velocity * applied_turbulence_velocity
    # Integral length scale of 7% of the 50.8 mm body diameter. The request
    # carries measured/modelled gust intensity; the RANS field resolves its
    # mean turbulence rather than inventing visible vortex lines.
    turbulent_omega = math.sqrt(turbulent_k) / (0.09 ** 0.25 * 0.003556)
    ddt_scheme = "Euler" if request.mode == "transient" else "steadyState"
    overset_schemes = ""
    solution_controls = (
        "PIMPLE { momentumPredictor yes; correctPhi no; nOuterCorrectors 1; nCorrectors 2; nNonOrthogonalCorrectors 1; pRefPoint (3 0 0); pRefValue 0; }"
        if request.mode == "transient" else
        "SIMPLE { nNonOrthogonalCorrectors 1; consistent yes; } relaxationFactors { fields { p 0.3; } equations { U 0.7; k 0.7; omega 0.7; } }"
    )
    # Snapshots can see air from any body-axis direction.  Freestream outer
    # boundaries switch between inlet and outlet from the local flux instead
    # of assuming that every case enters through the -X face.
    freestream_u = (
        f"type freestream; freestreamValue uniform ({inlet_text}); value uniform ({inlet_text});"
        if steady else inlet_boundary
    )
    freestream_p = "type freestreamPressure; freestreamValue uniform 0; value uniform 0;"
    turbulence_outer = f"type inletOutlet; inletValue uniform {turbulent_k:.9g}; value uniform {turbulent_k:.9g};"
    omega_outer = f"type inletOutlet; inletValue uniform {turbulent_omega:.9g}; value uniform {turbulent_omega:.9g};"
    parallel_allrun = (
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "nprocs=\"${FOAM_NPROCS:-1}\"\n"
        "if ! [[ \"$nprocs\" =~ ^[1-9][0-9]*$ ]]; then echo \"FOAM_NPROCS inválido: $nprocs\" >&2; exit 2; fi\n"
        "echo 'CFD_PHASE: prevalidación de herramientas MPI'\n"
        "command -v blockMesh >/dev/null; command -v snappyHexMesh >/dev/null; command -v checkMesh >/dev/null; command -v surfaceCheck >/dev/null; command -v mpirun >/dev/null; command -v decomposePar >/dev/null; command -v reconstructPar >/dev/null; command -v reconstructParMesh >/dev/null\n"
        "test -s constant/triSurface/rocket.stl || { echo 'CFD_PRECHECK_GEOMETRY_ERROR: falta constant/triSurface/rocket.stl' >&2; exit 20; }\n"
        "echo 'CFD_PHASE: validación de geometría STL'\n"
        "surfaceCheck constant/triSurface/rocket.stl | tee geometry-preflight.log\n"
        # ``surfaceCheck`` reports a healthy surface as "Surface has *no*
        # illegal triangles".  Match a non-zero count explicitly so that the
        # valid success message cannot abort a CFD run as a false positive.
        "if grep -Eqi 'surface has [1-9][0-9]* illegal triangles|surface is not closed|connected to >2 faces|not all edges.*connected to two|non[- ]manifold' geometry-preflight.log; then\n"
        "  echo 'CFD_PRECHECK_GEOMETRY_ERROR: STL no estanco o con caras ilegales; exporta un sólido cerrado sin solapes antes de mallar.' >&2; exit 20\n"
        "fi\n"
        "echo 'CFD_PHASE: generación de malla'\n"
        "blockMesh\n"
        "snappyHexMesh -overwrite\n"
        "checkMesh -meshQuality | tee mesh-preflight.log\n"
        # A global skewness failure is still a failed mesh check even when the
        # per-face error table uses a looser threshold.  Quantitative loads are
        # not published from that mesh.
        "if grep -Eqi 'Failed [1-9][0-9]* mesh checks' mesh-preflight.log; then\n"
        "  echo 'CFD_PRECHECK_MESH_ERROR: checkMesh reportó errores de calidad; el solver no se ejecutará.' >&2; exit 21\n"
        "fi\n"
        + (
            # A dynamic mesh requires these named zones even at time zero.
            # Creating them only after the initial solve caused simpleFoam to
            # abort with ``No matching cellZones: canard1``.
            "echo 'CFD_PHASE: zonas de canards'\n"
            "topoSet\n"
            "checkMesh -meshQuality | tee mesh-preflight-dynamic.log\n"
            "if grep -Eqi 'Failed [1-9][0-9]* mesh checks' mesh-preflight-dynamic.log; then\n"
            "  echo 'CFD_PRECHECK_MESH_ERROR: checkMesh reportó errores de calidad; el solver no se ejecutará.' >&2; exit 21\n"
            "fi\n"
            # RANS initialisation is deliberately static; advancing the PID
            # table during its pseudo-iterations is not an initial condition.
            "mv constant/dynamicMeshDict constant/dynamicMeshDict.transient\n"
            "echo 'CFD_PHASE: inicializaciÃ³n estacionaria del flujo'\n"
            "cp system/controlDict system/controlDict.transient\n"
            "cp system/fvSchemes system/fvSchemes.transient\n"
            "cp system/fvSolution system/fvSolution.transient\n"
            "cp system/controlDict.initialise system/controlDict\n"
            "cp system/fvSchemes.initialise system/fvSchemes\n"
            "cp system/fvSolution.initialise system/fvSolution\n"
            "simpleFoam\n"
            "for field in U p k omega nut; do test -s \"150/$field\" && cp \"150/$field\" \"0/$field\"; done\n"
            "cp system/controlDict.transient system/controlDict\n"
            "cp system/fvSchemes.transient system/fvSchemes\n"
            "cp system/fvSolution.transient system/fvSolution\n"
            "mv constant/dynamicMeshDict.transient constant/dynamicMeshDict\n"
            if request.mode == "transient" else ""
        )
        + "if [ \"$nprocs\" -gt 1 ]; then\n"
        "  echo 'CFD_PHASE: descomposición MPI'\n"
        "  decomposePar -force\n"
        "  mpirun --allow-run-as-root --bind-to none -np \"$nprocs\" checkMesh -parallel -meshQuality\n"
        f"  echo 'CFD_PHASE: solver {solver} paralelo'\n"
        f"  mpirun --allow-run-as-root --bind-to none -np \"$nprocs\" {solver} -parallel\n"
        "  echo 'CFD_PHASE: reconstrucción de resultados'\n"
        "  reconstructParMesh -constant\n"
        "  reconstructPar -latestTime\n"
        "else\n"
        + f"  echo 'CFD_PHASE: solver {solver}'\n  {solver}\n"
        "fi\n"
        + "echo 'CFD_PHASE: exportación VTK'\n"
        + "postProcess -func forces -time '0:" + end_time + "' || true\n"
        + "postProcess -func wallShearStress -time '0:" + end_time + "' || true\n"
        + "postProcess -func vorticity -time '0:" + end_time + "' || true\n"
        + "postProcess -func Q -time '0:" + end_time + "' || true\n"
        + "foamToVTK -allPatches\n"
    )
    def header(class_name: str, object_name: str) -> str:
        return (
            "FoamFile\n{\n"
            "    version     2.0;\n    format      ascii;\n"
            f"    class       {class_name};\n    object      {object_name};\n"
            "}\n"
        )
    files = {
        "system/controlDict": header("dictionary", "controlDict") + f"application {solver};\nstartFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime {end_time};\ndeltaT {delta_t};\nadjustTimeStep {'yes' if request.mode == 'transient' else 'no'};\nmaxCo {max_courant};\nmaxDeltaT {max_delta_t};\nwriteControl runTime;\nwriteInterval {write_interval};\nwriteAtEnd true;\nwriteFormat binary;\nfunctions {{\n  forces {{ type forces; libs (\"libforces.so\"); patches (rocket); CofR ({request.center_of_gravity_body_m[0]:.9g} {request.center_of_gravity_body_m[1]:.9g} {request.center_of_gravity_body_m[2]:.9g}); rho rhoInf; rhoInf {air_density:.9g}; writeControl writeTime; }}\n  wallShearStress {{ type wallShearStress; libs (\"fieldFunctionObjects\"); patches (rocket); writeControl writeTime; }}\n  vorticity {{ type vorticity; libs (\"fieldFunctionObjects\"); writeControl writeTime; }}\n  Q {{ type Q; libs (\"fieldFunctionObjects\"); writeControl writeTime; }}\n  rocketSurface {{ type surfaces; libs (sampling); writeControl writeTime; surfaceFormat vtk; fields (p wallShearStress); surfaces {{ rocket {{ type patch; patches (rocket); interpolate false; }} }} }}\n}}\n",
        "system/blockMeshDict": f"FoamFile {{ version 2.0; format ascii; class dictionary; object blockMeshDict; }}\nconvertToMeters 1;\nvertices ((-3 -3 -3) (8 -3 -3) (8 3 -3) (-3 3 -3) (-3 -3 3) (8 -3 3) (8 3 3) (-3 3 3));\nblocks (hex (0 1 2 3 4 5 6 7) ({mesh_cells}) simpleGrading (1 1 1));\nedges ();\nboundary (inlet {{ type patch; faces ((0 4 7 3)); }} outlet {{ type patch; faces ((1 2 6 5)); }} farfield {{ type patch; faces ((0 1 5 4) (3 7 6 2) (0 3 2 1) (4 5 6 7)); }});\nmergePatchPairs ();\n",
        "system/snappyHexMeshDict": f"FoamFile {{ version 2.0; format ascii; class dictionary; object snappyHexMeshDict; }}\ncastellatedMesh true; snap true; addLayers true;\ngeometry {{ rocket.stl {{ type triSurfaceMesh; name rocket; }} }}\ncastellatedMeshControls {{ maxLocalCells 1800000; maxGlobalCells 2200000; minRefinementCells 0; nCellsBetweenLevels 3; features (); refinementSurfaces {{ rocket {{ level {surface_refinement}; patchInfo {{ type wall; }} }} }} resolveFeatureAngle 30; refinementRegions {{}}; locationInMesh (3 0 0); allowFreeStandingZoneFaces true; }}\nsnapControls {{ nSmoothPatch 10; tolerance 1.35; nSolveIter 120; nRelaxIter 12; }}\naddLayersControls {{ relativeSizes true; layers {{ rocket {{ nSurfaceLayers {surface_layers}; }} }} expansionRatio 1.15; finalLayerThickness 0.20; minThickness 0.07; nGrow 1; featureAngle 55; nRelaxIter 10; nSmoothSurfaceNormals 4; nSmoothNormals 6; nSmoothThickness 20; maxFaceThicknessRatio 0.40; maxThicknessToMedialRatio 0.28; minMedialAxisAngle 90; nBufferCellsNoExtrude 2; nLayerIter 100; nRelaxedIter 35; }}\nmeshQualityControls {{ #includeEtc \"caseDicts/meshQualityDict\" maxNonOrtho 65; maxInternalSkewness 3.8; nSmoothScale 8; errorReduction 0.65; relaxed {{ maxNonOrtho 70; maxInternalSkewness 3.8; }} }}\nmergeTolerance 1e-6;\n",
        "system/meshQualityDict": header("dictionary", "meshQualityDict") + "#includeEtc \"caseDicts/meshQualityDict\"\nmaxNonOrtho 65;\nmaxBoundarySkewness 20;\nmaxInternalSkewness 4;\nminVol 1e-13;\nminTetQuality 1e-12;\nminDeterminant 0.0002;\nminFaceWeight 0.05;\nminVolRatio 0.01;\nminTwist 0.02;\nminTriangleTwist -1;\nminArea -1;\nminFaceFlatness -1;\n",
        "system/fvSchemes": f"FoamFile {{ version 2.0; format ascii; class dictionary; object fvSchemes; }}\nddtSchemes {{ default {ddt_scheme}; }}\ngradSchemes {{ default cellLimited Gauss linear 1; }}\ndivSchemes {{ default none; div(phi,U) bounded Gauss linearUpwind grad(U); div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear; }}\nlaplacianSchemes {{ default Gauss linear limited 0.333; }}\ninterpolationSchemes {{ default linear; }}\nsnGradSchemes {{ default limited 0.333; }}\nwallDist {{ method meshWave; }}\n{overset_schemes}",
        "system/fvSolution": "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\nsolvers { p { solver GAMG; tolerance 1e-7; relTol 0.1; smoother GaussSeidel; } pFinal { $p; relTol 0; } \"(U|k|omega)\" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0.1; } \"(U|k|omega)Final\" { $U; relTol 0; } }\n" + solution_controls + "\n",
        "system/decomposeParDict": header("dictionary", "decomposeParDict") + f"numberOfSubdomains {max(1, int(request.cores))};\nmethod scotch;\n",
        "0/U": f"FoamFile {{ version 2.0; format ascii; class volVectorField; object U; }}\ndimensions [0 1 -1 0 0 0 0];\ninternalField uniform ({inlet_text});\nboundaryField {{ inlet {{ {freestream_u} }} outlet {{ {freestream_u} }} farfield {{ {freestream_u} }} rocket {{ type noSlip; }} }}\n",
        "0/p": f"FoamFile {{ version 2.0; format ascii; class volScalarField; object p; }}\ndimensions [0 2 -2 0 0 0 0];\ninternalField uniform 0;\nboundaryField {{ inlet {{ {freestream_p} }} outlet {{ {freestream_p} }} farfield {{ {freestream_p} }} rocket {{ type zeroGradient; }} }}\n",
        "0/k": f"FoamFile {{ version 2.0; format ascii; class volScalarField; object k; }}\ndimensions [0 2 -2 0 0 0 0];\ninternalField uniform {turbulent_k:.9g};\nboundaryField {{ inlet {{ {turbulence_outer} }} outlet {{ {turbulence_outer} }} farfield {{ {turbulence_outer} }} rocket {{ type kqRWallFunction; value uniform {turbulent_k:.9g}; }} }}\n",
        "0/omega": f"FoamFile {{ version 2.0; format ascii; class volScalarField; object omega; }}\ndimensions [0 0 -1 0 0 0 0];\ninternalField uniform {turbulent_omega:.9g};\nboundaryField {{ inlet {{ {omega_outer} }} outlet {{ {omega_outer} }} farfield {{ {omega_outer} }} rocket {{ type omegaWallFunction; value uniform {turbulent_omega:.9g}; }} }}\n",
        "0/nut": "FoamFile { version 2.0; format ascii; class volScalarField; object nut; }\ndimensions [0 2 -1 0 0 0 0];\ninternalField uniform 0;\nboundaryField { inlet { type calculated; value uniform 0; } outlet { type calculated; value uniform 0; } farfield { type calculated; value uniform 0; } rocket { type nutkWallFunction; value uniform 0; } }\n",
        "constant/transportProperties": header("dictionary", "transportProperties") + f"transportModel Newtonian; nu [0 2 -1 0 0 0 0] {kinematic_viscosity:.9g};\n",
        "constant/turbulenceProperties": header("dictionary", "turbulenceProperties") + "simulationType RAS; RAS { RASModel kOmegaSST; turbulence on; printCoeffs on; }\n",
        "constant/canardSchedule.csv": "time_s,canard_1_deg,canard_2_deg,canard_3_deg,canard_4_deg\n" + "".join(
            f"{time_s},{c1},{c2},{c3},{c4}\n" for time_s, c1, c2, c3, c4 in request.canard_schedule
        ),
        "system/controlDict.initialise": header("dictionary", "controlDict") + (
            "application simpleFoam;\nstartFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime 150;\ndeltaT 1;\n"
            "writeControl timeStep;\nwriteInterval 150;\nwriteAtEnd true;\nwriteFormat binary;\nfunctions {}\n"
        ),
        "system/fvSchemes.initialise": (
            "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
            "ddtSchemes { default steadyState; }\ngradSchemes { default cellLimited Gauss linear 1; }\n"
            "divSchemes { default none; div(phi,U) bounded Gauss linearUpwind grad(U); div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear; }\n"
            "laplacianSchemes { default Gauss linear limited 0.333; }\ninterpolationSchemes { default linear; }\nsnGradSchemes { default limited 0.333; }\nwallDist { method meshWave; }\n"
        ),
        "system/fvSolution.initialise": (
            "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
            "solvers { p { solver GAMG; tolerance 1e-7; relTol 0.1; smoother GaussSeidel; } pFinal { $p; relTol 0; } \"(U|k|omega)\" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0.1; } \"(U|k|omega)Final\" { $U; relTol 0; } }\n"
            "SIMPLE { nNonOrthogonalCorrectors 1; consistent yes; } relaxationFactors { fields { p 0.3; } equations { U 0.7; k 0.7; omega 0.7; } }\n"
        ),
        "constant/laboratory.json": json.dumps({**asdict(request), "solver": solver, "note": rain_note, "air_density_kg_m3": air_density, "raw_inlet_turbulence_intensity": raw_inlet_turbulence, "inlet_turbulence_intensity": inlet_turbulence}, indent=2),
        "README.md": "# Caso CFD detallado\n\nConvención: +X apunta hacia la nariz del cohete; +Y a la derecha y +Z arriba. `U` es la velocidad real del aire respecto al cohete (opuesta a velocidad_cohete - viento) y las seis caras exteriores usan contorno freestream, por lo que el flujo puede llegar desde cualquier dirección del estado 6-DoF. Los campos `U`, `p`, `vorticity` y `Q` exportados por `foamToVTK` son la única fuente de contornos, líneas de corriente y vórtices de la interfaz.\n\nEl caso usa malla `snappyHexMesh` con capas de pared, RANS k-ω SST y `simpleFoam` para estacionario. Las fuerzas OpenFOAM corresponden exclusivamente al STL del cohete. La carga de recuperación q*CdS se conserva aparte porque no existe una geometría de campana y líneas validada para resolverla como CFD.\n",
        # Do not restrict foamToVTK to the requested endTime: adaptive CFL time
        # stepping may write the final, physically solved state a little past it.
        # Exporting all written directories keeps the player tied to real states.
        "Allrun": parallel_allrun,
    }
    if request.mode == "transient":
        # Four cell zones follow the four independently articulated canards.
        # Their motion functions read the actual 6-DoF/PID history rather than
        # a display-only CSV.  The zones are created after snappyHexMesh.
        # Same hinges as CfdViewport._canard_hinge_pivot after centring,
        # 1.2 m scaling and its source +Z -> tunnel +X transform.
        pivots = ((0.1287, 0.0, -0.0354), (0.1175, 0.0354, 0.0),
                  (0.1287, 0.0, 0.0354), (0.1175, -0.0354, 0.0))
        motions = "\n".join(
            f"  canard{index + 1} {{ solidBodyMotionFunction tabulated6DoFMotion; tabulated6DoFMotionCoeffs {{ CofG ({pivot[0]:.4g} {pivot[1]:.4g} {pivot[2]:.4g}); timeDataFileName \"<constant>/canardMotion{index + 1}.dat\"; interpolationScheme linear; }} }}"
            for index, pivot in enumerate(pivots)
        )
        files["constant/dynamicMeshDict"] = header("dictionary", "dynamicMeshDict") + (
            "dynamicFvMesh dynamicMotionSolverFvMesh;\nsolver multiSolidBodyMotionSolver;\n"
            "multiSolidBodyMotionSolverCoeffs\n{\n" + motions + "\n}\n"
        )
        boxes = tuple(
            f"canard{index} {{ type boxToCell; box ({low[0]:.4g} {low[1]:.4g} {low[2]:.4g}) ({high[0]:.4g} {high[1]:.4g} {high[2]:.4g}); }}"
            for index, (low, high) in enumerate(CANARD_ZONE_BOXES, start=1)
        )
        actions = "\n".join(
            f"{{ name canard{index + 1}; type cellSet; action new; source boxToCell; sourceInfo {{ box {box.split('box ', 1)[1].rstrip(' }')}; }} }}\n{{ name canard{index + 1}; type cellZoneSet; action new; source setToCellZone; sourceInfo {{ set canard{index + 1}; }} }}"
            for index, box in enumerate(boxes)
        )
        files["system/topoSetDict"] = header("dictionary", "topoSetDict") + "actions (\n" + actions + "\n);\n"
        files["0/zoneID"] = header("volScalarField", "zoneID") + (
            "dimensions [0 0 0 0 0 0 0];\ninternalField uniform 0;\n"
            "boundaryField { inlet { type zeroGradient; } outlet { type zeroGradient; } farfield { type zeroGradient; } rocket { type zeroGradient; } }\n"
        )
        frame_regions = "\n".join(
            f"cellToCell {{ set canard{index}; fieldValues (volScalarFieldValue zoneID {index}); }}"
            for index in range(1, 5)
        )
        files["system/setFieldsDict"] = header("dictionary", "setFieldsDict") + (
            "defaultFieldValues (volScalarFieldValue zoneID 0);\nregions (\n" + frame_regions + "\n);\n"
        )
        for index in range(4):
            files[f"constant/canardMotion{index + 1}.dat"] = _motion_table(request.canard_schedule, index)
    return files


def prepare_case(root: Path, request: CfdCaseRequest, rocket_stl: Path | None = None) -> Path:
    _validate_request(request)
    selected_stl = rocket_stl
    if selected_stl is None and request.rocket_stl_path.strip():
        selected_stl = Path(request.rocket_stl_path).expanduser()
    if selected_stl is not None and (selected_stl.suffix.lower() != ".stl" or not selected_stl.is_file()):
        raise ValueError(f"La superficie CFD debe ser un archivo STL existente: {selected_stl}")
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in request.case_name).strip("-") or "case"
    case_root = root / "out" / "cfd"
    case_dir = case_root / safe_name
    suffix = 2
    while case_dir.exists():
        case_dir = case_root / f"{safe_name}-{suffix}"
        suffix += 1
    case_dir.mkdir(parents=True)
    for relative, content in _foam_case_files(request).items():
        path = case_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    source = selected_stl or (root / "data" / "models" / PRIMARY_MODEL_STL)
    # Unit tests and exported historical cases may not carry the bundled NACA
    # asset.  Production always resolves the NACA model from the application.
    if selected_stl is None and not source.is_file():
        bundled = Path(__file__).resolve().parents[3] / "data" / "models" / PRIMARY_MODEL_STL
        historical = root / "data" / "models" / "ensamble_todo_v2_cfd.stl"
        source = historical if historical.is_file() else bundled
    if not source.is_file():
        raise FileNotFoundError(f"No existe la geometría CFD NACA: {source}")
    target = case_dir / "constant" / "triSurface" / "rocket.stl"
    if source.name == NACA_MODEL_STL:
        geometry = prepare_snapshot_surface(source, target, request.canard_deg)
        geometry_audit = {
            "source": str(source), "body_length_m": geometry.length_m,
            "components_closed": geometry.component_count, "canards": geometry.canard_count,
            "sha256": geometry.geometry_hash, "coordinate_transform": "mm/+Z -> m/+X",
        }
    else:
        # A caller-provided/historical STL is retained verbatim for audit and
        # still passes surfaceCheck before meshing.  The product default never
        # takes this path: it is the normalized NACA assembly above.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        geometry_audit = {"source": str(source), "legacy_or_custom": True}
    laboratory_path = case_dir / "constant" / "laboratory.json"
    laboratory = json.loads(laboratory_path.read_text(encoding="utf-8"))
    laboratory["geometry"] = geometry_audit
    laboratory_path.write_text(json.dumps(laboratory, indent=2, ensure_ascii=False), encoding="utf-8")
    return case_dir


_NUMBER = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
_TIME_STEP = re.compile(r"^\s*deltaT\s*=\s*(%s)\s*$" % _NUMBER.pattern)
# A transient case starts at 1e-4 s.  A smaller value by six orders of
# magnitude is a numerical collapse, not a useful adaptive-CFL refinement.
MIN_STABLE_TIME_STEP_S = 1e-10


def collapsed_timestep(line: str) -> float | None:
    """Return an unusably small OpenFOAM timestep, if the log contains one."""
    match = _TIME_STEP.match(line)
    if match is None:
        return None
    value = float(match.group(1))
    return value if 0.0 < value < MIN_STABLE_TIME_STEP_S else None


def _stop_solver_process(process: subprocess.Popen[str], container_name: str | None = None) -> None:
    """Stop Docker promptly and avoid leaving a hung CFD job behind."""
    if container_name:
        docker = shutil.which("docker") or "docker"
        try:
            subprocess.run([docker, "stop", "--timeout", "4", container_name], capture_output=True, text=True, timeout=8, creationflags=SUBPROCESS_CREATION_FLAGS)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        process.terminate()
    except (OSError, AttributeError):
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def cfd_phase_from_log_line(line: str, current: str = "preparación") -> str:
    """Map OpenFOAM output to a short phase name for the desktop UI."""
    text = line.lower()
    if "cfd_phase:" in text:
        return text.split("cfd_phase:", 1)[1].strip()
    if "checkmesh" in text or "cfd_precheck_mesh_error" in text:
        return "validación de malla"
    if "surfacecheck" in text or "cfd_precheck_geometry_error" in text:
        return "validación de geometría STL"
    if "snappyhexmesh" in text:
        return "malla snappyHexMesh"
    if "blockmesh" in text:
        return "malla base blockMesh"
    if "decomposepar" in text:
        return "descomposición MPI"
    if "toposet" in text:
        return "zonas de canards"
    if "pimplefoam" in text or "simplefoam" in text or "overinterdymfoam" in text or "time =" in text:
        return "solver OpenFOAM"
    if "reconstructpar" in text:
        return "reconstrucción de resultados"
    if "foamtovtk" in text:
        return "exportación VTK"
    return current


def cfd_failure_detail(returncode: int, phase: str, last_line: str) -> str:
    """Turn a shell/OpenFOAM failure into one useful next action for the UI."""
    normalized = last_line.lower()
    prefix = f"OpenFOAM terminó con código {returncode}; "
    if returncode == 21 or "cfd_precheck_mesh_error" in normalized or phase == "validación de malla":
        return prefix + "la malla no pasó checkMesh; revisa calidad, capas y zonas de canards antes de reintentar"
    if "cfd_precheck_geometry_error" in normalized or phase == "validación de geometría STL":
        return prefix + (
            "la validación previa rechazó la geometría STL; exporta un sólido estanco, sin caras ilegales "
            "ni piezas solapadas, y vuelve a ejecutar"
        )
    if phase == "descomposición MPI":
        return prefix + "falló la descomposición MPI; reduce núcleos o revisa decomposePar y las herramientas MPI"
    if phase == "zonas de canards":
        return prefix + "falló la creación de zonas dinámicas; revisa que las cuatro zonas sean disjuntas y cubran los canards"
    if "solver" in phase:
        return prefix + "el solver no pudo avanzar; revisa estabilidad, malla y amplitud/velocidad de los canards"
    return prefix + "el caso se conservó para diagnóstico"


def _time_from_vtk_path(path: Path) -> float | None:
    """Read the physical OpenFOAM time carried by a VTK file.

    Recent ``foamToVTK`` versions name directories with a time *index*
    (for example ``case_11``), not necessarily the floating-point time.
    ``TimeValue`` is therefore authoritative; the directory suffix is only a
    backwards-compatible fallback for older exports and tests.
    """
    try:
        import pyvista as pv

        time_values = pv.read(path).field_data.get("TimeValue")
        if time_values is not None and len(time_values):
            return float(time_values[0])
    except Exception:
        pass
    for parent in (path.parent, *path.parents):
        match = re.search(r"(?:^|[_-])(-?\d+(?:\.\d+)?)$", parent.name)
        if match:
            return float(match.group(1))
    return None


def _canards_at(schedule: tuple[tuple[float, float, float, float, float], ...], time_s: float) -> tuple[float, float, float, float]:
    """Match the same piecewise-linear motion table consumed by OpenFOAM."""
    if not schedule:
        return (0.0, 0.0, 0.0, 0.0)
    ordered = sorted(schedule, key=lambda row: row[0])
    if time_s <= ordered[0][0]:
        return tuple(float(value) for value in ordered[0][1:])
    if time_s >= ordered[-1][0]:
        return tuple(float(value) for value in ordered[-1][1:])
    for low, high in zip(ordered, ordered[1:]):
        if low[0] <= time_s <= high[0]:
            ratio = (time_s - low[0]) / max(high[0] - low[0], 1e-12)
            return tuple(float(low[index] + ratio * (high[index] - low[index])) for index in range(1, 5))
    return tuple(float(value) for value in ordered[-1][1:])


def _force_from_numbers(numbers: list[float], *, totals_first: bool = False) -> tuple[tuple[float, float, float], tuple[float, float, float] | None, tuple[float, float, float] | None] | None:
    """Parse both legacy ``forces.dat`` and v2512 ``force.dat`` rows."""
    if len(numbers) >= 10:
        if totals_first:  # OpenFOAM v2512: total, pressure, viscous.
            total = tuple(numbers[1 + index] for index in range(3))
            pressure = tuple(numbers[4 + index] for index in range(3))
            viscous = tuple(numbers[7 + index] for index in range(3))
            return total, pressure, viscous
        # Legacy output: pressure, viscous, porous (without a total column).
        pressure = tuple(numbers[1 + index] for index in range(3))
        viscous = tuple(numbers[4 + index] for index in range(3))
        return tuple(pressure[index] + viscous[index] + numbers[7 + index] for index in range(3)), pressure, viscous
    return None


def _read_force_series(case_dir: Path) -> dict[float, tuple[tuple[float, float, float], tuple[float, float, float] | None, tuple[float, float, float] | None]]:
    paths = sorted((*case_dir.glob("postProcessing/forces/*/forces.dat"), *case_dir.glob("postProcessing/forces/*/force.dat")))
    result: dict[float, tuple[tuple[float, float, float], tuple[float, float, float] | None, tuple[float, float, float] | None]] = {}
    for path in paths:
        content = path.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            if not line or line.startswith("#"):
                continue
            numbers = [float(value) for value in _NUMBER.findall(line)]
            parsed = _force_from_numbers(numbers, totals_first="total_x" in content)
            if parsed is not None and numbers:
                result[float(numbers[0])] = parsed
    return result


def _read_moment_series(case_dir: Path) -> dict[float, tuple[float, float, float]]:
    result: dict[float, tuple[float, float, float]] = {}
    for path in sorted(case_dir.glob("postProcessing/forces/*/moment.dat")):
        content = path.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            if not line or line.startswith("#"):
                continue
            numbers = [float(value) for value in _NUMBER.findall(line)]
            if len(numbers) >= 10:
                result[float(numbers[0])] = (
                    tuple(numbers[1 + index] for index in range(3))
                    if "total_x" in content else
                    tuple(numbers[1 + index] + numbers[4 + index] + numbers[7 + index] for index in range(3))
                )
    return result


def _vector_magnitude(vector: tuple[float, float, float]) -> float:
    return math.sqrt(sum(float(value) * float(value) for value in vector))


def _vector_at(
    schedule: tuple[tuple[float, float, float, float], ...], time_s: float,
    default: tuple[float, float, float] = (1.0, 0.0, 0.0),
) -> tuple[float, float, float]:
    if not schedule:
        return default
    ordered = sorted(schedule, key=lambda row: row[0])
    if time_s <= ordered[0][0]:
        return tuple(float(value) for value in ordered[0][1:4])
    if time_s >= ordered[-1][0]:
        return tuple(float(value) for value in ordered[-1][1:4])
    for low, high in zip(ordered, ordered[1:]):
        if low[0] <= time_s <= high[0]:
            ratio = (time_s - low[0]) / max(high[0] - low[0], 1e-12)
            return tuple(float(low[index] + ratio * (high[index] - low[index])) for index in range(1, 4))
    return default


def recovery_force_from_cds(
    inlet_velocity_body_mps: tuple[float, float, float], density_kg_m3: float, cds_m2: float,
) -> tuple[float, float, float]:
    """Return the auditable 6-DoF recovery load, separate from rocket CFD."""
    speed = _vector_magnitude(inlet_velocity_body_mps)
    if speed <= 1e-12 or cds_m2 <= 0.0:
        return (0.0, 0.0, 0.0)
    magnitude = 0.5 * max(float(density_kg_m3), 0.0) * speed * speed * max(float(cds_m2), 0.0)
    return tuple(magnitude * float(value) / speed for value in inlet_velocity_body_mps)


def _force_stability_ratio(
    series: dict[float, tuple[tuple[float, float, float], tuple[float, float, float] | None, tuple[float, float, float] | None]],
    window: int = 5,
) -> float | None:
    """Maximum normalized force change across the final written iterations."""
    vectors = [series[key][0] for key in sorted(series)]
    if len(vectors) < window:
        return None
    tail = vectors[-window:]
    scale = max(max(_vector_magnitude(vector) for vector in tail), 0.02)
    return max(
        _vector_magnitude(tuple(current[index] - previous[index] for index in range(3))) / scale
        for previous, current in zip(tail, tail[1:])
    )


def _residual_ratio(log_path: Path) -> float | None:
    """Return the worst final SIMPLE residual divided by its acceptance limit."""
    if not log_path.is_file():
        return None
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    matches = re.findall(
        r"Solving for\s+([^,]+),\s+Initial residual\s*=\s*([-+0-9.eE]+)", text,
    )
    if not matches:
        return None
    latest: dict[str, float] = {}
    for field, value in matches:
        latest[field.strip()] = abs(float(value))
    limits = {"Ux": 1e-3, "Uy": 1e-3, "Uz": 1e-3, "p": 1e-2, "k": 5e-3, "omega": 5e-3}
    if any(field not in latest for field in limits):
        return None
    return max(latest[field] / limit for field, limit in limits.items())


def _nearest_series_value(series: dict[float, object], time_s: float, default: object) -> object:
    if not series:
        return default
    closest = min(series, key=lambda sample_time: abs(sample_time - time_s))
    return series[closest]


def _collect_frames(
    case_dir: Path,
    schedule: tuple[tuple[float, float, float, float, float], ...],
    inlet_schedule: tuple[tuple[float, float, float, float], ...] = (),
    density_kg_m3: float = 1.225,
    recovery_cds_m2: float = 0.0,
    center_of_gravity_body_m: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[CfdFrame, ...]:
    """Collect all discrete VTK exports and their matching OpenFOAM loads."""
    force_series = _read_force_series(case_dir)
    moment_series = _read_moment_series(case_dir)
    candidates = [path for path in case_dir.glob("VTK/**/*.vtu") if path.name.lower() == "internal.vtu"]
    timed = [(time_s, path) for path in candidates if (time_s := _time_from_vtk_path(path)) is not None]
    frames: list[CfdFrame] = []
    for time_s, vtk_path in sorted(timed, key=lambda item: item[0]):
        surface_candidates = sorted((
            *vtk_path.parent.glob("**/*rocket*.vtp"),
            *vtk_path.parent.glob("**/*rocket*.vtu"),
        ))
        if not surface_candidates:
            exported_surfaces = tuple(case_dir.glob("postProcessing/**/rocket*.vtp")) + tuple(case_dir.glob("postProcessing/**/rocket*.vtu"))
            candidates_with_time: list[tuple[float, Path]] = []
            for surface_path in exported_surfaces:
                for parent in (surface_path.parent, *surface_path.parents):
                    try:
                        candidates_with_time.append((float(parent.name), surface_path))
                        break
                    except ValueError:
                        continue
            if candidates_with_time:
                surface_candidates = [min(candidates_with_time, key=lambda item: abs(item[0] - time_s))[1]]
        force, pressure_force, viscous_force = _nearest_series_value(
            force_series, time_s, ((0.0, 0.0, 0.0), None, None)
        )
        moment = _nearest_series_value(moment_series, time_s, (0.0, 0.0, 0.0))
        inlet_velocity = _vector_at(inlet_schedule, time_s)
        frames.append(CfdFrame(
            time_s=time_s, canard_deg=_canards_at(schedule, time_s), vtk_path=vtk_path,
            surface_vtk_path=surface_candidates[0] if surface_candidates else None,
            force_n=force, moment_nm=moment, pressure_force_n=pressure_force, viscous_force_n=viscous_force,
            inlet_velocity_body_mps=inlet_velocity,
            recovery_force_n=recovery_force_from_cds(inlet_velocity, density_kg_m3, recovery_cds_m2),
            center_of_gravity_body_m=center_of_gravity_body_m,
        ))
    return tuple(frames)


def _write_frame_manifest(case_dir: Path, frames: tuple[CfdFrame, ...]) -> None:
    """Persist relative paths so downloaded cases replay independently of the UI."""
    payload = {
        "version": 1,
        "frames": [
            {
                "time_s": frame.time_s,
                "canard_deg": frame.canard_deg,
                "vtk_path": str(frame.vtk_path.relative_to(case_dir)) if frame.vtk_path else None,
                "surface_vtk_path": str(frame.surface_vtk_path.relative_to(case_dir)) if frame.surface_vtk_path else None,
                "force_n": frame.force_n,
                "moment_nm": frame.moment_nm,
                "pressure_force_n": frame.pressure_force_n,
                "viscous_force_n": frame.viscous_force_n,
                "inlet_velocity_body_mps": frame.inlet_velocity_body_mps,
                "recovery_force_n": frame.recovery_force_n,
                "center_of_gravity_body_m": frame.center_of_gravity_body_m,
            }
            for frame in frames
        ],
    }
    (case_dir / "cfd_frames.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def mesh_preflight_passes(path: Path) -> bool:
    """Accept only a mesh for which checkMesh reports no failed checks."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "Checking faces in error" not in text:
        return False
    if re.search(r"Failed\s+[1-9][0-9]*\s+mesh checks", text, re.IGNORECASE):
        return False
    table = text.rsplit("Checking faces in error", 1)[1]
    return re.search(r":\s*[1-9][0-9]*\s*$", table, re.MULTILINE) is None


def parse_result(case_dir: Path, *, execution_backend: str | None = None) -> CfdResult:
    force = moment = (0.0, 0.0, 0.0)
    pressure_force: tuple[float, float, float] | None = None
    viscous_force: tuple[float, float, float] | None = None
    pressure = 0.0
    force_file = case_dir / "forces.csv"
    if force_file.exists():
        values = force_file.read_text(encoding="utf-8").split()
        if len(values) >= 6:
            force, moment = tuple(map(float, values[:3])), tuple(map(float, values[3:6]))
    pressure_file = case_dir / "pressure.csv"
    if pressure_file.exists():
        with pressure_file.open(newline="", encoding="utf-8") as handle:
            row = next(csv.reader(handle), ["", "0"])
            if len(row) > 1:
                pressure = float(row[1])
    force_outputs = sorted((*case_dir.glob("postProcessing/forces/*/forces.dat"), *case_dir.glob("postProcessing/forces/*/force.dat")))
    if force_outputs:
        lines = [line for line in force_outputs[-1].read_text(encoding="utf-8", errors="ignore").splitlines() if line and not line.startswith("#")]
        if lines:
            numbers = [float(value) for value in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", lines[-1])]
            if len(numbers) >= 16:  # Legacy forces.dat: force and moment on one line.
                force = tuple(numbers[1 + index] + numbers[4 + index] for index in range(3))
                moment = tuple(numbers[10 + index] + numbers[13 + index] for index in range(3))
            elif len(numbers) >= 10:
                totals_first = "total_x" in force_outputs[-1].read_text(encoding="utf-8", errors="ignore")
                parsed_force = _force_from_numbers(numbers, totals_first=totals_first)
                assert parsed_force is not None
                force, pressure_force, viscous_force = parsed_force
                moment_outputs = sorted(case_dir.glob("postProcessing/forces/*/moment.dat"))
                if moment_outputs:
                    moment_lines = [line for line in moment_outputs[-1].read_text(encoding="utf-8", errors="ignore").splitlines() if line and not line.startswith("#")]
                    if moment_lines:
                        values = [float(value) for value in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", moment_lines[-1])]
                        if len(values) >= 10:
                            totals_first = "total_x" in moment_outputs[-1].read_text(encoding="utf-8", errors="ignore")
                            moment = (
                                tuple(values[1 + index] for index in range(3)) if totals_first else
                                tuple(values[1 + index] + values[4 + index] + values[7 + index] for index in range(3))
                            )
    schedule: tuple[tuple[float, float, float, float, float], ...] = ()
    density = 1.225
    recorded_cores = 1
    execution_scope = "full_flight"
    geometry_hash = ""
    snapshot_source_time_s: float | None = None
    snapshot_reason = ""
    inlet_schedule: tuple[tuple[float, float, float, float], ...] = ()
    recovery_cds_m2 = 0.0
    center_of_gravity_body_m = (0.0, 0.0, 0.0)
    laboratory = case_dir / "constant" / "laboratory.json"
    if laboratory.is_file():
        try:
            laboratory_data = json.loads(laboratory.read_text(encoding="utf-8"))
            density = float(laboratory_data.get("air_density_kg_m3", density))
            schedule = tuple(tuple(float(value) for value in row) for row in laboratory_data.get("canard_schedule", ()))
            if not schedule:
                fixed_canards = tuple(float(value) for value in laboratory_data.get("canard_deg", ()))
                if len(fixed_canards) == 4:
                    schedule = ((0.0, *fixed_canards),)
            recorded_cores = int(laboratory_data.get("cores", recorded_cores))
            execution_scope = str(laboratory_data.get("execution_scope", execution_scope))
            geometry_hash = str(laboratory_data.get("geometry", {}).get("sha256", ""))
            raw_time = laboratory_data.get("snapshot_source_time_s")
            snapshot_source_time_s = None if raw_time is None else float(raw_time)
            snapshot_reason = str(laboratory_data.get("snapshot_reason", ""))
            inlet_schedule = tuple(
                tuple(float(value) for value in row)
                for row in laboratory_data.get("inlet_velocity_schedule", ())
                if len(row) >= 4
            )
            recovery_cds_m2 = max(0.0, float(laboratory_data.get("recovery_cds_m2", 0.0)))
            raw_cg = tuple(float(value) for value in laboratory_data.get("center_of_gravity_body_m", (0.0, 0.0, 0.0)))
            if len(raw_cg) == 3:
                center_of_gravity_body_m = raw_cg
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    frames = _collect_frames(
        case_dir, schedule, inlet_schedule, density, recovery_cds_m2, center_of_gravity_body_m,
    )
    if frames:
        _write_frame_manifest(case_dir, frames)
        final_frame = frames[-1]
        vtk = final_frame.vtk_path
        force, moment = final_frame.force_n, final_frame.moment_nm
        pressure_force, viscous_force = final_frame.pressure_force_n, final_frame.viscous_force_n
    else:
        vtk = next(iter(sorted(case_dir.glob("VTK/**/*.vtu"))), None)
    # Field discovery is intentionally lazy in the viewport.  Reading a
    # 40–50 MB VTU here for each of the five completed snapshots caused a
    # large post-solver memory spike before the user selected any field.
    fields: tuple[str, ...] = ()
    mesh_ok = mesh_preflight_passes(case_dir / "mesh-preflight.log")
    force_series = _read_force_series(case_dir)
    force_stability = _force_stability_ratio(force_series)
    residual_quality = _residual_ratio(case_dir / "run.log")
    convergence_failures: list[str] = []
    if not mesh_ok:
        convergence_failures.append("checkMesh no pasó todas las comprobaciones")
    if force_stability is None:
        convergence_failures.append("faltan al menos cinco escrituras de fuerza para verificar estabilidad")
    elif force_stability > 0.02:
        convergence_failures.append(f"las fuerzas aún cambian {100.0 * force_stability:.1f}% (límite 2%)")
    if residual_quality is None:
        convergence_failures.append("no se pudieron verificar los residuales finales")
    elif residual_quality > 1.0:
        convergence_failures.append(f"los residuales exceden el criterio por un factor {residual_quality:.1f}")
    converged = bool(vtk and force_outputs and not convergence_failures)
    inlet_velocity = _vector_at(inlet_schedule, frames[-1].time_s if frames else 0.0)
    recovery_force = recovery_force_from_cds(inlet_velocity, density, recovery_cds_m2)
    return CfdResult(
        case_dir, force, moment, pressure, case_dir / "run.log", vtk,
        execution_backend=execution_backend or select_execution_backend(
            cuda_solver_available=False, gpu_runtime_available=False, mpi_cores=recorded_cores,
        ).label,
        execution_scope=execution_scope,
        available_fields=fields, air_density_kg_m3=density,
        pressure_force_n=pressure_force, viscous_force_n=viscous_force,
        frames=frames,
        converged=converged, geometry_hash=geometry_hash,
        snapshot_source_time_s=snapshot_source_time_s, snapshot_reason=snapshot_reason,
        inlet_velocity_body_mps=inlet_velocity, recovery_force_n=recovery_force,
        center_of_gravity_body_m=center_of_gravity_body_m,
        convergence_reason="convergencia verificada" if converged else "; ".join(convergence_failures),
        force_stability_ratio=force_stability, residual_ratio=residual_quality,
    )


def export_result_bundle(result: CfdResult, destination: Path) -> Path:
    """Create a portable, auditable ZIP of an actual CFD result and its inputs."""
    if not result.is_openfoam:
        raise ValueError("Solo se pueden descargar paquetes de resultados CFD/OpenFOAM reales")
    case_dir = result.case_dir.resolve()
    if not case_dir.is_dir():
        raise FileNotFoundError(f"No existe el caso CFD: {case_dir}")
    destination = destination.resolve().with_suffix(".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    laboratory: dict[str, object] = {}
    laboratory_path = case_dir / "constant" / "laboratory.json"
    if laboratory_path.is_file():
        try:
            laboratory = json.loads(laboratory_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            laboratory = {}
    vtk_relative = None
    if result.vtk_path is not None:
        vtk_relative = str(Path(result.vtk_path).resolve().relative_to(case_dir))
    summary = {
        "backend": result.backend,
        "execution_backend": result.execution_backend,
        "execution_scope": result.execution_scope,
        "case_directory": case_dir.name,
        "force_n": result.force_n,
        "pressure_force_n": result.pressure_force_n,
        "viscous_force_n": result.viscous_force_n,
        "inlet_velocity_body_mps": result.inlet_velocity_body_mps,
        "recovery_force_n": result.recovery_force_n,
        "system_force_n": result.system_force_n,
        "center_of_gravity_body_m": result.center_of_gravity_body_m,
        "moment_nm": result.moment_nm,
        "pressure_reference_pa": result.pressure_pa,
        "air_density_kg_m3": result.air_density_kg_m3,
        "vtk_path": vtk_relative,
        "available_fields": result.available_fields,
        "frame_manifest": "cfd_frames.json" if result.frames else None,
        "frame_count": len(result.frames),
        "converged": result.converged,
        "convergence_reason": result.convergence_reason,
        "force_stability_ratio": result.force_stability_ratio,
        "residual_ratio": result.residual_ratio,
        "case_settings": laboratory,
    }
    guide = """# Resultados CFD detallados / OpenFOAM

Este paquete contiene el caso ejecutado, la malla, los archivos de configuración,
el registro, fuerzas, y los campos VTK reales. `result_summary.json` identifica
el caso usado y resume la fuerza y el momento finales.

## Cómo analizarlo

- Abra `VTK/case_*/internal.vtu` en ParaView o PyVista.
- `U` es velocidad en m/s. `p` es presión cinemática de simpleFoam; multiplíquela
  por `air_density_kg_m3` del resumen para obtener Pa.
- `vorticity` está en 1/s y `Q` identifica regiones de rotación.
- `postProcessing/forces/*/force.dat` y `moment.dat` contienen, por fila, las
  contribuciones de presión, viscosa y porosa en N y N·m.
- Revise `run.log`, `checkMesh` y el historial de fuerzas antes de usar los
  valores para decisiones de diseño. Este caso es RANS k-omega SST estacionario.
"""
    included_roots = ("0", "constant", "system", "postProcessing", "VTK", "run.log", "cfd_frames.json")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative in included_roots:
            source = case_dir / relative
            if source.is_file():
                archive.write(source, source.relative_to(case_dir))
            elif source.is_dir():
                for file_path in source.rglob("*"):
                    if file_path.is_file():
                        archive.write(file_path, file_path.relative_to(case_dir))
        archive.writestr("result_summary.json", json.dumps(summary, indent=2, ensure_ascii=False))
        archive.writestr("GUIA_DE_ANALISIS.md", guide)
    return destination


def _preliminary_local_result(case_dir: Path, request: CfdCaseRequest, reason: str, progress: Callable[[str], None]) -> CfdResult:
    """Provide an explicitly-labelled local engineering estimate when Docker cannot run.

    This keeps the laboratory useful during a registry outage, but never claims
    that the result is a validated OpenFOAM computation.  The generated files
    use the same result boundary as OpenFOAM so the 3D viewport and canard
    playback remain available to the user.
    """
    rho = 1.225
    reference_area_m2 = 0.002027
    alpha = math.radians(request.alpha_deg)
    beta = math.radians(request.beta_deg)
    canard_mean = sum(request.canard_deg) / 4.0
    q = 0.5 * rho * request.speed_mps ** 2
    rain_factor = 1.0 + min(0.15, 0.01 * request.rain_rate_mm_h)
    cd = (0.55 + 0.8 * alpha ** 2 + 0.35 * beta ** 2) * rain_factor
    drag = q * reference_area_m2 * cd
    normal = q * reference_area_m2 * (2.0 * alpha + 0.025 * math.radians(canard_mean))
    side = q * reference_area_m2 * (1.6 * beta)
    force = (-drag, side, normal)
    moment = (0.0, normal * 0.34, side * 0.34)
    (case_dir / "forces.csv").write_text(" ".join(f"{value:.9g}" for value in (*force, *moment)) + "\n", encoding="utf-8")
    (case_dir / "pressure.csv").write_text(f"pressure_pa,{q:.9g}\n", encoding="utf-8")
    message = (
        "OpenFOAM no pudo descargarse; se generó una estimación aerodinámica local "
        "preliminar. No usar este resultado como CFD validado.\n"
        f"Motivo Docker: {reason}\n"
    )
    log_path = case_dir / "run.log"
    log_path.write_text(message, encoding="utf-8")
    progress("Docker Hub no disponible: usando modelo local preliminar (no CFD validado).")
    return CfdResult(
        case_dir, force, moment, q, log_path, backend="modelo local preliminar",
        execution_backend="CPU de respaldo", note=reason,
    )


def _pull_openfoam(case_dir: Path, request: CfdCaseRequest, progress: Callable[[str], None]) -> str | None:
    """Retry transient Docker Hub EOF failures before allowing the local fallback."""
    last_error = "No se pudo descargar OpenFOAM"
    for attempt in range(1, 4):
        progress(f"Descargando {OPENFOAM_IMAGE} (intento {attempt}/3)…")
        try:
            pull = subprocess.run(docker_command(case_dir, request, pull=True), capture_output=True, text=True, timeout=300, creationflags=SUBPROCESS_CREATION_FLAGS)
        except subprocess.TimeoutExpired:
            last_error = "La descarga de OpenFOAM superó el tiempo límite"
        else:
            if pull.returncode == 0:
                return None
            last_error = pull.stderr.strip() or pull.stdout.strip() or last_error
        if "EOF" not in last_error and attempt < 3:
            break
    progress("Docker Hub no respondió; probando el proxy de registro verificado…")
    docker = shutil.which("docker") or "docker"
    try:
        proxy_pull = subprocess.run([docker, "pull", OPENFOAM_PROXY_IMAGE], capture_output=True, text=True, timeout=600, creationflags=SUBPROCESS_CREATION_FLAGS)
        inspected = subprocess.run([docker, "image", "inspect", OPENFOAM_PROXY_IMAGE, "--format", "{{join .RepoDigests \",\"}}"], capture_output=True, text=True, timeout=30, creationflags=SUBPROCESS_CREATION_FLAGS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{last_error}; el proxy de registro no respondió: {exc}"
    digests = inspected.stdout.strip()
    if proxy_pull.returncode == 0 and inspected.returncode == 0 and OPENFOAM_2512_DIGEST in digests:
        tagged = subprocess.run([docker, "tag", OPENFOAM_PROXY_IMAGE, OPENFOAM_IMAGE], capture_output=True, text=True, timeout=30, creationflags=SUBPROCESS_CREATION_FLAGS)
        if tagged.returncode == 0:
            progress("OpenFOAM v2512 descargado por proxy y verificado por digest oficial.")
            return None
        return f"El proxy se verificó, pero no se pudo etiquetar la imagen: {tagged.stderr.strip()}"
    proxy_error = proxy_pull.stderr.strip() or proxy_pull.stdout.strip() or "el digest no coincidió con OpenFOAM v2512"
    return f"{last_error}; proxy rechazado: {proxy_error}"


def run_case(case_dir: Path, request: CfdCaseRequest, progress: Callable[[str], None], cancel: threading.Event) -> CfdResult:
    status = docker_status()
    if not status.available:
        return _preliminary_local_result(case_dir, request, status.message, progress)
    if not status.image_present:
        pull_error = _pull_openfoam(case_dir, request, progress)
        if pull_error:
            raise RuntimeError(
                "No se instaló OpenFOAM; no se generó un resultado preliminar para no confundirlo con CFD detallado. "
                + pull_error
            )
    container_name = "sultana-cfd-" + re.sub(r"[^a-z0-9-]", "-", case_dir.name.lower())[:35].strip("-") + "-" + uuid.uuid4().hex[:8]
    command = docker_command(case_dir, request, container_name=container_name)
    log_path = case_dir / "run.log"
    phase = "inicio del contenedor"
    last_lines: deque[str] = deque(maxlen=12)
    started = time.monotonic()
    latest_progress = 0.0
    line_queue: queue.Queue[str | None] = queue.Queue()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=case_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=SUBPROCESS_CREATION_FLAGS)
        assert process.stdout is not None
        def _read_stdout() -> None:
            try:
                for item in process.stdout:
                    line_queue.put(item)
            finally:
                line_queue.put(None)
        threading.Thread(target=_read_stdout, name="cfd-log-reader", daemon=True).start()
        stream_closed = False
        while not stream_closed or (getattr(process, "poll", lambda: getattr(process, "returncode", 0))() is None):
            if cancel.is_set():
                _stop_solver_process(process, container_name)
                raise CfdRunFailure(phase, "Caso CFD cancelado por el usuario", log_path, last_lines[-1] if last_lines else "")
            if time.monotonic() - started >= request.wall_time_limit_s:
                _stop_solver_process(process, container_name)
                raise CfdRunFailure(phase, f"timeout tras {request.wall_time_limit_s} s; el contenedor fue detenido", log_path, last_lines[-1] if last_lines else "")
            try:
                line = line_queue.get(timeout=0.25)
            except queue.Empty:
                now = time.monotonic()
                if now - latest_progress >= 0.25:
                    elapsed = now - started
                    progress(f"CFD_STATUS phase={phase}; elapsed_s={elapsed:.0f}; remaining_s={max(0.0, request.wall_time_limit_s - elapsed):.0f}")
                    latest_progress = now
                continue
            if line is None:
                stream_closed = True
                continue
            clean_line = line.rstrip()
            log.write(line); log.flush()
            if time.monotonic() - latest_progress >= 0.25:
                progress(clean_line); latest_progress = time.monotonic()
            last_lines.append(clean_line)
            phase = cfd_phase_from_log_line(clean_line, phase)
            if cancel.is_set():
                _stop_solver_process(process, container_name)
                raise CfdRunFailure(phase, "Caso CFD cancelado por el usuario", log_path, clean_line)
            collapsed = collapsed_timestep(line)
            if collapsed is not None:
                _stop_solver_process(process, container_name)
                raise CfdRunFailure(
                    "solver OpenFOAM", f"el paso de tiempo cayó a {collapsed:.3g} s, señal de inestabilidad numérica; "
                    "revisa la malla y el movimiento de canards antes de reintentar", log_path, clean_line,
                )
        if process.wait() != 0:
            last_line = next((line for line in reversed(last_lines) if line), "")
            raise CfdRunFailure(
                phase, cfd_failure_detail(process.returncode, phase, last_line), log_path, last_line,
            )
    result = parse_result(
        case_dir,
        execution_backend=select_execution_backend(
            cuda_solver_available=False, gpu_runtime_available=False, mpi_cores=max(1, int(request.cores)),
        ).label,
    )
    if result.is_openfoam and not result.converged:
        raise CfdRunFailure(
            "validación de convergencia",
            result.convergence_reason or "el caso terminó sin demostrar convergencia de residuales y fuerzas",
            log_path,
            last_lines[-1] if last_lines else "",
        )
    # ``asdict`` recursively turns CfdFrame instances into dictionaries.  The
    # Qt viewport needs the typed frame objects to access their VTK paths,
    # loads and times while replaying the result.
    return replace(result, wall_time_s=time.monotonic() - started)
