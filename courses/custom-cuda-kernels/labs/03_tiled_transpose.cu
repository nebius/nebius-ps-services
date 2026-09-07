#include "common.cuh"

constexpr int tile_width = 32;

__global__ void naive_transpose(const float* input, float* output, int rows, int columns) {
  const int column = blockIdx.x * blockDim.x + threadIdx.x;
  const int row = blockIdx.y * blockDim.y + threadIdx.y;
  if (row < rows && column < columns) output[column * rows + row] = input[row * columns + column];
}

__global__ void tiled_transpose(const float* input, float* output, int rows, int columns) {
  __shared__ float tile[tile_width][tile_width + 1];
  const int input_column = blockIdx.x * tile_width + threadIdx.x;
  const int input_row = blockIdx.y * tile_width + threadIdx.y;
  if (input_row < rows && input_column < columns) tile[threadIdx.y][threadIdx.x] = input[input_row * columns + input_column];
  __syncthreads();
  const int output_column = blockIdx.y * tile_width + threadIdx.x;
  const int output_row = blockIdx.x * tile_width + threadIdx.y;
  if (output_row < columns && output_column < rows) output[output_row * rows + output_column] = tile[threadIdx.x][threadIdx.y];
}

__global__ void tiled_unpadded_transpose(const float* input, float* output, int rows, int columns) {
  __shared__ float tile[tile_width][tile_width];
  const int input_column = blockIdx.x * tile_width + threadIdx.x;
  const int input_row = blockIdx.y * tile_width + threadIdx.y;
  if (input_row < rows && input_column < columns) tile[threadIdx.y][threadIdx.x] = input[input_row * columns + input_column];
  __syncthreads();
  const int output_column = blockIdx.y * tile_width + threadIdx.x;
  const int output_row = blockIdx.x * tile_width + threadIdx.y;
  if (output_row < columns && output_column < rows) output[output_row * rows + output_column] = tile[threadIdx.x][threadIdx.y];
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 03_tiled_transpose [--smoke]\n"; return 0; }
  try {
    const int rows = static_cast<int>(problem_size(argc, argv, 1003, 8192));
    require_h100();
    const int columns = rows + 17;
    const std::size_t count = static_cast<std::size_t>(rows) * columns;
    std::vector<float> input(count), expected(count), naive_observed(count), unpadded_observed(count), padded_observed(count);
    for (std::size_t index = 0; index < count; ++index) input[index] = static_cast<float>(index % 251);
    for (int row = 0; row < rows; ++row) for (int column = 0; column < columns; ++column) expected[column * rows + row] = input[row * columns + column];
    DeviceBuffer<float> device_input(count), naive_output(count), unpadded_output(count), padded_output(count);
    CUDA_CHECK(cudaMemcpy(device_input.get(), input.data(), count * sizeof(float), cudaMemcpyHostToDevice));
    const dim3 threads(tile_width, tile_width);
    const dim3 blocks((columns + tile_width - 1) / tile_width, (rows + tile_width - 1) / tile_width);
    const auto naive_timing = benchmark_cuda([&] {
      naive_transpose<<<blocks, threads>>>(device_input.get(), naive_output.get(), rows, columns);
    });
    const auto unpadded_timing = benchmark_cuda([&] {
      tiled_unpadded_transpose<<<blocks, threads>>>(device_input.get(), unpadded_output.get(), rows, columns);
    });
    const auto padded_timing = benchmark_cuda([&] {
      tiled_transpose<<<blocks, threads>>>(device_input.get(), padded_output.get(), rows, columns);
    });
    CUDA_CHECK(cudaMemcpy(naive_observed.data(), naive_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaMemcpy(unpadded_observed.data(), unpadded_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaMemcpy(padded_observed.data(), padded_output.get(), count * sizeof(float), cudaMemcpyDeviceToHost));
    check_close(expected, naive_observed);
    check_close(expected, unpadded_observed);
    check_close(expected, padded_observed);
    print_result("03_tiled_transpose", padded_timing, count);
    print_timing("naive", naive_timing);
    print_timing("tiled_unpadded", unpadded_timing);
    print_timing("tiled_padded", padded_timing);
    std::cout << "naive_correctness=passed\nunpadded_correctness=passed\npadded_correctness=passed\nunpadded_shared_columns=32\npadded_shared_columns=33\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
