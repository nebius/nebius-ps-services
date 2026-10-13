#include "common.cuh"

__global__ void cluster_probe(unsigned int* observed) {
#if __CUDA_ARCH__ >= 900
  if (threadIdx.x == 0) observed[blockIdx.x] = blockIdx.x;
#endif
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 10_hopper_cluster [--workload small|large]\n"; return 0; }
  try {
    validate_simple_arguments(argc, argv);
    require_course_gpu();
    constexpr int blocks = 4;
    // Allocate device buffers; their owners release them when this scope ends.
    DeviceBuffer<unsigned int> output(blocks);
    cudaLaunchConfig_t config{};
    config.gridDim = dim3(blocks);
    config.blockDim = dim3(128);
    cudaLaunchAttribute attribute{};
    attribute.id = cudaLaunchAttributeClusterDimension;
    attribute.val.clusterDim.x = 2;
    attribute.val.clusterDim.y = 1;
    attribute.val.clusterDim.z = 1;
    config.attrs = &attribute;
    config.numAttrs = 1;
    // Warm up, then time GPU work with the shared synchronized CUDA-event helper.
    const auto timing = benchmark_cuda([&] {
      CUDA_CHECK(cudaLaunchKernelEx(&config, cluster_probe, output.get()));
    });
    // Allocate host inputs and reference/output storage.
    std::vector<unsigned int> observed(blocks);
    // Copy completed device output back for validation outside kernel timing.
    CUDA_CHECK(cudaMemcpy(observed.data(), output.get(), blocks * sizeof(unsigned int), cudaMemcpyDeviceToHost));
    for (int index = 0; index < blocks; ++index) if (observed[index] != static_cast<unsigned int>(index)) throw std::runtime_error("cluster launch output mismatch");
    // Emit the measured summary for the job’s CUDA result adapter.
    print_result("10_hopper_cluster", timing, blocks);
    std::cout << "architecture=sm_90a\ncluster_blocks=2\nportable_default=false\n";
    std::cout << "course_checks=passed\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
