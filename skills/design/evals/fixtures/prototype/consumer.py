"""Synthetic consumer exposing comma-containing identifier corruption."""

from service import encode_ids


def export_ids(identifiers):
    return encode_ids(identifiers).split(",")
