from __future__ import annotations

from PySide6.QtCore import QDateTime, QSignalBlocker, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QDateEdit, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QLabel, QPushButton, QTimeEdit, QVBoxLayout, QWidget

from .map_widget import MapWidget
from .rocket_viewport import RocketViewport
from .context_help import HelpButton, attach_help, help_label
from app.services.monte_carlo import ellipse_dimensions_label


class SimulatorTab(QWidget):
    launch_requested = Signal(float, float, float, bool)
    environment_requested = Signal(str, float, float, object)
    weather_input_changed = Signal()
    monte_carlo_requested = Signal(int)
    animation_requested = Signal(bool)
    animation_speed_requested = Signal(int)
    center_requested = Signal()
    motor_selected = Signal(str)
    openrocket_selected = Signal(str)
    default_rocket_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        controls = QVBoxLayout()
        form = QFormLayout()
        self.location_query = QLineEdit("Guadalupe, Nuevo León, México")
        now = QDateTime.currentDateTime()
        self.date = QDateEdit(now.date())
        self.date.setDisplayFormat("dd/MM/yyyy")
        self.date.setCalendarPopup(True)
        self.time = QTimeEdit(now.time())
        self.time.setDisplayFormat("HH:mm")
        self.latitude, self.longitude, self.altitude = self._spin(25.681, -90, 90), self._spin(-100.311, -180, 180), self._spin(500.0, -500, 9000)
        self.motor_selector = QComboBox()
        self.motor_selector.setEnabled(False)
        self.motor_selector.currentIndexChanged.connect(self._emit_motor_selected)
        self.motor_performance = QLabel("RocketCEA: pendiente de evaluar el motor seleccionado.")
        self.motor_performance.setWordWrap(True)
        self.rocket_model_button = QPushButton("Usar otro modelo de cohete…")
        self.rocket_model_button.setToolTip("Importa masa, CG, geometría, aerodinámica y recuperación desde un archivo .ork.")
        self.rocket_model_button.clicked.connect(self._choose_openrocket)
        self.default_rocket_button = QPushButton("Usar Sultana")
        self.default_rocket_button.setEnabled(False)
        self._has_custom_rocket = False
        self.default_rocket_button.clicked.connect(self.default_rocket_requested.emit)
        rocket_buttons = QWidget(); rocket_buttons_layout = QHBoxLayout(rocket_buttons)
        rocket_buttons_layout.setContentsMargins(0, 0, 0, 0)
        rocket_buttons_layout.addWidget(self.rocket_model_button, 1); rocket_buttons_layout.addWidget(self.default_rocket_button)
        self.rocket_model_summary = QLabel("Modelo activo: Sultana del Norte (predeterminado).")
        self.rocket_model_summary.setWordWrap(True)
        self.duration_result = QLabel("Se calcula de despegue a aterrizaje")
        form.addRow(help_label("Ubicación", "Lugar de lanzamiento usado para mapa, relieve y consulta meteorológica."), self.location_query)
        form.addRow(help_label("Fecha local", "Fecha local usada para solicitar las condiciones atmosféricas horarias."), self.date)
        form.addRow(help_label("Hora local", "Hora local del lanzamiento; modifica clima, viento y densidad del aire."), self.time)
        form.addRow(help_label("Latitud (°)", "Coordenada norte/sur del sitio de lanzamiento."), self.latitude)
        form.addRow(help_label("Longitud (°)", "Coordenada este/oeste del sitio de lanzamiento."), self.longitude)
        form.addRow(help_label("Altitud MSL (m)", "Altitud sobre el nivel medio del mar; influye en presión y densidad."), self.altitude)
        form.addRow(help_label("Duración de vuelo", "Resultado calculado desde despegue hasta aterrizaje."), self.duration_result)
        form.addRow(help_label("Motor", "Motor y curva de empuje utilizados por la simulación 6-DoF."), self.motor_selector)
        form.addRow(help_label("RocketCEA", "Rendimiento ideal estimado de cámara y tobera; no sustituye la curva de empuje medida."), self.motor_performance)
        form.addRow(help_label("Modelo de cohete", "Opcional: usa el fuselaje y propiedades físicas de OpenRocket. El motor del .ork siempre se ignora."), rocket_buttons)
        form.addRow("", self.rocket_model_summary)
        group = QGroupBox("Lanzamiento y entorno"); group.setLayout(form); controls.addWidget(group)
        self.weather_button = QPushButton("Cargar mapa y clima")
        self.weather_button.clicked.connect(self._emit_environment_request); controls.addWidget(self.weather_button)
        self.weather_summary = QLabel("Perfil local: sin consulta meteorológica todavía.")
        self.weather_summary.setWordWrap(True); controls.addWidget(self.weather_summary)
        self.weather_context = QLabel("Clima aplicado: ninguno; carga mapa y clima antes de lanzar.")
        self.weather_context.setWordWrap(True); controls.addWidget(self.weather_context)
        self.calculate_dispersion = QCheckBox("Calcular dispersión de aterrizaje")
        self.calculate_dispersion.setChecked(True)
        dispersion_toggle = QHBoxLayout(); dispersion_toggle.addWidget(self.calculate_dispersion); dispersion_toggle.addWidget(HelpButton("Activa el análisis estadístico del punto de aterrizaje después de la trayectoria nominal.")); dispersion_toggle.addStretch(1)
        controls.addLayout(dispersion_toggle)
        self.wind_sigma = self._spin(1.5, 0.0, 25.0); self.wind_sigma.setDecimals(2); self.wind_sigma.setSuffix(" m/s")
        controls.addWidget(help_label("Incertidumbre de viento (σ)", "Desviación estándar aplicada al viento en Monte Carlo; aumenta la dispersión esperada.")); controls.addWidget(self.wind_sigma)
        self.dispersion_warning_distance = self._spin(1000.0, 10.0, 20000.0); self.dispersion_warning_distance.setDecimals(0); self.dispersion_warning_distance.setSuffix(" m")
        controls.addWidget(help_label("Advertir si semieje supera", "Umbral de seguridad para alertar cuando la elipse de aterrizaje es demasiado grande.")); controls.addWidget(self.dispersion_warning_distance)
        monte_carlo_row = QHBoxLayout()
        self.monte_carlo_runs = QComboBox()
        for runs in (100, 250, 500, 1000):
            self.monte_carlo_runs.addItem(f"{runs} corridas", runs)
        self.monte_carlo_button = QPushButton("Recalcular dispersión")
        self.monte_carlo_button.setToolTip("Repite el análisis de dispersión con la última trayectoria nominal.")
        self.monte_carlo_button.setEnabled(False)
        self._has_nominal_result = False
        self.monte_carlo_button.clicked.connect(lambda: self.monte_carlo_requested.emit(self.selected_monte_carlo_runs()))
        monte_carlo_row.addWidget(self.monte_carlo_runs); monte_carlo_row.addWidget(HelpButton("Número de trayectorias aleatorias usadas para estimar incertidumbre y elipse de aterrizaje.")); monte_carlo_row.addWidget(self.monte_carlo_button)
        controls.addLayout(monte_carlo_row)
        self.monte_carlo_summary = QLabel("Riesgo: ejecuta Monte Carlo para obtener intervalo y elipse de aterrizaje.")
        self.monte_carlo_summary.setWordWrap(True); controls.addWidget(self.monte_carlo_summary)
        self.request_prediction = QPushButton("Solicitar predicción de vuelo")
        self.request_prediction.setCheckable(True); controls.addWidget(self.request_prediction)
        self.status = QLabel("Listo: selecciona sitio, fecha/hora y carga el clima.")
        self.status.setWordWrap(True); controls.addWidget(self.status)
        self.launch_button = QPushButton("Lanzar simulación")
        self._weather_ready = False
        self.launch_button.setEnabled(False)
        self.launch_button.clicked.connect(self._emit_launch); controls.addWidget(self.launch_button); controls.addStretch()
        self.play_button = QPushButton("Reproducir animación 3D")
        self.play_button.setCheckable(True); self.play_button.setEnabled(False)
        self.play_button.toggled.connect(self.animation_requested.emit)
        self.playback_speed = QComboBox()
        for multiplier in (1, 2, 4):
            self.playback_speed.addItem(f"Velocidad {multiplier}×", multiplier)
        self.playback_speed.currentIndexChanged.connect(self._emit_animation_speed)
        self.playback_speed.setEnabled(False)
        play_controls = QHBoxLayout(); play_controls.addWidget(self.play_button, 1); play_controls.addWidget(self.playback_speed)
        controls.insertLayout(6, play_controls)
        self.center_button = QPushButton("Centrar en cohete")
        self.center_button.setEnabled(False); self.center_button.clicked.connect(self.center_requested.emit)
        controls.insertWidget(7, self.center_button)
        root.addLayout(controls, 1)
        view = QVBoxLayout(); self.map = MapWidget(self); self.viewport = RocketViewport(self)
        attach_help(self.map, "Mapa interactivo para seleccionar el lugar de lanzamiento y actualizar coordenadas.")
        attach_help(self.viewport, "Vista 3D de la trayectoria, orientación y entorno del cohete.")
        self.map.location_selected.connect(self._set_map_location)
        self.latitude.valueChanged.connect(self.weather_input_changed.emit)
        self.longitude.valueChanged.connect(self.weather_input_changed.emit)
        self.date.dateChanged.connect(self.weather_input_changed.emit)
        self.time.timeChanged.connect(self.weather_input_changed.emit)
        view.addWidget(self.map, 1); view.addWidget(self.viewport, 2); root.addLayout(view, 3)
        self.map.set_launch_site(self.latitude.value(), self.longitude.value(), self.location_query.text())

    @staticmethod
    def _spin(value: float, minimum: float, maximum: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox(); box.setRange(minimum, maximum); box.setDecimals(6); box.setValue(value); return box

    def _set_map_location(self, latitude: float, longitude: float) -> None:
        self.latitude.setValue(latitude); self.longitude.setValue(longitude)
        self.location_query.setText(f"Punto seleccionado: {latitude:.5f}, {longitude:.5f}")
        self.status.setText("Ubicación tomada del mapa. La altitud se actualizará al cargar el relieve y clima.")
        self.map.set_launch_site(latitude, longitude, "sitio elegido en el mapa")

    def update_map_reference(self, latitude: float, longitude: float, altitude_msl: float) -> None:
        """Reflect a pan/zoom destination without recentering the interactive map."""
        self.latitude.setValue(latitude); self.longitude.setValue(longitude); self.altitude.setValue(altitude_msl)
        self.location_query.setText(f"Punto seleccionado: {latitude:.5f}, {longitude:.5f}")
        self.status.setText("Mapa 3D y altitud MSL actualizados para este punto. Vuelve a simular para recalcular la trayectoria.")

    def _emit_environment_request(self) -> None:
        selected_when = QDateTime(self.date.date(), self.time.time()).toPython()
        self.environment_requested.emit(
            self.location_query.text(), self.latitude.value(), self.longitude.value(), selected_when,
        )

    def set_motor_options(self, options: list[tuple[str, str]], selected_id: str) -> None:
        blocker = QSignalBlocker(self.motor_selector)
        self.motor_selector.clear()
        for identifier, label in options:
            self.motor_selector.addItem(label, identifier)
        selected_index = self.motor_selector.findData(selected_id)
        self.motor_selector.setCurrentIndex(max(0, selected_index))
        self.motor_selector.setEnabled(bool(options))
        del blocker

    def _emit_motor_selected(self, _index: int) -> None:
        motor_id = self.motor_selector.currentData()
        if motor_id:
            self.motor_selected.emit(str(motor_id))

    def _choose_openrocket(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, "Usar otro modelo de cohete", "", "Proyectos OpenRocket (*.ork);;Todos los archivos (*)",
        )
        if path:
            self.openrocket_selected.emit(path)

    def set_rocket_model(self, summary: str | None) -> None:
        self._has_custom_rocket = bool(summary)
        if summary:
            self.rocket_model_summary.setText(f"Modelo OpenRocket activo: {summary}")
            self.default_rocket_button.setEnabled(True)
        else:
            self.rocket_model_summary.setText("Modelo activo: Sultana del Norte (predeterminado).")
            self.default_rocket_button.setEnabled(False)

    def set_motor_performance(self, report: object | None, error: str = "") -> None:
        """Show equilibrium performance separately from the measured thrust curve."""
        if report is None:
            self.motor_performance.setText(f"RocketCEA: {error or 'no disponible'}")
            return
        self.motor_performance.setText(
            f"CEA ideal: Isp {report.ideal_isp_s:.1f} s · c* {report.cstar_m_s:.0f} m/s · "
            f"Tc {report.chamber_temperature_k:.0f} K (Pc {report.chamber_pressure_pa / 1e6:.2f} MPa, Ae/At {report.expansion_ratio:.1f}).\n"
            f"{report.status}"
        )

    def set_environment(self, name: str, latitude: float, longitude: float, altitude: float, weather: object) -> None:
        # This is an internal synchronization after a weather request, not a
        # user change that should invalidate the newly loaded weather profile.
        with QSignalBlocker(self.latitude), QSignalBlocker(self.longitude):
            self.latitude.setValue(latitude)
            self.longitude.setValue(longitude)
        self.altitude.setValue(altitude)
        self.map.set_launch_site(latitude, longitude, name)
        wind = weather.mean_wind_enu_mps
        self.weather_summary.setText(
            f"{name}\n{weather.source}: {weather.surface_temperature_k - 273.15:.1f} °C, "
            f"{weather.surface_pressure_pa:.0f} Pa, viento ENU ({wind[0]:.1f}, {wind[1]:.1f}, {wind[2]:.1f}) m/s, lluvia {weather.rain_rate_mm_h:.1f} mm/h"
        )
        self.status.setText("Clima cargado; la simulación usará estas condiciones horarias.")

    def _emit_launch(self) -> None:
        self.map.set_launch_site(self.latitude.value(), self.longitude.value(), self.location_query.text())
        self.launch_requested.emit(self.latitude.value(), self.longitude.value(), self.altitude.value(), self.request_prediction.isChecked())

    def set_busy(self, busy: bool, message: str | None = None) -> None:
        self.launch_button.setEnabled(not busy and self._weather_ready); self.weather_button.setEnabled(not busy)
        self.rocket_model_button.setEnabled(not busy); self.default_rocket_button.setEnabled(not busy and self._has_custom_rocket)
        self.monte_carlo_button.setEnabled(not busy and self._has_nominal_result)
        self.calculate_dispersion.setEnabled(not busy); self.monte_carlo_runs.setEnabled(not busy)
        self.wind_sigma.setEnabled(not busy); self.dispersion_warning_distance.setEnabled(not busy)
        if message: self.status.setText(message)

    def set_weather_ready(self, ready: bool, description: str | None = None) -> None:
        self._weather_ready = ready
        self.launch_button.setEnabled(ready)
        if description:
            self.weather_context.setText(description)

    def mark_weather_stale(self) -> None:
        self._weather_ready = False
        self.launch_button.setEnabled(False)
        self.weather_context.setText("Clima desactualizado: cambia de punto, fecha u hora; vuelve a cargar mapa y clima.")

    def current_selection(self) -> tuple[float, float, object]:
        return self.latitude.value(), self.longitude.value(), QDateTime(self.date.date(), self.time.time()).toPython()

    def wind_uncertainty_mps(self) -> float:
        return self.wind_sigma.value()

    def wide_dispersion_limit_m(self) -> float:
        return self.dispersion_warning_distance.value()

    def dispersion_requested(self) -> bool:
        return self.calculate_dispersion.isChecked()

    def selected_monte_carlo_runs(self) -> int:
        return int(self.monte_carlo_runs.currentData())

    def set_nominal_result_available(self, available: bool) -> None:
        self._has_nominal_result = available
        self.monte_carlo_button.setEnabled(available)

    def set_animation_available(self, available: bool) -> None:
        self.play_button.setEnabled(available)
        self.playback_speed.setEnabled(available)
        self.center_button.setEnabled(available)
        if not available:
            self.play_button.setChecked(False)

    def set_flight_duration(self, duration_s: float) -> None:
        self.duration_result.setText(f"{duration_s:.2f} s (despegue a aterrizaje)")

    def set_monte_carlo_summary(self, summary: object) -> None:
        east, north = summary.landing_center_enu_m
        major, minor, heading = summary.landing_semi_axes_95_m
        low, high = summary.apogee_p95_m
        descent_low, descent_high = summary.descent_time_p95_s
        self.monte_carlo_summary.setText(
            f"{summary.runs} corridas · apogeo 95%: {low:.0f}–{high:.0f} m · aterrizaje ENU: ({east:.0f}, {north:.0f}) m · "
            f"{ellipse_dimensions_label(summary.landing_semi_axes_95_m)}; {heading:.0f}° · "
            f"descenso: {descent_low:.0f}–{descent_high:.0f} s · causa principal: {summary.primary_dispersion_cause}."
        )

    def clear_monte_carlo_summary(self) -> None:
        self.monte_carlo_summary.setText("Riesgo: ejecuta Monte Carlo para obtener intervalo y elipse de aterrizaje.")

    def _emit_animation_speed(self) -> None:
        self.animation_speed_requested.emit(int(self.playback_speed.currentData()))

    def set_animation_speed(self, multiplier: int) -> None:
        index = self.playback_speed.findData(multiplier)
        if index >= 0 and index != self.playback_speed.currentIndex():
            self.playback_speed.blockSignals(True)
            self.playback_speed.setCurrentIndex(index)
            self.playback_speed.blockSignals(False)
