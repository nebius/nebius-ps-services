#!/usr/bin/env python3
"""Offline end-to-end test for the app-stack/frontend scaffold handoff."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock


SKILLS_ROOT = Path(__file__).resolve().parents[2]
SCAFFOLD_PATH = SKILLS_ROOT / "scaffold-project/scripts/scaffold_project.py"
FRONTEND_PATH = SKILLS_ROOT / "frontend-project/scripts/frontend_project.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCAFFOLD = _load_module("scaffold_project_e2e", SCAFFOLD_PATH)
FRONTEND = _load_module("frontend_project_e2e", FRONTEND_PATH)


class FrontendScaffoldEndToEndTest(unittest.TestCase):
    def test_app_stack_and_scaffold_schemas_mirror_binding_contract(self) -> None:
        handoff_schema = json.loads(
            (
                SKILLS_ROOT / "app-stack/references/scaffold-handoff.schema.json"
            ).read_text(encoding="utf-8")
        )
        component_schema = handoff_schema["properties"]["components"]["items"]
        technology_schema = component_schema["properties"]["technology"]
        self.assertEqual(handoff_schema["properties"]["schema_version"]["const"], 2)
        self.assertEqual(
            set(component_schema["properties"]["component_class"]["enum"]),
            SCAFFOLD.APP_STACK_COMPONENT_CLASSES,
        )
        self.assertIn("component_class", component_schema["required"])
        self.assertIn("name", technology_schema["required"])

        scaffold_schema = json.loads(
            (
                SKILLS_ROOT / "scaffold-project/references/scaffold-plan.schema.json"
            ).read_text(encoding="utf-8")
        )
        capability_properties = scaffold_schema["properties"]["capabilities"]["items"][
            "properties"
        ]
        unit_properties = scaffold_schema["properties"]["materialization_units"][
            "items"
        ]["properties"]
        self.assertEqual(
            capability_properties["technology"]["$ref"],
            "#/$defs/technology",
        )
        self.assertTrue(unit_properties["technologies"]["uniqueItems"])
        self.assertEqual(unit_properties["technologies"]["minItems"], 1)
        self.assertIn(
            "capability_id",
            scaffold_schema["properties"]["runtime_units"]["items"]["required"],
        )
        self.assertIn(
            "capability_id",
            scaffold_schema["properties"]["runtime_units"]["items"]["properties"],
        )
        self.assertNotIn("capability_id", capability_properties)

    def test_app_stack_handoff_rejects_physical_topology_fields(self) -> None:
        handoff = {
            "schema_version": 2,
            "decision_id": "example-stack",
            "components": [
                {
                    "id": "web",
                    "component_class": "frontend",
                    "kind": "web-ui",
                    "status": "required",
                    "technology": {
                        "name": "react-vite",
                        "language": "typescript",
                        "framework": "react",
                        "profile": "react-vite",
                        "runtime": "browser",
                        "package_manager": "pnpm",
                        "versions": {"react": "19.1.0"},
                    },
                    "capabilities": [],
                    "constraints": [],
                    "validation_expectations": [],
                    "revisit_trigger": None,
                    "path": "apps/web",
                }
            ],
        }

        with self.assertRaisesRegex(
            SCAFFOLD.ScaffoldError,
            "unknown fields.*path",
        ):
            SCAFFOLD._validate_app_stack_handoff_data(handoff)

    def test_required_frontend_profile_and_capabilities_are_bound(self) -> None:
        versions = {
            "react": "19.1.0",
            "react-dom": "19.1.0",
            "react-router": "7.6.2",
        }
        inputs = {
            "candidate_set_id": "frontend-web",
            "component_id": "web",
            "materialization_unit_id": "web",
            "profile": "react-vite",
            "package": {"manager": "pnpm"},
            "versions": versions,
            "capabilities": {
                "routing": {"profile": "none", "version": None},
                "styling": "plain-css",
                "testing": "vitest",
                "public_environment": {"variables": []},
                "lint": {"profile": "none", "version": None},
                "format": {"profile": "none", "version": None},
            },
        }
        handoff = {
            "schema_version": 2,
            "decision_id": "example-stack",
            "components": [
                {
                    "id": "web",
                    "component_class": "frontend",
                    "kind": "web-ui",
                    "status": "required",
                    "technology": {
                        "name": "react-vite",
                        "language": "typescript",
                        "framework": "react",
                        "profile": "react-vite",
                        "runtime": "browser",
                        "package_manager": "pnpm",
                        "versions": versions,
                    },
                    "capabilities": [
                        {
                            "id": "routing",
                            "status": "required",
                            "selection": "react-router",
                            "revisit_trigger": None,
                        }
                    ],
                    "constraints": [],
                    "validation_expectations": [],
                    "revisit_trigger": None,
                }
            ],
        }
        document = {
            "candidate_sets": [
                {
                    "id": "frontend-web",
                    "owner": "frontend-project",
                    "materialization_unit_id": "web",
                }
            ],
            "capabilities": [
                {
                    "id": "web",
                    "kind": "web-ui",
                    "status": "required",
                    "materialization_unit_ids": ["web"],
                    "technology": handoff["components"][0]["technology"],
                }
            ],
            "materialization_units": [
                {
                    "id": "web",
                    "language": "typescript",
                    "technologies": ["react-vite"],
                }
            ],
            "runtime_units": [
                {
                    "capability_id": "web",
                    "materialization_unit_id": "web",
                    "runtime": "browser",
                }
            ],
            "external_services": [],
        }

        with self.assertRaisesRegex(
            SCAFFOLD.ScaffoldError,
            "approved frontend capability mismatch",
        ):
            SCAFFOLD._validate_app_stack_bindings(
                document,
                handoff,
                {"frontend-web": inputs},
            )

        unknown_capability = copy.deepcopy(handoff)
        unknown_capability["components"][0]["capabilities"] = [
            {
                "id": "routng",
                "status": "required",
                "selection": "react-router",
                "revisit_trigger": None,
            }
        ]
        with self.assertRaisesRegex(
            SCAFFOLD.ScaffoldError,
            "unsupported frontend capability",
        ):
            SCAFFOLD._validate_app_stack_bindings(
                document,
                unknown_capability,
                {"frontend-web": inputs},
            )

        unsupported_handoff = copy.deepcopy(handoff)
        unsupported_handoff["components"][0]["kind"] = "frontend"
        unsupported_handoff["components"][0]["technology"]["profile"] = "nextjs"
        with self.assertRaisesRegex(
            SCAFFOLD.ScaffoldError,
            "unsupported required frontend",
        ):
            SCAFFOLD._validate_app_stack_bindings(
                {
                    "candidate_sets": [],
                    "capabilities": [
                        {
                            "id": "web",
                            "kind": "frontend",
                            "status": "required",
                            "materialization_unit_ids": ["web"],
                            "technology": unsupported_handoff["components"][0][
                                "technology"
                            ],
                        }
                    ],
                    "materialization_units": [
                        {
                            "id": "web",
                            "language": "typescript",
                            "technologies": ["react-vite"],
                        }
                    ],
                    "runtime_units": [
                        {
                            "capability_id": "web",
                            "materialization_unit_id": "web",
                            "runtime": "browser",
                        }
                    ],
                    "external_services": [],
                },
                unsupported_handoff,
                {},
            )

    def test_every_required_app_stack_component_is_covered(self) -> None:
        handoff = {
            "schema_version": 2,
            "decision_id": "example-stack",
            "components": [
                {
                    "id": "api",
                    "component_class": "application",
                    "kind": "api",
                    "status": "required",
                    "technology": {
                        "name": "fastapi",
                        "language": "python",
                        "framework": "fastapi",
                        "profile": "api-service",
                        "runtime": "python",
                        "package_manager": "uv",
                        "versions": {"python": "3.13.5"},
                    },
                    "capabilities": [],
                    "constraints": [],
                    "validation_expectations": [],
                    "revisit_trigger": None,
                }
            ],
        }

        with self.assertRaisesRegex(
            SCAFFOLD.ScaffoldError,
            "required app-stack component api",
        ):
            SCAFFOLD._validate_app_stack_bindings(
                {
                    "candidate_sets": [],
                    "capabilities": [],
                    "materialization_units": [],
                    "runtime_units": [],
                    "external_services": [],
                },
                handoff,
                {},
            )

    def test_app_stack_application_technology_drift_is_rejected(self) -> None:
        handoff = {
            "schema_version": 2,
            "decision_id": "example-stack",
            "components": [
                {
                    "id": "api",
                    "component_class": "application",
                    "kind": "api",
                    "status": "required",
                    "technology": {
                        "name": "fastapi",
                        "language": "python",
                        "framework": "fastapi",
                        "profile": "api-service",
                        "runtime": "python",
                        "package_manager": "uv",
                        "versions": {"python": "3.13.5"},
                    },
                    "capabilities": [],
                    "constraints": [],
                    "validation_expectations": [],
                    "revisit_trigger": None,
                }
            ],
        }
        document = {
            "candidate_sets": [],
            "capabilities": [
                {
                    "id": "api",
                    "kind": "api",
                    "status": "required",
                    "materialization_unit_ids": ["api"],
                    "technology": handoff["components"][0]["technology"],
                }
            ],
            "materialization_units": [
                {
                    "id": "api",
                    "language": "python",
                    "technologies": ["fastapi"],
                }
            ],
            "runtime_units": [
                {
                    "capability_id": "api",
                    "materialization_unit_id": "api",
                    "runtime": "python",
                }
            ],
            "external_services": [],
        }

        SCAFFOLD._validate_app_stack_bindings(document, handoff, {})

        mismatches = {
            "selection": lambda value: value["capabilities"][0].update(
                {
                    "technology": {
                        **handoff["components"][0]["technology"],
                        "name": "django",
                        "framework": "django",
                    }
                }
            ),
            "unit technology": lambda value: value["materialization_units"][0].update(
                {"technologies": ["django"]}
            ),
            "language": lambda value: value["materialization_units"][0].update(
                {"language": "go"}
            ),
            "runtime": lambda value: value["runtime_units"][0].update(
                {"runtime": "node"}
            ),
            "kind": lambda value: value["capabilities"][0].update({"kind": "worker"}),
            "extra unit": lambda value: (
                value["capabilities"][0]["materialization_unit_ids"].append("django"),
                value["materialization_units"].append(
                    {
                        "id": "django",
                        "language": "python",
                        "technologies": ["django"],
                    }
                ),
            ),
            "extra runtime": lambda value: value["runtime_units"].append(
                {
                    "capability_id": "api",
                    "materialization_unit_id": "api",
                    "runtime": "ruby",
                }
            ),
        }
        for label, mutate in mismatches.items():
            with self.subTest(label=label):
                mismatched = copy.deepcopy(document)
                mutate(mismatched)
                with self.assertRaisesRegex(
                    SCAFFOLD.ScaffoldError,
                    "app-stack technology mismatch",
                ):
                    SCAFFOLD._validate_app_stack_bindings(
                        mismatched,
                        handoff,
                        {},
                    )

        capability_selection = copy.deepcopy(handoff)
        capability_selection["components"][0]["capabilities"] = [
            {
                "id": "serialization",
                "status": "required",
                "selection": "pydantic",
                "revisit_trigger": None,
            }
        ]
        with self.assertRaisesRegex(
            SCAFFOLD.ScaffoldError,
            "non-frontend capability selections are unsupported",
        ):
            SCAFFOLD._validate_app_stack_bindings(
                document,
                capability_selection,
                {},
            )

    def test_app_stack_external_service_technology_drift_is_rejected(self) -> None:
        handoff = {
            "schema_version": 2,
            "decision_id": "example-stack",
            "components": [
                {
                    "id": "database",
                    "component_class": "external-service",
                    "kind": "database",
                    "status": "required",
                    "technology": {
                        "name": "postgresql",
                        "language": None,
                        "framework": None,
                        "profile": "relational-database",
                        "runtime": None,
                        "package_manager": None,
                        "versions": {"postgresql": "17.5.0"},
                    },
                    "capabilities": [],
                    "constraints": [],
                    "validation_expectations": [],
                    "revisit_trigger": None,
                }
            ],
        }
        document = {
            "candidate_sets": [],
            "capabilities": [],
            "materialization_units": [],
            "runtime_units": [],
            "external_services": [
                {
                    "id": "database",
                    "kind": "database",
                    "status": "required",
                    "technology": "redis",
                }
            ],
        }

        with self.assertRaisesRegex(
            SCAFFOLD.ScaffoldError,
            "app-stack technology mismatch",
        ):
            SCAFFOLD._validate_app_stack_bindings(
                document,
                handoff,
                {},
            )

    def test_app_stack_handoff_finalize_validate_digest_apply_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "project"
            bundle = root / "bundle"
            bundle.mkdir(mode=0o700)
            candidate_output = bundle / "candidates/frontend-project/frontend-web"
            candidate_output.mkdir(mode=0o700, parents=True)
            current = candidate_output.parent
            while current != bundle:
                os.chmod(current, 0o700)
                current = current.parent

            relative_paths = set(FRONTEND.BASE_PATHS)
            request = {
                "schema_version": 1,
                "candidate_set_id": "frontend-web",
                "profile": "react-vite",
                "component_id": "web",
                "materialization_unit_id": "web",
                "component_root": "apps/web",
                "assigned_paths": [
                    f"apps/web/{path}" for path in sorted(relative_paths)
                ],
                "excluded_paths": ["apps/api", "infra"],
                "package": {
                    "name": "@example/web",
                    "display_name": "Example Web",
                    "manager": "pnpm",
                    "manager_version": "10.10.0",
                    "node_range": ">=22.0.0 <23.0.0",
                },
                "versions": {
                    "@testing-library/jest-dom": "6.6.3",
                    "@testing-library/react": "16.3.0",
                    "@types/node": "22.15.3",
                    "@types/react": "19.1.2",
                    "@types/react-dom": "19.1.2",
                    "@vitejs/plugin-react": "4.4.1",
                    "jsdom": "26.1.0",
                    "react": "19.1.0",
                    "react-dom": "19.1.0",
                    "typescript": "5.8.3",
                    "vite": "6.3.4",
                    "vitest": "3.1.2",
                },
                "capabilities": {
                    "routing": {"profile": "none", "version": None},
                    "styling": "plain-css",
                    "testing": "vitest",
                    "public_environment": {
                        "variables": [{"name": "VITE_API_ORIGIN", "required": True}]
                    },
                    "lint": {"profile": "none", "version": None},
                    "format": {"profile": "none", "version": None},
                },
            }
            request_path = root / "frontend-request.json"
            request_path.write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            handoff = {
                "schema_version": 2,
                "decision_id": "example-stack",
                "components": [
                    {
                        "id": "web",
                        "component_class": "frontend",
                        "kind": "web-ui",
                        "status": "required",
                        "technology": {
                            "name": "react-vite",
                            "language": "typescript",
                            "framework": "react",
                            "profile": "react-vite",
                            "runtime": "browser",
                            "package_manager": "pnpm",
                            "versions": request["versions"],
                        },
                        "capabilities": [
                            {
                                "id": "routing",
                                "status": "rejected",
                                "selection": None,
                                "revisit_trigger": "Add when multiple routes exist.",
                            },
                            {
                                "id": "public-environment",
                                "status": "required",
                                "selection": "vite-public-environment",
                                "revisit_trigger": None,
                            },
                        ],
                        "constraints": [
                            "Materialize only after scaffold path assignment."
                        ],
                        "validation_expectations": [
                            "Typecheck, test, and build after dependencies exist."
                        ],
                        "revisit_trigger": None,
                    }
                ],
            }
            handoff_path = root / "app-stack-handoff.json"
            handoff_bytes = (
                json.dumps(
                    handoff,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            handoff_path.write_bytes(handoff_bytes)
            handoff_digest = hashlib.sha256(handoff_bytes).hexdigest()

            with mock.patch(
                "socket.create_connection",
                side_effect=AssertionError("network access is forbidden"),
            ):
                rendered = FRONTEND.render_candidates(
                    request_path,
                    candidate_output,
                )
                candidate_manifest_path = candidate_output / "manifest.json"
                candidate_manifest = json.loads(
                    candidate_manifest_path.read_text(encoding="utf-8")
                )
                manifest_relative = candidate_manifest_path.relative_to(
                    bundle
                ).as_posix()
                manifest_parent = PurePosixPath(manifest_relative).parent
                operations = [
                    {
                        "path": entry["path"],
                        "action": "create",
                        "owner": "frontend-project",
                        "materialization_unit_id": "web",
                        "candidate": (
                            manifest_parent / PurePosixPath(entry["candidate"])
                        ).as_posix(),
                        "mode": entry["mode"],
                        "candidate_set_id": "frontend-web",
                    }
                    for entry in candidate_manifest["files"]
                ]
                draft = {
                    "schema_version": 2,
                    "project": {
                        "name": "example",
                        "repository_shape": "single-component",
                        "architecture": {
                            "approval": "approved-artifact",
                            "approval_reference": "app-stack:example-stack",
                            "handoff": {
                                "schema_version": 2,
                                "path": str(handoff_path),
                                "sha256": handoff_digest,
                            },
                            "sources": [
                                {
                                    "path": str(handoff_path),
                                    "sha256": handoff_digest,
                                }
                            ],
                        },
                    },
                    "capabilities": [
                        {
                            "id": "web",
                            "kind": "web-ui",
                            "status": "required",
                            "materialization_unit_ids": ["web"],
                            "trigger": None,
                            "technology": handoff["components"][0]["technology"],
                        }
                    ],
                    "materialization_units": [
                        {
                            "id": "web",
                            "kind": "application-source",
                            "path": "apps/web",
                            "language": "typescript",
                            "framework": "react-vite",
                            "owner": "frontend-project",
                            "invocation_scope": "coordinated-candidate",
                            "technologies": ["react-vite"],
                        }
                    ],
                    "runtime_units": [
                        {
                            "id": "web",
                            "kind": "static-web",
                            "capability_id": "web",
                            "materialization_unit_id": "web",
                            "runtime": "browser",
                        }
                    ],
                    "external_services": [],
                    "candidate_sets": [
                        {
                            "id": "frontend-web",
                            "owner": "frontend-project",
                            "materialization_unit_id": "web",
                            "profile": "react-vite",
                            "input_sha256": rendered["input_sha256"],
                            "manifest": manifest_relative,
                            "manifest_sha256": rendered["manifest_sha256"],
                            "operation_paths": sorted(
                                entry["path"] for entry in candidate_manifest["files"]
                            ),
                            "validation_ids": [
                                validation["id"]
                                for validation in candidate_manifest["validations"]
                            ],
                        }
                    ],
                    "operations": operations,
                    "validations": candidate_manifest["validations"],
                    "execution": {
                        "allow_apply": True,
                        "initialize_git": False,
                        "install_dependencies": False,
                        "network_access": False,
                        "provision_services": False,
                        "deploy": False,
                    },
                    "safety": {"reserved_paths": []},
                }
                draft_path = bundle / "manifest.draft.json"
                draft_path.write_text(
                    json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.chmod(draft_path, 0o600)

                mismatched_handoff = copy.deepcopy(handoff)
                mismatched_handoff["components"][0]["technology"]["versions"][
                    "react"
                ] = "19.2.0"
                mismatched_bytes = (
                    json.dumps(
                        mismatched_handoff,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode()
                handoff_path.write_bytes(mismatched_bytes)
                mismatched_digest = hashlib.sha256(mismatched_bytes).hexdigest()
                draft["project"]["architecture"]["handoff"]["sha256"] = (
                    mismatched_digest
                )
                draft["project"]["architecture"]["sources"][0]["sha256"] = (
                    mismatched_digest
                )
                draft_path.write_text(
                    json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.chmod(draft_path, 0o600)
                with self.assertRaisesRegex(
                    SCAFFOLD.ScaffoldError,
                    "app-stack technology mismatch",
                ):
                    SCAFFOLD.finalize_bundle(target, bundle)

                handoff_path.write_bytes(handoff_bytes)
                draft["project"]["architecture"]["handoff"]["sha256"] = handoff_digest
                draft["project"]["architecture"]["sources"][0]["sha256"] = (
                    handoff_digest
                )
                draft_path.write_text(
                    json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.chmod(draft_path, 0o600)

                finalized = SCAFFOLD.finalize_bundle(target, bundle)
                validated = SCAFFOLD.validate_bundle(target, bundle)
                self.assertEqual(finalized["bundle_digest"], validated["bundle_digest"])
                with self.assertRaisesRegex(SCAFFOLD.ScaffoldError, "expected digest"):
                    SCAFFOLD.apply_bundle(target, bundle, "0" * 64)
                self.assertFalse(target.exists())

                applied = SCAFFOLD.apply_bundle(
                    target,
                    bundle,
                    finalized["bundle_digest"],
                )
                self.assertEqual(applied["applied"], len(operations))
                repeated = SCAFFOLD.apply_bundle(
                    target,
                    bundle,
                    finalized["bundle_digest"],
                )
                self.assertEqual(repeated["applied"], 0)

            actual_files = {
                path.relative_to(target).as_posix()
                for path in target.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual_files, set(request["assigned_paths"]))
            self.assertTrue(all(path.startswith("apps/web/") for path in actual_files))
            self.assertEqual(
                (target / "apps/web/.env.example").read_text(encoding="utf-8"),
                "VITE_API_ORIGIN=\n",
            )


if __name__ == "__main__":
    unittest.main()
