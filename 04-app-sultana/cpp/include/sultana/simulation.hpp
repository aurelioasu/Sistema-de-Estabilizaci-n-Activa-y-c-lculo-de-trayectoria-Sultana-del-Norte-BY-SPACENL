#pragma once

#include "sultana/types.hpp"

namespace sultana {

class SimulationEngine {
 public:
  explicit SimulationEngine(SimulationConfig config);
  [[nodiscard]] SimulationResult run() const;

 private:
  SimulationConfig config_;
};

} // namespace sultana
