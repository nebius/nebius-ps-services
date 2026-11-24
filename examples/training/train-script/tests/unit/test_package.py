from importlib import metadata


def test_distribution():
    assert metadata.version("nebius-train-script")
