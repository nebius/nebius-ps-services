#include "common.cuh"

__global__ void divergent_work(const float* input, float* output, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index >= count) return;
  float value = input[index];
  if ((index & 1U) == 0U) {
    for (int iteration = 0; iteration < 32; ++iteration) value = fmaf(value, 1.00001F, 0.00001F);
  } else {
    for (int iteration = 0; iteration < 4; ++iteration) value = fmaf(value, 1.00001F, 0.00001F);
  }
  output[index] = value;
}

__global__ void pack_grouped(const float* input, float* grouped_input, std::size_t count,
                             std::size_t long_count) {
  const std::size_t grouped_index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (grouped_index >= count) return;
  const std::size_t logical_index = grouped_index < long_count
                                        ? grouped_index * 2
                                        : (grouped_index - long_count) * 2 + 1;
  grouped_input[grouped_index] = input[logical_index];
}

__global__ void grouped_work(const float* input, float* output, std::size_t count,
                             std::size_t long_count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index >= count) return;
  const bool long_path = index < long_count;
  float value = input[index];
  const int iterations = long_path ? 32 : 4;
  for (int iteration = 0; iteration < iterations; ++iteration) value = fmaf(value, 1.00001F, 0.00001F);
  output[index] = value;
}

__global__ void scatter_grouped(const float* grouped_output, float* logical_output,
                                std::size_t count, std::size_t long_count) {
  const std::size_t grouped_index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (grouped_index >= count) return;
  const std::size_t logical_index = grouped_index < long_count
                                        ? grouped_index * 2
                                        : (grouped_index - long_count) * 2 + 1;
  logical_output[logical_index] = grouped_output[grouped_index];
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 06_divergence_tail [--smoke]\n"; return 0; }
  try {
    const std::size_t count = problem_size(argc, argv, 4096, 1U << 25);
    const auto properties = require_h100();
    std::vector<float> input(count), expected(count), divergent_observed(count), grouped_observed(count);
    auto apply_work = [](float value, int iterations) {
      for (int iteration = 0; iteration < iterations; ++iteration) value = std::fma(value, 1.00001F, 0.00001F);
      return value;
    };
    for (std::size_t index = 0; index < count; ++index) {
      input[index] = 0.25F + static_cast<float>(index % 31) * 0.01F;
      expected[index] = apply_work(input[index], (index & 1U) == 0U ? 32 : 4);
    }
    DeviceBuffer<float> device_input(count), divergent_output(count), grouped_input(count),
        grouped_output(count), grouped_scattered_output(count);
    CUDA_CHECK(cudaMemcpy(device_input.get(), input.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    constexpr int threads = 256;
    const int blocks = static_cast<int>((count + threads - 1) / threads);
    const std::size_t long_count = (count + 1) / 2;
    const auto divergent_timing = benchmark_cuda([&] {
      divergent_work<<<blocks, threads>>>(device_input.get(), divergent_output.get(), count);
    });
    pack_grouped<<<blocks, threads>>>(device_input.get(), grouped_input.get(), count, long_count);
    CUDA_CHECK(cudaDeviceSynchronize());
    const auto grouped_core_timing = benchmark_cuda([&] {
      grouped_work<<<blocks, threads>>>(grouped_input.get(), grouped_output.get(), count, long_count);
    });
    const auto grouping_overhead_timing = benchmark_cuda([&] {
      pack_grouped<<<blocks, threads>>>(device_input.get(), grouped_input.get(), count, long_count);
      scatter_grouped<<<blocks, threads>>>(grouped_output.get(), grouped_scattered_output.get(), count, long_count);
    });
    const auto grouped_end_to_end_timing = benchmark_cuda([&] {
      pack_grouped<<<blocks, threads>>>(device_input.get(), grouped_input.get(), count, long_count);
      grouped_work<<<blocks, threads>>>(grouped_input.get(), grouped_output.get(), count, long_count);
      scatter_grouped<<<blocks, threads>>>(grouped_output.get(), grouped_scattered_output.get(), count, long_count);
    });
    CUDA_CHECK(cudaMemcpy(divergent_observed.data(), divergent_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaMemcpy(grouped_observed.data(), grouped_scattered_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    check_close(expected, divergent_observed);
    check_close(expected, grouped_observed);
    check_close(divergent_observed, grouped_observed);
    const int waves = (blocks + properties.multiProcessorCount - 1) / properties.multiProcessorCount;
    const int tail_blocks = blocks - (waves - 1) * properties.multiProcessorCount;
    print_result("06_divergence_tail", grouped_end_to_end_timing, count);
    print_timing("divergent", divergent_timing);
    print_timing("grouped_core_only", grouped_core_timing);
    print_timing("pack_and_scatter_overhead", grouping_overhead_timing);
    print_timing("grouped_end_to_end", grouped_end_to_end_timing);
    std::cout << "long_path_items=" << long_count
              << "\nshort_path_items=" << count - long_count
              << "\ngrouping_preserves_logical_work=true"
              << "\ndivergent_correctness=passed\ngrouped_correctness=passed"
              << "\noutputs_match_each_other=passed\nblocks=" << blocks
              << "\nmodeled_waves_one_block_per_sm=" << waves
              << "\ntail_blocks_one_block_per_sm=" << tail_blocks
              << "\nprimary_grouped_timing_includes_pack_and_scatter=true"
              << "\nverify_residency_with_profiler=true\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
