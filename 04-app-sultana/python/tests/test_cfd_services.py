from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
import shutil
import zipfile
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app.services.cfd import (CANARD_ZONE_BOXES, CanardMotionInterval, CfdCaseRequest, CfdResult, CfdRunFailure, DockerStatus, _motion_table,
                               _force_stability_ratio, _preliminary_local_result, _residual_ratio, cfd_failure_detail, collapsed_timestep, detect_canard_motion_intervals,
                               docker_command, export_result_bundle, parse_result, prepare_case, run_case,
                               mesh_preflight_passes, recovery_force_from_cds,
                               schedule_for_motion_interval, select_execution_backend, speed_schedule_for_motion_interval,
                               select_flight_phase_snapshots, select_representative_snapshots, validate_canard_zone_boxes,
                               vector_schedule_for_motion_interval)
from app.services.config_loader import load_scenario
from app.services.scenario_store import save_laboratory_scenario
import app.ui.cfd_tab as cfd_tab_module
from app.ui.cfd_tab import CfdTab, body_relative_velocity_from_sample, canard_correction_label
from app.ui.cfd_viewport import (
    CFD_VISUAL_MODES, CfdViewport, DISPLAY_LAYERS, aerodynamic_force_vectors, aerodynamic_metrics, flow_direction,
    robust_scalar_range,
)
from app.ui.rocket_viewport import RocketViewport


ROOT = Path(__file__).resolve().parents[2]


def _request() -> CfdCaseRequest:
    return CfdCaseRequest("prueba cfd", "steady", 42.0, 3.0, 1.0, 0.0, (1.0, -1.0, -1.0, 1.0), False)


def test_cfd_case_is_prepared_with_safe_docker_command(tmp_path: Path) -> None:
    case_dir = prepare_case(tmp_path, _request())
    # tmp root deliberately has no OBJ: case creation remains testable without VTK.
    assert (case_dir / "system" / "controlDict").is_file()
    assert "simpleFoam" in (case_dir / "system" / "controlDict").read_text(encoding="utf-8")
    command = docker_command(case_dir, _request())
    assert command[:2] == [command[0], "run"]
    assert "--cpus" in command and "--memory" in command and f"FOAM_NPROCS={_request().cores}" in command and "bash" in command
    allrun = (case_dir / "Allrun").read_text(encoding="utf-8")
    assert "decomposePar -force" in allrun
    assert "snappyHexMesh -overwrite" in allrun
    assert "decomposePar -force" in allrun.split("snappyHexMesh -overwrite", 1)[1]
    assert "checkMesh -parallel" in allrun and "mpirun" in allrun
    assert "simpleFoam -parallel" in allrun
    assert "surfaceCheck constant/triSurface/rocket.stl" in allrun
    assert "CFD_PRECHECK_GEOMETRY_ERROR" in allrun and "CFD_PRECHECK_MESH_ERROR" in allrun
    assert "checkMesh -meshQuality | tee mesh-preflight.log" in allrun
    assert "minFaceWeight 0.05" in (case_dir / "system" / "meshQualityDict").read_text(encoding="utf-8")


def test_selected_cad_stl_is_copied_into_the_audited_case_and_missing_input_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "cohete-cad.stl"
    source.write_text("solid rocket\nendsolid rocket\n", encoding="utf-8")
    request = CfdCaseRequest("cad", "steady", 25, 0, 0, 0, (0, 0, 0, 0), False, rocket_stl_path=str(source))
    case_dir = prepare_case(tmp_path, request)
    assert (case_dir / "constant/triSurface/rocket.stl").read_bytes() == source.read_bytes()
    missing = CfdCaseRequest("sin-cad", "steady", 25, 0, 0, 0, (0, 0, 0, 0), False, rocket_stl_path=str(tmp_path / "ausente.stl"))
    with pytest.raises(ValueError, match="archivo STL existente"):
        prepare_case(tmp_path, missing)
    assert not (tmp_path / "out/cfd/sin-cad").exists()


def test_collapsed_timestep_is_detected_before_a_cfd_job_loops_indefinitely() -> None:
    assert collapsed_timestep("deltaT = 5.71128e-36") == pytest.approx(5.71128e-36)
    assert collapsed_timestep("deltaT = 0.0001") is None
    assert collapsed_timestep("Time = 0.0272811") is None


def test_exit_134_reports_the_openfoam_phase_log_and_recovers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FailedProcess:
        stdout = iter(("Exec : checkMesh -parallel\n", "FOAM aborting\n"))
        returncode = 134

        @staticmethod
        def wait() -> int:
            return 134

    import app.services.cfd as cfd_module
    monkeypatch.setattr(cfd_module, "docker_status", lambda: DockerStatus(True, "Docker listo", True))
    monkeypatch.setattr(cfd_module.subprocess, "Popen", lambda *_args, **_kwargs: FailedProcess())
    with pytest.raises(CfdRunFailure, match="validación de malla.*código 134.*run.log"):
        run_case(tmp_path, _request(), lambda _line: None, __import__("threading").Event())
    assert "FOAM aborting" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_exit_134_reenables_cfd_controls_and_shows_the_actionable_failure() -> None:
    class Button:
        def __init__(self) -> None: self.enabled = False
        def setEnabled(self, value: bool) -> None: self.enabled = value

    class Label:
        def __init__(self) -> None: self.text = ""
        def setText(self, value: str) -> None: self.text = value
        def setStyleSheet(self, _value: str) -> None: pass

    class Progress:
        def setVisible(self, _value: bool) -> None: pass
        def setRange(self, _low: int, _high: int) -> None: pass

    tab = CfdTab.__new__(CfdTab)
    tab.run_button, tab.cancel_button, tab.status, tab.run_progress = Button(), Button(), Label(), Progress()
    tab.run_announcement = Label()
    failure = CfdRunFailure("validación de malla", "OpenFOAM terminó con código 134; revisa checkMesh", Path("run.log"), "FOAM aborting")
    tab.set_run_finished(None, f"CFD no ejecutado: {failure}")
    assert tab.run_button.enabled and not tab.cancel_button.enabled
    assert "validación de malla" in tab.status.text and "run.log" in tab.status.text


def test_gpu_backend_is_never_claimed_without_a_cuda_solver_and_runtime() -> None:
    cpu = select_execution_backend(cuda_solver_available=False, gpu_runtime_available=True, mpi_cores=6)
    gpu = select_execution_backend(cuda_solver_available=True, gpu_runtime_available=True, mpi_cores=6)
    assert cpu.label == "CPU paralelo" and not cpu.uses_gpu
    assert gpu.label == "GPU" and gpu.uses_gpu


def test_dynamic_canard_mapping_uses_the_same_transformed_axes_and_disjoint_zones_as_the_viewport(tmp_path: Path) -> None:
    validate_canard_zone_boxes()
    assert all(
        not all(max(left[0][axis], right[0][axis]) < min(left[1][axis], right[1][axis]) for axis in range(3))
        for index, left in enumerate(CANARD_ZONE_BOXES) for right in CANARD_ZONE_BOXES[index + 1:]
    )
    table_1 = _motion_table(((0.0, 8.0, 0.0, 0.0, 0.0),), 0)
    table_3 = _motion_table(((0.0, 0.0, 0.0, 8.0, 0.0),), 2)
    assert "(0 0 -0.13962634)" in table_1 and "(0 0 0.13962634)" in table_3
    request = CfdCaseRequest("motion mapping", "transient", 42, 0, 0, 0, (0, 0, 0, 0), False)
    case_dir = prepare_case(tmp_path, request)
    dynamic = (case_dir / "constant" / "dynamicMeshDict").read_text(encoding="utf-8")
    topo = (case_dir / "system" / "topoSetDict").read_text(encoding="utf-8")
    assert "CofG (0.1287 0 -0.0354)" in dynamic
    assert "box (0.02 -0.02 -0.096) (0.151 0.02 -0.02)" in topo


def test_geometry_preflight_failure_explains_how_to_fix_the_stl() -> None:
    detail = cfd_failure_detail(20, "validación de geometría STL", "CFD_PRECHECK_ERROR: STL no estanco")
    assert "código 20" in detail and "sólido estanco" in detail and "solap" in detail


def test_cfd_case_uses_the_declared_solver_for_each_physics_mode(tmp_path: Path) -> None:
    transient = prepare_case(tmp_path, CfdCaseRequest("transient", "transient", 42, 0, 0, 0, (0, 0, 0, 0), False, canard_schedule=((0, 0, 0, 0, 0),)))
    rain = prepare_case(tmp_path, CfdCaseRequest("rain", "steady", 42, 0, 0, 4, (0, 0, 0, 0), True))
    assert "pimpleFoam" in (transient / "Allrun").read_text(encoding="utf-8")
    assert "overInterDyMFoam" in (rain / "Allrun").read_text(encoding="utf-8")


def test_transient_case_generates_four_pid_motion_zones_and_time_exports(tmp_path: Path) -> None:
    request = CfdCaseRequest(
        "moving", "transient", 42, 0, 0, 0, (0, 0, 0, 0), False,
        canard_schedule=((0.0, 0.0, 0.0, 0.0, 0.0), (0.5, 8.0, -4.0, -8.0, 4.0)),
    )
    case_dir = prepare_case(tmp_path, request)
    dynamic = (case_dir / "constant" / "dynamicMeshDict").read_text(encoding="utf-8")
    assert "dynamicMotionSolverFvMesh" in dynamic
    assert all(f"canard{index}" in dynamic for index in range(1, 5))
    assert "0.13962634" in (case_dir / "constant" / "canardMotion1.dat").read_text(encoding="utf-8")
    allrun = (case_dir / "Allrun").read_text(encoding="utf-8")
    assert "topoSet" in allrun and "foamToVTK -allPatches" in allrun
    assert allrun.index("topoSet") < allrun.index("simpleFoam")
    assert "mv constant/dynamicMeshDict constant/dynamicMeshDict.transient" in allrun
    assert "mv constant/dynamicMeshDict.transient constant/dynamicMeshDict" in allrun
    assert "-time" not in allrun.split("foamToVTK -allPatches", 1)[1]
    assert "rocketSurface" in (case_dir / "system" / "controlDict").read_text(encoding="utf-8")


def test_detailed_case_records_mesh_turbulence_and_pressure_density_convention(tmp_path: Path) -> None:
    case_dir = prepare_case(tmp_path, _request())
    control = (case_dir / "system" / "controlDict").read_text(encoding="utf-8")
    mesh = (case_dir / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
    audit = (case_dir / "constant" / "laboratory.json").read_text(encoding="utf-8")
    assert "rho rhoInf" in control and "vorticity" in control
    assert "addLayers true" in mesh and "level (4 5)" in mesh
    assert "air_density_kg_m3" in audit


def test_cfd_uses_weather_gust_turbulence_to_initialize_rans_fields(tmp_path: Path) -> None:
    request = CfdCaseRequest("gust", "steady", 20, 0, 0, 0, (0, 0, 0, 0), False, turbulence_intensity_mps=2.0)
    case_dir = prepare_case(tmp_path, request)
    turbulence = (case_dir / "constant/laboratory.json").read_text(encoding="utf-8")
    k_field = (case_dir / "0/k").read_text(encoding="utf-8")
    assert '"turbulence_intensity_mps": 2.0' in turbulence
    assert "internalField uniform 6" in k_field


def test_cfd_inlet_respects_alpha_and_beta(tmp_path: Path) -> None:
    angled = prepare_case(
        tmp_path,
        CfdCaseRequest("angled", "steady", 20, 30, 20, 0, (0, 0, 0, 0), False),
    )
    velocity = (angled / "0/U").read_text(encoding="utf-8")
    assert "uniform (16.2759536 6.84040287 10)" in velocity


def test_transient_cfd_inlet_uses_the_6dof_airspeed_history(tmp_path: Path) -> None:
    case_dir = prepare_case(tmp_path, CfdCaseRequest(
        "airspeed-history", "transient", 9, 0, 0, 0, (0, 0, 0, 0), False,
        canard_schedule=((0, 0, 0, 0, 0), (1, 1, 0, -1, 0)),
        speed_schedule=((0, 3), (0.5, 22), (1, 7)),
    ))
    velocity = (case_dir / "0/U").read_text(encoding="utf-8")
    assert "uniformFixedValue" in velocity
    assert "(0 (3 0 0))" in velocity
    assert "(0.5 (22 0 0))" in velocity
    assert "(1 (7 0 0))" in velocity


def test_transient_cfd_body_velocity_table_preserves_lateral_wind_component(tmp_path: Path) -> None:
    case_dir = prepare_case(tmp_path, CfdCaseRequest(
        "body-velocity", "transient", 9, 0, 0, 0, (0, 0, 0, 0), False,
        inlet_velocity_schedule=((0, 12, -3, 1), (1, 25, 4, -2)),
    ))
    velocity = (case_dir / "0/U").read_text(encoding="utf-8")
    assert "(0 (12 -3 1))" in velocity
    assert "(1 (25 4 -2))" in velocity


def test_rans_turbulence_ratio_uses_the_first_6dof_inlet_vector(tmp_path: Path) -> None:
    case_dir = prepare_case(tmp_path, CfdCaseRequest(
        "vector-turbulence", "transient", 100, 0, 0, 0, (0, 0, 0, 0), False,
        inlet_velocity_schedule=((0, 3, 4, 0),), turbulence_intensity_mps=1.0,
    ))
    audit = (case_dir / "constant/laboratory.json").read_text(encoding="utf-8")
    assert '"raw_inlet_turbulence_intensity": 0.2' in audit
    assert '"inlet_turbulence_intensity": 0.1' in audit
    assert "internalField uniform 0.375" in (case_dir / "0/k").read_text(encoding="utf-8")


def test_live_flow_metrics_and_direction_respond_to_conditions() -> None:
    direction = flow_direction(12.0, -8.0)
    dry = aerodynamic_metrics(30.0, 12.0, -8.0, (4.0, -3.0, -4.0, 3.0), 0.0)
    wet = aerodynamic_metrics(30.0, 12.0, -8.0, (4.0, -3.0, -4.0, 3.0), 8.0)
    assert sum(component * component for component in direction) == pytest.approx(1.0)
    assert dry["lift_n"] > 0.0
    assert dry["vorticity_s"] > 0.0
    assert wet["drag_n"] > dry["drag_n"]


def test_integrated_forces_are_projected_on_the_real_inlet_basis() -> None:
    # U∞ is diagonal, so no Cartesian component is itself "drag".
    components = aerodynamic_force_vectors((3.0, 4.0, 5.0), (-6.0, 8.0, 0.0))
    assert components["drag"] == pytest.approx((-0.84, 1.12, 0.0))
    reconstructed = components["drag"] + components["side"] + components["lift"]
    assert reconstructed == pytest.approx((3.0, 4.0, 5.0))


def test_snapshot_uses_freestream_on_all_outer_boundaries(tmp_path: Path) -> None:
    case_dir = prepare_case(tmp_path, CfdCaseRequest(
        "oblique", "snapshot", 10, 0, 0, 0, (0, 0, 0, 0), False,
        inlet_velocity_schedule=((0, -8, 3, 5),),
    ))
    block = (case_dir / "system/blockMeshDict").read_text(encoding="utf-8")
    velocity = (case_dir / "0/U").read_text(encoding="utf-8")
    pressure = (case_dir / "0/p").read_text(encoding="utf-8")
    assert "farfield { type patch" in block and "walls" not in block
    assert velocity.count("type freestream") == 3
    assert velocity.count("freestreamValue uniform (-8 3 5)") == 3
    assert pressure.count("type freestreamPressure") == 3


def test_weather_direction_and_pid_action_are_preserved() -> None:
    action = canard_correction_label((5.0, -2.0, -5.0, 2.0), (4.0, -1.0, -4.0, 1.0))
    assert "pitch +5.00°" in action
    assert "yaw −2.00°" in action
    assert "C1↑" in action and "C3↓" in action


def test_canard_motion_intervals_merge_nearby_actions_and_keep_stabilizing_margins() -> None:
    schedule = (
        (0.0, 0, 0, 0, 0), (0.1, 1, 0, 0, 0), (0.2, 2, 0, 0, 0),
        (1.0, 2, 0, 0, 0), (1.1, 2, -2, 0, 0), (1.2, 2, -4, 0, 0),
    )
    intervals = detect_canard_motion_intervals(schedule, threshold_deg=0.5, merge_gap_s=0.25, margin_s=0.1)
    assert [(interval.start_s, interval.end_s, interval.canard_indices) for interval in intervals] == [
        (0.0, 0.30000000000000004, (0,)), (0.9, 1.2, (1,)),
    ]
    local = schedule_for_motion_interval(schedule, intervals[1])
    assert local[0] == pytest.approx((0.0, 2, 0, 0, 0))
    assert local[-1][0] == pytest.approx(0.3)
    local_speed = speed_schedule_for_motion_interval(((0.0, 5), (1.0, 15), (1.2, 25)), intervals[1])
    assert local_speed[0] == pytest.approx((0.0, 14.0))
    assert local_speed[-1] == pytest.approx((0.3, 25.0))
    local_vector = vector_schedule_for_motion_interval(((0.0, 5, 1, 0), (1.0, 15, -3, 4), (1.2, 25, 0, 8)), intervals[1])
    assert local_vector[0] == pytest.approx((0.0, 14.0, -2.6, 3.6))
    assert local_vector[-1] == pytest.approx((0.3, 25.0, 0.0, 8.0))


def test_motion_interval_tables_drop_near_duplicate_endpoints_required_by_openfoam() -> None:
    interval = CanardMotionInterval(0.0, 1.000000000000001, (0,))
    vector = vector_schedule_for_motion_interval(
        ((0.0, 5.0, 0.0, 0.0), (1.0000000000000002, 10.0, 1.0, 0.0)), interval,
    )
    speed = speed_schedule_for_motion_interval(((0.0, 5.0), (1.0000000000000002, 10.0)), interval)
    assert len(vector) == len(speed) == 2
    assert vector[-1][0] == pytest.approx(interval.end_s)
    assert speed[-1][0] == pytest.approx(interval.end_s)


def test_phase_snapshots_make_five_static_requests() -> None:
    details = tuple({"time_s": float(index), "parachute_deployed": index >= 3} for index in range(7))
    captured: list[dict[str, object]] = []
    tab = SimpleNamespace(_flight_details=details, case_name=SimpleNamespace(text=lambda: "canards"))
    tab._request = lambda **kwargs: captured.append(kwargs) or object()
    assert len(CfdTab._requests(tab)) == 5
    assert [entry["snapshot_time_s"] for entry in captured] == [0.0, 1.0, 3.0, 4.0, 6.0]
    assert [entry["snapshot_reason"] for entry in captured] == [
        "despegue", "intermedio entre despegue y paracaídas", "despliegue de paracaídas",
        "intermedio entre paracaídas y aterrizaje", "aterrizaje",
    ]


def test_phase_snapshots_require_parachute_deployment() -> None:
    tab = SimpleNamespace(_flight_details=tuple({"time_s": float(index), "parachute_deployed": False} for index in range(6)))
    with pytest.raises(ValueError, match="no desplegó paracaídas"):
        CfdTab._requests(tab)


def test_cfd_tab_exposes_only_the_fixed_five_phase_workflow() -> None:
    source = Path(cfd_tab_module.__file__).read_text(encoding="utf-8")
    for obsolete_control in (
        "simple_view_button", "advanced_group", "_toggle_advanced", "case_mode",
        "transient_scope", "interval_list", "motion_intervals",
    ):
        assert obsolete_control not in source
    assert "Snapshots automáticos" in source
    assert "5 snapshots estáticos de fase" in source


def test_flight_phase_snapshot_selection_uses_ordered_distinct_telemetry_states() -> None:
    snapshots = select_flight_phase_snapshots(tuple(
        {"time_s": float(index), "parachute_deployed": index >= 3} for index in range(7)
    ))
    assert [snapshot.index for snapshot in snapshots] == [0, 1, 3, 4, 6]
    assert [snapshot.source_time_s for snapshot in snapshots] == [0.0, 1.0, 3.0, 4.0, 6.0]
    assert [snapshot.reason for snapshot in snapshots] == [
        "despegue", "intermedio entre despegue y paracaídas", "despliegue de paracaídas",
        "intermedio entre paracaídas y aterrizaje", "aterrizaje",
    ]


def test_body_relative_velocity_uses_the_inverse_body_to_enu_quaternion() -> None:
    identity = SimpleNamespace(
        relative_velocity_enu_mps=(10.0, -2.0, 1.0),
        quaternion=SimpleNamespace(w=1.0, x=0.0, y=0.0, z=0.0),
    )
    assert body_relative_velocity_from_sample(identity) == pytest.approx((-10.0, 2.0, -1.0))
    # 180° body-to-ENU yaw maps vehicle ENU +X to body -X; OpenFOAM needs
    # the opposite air velocity, body +X.
    yaw_180 = SimpleNamespace(
        relative_velocity_enu_mps=(10.0, 0.0, 0.0),
        quaternion=SimpleNamespace(w=0.0, x=0.0, y=0.0, z=1.0),
    )
    assert body_relative_velocity_from_sample(yaw_180) == pytest.approx((10.0, 0.0, 0.0))


def test_cfd_request_carries_the_stl_selected_in_the_interface() -> None:
    class Value:
        def __init__(self, value: float) -> None: self._value = value
        def value(self) -> float: return self._value

    class Text:
        def __init__(self, value: str) -> None: self._value = value
        def text(self) -> str: return self._value

    tab = SimpleNamespace(
        _flight_details=({
            "time_s": 1.0, "canards": (1.0, -2.0, 3.0, -4.0),
            "body_velocity_mps": (12.0, -3.0, 1.0), "wind_enu_mps": (1.0, 0.0, 0.0),
            "temperature_k": 286.0, "pressure_pa": 90000.0, "humidity_ratio": 0.5,
            "parachute_cds_m2": 0.25, "cg_from_nose_m": 0.63,
        },),
        rain=Value(0), _weather_source="prueba",
        surface_stl_path=Text("C:/CAD/cohete-estanco.stl"),
    )
    tab._weather_value = lambda _field, fallback: fallback
    request = CfdTab._request(tab, case_name="con-cad-fase-1", snapshot_time_s=1.0, snapshot_reason="despegue")
    assert request.rocket_stl_path == "C:/CAD/cohete-estanco.stl"
    assert request.mode == "snapshot" and request.execution_scope == "snapshot"
    assert request.canard_deg == (1.0, -2.0, 3.0, -4.0)
    assert request.inlet_velocity_schedule == ((0.0, 12.0, -3.0, 1.0),)
    assert request.recovery_cds_m2 == pytest.approx(0.25)
    assert request.center_of_gravity_body_m == pytest.approx((-0.179, 0.0, 0.0))


def test_color_layer_filters_cover_each_visual_quantity() -> None:
    assert {"flow", "vortices", "particles", "rain", "drag", "lift", "side", "friction", "cfd_field"} <= set(DISPLAY_LAYERS)
    viewport = CfdViewport.__new__(CfdViewport)
    viewport._layer_visibility = {key: True for key in DISPLAY_LAYERS}
    viewport._plotter = None
    viewport._streamline_names = []
    viewport._vortex_names = []
    viewport._force_actor_names = {}
    viewport._force_metrics = {}
    viewport.set_layer_visible("drag", False)
    assert not viewport.layer_visible("drag")
    with pytest.raises(ValueError, match="Capa CFD desconocida"):
        viewport.set_layer_visible("magenta", True)


def test_cfd_magnitude_selector_is_exclusive_and_covers_all_audited_views() -> None:
    _application = QApplication.instance() or QApplication([])
    _group, buttons = CfdTab._layer_controls()
    assert set(buttons) == {
        "velocity", "pressure", "vorticity", "turbulence",
        "drag", "lift", "side", "friction", "moment",
    }
    assert set(buttons) == set(CFD_VISUAL_MODES)
    assert [key for key, button in buttons.items() if button.isChecked()] == ["velocity"]
    buttons["pressure"].click()
    assert [key for key, button in buttons.items() if button.isChecked()] == ["pressure"]


def test_cfd_frame_renderer_builds_only_the_selected_memory_bounded_view() -> None:
    source = inspect.getsource(CfdViewport.set_cfd_frame)
    assert 'self._cfd_layer in {"velocity", "vorticity", "turbulence"}' in source
    assert "_render_active_cfd_mode" in source
    assert "_render_velocity_glyphs" not in source


def test_cfd_robust_scalar_range_ignores_non_finite_outliers_and_centres_pressure() -> None:
    import numpy as np

    low, high = robust_scalar_range([-10.0, -2.0, 0.0, 2.0, 10.0, np.nan], symmetric=True)
    assert low == pytest.approx(-high)
    assert 8.0 < high <= 10.0


def test_streamline_rake_is_placed_upstream_of_actual_openfoam_velocity() -> None:
    import numpy as np
    import pyvista as pv

    viewport = CfdViewport.__new__(CfdViewport)
    viewport._pv = pv
    field = SimpleNamespace(point_data={"U": np.tile((-20.0, 0.0, 0.0), (20, 1))})

    seeds = viewport._streamline_seed_cloud(field)

    assert seeds.n_points == 64
    assert float(np.mean(seeds.points[:, 0])) > 0.60
    assert float(np.max(np.abs(seeds.points[:, 1]))) == pytest.approx(0.205, abs=1e-6)


def test_detailed_cfd_views_use_ranked_pathlines_and_surface_pressure_without_vortex_mass() -> None:
    source = Path(cfd_tab_module.__file__).with_name("cfd_viewport.py").read_text(encoding="utf-8")
    assert 'integration_direction = "forward"' in source
    assert 'cmap="coolwarm"' in source and "Presión manométrica" in source
    assert "_ranked_streamline_cells" in source and "Sin envolventes ni volúmenes decorativos" in source
    assert "_wake_seed_cloud" in source
    assert '("vorticity", "curlU")' in source
    assert 'vector_name = str(vortex_vectors)' in source
    vorticity_source = inspect.getsource(CfdViewport._add_real_vorticity_surface)
    assert 'scalars="Q"' not in vorticity_source
    assert "_add_vortex_rotation_rings" in vorticity_source
    assert "Anillo y flecha = sentido local de curl(U)" in source


def test_detailed_cfd_viewer_uses_neutral_background_and_clears_preview_text() -> None:
    source = Path(cfd_tab_module.__file__).with_name("cfd_viewport.py").read_text(encoding="utf-8")
    assert 'self._plotter.set_background("#f3f5f7", top="#cfd5db")' in source
    assert '"preview-notice", "cfd-caption"' in source
    assert 'name="force-legend", position="upper_right"' in source
    assert 'color="#17212b"' in source
    assert '"#087da8", "#dd642b", "#285f78"' in source


def test_turbulence_view_is_derived_from_openfoam_k_and_velocity() -> None:
    source = inspect.getsource(CfdViewport._discover_cfd_fields)
    assert 'field.point_data["k"]' in source
    assert 'field.point_data["U"]' in source
    assert 'field.point_data["turbulence_intensity_pct"]' in source


def test_cfd_and_canard_playback_have_independent_controls() -> None:
    source = Path(cfd_tab_module.__file__).read_text(encoding="utf-8")
    assert 'QPushButton("Reproducir CFD")' in source
    assert 'QPushButton("Reproducir canards")' in source
    assert "def _toggle_cfd_play" in source and "def _toggle_canards_play" in source
    assert "Reproducir CFD + canards" not in source


def test_color_layer_filters_hide_only_the_selected_actors() -> None:
    class Actor:
        def __init__(self) -> None:
            self.visible = True

        def SetVisibility(self, visible: bool) -> None:
            self.visible = visible

    class Plotter:
        def __init__(self) -> None:
            self.actors = {"streamline-0": Actor(), "force-0": Actor()}
            self.scalar_bars = {"Velocidad (m/s)": Actor()}

        def add_text(self, *_args: object, **_kwargs: object) -> None:
            pass

        def render(self) -> None:
            pass

    viewport = CfdViewport.__new__(CfdViewport)
    viewport._layer_visibility = {key: True for key in DISPLAY_LAYERS}
    viewport._plotter = Plotter()
    viewport._streamline_names = ["streamline-0"]
    viewport._vortex_names = []
    viewport._force_actor_names = {"drag": "force-0"}
    viewport._force_metrics = {"drag_n": 1.0}
    viewport.set_layer_visible("flow", False)
    assert not viewport._plotter.actors["streamline-0"].visible
    assert not viewport._plotter.scalar_bars["Velocidad (m/s)"].visible
    assert viewport._plotter.actors["force-0"].visible
    viewport.set_layer_visible("drag", False)
    assert not viewport._plotter.actors["force-0"].visible


def test_canard_hinges_stay_at_the_body_side_while_the_fin_rotates() -> None:
    bounds = (-0.4, 2.7, -2.7, 0.4, 61.3, 64.3)
    assert tuple(CfdViewport._canard_hinge_pivot(0, bounds)) == pytest.approx((-0.4, -1.15, 64.3))
    assert tuple(CfdViewport._canard_hinge_pivot(1, bounds)) == pytest.approx((1.15, -2.7, 64.3))
    assert tuple(CfdViewport._canard_hinge_pivot(2, bounds)) == pytest.approx((2.7, -1.15, 64.3))
    assert tuple(CfdViewport._canard_hinge_pivot(3, bounds)) == pytest.approx((1.15, 0.4, 64.3))


def test_primary_stl_canard_tip_moves_but_its_hinge_does_not() -> None:
    import numpy as np
    import pyvista as pv

    mesh = pv.read(ROOT / "data/models/ensamble_todo_v2.stl")
    split = CfdViewport._stl_components(mesh)
    assert split is not None
    _fixed, canards = split
    viewport = CfdViewport.__new__(CfdViewport)
    viewport._length = float(mesh.bounds[5] - mesh.bounds[4])
    viewport._canard_meshes = [
        piece.copy(deep=True).translate(-np.asarray(mesh.center), inplace=False)
        for piece in canards
    ]
    fin = viewport._canard_meshes[0]
    pivot = CfdViewport._canard_hinge_pivot(0, fin.bounds)
    rest = viewport._canard_transform(0, 0.0)
    deflected = viewport._canard_transform(0, 15.0)
    pivot_h = np.append(pivot, 1.0)
    assert rest @ pivot_h == pytest.approx(deflected @ pivot_h)
    tip = fin.points[np.argmin(fin.points[:, 2])]
    assert np.linalg.norm((rest @ np.append(tip, 1.0)) - (deflected @ np.append(tip, 1.0))) > 0.01


def test_panned_terrain_keeps_its_geographic_offset_from_the_launch_site() -> None:
    viewport = RocketViewport.__new__(RocketViewport)
    viewport._samples = [SimpleNamespace(latitude_deg=25.0, longitude_deg=-100.0)]
    viewport._terrain = SimpleNamespace(
        center_east_m=12.0, center_north_m=-8.0,
        reference_latitude_deg=25.01, reference_longitude_deg=-99.99,
    )
    center = viewport._terrain_center_in_launch_enu()
    assert center[0] > 900.0
    assert center[1] > 1_000.0


def test_visual_canard_angle_is_readable_but_limited() -> None:
    assert CfdViewport._visual_canard_angle(15.0) == pytest.approx(15.0)
    assert CfdViewport._visual_canard_angle(-15.0) == pytest.approx(-15.0)
    assert CfdViewport._visual_canard_angle(120.0) == pytest.approx(90.0)


def test_canard_angle_control_accepts_negative_decimals_without_live_preview() -> None:
    _application = QApplication.instance() or QApplication([])
    control = CfdTab._spin(0.0, -15.0, 15.0, decimals=2, step=0.1)
    control.lineEdit().setText("-12.35")
    control.interpretText()
    assert control.value() == pytest.approx(-12.35)
    assert control.singleStep() == pytest.approx(0.1)
    assert not control.keyboardTracking()


def test_preview_changes_are_marked_dirty_until_the_apply_button_is_used() -> None:
    class Button:
        text = ""

        def setText(self, value: str) -> None:
            self.text = value

    class Readout:
        text = ""

        def setText(self, value: str) -> None:
            self.text = value

    tab = CfdTab.__new__(CfdTab)
    tab._preview_dirty = False
    tab.apply_preview_button = Button()
    tab.readout = Readout()
    tab._mark_preview_dirty(-12.35)
    assert tab._preview_dirty
    assert "Aplicar" in tab.apply_preview_button.text
    assert "sin aplicar" in tab.readout.text


def test_openfoam_and_preliminary_results_are_explicitly_classified(tmp_path: Path) -> None:
    detailed = CfdResult(tmp_path, (0, 0, 0), (0, 0, 0), 0, tmp_path / "run.log")
    preliminary = CfdResult(tmp_path, (0, 0, 0), (0, 0, 0), 0, tmp_path / "run.log", backend="modelo local preliminar")
    assert detailed.is_openfoam
    assert not preliminary.is_openfoam


def test_complete_cfd_download_bundle_includes_case_fields_and_analysis_guide(tmp_path: Path) -> None:
    for relative, content in (
        ("0/U", "field"), ("constant/laboratory.json", "{}"), ("system/controlDict", "case"),
        ("postProcessing/forces/0/force.dat", "force"), ("VTK/case_50/internal.vtu", "vtk"), ("run.log", "log"),
    ):
        path = tmp_path / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding="utf-8")
    result = CfdResult(tmp_path, (1.0, 2.0, 3.0), (0.1, 0.2, 0.3), 0.0, tmp_path / "run.log", tmp_path / "VTK/case_50/internal.vtu")
    bundle = export_result_bundle(result, tmp_path.parent / "resultado")
    assert bundle.suffix == ".zip"
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert {"0/U", "VTK/case_50/internal.vtu", "result_summary.json", "GUIA_DE_ANALISIS.md"} <= names


def test_documented_flow_axis_convention_matches_alpha_and_beta() -> None:
    assert flow_direction(8.0, 0.0)[2] > 0.0  # positive alpha -> +Z
    assert flow_direction(0.0, 8.0)[1] > 0.0  # positive beta -> +Y/right


def test_orange_obj_groups_are_the_only_articulated_canards() -> None:
    assert CfdViewport._CANARD_GROUPS == (10, 13, 12, 11)
    assert set(CfdViewport._CANARD_GROUPS).isdisjoint(CfdViewport._FIXED_GROUPS)


def test_pid_replay_sends_its_exact_four_angles_to_the_orange_canard_viewport() -> None:
    class Scalar:
        def __init__(self, value: float) -> None:
            self._value = value

        def value(self) -> float:
            return self._value

    class Timeline:
        @staticmethod
        def maximum() -> int:
            return 600

    class Viewport:
        received: tuple[float, ...] | None = None

        def set_canard_deflections(self, canards: tuple[float, ...]) -> None:
            self.received = canards

    tab = CfdTab.__new__(CfdTab)
    tab.timeline = Timeline()
    tab._flight_schedule = (
        (0.0, 0.0, 0.0, 0.0, 0.0),
        (1.0, 12.0, -7.0, -12.0, 7.0),
    )
    tab._flight_details = ()
    displayed: list[tuple[float, ...]] = []
    tab._set_pid_canard_readouts = displayed.append
    tab.viewport = Viewport()
    tab.speed = Scalar(20.0)
    tab.alpha = Scalar(2.0)
    tab.beta = Scalar(-1.0)
    tab.rain = Scalar(3.0)

    tab._animate_sample(600)

    assert displayed == [(12.0, -7.0, -12.0, 7.0)]
    assert tab.viewport.received == (12.0, -7.0, -12.0, 7.0)


def test_cfd_replay_accepts_legacy_dictionary_frames_without_crashing() -> None:
    class Viewport:
        @staticmethod
        def set_cfd_frame(index: int) -> tuple[float, float, float, float]:
            assert index == 0
            return (1.0, -2.0, 3.0, -4.0)

    class Label:
        text = ""

        def setText(self, value: str) -> None:
            self.text = value

    tab = CfdTab.__new__(CfdTab)
    tab._cfd_frame_count = 1
    tab.viewport = Viewport()
    tab._set_pid_canard_readouts = lambda values: None
    tab._last_cfd_result = SimpleNamespace(frames=({
        "time_s": 50.0, "force_n": (1.0, 2.0, 3.0), "moment_nm": (0.1, 0.2, 0.3),
    },))
    tab.history_detail = Label()

    CfdTab._animate_sample(tab, 0)

    assert "t = 50" in tab.history_detail.text
    assert "F = (1, 2, 3) N" in tab.history_detail.text


def test_snapshot_picker_loads_the_finished_phase_requested_by_the_user() -> None:
    class Selector:
        @staticmethod
        def currentIndex() -> int:
            return 1

        @staticmethod
        def currentText() -> str:
            return "2/5 · intermedio entre despegue y paracaídas · t=4.00 s"

    first, second = object(), object()
    tab = CfdTab.__new__(CfdTab)
    tab.snapshot_result_selector = Selector()
    tab._completed_snapshot_results = (first, second)
    captured: list[tuple[object, str]] = []
    tab.set_run_finished = lambda result, message: captured.append((result, message))

    CfdTab._show_selected_snapshot(tab)

    assert captured == [(second, "Mostrando snapshot CFD seleccionado: 2/5 · intermedio entre despegue y paracaídas · t=4.00 s")]


def test_packaged_cfd_tab_can_load_a_development_openfoam_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A locally packaged EXE can inspect the adjacent source-run VTK too."""
    project = tmp_path / "project"
    (project / "python" / "app").mkdir(parents=True)
    case_dir = project / "out" / "cfd" / "finished"; case_dir.mkdir(parents=True)
    vtk = case_dir / "VTK" / "case_500" / "internal.vtu"; vtk.parent.mkdir(parents=True); vtk.write_text("vtk", encoding="utf-8")
    frozen_root = project / "output" / "Sultana-del-Norte" / "_internal"; frozen_root.mkdir(parents=True)

    class Label:
        text = ""

        def setText(self, value: str) -> None:
            self.text = value

    class Tab:
        status = Label()
        finished: tuple[object, str] | None = None

        def set_run_finished(self, result: object, message: str) -> None:
            self.finished = (result, message)

    result = type("Result", (), {"is_openfoam": True, "vtk_path": vtk, "converged": True})()
    tab = Tab()
    monkeypatch.setattr(cfd_tab_module, "application_root", lambda: frozen_root)
    monkeypatch.setattr(cfd_tab_module, "parse_result", lambda path: result if path == case_dir else None)

    CfdTab._load_latest_result(tab)  # type: ignore[arg-type]

    assert tab.finished == (result, "Resultado CFD/OpenFOAM cargado: finished")


def test_cfd_result_parser_reads_force_moment_and_pressure(tmp_path: Path) -> None:
    (tmp_path / "forces.csv").write_text("1 2 3 4 5 6\n", encoding="utf-8")
    (tmp_path / "pressure.csv").write_text("pressure_pa,123.5\n", encoding="utf-8")
    result = parse_result(tmp_path)
    assert result.force_n == (1.0, 2.0, 3.0)
    assert result.moment_nm == (4.0, 5.0, 6.0)
    assert result.pressure_pa == 123.5


def test_recovery_load_is_audited_separately_from_openfoam_force() -> None:
    force = recovery_force_from_cds((-3.0, 4.0, 0.0), 1.2, 0.30)
    # q = 15 Pa and q*CdS = 4.5 N, aligned with the actual air velocity.
    assert force == pytest.approx((-2.7, 3.6, 0.0))


def test_mesh_warning_cannot_be_published_as_converged(tmp_path: Path) -> None:
    report = tmp_path / "mesh-preflight.log"
    report.write_text(
        "Checking faces in error :\nfaces with skewness : 0\nFailed 1 mesh checks.\n",
        encoding="utf-8",
    )
    assert not mesh_preflight_passes(report)


def test_convergence_requires_stable_forces_and_all_final_residuals(tmp_path: Path) -> None:
    stable = {
        float(index): ((1.0 + 0.001 * index, 0.2, -0.1), None, None)
        for index in range(5)
    }
    drifting = {
        float(index): ((1.0 + 0.1 * index, 0.2, -0.1), None, None)
        for index in range(5)
    }
    assert _force_stability_ratio(stable) < 0.02
    assert _force_stability_ratio(drifting) > 0.02
    log = tmp_path / "run.log"
    log.write_text("\n".join(
        f"smoothSolver: Solving for {field}, Initial residual = {value}, Final residual = 1e-7, No Iterations 2"
        for field, value in (("Ux", 2e-4), ("Uy", 3e-4), ("Uz", 4e-4), ("p", 2e-3), ("k", 1e-3), ("omega", 2e-3))
    ), encoding="utf-8")
    assert _residual_ratio(log) == pytest.approx(0.4)


def test_cfd_result_parser_reads_openfoam_2512_force_and_moment_files(tmp_path: Path) -> None:
    output = tmp_path / "postProcessing" / "forces" / "0"; output.mkdir(parents=True)
    (output / "force.dat").write_text("# Time total_x total_y total_z pressure_x pressure_y pressure_z viscous_x viscous_y viscous_z\n8 -27 0.02 -0.1 -28 0.01 -0.2 1 0 0\n", encoding="utf-8")
    (output / "moment.dat").write_text("# Time total_x total_y total_z pressure_x pressure_y pressure_z viscous_x viscous_y viscous_z\n8 1 2 3 4 5 6 7 8 9\n", encoding="utf-8")
    result = parse_result(tmp_path)
    assert result.force_n == pytest.approx((-27.0, 0.02, -0.1))
    assert result.moment_nm == pytest.approx((1.0, 2.0, 3.0))
    assert result.pressure_force_n == pytest.approx((-28.0, 0.01, -0.2))
    assert result.viscous_force_n == pytest.approx((1.0, 0.0, 0.0))


def test_transient_vtk_states_are_manifested_with_pid_and_openfoam_loads(tmp_path: Path) -> None:
    import numpy as np
    import pyvista as pv

    for vtk_index, time_s in enumerate((0.0, 0.5), start=10):
        directory = tmp_path / "VTK" / f"case_{vtk_index}"
        directory.mkdir(parents=True)
        mesh = pv.Wavelet().cast_to_unstructured_grid()
        mesh["U"] = np.tile((1.0 + time_s, 0.0, 0.0), (mesh.n_points, 1))
        mesh.field_data["TimeValue"] = np.asarray([time_s])
        mesh.save(directory / "internal.vtu")
    output = tmp_path / "postProcessing" / "forces" / "0"; output.mkdir(parents=True)
    (output / "force.dat").write_text(
        "# Time total_x total_y total_z pressure_x pressure_y pressure_z viscous_x viscous_y viscous_z\n0 1 0 0 2 0 0 0 0 0\n0.5 3 0 0 4 0 0 0 0 0\n", encoding="utf-8"
    )
    laboratory = tmp_path / "constant"; laboratory.mkdir()
    (laboratory / "laboratory.json").write_text(
        '{"canard_schedule": [[0, 0, 0, 0, 0], [0.5, 10, -5, -10, 5]]}', encoding="utf-8"
    )
    result = parse_result(tmp_path)
    assert [frame.time_s for frame in result.frames] == [0.0, 0.5]
    assert result.frames[-1].canard_deg == pytest.approx((10.0, -5.0, -10.0, 5.0))
    assert result.frames[-1].force_n == pytest.approx((3.0, 0.0, 0.0))
    assert (tmp_path / "cfd_frames.json").is_file()


def test_local_preliminary_result_keeps_the_laboratory_usable_when_docker_is_unavailable(tmp_path: Path) -> None:
    progress: list[str] = []
    result = _preliminary_local_result(tmp_path, _request(), "registry EOF", progress.append)
    assert result.backend == "modelo local preliminar"
    assert result.execution_backend == "CPU de respaldo"
    assert result.force_n[0] < 0.0
    assert result.pressure_pa > 0.0
    assert "CFD validado" in (tmp_path / "run.log").read_text(encoding="utf-8")
    assert progress


def test_cfd_geometry_uses_the_primary_stl_without_the_legacy_obj(tmp_path: Path) -> None:
    (tmp_path / "data" / "models").mkdir(parents=True)
    primary = ROOT / "data/models/ensamble_todo_v2_cfd.stl"
    shutil.copy2(primary, tmp_path / "data/models/ensamble_todo_v2_cfd.stl")
    straight = prepare_case(tmp_path, CfdCaseRequest("straight", "steady", 30, 0, 0, 0, (0, 0, 0, 0), False))
    first = (straight / "constant/triSurface/rocket.stl").read_bytes()
    assert first == primary.read_bytes()


def test_snapshot_selection_uses_three_distinct_ranked_states() -> None:
    selected = select_representative_snapshots((
        {"time_s": 0.0, "dynamic_pressure_pa": 5.0, "canards": (0, 0, 0, 0), "lateral_velocity_mps": 0.0},
        {"time_s": 1.0, "dynamic_pressure_pa": 20.0, "canards": (1, 0, 0, 0), "lateral_velocity_mps": 1.0},
        {"time_s": 2.0, "dynamic_pressure_pa": 18.0, "canards": (12, 0, 0, 0), "lateral_velocity_mps": 4.0},
        {"time_s": 3.0, "dynamic_pressure_pa": 15.0, "canards": (2, 0, 0, 0), "lateral_velocity_mps": 8.0},
    ))
    assert [entry.index for entry in selected] == [1, 2, 3]
    assert len({entry.source_time_s for entry in selected}) == 3


def test_naca_snapshot_is_normalized_and_audited(tmp_path: Path) -> None:
    model_dir = tmp_path / "data" / "models"; model_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "data/models/ensamble_naca_661_212.stl", model_dir / "ensamble_naca_661_212.stl")
    case = prepare_case(tmp_path, CfdCaseRequest("naca-snapshot", "snapshot", 30, 0, 0, 0, (5, -5, 5, -5), False,
                                                  snapshot_source_time_s=1.25, snapshot_reason="máxima presión dinámica"))
    audit = __import__("json").loads((case / "constant/laboratory.json").read_text(encoding="utf-8"))
    assert audit["geometry"]["body_length_m"] == pytest.approx(0.902)
    assert audit["geometry"]["components_closed"] == 8 and audit["geometry"]["canards"] == 4
    assert audit["snapshot_reason"] == "máxima presión dinámica"
    assert (case / "constant/triSurface/rocket.stl").is_file()


def test_saved_laboratory_scenario_is_independent_and_loadable(tmp_path: Path) -> None:
    root = tmp_path / "project"; (root / "configs").mkdir(parents=True); (root / "data").mkdir()
    base = load_scenario(ROOT / "configs/vehicle/sultana_4canard.yaml", ROOT / "configs/environments/guadalupe_example.yaml")
    # Retain the registry only if the temporary root also holds it; the scenario
    # itself still preserves all calibration booleans unchanged.
    vehicle = dict(base.vehicle); vehicle.pop("parameter_registry_yaml", None)
    saved = save_laboratory_scenario(root, "Ensayo lluvia", vehicle, base.environment,
                                     [[0.0, 0.0], [1.0, 10.0]],
                                     [[0.0, 0.2, 0.7, 0.02, 0.2, 0.2], [1.0, 0.0, 0.6, 0.02, 0.2, 0.2]],
                                     [[0.0, 0.5, 2.0], [1.0, 0.6, 2.1]])
    assert saved.vehicle_path.parent.name == "ensayo-lluvia"
    assert saved.vehicle["propulsion"]["thrust_curve_csv"].startswith("configs/scenarios/ensayo-lluvia")
    assert (root / "configs" / "scenarios" / "ensayo-lluvia" / "data" / "aero_table.csv").is_file()
