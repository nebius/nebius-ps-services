cmake_minimum_required(VERSION 3.25)
if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
  set(CMAKE_CUDA_ARCHITECTURES 90)
endif()
project(h100_custom_cuda_kernels LANGUAGES CXX CUDA)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CUDA_STANDARD 20)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)
option(COURSE_ENABLE_SM90A "Build the optional non-forward-compatible Hopper target" OFF)
option(COURSE_ENABLE_CUTLASS "Build required Lab 09 with the pinned CUTLASS fused epilogue" ON)
set(CUTLASS_ROOT "" CACHE PATH "Path to the reviewed CUTLASS 4.6.1 source tree")
find_package(CUDAToolkit REQUIRED)
enable_testing()

set(REQUIRED_LABS
  00_h100_preflight
  01_vector_add
  02_fused_elementwise
  03_tiled_transpose
  04_reduction
  05_tiled_stencil
  06_divergence_tail
  07_resource_sweep
  08_async_pipeline
  09_library_epilogue
  11_residual_rmsnorm
  12_capstone)

foreach(lab IN LISTS REQUIRED_LABS)
  add_executable(${lab} labs/${lab}.cu)
  target_include_directories(${lab} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/labs)
  target_compile_options(
    ${lab}
    PRIVATE
      $<$<COMPILE_LANGUAGE:CUDA>:--expt-relaxed-constexpr>
      $<$<COMPILE_LANGUAGE:CUDA>:--resource-usage>
  )
  if(lab STREQUAL "12_capstone")
    add_test(NAME ${lab}_smoke COMMAND ${lab} --smoke --variant-order baseline-first)
  else()
    add_test(NAME ${lab}_smoke COMMAND ${lab} --smoke)
  endif()
endforeach()

add_test(NAME 08_async_pipeline_zero_work COMMAND 08_async_pipeline --smoke --work-iterations 0)
add_test(NAME 08_async_pipeline_max_work COMMAND 08_async_pipeline --smoke --work-iterations 1024)
add_test(NAME 08_async_pipeline_invalid_work COMMAND 08_async_pipeline --work-iterations -1)
set_tests_properties(08_async_pipeline_invalid_work PROPERTIES WILL_FAIL TRUE)

target_link_libraries(09_library_epilogue PRIVATE CUDA::cublas)
if(COURSE_ENABLE_CUTLASS)
  if(NOT EXISTS "${CUTLASS_ROOT}/include/cutlass/version.h")
    message(FATAL_ERROR "COURSE_ENABLE_CUTLASS requires CUTLASS_ROOT pointing to CUTLASS 4.6.1")
  endif()
  target_include_directories(09_library_epilogue PRIVATE "${CUTLASS_ROOT}/include")
  target_compile_definitions(09_library_epilogue PRIVATE COURSE_ENABLE_CUTLASS=1)
endif()

if(COURSE_ENABLE_SM90A)
  add_executable(10_hopper_cluster labs/10_hopper_cluster.cu)
  target_include_directories(10_hopper_cluster PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/labs)
  set_property(TARGET 10_hopper_cluster PROPERTY CUDA_ARCHITECTURES OFF)
  target_compile_options(
    10_hopper_cluster
    PRIVATE
      $<$<COMPILE_LANGUAGE:CUDA>:-arch=sm_90a>
      $<$<COMPILE_LANGUAGE:CUDA>:--expt-relaxed-constexpr>
      $<$<COMPILE_LANGUAGE:CUDA>:--resource-usage>
  )
  add_test(NAME 10_hopper_cluster_smoke COMMAND 10_hopper_cluster --smoke)
endif()
