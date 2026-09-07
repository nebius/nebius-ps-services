#include "common.cuh"

#include <cub/device/device_reduce.cuh>

__global__ void atomic_sum(const float* input, float* output, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) atomicAdd(output, input[index]);
}

__device__ float warp_sum(float value) {
  for (int offset = 16; offset > 0; offset >>= 1) {
    value += __shfl_down_sync(0xffffffffU, value, offset);
  }
  return value;
}

__global__ void block_atomic_sum(const float* input, float* output, std::size_t count) {
  __shared__ float warp_partials[32];
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  const int lane = threadIdx.x & 31;
  const int warp = threadIdx.x >> 5;
  float value = index < count ? input[index] : 0.0F;
  value = warp_sum(value);
  if (lane == 0) warp_partials[warp] = value;
  __syncthreads();
  if (warp == 0) {
    value = lane < (blockDim.x + 31) / 32 ? warp_partials[lane] : 0.0F;
    value = warp_sum(value);
    if (lane == 0) atomicAdd(output, value);
  }
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 04_reduction [--smoke]\n"; return 0; }
  try {
    const std::size_t count = problem_size(argc, argv, 1003, 1U << 24);
    require_h100();
    std::vector<float> input(count, 1.0F);
    DeviceBuffer<float> device_input(count), atomic_output(1), block_output(1), cub_output(1);
    CUDA_CHECK(cudaMemcpy(device_input.get(), input.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemset(atomic_output.get(), 0, sizeof(float)));
    constexpr int threads = 256;
    const int blocks = static_cast<int>((count + threads - 1) / threads);
    const auto atomic_timing = benchmark_cuda_with_setup(
        [&] { CUDA_CHECK(cudaMemset(atomic_output.get(), 0, sizeof(float))); },
        [&] { atomic_sum<<<blocks, threads>>>(device_input.get(), atomic_output.get(), count); });
    const auto block_timing = benchmark_cuda_with_setup(
        [&] { CUDA_CHECK(cudaMemset(block_output.get(), 0, sizeof(float))); },
        [&] { block_atomic_sum<<<blocks, threads>>>(device_input.get(), block_output.get(), count); });
    std::size_t temporary_bytes = 0;
    cub::DeviceReduce::Sum(nullptr, temporary_bytes, device_input.get(), cub_output.get(), count);
    DeviceBuffer<unsigned char> temporary(temporary_bytes);
    const auto cub_timing = benchmark_cuda([&] {
      CUDA_CHECK(cub::DeviceReduce::Sum(temporary.get(), temporary_bytes, device_input.get(), cub_output.get(), count));
    });
    float atomic_value = 0.0F;
    float block_value = 0.0F;
    float cub_value = 0.0F;
    CUDA_CHECK(cudaMemcpy(&atomic_value, atomic_output.get(), sizeof(float), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaMemcpy(&block_value, block_output.get(), sizeof(float), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaMemcpy(&cub_value, cub_output.get(), sizeof(float), cudaMemcpyDeviceToHost));
    check_close(
        {static_cast<float>(count), static_cast<float>(count), static_cast<float>(count)},
        {atomic_value, block_value, cub_value});
    print_result("04_reduction", cub_timing, count);
    print_timing("per_element_atomic", atomic_timing);
    print_timing("warp_block_atomic", block_timing);
    print_timing("cub", cub_timing);
    std::cout << "per_element_atomic_operations=" << count << "\nblock_atomic_operations=" << blocks << "\ncub_temporary_bytes=" << temporary_bytes << '\n';
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
