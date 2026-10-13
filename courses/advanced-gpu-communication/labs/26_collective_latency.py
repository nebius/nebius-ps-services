"""Separate small-message collective latency from large-message payload rate."""

from network_experiments import run

if __name__ == "__main__":
    # The shared implementation owns inputs, measurement and correctness checks.
    run("latency")
