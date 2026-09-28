# Prototype Fixture

Synthetic application with a fixed Python standard-library stack. The name
does not establish whether anyone uses it; the evaluation supplies usage
context when known. Identifiers may contain commas. No external services are
needed. `service.encode_ids` and `consumer.export_ids` currently round-trip
identifiers through an unescaped comma-delimited string.
