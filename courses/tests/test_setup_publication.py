"""Ordinary setup initializes publication without running a publisher or GPU job."""

import json
import sys
from types import SimpleNamespace

import pytest

from course_bootstrap import catalog, cli, controller, runtime, selection
import course_publication_state as publication


@pytest.fixture
def setup_course(tmp_path, monkeypatch):
    root = tmp_path / "installed courses"
    course = root / "example"
    (course / "reference").mkdir(parents=True)
    (course / "reference/course.json").write_text(json.dumps({"slug": "example"}))
    (root / "tools").mkdir()
    (root / "tools/regular-lab-setup.py").touch()
    definitions = {
        "components": {
            "fixture": {
                "kind": "commands",
                "artifacts": ["ready"],
                "commands": [{"argv": [
                    sys.executable, "-c",
                    "from pathlib import Path; Path('ready').write_text('installed')",
                ]}],
            },
        },
        "runtimes": {
            "lab": {
                "components": ["fixture"],
                "environment": {"COURSE_PUBLISH_PYTHON": sys.executable},
            },
        },
    }
    monkeypatch.setattr(catalog, "load_catalog", lambda: definitions)
    monkeypatch.setattr(catalog, "fingerprint", lambda *args: "fixture")
    monkeypatch.setattr(controller, "ubuntu", lambda: {"os": "fixture"})
    monkeypatch.setattr(controller, "capacity", lambda: [])
    package_calls = []
    monkeypatch.setattr(controller, "install_packages", lambda *args, **kw: package_calls.append(kw))
    monkeypatch.setattr(
        cli, "prepare",
        lambda **kw: (root / ".runtime").mkdir(mode=0o700, exist_ok=True),
    )
    monkeypatch.setattr(
        selection, "resolve",
        lambda group, *args, **kw: {"group": group, "runtimes": ["lab"], "labs": ["example/lab"]},
    )
    monkeypatch.chdir(tmp_path)

    def configure(workspace="learner"):
        path = root / ".course-environment.sh"
        path.write_text(
            f"export COURSE_WORKSPACE={workspace}\n"
            "export COURSE_PUSHGATEWAY=http://127.0.0.1:9091\n"
            "export COURSE_METRICS_URL=http://127.0.0.1:8428\n"
        )
        path.chmod(0o600)

    def run(group="regular", *args):
        script = root / "tools" / f"{group}-lab-setup.py"
        monkeypatch.setattr(sys, "argv", [str(script), *args])
        return cli.main(script, group)

    return SimpleNamespace(
        root=root, course=course, definitions=definitions, configure=configure,
        run=run, package_calls=package_calls,
        state=root / ".runtime/publications",
    )


@pytest.mark.parametrize("group", selection.GROUPS)
@pytest.mark.parametrize("configured", [False, True])
def test_every_setup_group_initializes_and_cached_reruns_preserve_state(
    setup_course, group, configured, capsys,
):
    fixture = setup_course
    if configured:
        fixture.configure()
    args = () if group == "regular" else ("--all",)
    assert fixture.run(group, *args) == 0
    if not configured:
        assert "publication unconfigured" in capsys.readouterr().out
        assert not fixture.state.exists()
        fixture.configure()
        assert fixture.run(group, *args) == 0
    assert "publication initialized" in capsys.readouterr().out
    identity = publication.read(fixture.state / "identity.json")
    assert identity["binding"]["root"] == str(fixture.root)
    publication.atomic(fixture.state / "sequence.json", {
        "publisher_id": identity["publisher_id"], "sequence": 7,
    })
    (fixture.state / "runs/7.json").write_text("retained registration")
    before = {p.relative_to(fixture.state): p.read_bytes() for p in fixture.state.rglob("*") if p.is_file()}
    runtime = (fixture.root / ".runtime/runtimes/lab.json").read_bytes()
    assert fixture.run(group, *args) == 0
    assert "publication already initialized" in capsys.readouterr().out
    assert {p.relative_to(fixture.state): p.read_bytes() for p in fixture.state.rglob("*") if p.is_file()} == before
    assert (fixture.root / ".runtime/runtimes/lab.json").read_bytes() == runtime


@pytest.mark.parametrize("failure", ["packages", "component"])
def test_failed_preparation_never_creates_publication_identity(setup_course, monkeypatch, failure):
    fixture = setup_course
    fixture.configure()
    if failure == "packages":
        def fail(*args, **kwargs):
            raise RuntimeError("package installation failed")
        monkeypatch.setattr(controller, "install_packages", fail)
    else:
        fixture.definitions["components"]["fixture"]["commands"] = [
            {"argv": [sys.executable, "-c", "raise SystemExit(7)"]},
        ]
    with pytest.raises(SystemExit) as error:
        fixture.run()
    assert error.value.code == 1
    assert not fixture.state.exists()


@pytest.mark.parametrize("defect", ["malformed", "symlink", "shared-settings", "endpoint", "workspace"])
def test_invalid_monitoring_fails_before_installation(setup_course, defect):
    fixture = setup_course
    fixture.configure()
    path = fixture.root / ".course-environment.sh"
    if defect == "malformed":
        path.write_text("export UNKNOWN=value\n")
    elif defect == "symlink":
        original = path.with_name("original-settings")
        path.rename(original)
        path.symlink_to(original)
    elif defect == "shared-settings":
        path.chmod(0o644)
    elif defect == "endpoint":
        path.write_text(path.read_text().replace("http://127.0.0.1:9091", "invalid"))
    else:
        fixture.configure("InvalidWorkspace")
    with pytest.raises(SystemExit) as error:
        fixture.run()
    assert error.value.code == 1
    assert not fixture.package_calls
    assert not fixture.state.exists()


@pytest.mark.parametrize("defect", ["binding", "counter", "symlink", "shared-state"])
def test_invalid_existing_state_is_not_reset_before_installation(setup_course, defect):
    fixture = setup_course
    fixture.configure()
    fixture.run()
    original = (fixture.state / "identity.json").read_bytes()
    if defect == "binding":
        fixture.configure("another-learner")
    elif defect == "counter":
        (fixture.state / "sequence.json").unlink()
    elif defect == "symlink":
        original_state = fixture.state.with_name("retained-publications")
        fixture.state.rename(original_state)
        fixture.state.symlink_to(original_state, target_is_directory=True)
    else:
        fixture.state.chmod(0o755)
    fixture.package_calls.clear()
    with pytest.raises(SystemExit) as error:
        fixture.run()
    assert error.value.code == 1
    assert not fixture.package_calls
    assert (fixture.state / "identity.json").read_bytes() == original


@pytest.mark.parametrize("group", selection.GROUPS)
def test_plans_help_and_unselected_specialized_scripts_do_not_initialize(setup_course, group):
    fixture = setup_course
    fixture.configure()
    assert fixture.run(group, "--plan") == 0
    with pytest.raises(SystemExit) as error:
        fixture.run(group, "--help")
    assert error.value.code == 0
    if group != "regular":
        assert fixture.run(group) == 0
    assert not fixture.package_calls
    assert not (fixture.root / ".runtime").exists()


def test_separate_initialization_command_is_removed(setup_course):
    with pytest.raises(SystemExit) as error:
        setup_course.run("regular", "publication-init")
    assert error.value.code == 2
    assert not setup_course.package_calls
    assert not (setup_course.root / ".runtime").exists()


def test_job_registration_uses_identity_created_by_normal_setup(setup_course, monkeypatch):
    fixture = setup_course
    fixture.configure()
    fixture.run()
    monkeypatch.setattr(runtime, "load", lambda *args, **kw: (
        fixture.root, {"environment": {"COURSE_PUBLISH_PYTHON": sys.executable}},
    ))
    path = fixture.course / "job.json"
    path.write_text(json.dumps({
        "lab_id": "lab", "job_id": "101", "started_at": "2026-01-01T00:00:00Z",
        "stages": [{"runtime": "lab"}],
    }))
    result = publication.register(fixture.course, path)
    assert result["status"] == "registered"
    assert result["registration"]["sequence"] == 1
    assert result["registration"]["publisher_id"] == publication.read(
        fixture.state / "identity.json",
    )["publisher_id"]


def test_monitoring_changes_during_setup_require_rerun(setup_course, monkeypatch):
    fixture = setup_course
    fixture.configure()
    monkeypatch.setattr(controller, "install_packages", lambda *args, **kw: fixture.configure("changed"))
    with pytest.raises(SystemExit) as error:
        fixture.run()
    assert error.value.code == 1
    assert not fixture.state.exists()
