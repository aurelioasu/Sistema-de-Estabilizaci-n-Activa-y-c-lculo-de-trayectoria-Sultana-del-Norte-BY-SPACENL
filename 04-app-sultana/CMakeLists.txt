cmake_minimum_required(VERSION 3.24)
project(sultana_flight_simulator VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(FetchContent)

find_package(Eigen3 CONFIG QUIET)
if(NOT Eigen3_FOUND)
  FetchContent_Declare(eigen
    GIT_REPOSITORY https://gitlab.com/libeigen/eigen.git
    GIT_TAG 3.4.0
    GIT_SHALLOW TRUE)
  FetchContent_MakeAvailable(eigen)
endif()

find_package(pybind11 CONFIG QUIET)
if(NOT pybind11_FOUND)
  FetchContent_Declare(pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG v3.0.1
    GIT_SHALLOW TRUE)
  FetchContent_MakeAvailable(pybind11)
endif()

add_library(sultana_physics STATIC
  cpp/src/simulation.cpp
)
target_include_directories(sultana_physics PUBLIC cpp/include)
target_link_libraries(sultana_physics PUBLIC Eigen3::Eigen)
target_compile_features(sultana_physics PUBLIC cxx_std_20)
set_target_properties(sultana_physics PROPERTIES POSITION_INDEPENDENT_CODE ON)

pybind11_add_module(sultana_core cpp/bindings/module.cpp)
target_link_libraries(sultana_core PRIVATE sultana_physics)
target_include_directories(sultana_core PRIVATE cpp/include)

include(CTest)
if(BUILD_TESTING)
  find_package(Catch2 CONFIG QUIET)
  if(NOT Catch2_FOUND)
    FetchContent_Declare(catch2
      GIT_REPOSITORY https://github.com/catchorg/Catch2.git
      GIT_TAG v3.8.1
      GIT_SHALLOW TRUE)
    FetchContent_MakeAvailable(catch2)
  endif()
  add_executable(sultana_physics_tests cpp/tests/test_simulation.cpp)
  target_link_libraries(sultana_physics_tests PRIVATE sultana_physics Catch2::Catch2WithMain)
  add_test(NAME sultana_physics_tests COMMAND sultana_physics_tests)
endif()
