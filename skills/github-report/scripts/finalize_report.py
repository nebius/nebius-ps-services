#!/usr/bin/env python3
"""Private stdin boundary: {report: collected schema-v3 report, assessments: [...]}.

No network, authentication, files, or public skill flags. The caller preserves
collector evidence unchanged and supplies only bounded folder assessments.
"""

import json
import sys

sys.dont_write_bytecode = True

from report_finalization import finalize  # noqa: E402
from report_model import ReportError  # noqa: E402


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ReportError(
                "INPUT_LIMIT", "Finalization input exceeds the bounded input size."
            )
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"report", "assessments"}:
            raise ReportError(
                "INVALID_INPUT", "Expected a collected report and folder assessments."
            )
        if not isinstance(value["assessments"], list):
            raise ReportError("INVALID_INPUT", "Folder assessments must be a list.")
        result = finalize(value["report"], value["assessments"])
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["coverage"] == "complete" else 3
    except (ReportError, ValueError, TypeError, KeyError, AttributeError):
        print(
            json.dumps(
                {
                    "coverage": "unavailable",
                    "error": "INVALID_FINALIZATION",
                    "message": "Cannot safely finalize the supplied evidence and assessments.",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
