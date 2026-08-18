from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.runtime import VISUAL_ROCKET_MODEL_STL, application_root


class RocketViewport(QWidget):
    """Interactive world scene: the camera stays under user control while the vehicle animates."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plotter = None; self._pv = None; self._samples: list[object] = []
        self._render_enabled = False; self._widget_visible = False; self._closed = False
        self._render_pending = False
        self._visual_scale = 3.0; self._extent = 50.0; self._current_index = 0
        self._terrain = None
        self._rocket_mesh = None; self._rocket_mesh_center = None; self._rocket_mesh_length = 1.0
        self._rocket_actors: dict[str, object] = {}
        self._parachute_actor = None; self._parachute_shroud_actors: list[object] = []; self._parachute_shroud_meshes: list[object] = []
        self._parachute_line_count = 10
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        try:
            import pyvista as pv
            from pyvistaqt import QtInteractor
            # QtInteractor's default auto-update timer keeps rendering even
            # while a tab is hidden.  On Windows that can target a released
            # WGL context and flood the terminal with wglMakeCurrent errors.
            self._pv = pv; self._plotter = QtInteractor(self, auto_update=False)
            self._plotter.suppress_rendering = True
            self._plotter.set_background("#2c83ba", top="#d8f3ff")
            self._plotter.enable_trackball_style()  # camera orbit only; terrain and rocket actors stay fixed in ENU space
            self._plotter.add_axes(line_width=2)
            self._load_rocket_model()
            layout.addWidget(self._plotter.interactor)
        except ImportError:
            fallback = QLabel("Vista 3D no disponible. Ejecuta run_all.py para instalar PyVista y VTK.")
            fallback.setWordWrap(True); layout.addWidget(fallback)

    def set_rendering_enabled(self, enabled: bool) -> None:
        """Allow rendering only while this viewport owns the visible tab."""
        self._render_enabled = bool(enabled) and not self._closed
        self._sync_render_suppression()
        if self._render_enabled and self._widget_visible:
            self._render_scene()

    def showEvent(self, event: object) -> None:
        self._widget_visible = True
        self._sync_render_suppression()
        super().showEvent(event)
        if self._render_enabled:
            QTimer.singleShot(0, self._render_scene)

    def hideEvent(self, event: object) -> None:
        self._widget_visible = False
        self._sync_render_suppression()
        super().hideEvent(event)

    def _sync_render_suppression(self) -> None:
        if self._plotter is not None:
            self._plotter.suppress_rendering = not (
                self._render_enabled and self._widget_visible and not self._closed
            )

    def _render_scene(self) -> None:
        if (
            self._plotter is None
            or getattr(self, "_closed", False)
            or not getattr(self, "_render_enabled", True)
            or not getattr(self, "_widget_visible", True)
        ):
            self._render_pending = True
            return
        self._plotter.suppress_rendering = False
        try:
            self._plotter.render()
            self._render_pending = False
        except RuntimeError:
            # The native window may disappear between a queued Qt event and
            # this call.  Do not retry against an invalid OpenGL context.
            self._render_pending = True

    def shutdown(self) -> None:
        """Stop rendering and release the VTK window before Qt destroys it."""
        if self._closed:
            return
        self._closed = True
        self._render_enabled = False
        self._widget_visible = False
        if self._plotter is not None:
            self._plotter.suppress_rendering = True
            try:
                self._plotter.close()
            except RuntimeError:
                pass
            self._plotter = None

    def _load_rocket_model(self) -> None:
        """Load the supplied assembly once; the procedural rocket remains a safe fallback."""
        if not self._pv:
            return
        model_path = application_root() / "data" / "models" / VISUAL_ROCKET_MODEL_STL
        if not model_path.is_file():
            return
        try:
            mesh = self._pv.read(model_path)
            if mesh.n_points < 3:
                return
            self._rocket_mesh = mesh
            self._rocket_mesh_center = np.asarray(mesh.center, dtype=float)
            self._rocket_mesh_length = max(float(mesh.bounds[5] - mesh.bounds[4]), 1e-6)
        except Exception:
            self._rocket_mesh = None

    def set_parachute_line_count(self, count: int) -> None:
        """Keep the recovery visualization tied to the configured shroud count."""
        self._parachute_line_count = max(1, int(count))

    def set_terrain(self, terrain: object) -> None:
        """Set the georeferenced map mosaic supplied by the asynchronous environment worker."""
        self._terrain = terrain
        if self._plotter and not self._samples:
            self.show_terrain_preview()

    def show_terrain_preview(self) -> None:
        """Render the textured 3D map before a flight exists, keeping the camera interactive."""
        if not self._plotter or self._terrain is None:
            return
        self._extent = max(self._terrain.width_m, self._terrain.height_m) * 0.35
        self._visual_scale = max(12.0, self._extent * 0.090)
        self._plotter.clear(); self._clear_dynamic_actor_cache(); self._plotter.set_background("#2c83ba", top="#d8f3ff"); self._plotter.add_axes(line_width=2)
        self._add_world(np.asarray([[0.0, 0.0, 0.0]], dtype=float))
        self._plotter.add_text("Mapa 3D texturizado: arrastra el mapa superior para actualizarlo", font_size=10, color="#102030", name="title")
        self._set_orbit_camera(np.array([0.0, 0.0, 0.0]), min(max(self._extent * 0.9, 85.0), 420.0))
        self._render_scene()

    def show_result(self, result: object) -> None:
        if not self._plotter or not result.samples: return
        self._samples = list(result.samples)
        points = np.asarray([sample.position_enu_m for sample in self._samples], dtype=float)
        self._extent = max(float(np.ptp(points, axis=0).max()), 50.0)
        self._visual_scale = max(12.0, self._extent * 0.090)  # intentionally enlarged for legibility over the map
        points = points.copy()
        points[-1, 2] = -0.24  # touchdown is rendered on the textured ground plane
        self._plotter.clear(); self._clear_dynamic_actor_cache(); self._plotter.set_background("#2c83ba", top="#d8f3ff"); self._plotter.add_axes(line_width=2)
        self._add_world(points)
        parachute_index = next((index for index, sample in enumerate(self._samples) if sample.parachute_deployed), None)
        if parachute_index is None:
            self._add_trajectory(points, "#e2772c", "trayectoria-completa")
        else:
            self._add_trajectory(points[:parachute_index + 1], "#e2772c", "ascenso-propulsado")
            self._add_trajectory(points[parachute_index:], "#13a56d", "descenso-paracaidas")
            opening = points[parachute_index]
            self._plotter.add_mesh(self._pv.Sphere(center=opening, radius=self._visual_scale * 0.26), color="#ffb000", name="parachute-opening")
            self._plotter.add_point_labels([opening], ["APERTURA DE PARACAIDAS"], point_size=0, font_size=13, text_color="#3b2a16", name="opening-label")
        self._plotter.add_mesh(self._pv.Sphere(center=points[0], radius=self._visual_scale * 0.16), color="#34e59a", name="launch-marker")
        landing_pin = self._pv.Cone(center=points[-1] + np.array([0.0, 0.0, self._visual_scale * 0.42]), direction=(0, 0, 1), radius=self._visual_scale * 0.22, height=self._visual_scale * 0.84)
        self._plotter.add_mesh(landing_pin, color="#e23a2e", name="landing-marker")
        self._plotter.add_mesh(self._pv.Sphere(center=points[-1], radius=self._visual_scale * 0.13), color="#ffca28", name="landing-contact")
        self._plotter.add_point_labels([points[0], points[-1]], ["DESPEGUE", "ATERRIZAJE"], point_size=0, font_size=13, text_color="#3b2a16", name="site-labels")
        self._plotter.add_text("Escena 3D interactiva: arrastra para girar, rueda para zoom", font_size=10, color="#102030", name="title")
        self.set_sample_index(0)
        self._set_orbit_camera(points[0], min(max(self._visual_scale * 6.0, 85.0), 360.0))

    def _add_trajectory(self, points: np.ndarray, color: str, name: str) -> None:
        """Add a thick tube so ascent/descent remains visible over a textured terrain map."""
        if len(points) < 2:
            return
        stride = max(1, len(points) // 1600)
        visible = points[::stride]
        if not np.array_equal(visible[-1], points[-1]):
            visible = np.vstack((visible, points[-1]))
        spline = self._pv.Spline(visible, n_points=len(visible))
        tube = spline.tube(radius=max(0.45, self._visual_scale * 0.035), n_sides=12)
        self._plotter.add_mesh(tube, color=color, opacity=0.72, smooth_shading=True, name=name, reset_camera=False)

    def _add_world(self, points: np.ndarray) -> None:
        center = np.array([np.mean(points[:, 0]), np.mean(points[:, 1]), 0.0])
        ground_size = max(self._extent * 1.8, 120.0)
        if self._terrain is not None:
            terrain_center = self._terrain_center_in_launch_enu()
            map_plane = self._pv.Plane(
                center=(terrain_center[0], terrain_center[1], -0.3), direction=(0, 0, 1),
                i_size=self._terrain.width_m, j_size=self._terrain.height_m,
            )
            texture = self._pv.numpy_to_texture(self._terrain.pixels_rgb)
            # Bilinear sampling removes the blocky appearance between the
            # high-resolution mosaics requested from the 2D map zoom level.
            texture.interpolate = True
            texture.repeat = False
            self._plotter.add_mesh(map_plane, texture=texture, name="satellite-map", lighting=False)
            zoom = getattr(self._terrain, "zoom", None)
            detail = f" (zoom {zoom})" if zoom is not None else ""
            self._plotter.add_text(f"Mapa 3D: {self._terrain.attribution}{detail}", position="lower_left", font_size=8, color="#203040", name="map-attribution")
        else:
            ground = self._pv.Plane(center=center, direction=(0, 0, 1), i_size=ground_size, j_size=ground_size)
            self._plotter.add_mesh(ground, color="#507a39", opacity=0.96, name="ground")
        launch = points[0].copy(); launch[2] = 0.0
        rail = self._pv.Cylinder(center=launch + np.array([0.0, 0.0, self._visual_scale * 0.25]), direction=(0, 0, 1), radius=self._visual_scale * 0.05, height=self._visual_scale * 0.5)
        self._plotter.add_mesh(rail, color="#3c4045", name="launch_rail")
        rng = np.random.default_rng(42)
        for index in range(8):
            offset = rng.uniform(-0.45, 0.45, 3) * np.array([ground_size, ground_size, self._extent * 0.4])
            offset[2] = rng.uniform(self._extent * 0.15, self._extent * 0.65)
            cloud = self._pv.Sphere(center=center + offset, radius=max(4.0, self._extent * 0.04))
            cloud.scale((2.4, 1.3, 0.5), inplace=True)
            self._plotter.add_mesh(cloud, color="white", opacity=0.50, name=f"cloud-{index}")
        if any(sample.rain_impact_force_n > 1e-6 for sample in self._samples):
            for index in range(70):
                start = center + rng.uniform(-0.4, 0.4, 3) * np.array([ground_size, ground_size, self._extent])
                start[2] = rng.uniform(self._extent * 0.1, self._extent * 0.9)
                rain = self._pv.Line(start, start + np.array([0.0, 0.0, -max(5.0, self._extent * 0.08)]))
                self._plotter.add_mesh(rain, color="#6bb9ff", line_width=1, opacity=0.55, name=f"rain-{index}")

    def _terrain_center_in_launch_enu(self) -> np.ndarray:
        """Place a panned OSM raster relative to the selected launch site.

        Flight coordinates are ENU with the first sample as their geographic
        origin.  A terrain raster carries the map center used to download it,
        so its offset must be added before drawing; otherwise panning the 2D
        map visually drags the 3D launch point across the texture.
        """
        if self._terrain is None:
            return np.zeros(2)
        center = np.array((float(self._terrain.center_east_m), float(self._terrain.center_north_m)))
        if not self._samples:
            return center
        launch = self._samples[0]
        terrain_lat = float(getattr(self._terrain, "reference_latitude_deg", 0.0))
        terrain_lon = float(getattr(self._terrain, "reference_longitude_deg", 0.0))
        if not terrain_lat and not terrain_lon:
            return center
        earth_radius_m = 6_378_137.0
        launch_lat = float(launch.latitude_deg); launch_lon = float(launch.longitude_deg)
        east = math.radians(terrain_lon - launch_lon) * earth_radius_m * math.cos(math.radians(launch_lat))
        north = math.radians(terrain_lat - launch_lat) * earth_radius_m
        return center + np.array((east, north))

    @staticmethod
    def _basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        forward = direction / max(np.linalg.norm(direction), 1e-9)
        reference = np.array([0.0, 0.0, 1.0]) if abs(forward[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
        right = np.cross(forward, reference); right /= max(np.linalg.norm(right), 1e-9)
        up = np.cross(right, forward); up /= max(np.linalg.norm(up), 1e-9)
        return forward, right, up

    def set_sample_index(self, index: int) -> None:
        if not self._plotter or not self._samples or not 0 <= index < len(self._samples): return
        self._current_index = index
        sample = self._samples[index]; position = np.asarray(sample.position_enu_m, dtype=float)
        velocity = np.asarray(sample.velocity_enu_mps, dtype=float)
        if np.linalg.norm(velocity) < 1e-6: velocity = np.array([0.0, 0.0, 1.0])
        forward, right, up = self._basis(velocity); scale = self._visual_scale
        self._add_rocket_model(position, forward, right, up, sample.canard_deflection_rad, scale)
        self._add_parachute(position, forward, scale, sample.parachute_deployed)
        # Camera deliberately does not move: QtInteractor remains free for user rotation/zoom.
        self._render_scene()

    def center_on_rocket(self) -> None:
        """Move only the camera to the current vehicle sample; the simulation state is unchanged."""
        if not self._plotter or not self._samples:
            return
        position = np.asarray(self._samples[self._current_index].position_enu_m, dtype=float)
        self._set_orbit_camera(position, max(self._visual_scale * 4.2, 35.0))
        self._render_scene()

    def _set_orbit_camera(self, focus: np.ndarray, distance: float) -> None:
        """Set a nearby, north-up starting view; later gestures affect only the camera."""
        if not self._plotter:
            return
        camera = focus + np.array([distance, -distance, distance * 0.72])
        self._plotter.camera_position = [camera.tolist(), focus.tolist(), [0.0, 0.0, 1.0]]
        self._plotter.camera.SetViewUp(0.0, 0.0, 1.0)

    def _clear_dynamic_actor_cache(self) -> None:
        self._rocket_actors.clear()
        self._parachute_actor = None
        self._parachute_shroud_actors.clear(); self._parachute_shroud_meshes.clear()

    @staticmethod
    def _rotation_from_z(forward: np.ndarray) -> np.ndarray:
        """Rotation matrix mapping the OBJ's native +Z axis onto the velocity vector."""
        target = forward / max(np.linalg.norm(forward), 1e-9)
        source = np.array([0.0, 0.0, 1.0])
        axis = np.cross(source, target); axis_norm = np.linalg.norm(axis); dot = float(np.clip(np.dot(source, target), -1.0, 1.0))
        if axis_norm < 1e-8:
            return np.eye(3) if dot >= 0.0 else np.diag((1.0, -1.0, -1.0))
        axis /= axis_norm
        skew = np.array(((0.0, -axis[2], axis[1]), (axis[2], 0.0, -axis[0]), (-axis[1], axis[0], 0.0)))
        return np.eye(3) + skew * axis_norm + skew @ skew * (1.0 - dot)

    def _ensure_rocket_actors(self) -> None:
        if self._rocket_actors or self._rocket_mesh is None:
            return
        local = self._rocket_mesh.copy(deep=True)
        local.translate(-self._rocket_mesh_center, inplace=True)
        group_ids = local.cell_data.get("GroupIds")
        if group_ids is None:
            self._rocket_actors["rocket-model"] = self._plotter.add_mesh(local, color="#1976d2", smooth_shading=True, name="rocket-model", reset_camera=False)
            return
        for name, groups, color, metallic in (
            ("rocket-body", (0,), "#1976d2", 0.18), ("rocket-copper", (1, 9), "#d86b16", 0.35),
            ("rocket-collar", (2, 7, 8), "#8eb6d8", 0.45), ("rocket-canards", (3, 4, 5, 6, 10, 11, 12, 13), "#17212b", 0.12),
        ):
            cells = np.flatnonzero(np.isin(group_ids, groups))
            if len(cells):
                self._rocket_actors[name] = self._plotter.add_mesh(local.extract_cells(cells), color=color, smooth_shading=True, metallic=metallic, roughness=0.32, name=name, reset_camera=False)

    def _add_rocket_model(self, position: np.ndarray, forward: np.ndarray, right: np.ndarray, up: np.ndarray, deflections: object, scale: float) -> None:
        if self._rocket_mesh is not None:
            self._ensure_rocket_actors()
            transform = np.eye(4)
            transform[:3, :3] = self._rotation_from_z(forward) * ((2.10 * scale) / self._rocket_mesh_length)
            transform[:3, 3] = position
            for actor in self._rocket_actors.values():
                actor.user_matrix = transform
                actor.SetVisibility(True)
            return
        # Procedural fallback: white PVC body, copper nose and tail fins, upper black movable canards.
        body = self._pv.Cylinder(center=position - forward * (0.10 * scale), direction=forward, radius=0.12 * scale, height=0.92 * scale)
        nose = self._pv.Cone(center=position + forward * (0.53 * scale), direction=forward, radius=0.12 * scale, height=0.34 * scale)
        collar = self._pv.Cone(center=position + forward * (0.03 * scale), direction=-forward, radius=0.20 * scale, height=0.16 * scale)
        self._plotter.add_mesh(body, color="#f2f2ed", smooth_shading=True, name="rocket-body", reset_camera=False)
        self._plotter.add_mesh(nose, color="#b5611c", smooth_shading=True, name="rocket-nose", reset_camera=False)
        self._plotter.add_mesh(collar, color="#b8bbb7", smooth_shading=True, name="canard-collar", reset_camera=False)
        radial_axes = (up, right, -up, -right)
        tail_center = position - forward * (0.47 * scale)
        for fin_index, radial in enumerate(radial_axes):
            fin_center = tail_center + radial * (0.22 * scale)
            tail_fin = self._pv.Plane(center=fin_center, direction=radial, i_size=0.56 * scale, j_size=0.44 * scale)
            self._plotter.add_mesh(tail_fin, color="#b85f18", name=f"tail-fin-{fin_index}", reset_camera=False)
        for canard_index, (radial, deflection) in enumerate(zip(radial_axes, deflections)):
            canard_center = position + forward * (0.02 * scale) + radial * (0.16 * scale)
            canard = self._pv.Plane(center=canard_center, direction=radial, i_size=0.42 * scale, j_size=0.18 * scale)
            canard.rotate_vector(forward, float(np.degrees(deflection)), point=canard_center, inplace=True)
            self._plotter.add_mesh(canard, color="#111111", show_edges=True, edge_color="#29d9ff", name=f"canard-{canard_index}", reset_camera=False)

    def _add_parachute(self, position: np.ndarray, forward: np.ndarray, scale: float, deployed: bool) -> None:
        if self._parachute_actor is None:
            canopy = self._pv.Sphere(radius=1.0, start_phi=0, end_phi=90)
            self._parachute_actor = self._plotter.add_mesh(canopy, color="#e91e63", opacity=0.96, smooth_shading=True, name="parachute", reset_camera=False)
            for index in range(self._parachute_line_count):
                shroud = self._pv.Line((0.0, 0.0, 0.0), (0.0, 0.0, 0.01))
                self._parachute_shroud_meshes.append(shroud)
                self._parachute_shroud_actors.append(self._plotter.add_mesh(shroud, color="#ffd166", line_width=3, name=f"shroud-{index}", reset_camera=False))
        if not deployed:
            self._parachute_actor.SetVisibility(False)
            for actor in self._parachute_shroud_actors: actor.SetVisibility(False)
            return
        canopy_center = position + np.array([0.0, 0.0, 1.20 * scale])
        canopy_transform = np.eye(4); canopy_transform[:3, :3] *= 0.92 * scale; canopy_transform[:3, 3] = canopy_center
        self._parachute_actor.user_matrix = canopy_transform; self._parachute_actor.SetVisibility(True)
        for index, angle in enumerate(np.linspace(0, 2 * np.pi, self._parachute_line_count, endpoint=False)):
            anchor = canopy_center + np.array([np.cos(angle), np.sin(angle), -0.25]) * 0.72 * scale
            self._parachute_shroud_meshes[index].points = np.asarray((anchor, position + forward * 0.08 * scale))
            self._parachute_shroud_actors[index].SetVisibility(True)
