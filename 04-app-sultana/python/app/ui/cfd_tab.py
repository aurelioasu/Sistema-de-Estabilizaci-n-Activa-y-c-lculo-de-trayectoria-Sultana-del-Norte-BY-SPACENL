from __future__ import annotations

import copy
import math
import re
from bisect import bisect_left
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                               QFileDialog, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea,
                               QListWidget, QListWidgetItem, QRadioButton, QSlider, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.runtime import application_root
from app.services.cfd import (CfdCaseRequest, DockerStatus, export_result_bundle, parse_result,
                               select_flight_phase_snapshots)
from app.services.config_loader import load_aero_table, load_mass_curve, load_thrust_curve
from .cfd_viewport import CFD_VISUAL_MODES, CfdViewport
from .context_help import HelpButton, attach_help, help_label


def canard_correction_label(
    current: tuple[float, float, float, float],
    previous: tuple[float, float, float, float] | None = None,
) -> str:
    """Describe the stabilizing action encoded by the four real PID deflections."""
    pitch = 0.5 * (current[0] - current[2])
    yaw = 0.5 * (current[1] - current[3])
    actions: list[str] = []
    if abs(pitch) >= 0.05:
        actions.append(f"pitch {'+' if pitch > 0 else '−'}{abs(pitch):.2f}°")
    if abs(yaw) >= 0.05:
        actions.append(f"yaw {'+' if yaw > 0 else '−'}{abs(yaw):.2f}°")
    if not actions:
        actions.append("mantener estable")
    if previous is not None:
        movements = [
            f"C{index + 1}{'↑' if value > old else '↓'}"
            for index, (value, old) in enumerate(zip(current, previous))
            if abs(value - old) >= 0.03
        ]
        if movements:
            actions.append(" ".join(movements))
    return " · ".join(actions)


def body_relative_velocity_from_sample(sample: object) -> tuple[float, float, float] | None:
    """Return air velocity relative to the rocket in body axes.

    Native telemetry stores ``vehicle - wind``.  A stationary CFD domain
    requires the opposite vector, ``wind - vehicle``.
    """
    try:
        relative = tuple(float(value) for value in sample.relative_velocity_enu_mps)
        quaternion = sample.quaternion
        if len(relative) != 3:
            return None
        w, x, y, z = (float(quaternion.w), float(quaternion.x), float(quaternion.y), float(quaternion.z))
    except (AttributeError, TypeError, ValueError):
        return None
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm < 1e-12:
        return None
    w, x, y, z = (w / norm, x / norm, y / norm, z / norm)
    rotation = (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )
    # R is body -> ENU, therefore U_body = -R^T (vehicle - wind).
    return tuple(-sum(rotation[row][column] * relative[row] for row in range(3)) for column in range(3))


def cfd_frame_value(frame: object, name: str, default: object = None) -> object:
    """Support typed CfdFrame values and legacy dictionaries in an open UI."""
    if isinstance(frame, dict):
        return frame.get(name, default)
    return getattr(frame, name, default)


class CfdTab(QWidget):
    run_requested = Signal(object, object, object, object, object)  # request, vehicle, environment, tables, viewport
    save_requested = Signal(str, object, object, object)
    scenario_activated = Signal(object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vehicle: dict[str, Any] = {}; self._environment: dict[str, Any] = {}
        self._flight_schedule: tuple[tuple[float, float, float, float, float], ...] = ()
        self._flight_speed_schedule: tuple[tuple[float, float], ...] = ()
        self._flight_inlet_velocity_schedule: tuple[tuple[float, float, float, float], ...] = ()
        self._flight_details: tuple[dict[str, Any], ...] = ()
        self._phase_snapshots: tuple[object, ...] = ()
        self._phase_snapshot_error = "Ejecuta el vuelo 6-DoF para seleccionar los cinco snapshots de fase."
        self._completed_snapshot_results: tuple[object, ...] = ()
        self._history_row_indices: list[int] = []
        self._selected_history_row = -1
        self._weather_wind_enu = (0.0, 0.0, 0.0)
        self._weather_source = "escenario base"
        self._controls: dict[str, QDoubleSpinBox] = {}; self._playback_mode = "canards"; self._preview_dirty = False
        self._cfd_frame_count = 0
        root = QVBoxLayout(self)
        banner = QLabel("LABORATORIO DE FLUJO · Vista previa ilustrativa y CFD detallado/OpenFOAM son modos distintos. Valida malla, convergencia y sensibilidad antes de diseñar.")
        banner.setWordWrap(True); root.addWidget(banner)
        splitter = QSplitter(Qt.Horizontal); root.addWidget(splitter, 1)
        left = QWidget(); controls = QVBoxLayout(left)
        self.case_name = QLineEdit("canards-ensayo")
        self.surface_stl_path = QLineEdit()
        self.surface_stl_path.setPlaceholderText("Opcional: STL cerrado sólo para la malla CFD (no cambia masa ni CG)")
        self.surface_stl_path.setToolTip("Selecciona una superficie STL cerrada para OpenFOAM, en metros y con el eje longitudinal en +X. Las propiedades físicas proceden del modelo activo, no del STL.")
        self.surface_stl_browse = QPushButton("Elegir STL…")
        self.surface_stl_browse.clicked.connect(self._choose_surface_stl)
        surface_row = QWidget(); surface_layout = QHBoxLayout(surface_row)
        surface_layout.setContentsMargins(0, 0, 0, 0); surface_layout.addWidget(self.surface_stl_path, 1); surface_layout.addWidget(self.surface_stl_browse)
        self.snapshot_list = QListWidget(); self.snapshot_list.setMinimumHeight(132)
        self.snapshot_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.snapshot_list.setToolTip("Cinco momentos fijos derivados automáticamente de la trayectoria 6-DoF.")
        case_form = QFormLayout()
        case_form.addRow(help_label("Nombre base", "Prefijo usado para identificar las carpetas y resultados de los cinco casos OpenFOAM."), self.case_name)
        case_form.addRow(help_label("Modo", "Cinco estados estacionarios seleccionados desde fases representativas del vuelo 6-DoF."), QLabel("5 snapshots estáticos de fase · simpleFoam"))
        case_form.addRow(help_label("Superficie CFD STL", "Sólo define la superficie mallada por OpenFOAM. Para cambiar masa, CG y dimensiones usa un .ork en la pestaña principal."), surface_row)
        case_form.addRow(help_label("Snapshots automáticos", "Despegue, ascenso intermedio, paracaídas, descenso intermedio y aterrizaje."), self.snapshot_list)
        self.case_options = QGroupBox("Caso OpenFOAM"); self.case_options.setLayout(case_form)
        form = QFormLayout()
        self.speed = self._spin(45, 0, 450); self.alpha = self._spin(0, -45, 45); self.beta = self._spin(0, -45, 45); self.rain = self._spin(0, 0, 100)
        tunnel_help = (
            "Velocidad relativa de entrada usada por la vista previa y el caso CFD.",
            "Ángulo vertical entre el flujo y el eje longitudinal del cohete.",
            "Ángulo lateral entre el flujo y el eje longitudinal del cohete.",
            "Intensidad de lluvia utilizada para estimar la modificación aerodinámica.",
        )
        for (label, widget), explanation in zip((("Velocidad aire (m/s)", self.speed), ("Ángulo de ataque α (°)", self.alpha), ("Deriva β (°)", self.beta), ("Lluvia (mm/h)", self.rain)), tunnel_help):
            form.addRow(help_label(label, explanation), widget)
        env_group = QGroupBox("Condiciones de túnel"); env_group.setLayout(form); controls.addWidget(env_group)
        canard_form = QFormLayout(); self.canards = [self._spin(0, -15, 15, decimals=2, step=0.1) for _ in range(4)]
        canard_names = ("C1 superior", "C2 derecha", "C3 inferior", "C4 izquierda")
        for index, widget in enumerate(self.canards):
            canard_form.addRow(
                help_label(f"{canard_names[index]} (−15° a +15°)", "Deflexión física del canard. Positivo/negativo cambia la fuerza y el momento alrededor del cohete."),
                widget,
            )
        canard_group = QGroupBox("Canards articulados · rango físico ±15°"); canard_group.setLayout(canard_form); controls.addWidget(canard_group)
        self.layer_group, self.layer_checkboxes = self._layer_controls()
        controls.addWidget(self.layer_group)
        self.scalar_group = QGroupBox("Modelo completo (SI)"); scalar_form = QFormLayout(); self.scalar_group.setLayout(scalar_form)
        for key, label, low, high in (
            ("geometry.diameter_m", "Diámetro (m)", 0.001, 2), ("geometry.reference_area_m2", "Área de referencia (m²)", 0.000001, 20),
            ("geometry.body_length_m", "Longitud (m)", 0.01, 20), ("geometry.cp_m", "CP (m)", 0, 20),
            ("mass.dry_mass_kg", "Masa seca (kg)", 0.01, 200), ("mass.propellant_mass_kg", "Propelente (kg)", 0, 100),
            ("mass.cg_dry_m", "CG seco (m)", 0, 20), ("mass.cg_wet_m", "CG cargado (m)", 0, 20),
            ("mass.inertia_dry_kg_m2.0", "Ixx seca", 0.000001, 100), ("mass.inertia_dry_kg_m2.1", "Iyy seca", 0.000001, 100),
            ("mass.inertia_dry_kg_m2.2", "Izz seca", 0.000001, 100), ("mass.inertia_wet_kg_m2.0", "Ixx cargada", 0.000001, 100),
            ("mass.inertia_wet_kg_m2.1", "Iyy cargada", 0.000001, 100), ("mass.inertia_wet_kg_m2.2", "Izz cargada", 0.000001, 100),
            ("aerodynamics.cd_base", "Cd base", 0.01, 5), ("aerodynamics.body_cn_alpha_per_rad", "Cnα cuerpo", 0, 50),
            ("aerodynamics.cd_alpha2", "Cd α²", 0, 50), ("aerodynamics.angular_damping_nm_per_rad_s", "Amortiguamiento", 0, 50),
            ("aerodynamics.canard_area_m2", "Área canard (m²)", 0.000001, 1), ("aerodynamics.canard_arm_m", "Brazo canard (m)", 0, 10),
            ("aerodynamics.canard_cl_alpha_per_rad", "CLα canard", 0, 20), ("actuators.max_canard_deflection_deg", "Límite servo (°)", 0.1, 90),
            ("actuators.max_canard_rate_deg_s", "Velocidad servo (°/s)", 1, 5000), ("actuators.canard_command_delay_s", "Retardo servo (s)", 0, 5),
            ("recovery.parachute_area_m2", "Área paracaídas (m²)", 0.0001, 100), ("recovery.parachute_cd", "Cd paracaídas", 0.01, 10),
            ("recovery.parachute_deploy_delay_s", "Retardo paracaídas (s)", 0, 60), ("recovery.parachute_inflation_time_s", "Inflado paracaídas (s)", 0, 60),
            ("propulsion.burn_time_s", "Tiempo de combustión (s)", 0.01, 60), ("controller.kp", "PID Kp", 0, 100),
            ("controller.ki", "PID Ki", 0, 100), ("controller.kd", "PID Kd", 0, 100),
            ("weather.surface_temperature_k", "Temperatura (K)", 150, 350), ("weather.surface_pressure_pa", "Presión (Pa)", 1000, 120000),
            ("weather.humidity_ratio", "Humedad (0–1)", 0, 1), ("weather.turbulence_intensity_mps", "Ráfagas (m/s)", 0, 100),
            ("weather.rain_cd_delta", "ΔCd lluvia", 0, 10), ("weather.friction_heat_coefficient", "Coef. fricción", 0, 10),
            ("rail.length_m", "Longitud de riel (m)", 0.1, 100), ("rail.elevation_deg", "Elevación de riel (°)", -10, 100), ("rail.azimuth_deg", "Azimut de riel (°)", -360, 360),
            ("sensors.imu_accel_noise_std_mps2", "Ruido IMU", 0, 100), ("sensors.barometer_noise_std_m", "Ruido barómetro", 0, 100), ("sensors.gps_noise_std_m", "Ruido GPS", 0, 100),
        ):
            box = self._spin(0, low, high)
            self._controls[key] = box
            scalar_form.addRow(help_label(label, self._model_field_help(key, label)), box)
        self.apply_preview_button = QPushButton("Aplicar cambios a la vista previa")
        self.apply_preview_button.setToolTip("Actualiza solo la geometría, ángulos y viento ilustrativos. No ejecuta CFD.")
        self.apply_preview_button.clicked.connect(self.apply_preview)
        controls.addWidget(self.apply_preview_button)
        self.tables_group = self._tables()
        controls.addWidget(self.case_options); controls.addWidget(self.scalar_group); controls.addWidget(self.tables_group); controls.addStretch()
        left_scroll = QScrollArea(); left_scroll.setWidgetResizable(True); left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)
        right = QWidget(); right_layout = QVBoxLayout(right); self.viewport = CfdViewport(right); right_layout.addWidget(self.viewport, 1)
        attach_help(self.viewport, "Visor CFD 3D. Representa una magnitud a la vez usando los campos VTK y las fuerzas exportadas por OpenFOAM.")
        view_controls = QHBoxLayout()
        view_controls.addWidget(QLabel("Cámara 3D ◈"))
        self.camera_preset = QComboBox()
        for label, key in (("Isométrica", "isometric"), ("Lateral", "side"), ("Frontal", "front"),
                           ("Superior", "top"), ("Posterior", "rear")):
            self.camera_preset.addItem(label, key)
        self.camera_preset.setToolTip("◈ 3D: cambia la orientación de cámara sin alterar los datos CFD.")
        self.camera_preset.currentIndexChanged.connect(lambda _index: self.viewport.set_camera_preset(str(self.camera_preset.currentData())))
        view_controls.addWidget(self.camera_preset)
        view_controls.addWidget(HelpButton("Cambia solo el punto de vista de la cámara; no modifica los resultados CFD."))
        self.follow_button = QPushButton("Seguir cohete")
        self.follow_button.setCheckable(True); self.follow_button.setToolTip("Mantiene el foco de la cámara en el cohete durante la animación.")
        self.follow_button.toggled.connect(self._set_following)
        view_controls.addWidget(self.follow_button)
        right_layout.addLayout(view_controls)
        cfd_controls = QHBoxLayout(); cfd_controls.addWidget(QLabel("Magnitud CFD destacada"))
        self.cfd_layer = QComboBox()
        for key, (name, unit) in CFD_VISUAL_MODES.items():
            self.cfd_layer.addItem(f"{name} · {unit}", key)
        self.cfd_layer.setEnabled(False); self.cfd_layer.currentIndexChanged.connect(self._cfd_visual_mode_changed)
        cfd_controls.addWidget(self.cfd_layer); cfd_controls.addWidget(HelpButton("Selecciona una sola magnitud CFD y adapta la representación 3D a velocidad, presión, vorticidad, fuerzas o momento.")); cfd_controls.addWidget(QLabel("Se muestra una sola magnitud a la vez con una representación 3D adaptada."), 1)
        right_layout.addLayout(cfd_controls)
        for mode, button in self.layer_checkboxes.items():
            button.toggled.connect(lambda checked, key=mode: checked and self._select_cfd_visual_mode(key))
        self.weather_badge = QLabel("Clima CFD: esperando escenario.")
        self.weather_badge.setWordWrap(True); right_layout.addWidget(self.weather_badge)
        self.readout = QLabel("Vista previa ilustrativa activa. No hay resultados CFD detallados todavía."); self.readout.setWordWrap(True); right_layout.addWidget(self.readout)
        self.status = QLabel("Comprobando Docker…"); self.status.setWordWrap(True); right_layout.addWidget(self.status)
        self.run_announcement = QLabel("CFD detallado listo para ejecutar")
        self.run_announcement.setStyleSheet("padding: 7px; border-radius: 4px; background: #24303a; color: #dcecf5; font-weight: 600;")
        self.run_announcement.setWordWrap(True); right_layout.addWidget(self.run_announcement)
        self.run_progress = QProgressBar(); self.run_progress.setRange(0, 100); self.run_progress.setValue(0)
        self.run_progress.setVisible(False); right_layout.addWidget(self.run_progress)
        self._last_cfd_result: object | None = None
        self.history_table = QTableWidget(0, 9)
        self.history_table.setHorizontalHeaderLabels(
            ["t (s)", "C1 superior", "C2 derecha", "C3 inferior", "C4 izquierda", "Acción PID",
             "q (Pa)", "Sust. (N)", "Arrastre (N)"]
        )
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setMinimumHeight(160); self.history_table.setMaximumHeight(225)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.history_table.cellClicked.connect(self._scrub_history)
        attach_help(self.history_table, "Historial temporal del PID, deflexiones, presión dinámica, sustentación y arrastre de la trayectoria 6-DoF.")
        right_layout.addWidget(self.history_table)
        self.history_detail = QLabel("Ejecuta el vuelo 6-DoF para cargar el historial PID real de los canards.")
        self.history_detail.setWordWrap(True); right_layout.addWidget(self.history_detail)
        actions = QHBoxLayout(); self.run_button = QPushButton("Ejecutar simulación CFD detallada"); self.run_button.setToolTip("Prepara malla y ejecuta OpenFOAM. Puede tardar; las capas resultan solo del VTK exportado."); self.run_button.clicked.connect(self._run); actions.addWidget(self.run_button)
        self.cancel_button = QPushButton("Cancelar"); self.cancel_button.setEnabled(False); actions.addWidget(self.cancel_button)
        self.download_button = QPushButton("Descargar resultados completos")
        self.download_button.setToolTip("Guarda un ZIP con VTK, malla, caso, fuerzas, log y guía de análisis.")
        self.download_button.setEnabled(False); self.download_button.clicked.connect(self._download_results); actions.addWidget(self.download_button)
        self.load_result_button = QPushButton("Cargar último CFD")
        self.load_result_button.setToolTip("Abre el último resultado OpenFOAM ya terminado, sin volver a ejecutarlo.")
        self.load_result_button.clicked.connect(self._load_latest_result); actions.addWidget(self.load_result_button)
        self.snapshot_result_selector = QComboBox()
        self.snapshot_result_selector.setToolTip("Selecciona una de las fases CFD que ya terminaron.")
        self.snapshot_result_selector.setVisible(False)
        self.show_snapshot_button = QPushButton("Mostrar snapshot")
        self.show_snapshot_button.setToolTip("Carga en el visor 3D el snapshot CFD seleccionado, sin volver a simular.")
        self.show_snapshot_button.setVisible(False)
        self.show_snapshot_button.clicked.connect(self._show_selected_snapshot)
        actions.addWidget(self.snapshot_result_selector); actions.addWidget(self.show_snapshot_button)
        self.save_name = QLineEdit("ensayo-cfd"); self.save_button = QPushButton("Guardar escenario"); self.save_button.clicked.connect(self._save); actions.addWidget(self.save_name); actions.addWidget(self.save_button); right_layout.addLayout(actions)
        playback = QHBoxLayout(); self.timeline = QSlider(Qt.Horizontal); self.timeline.setRange(0, 600); self.timeline.valueChanged.connect(self._animate_sample); playback.addWidget(self.timeline, 1)
        playback.addWidget(HelpButton("Recorre el snapshot CFD cargado o la animación temporal de los canards."))
        self.play_cfd = QPushButton("Reproducir CFD"); self.play_cfd.setCheckable(True); self.play_cfd.setEnabled(False)
        self.play_cfd.toggled.connect(self._toggle_cfd_play); playback.addWidget(self.play_cfd)
        self.play_canards = QPushButton("Reproducir canards"); self.play_canards.setCheckable(True); self.play_canards.setEnabled(False)
        self.play_canards.toggled.connect(self._toggle_canards_play); playback.addWidget(self.play_canards); right_layout.addLayout(playback)
        splitter.addWidget(right); splitter.setSizes([480, 1000])
        self._timer = QTimer(self); self._timer.setInterval(1000 // 60); self._timer.timeout.connect(self._next_frame)
        for widget in (self.speed, self.alpha, self.beta, self.rain, *self.canards): widget.valueChanged.connect(self._mark_preview_dirty)

    def set_active(self, active: bool) -> None:
        """Pause every native render source when another laboratory is visible."""
        self.viewport.set_rendering_enabled(active)
        if active:
            return
        self._timer.stop()
        if self.play_cfd.isChecked():
            self.play_cfd.setChecked(False)
        if self.play_canards.isChecked():
            self.play_canards.setChecked(False)

    @staticmethod
    def _layer_controls() -> tuple[QGroupBox, dict[str, QRadioButton]]:
        group = QGroupBox("Magnitud visible · selección exclusiva")
        grid = QGridLayout(group)
        legend = QLabel("Solo se dibuja una magnitud CFD a la vez para mantener el cohete legible y limitar el uso de memoria.")
        legend.setWordWrap(True); grid.addWidget(legend, 0, 0, 1, 3)
        colors = {
            "velocity": "#28c9ff", "pressure": "#ffdd33", "vorticity": "#cf35f4",
            "turbulence": "#20d9a1",
            "drag": "#ff3b30", "lift": "#47ff78", "side": "#39a8ff",
            "friction": "#ff9f1c", "moment": "#b440ff",
        }
        checks: dict[str, QRadioButton] = {}
        for row, (key, (name, unit)) in enumerate(CFD_VISUAL_MODES.items(), start=1):
            swatch = QLabel("■")
            swatch.setStyleSheet(f"color: {colors[key]}; font-size: 16px;")
            checkbox = QRadioButton(f"{name} · {unit}", group)
            checkbox.setToolTip(f"Mostrar únicamente {name.lower()} en el visor 3D.")
            grid.addWidget(swatch, row, 0)
            grid.addWidget(checkbox, row, 1)
            grid.addWidget(
                HelpButton(f"Muestra únicamente {name.lower()} con la escala y geometría visual apropiadas; oculta las demás magnitudes.", group),
                row,
                2,
            )
            checks[key] = checkbox
        checks["velocity"].setChecked(True)
        return group, checks

    def _tables(self) -> QGroupBox:
        group = QGroupBox("Tablas editables")
        layout = QVBoxLayout(group)
        self.thrust_table = self._table(["t (s)", "Empuje (N)"], 2)
        self.mass_table = self._table(["t", "Prop", "CG", "Ixx", "Iyy", "Izz"], 6)
        self.aero_table = self._table(["Mach", "Cd", "Cnα/rad"], 3)
        for title, table in (("Empuje", self.thrust_table), ("Masa / inercia", self.mass_table), ("Aerodinámica", self.aero_table)):
            explanations = {
                "Empuje": "Curva editable de fuerza del motor respecto al tiempo.",
                "Masa / inercia": "Evolución de propelente, centro de gravedad y momentos de inercia.",
                "Aerodinámica": "Coeficientes Cd y Cnα interpolados según el número de Mach.",
            }
            layout.addWidget(help_label(title, explanations[title])); layout.addWidget(table)
            attach_help(table, explanations[title] + " Modificar una celda cambia el escenario usado en la siguiente simulación.")
        return group

    @staticmethod
    def _model_field_help(key: str, label: str) -> str:
        section = key.split(".", 1)[0]
        purpose = {
            "geometry": "Define la geometría y las referencias usadas para malla, estabilidad y fuerzas.",
            "mass": "Define masa, centro de gravedad o inercia y modifica la respuesta 6-DoF.",
            "aerodynamics": "Modifica el modelo aerodinámico de cuerpo y canards.",
            "actuators": "Limita la rapidez, amplitud o retraso de los servos de canards.",
            "recovery": "Configura el despliegue y comportamiento del paracaídas.",
            "propulsion": "Configura la fase propulsada del vuelo.",
            "controller": "Ganancia del controlador PID de actitud.",
            "weather": "Condición ambiental que modifica densidad, perturbaciones o cargas.",
            "rail": "Geometría y orientación inicial del riel de lanzamiento.",
            "sensors": "Ruido usado por el sensor simulado y el estimador EKF.",
        }.get(section, "Parámetro del modelo físico.")
        return f"{label}: {purpose} Se aplica al siguiente cálculo y no altera resultados ya terminados."

    @staticmethod
    def _table(headers: list[str], columns: int) -> QTableWidget:
        table = QTableWidget(0, columns); table.setHorizontalHeaderLabels(headers); table.setMinimumHeight(115)
        return table

    @staticmethod
    def _spin(value: float, minimum: float, maximum: float, *, decimals: int = 3, step: float = 0.1) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum); box.setDecimals(decimals); box.setSingleStep(step)
        box.setKeyboardTracking(False); box.setMinimumWidth(118); box.setAccelerated(True)
        box.setValue(value)
        return box

    def set_scenario(self, scenario: object) -> None:
        self._vehicle, self._environment = copy.deepcopy(scenario.vehicle), copy.deepcopy(scenario.environment)
        for key, control in self._controls.items():
            parts = key.split("."); section, field = parts[0], parts[1]
            mapping = self._vehicle if section not in {"weather", "controller", "rail", "sensors"} else self._environment
            value = mapping[section][field]
            if len(parts) == 3:
                value = value[int(parts[2])]
            control.setValue(float(value))
        weather = self._environment["weather"]
        self._weather_wind_enu = tuple(float(value) for value in weather.get("mean_wind_enu_mps", [0, 0, 0]))
        self._weather_source = str(weather.get("source", "escenario base"))
        self._set_rows(self.thrust_table, load_thrust_curve(scenario)); self._set_rows(self.mass_table, load_mass_curve(scenario)); self._set_rows(self.aero_table, load_aero_table(scenario))
        self._preview()

    @staticmethod
    def _set_rows(table: QTableWidget, rows: object) -> None:
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            flattened = list(row[:3]) + list(row[3]) if len(row) == 4 and isinstance(row[3], list) else list(row)
            for column, value in enumerate(flattened): table.setItem(row_index, column, QTableWidgetItem(f"{float(value):.8g}"))
        table.resizeColumnsToContents()

    def set_docker_status(self, status: DockerStatus) -> None:
        self.status.setText(
            status.message + (" Imagen OpenFOAM lista." if status.image_present else " La imagen se descargará al ejecutar.")
            + f" Backend: {status.execution_backend}."
        )

    def apply_weather_profile(self, weather: object) -> None:
        """Use the map/weather result as a starting point; controls remain local."""
        if not self._environment:
            return
        values = {
            "surface_temperature_k": float(weather.surface_temperature_k),
            "surface_pressure_pa": float(weather.surface_pressure_pa),
            "humidity_ratio": float(weather.humidity_ratio),
            "turbulence_intensity_mps": max(
                float(self._environment.get("weather", {}).get("turbulence_intensity_mps", 0.0)),
                max(0.0, float(getattr(weather, "wind_gust_mps", 0.0)) - np_norm(weather.mean_wind_enu_mps)) / (2.0 ** 0.5),
            ),
        }
        self._environment["weather"].update(values)
        for field, value in values.items():
            control = self._controls.get(f"weather.{field}")
            if control: control.setValue(value)
        self._environment["weather"]["rain_rate_mm_h"] = float(weather.rain_rate_mm_h)
        self._weather_wind_enu = tuple(float(value) for value in weather.mean_wind_enu_mps)
        self._weather_source = str(weather.source)
        self.rain.setValue(float(weather.rain_rate_mm_h))
        self._preview()

    def set_flight_result(self, result: object) -> None:
        """Use the native 6-DoF/PID canard history for a transient CFD replay."""
        samples = tuple(result.samples)
        self._flight_schedule = tuple(
            (float(sample.time_s), *(float(math.degrees(angle)) for angle in sample.canard_deflection_rad))
            for sample in samples
        )
        self._flight_speed_schedule = tuple(
            (float(sample.time_s), max(0.0, float(getattr(sample, "airspeed_mps", 0.0))))
            for sample in samples
        )
        inlet_rows = []
        for sample in samples:
            body_velocity = body_relative_velocity_from_sample(sample)
            if body_velocity is not None:
                inlet_rows.append((float(sample.time_s), *body_velocity))
        self._flight_inlet_velocity_schedule = tuple(inlet_rows)
        details: list[dict[str, Any]] = []
        previous: tuple[float, float, float, float] | None = None
        for sample, schedule in zip(samples, self._flight_schedule):
            canards = tuple(schedule[1:])
            body_velocity = body_relative_velocity_from_sample(sample) or (0.0, 0.0, 0.0)
            details.append({
                "time_s": schedule[0],
                "canards": canards,
                "action": canard_correction_label(canards, previous),
                "dynamic_pressure_pa": float(getattr(sample, "dynamic_pressure_pa", 0.0)),
                "lift_n": float(getattr(sample, "canard_lift_n", 0.0)),
                "drag_n": float(getattr(sample, "drag_force_n", 0.0)),
                "wind_enu_mps": tuple(float(value) for value in getattr(sample, "wind_enu_mps", self._weather_wind_enu)),
                "temperature_k": float(getattr(sample, "air_temperature_k", self._weather_value("surface_temperature_k", 288.15))),
                "pressure_pa": float(getattr(sample, "air_pressure_pa", self._weather_value("surface_pressure_pa", 101325.0))),
                "humidity_ratio": float(getattr(sample, "air_relative_humidity", self._weather_value("humidity_ratio", 0.0))),
                "density_kg_m3": float(getattr(sample, "air_density_kg_m3", 0.0)),
                "lateral_velocity_mps": float(body_velocity[1]),
                "body_velocity_mps": tuple(float(value) for value in body_velocity),
                "parachute_deployed": bool(getattr(sample, "parachute_deployed", False)),
                "parachute_cds_m2": max(0.0, float(getattr(sample, "parachute_cds_m2", 0.0))),
                "cg_from_nose_m": float(getattr(sample, "cg_m", self._vehicle.get("mass", {}).get("cg_dry_m", 0.0))),
            })
            previous = canards
        self._flight_details = tuple(details)
        self._populate_history_table()
        self._populate_phase_snapshots()
        self.play_canards.setEnabled(bool(self._flight_schedule))
        if self._flight_schedule:
            if self._phase_snapshots:
                self.status.setText(
                    f"Historial PID real cargado: {len(self._flight_schedule)} muestras. "
                    "Se seleccionaron cinco snapshots CFD estáticos de las fases de vuelo."
                )
            else:
                self.status.setText(self._phase_snapshot_error)
            self._animate_sample(0)

    def _populate_phase_snapshots(self) -> None:
        self.snapshot_list.clear()
        try:
            self._phase_snapshots = select_flight_phase_snapshots(self._flight_details)
            self._phase_snapshot_error = ""
        except ValueError as exc:
            self._phase_snapshots = ()
            self._phase_snapshot_error = str(exc)
            self.snapshot_list.addItem(f"No disponible: {exc}")
            return
        for ordinal, selection in enumerate(self._phase_snapshots, start=1):
            self.snapshot_list.addItem(f"{ordinal}/5 · t={selection.source_time_s:.2f} s · {selection.reason}")

    def _mark_preview_dirty(self, _value: float | None = None) -> None:
        self._preview_dirty = True
        if hasattr(self, "apply_preview_button"):
            self.apply_preview_button.setText("Aplicar cambios a la vista previa •")
        if hasattr(self, "readout"):
            self.readout.setText("Cambios sin aplicar. La vista previa sigue mostrando la configuración anterior.")

    def apply_preview(self) -> None:
        """Apply an intentional, non-CFD preview update once controls are complete."""
        wind = self._weather_wind_enu
        temperature = self._weather_value("surface_temperature_k", 288.15)
        pressure = self._weather_value("surface_pressure_pa", 101325.0)
        humidity = self._weather_value("humidity_ratio", 0.0)
        self.viewport.set_weather_context(wind, temperature, pressure, humidity, self._weather_source)
        self.weather_badge.setText(
            f"CLIMA HEREDADO · {self._weather_source} · viento ENU "
            f"({wind[0]:.2f}, {wind[1]:.2f}, {wind[2]:.2f}) m/s · "
            f"{temperature - 273.15:.1f} °C · {pressure:.0f} Pa · HR {humidity * 100:.0f}% · "
            f"lluvia {self.rain.value():.1f} mm/h"
        )
        self.viewport.set_conditions(self.speed.value(), self.alpha.value(), self.beta.value(), tuple(box.value() for box in self.canards), self.rain.value())
        self._preview_dirty = False
        self.apply_preview_button.setText("Aplicar cambios a la vista previa")
        self.readout.setText("Vista previa ilustrativa aplicada: geometría, canards y dirección de viento. No representa resultados CFD.")

    def _preview(self) -> None:
        """Compatibility path for loading a scenario; interactive edits are deferred."""
        self.apply_preview()

    def _set_following(self, enabled: bool) -> None:
        self.viewport.set_follow_rocket(enabled)
        self.follow_button.setText("Dejar cámara manual" if enabled else "Seguir cohete")

    def _cfd_visual_mode_changed(self, _index: int) -> None:
        mode = str(self.cfd_layer.currentData())
        button = self.layer_checkboxes.get(mode)
        if button is not None and not button.isChecked():
            blocker = QSignalBlocker(button); button.setChecked(True); del blocker
        self.viewport.set_cfd_layer(mode)

    def _select_cfd_visual_mode(self, mode: str) -> None:
        index = self.cfd_layer.findData(mode)
        if index < 0:
            return
        if self.cfd_layer.currentIndex() != index:
            self.cfd_layer.setCurrentIndex(index)
        else:
            self.viewport.set_cfd_layer(mode)

    def _weather_value(self, field: str, fallback: float) -> float:
        control = self._controls.get(f"weather.{field}")
        if control is not None:
            return float(control.value())
        return float(self._environment.get("weather", {}).get(field, fallback))

    def _populate_history_table(self) -> None:
        count = len(self._flight_details)
        if not count:
            self.history_table.setRowCount(0)
            self._history_row_indices = []
            return
        stride = max(1, math.ceil(count / 240))
        indices = list(range(0, count, stride))
        if indices[-1] != count - 1:
            indices.append(count - 1)
        self._history_row_indices = indices
        self.history_table.setRowCount(len(indices))
        for row, sample_index in enumerate(indices):
            detail = self._flight_details[sample_index]
            canards = detail["canards"]
            values = (
                f"{detail['time_s']:.2f}",
                f"{canards[0]:+.2f}°", f"{canards[1]:+.2f}°",
                f"{canards[2]:+.2f}°", f"{canards[3]:+.2f}°",
                detail["action"],
                f"{detail['dynamic_pressure_pa']:.1f}",
                f"{detail['lift_n']:.3f}",
                f"{detail['drag_n']:.3f}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, sample_index)
                self.history_table.setItem(row, column, item)
        self.history_table.resizeRowsToContents()
        self._selected_history_row = -1

    def _scrub_history(self, row: int, _column: int) -> None:
        if not self._flight_schedule or row >= len(self._history_row_indices):
            return
        index = self._history_row_indices[row]
        frame = round(index / max(1, len(self._flight_schedule) - 1) * self.timeline.maximum())
        self.timeline.setValue(frame)

    def _set_pid_canard_readouts(self, values: tuple[float, float, float, float]) -> None:
        """Mirror the current PID sample in the four controls without restarting the preview."""
        for control, value in zip(self.canards, values):
            blocker = QSignalBlocker(control)
            control.setValue(float(value))
            del blocker

    def _rows(self, table: QTableWidget, columns: int) -> list[list[float]]:
        rows: list[list[float]] = []
        for row in range(table.rowCount()):
            values = []
            for column in range(columns):
                item = table.item(row, column)
                if not item:
                    raise ValueError("Completa todas las celdas de las tablas CFD")
                values.append(float(item.text().replace(",", ".")))
            rows.append(values)
        if len(rows) < 2:
            raise ValueError("Cada tabla debe tener al menos dos filas")
        if any(right[0] <= left[0] for left, right in zip(rows, rows[1:])):
            raise ValueError("La primera columna de cada tabla debe ser estrictamente creciente")
        return rows

    def model_data(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[list[float]]]]:
        vehicle, environment = copy.deepcopy(self._vehicle), copy.deepcopy(self._environment)
        if not vehicle or not environment:
            raise ValueError("Carga primero el escenario base")
        for key, control in self._controls.items():
            parts = key.split("."); section, field = parts[0], parts[1]
            mapping = vehicle if section not in {"weather", "controller", "rail", "sensors"} else environment
            if len(parts) == 3:
                mapping[section][field][int(parts[2])] = control.value()
            else:
                mapping[section][field] = control.value()
        environment["weather"]["rain_rate_mm_h"] = self.rain.value()
        environment["weather"]["mean_wind_enu_mps"] = list(self._weather_wind_enu)
        tables = {"thrust": self._rows(self.thrust_table, 2), "mass": self._rows(self.mass_table, 6), "aero": self._rows(self.aero_table, 3)}
        return vehicle, environment, tables

    def _choose_surface_stl(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, "Elegir superficie CAD para CFD", self.surface_stl_path.text(), "Superficies STL (*.stl);;Todos los archivos (*)",
        )
        if path:
            self.surface_stl_path.setText(path)
            viewport = getattr(self, "viewport", None)
            if viewport is not None and hasattr(viewport, "set_model_path"):
                try:
                    viewport.set_model_path(path)
                except (OSError, RuntimeError, ValueError) as exc:
                    self.surface_stl_path.clear()
                    self.status.setText(f"No se pudo cargar la vista previa del STL: {exc}")
                    return
            self.status.setText("STL seleccionado para CFD y vista previa. Debe estar en metros/+X; no modifica masa, CG ni motor del modelo activo.")

    def clear_flight_result(self) -> None:
        """Invalidate snapshots produced by a different physical rocket."""
        self._flight_schedule = (); self._flight_speed_schedule = (); self._flight_inlet_velocity_schedule = ()
        self._flight_details = (); self._phase_snapshots = (); self._completed_snapshot_results = ()
        self.snapshot_list.clear(); self.snapshot_list.addItem("Ejecuta el vuelo 6-DoF con el modelo activo.")
        self.play_canards.setEnabled(False)

    def _request(self, *, case_name: str, snapshot_time_s: float, snapshot_reason: str) -> CfdCaseRequest:
        if not self._flight_details:
            raise ValueError("Ejecuta una trayectoria 6-DoF completa para obtener snapshots CFD")
        detail = min(self._flight_details, key=lambda item: abs(float(item["time_s"]) - snapshot_time_s))
        canard_values = tuple(float(value) for value in detail["canards"])
        body_velocity = tuple(float(value) for value in detail.get("body_velocity_mps", (0.0, 0.0, 0.0)))
        if len(body_velocity) != 3:
            raise ValueError("La trayectoria no contiene velocidad relativa en ejes del cohete")
        speed = math.sqrt(sum(value * value for value in body_velocity))
        wind = tuple(float(value) for value in detail["wind_enu_mps"])
        temperature, pressure, humidity = (float(detail["temperature_k"]), float(detail["pressure_pa"]), float(detail["humidity_ratio"]))
        vehicle = getattr(self, "_vehicle", {})
        body_length = float(vehicle.get("geometry", {}).get("body_length_m", 0.902))
        cg_body_x = 0.5 * body_length - float(detail.get("cg_from_nose_m", 0.5 * body_length))
        return CfdCaseRequest(
            case_name=case_name,
            mode="snapshot",
            speed_mps=speed,
            alpha_deg=0.0,
            beta_deg=0.0,
            rain_rate_mm_h=self.rain.value(),
            canard_deg=canard_values,
            # La lluvia heredada describe el entorno y la vista ilustrativa.
            # No debe convertir silenciosamente un caso de aire RANS en
            # overInterDyMFoam: ese solver requiere campos y propiedades de
            # fases que este formulario no solicita.  El caso CFD detallado
            # sigue siendo aire monofásico y conserva la tasa de lluvia en su
            # manifiesto para que el resultado sea trazable.
            use_multiphase=False,
            inlet_velocity_schedule=((0.0, *body_velocity),),
            wind_enu_mps=wind,
            weather_source=self._weather_source,
            temperature_k=temperature,
            pressure_pa=pressure,
            humidity_ratio=humidity,
            turbulence_intensity_mps=self._weather_value("turbulence_intensity_mps", 0.0),
            execution_scope="snapshot",
            source_time_start_s=float(snapshot_time_s),
            source_time_end_s=float(snapshot_time_s),
            rocket_stl_path=self.surface_stl_path.text().strip(),
            snapshot_source_time_s=snapshot_time_s,
            snapshot_reason=snapshot_reason,
            recovery_cds_m2=float(detail.get("parachute_cds_m2", 0.0)),
            center_of_gravity_body_m=(cg_body_x, 0.0, 0.0),
        )

    def _requests(self) -> tuple[CfdCaseRequest, ...]:
        selections = select_flight_phase_snapshots(self._flight_details)
        return tuple(self._request(
            case_name=f"{self.case_name.text()}-fase-{ordinal}", snapshot_time_s=selection.source_time_s,
            snapshot_reason=selection.reason,
        ) for ordinal, selection in enumerate(selections, start=1))

    def _run(self) -> None:
        if not self._flight_schedule:
            self.status.setText("Ejecuta una trayectoria 6-DoF completa con recuperación activa para seleccionar los cinco snapshots CFD.")
            return
        selected_surface = self.surface_stl_path.text().strip()
        if selected_surface:
            candidate = Path(selected_surface).expanduser()
            if candidate.suffix.lower() != ".stl" or not candidate.is_file():
                self.status.setText("La superficie CFD elegida no es un archivo STL existente; elige un .stl válido o deja el campo vacío.")
                return
        try:
            vehicle, environment, tables = self.model_data()
        except ValueError as exc:
            self.status.setText(f"Configuración inválida: {exc}"); return
        self.run_button.setEnabled(False); self.cancel_button.setEnabled(True); self.download_button.setEnabled(False)
        self._last_cfd_result = None
        self._completed_snapshot_results = ()
        self._refresh_snapshot_result_selector(None)
        self.run_progress.setVisible(True); self.run_progress.setRange(0, 0)
        self.run_announcement.setText("SIMULACIÓN CFD DETALLADA EN PROCESO · preparando geometría, malla y solver OpenFOAM…")
        self.run_announcement.setStyleSheet("padding: 7px; border-radius: 4px; background: #174d68; color: #ffffff; font-weight: 700;")
        try:
            requests = self._requests()
        except ValueError as exc:
            self.run_button.setEnabled(True); self.cancel_button.setEnabled(False)
            self.status.setText(f"No se pudieron seleccionar snapshots: {exc}")
            return
        if len(requests) != 5:
            self.run_button.setEnabled(True); self.cancel_button.setEnabled(False)
            self.status.setText("No se pudieron obtener los cinco snapshots de fase requeridos.")
            return
        self._active_cfd_end_time = requests[0].transient_end_time_s
        self.status.setText("Se ejecutarán 5 snapshots CFD de fase en serie: despegue, ascenso, paracaídas, descenso y aterrizaje.")
        if requests[0].rain_rate_mm_h > 0.0:
            self.status.setText(
                f"Preparando CFD de aire; lluvia {requests[0].rain_rate_mm_h:.1f} mm/h registrada como condición ambiental (no multifásica)…"
            )
        self.run_requested.emit(requests, vehicle, environment, tables, self.viewport)

    def _save(self) -> None:
        try:
            vehicle, environment, tables = self.model_data()
            self.save_requested.emit(self.save_name.text(), vehicle, environment, tables)
        except ValueError as exc:
            self.status.setText(f"No se guardó: {exc}")

    def set_completed_snapshot_results(self, results: object) -> None:
        """Keep every successful flight-phase case available after the queue ends."""
        self._completed_snapshot_results = tuple(results) if isinstance(results, (tuple, list)) else ()

    def _refresh_snapshot_result_selector(self, active_result: object | None) -> None:
        if not hasattr(self, "snapshot_result_selector") or not hasattr(self, "show_snapshot_button"):
            return
        results = getattr(self, "_completed_snapshot_results", ())
        show_picker = len(results) > 1
        self.snapshot_result_selector.blockSignals(True)
        self.snapshot_result_selector.clear()
        active_index = 0
        for index, result in enumerate(results):
            reason = str(getattr(result, "snapshot_reason", "snapshot"))
            time_s = getattr(result, "snapshot_source_time_s", None)
            label = f"{index + 1}/5 · {reason}"
            if time_s is not None:
                label += f" · t={float(time_s):.2f} s"
            self.snapshot_result_selector.addItem(label, result)
            if result is active_result:
                active_index = index
        self.snapshot_result_selector.setCurrentIndex(active_index)
        self.snapshot_result_selector.blockSignals(False)
        self.snapshot_result_selector.setVisible(show_picker)
        self.show_snapshot_button.setVisible(show_picker)

    def _show_selected_snapshot(self) -> None:
        index = self.snapshot_result_selector.currentIndex()
        if index < 0 or index >= len(self._completed_snapshot_results):
            return
        result = self._completed_snapshot_results[index]
        label = self.snapshot_result_selector.currentText()
        self.set_run_finished(result, f"Mostrando snapshot CFD seleccionado: {label}")

    def set_run_finished(self, result: object | None, message: str) -> None:
        self.run_button.setEnabled(True); self.cancel_button.setEnabled(False); self.status.setText(message)
        self.run_progress.setVisible(True); self.run_progress.setRange(0, 100)
        if result:
            self._refresh_snapshot_result_selector(result)
            frames = tuple(getattr(result, "frames", ()))
            self._cfd_frame_count = len(frames)
            if self._cfd_frame_count:
                # Detailed playback is intentionally discrete: each slider
                # position selects one VTK state actually written by OpenFOAM.
                self._playback_mode = "cfd"
                self.timeline.blockSignals(True)
                self.timeline.setRange(0, self._cfd_frame_count - 1)
                self.timeline.setValue(0)
                self.timeline.blockSignals(False)
            fx, fy, fz = result.force_n; mx, my, mz = result.moment_nm
            recovery = tuple(float(value) for value in getattr(result, "recovery_force_n", (0.0, 0.0, 0.0)))
            system_force = tuple((fx, fy, fz)[index] + recovery[index] for index in range(3))
            recovery_magnitude = math.sqrt(sum(value * value for value in recovery))
            system_magnitude = math.sqrt(sum(value * value for value in system_force))
            backend = getattr(result, "backend", "OpenFOAM")
            execution_backend = getattr(result, "execution_backend", "CPU de respaldo")
            scope = getattr(result, "execution_scope", "full_flight")
            scope_label = ("snapshot representativo estático" if scope == "snapshot" else
                           "intervalo local (no vuelo continuo completo)" if scope == "motion_interval" else "vuelo completo")
            self.readout.setText(f"{backend} · {execution_backend} · {scope_label} · Fuerza ({fx:.2f}, {fy:.2f}, {fz:.2f}) N · Momento ({mx:.3f}, {my:.3f}, {mz:.3f}) N·m · p_ref manométrica {result.pressure_pa:.1f} Pa")
            self.viewport.show_cfd_result(result)
            velocity_index = self.cfd_layer.findData("velocity")
            if velocity_index >= 0:
                blocker = QSignalBlocker(self.cfd_layer); self.cfd_layer.setCurrentIndex(velocity_index); del blocker
                velocity_button = self.layer_checkboxes.get("velocity")
                if velocity_button is not None:
                    blocker = QSignalBlocker(velocity_button); velocity_button.setChecked(True); del blocker
            if backend == "OpenFOAM" and getattr(result, "status", "completed") == "completed" and bool(getattr(result, "converged", False)):
                self.readout.setText(
                    f"CFD detallado/OpenFOAM · Backend {execution_backend} · {scope_label} · Fuerza ({fx:.2f}, {fy:.2f}, {fz:.2f}) N · "
                    f"Momento ({mx:.3f}, {my:.3f}, {mz:.3f}) N·m · p_ref manométrica {result.pressure_pa:.1f} Pa"
                )
            else:
                convergence_reason = str(getattr(result, "convergence_reason", "sin verificación de convergencia"))
                self.readout.setText(
                    f"Resultado preliminar ({backend}) · Fuerza ({fx:.2f}, {fy:.2f}, {fz:.2f}) N. "
                    f"No es CFD validado: {convergence_reason}."
                )
            validated = backend == "OpenFOAM" and bool(getattr(result, "converged", False))
            available = self.viewport.cfd_available_layers() if validated else {}
            self.cfd_layer.setEnabled(any(available.values()))
            for index in range(self.cfd_layer.count()):
                layer = str(self.cfd_layer.itemData(index))
                enabled = bool(available.get(layer, False))
                self.cfd_layer.model().item(index).setEnabled(enabled)
                if layer in self.layer_checkboxes:
                    self.layer_checkboxes[layer].setEnabled(enabled)
            if backend == "OpenFOAM" and bool(getattr(result, "converged", False)):
                self._last_cfd_result = result; self.download_button.setEnabled(True)
                if recovery_magnitude > 1e-12:
                    self.readout.setText(
                        f"CFD cohete = ({fx:.3f}, {fy:.3f}, {fz:.3f}) N · recuperación q·CdS = {recovery_magnitude:.3f} N · "
                        f"|F sistema| = {system_magnitude:.3f} N · Momento sobre CG ({mx:.3f}, {my:.3f}, {mz:.3f}) N·m"
                    )
                if self._cfd_frame_count:
                    self.history_detail.setText(
                        f"Serie CFD temporal cargada: {self._cfd_frame_count} estados VTK. "
                        "Cada paso sincroniza canards, campos, fuerzas y momentos OpenFOAM."
                    )
                    self.play_cfd.setEnabled(self._cfd_frame_count > 1)
                    self.play_canards.setEnabled(bool(self._flight_schedule))
                    self._animate_sample(0)
                self.run_progress.setValue(100)
                self.run_announcement.setText("CFD DETALLADO COMPLETADO · campos VTK, fuerzas y momentos OpenFOAM listos para analizar.")
                self.run_announcement.setStyleSheet("padding: 7px; border-radius: 4px; background: #1f6b45; color: #ffffff; font-weight: 700;")
            else:
                self.download_button.setEnabled(False)
                self.run_progress.setVisible(False)
                self.run_announcement.setText(
                    "CFD NO VALIDADO · " + str(getattr(result, "convergence_reason", "faltan campos OpenFOAM válidos"))
                )
                self.run_announcement.setStyleSheet("padding: 7px; border-radius: 4px; background: #8b2f2f; color: #ffffff; font-weight: 700;")
                self.run_announcement.setStyleSheet("padding: 7px; border-radius: 4px; background: #7b3f15; color: #ffffff; font-weight: 700;")
        else:
            self._refresh_snapshot_result_selector(None)
            self.run_progress.setVisible(False)
            self.run_announcement.setText("SIMULACIÓN CFD NO COMPLETADA · revisa el mensaje y vuelve a ejecutar.")
            self.run_announcement.setStyleSheet("padding: 7px; border-radius: 4px; background: #7b2020; color: #ffffff; font-weight: 700;")

    def set_run_progress(self, line: str) -> None:
        """Expose useful solver progress without requiring users to read run.log."""
        if not line:
            return
        if line.startswith("CFD_STATUS "):
            fields = dict(part.strip().split("=", 1) for part in line[11:].split(";") if "=" in part)
            phase = fields.get("phase", "preparación")
            elapsed = fields.get("elapsed_s", "?"); remaining = fields.get("remaining_s", "?")
            self.run_announcement.setText(f"SNAPSHOT CFD · {phase} · transcurrido {elapsed} s · restante {remaining} s")
            return
        match = re.search(r"(?:^|\s)Time\s*=\s*([0-9.]+)", line)
        if match:
            total = 500.0
            value = max(0, min(99, round(float(match.group(1)) / total * 100)))
            self.run_progress.setRange(0, 100); self.run_progress.setValue(value)
            self.run_announcement.setText(f"SIMULACIÓN CFD DETALLADA EN PROCESO · solver OpenFOAM {value}% · iteración/tiempo {match.group(1)} de {total:g}")
        elif any(keyword in line for keyword in ("blockMesh", "snappyHexMesh", "decomposePar", "topoSet", "checkMesh", "reconstructPar", "foamToVTK")):
            self.run_announcement.setText(f"SIMULACIÓN CFD DETALLADA EN PROCESO · {line.strip()[:140]}")

    def _download_results(self) -> None:
        result = self._last_cfd_result
        if result is None:
            self.status.setText("No hay un resultado CFD/OpenFOAM terminado para descargar.")
            return
        default = result.case_dir.parent / f"{result.case_dir.name}-resultados-completos.zip"
        destination, _ = QFileDialog.getSaveFileName(self, "Descargar resultados CFD completos", str(default), "Paquete CFD (*.zip)")
        if not destination:
            return
        try:
            self.status.setText("Empaquetando VTK, malla, caso, fuerzas y guía de análisis…")
            bundle = export_result_bundle(result, Path(destination))
        except (OSError, ValueError) as exc:
            self.status.setText(f"No se pudo crear la descarga: {exc}")
            return
        self.status.setText(f"Resultados CFD completos guardados: {bundle}")

    def _load_latest_result(self) -> None:
        """Display an already-completed OpenFOAM run without rerunning the solver."""
        root = application_root()
        output_roots = [root / "out" / "cfd"]
        # A locally built PyInstaller copy keeps resources below ``_internal``
        # while an existing development run lives beside ``python/app``.  Make
        # that case immediately viewable too, without scanning arbitrary user
        # folders in a normal portable installation.
        development_root = next(
            (
                ancestor for ancestor in root.parents
                if (ancestor / "python" / "app").is_dir() and (ancestor / "out" / "cfd").is_dir()
            ),
            None,
        )
        if development_root is not None:
            output_roots.append(development_root / "out" / "cfd")
        candidates = sorted(
            (
                path
                for output_root in output_roots
                if output_root.is_dir()
                for path in output_root.iterdir()
                if path.is_dir()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            self.status.setText("Aún no hay casos CFD guardados en out/cfd.")
            return
        for case_dir in candidates:
            try:
                result = parse_result(case_dir)
            except (OSError, ValueError):
                continue
            if getattr(result, "is_openfoam", False) and getattr(result, "vtk_path", None) and bool(getattr(result, "converged", False)):
                phase_match = re.fullmatch(r"(.+)-fase-(\d+)", case_dir.name)
                if phase_match:
                    base_name = phase_match.group(1)
                    phase_cases = sorted(
                        (
                            path for path in case_dir.parent.iterdir()
                            if path.is_dir() and re.fullmatch(rf"{re.escape(base_name)}-fase-\d+", path.name)
                        ),
                        key=lambda path: int(path.name.rsplit("-fase-", 1)[1]),
                    )
                    completed: list[object] = []
                    for phase_case in phase_cases:
                        try:
                            phase_result = parse_result(phase_case)
                        except (OSError, ValueError):
                            continue
                        if getattr(phase_result, "is_openfoam", False) and getattr(phase_result, "vtk_path", None) and bool(getattr(phase_result, "converged", False)):
                            completed.append(phase_result)
                            if phase_case == case_dir:
                                result = phase_result
                    self.set_completed_snapshot_results(tuple(completed))
                self.status.setText(f"Cargando resultado OpenFOAM existente: {case_dir.name}…")
                self.set_run_finished(result, f"Resultado CFD/OpenFOAM cargado: {case_dir.name}")
                return
        self.status.setText("No se encontró un VTK OpenFOAM terminado en out/cfd.")

    def _toggle_cfd_play(self, checked: bool) -> None:
        if checked and not self._cfd_frame_count:
            blocker = QSignalBlocker(self.play_cfd); self.play_cfd.setChecked(False); del blocker
            return
        if checked:
            blocker = QSignalBlocker(self.play_canards); self.play_canards.setChecked(False); del blocker
            self.play_canards.setText("Reproducir canards")
            self._playback_mode = "cfd"
            self.timeline.blockSignals(True); self.timeline.setRange(0, self._cfd_frame_count - 1); self.timeline.setValue(0); self.timeline.blockSignals(False)
            self.play_cfd.setText("Pausar CFD")
            self._timer.setInterval(450); self._timer.start()
            self._animate_sample(0)
        else:
            self.play_cfd.setText("Reproducir CFD")
            if not self.play_canards.isChecked():
                self._timer.stop()

    def _toggle_canards_play(self, checked: bool) -> None:
        if checked and not self._flight_schedule:
            blocker = QSignalBlocker(self.play_canards); self.play_canards.setChecked(False); del blocker
            return
        if checked:
            blocker = QSignalBlocker(self.play_cfd); self.play_cfd.setChecked(False); del blocker
            self.play_cfd.setText("Reproducir CFD")
            self._playback_mode = "canards"
            self.timeline.blockSignals(True); self.timeline.setRange(0, 600); self.timeline.setValue(0); self.timeline.blockSignals(False)
            self.play_canards.setText("Pausar canards")
            self._timer.setInterval(1000 // 60); self._timer.start()
            self._animate_sample(0)
        else:
            self.play_canards.setText("Reproducir canards")
            if not self.play_cfd.isChecked():
                self._timer.stop()

    def _next_frame(self) -> None:
        # CFD playback advances one exported OpenFOAM state at a time.  It
        # must not skip frames because no intermediate field is fabricated.
        self.timeline.setValue((self.timeline.value() + 1) % (self.timeline.maximum() + 1))

    def _animate_sample(self, frame: int) -> None:
        frame_count = getattr(self, "_cfd_frame_count", 0)
        playback_mode = getattr(self, "_playback_mode", "cfd" if frame_count else "canards")
        if playback_mode == "cfd" and frame_count:
            index = min(self._cfd_frame_count - 1, max(0, int(frame)))
            values = self.viewport.set_cfd_frame(index)
            if values is not None:
                self._set_pid_canard_readouts(values)
            frames = tuple(getattr(getattr(self, "_last_cfd_result", None), "frames", ()))
            if index < len(frames):
                state = frames[index]
                time_s = float(cfd_frame_value(state, "time_s", 0.0))
                force_n = tuple(float(value) for value in cfd_frame_value(state, "force_n", (0.0, 0.0, 0.0)))
                moment_nm = tuple(float(value) for value in cfd_frame_value(state, "moment_nm", (0.0, 0.0, 0.0)))
                self.history_detail.setText(
                    f"CFD/OpenFOAM · t = {time_s:.5g} s · "
                    f"F = ({force_n[0]:.3g}, {force_n[1]:.3g}, {force_n[2]:.3g}) N · "
                    f"M = ({moment_nm[0]:.3g}, {moment_nm[1]:.3g}, {moment_nm[2]:.3g}) N·m"
                )
            return
        if self._flight_schedule:
            index = min(len(self._flight_schedule) - 1, round(frame / self.timeline.maximum() * (len(self._flight_schedule) - 1)))
            values = self._flight_schedule[index][1:]
            self._set_pid_canard_readouts(values)
            speed_schedule = getattr(self, "_flight_speed_schedule", ())
            if index < len(speed_schedule) and hasattr(self.speed, "setValue"):
                blocker = QSignalBlocker(self.speed)
                self.speed.setValue(speed_schedule[index][1])
                del blocker
            if index < len(self._flight_details):
                detail = self._flight_details[index]
                self.viewport.set_weather_context(
                    detail["wind_enu_mps"], detail["temperature_k"],
                    detail["pressure_pa"], detail["humidity_ratio"], self._weather_source,
                )
                self.history_detail.setText(
                    f"t = {detail['time_s']:.2f} s · {detail['action']} · "
                    f"q = {detail['dynamic_pressure_pa']:.1f} Pa · "
                    f"sustentación canards = {detail['lift_n']:.3f} N · "
                    f"arrastre = {detail['drag_n']:.3f} N Â· aire {detail['temperature_k'] - 273.15:.1f} Â°C, "
                    f"{detail['pressure_pa']:.0f} Pa, HR {detail['humidity_ratio'] * 100:.0f}%"
                )
                row = max(0, bisect_left(self._history_row_indices, index))
                if row >= len(self._history_row_indices):
                    row = len(self._history_row_indices) - 1
                elif row and abs(self._history_row_indices[row - 1] - index) < abs(self._history_row_indices[row] - index):
                    row -= 1
                if row != self._selected_history_row and row >= 0:
                    self._selected_history_row = row
                    self.history_table.selectRow(row)
            # Preserve a loaded CFD scene while the real PID history moves the
            # articulated canards. Rebuilding the preview here removed all VTK
            # actors on every animation frame.
            self.viewport.set_canard_deflections(values)
            return
        phase = frame / max(1, self.timeline.maximum()) * 2 * math.pi
        values = tuple(box.value() + 4.0 * math.sin(phase + offset) for box, offset in zip(self.canards, (0, math.pi / 2, math.pi, 3 * math.pi / 2)))
        self.viewport.set_conditions(self.speed.value(), self.alpha.value(), self.beta.value(), values, self.rain.value())


def np_norm(vector: object) -> float:
    try:
        return math.sqrt(sum(float(value) ** 2 for value in vector))
    except TypeError:
        return 0.0
