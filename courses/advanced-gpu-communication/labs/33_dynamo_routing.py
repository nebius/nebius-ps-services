"""Measure KV-aware routing on repeated-prefix requests with two TP8 replicas."""

from dynamo_experiments import run

if __name__ == "__main__":
    run("routing")
