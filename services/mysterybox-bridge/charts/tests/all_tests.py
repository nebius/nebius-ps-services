from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
CHARTS_DIR = ROOT / "services" / "mysterybox-bridge" / "charts"
SNAPSHOTS_DIR = CHARTS_DIR / "tests" / "snapshots"


@dataclass(frozen=True)
class RenderCase:
    chart: str
    output: str
    set_values: tuple[str, ...] = ()
    api_versions: tuple[str, ...] = ()
    namespace: str = "external-secrets"


CASES = (
    RenderCase(
        chart="mysterybox-webhook",
        output="mysterybox-webhook-default.yaml",
    ),
    RenderCase(
        chart="mysterybox-webhook",
        output="mysterybox-webhook-hpa.yaml",
        set_values=("hpa.enabled=true",),
    ),
    RenderCase(
        chart="mysterybox-webhook",
        output="mysterybox-webhook-tls-certmanager.yaml",
        set_values=("tls.enabled=true", "tls.certManager.enabled=true"),
        api_versions=("cert-manager.io/v1/Certificate",),
    ),
    RenderCase(
        chart="mysterybox-webhook",
        output="mysterybox-webhook-servicemonitor.yaml",
        set_values=("monitoring.serviceMonitor.enabled=true",),
        api_versions=("monitoring.coreos.com/v1/ServiceMonitor",),
    ),
    RenderCase(
        chart="jwt-minter",
        output="jwt-minter-enabled.yaml",
        set_values=("enabled=true",),
    ),
)


def render(case: RenderCase) -> str:
    chart_path = CHARTS_DIR / case.chart
    cmd = [
        "helm",
        "template",
        f"snapshot-{case.chart}",
        str(chart_path),
        "--namespace",
        case.namespace,
    ]
    for value in case.set_values:
        cmd.extend(["--set", value])
    for api_version in case.api_versions:
        cmd.extend(["--api-versions", api_version])

    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chart render snapshot tests for mysterybox-bridge"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Regenerate snapshot files instead of asserting.",
    )
    args = parser.parse_args()

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    for case in CASES:
        rendered = render(case)
        expected_path = SNAPSHOTS_DIR / case.output

        if args.update:
            expected_path.write_text(rendered, encoding="utf-8")
            print(f"updated: {expected_path}")
            continue

        if not expected_path.exists():
            failures.append(f"missing snapshot: {expected_path}")
            continue

        expected = expected_path.read_text(encoding="utf-8")
        if rendered != expected:
            failures.append(f"mismatch: {expected_path}")

    if failures:
        print("snapshot check failed:")
        for failure in failures:
            print(f"  - {failure}")
        print("run with --update to refresh snapshots")
        return 1

    if not args.update:
        print("snapshot check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
