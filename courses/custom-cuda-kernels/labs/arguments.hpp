#pragma once

#include <cstddef>
#include <stdexcept>
#include <string>

// Keep option validation independent of CUDA so errors need no device context.
inline bool wants_help(int argc, char** argv) {
  return argc == 2 && (std::string(argv[1]) == "--help" || std::string(argv[1]) == "-h");
}

inline void validate_simple_arguments(int argc, char** argv) {
  if (argc == 1 || (argc == 2 && std::string(argv[1]) == "--smoke")) return;
  throw std::runtime_error("expected no arguments or --smoke; use --help for usage");
}

inline std::size_t problem_size(int argc, char** argv, std::size_t smoke, std::size_t full) {
  validate_simple_arguments(argc, argv);
  return argc == 2 ? smoke : full;
}
