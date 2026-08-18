from __future__ import annotations

from collections import defaultdict, deque
import math
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QPushButton, QSlider, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget

from app.services.serial_telemetry import SerialTelemetryClient, available_serial_ports
from .context_help import HelpButton, attach_help, help_label


class TelemetryTab(QWidget):
    export_requested = Signal()
    sample_selected = Signal(int)
    playback_speed_changed = Signal(int)
    comparison_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        pg.setConfigOption("background", "#201d1a")
        pg.setConfigOption("foreground", "#f4ede5")
        pg.setConfigOption("antialias", True)
        self._result = None
        self._plot_lines: list[pg.InfiniteLine] = []
        self._serial = SerialTelemetryClient()
        self._live_started_at = 0.0
        self._live_series: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=2400))
        self._live_max_altitude = 0.0
        self._live_max_mach = 0.0
        self._live_max_q = 0.0
        self._live_max_rain = 0.0
        self._last_live_state = ""
        root = QVBoxLayout(self)

        receiver = QGroupBox("Recibidor de información en tiempo real")
        receiver_layout = QHBoxLayout(receiver)
        self.port_selector = QComboBox()
        self.port_selector.setMinimumWidth(180)
        self.refresh_ports_button = QPushButton("Actualizar COM")
        self.refresh_ports_button.clicked.connect(self.refresh_serial_ports)
        self.baud_selector = QComboBox()
        for baud in (9600, 57600, 115200, 230400, 460800, 921600):
            self.baud_selector.addItem(str(baud), baud)
        self.baud_selector.setCurrentIndex(self.baud_selector.findData(115200))
        self.connect_receiver_button = QPushButton("Conectar recibidor")
        self.connect_receiver_button.setCheckable(True)
        self.connect_receiver_button.toggled.connect(self._toggle_receiver)
        self.receiver_status = QLabel("● Desconectado")
        self.receiver_status.setStyleSheet("color: #b7aaa0; font-weight: 700;")
        receiver_layout.addWidget(help_label("Puerto COM", "Puerto serial donde está conectada la ESP receptora. Solo al conectar se habilitan y muestran los datos en vivo."))
        receiver_layout.addWidget(self.port_selector)
        receiver_layout.addWidget(self.refresh_ports_button)
        receiver_layout.addWidget(help_label("Baudios", "Velocidad serial. Debe coincidir con Serial.begin(...) en la ESP; 115200 es el valor recomendado."))
        receiver_layout.addWidget(self.baud_selector)
        receiver_layout.addWidget(self.connect_receiver_button)
        receiver_layout.addWidget(self.receiver_status, 1)
        receiver_layout.addWidget(HelpButton("La ESP debe enviar un paquete por línea. Se aceptan objetos JSON o pares como tiempo=1.2,altura=35.4,mach=0.18,c1=-3."))
        root.addWidget(receiver)

        self.live_values = QLabel()
        self.live_values.setWordWrap(True)
        self.live_values.setStyleSheet("background: #173825; border: 1px solid #2d8a58; border-radius: 5px; padding: 6px;")
        self.live_values.hide()
        root.addWidget(self.live_values)
        metric_layout = QFormLayout()
        self.metrics = {key: QLabel("-") for key in ("Apogeo AGL", "Mach", "q dinamico", "Duracion de vuelo", "Lluvia maxima", "Clasificacion")}
        metric_help = {
            "Apogeo AGL": "Altura máxima sobre el punto de lanzamiento.",
            "Mach": "Máxima relación entre velocidad del cohete y velocidad local del sonido.",
            "q dinamico": "Máxima presión dinámica; identifica la región de mayor carga aerodinámica.",
            "Duracion de vuelo": "Tiempo desde el inicio hasta la última muestra recibida o el aterrizaje simulado.",
            "Lluvia maxima": "Mayor fuerza estimada causada por impacto de lluvia.",
            "Clasificacion": "Estado o clasificación general comunicada por la simulación o por la ESP.",
        }
        for name, label in self.metrics.items(): metric_layout.addRow(help_label(name, metric_help[name]), label)
        metric_box = QWidget(); metric_box.setLayout(metric_layout)
        top = QHBoxLayout(); top.addWidget(metric_box)
        self.events = QListWidget()
        attach_help(self.events, "Secuencia de eventos del vuelo: salida del riel, apagado, apogeo, despliegue y aterrizaje; en vivo registra cambios de estado recibidos.")
        top.addWidget(self.events, 1); root.addLayout(top)
        self.comparison_summary = QLabel("Validación: carga telemetría de un vuelo para cuantificar el error.")
        self.comparison_summary.setWordWrap(True); root.addWidget(self.comparison_summary)

        splitter = QSplitter(Qt.Horizontal)
        plots_container = QWidget(); plots_layout = QGridLayout(plots_container)
        self.altitude_plot = self._plot("Altitud AGL / EKF", "m")
        self.altitude_truth = self.altitude_plot.plot(pen=pg.mkPen("#e2772c", width=3), name="Verdad")
        self.altitude_ekf = self.altitude_plot.plot(pen=pg.mkPen("#59524c", width=2), name="EKF")
        self.attitude_plot = self._plot("Actitud y PID", "rad")
        self.pitch_curve = self.attitude_plot.plot(pen=pg.mkPen("#e2772c", width=2), name="Pitch")
        self.yaw_curve = self.attitude_plot.plot(pen=pg.mkPen("#9b3f73", width=2), name="Yaw")
        self.pid_curve = self.attitude_plot.plot(pen=pg.mkPen("#46413d", width=2), name="PID pitch")
        self.wind_plot = self._plot("Viento ENU y velocidad relativa", "m/s")
        self.wind_east = self.wind_plot.plot(pen=pg.mkPen("#b85c28", width=2), name="Viento Este")
        self.wind_north = self.wind_plot.plot(pen=pg.mkPen("#43855a", width=2), name="Viento Norte")
        self.airspeed_curve = self.wind_plot.plot(pen=pg.mkPen("#7656a5", width=2), name="Airspeed")
        self.aero_plot = self._plot("Fuerzas aerodinamicas", "N")
        self.drag_curve = self.aero_plot.plot(pen=pg.mkPen("#d45936", width=2), name="Arrastre")
        self.rain_curve = self.aero_plot.plot(pen=pg.mkPen("#537f64", width=2), name="Impacto lluvia")
        self.lift_curve = self.aero_plot.plot(pen=pg.mkPen("#7656a5", width=2), name="Elevacion canards")
        self.thermal_plot = self._plot("Friccion y masa", "valor")
        self.heat_curve = self.thermal_plot.plot(pen=pg.mkPen("#d45936", width=2), name="Proxy calor friccion")
        self.mass_curve = self.thermal_plot.plot(pen=pg.mkPen("#3d3935", width=2), name="Masa kg")
        self.thrust_curve = self.thermal_plot.plot(pen=pg.mkPen("#e2772c", width=3), name="Empuje N")
        self.canard_plot = self._plot("Deflexion individual de canards", "grados")
        self.canard_curves = [self.canard_plot.plot(pen=pg.mkPen(color, width=2), name=f"Canard {index + 1}") for index, color in enumerate(("#e2772c", "#9b3f73", "#43855a", "#7656a5"))]
        plot_options = (self.altitude_plot, self.attitude_plot, self.wind_plot, self.aero_plot, self.thermal_plot, self.canard_plot)
        for index, plot in enumerate(plot_options):
            plot.setMinimumHeight(190)
            plots_layout.addWidget(plot, index // 2, index % 2)
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#d8c9bb", width=1, style=Qt.DashLine))
            plot.addItem(line); self._plot_lines.append(line)
        plot_help = (
            "Altitud real/medida y estimación EKF a lo largo del tiempo.",
            "Orientación pitch/yaw y corrección del controlador PID.",
            "Componentes del viento y velocidad del aire relativa al cohete.",
            "Arrastre, impacto de lluvia y sustentación producida por los canards.",
            "Indicador térmico por fricción, masa instantánea y empuje.",
            "Ángulo individual de los cuatro canards; muestra saturación y respuesta de control.",
        )
        for plot, explanation in zip(plot_options, plot_help):
            attach_help(plot, explanation)
        for row in range(3):
            plots_layout.setRowStretch(row, 1)
        plots_layout.setColumnStretch(0, 1); plots_layout.setColumnStretch(1, 1)
        splitter.addWidget(plots_container)

        analysis = QWidget(); analysis_layout = QVBoxLayout(analysis)
        self.current_values = QLabel("Selecciona una muestra para ver los calculos.")
        self.current_values.setWordWrap(True); analysis_layout.addWidget(self.current_values)
        self.math = QTextEdit(); self.math.setReadOnly(True); self.math.setMinimumWidth(360)
        attach_help(self.math, "Detalle matemático de la muestra seleccionada. En modo en vivo muestra el último paquete normalizado recibido de la ESP.")
        analysis_layout.addWidget(self.math, 2)
        self.canard_steps = QTableWidget(0, 6)
        self.canard_steps.setHorizontalHeaderLabels(["t (s)", "C1", "C2", "C3", "C4", "Correccion PID"])
        self.canard_steps.setEditTriggers(QTableWidget.NoEditTriggers)
        attach_help(self.canard_steps, "Tabla cronológica de deflexiones C1–C4 y corrección PID. En vivo conserva las muestras recientes sin bloquear la interfaz.")
        analysis_layout.addWidget(QLabel("Paso a paso de canards (muestras decimadas)")); analysis_layout.addWidget(self.canard_steps, 2)
        splitter.addWidget(analysis); splitter.setSizes([900, 430]); root.addWidget(splitter, 1)

        bottom = QHBoxLayout(); self.timeline = QSlider(Qt.Horizontal)
        self.timeline.valueChanged.connect(self._selection_changed); bottom.addWidget(self.timeline, 1)
        bottom.addWidget(HelpButton("Selecciona el instante mostrado en las gráficas y en el desglose matemático."))
        self.play_button = QPushButton("Reproducir"); self.play_button.setCheckable(True); self.play_button.toggled.connect(self._toggle_playback); bottom.addWidget(self.play_button)
        self.playback_speed = QComboBox()
        for multiplier in (1, 2, 4):
            self.playback_speed.addItem(f"{multiplier}×", multiplier)
        self.playback_speed.currentIndexChanged.connect(self._emit_playback_speed)
        bottom.addWidget(self.playback_speed)
        bottom.addWidget(HelpButton("Multiplica la velocidad de reproducción de la telemetría guardada."))
        compare = QPushButton("Comparar telemetría")
        compare.clicked.connect(self._request_comparison); bottom.addWidget(compare)
        export = QPushButton("Exportar CSV/Parquet"); export.clicked.connect(self.export_requested.emit); bottom.addWidget(export); root.addLayout(bottom)
        self._timer = QTimer(self); self._timer.setInterval(1000 // 60); self._timer.timeout.connect(self._next_sample)
        self._serial_timer = QTimer(self); self._serial_timer.setInterval(50); self._serial_timer.timeout.connect(self._poll_serial)
        self._step_stride = 1
        self._playback_speed = 1
        self.refresh_serial_ports()

    @staticmethod
    def _plot(title: str, unit: str) -> pg.PlotWidget:
        plot = pg.PlotWidget(title=title); plot.addLegend(); plot.setLabel("left", unit); plot.setLabel("bottom", "tiempo", units="s")
        plot.showGrid(x=True, y=True, alpha=0.2)
        return plot

    def _toggle_playback(self, playing: bool) -> None:
        self.play_button.setText("Pausar" if playing else "Reproducir")
        if playing:
            if self.timeline.value() >= self.timeline.maximum(): self.timeline.setValue(0)
            self._timer.start()
        else: self._timer.stop()

    def _next_sample(self) -> None:
        if self.timeline.value() >= self.timeline.maximum():
            self.play_button.setChecked(False); return
        self.timeline.setValue(min(self.timeline.maximum(), self.timeline.value() + self._playback_speed))

    def _emit_playback_speed(self) -> None:
        self._playback_speed = int(self.playback_speed.currentData())
        self.playback_speed_changed.emit(self._playback_speed)

    def set_playback_speed(self, multiplier: int) -> None:
        index = self.playback_speed.findData(multiplier)
        if index < 0:
            return
        self._playback_speed = multiplier
        if index != self.playback_speed.currentIndex():
            self.playback_speed.blockSignals(True)
            self.playback_speed.setCurrentIndex(index)
            self.playback_speed.blockSignals(False)

    def _request_comparison(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleccionar telemetría de vuelo", "", "CSV (*.csv)")
        if path:
            self.comparison_requested.emit(path)

    def refresh_serial_ports(self) -> None:
        selected = self.port_selector.currentData()
        self.port_selector.clear()
        for device, description in available_serial_ports():
            self.port_selector.addItem(f"{device} · {description}", device)
        if selected is not None:
            index = self.port_selector.findData(selected)
            if index >= 0:
                self.port_selector.setCurrentIndex(index)
        if self.port_selector.count() == 0:
            self.port_selector.addItem("No se detectaron puertos COM", None)
        self.connect_receiver_button.setEnabled(self._serial.available and self.port_selector.currentData() is not None)

    def _toggle_receiver(self, connect: bool) -> None:
        if connect:
            try:
                port = self.port_selector.currentData()
                if not port:
                    raise RuntimeError("Selecciona un puerto COM válido")
                self._serial.open(str(port), int(self.baud_selector.currentData()))
            except Exception as exc:
                self.connect_receiver_button.blockSignals(True)
                self.connect_receiver_button.setChecked(False)
                self.connect_receiver_button.blockSignals(False)
                self.receiver_status.setText(f"● No se pudo conectar: {exc}")
                self.receiver_status.setStyleSheet("color: #e06b55; font-weight: 700;")
                return
            self._begin_live_mode()
        else:
            self._end_live_mode()

    def _begin_live_mode(self) -> None:
        self._timer.stop()
        self.play_button.setChecked(False)
        self._live_started_at = time.monotonic()
        self._live_series.clear()
        self._live_max_altitude = self._live_max_mach = self._live_max_q = self._live_max_rain = 0.0
        self._last_live_state = ""
        self.events.clear()
        self.canard_steps.setRowCount(0)
        for curve in self._all_curves():
            curve.setData([], [])
        self.port_selector.setEnabled(False)
        self.baud_selector.setEnabled(False)
        self.refresh_ports_button.setEnabled(False)
        self.connect_receiver_button.setText("Desconectar")
        self.receiver_status.setText(f"● Conectado a {self.port_selector.currentData()} · esperando paquetes")
        self.receiver_status.setStyleSheet("color: #55d58a; font-weight: 700;")
        self.live_values.setText("EN VIVO · receptor conectado; los valores aparecerán al recibir el primer paquete.")
        self.live_values.show()
        self.comparison_summary.setText("Modo en vivo activo. Los datos simulados permanecen guardados y volverán al desconectar.")
        self._serial_timer.start()

    def _end_live_mode(self) -> None:
        self._serial_timer.stop()
        self._serial.close()
        self.port_selector.setEnabled(True)
        self.baud_selector.setEnabled(True)
        self.refresh_ports_button.setEnabled(True)
        self.connect_receiver_button.setText("Conectar recibidor")
        self.receiver_status.setText("● Desconectado")
        self.receiver_status.setStyleSheet("color: #b7aaa0; font-weight: 700;")
        self.live_values.hide()
        if self._result is not None:
            result = self._result
            self.set_result(result)
        else:
            for label in self.metrics.values():
                label.setText("-")
            for curve in self._all_curves():
                curve.setData([], [])

    def _poll_serial(self) -> None:
        try:
            packets, errors = self._serial.read_available()
        except Exception as exc:
            self.receiver_status.setText(f"● Conexión perdida: {exc}")
            self.connect_receiver_button.setChecked(False)
            return
        for error in errors[-3:]:
            self.receiver_status.setText(f"● Conectado · paquete ignorado: {error}")
        for packet in packets:
            self._append_live_packet(packet)

    @staticmethod
    def _number(packet: dict[str, object], key: str, default: float = math.nan) -> float:
        try:
            return float(packet.get(key, default))
        except (TypeError, ValueError):
            return default

    def _append_live_packet(self, packet: dict[str, object]) -> None:
        elapsed = time.monotonic() - self._live_started_at
        timestamp = self._number(packet, "time_s", elapsed)
        fields = (
            "altitude_agl_m", "estimated_altitude_agl_m", "pitch_deg", "yaw_deg", "pid_pitch",
            "wind_east_mps", "wind_north_mps", "airspeed_mps", "drag_force_n", "rain_impact_force_n",
            "canard_lift_n", "friction_heat_proxy", "mass_kg", "thrust_n", "canard1_deg", "canard2_deg",
            "canard3_deg", "canard4_deg",
        )
        self._live_series["time_s"].append(timestamp)
        for key in fields:
            self._live_series[key].append(self._number(packet, key))
        time_values = list(self._live_series["time_s"])
        curves = (
            (self.altitude_truth, "altitude_agl_m"), (self.altitude_ekf, "estimated_altitude_agl_m"),
            (self.pitch_curve, "pitch_deg"), (self.yaw_curve, "yaw_deg"), (self.pid_curve, "pid_pitch"),
            (self.wind_east, "wind_east_mps"), (self.wind_north, "wind_north_mps"), (self.airspeed_curve, "airspeed_mps"),
            (self.drag_curve, "drag_force_n"), (self.rain_curve, "rain_impact_force_n"), (self.lift_curve, "canard_lift_n"),
            (self.heat_curve, "friction_heat_proxy"), (self.mass_curve, "mass_kg"), (self.thrust_curve, "thrust_n"),
        )
        for curve, key in curves:
            values = list(self._live_series[key])
            if key in {"pitch_deg", "yaw_deg"}:
                values = [math.radians(value) if math.isfinite(value) else value for value in values]
            curve.setData(time_values, values)
        for curve, key in zip(self.canard_curves, ("canard1_deg", "canard2_deg", "canard3_deg", "canard4_deg")):
            curve.setData(time_values, list(self._live_series[key]))

        altitude = self._number(packet, "altitude_agl_m", 0.0)
        mach = self._number(packet, "mach", 0.0)
        dynamic_q = self._number(packet, "dynamic_pressure_pa", 0.0)
        rain = self._number(packet, "rain_impact_force_n", 0.0)
        self._live_max_altitude = max(self._live_max_altitude, altitude)
        self._live_max_mach = max(self._live_max_mach, mach)
        self._live_max_q = max(self._live_max_q, dynamic_q)
        self._live_max_rain = max(self._live_max_rain, rain)
        state = str(packet.get("state", "telemetría en vivo"))
        self.metrics["Apogeo AGL"].setText(f"{self._live_max_altitude:.1f} m")
        self.metrics["Mach"].setText(f"{self._live_max_mach:.3f}")
        self.metrics["q dinamico"].setText(f"{self._live_max_q:.0f} Pa")
        self.metrics["Duracion de vuelo"].setText(f"{timestamp:.2f} s")
        self.metrics["Lluvia maxima"].setText(f"{self._live_max_rain:.3f} N")
        self.metrics["Clasificacion"].setText(state)
        if state and state != self._last_live_state:
            self.events.addItem(f"{timestamp:.2f} s · {state}")
            self.events.scrollToBottom()
            self._last_live_state = state

        battery = self._number(packet, "battery_v")
        rssi = self._number(packet, "rssi_dbm")
        self.live_values.setText(
            f"EN VIVO · t={timestamp:.2f} s · altitud={altitude:.2f} m · Mach={mach:.3f} · q={dynamic_q:.1f} Pa · "
            f"batería={'—' if not math.isfinite(battery) else f'{battery:.2f} V'} · "
            f"RSSI={'—' if not math.isfinite(rssi) else f'{rssi:.0f} dBm'}"
        )
        self.current_values.setText(self.live_values.text())
        self.math.setPlainText("ÚLTIMO PAQUETE NORMALIZADO DE LA ESP\n\n" + "\n".join(f"{key}: {value}" for key, value in sorted(packet.items())))
        self._append_live_table_row(timestamp, packet)
        self.receiver_status.setText(f"● Conectado a {self.port_selector.currentData()} · recibiendo")

    def _append_live_table_row(self, timestamp: float, packet: dict[str, object]) -> None:
        row = self.canard_steps.rowCount()
        if row >= 500:
            self.canard_steps.removeRow(0)
            row -= 1
        self.canard_steps.insertRow(row)
        values = [timestamp, *(self._number(packet, f"canard{index}_deg") for index in range(1, 5)), packet.get("pid_pitch", "—")]
        for column, value in enumerate(values):
            text = f"{value:.2f}" if isinstance(value, (float, int)) else str(value)
            self.canard_steps.setItem(row, column, QTableWidgetItem(text))

    def _all_curves(self) -> list[object]:
        return [
            self.altitude_truth, self.altitude_ekf, self.pitch_curve, self.yaw_curve, self.pid_curve,
            self.wind_east, self.wind_north, self.airspeed_curve, self.drag_curve, self.rain_curve,
            self.lift_curve, self.heat_curve, self.mass_curve, self.thrust_curve, *self.canard_curves,
        ]

    def closeEvent(self, event: object) -> None:
        self._serial_timer.stop()
        self._serial.close()
        super().closeEvent(event)

    def set_comparison(self, comparison: object) -> None:
        errors = comparison.error
        self.comparison_summary.setText(
            "Validación contra telemetría · " + " · ".join(
                f"{name}: {value:+.2f}" for name, value in errors.items()
            )
        )

    def _selection_changed(self, index: int) -> None:
        self.update_selection(index)
        self.sample_selected.emit(index)

    def set_result(self, result: object) -> None:
        self._result = result
        if self._serial.is_open:
            return
        samples = result.samples
        if not samples: return
        time = np.asarray([sample.time_s for sample in samples])
        altitude = np.asarray([sample.altitude_agl_m for sample in samples])
        estimated = np.asarray([sample.estimated_altitude_agl_m for sample in samples])
        eulers = np.asarray([sample.euler_rad for sample in samples]); pid = np.asarray([sample.pid_output for sample in samples])
        wind = np.asarray([sample.wind_enu_mps for sample in samples]); canards = np.degrees(np.asarray([sample.canard_deflection_rad for sample in samples]))
        self.altitude_truth.setData(time, altitude); self.altitude_ekf.setData(time, estimated)
        self.pitch_curve.setData(time, eulers[:, 1]); self.yaw_curve.setData(time, eulers[:, 2]); self.pid_curve.setData(time, pid[:, 0])
        self.wind_east.setData(time, wind[:, 0]); self.wind_north.setData(time, wind[:, 1]); self.airspeed_curve.setData(time, [sample.airspeed_mps for sample in samples])
        self.drag_curve.setData(time, [sample.drag_force_n for sample in samples]); self.rain_curve.setData(time, [sample.rain_impact_force_n for sample in samples]); self.lift_curve.setData(time, [sample.canard_lift_n for sample in samples])
        self.heat_curve.setData(time, [sample.friction_heat_proxy for sample in samples]); self.mass_curve.setData(time, [sample.mass_kg for sample in samples]); self.thrust_curve.setData(time, [sample.thrust_n for sample in samples])
        for index, curve in enumerate(self.canard_curves): curve.setData(time, canards[:, index])
        last = samples[-1]
        self.metrics["Apogeo AGL"].setText(f"{max(altitude):.1f} m"); self.metrics["Mach"].setText(f"{max(sample.mach for sample in samples):.3f}")
        self.metrics["q dinamico"].setText(f"{max(sample.dynamic_pressure_pa for sample in samples):.0f} Pa"); self.metrics["Duracion de vuelo"].setText(f"{last.time_s:.2f} s")
        self.metrics["Lluvia maxima"].setText(f"{max(sample.rain_impact_force_n for sample in samples):.3f} N")
        self.metrics["Clasificacion"].setText(result.classification)
        self.events.clear(); self.events.addItems(result.events)
        self._timer.stop(); self.play_button.setChecked(False); self.timeline.setRange(0, len(samples) - 1); self.timeline.setValue(0)
        self._populate_canard_steps(samples, canards)

    def _populate_canard_steps(self, samples: object, canards: np.ndarray) -> None:
        self._step_stride = max(1, len(samples) // 350)
        indices = range(0, len(samples), self._step_stride)
        self.canard_steps.setRowCount(len(range(0, len(samples), self._step_stride)))
        for row, sample_index in enumerate(indices):
            sample = samples[sample_index]
            values = [f"{sample.time_s:.2f}", *(f"{angle:.1f}°" for angle in canards[sample_index]), f"{sample.pid_output[0]:.3f} / {sample.pid_output[1]:.3f}"]
            for column, value in enumerate(values): self.canard_steps.setItem(row, column, QTableWidgetItem(value))
        self.canard_steps.resizeColumnsToContents()

    def update_selection(self, index: int) -> None:
        if not self._result or not 0 <= index < len(self._result.samples): return
        sample = self._result.samples[index]
        for line in self._plot_lines: line.setValue(sample.time_s)
        angles = [float(np.degrees(value)) for value in sample.canard_deflection_rad]
        wind = sample.wind_enu_mps
        self.current_values.setText(f"t={sample.time_s:.2f} s | viento ENU=({wind[0]:.2f}, {wind[1]:.2f}, {wind[2]:.2f}) m/s | paracaidas={'SI' if sample.parachute_deployed else 'NO'}")
        self.math.setPlainText(
            "CALCULOS DEL NUCLEO C++ EN ESTA MUESTRA\n\n"
            "Estado: x=[r_ENU, v_ENU, q_cuerpo_a_ENU, omega_cuerpo]\n"
            "RK4: x(k+1)=x(k)+dt/6*(k1+2k2+2k3+k4)\n"
            "q_dot = 1/2 q x [0,omega]\n"
            "a = (F_empuje + F_aero + F_paracaidas + m*g) / m\n"
            "omega_dot = I^-1 (M - omega x (I omega))\n\n"
            f"V_rel = v - viento = {sample.airspeed_mps:.3f} m/s\n"
            f"q = 1/2 rho V_rel^2 = {sample.dynamic_pressure_pa:.3f} Pa\n"
            f"D = q S Cd = {sample.drag_force_n:.3f} N\n"
            f"F_lluvia = q S DeltaCd_lluvia = {sample.rain_impact_force_n:.5f} N\n"
            f"Q_friccion (proxy) = h_c V_rel^2 = {sample.friction_heat_proxy:.6f}\n"
            f"L_canard = q S_c CLalpha delta = {sample.canard_lift_n:.3f} N\n"
            f"Margen estatico = (x_cp - x_cg) / D = {sample.static_margin_calibers:.3f} calibres\n"
            "Mezcla cuatro canards: [pitch, yaw, -pitch, -yaw], limitada a +/-15 grados\n"
            f"PID: u = Kp e + Ki integral(e) + Kd de/dt = ({sample.pid_output[0]:.4f}, {sample.pid_output[1]:.4f}) rad\n\n"
            f"Canard 1={angles[0]:.2f}°, C2={angles[1]:.2f}°, C3={angles[2]:.2f}°, C4={angles[3]:.2f}°\n"
            f"Error controlador pitch/yaw = ({sample.controller_error_rad[1]:.4f}, {sample.controller_error_rad[2]:.4f}) rad\n"
            f"Empuje={sample.thrust_n:.2f} N, masa={sample.mass_kg:.3f} kg, Mach={sample.mach:.3f}"
        )
        row = min(index // self._step_stride, self.canard_steps.rowCount() - 1)
        if row >= 0: self.canard_steps.selectRow(row)
