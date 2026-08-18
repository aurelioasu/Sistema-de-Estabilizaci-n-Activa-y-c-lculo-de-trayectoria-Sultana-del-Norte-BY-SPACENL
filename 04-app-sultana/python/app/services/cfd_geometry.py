"""Shared, auditable geometry preparation for the CFD tunnel and viewport.

The authoritative NACA assembly is supplied in millimetres with its long axis
on +Z.  OpenFOAM and the viewport use metres with the long axis on +X.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
import struct


PHYSICAL_BODY_LENGTH_M = 0.902
NACA_MODEL_STL = "ensamble_naca_661_212.stl"


@dataclass(frozen=True)
class PreparedGeometry:
    path: Path
    geometry_hash: str
    component_count: int
    canard_count: int
    length_m: float


def _read_binary_stl(path: Path) -> tuple[bytes, list[list[tuple[float, float, float]]]]:
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError(f"STL inválido o vacío: {path}")
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) != 84 + 50 * count:
        raise ValueError("La superficie CFD debe ser STL binario; exporta el CAD como STL binario cerrado")
    faces: list[list[tuple[float, float, float]]] = []
    for index in range(count):
        offset = 84 + 50 * index + 12
        values = struct.unpack_from("<9f", data, offset)
        faces.append([tuple(values[vertex * 3:vertex * 3 + 3]) for vertex in range(3)])
    return data[:80], faces


def _write_binary_stl(path: Path, header: bytes, faces: list[list[tuple[float, float, float]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (header[:80] + b" " * 80)[:80]
    with path.open("wb") as handle:
        handle.write(header)
        handle.write(struct.pack("<I", len(faces)))
        for face in faces:
            a, b, c = face
            ux, uy, uz = (b[i] - a[i] for i in range(3)); vx, vy, vz = (c[i] - a[i] for i in range(3))
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            magnitude = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            handle.write(struct.pack("<12fH", nx / magnitude, ny / magnitude, nz / magnitude,
                                     *(coordinate for vertex in face for coordinate in vertex), 0))


def _components(faces: list[list[tuple[float, float, float]]]) -> list[list[int]]:
    """Return connected closed-surface components using shared STL vertices."""
    vertex_faces: dict[tuple[int, int, int], list[int]] = {}
    for face_index, face in enumerate(faces):
        for vertex in face:
            key = tuple(round(value * 1_000_000) for value in vertex)
            vertex_faces.setdefault(key, []).append(face_index)
    neighbours: list[set[int]] = [set() for _ in faces]
    for related in vertex_faces.values():
        first = related[0]
        neighbours[first].update(related[1:])
        for item in related[1:]: neighbours[item].add(first)
    unseen = set(range(len(faces))); result: list[list[int]] = []
    while unseen:
        start = unseen.pop(); stack = [start]; component = [start]
        while stack:
            current = stack.pop()
            for adjacent in neighbours[current] & unseen:
                unseen.remove(adjacent); stack.append(adjacent); component.append(adjacent)
        result.append(component)
    return result


def _bounds(faces: list[list[tuple[float, float, float]]], indices: list[int] | None = None) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    selected = indices if indices is not None else list(range(len(faces)))
    points = [vertex for index in selected for vertex in faces[index]]
    return tuple(min(point[axis] for point in points) for axis in range(3)), tuple(max(point[axis] for point in points) for axis in range(3))


def _canard_components(faces: list[list[tuple[float, float, float]],], components: list[list[int]]) -> tuple[int, int, int, int]:
    # After shared conversion, select the four most radially separated pieces.
    radial: list[tuple[float, int]] = []
    for index, component in enumerate(components):
        low, high = _bounds(faces, component)
        centre = tuple((low[axis] + high[axis]) * 0.5 for axis in range(3))
        radial.append((math.hypot(centre[1], centre[2]), index))
    candidates = [index for _, index in sorted(radial, reverse=True)[:4]]
    if len(candidates) != 4:
        raise ValueError("La geometría CFD debe contener cuatro canards independientes")
    centres = {index: tuple((a + b) * 0.5 for a, b in zip(*_bounds(faces, components[index]))) for index in candidates}
    top = max(candidates, key=lambda index: centres[index][2]); bottom = min(candidates, key=lambda index: centres[index][2])
    remaining = [index for index in candidates if index not in (top, bottom)]
    right = max(remaining, key=lambda index: centres[index][1]); left = min(remaining, key=lambda index: centres[index][1])
    return top, right, bottom, left


def _rotate(point: tuple[float, float, float], pivot: tuple[float, float, float], axis: tuple[float, float, float], radians: float) -> tuple[float, float, float]:
    x, y, z = (point[i] - pivot[i] for i in range(3)); ax, ay, az = axis
    cosine, sine = math.cos(radians), math.sin(radians)
    dot = x * ax + y * ay + z * az
    cross = (ay * z - az * y, az * x - ax * z, ax * y - ay * x)
    return tuple(pivot[i] + (x, y, z)[i] * cosine + cross[i] * sine + (ax, ay, az)[i] * dot * (1.0 - cosine) for i in range(3))


def prepare_snapshot_surface(source: Path, target: Path, canard_deg: tuple[float, float, float, float]) -> PreparedGeometry:
    """Convert mm/+Z to m/+X and bake the four requested canard angles."""
    header, source_faces = _read_binary_stl(source)
    low, high = _bounds(source_faces)
    source_length = high[2] - low[2]
    if source_length <= 0:
        raise ValueError("El STL no tiene longitud longitudinal positiva")
    # NACA is in millimetres; deriving the scale from its measured Z extent also
    # protects supplied CAD from accidental unit changes.
    scale = PHYSICAL_BODY_LENGTH_M / source_length
    centre = tuple((low[axis] + high[axis]) * 0.5 for axis in range(3))
    def convert(vertex: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = ((vertex[axis] - centre[axis]) * scale for axis in range(3))
        return z, y, -x
    faces = [[convert(vertex) for vertex in face] for face in source_faces]
    components = _components(faces)
    if source.name == NACA_MODEL_STL and len(components) != 8:
        raise ValueError(f"El ensamblaje NACA debe conservar 8 componentes cerrados; se encontraron {len(components)}")
    canards = _canard_components(faces, components)
    for angle, component_index in zip(canard_deg, canards):
        indices = components[component_index]
        vertices = [vertex for face_index in indices for vertex in faces[face_index]]
        # The hinge lies at the component's body-side radial edge.  Its axis is
        # radial; this is invariant under the shared +Z -> +X conversion.
        radius = [math.hypot(vertex[1], vertex[2]) for vertex in vertices]
        minimum = min(radius); hinge_vertices = [vertex for vertex, value in zip(vertices, radius) if value <= minimum + 1e-5]
        pivot = tuple(sum(vertex[axis] for vertex in hinge_vertices) / len(hinge_vertices) for axis in range(3))
        radial_norm = math.hypot(pivot[1], pivot[2]) or 1.0
        axis = (0.0, pivot[1] / radial_norm, pivot[2] / radial_norm)
        for face_index in indices:
            faces[face_index] = [_rotate(vertex, pivot, axis, math.radians(float(angle))) for vertex in faces[face_index]]
    _write_binary_stl(target, b"Sultana NACA 661-212 snapshot, metres, +X longitudinal", faces)
    digest = sha256(target.read_bytes()).hexdigest()
    return PreparedGeometry(target, digest, len(components), len(canards), PHYSICAL_BODY_LENGTH_M)
