"""Project-owned rendering: implement with the project's established renderer.

Register courses explicitly here. Never glob arbitrary inputs into publication.
Return full rendered pages and any companion archives from plan_outputs().
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_ROOT = PROJECT_ROOT
INVENTORY = "directory"  # Dedicated publication tree; use 'git' for a repository root.
MAX_FILE_BYTES = None  # GitHub regular Git files: 104_857_600.
MAX_SITE_BYTES = None  # GitHub Pages conservative cap: 1_000_000_000.


def plan_outputs(project_root: Path) -> dict[str, bytes]:
    """Keys are PUBLICATION_ROOT-relative paths, values complete output bytes.

    Use the copied tools/assets shell and CSS. Render all canonical prose and
    selected source listings, validate local navigation, and reject unsupported
    Markdown or unresolved authoring placeholders. For results downloads call
    publication.results_zip with an explicit member-to-source path inventory.
    """
    raise ValueError(
        "Configure course_adapter.plan_outputs with the project renderer before building"
    )
