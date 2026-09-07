#include "common.cuh"

__global__ void stencil(const float* input, float* output, std::size_t count) {
  extern __shared__ float tile[];
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  const int local = static_cast<int>(threadIdx.x) + 1;
  tile[local] = index < count ? input[index] : 0.0F;
  if (threadIdx.x == 0) tile[0] = index > 0 ? input[index - 1] : 0.0F;
  if (threadIdx.x == blockDim.x - 1) tile[blockDim.x + 1] = index + 1 < count ? input[index + 1] : 0.0F;
  __syncthreads();
  if (index < count) output[index] = 0.25F * tile[local - 1] + 0.5F * tile[local] + 0.25F * tile[local + 1];
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 05_tiled_stencil [--smoke]\n"; return 0; }
  try {
    const std::size_t count = problem_size(argc, argv, 1003, 1U << 24);
    require_h100();
    std::vector<float> input(count), expected(count), observed(count);
    for (std::size_t index = 0; index < count; ++index) input[index] = static_cast<float>(index % 101) / 101.0F;
    for (std::size_t index = 0; index < count; ++index) {
      const float left = index > 0 ? input[index - 1] : 0.0F;
      const float right = index + 1 < count ? input[index + 1] : 0.0F;
      expected[index] = 0.25F * left + 0.5F * input[index] + 0.25F * right;
    }
    DeviceBuffer<float> device_input(count), output(count);
    CUDA_CHECK(cudaMemcpy(device_input.get(), input.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    constexpr int threads = 256;
    const int blocks = static_cast<int>((count + threads - 1) / threads);
    const auto timing = benchmark_cuda([&] {
      stencil<<<blocks, threads, (threads + 2) * sizeof(float)>>>(device_input.get(), output.get(), count);
    });
    CUDA_CHECK(cudaMemcpy(observed.data(), output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    check_close(expected, observed);
    print_result("05_tiled_stencil", timing, count);
    std::cout << "shared_bytes_per_block=" << (threads + 2) * sizeof(float) << "\nhalo_values_per_full_block=2\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
