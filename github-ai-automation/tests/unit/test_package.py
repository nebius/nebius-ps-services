from importlib import metadata


def test_distribution():
    assert metadata.version("github-ai-automation")
