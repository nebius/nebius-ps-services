#include "common.cuh"

__global__ void vector_add(const float* left, const float* right, float* output, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) output[index] = left[index] + right[index];
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 01_vector_add [--smoke]\n"; return 0; }
  try {
    const std::size_t count = problem_size(argc, argv, 1003, 1U << 24);
    require_h100();
    std::vector<float> left(count), right(count), expected(count), observed(count);
    for (std::size_t index = 0; index < count; ++index) {
      left[index] = static_cast<float>(index % 97) / 97.0F;
      right[index] = static_cast<float>(index % 53) / 53.0F;
      expected[index] = left[index] + right[index];
    }
    DeviceBuffer<float> device_left(count), device_right(count), device_output(count);
    CUDA_CHECK(cudaMemcpy(device_left.get(), left.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(device_right.get(), right.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    constexpr int threads = 256;
    const int blocks = static_cast<int>((count + threads - 1) / threads);
    const auto timing = benchmark_cuda([&] {
      vector_add<<<blocks, threads>>>(device_left.get(), device_right.get(), device_output.get(), count);
    });
    CUDA_CHECK(cudaMemcpy(observed.data(), device_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    check_close(expected, observed);
    print_result("01_vector_add", timing, count);
    std::cout << "blocks=" << blocks << "\nthreads_per_block=" << threads << '\n';
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
