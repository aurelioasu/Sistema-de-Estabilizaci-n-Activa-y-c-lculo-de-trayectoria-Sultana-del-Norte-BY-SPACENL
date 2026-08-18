#pragma once

#include <Eigen/Dense>
#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace sultana {

using Vec3 = Eigen::Vector3d;
using Mat3 = Eigen::Matrix3d;

struct Quaternion {
  double w{1.0}, x{0.0}, y{0.0}, z{0.0};
  void normalize();
  [[nodiscard]] Quaternion derivative(const Vec3& omega_body) const;
  [[nodiscard]] Mat3 to_rotation_matrix() const;
};

struct LaunchSite {
  double latitude_deg{25.68};
  double longitude_deg{-100.31};
  double altitude_msl_m{500.0};
};

struct ThrustPoint { double time_s; double thrust_n; };
struct MassPoint {
  double time_s{};
  double propellant_mass_kg{};
  double cg_m{};
  Vec3 inertia_kg_m2{Vec3::Zero()};
};
struct AeroPoint { double mach{}; double cd{}; double cn_alpha_per_rad{}; };
struct AtmospherePoint {
  double altitude_agl_m{};
  double temperature_k{};
  double pressure_pa{};
  double relative_humidity{};
  Vec3 wind_enu_mps{Vec3::Zero()};
};

struct CalibrationStatus {
  bool thrust_curve{false};
  bool mass_properties{false};
  bool aerodynamics{false};
  [[nodiscard]] bool complete() const { return thrust_curve && mass_properties && aerodynamics; }
};

struct VehicleModel {
  double diameter_m{0.0508};
  double reference_area_m2{0.0020268};
  double dry_mass_kg{1.2};
  double propellant_mass_kg{0.25};
  double burn_time_s{2.0};
  double body_length_m{0.902};
  double cg_dry_m{0.63};
  double cg_wet_m{0.72};
  Vec3 inertia_dry_kg_m2{0.025, 0.22, 0.22};
  Vec3 inertia_wet_kg_m2{0.028, 0.25, 0.25};
  double cd_base{0.55};
  double cp_m{0.78};
  double canard_area_m2{0.0012};
  double canard_arm_m{0.42};
  double canard_cl_alpha_per_rad{2.5};
  double max_canard_deflection_rad{0.2617993878};
  double max_canard_rate_rad_s{11.63552835};
  double parachute_area_m2{0.7};
  double parachute_cd{1.5};
  double parachute_deploy_delay_s{0.25};
  double parachute_inflation_time_s{0.8};
  double body_cn_alpha_per_rad{2.0};
  double cd_alpha2{0.0};
  double cd_reynolds_per_log{0.0};
  double reference_reynolds{100000.0};
  double angular_damping_nm_per_rad_s{0.04};
  double canard_command_delay_s{0.02};
  std::array<double, 4> canard_mount_offset_rad{};
  std::vector<ThrustPoint> thrust_curve;
  std::vector<MassPoint> mass_curve;
  std::vector<AeroPoint> aero_curve;
  CalibrationStatus calibration;
};

struct EnvironmentModel {
  double surface_temperature_k{288.15};
  double surface_pressure_pa{101325.0};
  double humidity_ratio{0.0};
  Vec3 mean_wind_enu_mps{0.0, 0.0, 0.0};
  double turbulence_intensity_mps{0.0};
  std::uint32_t turbulence_seed{42};
  double rain_rate_mm_h{0.0};
  double rain_cd_delta{0.0};
  double friction_heat_coefficient{0.0};
  double vibration_gain{0.0};
  std::vector<AtmospherePoint> profile;
};

struct ControllerConfig {
  double kp{0.25};
  double ki{0.03};
  double kd{0.04};
  double target_pitch_rad{-1.57079632679}; // vertical launch in the body-forward/ENU convention
  double target_yaw_rad{0.0};
};

struct RailConfig {
  double length_m{3.0};
  double elevation_rad{1.57079632679};
  double azimuth_rad{0.0};
};

struct SensorConfig {
  double imu_accel_noise_std_mps2{0.0};
  double attitude_noise_std_rad{0.0};
  double attitude_update_period_s{0.01};
  double barometer_noise_std_m{0.0};
  double barometer_bias_m{0.0};
  double gps_noise_std_m{0.0};
  double estimator_time_constant_s{0.5};
  std::uint32_t seed{314159};
};

struct SimulationConfig {
  LaunchSite launch_site;
  VehicleModel vehicle;
  EnvironmentModel environment;
  ControllerConfig controller;
  RailConfig rail;
  SensorConfig sensors;
  double duration_s{90.0};
  double integration_step_s{0.001};
  double telemetry_period_s{1.0 / 60.0};
  bool request_flight_prediction{false};
};

struct FlightState {
  double time_s{0.0};
  Vec3 position_enu_m{Vec3::Zero()};
  Vec3 velocity_enu_mps{Vec3::Zero()};
  Quaternion q_body_to_enu{1.0, 0.0, 0.0, 0.0};
  Vec3 omega_body_rad_s{Vec3::Zero()};
  bool parachute_deployed{false};
  bool on_rail{true};
  double parachute_command_time_s{-1.0};
};

struct TelemetrySample {
  double time_s{};
  Vec3 position_enu_m{Vec3::Zero()};
  double latitude_deg{};
  double longitude_deg{};
  double altitude_msl_m{};
  Vec3 velocity_enu_mps{Vec3::Zero()};
  Quaternion quaternion{};
  Vec3 euler_rad{Vec3::Zero()}; // roll, pitch, yaw
  Vec3 omega_body_rad_s{Vec3::Zero()};
  std::array<double, 4> canard_deflection_rad{}; // top, right, bottom, left
  Vec3 pid_output{Vec3::Zero()};
  double altitude_agl_m{};
  double mach{};
  double dynamic_pressure_pa{};
  // Ambient values selected from the atmospheric profile at this integration
  // sample.  These are distinct from surface_temperature_k, which includes a
  // heat proxy for telemetry/thermal display.
  double air_temperature_k{};
  double air_pressure_pa{};
  double air_relative_humidity{};
  double air_density_kg_m3{};
  double surface_temperature_k{};
  double estimated_altitude_agl_m{};
  bool parachute_deployed{false};
  Vec3 wind_enu_mps{Vec3::Zero()};
  Vec3 relative_velocity_enu_mps{Vec3::Zero()};
  Vec3 controller_error_rad{Vec3::Zero()};
  double airspeed_mps{};
  double thrust_n{};
  double mass_kg{};
  double drag_force_n{};
  double rain_impact_force_n{};
  double canard_lift_n{};
  double static_margin_calibers{};
  double friction_heat_proxy{};
  double cg_m{};
  Vec3 inertia_kg_m2{Vec3::Zero()};
  bool on_rail{};
  double parachute_cds_m2{};
  double barometer_altitude_agl_m{};
  double gps_altitude_agl_m{};
  Vec3 imu_acceleration_body_mps2{Vec3::Zero()};
};

struct SimulationResult {
  std::vector<TelemetrySample> samples;
  std::vector<std::string> events;
  bool flight_prediction_allowed{false};
  std::string classification{"preliminary_analysis"};
};

} // namespace sultana
