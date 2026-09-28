#pragma once

#include <cstddef>
#include <stdexcept>
#include <string>

// Validate before creating a CUDA context. Omitted profile keeps the large default.
inline bool wants_help(int argc, char** argv) {
  return argc == 2 && (std::string(argv[1]) == "--help" || std::string(argv[1]) == "-h");
}

inline bool parse_small_profile(int argc, char** argv, int& index, bool& seen) {
  if (seen || index + 1 >= argc) throw std::runtime_error("duplicate or incomplete --profile");
  const std::string value(argv[++index]);
  if (value != "small" && value != "large") throw std::runtime_error("--profile must be small or large");
  seen = true;
  return value == "small";
}

inline bool small_profile(int argc, char** argv) {
  if (argc == 1) return false;
  if (argc != 3 || std::string(argv[1]) != "--profile")
    throw std::runtime_error("expected --profile small|large; use --help for usage");
  int index = 1;
  bool seen = false;
  return parse_small_profile(argc, argv, index, seen);
}

inline void validate_simple_arguments(int argc, char** argv) {
  (void)small_profile(argc, argv);
}

inline std::size_t problem_size(int argc, char** argv, std::size_t small, std::size_t large) {
  return small_profile(argc, argv) ? small : large;
}
