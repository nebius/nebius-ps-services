#include "common.cuh"

#include <cublas_v2.h>

#ifdef COURSE_ENABLE_CUTLASS
#include <cutlass/cutlass.h>
#include <cutlass/epilogue/thread/linear_combination_relu.h>
#include <cutlass/gemm/device/gemm.h>
#include <cutlass/gemm/threadblock/threadblock_swizzle.h>
#include <cutlass/layout/matrix.h>
#include <cutlass/version.h>
#endif

#define CUBLAS_CHECK(call) do { if ((call) != CUBLAS_STATUS_SUCCESS) throw std::runtime_error(std::string(#call) + ": cuBLAS failure"); } while (false)

#ifdef COURSE_ENABLE_CUTLASS
#define CUTLASS_CHECK(call) do { if ((call) != cutlass::Status::kSuccess) throw std::runtime_error(std::string(#call) + ": CUTLASS failure"); } while (false)
static_assert(CUTLASS_MAJOR == 4 && CUTLASS_MINOR == 6 && CUTLASS_PATCH == 1,
              "Lab 09 is source-pinned to CUTLASS 4.6.1; review before changing it");

using CutlassEpilogue = cutlass::epilogue::thread::LinearCombinationRelu<
    float, 1, float, float>;
using CutlassGemm = cutlass::gemm::device::Gemm<
    float, cutlass::layout::RowMajor,
    float, cutlass::layout::RowMajor,
    float, cutlass::layout::RowMajor,
    float, cutlass::arch::OpClassSimt, cutlass::arch::Sm80,
    cutlass::gemm::GemmShape<128, 128, 8>,
    cutlass::gemm::GemmShape<32, 64, 8>,
    cutlass::gemm::GemmShape<1, 1, 1>,
    CutlassEpilogue,
    cutlass::gemm::threadblock::GemmIdentityThreadblockSwizzle<>, 2>;
#endif

__global__ void bias_relu(float* matrix, const float* bias, int rows, int columns) {
  const int index = blockIdx.x * blockDim.x + threadIdx.x;
  if (index < rows * columns) matrix[index] = fmaxf(matrix[index] + bias[index % columns], 0.0F);
}

int main(int argc, char** argv) {
  if (wants_help(argc, argv)) { std::cout << "Usage: 09_library_epilogue [--smoke]\n"; return 0; }
  try {
    validate_simple_arguments(argc, argv);
    require_h100();
    const bool smoke = argc > 1 && std::string(argv[1]) == "--smoke";
    const int rows = smoke ? 128 : 1024;
    const int columns = smoke ? 192 : 1536;
    const int depth = smoke ? 96 : 768;
    const std::size_t left_elements = static_cast<std::size_t>(rows) * depth;
    const std::size_t right_elements = static_cast<std::size_t>(depth) * columns;
    const std::size_t output_elements = static_cast<std::size_t>(rows) * columns;
    std::vector<float> left(left_elements), right(right_elements), bias(columns),
        bias_matrix(output_elements), expected(output_elements), cublas_observed(output_elements);
    for (int row = 0; row < rows; ++row) {
      for (int inner = 0; inner < depth; ++inner) {
        left[static_cast<std::size_t>(row) * depth + inner] =
            static_cast<float>((row * 3 + inner * 5) % 17 - 8) * 0.01F;
      }
    }
    for (int inner = 0; inner < depth; ++inner) {
      for (int column = 0; column < columns; ++column) {
        right[static_cast<std::size_t>(inner) * columns + column] =
            static_cast<float>((inner * 7 + column * 11) % 19 - 9) * 0.015F;
      }
    }
    for (int column = 0; column < columns; ++column) {
      bias[column] = static_cast<float>(column % 7 - 3) * 0.05F;
    }
    std::size_t positive_elements = 0;
    std::size_t clamped_elements = 0;
    for (int row = 0; row < rows; ++row) {
      for (int column = 0; column < columns; ++column) {
        float value = bias[column];
        for (int inner = 0; inner < depth; ++inner) {
          value += left[static_cast<std::size_t>(row) * depth + inner] *
                   right[static_cast<std::size_t>(inner) * columns + column];
        }
        const std::size_t index = static_cast<std::size_t>(row) * columns + column;
        bias_matrix[index] = bias[column];
        expected[index] = std::max(value, 0.0F);
        if (expected[index] > 0.0F) ++positive_elements;
        else ++clamped_elements;
      }
    }
    if (positive_elements == 0 || clamped_elements == 0) {
      throw std::runtime_error("reference data must exercise both ReLU branches");
    }
    DeviceBuffer<float> device_left(left_elements), device_right(right_elements),
        device_output(output_elements), device_bias(columns);
    CUDA_CHECK(cudaMemcpy(device_left.get(), left.data(), left_elements * sizeof(float), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(device_right.get(), right.data(), right_elements * sizeof(float), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(device_bias.get(), bias.data(), columns * sizeof(float), cudaMemcpyHostToDevice));
    cublasHandle_t handle{};
    CUBLAS_CHECK(cublasCreate(&handle));
    CUBLAS_CHECK(cublasSetMathMode(handle, CUBLAS_PEDANTIC_MATH));
    const float alpha = 1.0F;
    const float beta = 0.0F;
    const int threads = 256;
    const auto timing = benchmark_cuda([&] {
      // Row-major C=A*B is column-major C^T=B^T*A^T for the cuBLAS call.
      CUBLAS_CHECK(cublasSgemm(handle, CUBLAS_OP_N, CUBLAS_OP_N,
                              columns, rows, depth, &alpha,
                              device_right.get(), columns,
                              device_left.get(), depth, &beta,
                              device_output.get(), columns));
      bias_relu<<<(output_elements + threads - 1) / threads, threads>>>(
          device_output.get(), device_bias.get(), rows, columns);
    });
    CUDA_CHECK(cudaMemcpy(cublas_observed.data(), device_output.get(), output_elements * sizeof(float), cudaMemcpyDeviceToHost));
    check_close(expected, cublas_observed);
    CUBLAS_CHECK(cublasDestroy(handle));
    print_result("09_library_epilogue", timing, output_elements);
    print_timing("cublas_plus_separate_bias_relu", timing);
    std::cout << "rows=" << rows << "\ncolumns=" << columns << "\ndepth=" << depth
              << "\nrelu_positive_elements=" << positive_elements
              << "\nrelu_clamped_elements=" << clamped_elements
              << "\ncublas_baseline_correctness=passed\n";
#ifdef COURSE_ENABLE_CUTLASS
    DeviceBuffer<float> device_cutlass_source(output_elements), device_cutlass_output(output_elements);
    CUDA_CHECK(cudaMemcpy(device_cutlass_source.get(), bias_matrix.data(), output_elements * sizeof(float), cudaMemcpyHostToDevice));
    CutlassGemm::Arguments arguments(
        {rows, columns, depth},
        {device_left.get(), depth},
        {device_right.get(), columns},
        {device_cutlass_source.get(), columns},
        {device_cutlass_output.get(), columns},
        {1.0F, 1.0F});
    CUTLASS_CHECK(CutlassGemm::can_implement(arguments));
    CutlassGemm cutlass_gemm;
    CUTLASS_CHECK(cutlass_gemm.initialize(arguments));
    const auto cutlass_timing = benchmark_cuda([&] { CUTLASS_CHECK(cutlass_gemm()); });
    std::vector<float> cutlass_observed(output_elements);
    CUDA_CHECK(cudaMemcpy(cutlass_observed.data(), device_cutlass_output.get(), output_elements * sizeof(float), cudaMemcpyDeviceToHost));
    check_close(expected, cutlass_observed);
    print_timing("cutlass_fused_bias_relu", cutlass_timing);
    std::cout << "cutlass_version=4.6.1\ncutlass_fused_correctness=passed\ncomparison_scope=pedagogical-fp32-simt-epilogue\n";
#else
    std::cerr << "ERROR: required CUTLASS fused epilogue was not built; configure "
                 "with COURSE_ENABLE_CUTLASS=ON and the reviewed CUTLASS_ROOT\n";
    return 3;
#endif
    return 0;
  } catch (const std::exception& error) { std::cerr << "ERROR: " << error.what() << '\n'; return 2; }
}
