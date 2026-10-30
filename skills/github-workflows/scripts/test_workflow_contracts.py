#!/usr/bin/env python3
"""Repository inventory and executable lane/metadata contract checks."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github/workflows"


def workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def step(value, name):
    return next(s for s in value if s.get("name") == name)


class WorkflowContracts(unittest.TestCase):
    def test_policy_listeners_and_ci_filters_agree(self):
        policy = json.loads((ROOT / ".github/merge-policy.json").read_text())
        names = []
        for row in policy["ci_workflows"]:
            ci = workflow(row["file"])
            names.append(ci["name"])
            for event in ["pull_request", "push"]:
                actual = (ci["on"][event] or {}).get("paths", ["**"])
                self.assertEqual(set(actual), set(row["paths"]), (row["file"], event))
            self.assertTrue(
                set(
                    [
                        "result_sha",
                        "merge_pr",
                        "broker_run_id",
                        "broker_run_attempt",
                        "correlation_id",
                    ]
                ).issubset(ci["on"]["workflow_dispatch"]["inputs"])
            )
            steps = ci["jobs"]["merge-inputs"]["steps"]
            self.assertIn(
                "ci-route --workflow " + row["file"],
                step(steps, "Freeze result routing identity before tests")["run"],
            )
            self.assertEqual(
                step(steps, "Preserve result routing identity")["with"]["name"],
                "result-route-${{ github.run_id }}-${{ github.run_attempt }}",
            )
        broker = workflow(policy["merge_workflow"])
        completion = workflow(policy["completion_workflow"])
        self.assertEqual(set(broker["on"]["workflow_run"]["workflows"]), set(names))
        self.assertEqual(
            set(completion["on"]["workflow_run"]["workflows"]),
            set(names + [broker["name"]]),
        )
        self.assertEqual(
            broker["on"]["workflow_run"]["types"], ["in_progress", "completed"]
        )
        self.assertNotIn("schedule", broker["on"])
        self.assertEqual(completion["on"]["schedule"], [{"cron": "17 * * * *"}])
        for job in ["recovery-ci-targets", "repair-ci-status", "candidates"]:
            self.assertIn(
                "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)",
                completion["jobs"][job]["if"],
            )

    def test_release_evidence_precedes_effect_and_verification_follows(self):
        for name in [
            "vpngw-release.yml",
            "nebius-cxcli-release.yml",
        ]:
            with self.subTest(workflow=name):
                value = workflow(name)
                steps = value["jobs"]["release"]["steps"]
                labels = [s.get("name") for s in steps]
                self.assertLess(
                    labels.index("Freeze asset hashes before publication"),
                    labels.index("Preserve immutable pre-publication evidence"),
                )
                self.assertLess(
                    labels.index("Preserve immutable pre-publication evidence"),
                    labels.index("Create GitHub release"),
                )
                self.assertGreater(
                    labels.index("Verify published release against original evidence"),
                    labels.index("Create GitHub release"),
                )
                effect = step(steps, "Create GitHub release")["run"]
                manifest = step(steps, "Freeze asset hashes before publication")["run"]
                assets = re.findall(
                    r"\$\{\{ steps\.(?:asset|package)\.outputs\.[a-z_]+ \}\}", effect
                )
                self.assertTrue(assets)
                for asset in assets:
                    self.assertIn('--asset "' + asset + '"', manifest)
                self.assertEqual(value["concurrency"]["queue"], "max")
                self.assertNotIn("gh release view", json.dumps(value))

    def test_tag_and_manual_image_lanes_do_not_share_aliases(self):
        value = workflow("nccl-test-image-publish.yml")
        self.assertNotIn("release_version", value["on"]["workflow_dispatch"]["inputs"])
        self.assertNotIn("github.ref", value["concurrency"]["group"])
        self.assertEqual(value["concurrency"]["queue"], "max")
        self.assertEqual(value["concurrency"]["cancel-in-progress"], "false")
        steps = value["jobs"]["publish"]["steps"]
        self.assertIn(
            '"$RUNNER_TEMP/publication.py"',
            step(steps, "Freeze helper from workflow revision")["run"],
        )
        self.assertEqual(
            step(steps, "Checkout trusted publication owner")["with"]["ref"],
            "${{ github.sha }}",
        )
        latest_step = step(
            steps, "Update explicitly requested latest from verified digest"
        )
        self.assertEqual(
            latest_step["if"], "steps.existing.outputs.latest_needed == 'true'"
        )
        self.assertIn(
            '--prefer-index=false --tag "$LATEST_REF" "$VERIFIED_SOURCE"',
            latest_step["run"],
        )
        script = step(steps, "Resolve image metadata")["run"].replace(
            "${{ steps.release.outputs.commit }}", "a" * 40
        )
        for version, latest, expected in [
            (
                "1.2.3",
                "false",
                ["registry/image:1.2.3", "registry/image:1.2.3-gaaaaaaa"],
            ),
            ("", "true", ["registry/image:sha-aaaaaaa", "registry/image:latest"]),
        ]:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "output"
                env = {
                    **os.environ,
                    "GITHUB_OUTPUT": str(output),
                    "FULL_IMAGE_NAME": "registry/image",
                    "PUBLISH_VERSION": version,
                    "PUBLISH_LATEST": latest,
                    "SHORT_SHA": "aaaaaaa",
                    "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_REPOSITORY": "example/project",
                    "DEFAULT_CUDA_IMAGE_TAG": "fixture",
                    "DEFAULT_NCCL_TESTS_REF": "fixture",
                }
                subprocess.run(
                    ["bash", "-c", script], env=env, check=True, capture_output=True
                )
                tags = (
                    output.read_text()
                    .split("tags<<EOF\n")[1]
                    .split("\nEOF")[0]
                    .splitlines()
                )
                self.assertEqual(tags, expected)

    def test_review_routing_preserves_manual_people_and_teams(self):
        value = workflow("pr-reviewer-trimmer.yml")
        self.assertIn("pull_request_target", value["on"])
        self.assertEqual(len(value["jobs"]), 1)
        script = value["jobs"]["request-reviewers"]["steps"][0]["with"]["script"]
        harness = r"""
const script = JSON.parse(process.argv[1]);
const requests = [];
const github = {rest:{pulls:{get:async()=>({data:{state:'open',draft:false,head:{repo:{full_name:'example/project'}},base:{sha:'trusted'},user:{login:'author'}}}),listRequestedReviewers:async()=>({data:{users:[{login:'manual'}],teams:[{slug:'manual-team'}]}}),requestReviewers:async value=>requests.push(value)},repos:{getContent:async value=>{if(value.ref!=='trusted')throw Error('untrusted');return {data:{type:'file',encoding:'base64',content:Buffer.from('@author\n@configured\n').toString('base64')}}}}}};
const context={repo:{owner:'example',repo:'project'},payload:{pull_request:{number:1}}};
const core={info:()=>{},notice:()=>{}};
new (Object.getPrototypeOf(async function(){}).constructor)('github','context','core',script)(github,context,core).then(()=>{if(JSON.stringify(requests.map(r=>r.reviewers))!==JSON.stringify([['configured']]))throw Error('bad additions');}).catch(e=>{process.stderr.write(e.message);process.exitCode=1;});
"""
        subprocess.run(
            ["node", "-e", harness, json.dumps(script)], check=True, capture_output=True
        )

    def test_single_release_and_image_template_owners(self):
        for kind in ["release", "image"]:
            matches = list(
                (ROOT / "skills").glob(
                    f"*/assets/project-name-{kind}-publish.yml.template"
                )
            )
            self.assertEqual(
                [p.parent.parent.name for p in matches], ["github-workflows"]
            )


if __name__ == "__main__":
    unittest.main()
