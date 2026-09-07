#include "common.cuh"

__global__ void fused_residual_rmsnorm(const float* input, const float* residual, const float* scale, float* output, int width) {
  extern __shared__ float shared[];
  const int row = blockIdx.x;
  const int column = threadIdx.x;
  float value = 0.0F;
  float square = 0.0F;
  if (column < width) {
    value = input[row * width + column] + residual[row * width + column];
    square = value * value;
  }
  shared[column] = square;
  __syncthreads();
  for (int stride = blockDim.x / 2; stride > 0; stride >>= 1) {
    if (column < stride) shared[column] += shared[column + stride];
    __syncthreads();
  }
  const float inverse_rms = rsqrtf(shared[0] / static_cast<float>(width) + 1e-5F);
  if (column < width) output[row * width + column] = value * inverse_rms * scale[column];
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 11_residual_rmsnorm [--smoke]\n"; return 0; }
  try {
    const int rows = static_cast<int>(problem_size(argc, argv, 17, 4096));
    require_h100();
    constexpr int width = 256;
    const std::size_t count = static_cast<std::size_t>(rows) * width;
    std::vector<float> input(count), residual(count), scale(width), expected(count), observed(count);
    for (std::size_t index = 0; index < count; ++index) { input[index] = static_cast<float>(index % 31) / 31.0F; residual[index] = static_cast<float>(index % 17) / 37.0F; }
    std::fill(scale.begin(), scale.end(), 1.0F);
    for (int row = 0; row < rows; ++row) {
      float sum = 0.0F;
      for (int column = 0; column < width; ++column) { const float value = input[row * width + column] + residual[row * width + column]; sum += value * value; }
      const float inverse_rms = 1.0F / std::sqrt(sum / width + 1e-5F);
      for (int column = 0; column < width; ++column) expected[row * width + column] = (input[row * width + column] + residual[row * width + column]) * inverse_rms;
    }
    DeviceBuffer<float> device_input(count), device_residual(count), device_scale(width), output(count);
    CUDA_CHECK(cudaMemcpy(device_input.get(), input.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(device_residual.get(), residual.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(device_scale.get(), scale.data(), width * sizeof(float), cudaMemcpyHostToDevice));
    const auto timing = benchmark_cuda([&] {
      fused_residual_rmsnorm<<<rows, width, width * sizeof(float)>>>(device_input.get(), device_residual.get(), device_scale.get(), output.get(), width);
    });
    CUDA_CHECK(cudaMemcpy(observed.data(), output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    check_close(expected, observed);
    print_result("11_residual_rmsnorm", timing, count);
    std::cout << "accumulation_dtype=float32\nepsilon=1e-5\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
