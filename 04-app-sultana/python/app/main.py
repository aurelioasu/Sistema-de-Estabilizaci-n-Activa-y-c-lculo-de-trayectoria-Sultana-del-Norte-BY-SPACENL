from __future__ import annotations

import sys

import numpy as np
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

from app.services.config_loader import ConfigValidationError, build_core_config, load_scenario, motor_options
from app.services.rocketcea_motor import rocketcea_motor_report
from app.services.openrocket import OpenRocketImportError, apply_openrocket_model, load_openrocket
from app.services.exporter import export_result
from app.services.validation import compare_result_to_telemetry
from app.services.weather_application import apply_weather_profile, local_weather_profile
from app.services.cfd import docker_status
from app.services.scenario_store import save_laboratory_scenario
from app.runtime import application_root
from app.ui.cfd_tab import CfdTab
from app.ui.kutta_tab import KuttaTab
from app.ui.simulator_tab import SimulatorTab
from app.ui.telemetry_tab import TelemetryTab
from app.workers import CfdWorker, EnvironmentWorker, MapTerrainWorker, MonteCarloWorker, SimulationWorker


SPACE_NL_STYLE = """
QMainWindow, QWidget { background: #171513; color: #f4ede5; font-family: 'Segoe UI'; font-size: 12px; }
QTabWidget::pane { border: 1px solid #443b34; background: #201d1a; }
QTabBar::tab { background: #2b2723; color: #dccfc2; padding: 10px 18px; margin-right: 2px; border-top-left-radius: 5px; border-top-right-radius: 5px; }
QTabBar::tab:selected { background: #e2772c; color: #ffffff; font-weight: 700; }
QGroupBox { border: 1px solid #52473e; border-radius: 8px; margin-top: 10px; padding: 12px 8px 8px 8px; font-weight: 700; color: #ffae65; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QLineEdit, QDoubleSpinBox, QSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit, QComboBox { background: #292521; border: 1px solid #5a4c40; border-radius: 5px; padding: 6px; color: #fff8f1; selection-background-color: #e2772c; selection-color: #ffffff; }
QPushButton { background: #e2772c; color: #ffffff; border: 0; border-radius: 6px; padding: 8px; font-weight: 700; }
QPushButton:hover { background: #f08b42; }
QPushButton:checked { background: #5a321f; color: #ffd8b7; border: 1px solid #e2772c; }
QPushButton:disabled { background: #3a3530; color: #8b8279; }
QLabel { color: #f4ede5; }
QTextEdit, QTableWidget, QListWidget { background: #24201d; border: 1px solid #52473e; border-radius: 5px; color: #f4ede5; gridline-color: #403831; }
QHeaderView::section { background: #342d28; color: #ffb476; border: 0; padding: 5px; font-weight: 700; }
QTableWidget::item:selected, QListWidget::item:selected { background: #5a321f; color: #ffffff; }
QSlider::groove:horizontal { height: 6px; background: #4b423a; border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; margin: -5px 0; border-radius: 8px; background: #e2772c; }
QScrollBar:vertical { background: #211d1a; width: 10px; } QScrollBar::handle:vertical { background: #66584c; border-radius: 5px; }
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sultana - Simulador 6-DoF")
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1200, 760)
        else:
            available = screen.availableGeometry()
            width = max(320, min(1450, int(available.width() * 0.94)))
            height = max(240, min(900, int(available.height() * 0.90)))
            self.resize(width, height)
            self.move(
                available.x() + max(0, (available.width() - width) // 2),
                available.y() + max(0, (available.height() - height) // 2),
            )
        self._root = application_root()
        self._active_vehicle_path = self._root / "configs/vehicle/sultana_4canard.yaml"
        self._active_environment_path = self._root / "configs/environments/guadalupe_example.yaml"
        self._result = None
        self._monte_carlo_summary = None
        self._weather_profile = None
        self._weather_context = None
        self._applying_environment = False
        self._pending_weather_request = None
        self._terrain_raster = None
        self._location_name = "sitio seleccionado"
        self._selected_motor_id = ""
        self._openrocket_model = None
        self._map_pending: tuple[float, float, float] | None = None
        self._map_refreshing = False
        self._map_timer = QTimer(self)
        self._map_timer.setSingleShot(True)
        self._map_timer.setInterval(650)
        self._map_timer.timeout.connect(self._refresh_map_reference)
        self.simulator = SimulatorTab(self)
        self.telemetry = TelemetryTab(self)
        self.cfd = CfdTab(self)
        self.wind_tunnel = KuttaTab(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.simulator, "Simulador 3D y entorno")
        self.tabs.addTab(self.telemetry, "Telemetria y analisis")
        self.tabs.addTab(self.cfd, "Laboratorio CFD del cohete")
        self.tabs.addTab(self.wind_tunnel, "Túnel de viento 2D")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self.simulator.launch_requested.connect(self._launch)
        self.simulator.environment_requested.connect(self._load_environment)
        self.simulator.weather_input_changed.connect(self._weather_input_changed)
        self.simulator.monte_carlo_requested.connect(self._run_monte_carlo)
        self.simulator.animation_requested.connect(self._set_playback)
        self.simulator.animation_speed_requested.connect(self._set_playback_speed)
        self.simulator.center_requested.connect(self.simulator.viewport.center_on_rocket)
        self.simulator.motor_selected.connect(self._select_motor)
        self.simulator.openrocket_selected.connect(self._select_openrocket)
        self.simulator.default_rocket_requested.connect(self._use_default_rocket)
        self.telemetry.playback_speed_changed.connect(self._set_playback_speed)
        self.simulator.map.map_center_changed.connect(self._map_center_changed)
        self.telemetry.export_requested.connect(self._export)
        self.telemetry.comparison_requested.connect(self._compare_telemetry)
        self.telemetry.sample_selected.connect(self._select_sample)
        self.cfd.run_requested.connect(self._run_cfd)
        self.cfd.save_requested.connect(self._save_cfd_scenario)
        self.cfd.cancel_button.clicked.connect(self._cancel_cfd)
        try:
            initial_scenario = self._load_active_scenario()
            self._selected_motor_id = str(initial_scenario.vehicle["propulsion"].get("selected_motor_id", ""))
            self.simulator.set_motor_options(motor_options(initial_scenario), self._selected_motor_id)
            parachute = initial_scenario.vehicle.get("physical_inventory", {}).get("parachute", {})
            self.simulator.viewport.set_parachute_line_count(int(parachute.get("lines", 10)))
            try:
                self.simulator.set_motor_performance(rocketcea_motor_report(initial_scenario))
            except (RuntimeError, ValueError) as exc:
                self.simulator.set_motor_performance(None, str(exc))
            self.cfd.set_scenario(initial_scenario)
        except ConfigValidationError as exc:
            self.cfd.status.setText(f"No se pudo cargar el escenario inicial: {exc}")
        self.cfd.set_docker_status(docker_status())
        self._tab_changed(self.tabs.currentIndex())

    def _tab_changed(self, index: int) -> None:
        active = self.tabs.widget(index)
        simulator = getattr(self, "simulator", None)
        if simulator is not None:
            viewport = getattr(simulator, "viewport", None)
            if viewport is not None and hasattr(viewport, "set_rendering_enabled"):
                viewport.set_rendering_enabled(active is simulator)
        cfd = getattr(self, "cfd", None)
        if cfd is not None:
            if hasattr(cfd, "set_active"):
                cfd.set_active(active is cfd)
            else:
                viewport = getattr(cfd, "viewport", None)
                if viewport is not None and hasattr(viewport, "set_rendering_enabled"):
                    viewport.set_rendering_enabled(active is cfd)
        if active is self.wind_tunnel:
            self.wind_tunnel.start()
        else:
            # Kutta owns a native GPU renderer. Do not leave it consuming
            # memory invisibly while the user works in another laboratory.
            self.wind_tunnel.stop()

    def closeEvent(self, event: object) -> None:
        self.wind_tunnel.stop()
        for tab in (getattr(self, "simulator", None), getattr(self, "cfd", None)):
            viewport = getattr(tab, "viewport", None)
            if viewport is not None and hasattr(viewport, "shutdown"):
                viewport.shutdown()
        super().closeEvent(event)

    def _select_motor(self, motor_id: str) -> None:
        """Apply the selected catalogue motor to the next 6-DoF and CFD case."""
        previous_motor_id = self._selected_motor_id
        self._selected_motor_id = motor_id
        try:
            scenario = self._load_active_scenario()
            # Reuse the selected curve and mass profile in the CFD laboratory.
            self.cfd.set_scenario(scenario)
        except (ConfigValidationError, OpenRocketImportError) as exc:
            self._selected_motor_id = previous_motor_id
            self.simulator.status.setText(f"No se pudo seleccionar el motor: {exc}")
            return
        label = self.simulator.motor_selector.currentText()
        try:
            report = rocketcea_motor_report(scenario, motor_id)
            self.simulator.set_motor_performance(report)
            cea_note = f" RocketCEA ideal: Isp {report.ideal_isp_s:.1f} s."
        except (RuntimeError, ValueError) as exc:
            self.simulator.set_motor_performance(None, str(exc))
            cea_note = " RocketCEA no disponible."
        self._invalidate_vehicle_result()
        self.simulator.status.setText(f"Motor seleccionado: {label}. Curva estimada; requiere ensayo estático para validación.{cea_note}")

    def _load_active_scenario(self) -> object:
        scenario = load_scenario(self._active_vehicle_path, self._active_environment_path)
        if self._selected_motor_id:
            scenario.vehicle["propulsion"]["selected_motor_id"] = self._selected_motor_id
        if self._openrocket_model is not None:
            scenario = apply_openrocket_model(scenario, self._openrocket_model)
        return scenario

    def _select_openrocket(self, path: str) -> None:
        try:
            model = load_openrocket(path)
            previous = self._openrocket_model
            self._openrocket_model = model
            try:
                scenario = self._load_active_scenario()
            except Exception:
                self._openrocket_model = previous
                raise
        except (OSError, OpenRocketImportError, ConfigValidationError) as exc:
            QMessageBox.warning(self, "No se pudo importar OpenRocket", str(exc))
            return
        self._invalidate_vehicle_result()
        self.cfd.set_scenario(scenario)
        parachute = scenario.vehicle.get("physical_inventory", {}).get("parachute", {})
        self.simulator.viewport.set_parachute_line_count(int(parachute.get("lines", 10)))
        self.simulator.set_rocket_model(model.summary())
        self.simulator.status.setText(
            "Modelo OpenRocket aplicado a 6-DoF, Monte Carlo y CFD. "
            "La propulsión del archivo fue ignorada; selecciona KNSB 10, 15 o 20 cm."
        )

    def _use_default_rocket(self) -> None:
        self._openrocket_model = None
        self._active_vehicle_path = self._root / "configs/vehicle/sultana_4canard.yaml"
        try:
            scenario = self._load_active_scenario()
        except ConfigValidationError as exc:
            QMessageBox.warning(self, "No se pudo restaurar Sultana", str(exc))
            return
        self._invalidate_vehicle_result()
        self.cfd.set_scenario(scenario)
        parachute = scenario.vehicle.get("physical_inventory", {}).get("parachute", {})
        self.simulator.viewport.set_parachute_line_count(int(parachute.get("lines", 10)))
        self.simulator.set_rocket_model(None)
        self.simulator.status.setText("Modelo Sultana del Norte restaurado; se conservan el sitio, el clima y el motor KNSB seleccionado.")

    def _invalidate_vehicle_result(self) -> None:
        self._result = None
        self._monte_carlo_summary = None
        self.simulator.set_nominal_result_available(False)
        self.simulator.set_animation_available(False)
        self.simulator.clear_monte_carlo_summary()
        self.simulator.map.clear_dispersion()
        self.cfd.clear_flight_result()

    def _load_environment(self, name: str, latitude: float, longitude: float, when: object) -> None:
        self._pending_weather_request = (name, latitude, longitude, when)
        self.simulator.set_busy(True, "Cargando relieve y clima horario para el punto seleccionado...")
        self._environment_thread = QThread(self)
        self._environment_worker = EnvironmentWorker()
        self._environment_worker.moveToThread(self._environment_thread)
        self._environment_thread.started.connect(lambda: self._environment_worker.load(name, latitude, longitude, when))
        self._environment_worker.completed.connect(self._environment_loaded)
        self._environment_worker.failed.connect(self._environment_failed)
        self._environment_worker.completed.connect(self._environment_thread.quit)
        self._environment_worker.failed.connect(self._environment_thread.quit)
        self._environment_thread.finished.connect(self._environment_worker.deleteLater)
        self._environment_thread.finished.connect(self._environment_thread.deleteLater)
        self._environment_thread.start()

    def _environment_loaded(self, location: object, weather: object, terrain: object) -> None:
        if not self._pending_weather_request or not self._selection_matches(*self._pending_weather_request[1:]):
            self.simulator.set_busy(False)
            self.simulator.mark_weather_stale()
            return
        self._weather_profile = weather
        self._terrain_raster = terrain
        self._location_name = location.name
        self.simulator.set_busy(False)
        self.simulator.viewport.set_terrain(terrain)
        _, _, _, when = self._pending_weather_request
        self._applying_environment = True
        try:
            self.simulator.set_environment(location.name, location.latitude_deg, location.longitude_deg, location.elevation_m, weather)
        finally:
            self._applying_environment = False
        latitude, longitude, selected_when = self.simulator.current_selection()
        self._weather_context = {
            "source": weather.source,
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "requested_when": selected_when,
        }
        self.simulator.set_weather_ready(True, f"Clima aplicado: {weather.source} · {location.name} · {when:%Y-%m-%d %H:%M}")
        try:
            self.cfd.apply_weather_profile(weather)
        except Exception as exc:  # The optional CFD preview must never block the 6-DoF launch.
            self.cfd.status.setText(f"No se pudo sincronizar el clima con la vista CFD: {exc}")

    def _environment_failed(self, message: str) -> None:
        if not self._pending_weather_request or not self._selection_matches(*self._pending_weather_request[1:]):
            self.simulator.set_busy(False)
            self.simulator.mark_weather_stale()
            return
        name, latitude, longitude, when = self._pending_weather_request
        try:
            scenario = self._load_active_scenario()
            fallback = local_weather_profile(scenario.environment["weather"])
        except (ConfigValidationError, RuntimeError) as exc:
            self.simulator.set_busy(False, f"No se pudo obtener el perfil local: {exc}")
            return
        self._weather_profile = fallback
        self.simulator.set_busy(False)
        self._applying_environment = True
        try:
            self.simulator.set_environment(name, latitude, longitude, self.simulator.altitude.value(), fallback)
        finally:
            self._applying_environment = False
        current_latitude, current_longitude, selected_when = self.simulator.current_selection()
        self._weather_context = {
            "source": fallback.source,
            "latitude_deg": current_latitude,
            "longitude_deg": current_longitude,
            "requested_when": selected_when,
        }
        self.simulator.set_weather_ready(True, f"Clima aplicado: perfil local · {name} · {when:%Y-%m-%d %H:%M}")
        try:
            self.cfd.apply_weather_profile(fallback)
        except Exception as exc:  # The optional CFD preview must never block the 6-DoF launch.
            self.cfd.status.setText(f"No se pudo sincronizar el clima con la vista CFD: {exc}")
        self.simulator.status.setText(f"Open-Meteo falló: {message}. Monte Carlo usa perfil local; carga mapa y clima para usar Open-Meteo.")

    def _selection_matches(self, latitude: float, longitude: float, when: object) -> bool:
        current_lat, current_lon, current_when = self.simulator.current_selection()
        return abs(current_lat - latitude) < 1e-6 and abs(current_lon - longitude) < 1e-6 and current_when == when

    def _weather_input_changed(self) -> None:
        if self._applying_environment:
            return
        if not self._weather_context:
            self.simulator.mark_weather_stale()
            return
        if not self._selection_matches(
            self._weather_context["latitude_deg"], self._weather_context["longitude_deg"], self._weather_context["requested_when"],
        ):
            self._weather_profile = None
            self._weather_context = None
            self.simulator.mark_weather_stale()

    def _map_center_changed(self, latitude: float, longitude: float, zoom: float) -> None:
        # MapLibre fires frequently during a pan. Debounce network work until the map settles.
        self._map_pending = (latitude, longitude, zoom)
        self._map_timer.start()

    def _refresh_map_reference(self) -> None:
        if self._map_refreshing or self._map_pending is None:
            return
        latitude, longitude, zoom = self._map_pending
        self._map_pending = None
        self._map_refreshing = True
        self._map_thread = QThread(self)
        self._map_worker = MapTerrainWorker()
        self._map_worker.moveToThread(self._map_thread)
        self._map_thread.started.connect(lambda: self._map_worker.load(latitude, longitude, zoom))
        self._map_worker.completed.connect(self._map_reference_loaded)
        self._map_worker.failed.connect(self._map_reference_failed)
        self._map_worker.completed.connect(self._map_thread.quit)
        self._map_worker.failed.connect(self._map_thread.quit)
        self._map_thread.finished.connect(self._map_worker.deleteLater)
        self._map_thread.finished.connect(self._map_thread.deleteLater)
        self._map_thread.start()

    def _map_reference_loaded(self, latitude: float, longitude: float, elevation: float, terrain: object) -> None:
        self._map_refreshing = False
        self._terrain_raster = terrain
        self.simulator.viewport.set_terrain(terrain)
        # Moving the map is only a camera/terrain action.  It must not silently
        # overwrite the selected launch site or invalidate its loaded weather.
        # A deliberate map click still goes through location_selected.
        self.simulator.status.setText("Mapa 3D actualizado. El sitio de lanzamiento y el clima cargado se conservan.")
        if self._result:
            self.simulator.viewport.show_result(self._result)
        if self._map_pending is not None:
            self._map_timer.start()

    def _map_reference_failed(self, message: str) -> None:
        self._map_refreshing = False
        self.simulator.status.setText(f"No se pudo actualizar la textura/elevacion del mapa: {message}")
        if self._map_pending is not None:
            self._map_timer.start()

    def _launch(self, latitude: float, longitude: float, altitude: float, prediction_requested: bool) -> None:
        if not self._weather_profile or not self._weather_context or not self._selection_matches(
            self._weather_context["latitude_deg"], self._weather_context["longitude_deg"], self._weather_context["requested_when"],
        ):
            self.simulator.mark_weather_stale()
            self.simulator.status.setText("Carga mapa y clima para el punto, fecha y hora seleccionados antes de lanzar.")
            return
        try:
            scenario = self._load_active_scenario()
            self._scenario = scenario
            scenario.environment["launch_site"].update({"latitude_deg": latitude, "longitude_deg": longitude, "altitude_msl_m": altitude})
            self._launch_site = (latitude, longitude, altitude)
            # 180 s is an internal safety horizon; the displayed duration is always the actual landing time.
            config = build_core_config(scenario, 180.0, prediction_requested)
            apply_weather_profile(config, self._weather_profile)
        except (ConfigValidationError, RuntimeError) as exc:
            QMessageBox.critical(self, "Configuracion invalida", str(exc))
            return
        self._monte_carlo_summary = None
        self.simulator.set_nominal_result_available(False)
        self.simulator.clear_monte_carlo_summary()
        self.simulator.map.clear_dispersion()
        self.simulator.set_busy(True, "Calculando trayectoria nominal con las condiciones seleccionadas...")
        self._thread = QThread(self)
        self._worker = SimulationWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(lambda: self._worker.run(config))
        self._worker.completed.connect(self._completed)
        self._worker.failed.connect(self._failed)
        self._worker.completed.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _run_monte_carlo(self, runs: int) -> None:
        if not self._result or not hasattr(self, "_scenario"):
            QMessageBox.information(self, "Sin trayectoria nominal", "Lanza primero la trayectoria nominal para recalcular su dispersión.")
            return
        try:
            scenario = self._scenario
            launch_site = self._launch_site
        except (ConfigValidationError, RuntimeError) as exc:
            QMessageBox.critical(self, "Configuración inválida", str(exc))
            return
        wind_sigma = self.simulator.wind_uncertainty_mps()
        self.simulator.set_busy(True, f"Calculando dispersión: 0/{runs} corridas")
        if self._weather_profile.source == "local_profile":
            self.simulator.status.setText("Monte Carlo usa perfil local; carga mapa y clima para usar Open-Meteo.")
        self._monte_carlo_thread = QThread(self)
        self._monte_carlo_worker = MonteCarloWorker()
        self._monte_carlo_worker.moveToThread(self._monte_carlo_thread)
        self._monte_carlo_thread.started.connect(
            lambda: self._monte_carlo_worker.run(
                scenario, self._weather_profile, runs, launch_site, wind_sigma,
            )
        )
        self._monte_carlo_worker.progress.connect(self._monte_carlo_progress)
        self._monte_carlo_worker.completed.connect(self._monte_carlo_completed)
        self._monte_carlo_worker.failed.connect(self._monte_carlo_failed)
        self._monte_carlo_worker.completed.connect(self._monte_carlo_thread.quit)
        self._monte_carlo_worker.failed.connect(self._monte_carlo_thread.quit)
        self._monte_carlo_thread.finished.connect(self._monte_carlo_worker.deleteLater)
        self._monte_carlo_thread.finished.connect(self._monte_carlo_thread.deleteLater)
        self._monte_carlo_thread.start()

    def _monte_carlo_progress(self, completed: int, total: int) -> None:
        self.simulator.status.setText(f"Calculando dispersión: {completed}/{total} corridas")

    def _monte_carlo_completed(self, summary: object) -> None:
        self._monte_carlo_summary = summary
        self.simulator.set_monte_carlo_summary(summary)
        label_status = "análisis preliminar" if not self._scenario.calibration_complete else "modelo calibrado"
        low, high = summary.apogee_p95_m
        major, minor, heading = summary.landing_semi_axes_95_m
        wind = self._weather_profile.mean_wind_enu_mps
        wind_sigma = summary.uncertainties["wind_east_std_mps"]
        descent_low, descent_high = summary.descent_time_p95_s
        warning = ""
        if max(major, minor) > self.simulator.wide_dispersion_limit_m():
            warning = " Zona de caída amplia: no usar como predicción operativa sin medir mejor el viento y validar el modelo."
        self.simulator.map.set_dispersion(
            self._launch_site[0], self._launch_site[1], summary.landing_center_enu_m, summary.landing_semi_axes_95_m,
            f"Dispersión 95% · {label_status}\n{summary.runs} corridas · apogeo {low:.0f}–{high:.0f} m\n"
            f"Semiejes {major:.0f} × {minor:.0f} m; extensión total {2 * major:.0f} × {2 * minor:.0f} m · {heading:.0f}°\n"
            f"Viento base ENU ({wind[0]:.1f}, {wind[1]:.1f}, {wind[2]:.1f}) m/s · σ={wind_sigma:.2f} m/s · descenso {descent_low:.0f}–{descent_high:.0f} s\n"
            f"Causa principal: {summary.primary_dispersion_cause}. El área sombreada contiene aproximadamente 95% de las corridas simuladas.{warning}",
        )
        self.simulator.set_busy(False, f"Dispersión terminada: trayectoria nominal y zona probable actualizadas.{warning}")

    def _monte_carlo_failed(self, message: str) -> None:
        self.simulator.set_busy(False, f"La trayectoria nominal se conserva. No se pudo calcular dispersión: {message}")
        QMessageBox.warning(self, "Error de dispersión", f"La trayectoria nominal sigue disponible.\n\n{message}")

    def _completed(self, result: object) -> None:
        self._result = result
        self.simulator.set_nominal_result_available(True)
        self.simulator.viewport.show_result(result)
        self.telemetry.set_result(result)
        self.cfd.set_flight_result(result)
        self.simulator.set_animation_available(True)
        self.simulator.set_flight_duration(result.samples[-1].time_s)
        stride = max(1, len(result.samples) // 500)
        path = [[float(sample.longitude_deg), float(sample.latitude_deg)] for sample in result.samples[::stride]]
        last = result.samples[-1]
        last_point = [float(last.longitude_deg), float(last.latitude_deg)]
        if path[-1] != last_point:
            path.append(last_point)
        first = result.samples[0]
        self.simulator.map.set_launch_site(float(first.latitude_deg), float(first.longitude_deg), self._location_name)
        parachute_sample = next((sample for sample in result.samples if sample.parachute_deployed), None)
        parachute_point = None if parachute_sample is None else [float(parachute_sample.longitude_deg), float(parachute_sample.latitude_deg)]
        self.simulator.map.set_flight_path(path, parachute_point)
        if self.simulator.dispersion_requested():
            self._run_monte_carlo(self.simulator.selected_monte_carlo_runs())
        else:
            self.simulator.set_busy(False, "Predicción habilitada" if result.flight_prediction_allowed else "Trayectoria nominal lista: análisis preliminar por calibraciones pendientes.")

    def _set_playback(self, playing: bool) -> None:
        if self.telemetry.play_button.isChecked() != playing:
            self.telemetry.play_button.setChecked(playing)

    def _set_playback_speed(self, multiplier: int) -> None:
        self.simulator.set_animation_speed(multiplier)
        self.telemetry.set_playback_speed(multiplier)

    def _run_cfd(self, requests: object, vehicle: object, environment: object, tables: object, viewport: object) -> None:
        """Run the five static flight-phase snapshots one at a time on the CPU."""
        self._cfd_queue = list(requests) if isinstance(requests, (tuple, list)) else [requests]
        self._cfd_queue_total = len(self._cfd_queue)
        self._cfd_results: list[object] = []
        self._cfd_failures: list[str] = []
        self._cfd_cancelled = False
        self._start_next_cfd()

    def _start_next_cfd(self) -> None:
        if not self._cfd_queue:
            return
        request = self._cfd_queue.pop(0)
        self._active_cfd_request = request
        completed = len(self._cfd_results) + len(self._cfd_failures)
        if self._cfd_queue_total > 1:
            phase = getattr(request, "snapshot_reason", "snapshot")
            source_time = getattr(request, "snapshot_source_time_s", None)
            time_label = "" if source_time is None else f" · t={float(source_time):.2f} s"
            self.cfd.status.setText(
                f"Snapshot CFD {completed + 1}/{self._cfd_queue_total}: {phase}{time_label} · preparando…"
            )
        self._cfd_thread = QThread(self)
        self._cfd_worker = CfdWorker()
        self._cfd_worker.set_job(self._root, request)
        self._cfd_worker.moveToThread(self._cfd_thread)
        self._cfd_thread.started.connect(self._cfd_worker.run)
        self._cfd_worker.progress.connect(self._cfd_progress)
        self._cfd_worker.completed.connect(self._cfd_completed)
        self._cfd_worker.failed.connect(self._cfd_failed)
        self._cfd_worker.completed.connect(self._cfd_thread.quit)
        self._cfd_worker.failed.connect(self._cfd_thread.quit)
        self._cfd_thread.finished.connect(self._cfd_worker.deleteLater)
        self._cfd_thread.finished.connect(self._cfd_thread.deleteLater)
        self._cfd_thread.start()

    def _cancel_cfd(self) -> None:
        self._cfd_cancelled = True
        self._cfd_queue = []
        if hasattr(self, "_cfd_worker"):
            # threading.Event is safe to set from the GUI thread; it is checked
            # between OpenFOAM log lines by the worker.
            self._cfd_worker.cancel()
            self.cfd.status.setText("Solicitando cancelación del caso CFD…")

    def _cfd_progress(self, line: str) -> None:
        if line:
            current = len(getattr(self, "_cfd_results", ())) + len(getattr(self, "_cfd_failures", ())) + 1
            request = getattr(self, "_active_cfd_request", None)
            phase = getattr(request, "snapshot_reason", "snapshot")
            source_time = getattr(request, "snapshot_source_time_s", None)
            time_label = "" if source_time is None else f" · t={float(source_time):.2f} s"
            self.cfd.status.setText(
                f"Snapshot CFD {current}/{getattr(self, '_cfd_queue_total', 1)}: {phase}{time_label} · {line[-180:]}"
            )
            self.cfd.set_run_progress(line)

    def _cfd_completed(self, result: object) -> None:
        self._cfd_results.append(result)
        if self._cfd_queue:
            self.cfd.status.setText(
                f"Snapshot CFD {len(self._cfd_results)}/{self._cfd_queue_total} terminado; iniciando el siguiente."
            )
            self._start_next_cfd()
            return
        backend = getattr(result, "backend", "OpenFOAM")
        note = getattr(result, "note", "")
        if "EOF" in note or "cloudfront" in note.lower():
            note = "Docker Hub interrumpió la descarga de OpenFOAM; se usó el modelo local preliminar."
        suffix = "" if not note else f" · {note[:180]}"
        failures = f" · {len(self._cfd_failures)} fallido(s)" if self._cfd_failures else ""
        self.cfd.set_completed_snapshot_results(tuple(self._cfd_results))
        self.cfd.set_run_finished(result, f"Cola CFD terminada: {len(self._cfd_results)} válido(s){failures}. Último caso ({backend}): {result.case_dir}{suffix}")

    def _cfd_failed(self, message: str) -> None:
        if getattr(self, "_cfd_cancelled", False):
            self.cfd.set_run_finished(None, f"CFD cancelado: {message}")
            return
        self._cfd_failures.append(message)
        if self._cfd_queue:
            self.cfd.status.setText(f"Caso CFD falló; continúa el siguiente ({len(self._cfd_results) + len(self._cfd_failures) + 1}/{self._cfd_queue_total}).")
            self._start_next_cfd()
            return
        if self._cfd_results:
            result = self._cfd_results[-1]
            self.cfd.set_completed_snapshot_results(tuple(self._cfd_results))
            self.cfd.set_run_finished(result, f"Cola CFD finalizada: {len(self._cfd_results)} válido(s), {len(self._cfd_failures)} fallido(s).")
        else:
            self.cfd.set_run_finished(
                None,
                f"Cola CFD finalizada: 0 válido(s), {len(self._cfd_failures)} fallido(s). Último error: {message}",
            )

    def _save_cfd_scenario(self, name: str, vehicle: object, environment: object, tables: object) -> None:
        try:
            scenario = save_laboratory_scenario(
                self._root, name, vehicle, environment, tables["thrust"], tables["mass"], tables["aero"],
            )
        except (OSError, ValueError, ConfigValidationError) as exc:
            self.cfd.status.setText(f"No se pudo guardar el escenario: {exc}")
            return
        self._active_vehicle_path, self._active_environment_path = scenario.vehicle_path, scenario.environment_path
        self._scenario = scenario
        self.cfd.set_scenario(scenario)
        self.cfd.status.setText(f"Escenario activo guardado: {scenario.vehicle_path.parent}")

    def _failed(self, message: str) -> None:
        self.simulator.set_busy(False)
        QMessageBox.critical(self, "Error de simulacion", message)

    def _select_sample(self, index: int) -> None:
        if self._result and 0 <= index < len(self._result.samples):
            sample = self._result.samples[index]
            self.simulator.viewport.set_sample_index(index)
            angles = [round(float(np.degrees(value)), 1) for value in sample.canard_deflection_rad]
            self.simulator.status.setText(f"t={sample.time_s:.2f}s - canards {angles} grados")

    def _export(self) -> None:
        if not self._result:
            return
        manifest = {"vehicle_config": str(self._active_vehicle_path), "environment_config": str(self._active_environment_path)}
        manifest["selected_motor_id"] = self._selected_motor_id
        if self._openrocket_model is not None:
            manifest["openrocket_import"] = {
                "source": str(self._openrocket_model.source_path),
                "name": self._openrocket_model.name,
                "ignored_motors": list(self._openrocket_model.ignored_motors),
                "airframe_only": True,
            }
        if hasattr(self, "_scenario") and self._scenario.parameter_registry:
            manifest["parameter_registry"] = self._scenario.parameter_registry
            manifest["parameter_registry_path"] = str(self._scenario.parameter_registry_path)
        if self._monte_carlo_summary:
            manifest["monte_carlo"] = self._monte_carlo_summary.to_manifest()
        if self._weather_context and self._weather_profile:
            manifest["weather_applied"] = {
                **self._weather_context,
                "requested_when": self._weather_context["requested_when"].isoformat(),
                "base_wind_enu_mps": list(self._weather_profile.mean_wind_enu_mps),
                "wind_uncertainty_mps": self.simulator.wind_uncertainty_mps(),
            }
        outputs = export_result(self._result, self._root / "out", manifest)
        QMessageBox.information(self, "Exportacion", "\n".join(f"{kind}: {path}" for kind, path in outputs.items()))

    def _compare_telemetry(self, telemetry_path: str) -> None:
        if not self._result:
            return
        try:
            self.telemetry.set_comparison(compare_result_to_telemetry(self._result, telemetry_path))
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "No se pudo comparar la telemetría", str(exc))


def main() -> int:
    from app.bootstrap import main as bootstrap_main

    return bootstrap_main()


if __name__ == "__main__":
    raise SystemExit(main())
