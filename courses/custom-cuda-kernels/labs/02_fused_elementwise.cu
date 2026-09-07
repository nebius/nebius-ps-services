#include "common.cuh"

__global__ void scale_bias(const float* input, const float* bias, float* temporary, float scale, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) temporary[index] = input[index] * scale + bias[index];
}

__global__ void relu(const float* input, float* output, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) output[index] = fmaxf(input[index], 0.0F);
}

__global__ void fused_scale_bias_relu(const float* input, const float* bias, float* output, float scale, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) output[index] = fmaxf(input[index] * scale + bias[index], 0.0F);
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 02_fused_elementwise [--smoke]\n"; return 0; }
  try {
    const std::size_t count = problem_size(argc, argv, 4099, 1U << 25);
    require_h100();
    std::vector<float> input(count), bias(count), expected(count), separate_observed(count), fused_observed(count);
    for (std::size_t index = 0; index < count; ++index) {
      input[index] = static_cast<float>(static_cast<int>(index % 101) - 50) / 25.0F;
      bias[index] = static_cast<float>(index % 7) / 10.0F;
      expected[index] = std::max(input[index] * 1.25F + bias[index], 0.0F);
    }
    DeviceBuffer<float> device_input(count), device_bias(count), temporary(count), separate_output(count), fused_output(count);
    CUDA_CHECK(cudaMemcpy(device_input.get(), input.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(device_bias.get(), bias.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    constexpr int threads = 256;
    const int blocks = static_cast<int>((count + threads - 1) / threads);
    const auto separate_timing = benchmark_cuda([&] {
      scale_bias<<<blocks, threads>>>(device_input.get(), device_bias.get(), temporary.get(), 1.25F, count);
      relu<<<blocks, threads>>>(temporary.get(), separate_output.get(), count);
    });
    const auto fused_timing = benchmark_cuda([&] {
      fused_scale_bias_relu<<<blocks, threads>>>(device_input.get(), device_bias.get(), fused_output.get(), 1.25F, count);
    });
    CUDA_CHECK(cudaMemcpy(separate_observed.data(), separate_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaMemcpy(fused_observed.data(), fused_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    check_close(expected, separate_observed);
    check_close(expected, fused_observed);
    print_result("02_fused_elementwise", fused_timing, count);
    print_timing("separate", separate_timing);
    print_timing("fused", fused_timing);
    std::cout << "baseline_correctness=passed\ncandidate_correctness=passed\nlogical_separate_bytes=" << count * sizeof(float) * 5 << "\nlogical_fused_bytes=" << count * sizeof(float) * 3 << '\n';
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
