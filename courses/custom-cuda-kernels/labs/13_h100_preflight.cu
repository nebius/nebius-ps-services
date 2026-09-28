#include "common.cuh"

#include <iostream>

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) {
    std::cout << "Usage: 13_h100_preflight [--profile small|large]\n";
    return 0;
  }
  try {
    validate_simple_arguments(argc, argv);
    const auto properties = require_course_gpu();
    int runtime = 0;
    int driver = 0;
    CUDA_CHECK(cudaRuntimeGetVersion(&runtime));
    CUDA_CHECK(cudaDriverGetVersion(&driver));
    std::cout << "schema=gpu-course-result/v1\nlab_id=13_h100_preflight\n"
              << "compute_capability=" << properties.major << '.' << properties.minor << '\n'
              << "sm_count=" << properties.multiProcessorCount << "\nglobal_memory_bytes=" << properties.totalGlobalMem << '\n'
              << "runtime_version=" << runtime << "\ndriver_api_version=" << driver << '\n';
    std::cout << "course_checks=passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "ERROR: " << error.what() << '\n';
    return 2;
  }
}
