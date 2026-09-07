#include "common.cuh"

template <int state_count>
__global__ void resource_kernel(const float* input, float* output, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index >= count) return;
  float state[state_count];
#pragma unroll
  for (int item = 0; item < state_count; ++item) {
    state[item] = input[index] + static_cast<float>(item) * 0.0001F;
  }
#pragma unroll
  for (int iteration = 0; iteration < 8; ++iteration) {
#pragma unroll
    for (int item = 0; item < state_count; ++item) {
      state[item] = fmaf(state[item], 1.00001F, static_cast<float>(item + 1) * 0.00001F);
    }
  }
  float sum = 0.0F;
#pragma unroll
  for (int item = 0; item < state_count; ++item) sum += state[item];
  output[index] = sum;
}

template <int state_count>
void report_case(const float* input, float* output, std::size_t count, int threads) {
  const int blocks = static_cast<int>((count + threads - 1) / threads);
  int active_blocks = 0;
  CUDA_CHECK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(
      &active_blocks, resource_kernel<state_count>, threads, 0));
  cudaFuncAttributes attributes{};
  CUDA_CHECK(cudaFuncGetAttributes(&attributes, resource_kernel<state_count>));
  const auto timing = benchmark_cuda([&] {
    resource_kernel<state_count><<<blocks, threads>>>(input, output, count);
  });
  float expected = 0.0F;
  for (int item = 0; item < state_count; ++item) {
    float value = 0.01F + static_cast<float>(item) * 0.0001F;
    for (int iteration = 0; iteration < 8; ++iteration) {
      value = std::fma(value, 1.00001F, static_cast<float>(item + 1) * 0.00001F);
    }
    expected += value;
  }
  std::vector<float> observed(2);
  CUDA_CHECK(cudaMemcpy(&observed[0], output, sizeof(float), cudaMemcpyDeviceToHost));
  CUDA_CHECK(cudaMemcpy(&observed[1], output + count - 1, sizeof(float), cudaMemcpyDeviceToHost));
  check_close({expected, expected}, observed);
  std::cout << "case threads=" << threads
            << " state_values=" << state_count
            << " registers_per_thread=" << attributes.numRegs
            << " local_bytes_per_thread=" << attributes.localSizeBytes
            << " active_blocks_per_sm=" << active_blocks
            << " resident_warps=" << static_cast<float>(active_blocks * threads) / 32.0F
            << " median_ms=" << timing.median_ms
            << " p90_ms=" << timing.p90_ms
            << " correctness=passed\n";
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 07_resource_sweep [--smoke]\n"; return 0; }
  try {
    const std::size_t count = problem_size(argc, argv, 4096, 1U << 24);
    const auto properties = require_h100();
    std::vector<float> input(count, 0.01F);
    DeviceBuffer<float> device_input(count), output(count);
    CUDA_CHECK(cudaMemcpy(device_input.get(), input.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    std::cout << "schema=gpu-course-result/v1\nlab_id=07_resource_sweep\n";
    for (const int threads : {64, 128, 256, 512}) {
      report_case<4>(device_input.get(), output.get(), count, threads);
    }
    report_case<16>(device_input.get(), output.get(), count, 256);
    report_case<64>(device_input.get(), output.get(), count, 256);
    std::cout << "sm_count=" << properties.multiProcessorCount << "\ninspect_compiler_resource_usage=true\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
