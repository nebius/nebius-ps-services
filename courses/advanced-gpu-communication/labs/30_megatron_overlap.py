"""Measure exposed gradient exchange in a real Megatron Bridge training loop."""

from bridge_experiments import run

if __name__ == "__main__":
    # The shared implementation owns inputs, measurement and correctness checks.
    run("overlap")
