# Catalog Fixture

Synthetic local application with a fixed Python standard-library stack.
`docs/design.md` is this project's design document. The application has existing
consumers; `consumer.export_ids` must keep working. No external services or
credentials are required. Run `python3 -B -m unittest discover -s .` here.
