#include "sultana/simulation.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include <algorithm>
#include <cmath>

using namespace sultana;

TEST_CASE("simulation emits deterministic normalized quaternion telemetry") {
  SimulationConfig config;
  config.duration_s = 0.1;
  config.vehicle.thrust_curve = {{0.0, 0.0}, {0.05, 50.0}, {0.1, 0.0}};
  const auto a = SimulationEngine{config}.run();
  const auto b = SimulationEngine{config}.run();
  REQUIRE_FALSE(a.samples.empty());
  REQUIRE(a.samples.size() == b.samples.size());
  const auto& q = a.samples.back().quaternion;
  REQUIRE(std::sqrt(q.w*q.w + q.x*q.x + q.y*q.y + q.z*q.z) == Catch::Approx(1.0).margin(1e-10));
  REQUIRE(a.samples.back().position_enu_m.z() == Catch::Approx(b.samples.back().position_enu_m.z()));
}

TEST_CASE("unapproved model cannot become a flight prediction") {
  SimulationConfig config;
  config.duration_s = 0.01;
  config.request_flight_prediction = true;
  const auto result = SimulationEngine{config}.run();
  REQUIRE_FALSE(result.flight_prediction_allowed);
  REQUIRE(result.classification == "preliminary_analysis");
}

TEST_CASE("calibrated mass curve replaces the linear propellant approximation") {
  SimulationConfig config;
  config.duration_s = 0.9;
  config.telemetry_period_s = 0.1;
  config.vehicle.thrust_curve = {{0.0, 100.0}, {1.0, 100.0}};
  config.vehicle.mass_curve = {{0.0, 0.25, 0.72, Vec3{0.028, 0.25, 0.25}}, {1.0, 0.0, 0.63, Vec3{0.025, 0.22, 0.22}}};
  const auto result = SimulationEngine{config}.run();
  REQUIRE(result.samples.front().mass_kg == Catch::Approx(1.45));
  REQUIRE(result.samples.back().mass_kg < 1.25);
  REQUIRE(result.samples.back().cg_m < result.samples.front().cg_m);
}

TEST_CASE("rail and sensor telemetry are exposed to the flight model") {
  SimulationConfig config;
  config.duration_s = 1.0;
  config.vehicle.thrust_curve = {{0.0, 120.0}, {1.0, 120.0}};
  config.rail.length_m = 0.2;
  config.sensors.barometer_noise_std_m = 2.0;
  const auto result = SimulationEngine{config}.run();
  REQUIRE(std::find(result.events.begin(), result.events.end(), "rail_exit") != result.events.end());
  REQUIRE_FALSE(result.samples.back().on_rail);
  REQUIRE(result.samples.back().barometer_altitude_agl_m != Catch::Approx(result.samples.back().altitude_agl_m));
}
