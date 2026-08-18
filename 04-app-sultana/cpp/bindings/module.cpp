#include "sultana/simulation.hpp"

#include <pybind11/eigen.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using namespace sultana;

PYBIND11_MODULE(sultana_core, m) {
  m.doc() = "Deterministic 6-DoF simulation core for the Sultana platform";

  py::class_<Quaternion>(m, "Quaternion")
      .def(py::init<>())
      .def_readwrite("w", &Quaternion::w).def_readwrite("x", &Quaternion::x)
      .def_readwrite("y", &Quaternion::y).def_readwrite("z", &Quaternion::z);
  py::class_<LaunchSite>(m, "LaunchSite")
      .def(py::init<>()).def_readwrite("latitude_deg", &LaunchSite::latitude_deg)
      .def_readwrite("longitude_deg", &LaunchSite::longitude_deg).def_readwrite("altitude_msl_m", &LaunchSite::altitude_msl_m);
  py::class_<ThrustPoint>(m, "ThrustPoint")
      .def(py::init<>()).def_readwrite("time_s", &ThrustPoint::time_s).def_readwrite("thrust_n", &ThrustPoint::thrust_n);
  py::class_<MassPoint>(m, "MassPoint")
      .def(py::init<>()).def_readwrite("time_s", &MassPoint::time_s)
      .def_readwrite("propellant_mass_kg", &MassPoint::propellant_mass_kg).def_readwrite("cg_m", &MassPoint::cg_m)
      .def_readwrite("inertia_kg_m2", &MassPoint::inertia_kg_m2);
  py::class_<AeroPoint>(m, "AeroPoint")
      .def(py::init<>()).def_readwrite("mach", &AeroPoint::mach).def_readwrite("cd", &AeroPoint::cd)
      .def_readwrite("cn_alpha_per_rad", &AeroPoint::cn_alpha_per_rad);
  py::class_<AtmospherePoint>(m, "AtmospherePoint")
      .def(py::init<>()).def_readwrite("altitude_agl_m", &AtmospherePoint::altitude_agl_m)
      .def_readwrite("temperature_k", &AtmospherePoint::temperature_k).def_readwrite("pressure_pa", &AtmospherePoint::pressure_pa)
      .def_readwrite("relative_humidity", &AtmospherePoint::relative_humidity).def_readwrite("wind_enu_mps", &AtmospherePoint::wind_enu_mps);
  py::class_<CalibrationStatus>(m, "CalibrationStatus")
      .def(py::init<>()).def_readwrite("thrust_curve", &CalibrationStatus::thrust_curve)
      .def_readwrite("mass_properties", &CalibrationStatus::mass_properties)
      .def_readwrite("aerodynamics", &CalibrationStatus::aerodynamics)
      .def_property_readonly("complete", &CalibrationStatus::complete);
  py::class_<VehicleModel>(m, "VehicleModel")
      .def(py::init<>())
      .def_readwrite("diameter_m", &VehicleModel::diameter_m).def_readwrite("reference_area_m2", &VehicleModel::reference_area_m2)
      .def_readwrite("dry_mass_kg", &VehicleModel::dry_mass_kg).def_readwrite("propellant_mass_kg", &VehicleModel::propellant_mass_kg)
      .def_readwrite("burn_time_s", &VehicleModel::burn_time_s).def_readwrite("body_length_m", &VehicleModel::body_length_m)
      .def_readwrite("cg_dry_m", &VehicleModel::cg_dry_m).def_readwrite("cg_wet_m", &VehicleModel::cg_wet_m)
      .def_readwrite("inertia_dry_kg_m2", &VehicleModel::inertia_dry_kg_m2).def_readwrite("inertia_wet_kg_m2", &VehicleModel::inertia_wet_kg_m2)
      .def_readwrite("cd_base", &VehicleModel::cd_base).def_readwrite("cp_m", &VehicleModel::cp_m)
      .def_readwrite("canard_area_m2", &VehicleModel::canard_area_m2).def_readwrite("canard_arm_m", &VehicleModel::canard_arm_m)
      .def_readwrite("canard_cl_alpha_per_rad", &VehicleModel::canard_cl_alpha_per_rad)
      .def_readwrite("max_canard_deflection_rad", &VehicleModel::max_canard_deflection_rad)
      .def_readwrite("max_canard_rate_rad_s", &VehicleModel::max_canard_rate_rad_s)
      .def_readwrite("parachute_area_m2", &VehicleModel::parachute_area_m2).def_readwrite("parachute_cd", &VehicleModel::parachute_cd)
      .def_readwrite("parachute_deploy_delay_s", &VehicleModel::parachute_deploy_delay_s).def_readwrite("parachute_inflation_time_s", &VehicleModel::parachute_inflation_time_s)
      .def_readwrite("body_cn_alpha_per_rad", &VehicleModel::body_cn_alpha_per_rad).def_readwrite("cd_alpha2", &VehicleModel::cd_alpha2)
      .def_readwrite("cd_reynolds_per_log", &VehicleModel::cd_reynolds_per_log).def_readwrite("reference_reynolds", &VehicleModel::reference_reynolds)
      .def_readwrite("angular_damping_nm_per_rad_s", &VehicleModel::angular_damping_nm_per_rad_s)
      .def_readwrite("canard_command_delay_s", &VehicleModel::canard_command_delay_s).def_readwrite("canard_mount_offset_rad", &VehicleModel::canard_mount_offset_rad)
      .def_readwrite("thrust_curve", &VehicleModel::thrust_curve).def_readwrite("mass_curve", &VehicleModel::mass_curve)
      .def_readwrite("aero_curve", &VehicleModel::aero_curve).def_readwrite("calibration", &VehicleModel::calibration);
  py::class_<EnvironmentModel>(m, "EnvironmentModel")
      .def(py::init<>()).def_readwrite("surface_temperature_k", &EnvironmentModel::surface_temperature_k)
      .def_readwrite("surface_pressure_pa", &EnvironmentModel::surface_pressure_pa).def_readwrite("humidity_ratio", &EnvironmentModel::humidity_ratio)
      .def_readwrite("mean_wind_enu_mps", &EnvironmentModel::mean_wind_enu_mps)
      .def_readwrite("turbulence_intensity_mps", &EnvironmentModel::turbulence_intensity_mps)
      .def_readwrite("turbulence_seed", &EnvironmentModel::turbulence_seed).def_readwrite("rain_rate_mm_h", &EnvironmentModel::rain_rate_mm_h)
      .def_readwrite("rain_cd_delta", &EnvironmentModel::rain_cd_delta)
      .def_readwrite("friction_heat_coefficient", &EnvironmentModel::friction_heat_coefficient)
      .def_readwrite("vibration_gain", &EnvironmentModel::vibration_gain).def_readwrite("profile", &EnvironmentModel::profile);
  py::class_<ControllerConfig>(m, "ControllerConfig")
      .def(py::init<>()).def_readwrite("kp", &ControllerConfig::kp).def_readwrite("ki", &ControllerConfig::ki)
      .def_readwrite("kd", &ControllerConfig::kd).def_readwrite("target_pitch_rad", &ControllerConfig::target_pitch_rad)
      .def_readwrite("target_yaw_rad", &ControllerConfig::target_yaw_rad);
  py::class_<RailConfig>(m, "RailConfig")
      .def(py::init<>()).def_readwrite("length_m", &RailConfig::length_m).def_readwrite("elevation_rad", &RailConfig::elevation_rad)
      .def_readwrite("azimuth_rad", &RailConfig::azimuth_rad);
  py::class_<SensorConfig>(m, "SensorConfig")
      .def(py::init<>()).def_readwrite("imu_accel_noise_std_mps2", &SensorConfig::imu_accel_noise_std_mps2)
      .def_readwrite("attitude_noise_std_rad", &SensorConfig::attitude_noise_std_rad)
      .def_readwrite("attitude_update_period_s", &SensorConfig::attitude_update_period_s)
      .def_readwrite("barometer_noise_std_m", &SensorConfig::barometer_noise_std_m).def_readwrite("barometer_bias_m", &SensorConfig::barometer_bias_m)
      .def_readwrite("gps_noise_std_m", &SensorConfig::gps_noise_std_m).def_readwrite("estimator_time_constant_s", &SensorConfig::estimator_time_constant_s)
      .def_readwrite("seed", &SensorConfig::seed);
  py::class_<SimulationConfig>(m, "SimulationConfig")
      .def(py::init<>()).def_readwrite("launch_site", &SimulationConfig::launch_site)
      .def_readwrite("vehicle", &SimulationConfig::vehicle).def_readwrite("environment", &SimulationConfig::environment)
      .def_readwrite("controller", &SimulationConfig::controller).def_readwrite("rail", &SimulationConfig::rail).def_readwrite("sensors", &SimulationConfig::sensors).def_readwrite("duration_s", &SimulationConfig::duration_s)
      .def_readwrite("integration_step_s", &SimulationConfig::integration_step_s)
      .def_readwrite("telemetry_period_s", &SimulationConfig::telemetry_period_s)
      .def_readwrite("request_flight_prediction", &SimulationConfig::request_flight_prediction);
  py::class_<TelemetrySample>(m, "TelemetrySample")
      .def_readonly("time_s", &TelemetrySample::time_s).def_readonly("position_enu_m", &TelemetrySample::position_enu_m)
      .def_readonly("latitude_deg", &TelemetrySample::latitude_deg).def_readonly("longitude_deg", &TelemetrySample::longitude_deg)
      .def_readonly("altitude_msl_m", &TelemetrySample::altitude_msl_m)
      .def_readonly("velocity_enu_mps", &TelemetrySample::velocity_enu_mps).def_readonly("quaternion", &TelemetrySample::quaternion)
      .def_readonly("euler_rad", &TelemetrySample::euler_rad).def_readonly("omega_body_rad_s", &TelemetrySample::omega_body_rad_s)
      .def_readonly("canard_deflection_rad", &TelemetrySample::canard_deflection_rad).def_readonly("pid_output", &TelemetrySample::pid_output)
      .def_readonly("altitude_agl_m", &TelemetrySample::altitude_agl_m).def_readonly("mach", &TelemetrySample::mach)
      .def_readonly("dynamic_pressure_pa", &TelemetrySample::dynamic_pressure_pa)
      .def_readonly("air_temperature_k", &TelemetrySample::air_temperature_k).def_readonly("air_pressure_pa", &TelemetrySample::air_pressure_pa)
      .def_readonly("air_relative_humidity", &TelemetrySample::air_relative_humidity).def_readonly("air_density_kg_m3", &TelemetrySample::air_density_kg_m3)
      .def_readonly("surface_temperature_k", &TelemetrySample::surface_temperature_k)
      .def_readonly("estimated_altitude_agl_m", &TelemetrySample::estimated_altitude_agl_m)
      .def_readonly("parachute_deployed", &TelemetrySample::parachute_deployed)
      .def_readonly("wind_enu_mps", &TelemetrySample::wind_enu_mps).def_readonly("relative_velocity_enu_mps", &TelemetrySample::relative_velocity_enu_mps)
      .def_readonly("controller_error_rad", &TelemetrySample::controller_error_rad).def_readonly("airspeed_mps", &TelemetrySample::airspeed_mps)
      .def_readonly("thrust_n", &TelemetrySample::thrust_n).def_readonly("mass_kg", &TelemetrySample::mass_kg)
      .def_readonly("drag_force_n", &TelemetrySample::drag_force_n).def_readonly("rain_impact_force_n", &TelemetrySample::rain_impact_force_n)
      .def_readonly("canard_lift_n", &TelemetrySample::canard_lift_n).def_readonly("static_margin_calibers", &TelemetrySample::static_margin_calibers)
      .def_readonly("friction_heat_proxy", &TelemetrySample::friction_heat_proxy).def_readonly("cg_m", &TelemetrySample::cg_m)
      .def_readonly("inertia_kg_m2", &TelemetrySample::inertia_kg_m2).def_readonly("on_rail", &TelemetrySample::on_rail)
      .def_readonly("parachute_cds_m2", &TelemetrySample::parachute_cds_m2).def_readonly("barometer_altitude_agl_m", &TelemetrySample::barometer_altitude_agl_m)
      .def_readonly("gps_altitude_agl_m", &TelemetrySample::gps_altitude_agl_m).def_readonly("imu_acceleration_body_mps2", &TelemetrySample::imu_acceleration_body_mps2);
  py::class_<SimulationResult>(m, "SimulationResult")
      .def_readonly("samples", &SimulationResult::samples).def_readonly("events", &SimulationResult::events)
      .def_readonly("flight_prediction_allowed", &SimulationResult::flight_prediction_allowed)
      .def_readonly("classification", &SimulationResult::classification);
  m.def("run_simulation", [](const SimulationConfig& config) {
    py::gil_scoped_release release;
    return SimulationEngine{config}.run();
  }, py::arg("config"));
}
