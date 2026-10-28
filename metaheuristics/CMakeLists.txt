cmake_minimum_required(VERSION 3.16)
project(POIP LANGUAGES CXX)

# =========================
# Global C++ settings
# =========================
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# =========================
# Warnings
# =========================
if(MSVC)
    add_compile_options(/W4 /permissive-)
else()
    add_compile_options(-Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion)
endif()

# =========================
# Common library
# =========================
add_library(poip_common
    common/instance.cpp
    common/solution.cpp
)
target_include_directories(poip_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/common)

# =========================
# Output directories
# =========================
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

# =========================
# Heuristics (non-gurobi)
# =========================
add_executable(heuristic_blank heuristics/heuristic_blank.cpp)
target_link_libraries(heuristic_blank PRIVATE poip_common)

add_executable(test_load test_load.cpp)
target_link_libraries(test_load PRIVATE poip_common)

add_executable(heuristic_greedy_circuit
    heuristics/heuristic_greedy_circuit.cpp
    common/instance.cpp
    common/solution.cpp
)

add_executable(heuristic_greedy_circuit_smart heuristics/heuristic_greedy_circuit_smart.cpp)
target_link_libraries(heuristic_greedy_circuit_smart PRIVATE poip_common)
# =========================
# Heuristics NS
# =========================

add_executable(heuristic_aeration_ns
    heuristics/heuristic_aeration_ns.cpp
)

target_link_libraries(heuristic_aeration_ns
    poip_common
)

target_compile_features(heuristic_aeration_ns PRIVATE cxx_std_17)

# =========================
# Heuristics Tabu
# =========================

add_executable(heuristic_tabu_circuit_blocks
    heuristics/heuristic_tabu_circuit_blocks.cpp
)

target_link_libraries(heuristic_tabu_circuit_blocks
    poip_common
)

target_compile_features(heuristic_tabu_circuit_blocks PRIVATE cxx_std_17)

# =========================
# Heuristics Tabu violation
# =========================

add_executable(heuristic_tabu_violation
    heuristics/heuristic_tabu_violation.cpp
)

target_link_libraries(heuristic_tabu_violation PRIVATE poip_common)

target_compile_features(heuristic_tabu_violation PRIVATE cxx_std_17)


# =========================
# Gurobi (optional)
# =========================

# list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/cmake")
# find_package(GUROBI QUIET)

# if(GUROBI_FOUND)
#     message(STATUS "Found GUROBI: ${GUROBI_INCLUDE_DIRS}")

#     add_executable(gurobi_slotting_toy
#         heuristics/gurobi_slotting_toy.cpp
#         common/instance.cpp
#         common/solution.cpp
#     )
#     target_include_directories(gurobi_slotting_toy PRIVATE ${GUROBI_INCLUDE_DIRS})
    

#     target_link_libraries(gurobi_slotting_toy PRIVATE
#         ${GUROBI_CXX_LIBRARY}
#         ${GUROBI_LIBRARY}
#     )
#     if(UNIX)
#         target_link_libraries(gurobi_slotting_toy PRIVATE m pthread dl)
#     endif()
#         add_executable(gurobi_model2
#         heuristics/gurobi_model2.cpp
#     )
#     target_link_libraries(gurobi_model2 PRIVATE poip_common)
#     target_include_directories(gurobi_model2 PRIVATE ${GUROBI_INCLUDE_DIRS})

#     target_link_libraries(gurobi_model2 PRIVATE
#         ${GUROBI_CXX_LIBRARY}
#         ${GUROBI_LIBRARY}
#     )
#     if(UNIX)
#         target_link_libraries(gurobi_model2 PRIVATE m pthread dl)
#     endif()
            

#     target_link_libraries(gurobi_model_new_obj PRIVATE
#         ${GUROBI_CXX_LIBRARY}
#         ${GUROBI_LIBRARY}
#     )
#     if(UNIX)
#         target_link_libraries(gurobi_model_new_obj PRIVATE m pthread dl)
#     endif()

# else()
#     message(STATUS "GUROBI not found: gurobi_slotting_toy will not be built.")
# endif()

# # =========================
# # Nice messages
# # =========================
# message(STATUS "Build type      : ${CMAKE_BUILD_TYPE}")
# message(STATUS "C++ standard    : C++${CMAKE_CXX_STANDARD}")
# message(STATUS "Binary dir      : ${CMAKE_BINARY_DIR}/bin")
