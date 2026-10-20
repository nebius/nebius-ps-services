"""Synthetic direct-objective state for deterministic hook fixtures only.

Never import this fixture builder from a live trial or product transition.
"""
import hashlib
import json
from pathlib import Path
import subprocess


def seed(run_dir: Path, project: Path, project_id: str, session: str, *, status='accepted', owner='agentic-sdlc'):
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
    context = {'schema':'workflow/project-v1','owner':owner,'project_id':project_id,'project_root':str(project),
               'git_root':git('rev-parse','--show-toplevel'),
               'git_common_dir':git('rev-parse','--path-format=absolute','--git-common-dir')}
    write(run_dir.parent/'project.json',context)
    write(run_dir/'run.json',{**context,'schema':'workflow/run-v1','run_id':run_dir.name,
          'coordinator_session_sha256':session_sha,'status':status,'fence':None,
          'source_branch':git('symbolic-ref','--short','HEAD'),'source_head':git('rev-parse','HEAD'),
          'created_at':'2026-10-19T00:00:00+00:00','updated_at':'2026-10-19T00:00:00+00:00'})
    write(run_dir/'objectives/r0001.json',objective)
    write(run_dir/'objective.json',{'schema':'workflow/accepted-objective-v1','owner':owner,'run_id':run_dir.name,
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
