#!/usr/bin/env python3
"""Repository inventory and executable lane/metadata contract checks."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github/workflows"


def workflow(name, *, prepared=False):
    path = (ROOT / "github-ai-automation/deployment/workflows" / (name + ".template")) if prepared else WORKFLOWS / name
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def step(value, name):
    return next(s for s in value if s.get("name") == name)


class WorkflowContracts(unittest.TestCase):
    def test_generic_release_passes_operator_identity_to_qualification(self):
        path = ROOT / "skills/github-workflows/assets/project-name-release-publish.yml.template"
        value = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        steps = next(iter(value["jobs"].values()))["steps"]
        verify = step(steps, "Verify exact-commit release qualification")
        self.assertEqual(verify["env"].get("MERGE_OPERATOR_IDS"), "${{ vars.MERGE_OPERATOR_IDS }}")
        self.assertEqual(value["permissions"]["pull-requests"], "read")

    def test_every_active_workflow_has_a_declared_owner(self):
        policy = json.loads((ROOT / ".github/merge-policy.json").read_text())
        expected = {row["file"] for row in policy["ci_workflows"]}
        expected.update(policy[key] for key in ("merge_workflow", "completion_workflow", "status_workflow"))
        expected.update(profile["publisher"] for profile in policy["release_profiles"].values())
        expected.update({"pr-reviewer-trimmer.yml", "helm-chart-publish.yml", "nccl-test-image-publish.yml"})
        self.assertEqual({path.name for path in WORKFLOWS.glob("*.yml")}, expected)

    def test_release_profiles_cover_jobs_and_producer_modes(self):
        policy = json.loads((ROOT / ".github/merge-policy.json").read_text())
        for project, profile in policy["release_profiles"].items():
            for filename, definition in profile["workflows"].items():
                with self.subTest(project=project, workflow=filename):
                    value = workflow(filename)
                    inputs = value["on"]["workflow_dispatch"]["inputs"]
                    self.assertLessEqual(len(inputs), 25)
                    self.assertTrue({"qualification_sha", "qualification_profile", "qualification_id"}.issubset(inputs))
                    self.assertTrue(set(definition["jobs"]).issubset(value["jobs"]))
                    producers = [job for name, job in value["jobs"].items() if name.startswith("release-qualification-") and project in json.dumps(job)]
                    self.assertEqual(len(producers), 1)
                    self.assertTrue(set(definition["jobs"]).issubset(producers[0]["needs"]))
                    self.assertNotIn("write", json.dumps(producers[0]["permissions"]))
                    for job_id in definition["jobs"]:
                        job = value["jobs"][job_id]
                        self.assertNotIn("write", json.dumps(job.get("permissions", value["permissions"])))
                        commands = "\n".join(s.get("run", "") for s in job["steps"])
                        self.assertIn("release_qualification.py checkout", commands)
                    self.assertIn("qualification_sha", value["concurrency"]["group"])
                    self.assertIn("qualification_sha", value["concurrency"]["cancel-in-progress"])
            release = workflow(profile["publisher"])
            self.assertEqual(release["permissions"]["pull-requests"], "read")
            commands = "\n".join(s.get("run", "") for job in release["jobs"].values() for s in job["steps"])
            self.assertRegex(commands, r'release_qualification\.py"? verify')
            self.assertNotIn("release_qualification.py inputs", commands)

    def test_publication_intents_precede_alias_effects(self):
        image = workflow("nccl-test-image-publish.yml")["jobs"]["publish"]["steps"]
        serialized = [json.dumps(s) for s in image]
        intent = next(i for i, s in enumerate(serialized) if "publication-image-intent-" in s)
        alias = next(i for i, s in enumerate(serialized) if "Assign missing aliases" in s)
        build = next(s for s in image if s.get("uses", "").startswith("docker/build-push-action@"))
        self.assertNotIn("tags", build["with"])
        self.assertIn("push-by-digest=true", build["with"]["outputs"])
        self.assertLess(intent, alias)
        for filename in [
            WORKFLOWS / "nccl-test-image-publish.yml",
            ROOT / "skills/github-workflows/assets/project-name-image-publish.yml.template",
        ]:
            value = yaml.load(filename.read_text(), Loader=yaml.BaseLoader)
            steps = [s for job in value["jobs"].values() for s in job["steps"]]
            record = step(steps, "Record original image intent before aliases")
            preserve = step(steps, "Preserve original image intent before aliases")
            for value in [record, preserve]:
                self.assertEqual(value["if"], "steps.existing.outputs.publish_needed == 'true' || steps.existing.outputs.aliases_needed == 'true' || steps.existing.outputs.latest_needed == 'true'")
            self.assertIn("steps.existing.outputs.digest", record["env"]["IMAGE_DIGEST"])
        helm = workflow("helm-chart-publish.yml")
        self.assertEqual(helm["permissions"]["actions"], "read")
        text = json.dumps(helm)
        self.assertIn("--chart-ref", text)
        self.assertLess(text.index("Preserve chart publication intent"), text.index("helm push"))
        steps = helm["jobs"]["publish"]["steps"]
        for name in ["Preserve original chart package before push", "Record original chart publication intent", "Preserve chart publication intent before push"]:
            # Restored bytes receive new attempt-bound evidence before effects.
            self.assertNotIn("if", step(steps, name))
        self.assertIn("steps.restore.outputs.package_path", step(steps, "Preserve original chart package before push")["with"]["path"])
        self.assertIn("steps.restore.outputs.package_path", step(steps, "Record original chart publication intent")["env"]["PACKAGE_PATH"])

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
        observer = workflow(policy["status_workflow"])
        self.assertEqual(set(broker["on"]), {"workflow_dispatch"})
        self.assertEqual(set(observer["on"]["workflow_run"]["workflows"]), set(names))
        self.assertEqual(
            set(completion["on"]["workflow_run"]["workflows"]),
            {broker["name"]},
        )
        self.assertEqual(
            observer["on"]["workflow_run"]["types"], ["in_progress", "completed"]
        )
        self.assertNotIn("schedule", broker["on"])
        self.assertEqual(completion["on"]["schedule"], [{"cron": "17 * * * *"}])
        for job in ["recovery-ci-targets", "repair-ci-status", "candidates"]:
            self.assertIn(
                "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)",
                completion["jobs"][job]["if"],
            )

    def test_release_builders_cannot_publish_or_access_controller_secrets(self):
        for name in ["vpngw-release.yml", "nebius-cxcli-release.yml"]:
            with self.subTest(workflow=name):
                value = workflow(name, prepared=True)
                self.assertEqual(set(value["on"]), {"workflow_call"})
                self.assertEqual(value["permissions"], {"contents": "read"})
                self.assertNotIn("secrets", json.dumps(value))
                self.assertNotIn("gh release", json.dumps(value))
                steps = value["jobs"]["release"]["steps"]
                uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
                self.assertEqual(len(uploads), 1)
                self.assertIn("release-assets-", uploads[0]["with"]["name"])
        value = workflow("github-ai-release.yml", prepared=True)
        for lane in ["vpngw", "cxcli"]:
            self.assertEqual(value["jobs"][lane]["permissions"], {"contents": "read"})
            self.assertNotIn("secrets", value["jobs"][lane])
        publish = value["jobs"]["publish"]
        self.assertEqual(publish["environment"], "github-ai-automation")
        self.assertIn("needs.prepare.result == 'success'", publish["if"])
        effect = step(publish["steps"], "Publish and verify original assets")
        self.assertIn("release-publish", effect["run"])
        self.assertNotIn("TF_API_KEY", json.dumps(publish))
        # Behavioral original-hash, lease-loss and reservation tests live with
        # the sole publication owner in github-ai-automation/tests.

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
