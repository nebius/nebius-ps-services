#include "common.cuh"

__global__ void cluster_probe(unsigned int* observed) {
#if __CUDA_ARCH__ >= 900
  if (threadIdx.x == 0) observed[blockIdx.x] = blockIdx.x;
#endif
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 10_hopper_cluster [--smoke]\n"; return 0; }
  try {
    validate_simple_arguments(argc, argv);
    require_h100();
    constexpr int blocks = 4;
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
    const auto timing = benchmark_cuda([&] {
      CUDA_CHECK(cudaLaunchKernelEx(&config, cluster_probe, output.get()));
    });
    std::vector<unsigned int> observed(blocks);
    CUDA_CHECK(cudaMemcpy(observed.data(), output.get(), blocks * sizeof(unsigned int), cudaMemcpyDeviceToHost));
    for (int index = 0; index < blocks; ++index) if (observed[index] != static_cast<unsigned int>(index)) throw std::runtime_error("cluster launch output mismatch");
    print_result("10_hopper_cluster", timing, blocks);
    std::cout << "architecture=sm_90a\ncluster_blocks=2\nportable_default=false\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
