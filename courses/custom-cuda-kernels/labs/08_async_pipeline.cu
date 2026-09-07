#include "common.cuh"

#include <cooperative_groups.h>
#include <cooperative_groups/memcpy_async.h>
#include <charconv>
#include <limits>

namespace cg = cooperative_groups;

__global__ void serial_tiled_transform(const float* input, float* output, std::size_t count, int work_iterations) {
  extern __shared__ float tile[];
  auto block = cg::this_thread_block();
  const std::size_t tile_size = blockDim.x;
  const std::size_t tiles = (count + tile_size - 1) / tile_size;
  for (std::size_t tile_index = 0; tile_index < tiles; ++tile_index) {
    const std::size_t offset = tile_index * tile_size;
    if (offset + threadIdx.x < count) tile[threadIdx.x] = input[offset + threadIdx.x];
    block.sync();
    if (offset + threadIdx.x < count) {
      float value = tile[threadIdx.x];
      for (int iteration = 0; iteration < work_iterations; ++iteration) value = fmaf(value, 1.0001F, 0.0001F);
      output[offset + threadIdx.x] = value;
    }
    block.sync();
  }
}

__global__ void pipelined_transform(const float* input, float* output, std::size_t count, int work_iterations) {
  extern __shared__ float stages[];
  auto block = cg::this_thread_block();
  const std::size_t tile_size = blockDim.x;
  const std::size_t tiles = (count + tile_size - 1) / tile_size;
  if (tiles == 0) return;
  std::size_t current = 0;
  std::size_t valid = tile_size < count ? tile_size : count;
  cg::memcpy_async(block, stages, input, valid * sizeof(float));
  cg::wait(block);
  for (std::size_t tile = 0; tile < tiles; ++tile) {
    current = tile & 1U;
    const std::size_t next = current ^ 1U;
    const std::size_t offset = tile * tile_size;
    if (tile + 1 < tiles) {
      const std::size_t next_offset = offset + tile_size;
      const std::size_t remaining = count - next_offset;
      const std::size_t next_valid = tile_size < remaining ? tile_size : remaining;
      cg::memcpy_async(block, stages + next * tile_size, input + next_offset, next_valid * sizeof(float));
    }
    if (offset + threadIdx.x < count) {
      float value = stages[current * tile_size + threadIdx.x];
      for (int iteration = 0; iteration < work_iterations; ++iteration) value = fmaf(value, 1.0001F, 0.0001F);
      output[offset + threadIdx.x] = value;
    }
    cg::wait(block);
    block.sync();
  }
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) {
    std::cout << "Usage: 08_async_pipeline [--smoke] [--work-iterations N]\n"
              << "Default: sweep 0,8,32,128 FMAs per element. N: integer 0..1024.\n"
              << "One block; logical intensity excludes shared traffic and loop overhead.\n";
    return 0;
  }
  try {
    bool smoke = false;
    bool work_seen = false;
    std::vector<int> work_sweep{0, 8, 32, 128};
    for (int index = 1; index < argc; ++index) {
      const std::string argument(argv[index]);
      if (argument == "--smoke" && !smoke) { smoke = true; }
      else if (argument == "--work-iterations" && !work_seen && index + 1 < argc) {
        const std::string value(argv[++index]);
        int work = 0;
        const auto parsed = std::from_chars(value.data(), value.data() + value.size(), work);
        if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size() || work < 0 || work > 1024)
          throw std::runtime_error("--work-iterations must be an integer from 0 through 1024");
        work_sweep = {work};
        work_seen = true;
      } else { throw std::runtime_error("unknown, duplicate, or incomplete argument: " + argument); }
    }
    require_h100();
    // Both profiles exercise a partial final tile; keep the serial single-block
    // teaching workload bounded rather than presenting it as an HBM benchmark.
    const std::size_t count = smoke ? 4099 : (1U << 20) + 3;
    std::vector<float> input(count), expected(count), serial_observed(count), pipelined_observed(count);
    for (std::size_t index = 0; index < count; ++index)
      input[index] = 0.25F + static_cast<float>(index % 31) * 0.01F;
    DeviceBuffer<float> device_input(count), serial_output(count), pipelined_output(count);
    CUDA_CHECK(cudaMemcpy(device_input.get(), input.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    constexpr int threads = 256;
    for (const int work_iterations : work_sweep) {
      for (std::size_t index = 0; index < count; ++index) {
        float value = input[index];
        for (int iteration = 0; iteration < work_iterations; ++iteration)
          value = std::fma(value, 1.0001F, 0.0001F);
        expected[index] = value;
      }
      auto serial = [&] {
        serial_tiled_transform<<<1, threads, threads * sizeof(float)>>>(device_input.get(), serial_output.get(), count, work_iterations);
      };
      auto pipelined = [&] {
        pipelined_transform<<<1, threads, 2 * threads * sizeof(float)>>>(device_input.get(), pipelined_output.get(), count, work_iterations);
      };
      // NaN sentinels expose missing writes independently in both variants.
      std::vector<float> sentinel(count, std::numeric_limits<float>::quiet_NaN());
      CUDA_CHECK(cudaMemcpy(serial_output.get(), sentinel.data(), count * sizeof(float), cudaMemcpyHostToDevice));
      CUDA_CHECK(cudaMemcpy(pipelined_output.get(), sentinel.data(), count * sizeof(float), cudaMemcpyHostToDevice));
      serial();
      CUDA_CHECK(cudaGetLastError());
      pipelined();
      CUDA_CHECK(cudaGetLastError());
      CUDA_CHECK(cudaMemcpy(serial_observed.data(), serial_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
      CUDA_CHECK(cudaMemcpy(pipelined_observed.data(), pipelined_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
      check_close(expected, serial_observed);
      check_close(expected, pipelined_observed);
      const auto serial_timing = benchmark_cuda(serial);
      const auto pipelined_timing = benchmark_cuda(pipelined);
      std::cout << "sweep_point_begin\nwork_iterations=" << work_iterations
                << "\nlogical_flops=" << 2ULL * count * work_iterations
                << "\nlogical_global_bytes=" << 2ULL * count * sizeof(float)
                << "\nlogical_flops_per_byte=" << static_cast<double>(work_iterations) / 4.0
                << "\nserial_shared_bytes=" << threads * sizeof(float)
                << "\npipelined_shared_bytes=" << 2 * threads * sizeof(float)
                << "\nsingle_block_mechanism=true\n";
      print_result("08_async_pipeline", pipelined_timing, count);
      print_timing("serial", serial_timing);
      print_timing("pipelined", pipelined_timing);
      std::cout << "both_cpu_references=passed\npipeline_stages=2\nprime_steady_drain=true\nsweep_point_end\n";
    }
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
