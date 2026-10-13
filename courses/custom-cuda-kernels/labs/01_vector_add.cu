#include "common.cuh"

__global__ void vector_add(const float* left, const float* right, float* output, std::size_t count) {
  const std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) output[index] = left[index] + right[index];
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 01_vector_add [--workload small|large] [--threads 128|256|512]\n"; return 0; }
  try {
    bool small = false, profile_seen = false, threads_seen = false;
    int threads = 256;
    // Read workload and kernel controls before allocating buffers.
    for (int index = 1; index < argc; ++index) {
      const std::string argument(argv[index]);
      if (argument == "--workload") small = parse_small_profile(argc, argv, index, profile_seen);
      else if (argument == "--threads" && !threads_seen && index + 1 < argc) {
        const std::string value(argv[++index]);
        if (value != "128" && value != "256" && value != "512")
          throw std::runtime_error("--threads must be 128, 256, or 512");
        threads = std::stoi(value); threads_seen = true;
      } else throw std::runtime_error("unknown, duplicate, or incomplete argument: " + argument);
    }
    const std::size_t count = small ? 1003 : (1U << 24);
    require_course_gpu();
    // Allocate host inputs and reference/output storage.
    std::vector<float> left(count), right(count), expected(count), observed(count);
    for (std::size_t index = 0; index < count; ++index) {
      left[index] = static_cast<float>(index % 97) / 97.0F;
      right[index] = static_cast<float>(index % 53) / 53.0F;
      expected[index] = left[index] + right[index];
    }
    // Allocate device buffers; their owners release them when this scope ends.
    DeviceBuffer<float> device_left(count), device_right(count), device_output(count);
    // Copy initialized inputs to the GPU before measuring kernels.
    CUDA_CHECK(cudaMemcpy(device_left.get(), left.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(device_right.get(), right.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    const int blocks = static_cast<int>((count + threads - 1) / threads);
    // Warm up, then time GPU work with the shared synchronized CUDA-event helper.
    const auto timing = benchmark_cuda([&] {
      vector_add<<<blocks, threads>>>(device_left.get(), device_right.get(), device_output.get(), count);
    });
    // Copy completed device output back for validation outside kernel timing.
    CUDA_CHECK(cudaMemcpy(observed.data(), device_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    // Check the GPU output against the host reference before accepting timing.
    check_close(expected, observed);
    // Emit the measured summary for the job’s CUDA result adapter.
    print_result("01_vector_add", timing, count);
    std::cout << "blocks=" << blocks << "\nthreads_per_block=" << threads << '\n';
    std::cout << "course_checks=passed\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
