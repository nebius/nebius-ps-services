"""Compare two TP8 replicas with TP8 prefill and TP8 decode pools."""

from dynamo_experiments import run

if __name__ == "__main__":
    # The shared implementation owns inputs, measurement and correctness checks.
    run("disaggregation")
