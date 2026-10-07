"""Exercise the documented shell submissions without Slurm or GPU access."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDES = sorted(ROOT.glob('*/reference/labs/*.md'))


def submissions(path):
    return [block for block in re.findall(r"```bash\n(.*?)```", path.read_text(), re.S)
            if "sbatch " in block]


@pytest.fixture
def submit_spy(tmp_path):
    executable = tmp_path / 'sbatch'
    executable.write_text(
        f'#!{sys.executable}\n'
        'import json, os, sys\n'
        'from pathlib import Path\n'
        'Path("submission.json").write_text(json.dumps({"argv": sys.argv[1:], '
        '"python": os.environ.get("COURSE_PYTHON")}))\n'
        'sys.exit(int(os.environ.get("SUBMIT_EXIT", "0")))\n'
    )
    executable.chmod(0o700)
    return {'PATH': str(tmp_path) + os.pathsep + os.defpath}


def run_block(tmp_path, environment, block):
    return subprocess.run(
        ['bash', '-c', block], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=10,
    )


def test_native_example_submits_with_private_logs_and_no_monitoring(tmp_path, submit_spy):
    block = submissions(ROOT / 'README.md')[0]
    result = run_block(tmp_path, submit_spy, block)
    assert result.returncode == 0, result.stderr
    lab = '01_cpu_gpu_crossover'
    captured = json.loads((tmp_path / 'submission.json').read_text())
    assert captured['argv'] == [
        f'--chdir={tmp_path}',
        f'--output={tmp_path}/results/{lab}/logs/%j.out',
        f'--error={tmp_path}/results/{lab}/logs/%j.err',
        f'slurm/{lab}.sbatch', '--workload', 'small',
    ]
    assert not (tmp_path / "results").exists()  # Preparation is a separate setup operation.


def test_submission_failure_is_not_completion_or_old_result_selection(tmp_path, submit_spy):
    old = tmp_path / 'old-result.json'
    old.write_text('{"old": true}')
    result = run_block(
        tmp_path, {**submit_spy, 'SUBMIT_EXIT': '7'},
        submissions(ROOT / 'README.md')[0],
    )
    assert result.returncode == 7
    assert result.stdout == ''
    assert old.read_text() == '{"old": true}'


def test_mechanics_example_needs_no_parent_runtime_selection(tmp_path, submit_spy):
    result = run_block(
        tmp_path, {**submit_spy, 'HOME': str(tmp_path)},
        submissions(ROOT / 'llm-inference/README.md')[0],
    )
    assert result.returncode == 0, result.stderr
    captured = json.loads((tmp_path / 'submission.json').read_text())
    assert captured['python'] is None
    assert captured['argv'][-3:] == [
        'slurm/09_hf_prefill_decode.sbatch', '--workload', 'small',
    ]


@pytest.mark.parametrize(
    ('course', 'lab', 'cpu_runs'),
    [('llm-training', '32_learning_basics', 1),
     ('llm-inference', '35_inference_basics', 2)],
)
def test_intro_variations_produce_inspectable_jobs(
    tmp_path, submit_spy, course, lab, cpu_runs,
):
    guide = (ROOT / course / 'reference/labs' / f'{lab}.md').read_text()
    variations = guide.split('### Workload variations\n', 1)[1]
    block = re.search(r'```bash\n(.*?)```', variations, re.DOTALL)[1]
    commands = block.replace('\\\n', ' ').strip().splitlines()
    assert len(commands) == cpu_runs + 1  # CPU comparisons and optional CUDA.
    for index, command in enumerate(commands):
        assert command.startswith('sbatch '), command
        result = run_block(tmp_path, submit_spy, command)
        assert result.returncode == 0, result.stderr
        argv = json.loads((tmp_path / 'submission.json').read_text())['argv']
        assert not any('COURSE_PROFILE_TOOL=' in arg for arg in argv)
        assert f'--output={tmp_path}/results/{lab}/logs/%j.out' in argv
        assert f'--error={tmp_path}/results/{lab}/logs/%j.err' in argv
        launcher = lab if index < cpu_runs else lab + '.cuda'
        assert f'slurm/{launcher}.sbatch' in argv
        device = argv[argv.index('--device') + 1]
        assert device == ('cpu' if index < cpu_runs else 'cuda')


def test_every_lab_has_one_shared_prerequisite_link_and_native_inspection():
    assert len(GUIDES) == 110
    for path in GUIDES:
        source = path.read_text()
        before = source.split('## Before you start\n', 1)[1].split('\n## ', 1)[0]
        assert before.count('[Lab Guide](../../../lab-guide.html#lab-preparation-scripts)') == 1, path
        assert 'assigned Grafana dashboard' not in before, path
        assert 'tools/submit_lab.py' not in source, path
        assert '"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py' not in source, path
        assert 'cat "$RESULT_JSON"' in source, path


def test_all_learner_shell_examples_parse():
    paths = {ROOT / 'README.md', *GUIDES}
    for name in ['README.md', 'SYLLABUS.md', 'reference/cluster-smoke-test.md', 'reference/lab-mechanisms.md']:
        paths.update(ROOT.glob('*/' + name))
    for path in sorted(paths):
        for block in re.findall(r'```bash\n(.*?)```', path.read_text(), re.S):
            result = subprocess.run(['bash', '-n'], input=block, capture_output=True, text=True)
            assert result.returncode == 0, (path, result.stderr)
