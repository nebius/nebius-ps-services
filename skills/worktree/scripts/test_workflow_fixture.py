"""Synthetic direct-objective state for deterministic hook fixtures only.

Never import this fixture builder from a live trial or product transition.
"""
import hashlib
import json
from pathlib import Path
import subprocess


def seed(run_dir: Path, project: Path, project_id: str, session: str, *, status='accepted', owner='agentic-sdlc', uat=None):
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')
        path.chmod(0o600)
    def digest(value):
        return hashlib.sha256((json.dumps(value,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode()).hexdigest()
    def git(*args):
        return subprocess.run(['git','-C',str(project),*args],check=True,capture_output=True,text=True).stdout.strip()
    session_sha = hashlib.sha256(session.encode()).hexdigest()
    objective_id = 'objective-' + '1'*32
    objective = {'outcome':'Verify deterministic hook policy','scope':['fixture project'], 'constraints':[],
                 'acceptance_criteria':['hook policy observes the bound run'], 'clarifications':[],
                 'provenance':{'kind':'current_plan','source_sha256':'a'*64}}
    from workflow_objectives import normalize
    if uat is not None:
        objective["uat"] = uat
    objective = normalize(objective)
    context = {'schema':'workflow/project-v2','owner':owner,'project_id':project_id,'project_root':str(project),
               'git_root':git('rev-parse','--show-toplevel'),
               'git_common_dir':git('rev-parse','--path-format=absolute','--git-common-dir')}
    write(run_dir.parent/'project.json',context)
    write(run_dir/'run.json',{**context,'schema':'workflow/run-v4','run_id':run_dir.name,
          'coordinator_session_sha256':session_sha,'status':status,'fence':None,
          'source_branch':git('symbolic-ref','--short','HEAD'),'source_head':git('rev-parse','HEAD'),
          'created_at':'2026-10-19T00:00:00+00:00','updated_at':'2026-10-19T00:00:00+00:00'})
    write(run_dir/'objectives/r0001.json',objective)
    write(run_dir/'objective.json',{'schema':'workflow/accepted-objective-v3','owner':owner,'run_id':run_dir.name,
          'objective_id':objective_id,'revisions':[{'revision':'r0001','parent_sha256':None,
          'snapshot':'objectives/r0001.json','sha256':digest(objective),
          'intent_sha256':digest({k:v for k,v in objective.items() if k!='provenance'})}]})
    write(run_dir.parent/'active-run.json',{'schema':'workflow/active-run-v1','run_id':run_dir.name})
    write(run_dir.parent/'active.lock',{'schema':'workflow/coordinator-ownership-v1','owner':owner,
          'project_id':project_id,'project_root':str(project),'run_id':run_dir.name,'status':status,
          'owner_session_hash':session_sha,'updated_at':'2026-10-19T00:00:00+00:00'})
    write(run_dir.parent.parent/'bindings'/f'{session_sha}.json',{'schema':'workflow/session-binding-v1','owner':owner,
          'session_sha256':session_sha,'run_dir':str(run_dir),'project_root':str(project),'objective_id':objective_id})
    return run_dir


# Shared deterministic execution fixtures; never imported by product code.
def write_private(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def seed_validation(plan):
    from workflow_validation import plan_path
    from workflow_execution import parse_locked_plan
    path = plan_path(plan)
    from workflow_task_acceptance import policy_path, SCHEMA
    if not policy_path(plan).exists() or all(
            c["reason"] == "Synthetic lifecycle fixture; no product runtime under test"
            for row in json.loads(policy_path(plan).read_text())["tasks"].values() for c in row["criteria"]):
        write_private(policy_path(plan), {"schema": SCHEMA, "tasks": {task.task_id: {"requires": [], "criteria": [{
            "id": task.task_id + "-fixture", "purpose": "implementation", "disposition": "not_applicable",
            "reason": "Synthetic lifecycle fixture; no product runtime under test",
            "prerequisite": None, "resolution_stage": None, "check": None
        }]} for task in parse_locked_plan(plan.read_text())}})
    if not path.exists():
        checks = [{"id": "fixture-readme", "argv": ["{python}", "-I", "-c",
                   "import subprocess; assert subprocess.check_output(['git', 'ls-files']).strip()"],
                   "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": []}]
        write_private(path, {"schema": "workflow/validation-plan-v2", "wave_checks": checks,
                            "task_checks": {task.task_id: checks for task in parse_locked_plan(plan.read_text())}})


def prepare_execution(run_dir, project, execution_id, plan, capacity, *, session="deterministic-coordinator"):
    from workflow_execution import prepare_execution as product_prepare_execution
    seed_validation(plan)
    from workflow_state import WorkflowError
    from workflow_impact import ObjectiveImpactError
    try:
        seed_readiness(run_dir, execution_id, plan, session=session)
    except (WorkflowError, ObjectiveImpactError):
        # Invalid-input tests still exercise the product owner's error boundary.
        pass
    return product_prepare_execution(run_dir, project, execution_id, plan, capacity)


def replan_future(run_dir, execution_id, plan, capacity, *, session="deterministic-coordinator"):
    from workflow_execution import replan_future as product_replan_future
    seed_validation(plan)
    seed_readiness(run_dir, execution_id, plan, session=session)
    return product_replan_future(run_dir, execution_id, plan, None)


def complete_wave(run_dir, execution_id, wave_id, _description, *, session):
    from workflow_validation import produce
    from workflow_execution import complete_wave as product_complete_wave, wave_path
    state = json.loads(wave_path(run_dir, execution_id, wave_id).read_text())
    receipt = state.get("combined_evidence") if state["status"] == "done" else produce(run_dir, execution_id, wave_id, session=session)["receipt_path"]
    return product_complete_wave(run_dir, execution_id, wave_id, Path(receipt))


def seed_readiness(run_dir, execution_id, plan=None, *, session="deterministic-coordinator"):
    """Synthetic coordinator phase completion, never native activation proof."""
    from workflow_state import read
    from workflow_readiness import inputs, issue
    run = read(run_dir / "run.json")
    if run["owner"] != "agentic-sdlc":
        return
    features = [execution_id]
    if execution_id.startswith("SLICE-"):
        from workflow_program import verify_inputs
        from workflow_impact import spec_checkout
        project = spec_checkout(run_dir, Path(run["project_root"]), execution_id=execution_id, strict_head=False)
        program = verify_inputs(run_dir, project)["definition"]
        features = next(row["feature_ids"] for row in program["slices"] if row["id"] == execution_id)
    for feature in features:
        context = run_dir / "context" / (feature + ".context.md")
        context.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not context.exists():
            context.write_text("# Synthetic fixture context\n")
    projection = inputs(run_dir, execution_id, plan)
    write_private(run_dir / "steering" / "auto-steering.json", {
        "state_version": 2, "run_id": run_dir.name, "current_execution": execution_id,
        "routing_request": None, "active_reminders": [], "entries": [{
            "status": "resolved", "disposition": "resolved",
            "objective_sha256": projection["objective_sha256"],
            "objective_revision": projection["objective_revision"],
        }],
    })
    (run_dir / "STEERING.md").write_text("# Synthetic fixture steering\n")
    issue(run_dir, execution_id, projection["inputs_sha256"], session, plan)
