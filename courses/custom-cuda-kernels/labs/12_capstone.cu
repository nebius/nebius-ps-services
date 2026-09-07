#include "common.cuh"

__global__ void baseline_first(const float* input, float* temporary, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) temporary[index] = input[index] * 1.25F + 0.5F;
}

__global__ void baseline_second(const float* temporary, float* output, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) output[index] = tanhf(temporary[index]);
}

__global__ void fused_candidate(const float* input, float* output, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) output[index] = tanhf(input[index] * 1.25F + 0.5F);
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 12_capstone [--smoke] --variant-order baseline-first|candidate-first\n"; return 0; }
  try {
    bool smoke = false;
    std::string variant_order;
    for (int index = 1; index < argc; ++index) {
      const std::string argument(argv[index]);
      if (argument == "--smoke") smoke = true;
      else if (argument == "--variant-order" && index + 1 < argc) variant_order = argv[++index];
      else throw std::runtime_error("unsupported or incomplete argument: " + argument);
    }
    if (variant_order != "baseline-first" && variant_order != "candidate-first") {
      throw std::runtime_error("--variant-order is required and must be baseline-first or candidate-first");
    }
    require_h100();
    const std::size_t count = smoke ? 4099 : 1U << 25;
    std::vector<float> input(count), expected(count), baseline_observed(count), candidate_observed(count);
    for (std::size_t index = 0; index < count; ++index) { input[index] = static_cast<float>(static_cast<int>(index % 101) - 50) / 50.0F; expected[index] = std::tanh(input[index] * 1.25F + 0.5F); }
    DeviceBuffer<float> device_input(count), temporary(count), baseline_output(count), candidate_output(count);
    CUDA_CHECK(cudaMemcpy(device_input.get(), input.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    constexpr int threads = 256;
    const int blocks = static_cast<int>((count + threads - 1) / threads);
    TimingSummary baseline_timing{};
    TimingSummary candidate_timing{};
    const auto measure_baseline = [&] {
      baseline_timing = benchmark_cuda([&] {
        baseline_first<<<blocks, threads>>>(device_input.get(), temporary.get(), count);
        baseline_second<<<blocks, threads>>>(temporary.get(), baseline_output.get(), count);
      });
    };
    const auto measure_candidate = [&] {
      candidate_timing = benchmark_cuda([&] {
        fused_candidate<<<blocks, threads>>>(device_input.get(), candidate_output.get(), count);
      });
    };
    if (variant_order == "baseline-first") {
      measure_baseline();
      measure_candidate();
    } else {
      measure_candidate();
      measure_baseline();
    }
    CUDA_CHECK(cudaMemcpy(baseline_observed.data(), baseline_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaMemcpy(candidate_observed.data(), candidate_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    check_close(expected, baseline_observed);
    check_close(expected, candidate_observed);
    print_result("12_capstone", candidate_timing, count);
    print_timing("baseline", baseline_timing);
    print_timing("candidate", candidate_timing);
    std::cout << "trial_kind=one-fresh-process\nvariant_order=" << variant_order
              << "\nbaseline_correctness=passed\ncandidate_correctness=passed\n"
              << "sanitizer=run-separately\nprofiler=run-separately\n"
              << "end_to_end_claim=pending-integration\n"
              << "publication_decision=pending-three-independent-run-aggregation\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
