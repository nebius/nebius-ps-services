from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from io import BytesIO

import pytest

from github_report.models import LocTarget, RepositoryRef
from github_report.services.github_client import GitHubRepositoryNotFoundError
from github_report.services.loc import build_loc_report_from_zip, count_text_lines, detect_language
from github_report.services.reporting import GitHubReportService
from github_report.settings import LocOptions


def build_zip(entries: dict[str, str | bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        for path, content in entries.items():
            payload = content if isinstance(content, bytes) else content.encode("utf-8")
            archive.writestr(f"repo-root/{path}", payload)
    return buffer.getvalue()


def test_build_loc_report_counts_only_requested_subfolder() -> None:
    archive_bytes = build_zip(
        {
            "services/nebius-cxcli/src/app.py": "# comment\n\nprint('hello')\n",
            "services/nebius-cxcli/config.yaml": "enabled: true\n# comment\n",
            "services/vpngw/src/app.py": "print('skip')\n",
            "services/nebius-cxcli/README.md": "# skipped docs\n",
        }
    )

    report = build_loc_report_from_zip(
        archive_bytes,
        target=LocTarget("nebius", "nebius-ps-services", "services/nebius-cxcli"),
        ref="main",
        generated_at=datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
    )

    assert report.metadata.files_counted == 2
    assert report.metadata.files_skipped == 1
    assert [(row.language, row.file_count, row.code_lines) for row in report.language_rows] == [
        ("Python", 1, 1),
        ("YAML", 1, 1),
    ]


def test_build_loc_report_rejects_missing_scope() -> None:
    archive_bytes = build_zip({"src/app.py": "print('hello')\n"})

    with pytest.raises(ValueError, match="was not found"):
        build_loc_report_from_zip(
            archive_bytes,
            target=LocTarget("nebius", "nebius-ps-services", "services/missing"),
            ref="main",
            generated_at=datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC),
        )


def test_count_text_lines_separates_blank_comment_and_code_lines() -> None:
    python = detect_language("src/app.py")
    assert python is not None

    counts = count_text_lines(
        '"""module doc"""\n\n# comment\nvalue = 1  # inline comment\n',
        python,
    )

    assert counts.code_lines == 1
    assert counts.comment_lines == 2
    assert counts.blank_lines == 1
    assert counts.total_lines == 4


class _FakeArchiveClient:
    def __init__(self, token: str, *, timeout_seconds: float) -> None:
        self.token = token
        self.timeout_seconds = timeout_seconds

    def get_repository(self, owner_name: str, repo_name: str) -> RepositoryRef:
        if (owner_name, repo_name) == ("nebius-ps-services", "services"):
            raise GitHubRepositoryNotFoundError("not found")
        if (owner_name, repo_name) == ("nebius", "nebius-ps-services"):
            return RepositoryRef(
                "nebius-ps-services",
                "nebius/nebius-ps-services",
                "main",
                False,
            )
        raise AssertionError((owner_name, repo_name))

    def find_repository_by_name(self, repo_name: str) -> RepositoryRef:
        assert repo_name == "nebius-ps-services"
        return RepositoryRef(
            "nebius-ps-services",
            "nebius/nebius-ps-services",
            "main",
            False,
        )

    def download_repository_zipball(self, repository: RepositoryRef, *, ref: str) -> bytes:
        assert repository.full_name == "nebius/nebius-ps-services"
        assert ref == "main"
        return build_zip({"services/nebius-cxcli/src/app.py": "print('hello')\n"})


def test_build_loc_report_resolves_bare_repo_path_target(monkeypatch) -> None:
    monkeypatch.setattr("github_report.services.reporting.resolve_github_token", lambda: "token")
    service = GitHubReportService(archive_client_cls=_FakeArchiveClient)

    report = service.build_loc_report(LocOptions(target="nebius-ps-services/services/nebius-cxcli"))

    assert report.metadata.owner == "nebius"
    assert report.metadata.repo == "nebius-ps-services"
    assert report.metadata.path == "services/nebius-cxcli"
    assert report.metadata.files_counted == 1
