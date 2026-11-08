"""Real execution bases must keep their physical catalog identity across observers."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import verify_agentic_sdlc as verifier


class CatalogTests(unittest.TestCase):
    def test_distinct_catalog_owner_isolated_and_exact_basis_preserved(self):
        source = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            copied = Path(temporary) / "installed"
            for name in verifier.SOURCE_PARITY_SKILLS:
                shutil.copytree(source / name, copied / name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                self.assertEqual(verifier.tree_digest(source / name), verifier.tree_digest(copied / name))
            # A fresh B parent creates the fixture with its real owner. Its
            # source A and installed B probes then get separate module caches.
            code = textwrap.dedent(r'''
                import hashlib,json,os,subprocess,sys
                from pathlib import Path
                source,copied=map(Path,sys.argv[1:])
                sys.path.insert(0,str(copied/'sdlc-workflow-test/scripts'))
                from test_three_tier_target import ThreeTierTargetTests, lifecycle
                case=ThreeTierTargetTests('test_fresh_specs_admit_without_early_commit_and_target_real_integration')
                try:
                    case.setUp()
                    coordinator=case.admit_and_prepare()
                    protected=[case.run/'run.json',case.run/'objective.json',case.run/'current-state.json',
                               *case.run.glob('bases/*/*.json'),*case.run.glob('objective-impact/execution/*.json'),
                               *(case.home/'sdlc-runs/bindings').glob('*.json')]
                    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
                    head=case.git('rev-parse','HEAD')
                    environment=dict(os.environ)
                    probe="""import json,sys
from pathlib import Path
catalog,root,run,project=map(Path,sys.argv[1:5])
sys.path.insert(0,str(catalog/'sdlc-workflow-test/scripts'))
sys.path.insert(0,str(catalog/'worktree/scripts'))
from workflow_impact import verify_execution
try:
 verify_execution(run,'FEAT-001',sys.argv[5],project)
 result={'basis':'PASS'}
except Exception as e:
 result={'basis':str(e)}
import three_tier_lifecycle as lifecycle
try:
 _,state=lifecycle.load_active(root)
 result['target']=lifecycle.execution_target(state,require_clean=False)['path']
except Exception as e:
 result['target']=str(e)
print(json.dumps(result))
"""
                    def observe(catalog):
                        result=subprocess.run([sys.executable,'-B','-c',probe,str(catalog),str(case.root),
                            str(case.run),str(case.project),coordinator['plan_digest']],
                            env=environment,text=True,capture_output=True,timeout=40)
                        assert result.returncode==0,result.stderr
                        return json.loads(result.stdout)
                    wrong=observe(source)
                    assert 'immutable execution basis is stale' in wrong['basis'],wrong
                    assert 'catalog differs' in wrong['target'],wrong
                    correct=observe(copied)
                    assert correct=={'basis':'PASS','target':coordinator['integration_worktree']},correct
                    dependency=copied/'worktree/scripts/workflow_impact.py'
                    original=dependency.read_bytes()
                    dependency.write_bytes(original+b'\n')
                    changed=observe(copied)
                    assert 'immutable execution basis is stale' in changed['basis'],changed
                    assert 'catalog changed' in changed['target'],changed
                    dependency.write_bytes(original)
                    # Synthetic parity alone must not bypass the frozen catalog,
                    # even when both the source and private helper change alike.
                    import three_tier_native as native
                    hook_home=case.root/'synthetic-hook-home'
                    for relative,data in native.hook_sources(copied).items():
                        tail=Path(relative).name
                        if '/hooks/lib/' in relative:
                            tail='lib/'+tail
                        path=hook_home/'hooks'/tail
                        path.parent.mkdir(parents=True,exist_ok=True)
                        path.write_bytes(data)
                        path.chmod(0o600)
                    identity=native._hook_identity(hook_home,copied)
                    hook_helper=copied/'sdlc-start/assets/hooks/lib/sdlc_policy.py'
                    original=hook_helper.read_bytes()
                    hook_helper.write_bytes(original+b'\n# changed hook dependency\n')
                    private_helper=hook_home/'hooks/lib/sdlc_policy.py'
                    private_helper.write_bytes(hook_helper.read_bytes())
                    assert native._hook_identity(hook_home,copied)==identity
                    changed=observe(copied)
                    assert 'catalog changed' in changed['target'],changed
                    hook_helper.unlink()
                    missing=observe(copied)
                    assert missing['target']=='Live runtime catalog closure is unavailable or invalid.',missing
                    hook_helper.write_bytes(original)
                    private_helper.write_bytes(original)
                    mandatory=copied/'worktree/references/document-delivery.md'
                    original=mandatory.read_bytes()
                    mandatory.unlink()
                    missing=observe(copied)
                    assert missing['target']=='Live runtime catalog closure is unavailable or invalid.',missing
                    mandatory.write_bytes(original)
                    assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
                    assert case.git('rev-parse','HEAD')==head
                    assert case.git('status','--porcelain')==''
                    assert dict(os.environ)==environment
                    print('catalog isolation, drift rejection and immutable evidence: PASS')
                finally:
                    case.doCleanups()
            ''')
            # The embedded probe has unindented lines by design; normalize only
            # the outer program's fixed indentation, not its literal body.
            lines = code.splitlines()
            code = "\n".join(line[16:] if line.startswith(" " * 16) else line for line in lines)
            result = subprocess.run([sys.executable, "-B", "-c", code, str(source), str(copied)],
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                                    text=True, capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("immutable evidence: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
