"""Compare flat and topology-aware hierarchical context communication."""

from bridge_experiments import run

if __name__ == "__main__":
    # The shared implementation owns inputs, measurement and correctness checks.
    run("context")
