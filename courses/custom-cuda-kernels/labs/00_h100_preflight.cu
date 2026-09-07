#include "common.cuh"

#include <iostream>

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) {
    std::cout << "Usage: 00_h100_preflight [--smoke]\n";
    return 0;
  }
  try {
    validate_simple_arguments(argc, argv);
    const auto properties = require_h100();
    int runtime = 0;
    int driver = 0;
    CUDA_CHECK(cudaRuntimeGetVersion(&runtime));
    CUDA_CHECK(cudaDriverGetVersion(&driver));
    std::cout << "schema=gpu-course-result/v1\nlab_id=00_h100_preflight\n"
              << "gpu_family=NVIDIA H100\ncompute_capability=" << properties.major << '.' << properties.minor << '\n'
              << "sm_count=" << properties.multiProcessorCount << "\nglobal_memory_bytes=" << properties.totalGlobalMem << '\n'
              << "runtime_version=" << runtime << "\ndriver_api_version=" << driver << '\n';
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "ERROR: " << error.what() << '\n';
    return 2;
  }
}
