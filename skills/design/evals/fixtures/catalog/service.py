"""Synthetic catalog service for design evaluations."""


def list_items(identifiers):
    return {"items": [{"id": value} for value in identifiers], "next_cursor": None}
