"""Executable consumer of the catalog response envelope."""

from service import list_items


def export_ids(identifiers):
    page = list_items(identifiers)
    return [item["id"] for item in page["items"]]
