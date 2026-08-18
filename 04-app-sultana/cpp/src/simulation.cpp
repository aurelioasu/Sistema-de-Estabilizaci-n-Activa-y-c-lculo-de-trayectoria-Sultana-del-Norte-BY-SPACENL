#include "sultana/simulation.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <deque>
#include <numbers>
#include <utility>

namespace sultana {
namespace {
constexpr double kGravity = 9.80665;
constexpr double kGasConstantDryAir = 287.05;
constexpr double kGasConstantWaterVapour = 461.495;
constexpr double kGammaAir = 1.4;

double clamp(double value, double lower, double upper) { return std::max(lower, std::min(value, upper)); }
double lerp(double a, double b, double alpha) { return a + (b - a) * alpha; }
Vec3 lerp(const Vec3& a, const Vec3& b, double alpha) { return a + (b - a) * alpha; }

double moist_air_density(double temperature_k, double pressure_pa, double relative_humidity) {
  const double temperature = std::max(150.0, temperature_k);
  const double pressure = std::max(1000.0, pressure_pa);
  const double rh = clamp(relative_humidity, 0.0, 1.0);
  const double celsius = temperature - 273.15;
  // Buck saturation-vapour equation. The configuration field is relative
  // humidity (0..1), not a mass mixing ratio.
  const double saturation = 611.21 * std::exp((18.678 - celsius / 234.5) * (celsius / (257.14 + celsius)));
  const double vapour = std::min(rh * saturation, pressure * 0.99);
  return std::max(0.2, (pressure - vapour) / (kGasConstantDryAir * temperature) + vapour / (kGasConstantWaterVapour * temperature));
}

template <typename Point, typename Key>
std::size_t upper_index(const std::vector<Point>& points, double value, Key key) {
  const auto iterator = std::lower_bound(points.begin(), points.end(), value, [&](const Point& point, double needle) { return key(point) < needle; });
  return static_cast<std::size_t>(std::distance(points.begin(), iterator));
}

double thrust_at(const VehicleModel& vehicle, double time_s) {
  const auto& curve = vehicle.thrust_curve;
  if (curve.empty() || time_s < curve.front().time_s || time_s > curve.back().time_s) return 0.0;
  const std::size_t index = upper_index(curve, time_s, [](const ThrustPoint& point) { return point.time_s; });
  if (index == 0) return curve.front().thrust_n;
  if (index >= curve.size()) return curve.back().thrust_n;
  const auto& left = curve[index - 1]; const auto& right = curve[index];
  return lerp(left.thrust_n, right.thrust_n, (time_s - left.time_s) / std::max(1e-9, right.time_s - left.time_s));
}

struct MassProperties { double mass_kg; double cg_m; Mat3 inertia; };

MassProperties mass_properties_at(const VehicleModel& vehicle, double time_s) {
  if (vehicle.mass_curve.empty()) {
    const double fraction = vehicle.burn_time_s <= 0.0 ? 0.0 : clamp(1.0 - time_s / vehicle.burn_time_s, 0.0, 1.0);
    const Vec3 inertia = vehicle.inertia_dry_kg_m2 + fraction * (vehicle.inertia_wet_kg_m2 - vehicle.inertia_dry_kg_m2);
    return {vehicle.dry_mass_kg + fraction * vehicle.propellant_mass_kg, lerp(vehicle.cg_dry_m, vehicle.cg_wet_m, fraction), inertia.asDiagonal()};
  }
  const auto& curve = vehicle.mass_curve;
  if (time_s <= curve.front().time_s) return {vehicle.dry_mass_kg + curve.front().propellant_mass_kg, curve.front().cg_m, curve.front().inertia_kg_m2.asDiagonal()};
  if (time_s >= curve.back().time_s) return {vehicle.dry_mass_kg + curve.back().propellant_mass_kg, curve.back().cg_m, curve.back().inertia_kg_m2.asDiagonal()};
  const std::size_t index = upper_index(curve, time_s, [](const MassPoint& point) { return point.time_s; });
  const auto& left = curve[index - 1]; const auto& right = curve[index];
  const double alpha = (time_s - left.time_s) / std::max(1e-9, right.time_s - left.time_s);
  return {vehicle.dry_mass_kg + lerp(left.propellant_mass_kg, right.propellant_mass_kg, alpha), lerp(left.cg_m, right.cg_m, alpha), lerp(left.inertia_kg_m2, right.inertia_kg_m2, alpha).asDiagonal()};
}

struct AirProperties {
  double density;
  double temperature_k;
  double pressure_pa;
  double relative_humidity;
  double sound_speed_mps;
  Vec3 wind_enu_mps;
};

Vec3 deterministic_turbulence(const EnvironmentModel& env, double time_s, double altitude_m) {
  const double seed = static_cast<double>(env.turbulence_seed % 997U);
  const double scale = env.turbulence_intensity_mps * std::exp(-std::max(altitude_m, 0.0) / 15000.0);
  return scale * Vec3{std::sin(0.71 * time_s + seed), std::sin(1.13 * time_s + seed * 0.37), std::sin(0.43 * time_s + seed * 0.71)};
}

AirProperties atmosphere_at(const EnvironmentModel& env, double time_s, double altitude_m) {
  const double altitude = std::max(0.0, altitude_m);
  double temperature = 0.0, pressure = 0.0, humidity = env.humidity_ratio;
  Vec3 wind = env.mean_wind_enu_mps;
  if (!env.profile.empty()) {
    const auto& profile = env.profile;
    const std::size_t index = upper_index(profile, altitude, [](const AtmospherePoint& point) { return point.altitude_agl_m; });
    const AtmospherePoint& left = profile[index == 0 ? 0 : index - 1];
    const AtmospherePoint& right = profile[std::min(index, profile.size() - 1)];
    const double alpha = left.altitude_agl_m == right.altitude_agl_m ? 0.0 : clamp((altitude - left.altitude_agl_m) / (right.altitude_agl_m - left.altitude_agl_m), 0.0, 1.0);
    temperature = lerp(left.temperature_k, right.temperature_k, alpha);
    pressure = lerp(left.pressure_pa, right.pressure_pa, alpha);
    humidity = lerp(left.relative_humidity, right.relative_humidity, alpha);
    wind = lerp(left.wind_enu_mps, right.wind_enu_mps, alpha);
  } else {
    constexpr double lapse_rate = 0.0065;
    temperature = std::max(216.65, env.surface_temperature_k - lapse_rate * altitude);
    pressure = env.surface_pressure_pa * std::pow(temperature / env.surface_temperature_k, kGravity / (kGasConstantDryAir * lapse_rate));
  }
  const double density = moist_air_density(temperature, pressure, humidity);
  return {density, temperature, pressure, humidity, std::sqrt(kGammaAir * kGasConstantDryAir * temperature), wind + deterministic_turbulence(env, time_s, altitude)};
}

struct AeroProperties { double cd; double cn_alpha; };

AeroProperties aero_at(const VehicleModel& vehicle, double mach, double reynolds, double alpha) {
  double cd = vehicle.cd_base;
  double cn_alpha = vehicle.body_cn_alpha_per_rad;
  if (!vehicle.aero_curve.empty()) {
    const auto& curve = vehicle.aero_curve;
    const std::size_t index = upper_index(curve, mach, [](const AeroPoint& point) { return point.mach; });
    const AeroPoint& left = curve[index == 0 ? 0 : index - 1]; const AeroPoint& right = curve[std::min(index, curve.size() - 1)];
    const double factor = left.mach == right.mach ? 0.0 : clamp((mach - left.mach) / (right.mach - left.mach), 0.0, 1.0);
    cd = lerp(left.cd, right.cd, factor); cn_alpha = lerp(left.cn_alpha_per_rad, right.cn_alpha_per_rad, factor);
  }
  cd += vehicle.cd_alpha2 * alpha * alpha;
  cd += vehicle.cd_reynolds_per_log * std::log(std::max(1.0, reynolds) / std::max(1.0, vehicle.reference_reynolds));
  return {std::max(0.01, cd), std::max(0.0, cn_alpha)};
}

Vec3 euler_from(const Quaternion& q) {
  const double sinr_cosp = 2.0 * (q.w * q.x + q.y * q.z); const double cosr_cosp = 1.0 - 2.0 * (q.x * q.x + q.y * q.y);
  const double sinp = 2.0 * (q.w * q.y - q.z * q.x); const double siny_cosp = 2.0 * (q.w * q.z + q.x * q.y); const double cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z);
  return {std::atan2(sinr_cosp, cosr_cosp), std::asin(clamp(sinp, -1.0, 1.0)), std::atan2(siny_cosp, cosy_cosp)};
}

Quaternion orientation_from_forward(const Vec3& forward) {
  const Vec3 x_axis{1.0, 0.0, 0.0}; const Vec3 unit = forward.normalized(); const double dot = clamp(x_axis.dot(unit), -1.0, 1.0);
  if (dot < -0.999999) return {0.0, 0.0, 1.0, 0.0};
  const Vec3 axis = x_axis.cross(unit); const double w = std::sqrt((1.0 + dot) * 0.5);
  return {w, axis.x() / (2.0 * w), axis.y() / (2.0 * w), axis.z() / (2.0 * w)};
}

std::array<double, 3> enu_to_wgs84(const LaunchSite& origin, const Vec3& enu_m) {
  constexpr double earth_radius_m = 6378137.0; const double latitude_rad = origin.latitude_deg * std::numbers::pi / 180.0;
  return {origin.latitude_deg + enu_m.y() / earth_radius_m * 180.0 / std::numbers::pi,
          origin.longitude_deg + enu_m.x() / (earth_radius_m * std::max(0.01, std::cos(latitude_rad))) * 180.0 / std::numbers::pi,
          origin.altitude_msl_m + enu_m.z()};
}

double parachute_cds_at(const FlightState& state, const VehicleModel& vehicle) {
  if (!state.parachute_deployed) return 0.0;
  const double inflation = vehicle.parachute_inflation_time_s <= 0.0 ? 1.0 : clamp((state.time_s - state.parachute_command_time_s) / vehicle.parachute_inflation_time_s, 0.0, 1.0);
  return vehicle.parachute_area_m2 * vehicle.parachute_cd * inflation;
}

struct Derivative { Vec3 position_dot{Vec3::Zero()}; Vec3 velocity_dot{Vec3::Zero()}; Quaternion quaternion_dot{}; Vec3 omega_dot{Vec3::Zero()}; };
FlightState add_scaled(const FlightState& state, const Derivative& derivative, double scale) {
  FlightState next = state; next.position_enu_m += derivative.position_dot * scale; next.velocity_enu_mps += derivative.velocity_dot * scale;
  next.q_body_to_enu.w += derivative.quaternion_dot.w * scale; next.q_body_to_enu.x += derivative.quaternion_dot.x * scale; next.q_body_to_enu.y += derivative.quaternion_dot.y * scale; next.q_body_to_enu.z += derivative.quaternion_dot.z * scale;
  next.q_body_to_enu.normalize(); next.omega_body_rad_s += derivative.omega_dot * scale; return next;
}

Derivative dynamics(const FlightState& state, const SimulationConfig& cfg, const std::array<double, 4>& canards) {
  const auto& vehicle = cfg.vehicle; const MassProperties mass = mass_properties_at(vehicle, state.time_s); const AirProperties air = atmosphere_at(cfg.environment, state.time_s, state.position_enu_m.z());
  const Vec3 relative_velocity = state.velocity_enu_mps - air.wind_enu_mps; const double speed = relative_velocity.norm(); const double dynamic_pressure = 0.5 * air.density * speed * speed;
  const Mat3 r_body_to_enu = state.q_body_to_enu.to_rotation_matrix(); const Vec3 relative_body = r_body_to_enu.transpose() * relative_velocity;
  const double alpha_pitch = std::atan2(-relative_body.z(), std::max(0.1, relative_body.x())); const double alpha_yaw = std::atan2(relative_body.y(), std::max(0.1, relative_body.x())); const double alpha = std::hypot(alpha_pitch, alpha_yaw);
  const AeroProperties aero = aero_at(vehicle, speed / air.sound_speed_mps, air.density * speed * vehicle.diameter_m / 1.8e-5, alpha);
  const Vec3 drag = speed > 1e-7 ? Vec3{-dynamic_pressure * vehicle.reference_area_m2 * aero.cd * relative_velocity.normalized()} : Vec3{0.0, 0.0, 0.0};
  // This is an explicitly empirical wet-surface increment, supplied by the
  // weather model. It is applied to the equations of motion as well as shown
  // in telemetry; it is not a claim of resolved multiphase droplet CFD.
  const Vec3 rain_drag = speed > 1e-7 ? Vec3{-dynamic_pressure * vehicle.reference_area_m2 * std::max(0.0, cfg.environment.rain_cd_delta) * relative_velocity.normalized()} : Vec3{0.0, 0.0, 0.0};
  const double pitch = 0.5 * (canards[0] - canards[2]); const double yaw = 0.5 * (canards[1] - canards[3]); const double canard_lift = dynamic_pressure * vehicle.canard_area_m2 * vehicle.canard_cl_alpha_per_rad;
  const Vec3 normal_body{0.0, -dynamic_pressure * vehicle.reference_area_m2 * aero.cn_alpha * alpha_yaw, dynamic_pressure * vehicle.reference_area_m2 * aero.cn_alpha * alpha_pitch};
  const Vec3 canard_force = r_body_to_enu * Vec3{0.0, canard_lift * yaw, -canard_lift * pitch};
  const Vec3 thrust = r_body_to_enu * Vec3{thrust_at(vehicle, state.time_s), 0.0, 0.0}; const Vec3 gravity{0.0, 0.0, -mass.mass_kg * kGravity};
  const double cds = parachute_cds_at(state, vehicle); const Vec3 parachute_drag = speed > 1e-7 ? Vec3{-dynamic_pressure * cds * relative_velocity.normalized()} : Vec3{0.0, 0.0, 0.0};
  const double static_margin = (vehicle.cp_m - mass.cg_m) / vehicle.diameter_m;
  Vec3 moment{0.0, canard_lift * pitch * vehicle.canard_arm_m, canard_lift * yaw * vehicle.canard_arm_m};
  const double restoring = dynamic_pressure * vehicle.reference_area_m2 * vehicle.diameter_m * static_margin;
  moment.y() -= restoring * alpha_pitch; moment.z() -= restoring * alpha_yaw; moment -= vehicle.angular_damping_nm_per_rad_s * state.omega_body_rad_s;
  Derivative out; out.position_dot = state.velocity_enu_mps; out.velocity_dot = (thrust + drag + rain_drag + r_body_to_enu * normal_body + gravity + canard_force + parachute_drag) / mass.mass_kg;
  out.quaternion_dot = state.q_body_to_enu.derivative(state.omega_body_rad_s); out.omega_dot = mass.inertia.inverse() * (moment - state.omega_body_rad_s.cross(mass.inertia * state.omega_body_rad_s)); return out;
}

FlightState rk4_step(const FlightState& state, const SimulationConfig& cfg, const std::array<double, 4>& canards, double dt) {
  const Derivative k1 = dynamics(state, cfg, canards); const Derivative k2 = dynamics(add_scaled(state, k1, dt * 0.5), cfg, canards); const Derivative k3 = dynamics(add_scaled(state, k2, dt * 0.5), cfg, canards); const Derivative k4 = dynamics(add_scaled(state, k3, dt), cfg, canards);
  FlightState out = state; out.position_enu_m += dt / 6.0 * (k1.position_dot + 2.0 * k2.position_dot + 2.0 * k3.position_dot + k4.position_dot); out.velocity_enu_mps += dt / 6.0 * (k1.velocity_dot + 2.0 * k2.velocity_dot + 2.0 * k3.velocity_dot + k4.velocity_dot);
  out.q_body_to_enu.w += dt / 6.0 * (k1.quaternion_dot.w + 2.0 * k2.quaternion_dot.w + 2.0 * k3.quaternion_dot.w + k4.quaternion_dot.w); out.q_body_to_enu.x += dt / 6.0 * (k1.quaternion_dot.x + 2.0 * k2.quaternion_dot.x + 2.0 * k3.quaternion_dot.x + k4.quaternion_dot.x); out.q_body_to_enu.y += dt / 6.0 * (k1.quaternion_dot.y + 2.0 * k2.quaternion_dot.y + 2.0 * k3.quaternion_dot.y + k4.quaternion_dot.y); out.q_body_to_enu.z += dt / 6.0 * (k1.quaternion_dot.z + 2.0 * k2.quaternion_dot.z + 2.0 * k3.quaternion_dot.z + k4.quaternion_dot.z);
  out.q_body_to_enu.normalize(); out.omega_body_rad_s += dt / 6.0 * (k1.omega_dot + 2.0 * k2.omega_dot + 2.0 * k3.omega_dot + k4.omega_dot); out.time_s += dt; return out;
}

double deterministic_noise(std::uint32_t seed, double time_s, double phase) { return std::sin(time_s * (0.73 + phase) + static_cast<double>(seed % 1009U) * (0.11 + phase)); }
} // namespace

void Quaternion::normalize() { const double norm = std::sqrt(w*w + x*x + y*y + z*z); if (norm > 1e-12) { w /= norm; x /= norm; y /= norm; z /= norm; } else { w = 1.0; x = y = z = 0.0; } }
Quaternion Quaternion::derivative(const Vec3& omega) const { return {-0.5*(x*omega.x()+y*omega.y()+z*omega.z()), 0.5*(w*omega.x()+y*omega.z()-z*omega.y()), 0.5*(w*omega.y()+z*omega.x()-x*omega.z()), 0.5*(w*omega.z()+x*omega.y()-y*omega.x())}; }
Mat3 Quaternion::to_rotation_matrix() const { Mat3 r; r << 1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), 2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), 2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y); return r; }
SimulationEngine::SimulationEngine(SimulationConfig config) : config_(std::move(config)) {}

SimulationResult SimulationEngine::run() const {
  SimulationResult result; result.flight_prediction_allowed = config_.vehicle.calibration.complete(); result.classification = result.flight_prediction_allowed && config_.request_flight_prediction ? "flight_prediction" : "preliminary_analysis";
  if (!result.flight_prediction_allowed && config_.request_flight_prediction) result.events.emplace_back("prediction_blocked: approved thrust, mass/inertia, and aerodynamic calibration are required");
  const Vec3 rail_direction{std::sin(config_.rail.azimuth_rad) * std::cos(config_.rail.elevation_rad), std::cos(config_.rail.azimuth_rad) * std::cos(config_.rail.elevation_rad), std::sin(config_.rail.elevation_rad)};
  const Quaternion rail_orientation = orientation_from_forward(rail_direction);
  FlightState state; state.q_body_to_enu = rail_orientation;
  double next_telemetry = 0.0, next_controller = 0.0, next_attitude = 0.0, pitch_integral = 0.0, yaw_integral = 0.0, previous_pitch_error = 0.0, previous_yaw_error = 0.0, estimated_altitude = 0.0;
  Vec3 measured_euler = euler_from(rail_orientation);
  std::array<double, 4> commands{}, targets{}, deflections{}; std::deque<std::pair<double, std::array<double, 4>>> command_history;
  int apogee_votes = 0; bool burnout_reported = false;
  while (state.time_s <= config_.duration_s) {
    if (!burnout_reported && state.time_s >= config_.vehicle.burn_time_s) { result.events.emplace_back("burnout"); burnout_reported = true; }
    if (state.on_rail && state.position_enu_m.dot(rail_direction) >= config_.rail.length_m) { state.on_rail = false; result.events.emplace_back("rail_exit"); }
    const double attitude_period = std::max(config_.integration_step_s, config_.sensors.attitude_update_period_s);
    if (state.time_s >= next_attitude) {
      measured_euler = euler_from(state.q_body_to_enu);
      const double noise = std::max(0.0, config_.sensors.attitude_noise_std_rad);
      measured_euler += noise * Vec3{
          deterministic_noise(config_.sensors.seed, state.time_s, 0.07),
          deterministic_noise(config_.sensors.seed, state.time_s, 0.11),
          deterministic_noise(config_.sensors.seed, state.time_s, 0.19)};
      next_attitude += attitude_period;
    }
    bool controller_tick = false;
    if (state.time_s >= next_controller) {
      const double pitch_error = config_.controller.target_pitch_rad - measured_euler.y(); const double yaw_error = config_.controller.target_yaw_rad - measured_euler.z();
      pitch_integral = clamp(pitch_integral + pitch_error * 0.01, -0.5, 0.5); yaw_integral = clamp(yaw_integral + yaw_error * 0.01, -0.5, 0.5);
      const double pitch = clamp(config_.controller.kp*pitch_error + config_.controller.ki*pitch_integral + config_.controller.kd*(pitch_error-previous_pitch_error)/0.01, -config_.vehicle.max_canard_deflection_rad, config_.vehicle.max_canard_deflection_rad);
      const double yaw = clamp(config_.controller.kp*yaw_error + config_.controller.ki*yaw_integral + config_.controller.kd*(yaw_error-previous_yaw_error)/0.01, -config_.vehicle.max_canard_deflection_rad, config_.vehicle.max_canard_deflection_rad);
      commands = {pitch + config_.vehicle.canard_mount_offset_rad[0], yaw + config_.vehicle.canard_mount_offset_rad[1], -pitch + config_.vehicle.canard_mount_offset_rad[2], -yaw + config_.vehicle.canard_mount_offset_rad[3]};
      command_history.emplace_back(state.time_s, commands); previous_pitch_error = pitch_error; previous_yaw_error = yaw_error; next_controller += 0.01; controller_tick = true;
    }
    while (!command_history.empty() && state.time_s - command_history.front().first >= config_.vehicle.canard_command_delay_s) { targets = command_history.front().second; command_history.pop_front(); }
    const double max_delta = config_.vehicle.max_canard_rate_rad_s * config_.integration_step_s;
    for (std::size_t index = 0; index < deflections.size(); ++index) deflections[index] += clamp(targets[index] - deflections[index], -max_delta, max_delta);
    if (!state.parachute_deployed && controller_tick && state.time_s > config_.vehicle.burn_time_s && state.velocity_enu_mps.z() < -0.5 && state.position_enu_m.z() > 20.0) {
      if (dynamics(state, config_, deflections).velocity_dot.norm() < 2.0*kGravity) ++apogee_votes; else apogee_votes = 0;
      if (apogee_votes >= 2) { state.parachute_command_time_s = state.time_s + config_.vehicle.parachute_deploy_delay_s; state.parachute_deployed = true; result.events.emplace_back("apogee_detected"); result.events.emplace_back("parachute_deploy_commanded"); }
    }
    if (state.time_s >= next_telemetry) {
      const AirProperties air = atmosphere_at(config_.environment, state.time_s, state.position_enu_m.z()); const Vec3 relative_velocity = state.velocity_enu_mps - air.wind_enu_mps; const double speed = relative_velocity.norm(); const double q = 0.5*air.density*speed*speed; const Vec3 relative_body = state.q_body_to_enu.to_rotation_matrix().transpose()*relative_velocity;
      const double alpha = std::hypot(std::atan2(-relative_body.z(), std::max(0.1, relative_body.x())), std::atan2(relative_body.y(), std::max(0.1, relative_body.x()))); const AeroProperties aero = aero_at(config_.vehicle, speed/air.sound_speed_mps, air.density*speed*config_.vehicle.diameter_m/1.8e-5, alpha); const MassProperties mass = mass_properties_at(config_.vehicle, state.time_s);
      const double baro = state.position_enu_m.z() + config_.sensors.barometer_bias_m + config_.sensors.barometer_noise_std_m*deterministic_noise(config_.sensors.seed, state.time_s, 0.17); const double gps = state.position_enu_m.z() + config_.sensors.gps_noise_std_m*deterministic_noise(config_.sensors.seed, state.time_s, 0.43); const double filter_alpha = config_.telemetry_period_s / std::max(config_.telemetry_period_s, config_.sensors.estimator_time_constant_s); estimated_altitude += clamp(filter_alpha, 0.0, 1.0)*(baro-estimated_altitude);
      const auto wgs84 = enu_to_wgs84(config_.launch_site, state.position_enu_m);
      TelemetrySample telemetry;
      telemetry.time_s = state.time_s; telemetry.position_enu_m = state.position_enu_m;
      telemetry.latitude_deg = wgs84[0]; telemetry.longitude_deg = wgs84[1]; telemetry.altitude_msl_m = wgs84[2];
      telemetry.velocity_enu_mps = state.velocity_enu_mps; telemetry.quaternion = state.q_body_to_enu;
      telemetry.euler_rad = euler_from(state.q_body_to_enu); telemetry.omega_body_rad_s = state.omega_body_rad_s;
      telemetry.canard_deflection_rad = deflections; telemetry.pid_output = {0.5*(commands[0]-commands[2]), 0.5*(commands[1]-commands[3]), 0.0};
      telemetry.altitude_agl_m = std::max(0.0,state.position_enu_m.z()); telemetry.mach = speed/air.sound_speed_mps; telemetry.dynamic_pressure_pa = q;
      telemetry.air_temperature_k = air.temperature_k; telemetry.air_pressure_pa = air.pressure_pa;
      telemetry.air_relative_humidity = air.relative_humidity; telemetry.air_density_kg_m3 = air.density;
      telemetry.surface_temperature_k = air.temperature_k+config_.environment.friction_heat_coefficient*speed*speed;
      telemetry.estimated_altitude_agl_m = estimated_altitude; telemetry.parachute_deployed = state.parachute_deployed;
      telemetry.wind_enu_mps = air.wind_enu_mps; telemetry.relative_velocity_enu_mps = relative_velocity;
      telemetry.controller_error_rad = {0.0, config_.controller.target_pitch_rad-telemetry.euler_rad.y(), config_.controller.target_yaw_rad-telemetry.euler_rad.z()};
      telemetry.airspeed_mps = speed; telemetry.thrust_n = thrust_at(config_.vehicle,state.time_s); telemetry.mass_kg = mass.mass_kg;
      telemetry.drag_force_n = q*config_.vehicle.reference_area_m2*aero.cd; telemetry.rain_impact_force_n = q*config_.vehicle.reference_area_m2*config_.environment.rain_cd_delta;
      telemetry.canard_lift_n = q*config_.vehicle.canard_area_m2*config_.vehicle.canard_cl_alpha_per_rad*std::max({std::abs(deflections[0]),std::abs(deflections[1]),std::abs(deflections[2]),std::abs(deflections[3])});
      telemetry.static_margin_calibers = (config_.vehicle.cp_m-mass.cg_m)/config_.vehicle.diameter_m; telemetry.friction_heat_proxy = config_.environment.friction_heat_coefficient*speed*speed;
      telemetry.cg_m = mass.cg_m; telemetry.inertia_kg_m2 = mass.inertia.diagonal(); telemetry.on_rail = state.on_rail;
      telemetry.parachute_cds_m2 = parachute_cds_at(state,config_.vehicle); telemetry.barometer_altitude_agl_m = baro; telemetry.gps_altitude_agl_m = gps;
      telemetry.imu_acceleration_body_mps2 = state.q_body_to_enu.to_rotation_matrix().transpose()*dynamics(state,config_,deflections).velocity_dot + config_.sensors.imu_accel_noise_std_mps2*Vec3{deterministic_noise(config_.sensors.seed,state.time_s,0.2),deterministic_noise(config_.sensors.seed,state.time_s,0.4),deterministic_noise(config_.sensors.seed,state.time_s,0.6)};
      result.samples.push_back(std::move(telemetry)); next_telemetry += config_.telemetry_period_s;
    }
    state = rk4_step(state, config_, deflections, config_.integration_step_s);
    if (state.on_rail) { const double distance = std::max(0.0, state.position_enu_m.dot(rail_direction)); const double velocity = std::max(0.0, state.velocity_enu_mps.dot(rail_direction)); state.position_enu_m=distance*rail_direction; state.velocity_enu_mps=velocity*rail_direction; state.q_body_to_enu=rail_orientation; state.omega_body_rad_s.setZero(); }
    if (state.position_enu_m.z() <= 0.0 && state.time_s > 1.0 && !state.on_rail) { result.events.emplace_back("landing"); break; }
  }
  return result;
}
} // namespace sultana
