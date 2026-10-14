"""Real producer-to-executor contracts; no producer validation is mocked here."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = load("python_producer_test", ROOT / "python-project/scripts/python_project.py")
S = load("python_scaffold_test", ROOT / "scaffold-project/scripts/scaffold_project.py")


def fixture(parent, request):
    parent = Path(parent).resolve()
    target = parent / "project"
    bundle = parent / "bundle"
    bundle.mkdir(mode=0o700)
    request_file = parent / "request.json"
    request_file.write_bytes(P.canonical(request))
    candidate_dir = bundle / "candidates/python-project/python-main"
    P.render_candidates(request_file, candidate_dir)
    manifests = [
        (
            "candidates/python-project/python-main/manifest.json",
            json.loads((candidate_dir / "manifest.json").read_text()),
        )
    ]
    for name, payload in P.integration_files(request).items():
        if request["component_root"] != "." and name != ".gitignore":
            continue
        owner = "gitignore" if name == ".gitignore" else "scaffold-project"
        set_id = "root-" + name.strip(".").lower()
        root = bundle / "candidates" / owner / set_id
        inputs = {"file": name}
        manifest = dict(
            schema_version=1,
            owner=owner,
            profile="root-integration",
            candidate_set_id=set_id,
            materialization_unit_id=request["materialization_unit_id"]
            if request["component_root"] == "."
            else None,
            input_sha256=P.digest(P.canonical(inputs)),
            inputs=inputs,
            files=[
                dict(
                    path=name,
                    candidate="files/" + name,
                    mode="0644",
                    sha256=P.digest(payload),
                )
            ],
            validations=[],
        )
        manifest["validations"] = [
            dict(
                id=set_id + ":static",
                owner=owner,
                materialization_unit_id=manifest["materialization_unit_id"],
                candidate_set_id=set_id,
                phase="candidate",
                command="integration fixture check",
                network_required=False,
                status="passed",
            )
        ]
        P.private_write(root / "files" / name, payload)
        P.private_write(root / "manifest.json", P.canonical(manifest))
        manifests.append((f"candidates/{owner}/{set_id}/manifest.json", manifest))
    sets, operations, validations = [], [], []
    for relative, manifest in manifests:
        sets.append(
            dict(
                id=manifest["candidate_set_id"],
                owner=manifest["owner"],
                materialization_unit_id=manifest["materialization_unit_id"],
                profile=manifest["profile"],
                input_sha256=manifest["input_sha256"],
                manifest=relative,
                manifest_sha256=P.digest((bundle / relative).read_bytes()),
                operation_paths=[f["path"] for f in manifest["files"]],
                validation_ids=[v["id"] for v in manifest["validations"]],
            )
        )
        for file in manifest["files"]:
            operations.append(
                dict(
                    path=file["path"],
                    action="create",
                    owner=manifest["owner"],
                    materialization_unit_id=manifest["materialization_unit_id"],
                    candidate_set_id=manifest["candidate_set_id"],
                    candidate=str(Path(relative).parent / file["candidate"]),
                    mode="0644",
                )
            )
        validations.extend(manifest["validations"])
    draft = dict(
        schema_version=2,
        project=dict(
            name=request["project_name"],
            repository_shape="single-component",
            architecture=dict(
                approval="direct-user",
                approval_reference="create-request",
                handoff=None,
                sources=[],
            ),
        ),
        capabilities=[
            dict(
                id=request["component_id"],
                kind="application",
                status="required",
                materialization_unit_ids=[request["materialization_unit_id"]],
                trigger=None,
            )
        ],
        materialization_units=[
            dict(
                id=request["materialization_unit_id"],
                kind="application-source",
                path=request["component_root"],
                language="python",
                framework="fastapi"
                if "api" in request["profiles"]
                else "typer"
                if "cli" in request["profiles"]
                else None,
                owner="python-project",
                invocation_scope="coordinated-candidate",
            )
        ],
        runtime_units=[],
        external_services=[],
        candidate_sets=sets,
        operations=operations,
        validations=validations,
        execution=dict(
            allow_apply=True,
            initialize_git=False,
            install_dependencies=False,
            network_access=False,
            provision_services=False,
            deploy=False,
        ),
        safety=dict(reserved_paths=[]),
    )
    P.private_write(bundle / "manifest.draft.json", P.canonical(draft))
    return target, bundle, draft


class PythonProducerTests(unittest.TestCase):
    def test_generated_version_is_ignored_for_root_and_nested_layouts(self):
        for root in (".", "apps/api", "apps/a[pi]"):
            with self.subTest(root=root), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                request = P.default_request(root=root)
                (project / ".gitignore").write_bytes(
                    P.integration_files(request)[".gitignore"]
                )
                subprocess.run(
                    ["git", "init", "--quiet", str(project)],
                    check=True,
                    capture_output=True,
                )
                result = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(project),
                        "check-ignore",
                        "--no-index",
                        P.repository_path(request, "src/example_project/_version.py"),
                    ],
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0)

    def test_real_producer_and_executor_cli_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, _ = fixture(directory, P.default_request(profiles=("cli",)))

            def command(script, *args, expected_code=0):
                result = subprocess.run(
                    [sys.executable, "-B", str(ROOT / script), *map(str, args)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, expected_code, result.stderr)
                return json.loads(result.stdout)

            producer = "python-project/scripts/python_project.py"
            executor = "scaffold-project/scripts/scaffold_project.py"
            rendered = command(
                producer,
                "render",
                "--request",
                Path(directory).resolve() / "request.json",
                "--output",
                bundle / "candidates/python-project/python-main",
            )
            self.assertTrue(
                command(producer, "validate", "--manifest", rendered["manifest"])["ok"]
            )
            finalized = command(
                executor, "finalize", "--target", target, "--bundle", bundle
            )
            args = (
                "apply",
                "--target",
                target,
                "--bundle",
                bundle,
                "--expected-digest",
                finalized["bundle_digest"],
            )
            with S.target_lock(target):
                refused = command(executor, *args, expected_code=2)
                self.assertIn("another scaffold", refused["error"])
            self.assertTrue(command(executor, *args)["ok"])
            self.assertTrue(
                command(executor, "status", "--target", target, "--bundle", bundle)[
                    "ok"
                ]
            )

    def test_profile_outputs_and_metadata(self):
        for root in (".", "apps/api"):
            for profiles in (
                (),
                ("cli",),
                ("api",),
                ("systemd",),
                ("cli", "api", "systemd"),
            ):
                with self.subTest(root=root, profiles=profiles):
                    request = P.default_request(root=root, profiles=profiles)
                    manifest, files = P.manifest_data(request)
                    metadata = tomllib.loads(
                        files[P.repository_path(request, "pyproject.toml")].decode()
                    )
                    self.assertEqual(
                        bool(metadata["project"].get("scripts")), "cli" in profiles
                    )
                    self.assertNotIn("license-files", metadata["project"])
                    self.assertFalse(
                        any(path.endswith("_version.py") for path in files)
                    )
                    self.assertEqual(set(request["assigned_paths"]), set(files))
                    self.assertEqual(manifest["validations"][-1]["status"], "pending")

    def test_reject_invalid_inputs(self):
        for field, value in [
            ("profiles", ["ai-ml"]),
            ("package_name", "class"),
            ("component_root", "../bad"),
            ("python_requires", ">=3"),
            ("dependencies", {"evil": "https://example.invalid"}),
            ("schema_version", True),
            ("service_module", "outside.service"),
        ]:
            r = P.default_request()
            r[field] = value
            with self.subTest(field=field), self.assertRaises(P.PythonProjectError):
                P.normalize(r)
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b"[]"):
            with self.assertRaises(P.PythonProjectError):
                P.decode(raw)
        for path in (".", "../a", "a/../b", "/tmp/x", ".git/config", "AGENTS.md"):
            with self.assertRaises(P.PythonProjectError):
                P.path_name(path)
        r = P.default_request()
        r["excluded_paths"].append("pyproject.toml")
        with self.assertRaises(P.PythonProjectError):
            P.normalize(r)

    def test_root_and_nested_apply_replay_after_generated_files(self):
        for root in (".", "apps/api"):
            with self.subTest(root=root), tempfile.TemporaryDirectory() as directory:
                target, bundle, draft = fixture(
                    directory, P.default_request(root=root, profiles=("cli",))
                )
                result = S.finalize_bundle(target, bundle)
                S.apply_bundle(target, bundle, result["bundle_digest"])
                paths = [target / op["path"] for op in draft["operations"]]
                before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
                project = target / root
                (project / "uv.lock").write_text("fixture generated lock\n")
                (project / ".venv").mkdir()
                (project / "src/example_project/_version.py").write_text(
                    "__version__='1'\n"
                )
                again = S.apply_bundle(target, bundle, result["bundle_digest"])
                self.assertEqual(
                    before,
                    {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in paths},
                )
                self.assertTrue(again["ok"])
                (project / "pyproject.toml").write_text("# edited\n")
                with self.assertRaises(S.ScaffoldError):
                    S.apply_bundle(target, bundle, result["bundle_digest"])

    def test_forged_candidate_rejected_without_app_stack(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, draft = fixture(directory, P.default_request())
            candidate = draft["candidate_sets"][0]
            mpath = bundle / candidate["manifest"]
            manifest = json.loads(mpath.read_text())
            entry = manifest["files"][0]
            payload = b"# forged\n"
            (mpath.parent / entry["candidate"]).write_bytes(payload)
            entry["sha256"] = P.digest(payload)
            mpath.write_bytes(P.canonical(manifest))
            candidate["manifest_sha256"] = P.digest(mpath.read_bytes())
            (bundle / "manifest.draft.json").write_bytes(P.canonical(draft))
            with self.assertRaisesRegex(S.ScaffoldError, "Python candidate"):
                S.finalize_bundle(target, bundle)
            self.assertFalse(target.exists())

    def test_root_is_only_python_unit_and_files_cannot_be_dot(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, draft = fixture(directory, P.default_request())
            other = copy.deepcopy(draft["materialization_units"][0])
            other.update(id="other", path="apps/other")
            draft["materialization_units"].append(other)
            (bundle / "manifest.draft.json").write_bytes(P.canonical(draft))
            with self.assertRaisesRegex(S.ScaffoldError, "sole Python"):
                S.finalize_bundle(target, bundle)
        with self.assertRaises(S.ScaffoldError):
            S._normalize_relative_path(".", "operation")

    def test_metadata_readme_must_be_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, draft = fixture(directory, P.default_request())
            draft["operations"] = [
                op for op in draft["operations"] if op["path"] != "README.md"
            ]
            draft["candidate_sets"] = [
                item
                for item in draft["candidate_sets"]
                if item["id"] != "root-readme.md"
            ]
            draft["validations"] = [
                v
                for v in draft["validations"]
                if v["candidate_set_id"] != "root-readme.md"
            ]
            (bundle / "manifest.draft.json").write_bytes(P.canonical(draft))
            with self.assertRaisesRegex(S.ScaffoldError, "README"):
                S.finalize_bundle(target, bundle)

    def test_brownfield_ignore_merge_binds_python_generated_rules(self):
        for extra_rule in ("", "private-unrelated/\n"):
            with (
                self.subTest(extra_rule=bool(extra_rule)),
                tempfile.TemporaryDirectory() as directory,
            ):
                target, bundle, draft = fixture(
                    directory, P.default_request(root="apps/api")
                )
                target.mkdir()
                original = b"# Existing content\n.DS_Store\n"
                (target / ".gitignore").write_bytes(original)
                candidate = next(
                    item
                    for item in draft["candidate_sets"]
                    if item["owner"] == "gitignore"
                )
                manifest_path = bundle / candidate["manifest"]
                manifest = json.loads(manifest_path.read_bytes())
                payload = manifest_path.parent / manifest["files"][0]["candidate"]
                payload.write_bytes(
                    original + payload.read_bytes() + extra_rule.encode()
                )
                manifest["files"][0]["sha256"] = P.digest(payload.read_bytes())
                manifest_path.write_bytes(P.canonical(manifest))
                candidate["manifest_sha256"] = P.digest(manifest_path.read_bytes())
                next(op for op in draft["operations"] if op["path"] == ".gitignore")[
                    "action"
                ] = "semantic_merge"
                (bundle / "manifest.draft.json").write_bytes(P.canonical(draft))
                if extra_rule:
                    with self.assertRaisesRegex(
                        S.ScaffoldError, "unsupported .gitignore"
                    ):
                        S.finalize_bundle(target, bundle)
                    self.assertEqual((target / ".gitignore").read_bytes(), original)
                    continue
                result = S.finalize_bundle(target, bundle)
                S.apply_bundle(target, bundle, result["bundle_digest"])
                self.assertEqual(
                    (target / ".gitignore").read_bytes(), payload.read_bytes()
                )
                S.apply_bundle(target, bundle, result["bundle_digest"])

    def test_direct_candidate_binds_framework_and_logical_component(self):
        for field in ("framework", "component"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                target, bundle, draft = fixture(
                    directory, P.default_request(profiles=("cli",))
                )
                if field == "framework":
                    draft["materialization_units"][0]["framework"] = "django"
                else:
                    draft["capabilities"][0]["id"] = "other"
                (bundle / "manifest.draft.json").write_bytes(P.canonical(draft))
                with self.assertRaisesRegex(
                    S.ScaffoldError, "framework|logical component"
                ):
                    S.finalize_bundle(target, bundle)

    def test_python_external_service_does_not_require_source_candidates(self):
        technology = dict(
            name="external-python",
            language="python",
            framework=None,
            profile=None,
            runtime=None,
            package_manager=None,
            versions={},
        )
        component = dict(
            id="external",
            component_class="external-service",
            kind="service",
            status="required",
            technology=technology,
            capabilities=[],
        )
        document = dict(
            candidate_sets=[],
            capabilities=[],
            materialization_units=[],
            runtime_units=[],
            external_services=[{**component, "technology": technology["name"]}],
        )
        S._validate_app_stack_bindings(document, {"components": [component]}, {})

    def test_target_lock_serializes_different_bundles(self):
        with tempfile.TemporaryDirectory() as directory:
            target, bundle, _ = fixture(directory, P.default_request())
            result = S.finalize_bundle(target, bundle)
            with S.target_lock(target):
                with self.assertRaisesRegex(ValueError, "another scaffold"):
                    S.apply_bundle(target, bundle, result["bundle_digest"])
            self.assertFalse(target.exists())

    def test_app_stack_python_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            request = P.default_request(profiles=("cli",))
            _, _, draft = fixture(directory, request)
            technology = dict(
                name="python",
                language="python",
                framework="typer",
                profile="python-package",
                runtime=None,
                package_manager="uv",
                versions={"python": request["python_requires"], "typer": ">=0.12,<1"},
            )
            draft["capabilities"][0]["technology"] = technology
            draft["materialization_units"][0]["technologies"] = ["python"]
            component = dict(
                id="main",
                component_class="application",
                kind="application",
                status="required",
                technology=technology,
                capabilities=[
                    dict(
                        id="cli",
                        status="required",
                        selection="typer",
                        revisit_trigger=None,
                    )
                ],
                constraints=[],
                validation_expectations=[],
                revisit_trigger=None,
            )
            handoff = dict(
                schema_version=2, decision_id="python", components=[component]
            )
            S._validate_app_stack_bindings(
                draft, handoff, {"python-main": P.normalize(request)}
            )
            extra = copy.deepcopy(draft)
            extra["candidate_sets"].append(
                {
                    "id": "unapproved",
                    "owner": "python-project",
                    "materialization_unit_id": "unapproved",
                }
            )
            with self.assertRaisesRegex(S.ScaffoldError, "not a required"):
                S._validate_app_stack_bindings(
                    extra,
                    handoff,
                    {
                        "python-main": P.normalize(request),
                        "unapproved": {
                            **P.normalize(request),
                            "component_id": "unapproved",
                        },
                    },
                )
            undeclared = P.default_request(profiles=("cli", "systemd"))
            with self.assertRaisesRegex(S.ScaffoldError, "undeclared Python profile"):
                S._validate_app_stack_bindings(
                    draft, handoff, {"python-main": P.normalize(undeclared)}
                )
            request["dependencies"]["typer"] = ">=0.15,<1"
            with self.assertRaisesRegex(S.ScaffoldError, "versions"):
                S._validate_app_stack_bindings(
                    draft, handoff, {"python-main": P.normalize(request)}
                )
            technology["versions"]["typer"] = ">=0.15,<1"
            S._validate_app_stack_bindings(
                draft, handoff, {"python-main": P.normalize(request)}
            )
            component["capabilities"][0]["selection"] = "click"
            with self.assertRaisesRegex(S.ScaffoldError, "capability"):
                S._validate_app_stack_bindings(
                    draft, handoff, {"python-main": P.normalize(request)}
                )


if __name__ == "__main__":
    unittest.main()
