#pragma once

#include "arguments.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#define CUDA_CHECK(call)                                                                  \
  do {                                                                                    \
    cudaError_t status_ = (call);                                                         \
    if (status_ != cudaSuccess) {                                                         \
      throw std::runtime_error(std::string(#call) + ": " + cudaGetErrorString(status_)); \
    }                                                                                     \
  } while (false)

inline cudaDeviceProp require_h100() {
  int count = 0;
  CUDA_CHECK(cudaGetDeviceCount(&count));
  if (count != 1) throw std::runtime_error("expected exactly one allocated GPU");
  cudaDeviceProp properties{};
  CUDA_CHECK(cudaGetDeviceProperties(&properties, 0));
  const std::string name(properties.name);
  if (properties.major != 9 || properties.minor != 0 || name.find("H100") == std::string::npos || name.find("MIG") != std::string::npos) {
    throw std::runtime_error("expected one full NVIDIA H100 with compute capability 9.0");
  }
  return properties;
}

class EventTimer {
 public:
  EventTimer() { CUDA_CHECK(cudaEventCreate(&start_)); CUDA_CHECK(cudaEventCreate(&stop_)); }
  ~EventTimer() { cudaEventDestroy(start_); cudaEventDestroy(stop_); }
  void start() { CUDA_CHECK(cudaEventRecord(start_)); }
  float stop() {
    CUDA_CHECK(cudaEventRecord(stop_));
    CUDA_CHECK(cudaEventSynchronize(stop_));
    float milliseconds = 0.0F;
    CUDA_CHECK(cudaEventElapsedTime(&milliseconds, start_, stop_));
    return milliseconds;
  }
 private:
  cudaEvent_t start_{};
  cudaEvent_t stop_{};
};

struct TimingSummary {
  std::size_t samples{};
  float minimum_ms{};
  float median_ms{};
  float p90_ms{};
};

inline TimingSummary summarize_samples(std::vector<float> samples) {
  if (samples.empty()) throw std::runtime_error("timing sample set is empty");
  std::sort(samples.begin(), samples.end());
  const std::size_t middle = samples.size() / 2;
  const float median = samples.size() % 2 == 0
                           ? (samples[middle - 1] + samples[middle]) / 2.0F
                           : samples[middle];
  const std::size_t p90_index = (9 * samples.size() + 9) / 10 - 1;
  return {samples.size(), samples.front(), median, samples[p90_index]};
}

template <typename Launch>
TimingSummary benchmark_cuda(Launch&& launch, int warmups = 5, int iterations = 20) {
  for (int index = 0; index < warmups; ++index) {
    launch();
    CUDA_CHECK(cudaGetLastError());
  }
  CUDA_CHECK(cudaDeviceSynchronize());
  EventTimer timer;
  std::vector<float> samples;
  samples.reserve(iterations);
  for (int index = 0; index < iterations; ++index) {
    timer.start();
    launch();
    CUDA_CHECK(cudaGetLastError());
    samples.push_back(timer.stop());
  }
  return summarize_samples(std::move(samples));
}

template <typename Prepare, typename Launch>
TimingSummary benchmark_cuda_with_setup(Prepare&& prepare, Launch&& launch, int warmups = 5, int iterations = 20) {
  for (int index = 0; index < warmups; ++index) {
    prepare();
    launch();
    CUDA_CHECK(cudaGetLastError());
  }
  CUDA_CHECK(cudaDeviceSynchronize());
  EventTimer timer;
  std::vector<float> samples;
  samples.reserve(iterations);
  for (int index = 0; index < iterations; ++index) {
    prepare();
    timer.start();
    launch();
    CUDA_CHECK(cudaGetLastError());
    samples.push_back(timer.stop());
  }
  return summarize_samples(std::move(samples));
}

inline void print_timing(const std::string& label, const TimingSummary& timing) {
  std::cout << label << "_samples=" << timing.samples << '\n'
            << label << "_min_ms=" << timing.minimum_ms << '\n'
            << label << "_median_ms=" << timing.median_ms << '\n'
            << label << "_p90_ms=" << timing.p90_ms << '\n';
}

inline void check_close(const std::vector<float>& expected, const std::vector<float>& observed, float rtol = 1e-5F, float atol = 1e-6F) {
  if (expected.size() != observed.size()) throw std::runtime_error("reference and candidate sizes differ");
  float maximum_error = 0.0F;
  for (std::size_t index = 0; index < expected.size(); ++index) {
    if (!std::isfinite(expected[index]) || !std::isfinite(observed[index])) {
      throw std::runtime_error("numerical comparison received a non-finite value");
    }
    const float error = std::abs(expected[index] - observed[index]);
    if (!std::isfinite(error)) throw std::runtime_error("numerical comparison error is non-finite");
    maximum_error = std::max(maximum_error, error);
    if (error > atol + rtol * std::abs(expected[index])) throw std::runtime_error("numerical comparison failed");
  }
  std::cout << "maximum_absolute_error=" << maximum_error << '\n';
}

template <typename T>
class DeviceBuffer {
 public:
  explicit DeviceBuffer(std::size_t count) : count_(count) { CUDA_CHECK(cudaMalloc(reinterpret_cast<void**>(&pointer_), count * sizeof(T))); }
  ~DeviceBuffer() { cudaFree(pointer_); }
  DeviceBuffer(const DeviceBuffer&) = delete;
  DeviceBuffer& operator=(const DeviceBuffer&) = delete;
  T* get() { return pointer_; }
  const T* get() const { return pointer_; }
  std::size_t size() const { return count_; }
 private:
  T* pointer_{};
  std::size_t count_{};
};

inline void print_result(const std::string& lab, const TimingSummary& timing, std::size_t elements) {
  std::cout << "schema=gpu-course-result/v1\nlab_id=" << lab << "\nelements=" << elements << "\nwarmup_samples=5\n";
  print_timing("kernel", timing);
}
