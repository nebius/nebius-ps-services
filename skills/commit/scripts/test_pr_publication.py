#!/usr/bin/env python3
"""Real disposable Git transports with mocked GitHub identity and permissions."""

from __future__ import annotations

from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import shlex
import shutil
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('transaction_tests', Path(__file__).with_name('test-commit-transaction.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
tx, git = fixture.transaction, fixture.git


class PublicationTests(fixture.TransactionFixture):
    def finish(self):
        self.publish()
        self.run_helper(*self.task_args('complete-checkout'), '--mode', 'finalize')
        return self.run_helper(*self.task_args('finish'), '--outcome', 'completed')

    def run_helper(self, *arguments, expected=0, classify=True):
        if arguments[0] == 'prepare' and classify and '--requested-action' not in arguments:
            session = arguments[arguments.index('--session-id') + 1]
            arguments = (*arguments, *self.request_assertions.get(session, ()))
        output = io.StringIO()
        with redirect_stdout(output):
            code = tx.main(arguments)
        self.assertEqual(code, expected, output.getvalue())
        return json.loads(output.getvalue())

    def setUp(self):
        fixture.TransactionFixture.setUp(self)
        # Disposable transport fixtures must not inherit operator Git filters.
        test_home = self.base / "home"
        test_home.mkdir()
        test_bin = self.base / "bin"
        test_bin.mkdir()
        (test_bin / "git").write_text("#!/bin/sh\nexec env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null "
                                      + shlex.quote(shutil.which("git")) + ' "$@"\n')
        (test_bin / "git").chmod(0o700)
        environment = patch.dict(fixture.os.environ, {
            "HOME": str(test_home), "XDG_CONFIG_HOME": str(test_home / "config"),
            "PATH": str(test_bin) + fixture.os.pathsep + fixture.os.environ["PATH"],
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.origin = self.base / 'base.git'
        self.fork = self.base / 'fork.git'
        for path in (self.origin, self.fork):
            git(self.root, 'init', '--bare', str(path))
        git(self.root, 'remote', 'add', 'origin', str(self.origin))
        git(self.root, 'branch', 'trunk')
        git(self.root, 'push', 'origin', 'trunk')
        git(self.root, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk')
        self.base_head = git(self.root, 'rev-parse', 'HEAD')
        git(self.root, 'switch', '-c', 'topic')
        (self.root / 'contribution.txt').write_text('contribution\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'contribution')
        self.initial_head = git(self.root, 'rev-parse', 'HEAD')
        git(self.root, 'push', str(self.fork), 'HEAD:refs/heads/topic')
        git(self.root, 'switch', 'feature/test')
        self.pr = {
            'number': 7, 'state': 'open', 'merged': False, 'maintainer_can_modify': True,
            'base': {'ref': 'trunk', 'sha': self.base_head, 'repo': {'id': 1, 'full_name': 'org/base'}},
            'head': {'ref': 'topic', 'sha': self.initial_head, 'repo': {'id': 2, 'full_name': 'person/fork'}},
        }
        self.allow = True
        self.permission_reads = 0
        urls = {'org/base': str(self.origin), 'person/fork': str(self.fork)}
        self.url_patch = patch.object(tx._pr, 'repository_url', side_effect=lambda name: urls[name])
        self.api_patch = patch.object(tx._pr, 'api', side_effect=self.api)
        self.url_patch.start()
        self.api_patch.start()
        self.addCleanup(self.url_patch.stop)
        self.addCleanup(self.api_patch.stop)
        self.alias = 'pr/1/7/review01'
        self.bindings = self.base / 'bindings.json'
        self.bindings.write_text(json.dumps({'refs/heads/' + self.alias: {'repository': 'org/base', 'number': 7}}))
        self.authorize('Review and repair the selected contributor PR')
        self.start_task()

    def start_task(self):
        self.begin('create-pr', pr_base='trunk', target=[self.alias], pr_bindings=str(self.bindings))
        self.run_helper(*self.task_args('checkout-target'), '--branch', self.alias)

    def api(self, path):
        if path == 'repos/org/base/pulls/7':
            value = copy.deepcopy(self.pr)
            value['head']['sha'] = git(self.fork, 'rev-parse', 'refs/heads/topic')
            value['maintainer_can_modify'] = self.allow
            return value
        if path == 'repos/org/base':
            return {'id': 1, 'default_branch': 'trunk', 'permissions': {'push': True}}
        if path == 'repos/person/fork':
            self.permission_reads += 1
            return {'id': 2, 'permissions': {'push': False}}
        raise AssertionError(path)

    def repair(self):
        (self.root / 'repair.txt').write_text('repair\n')
        result = self.pr_execute(self.pr_prepare())
        return result['commit']

    def publish(self, expected=0, **overrides):
        return self.run_helper(*self.task_args('publish-target'), '--branch', self.alias,
                               '--reviewed-head', overrides.get('head', git(self.root, 'rev-parse', 'HEAD')),
                               '--reviewed-base', overrides.get('base', self.base_head), expected=expected)

    def state(self):
        path = Path(self.task_contexts['session-1']['task'])
        return path, json.loads(path.with_suffix('.state.json').read_text())

    def test_fork_repair_uses_only_bound_head_and_replays_without_push(self):
        repaired = self.repair()
        origin_before = git(self.root, 'ls-remote', str(self.origin))
        self.assertEqual(self.publish()['head'], repaired)
        self.assertEqual(git(self.root, 'ls-remote', str(self.origin)), origin_before)
        self.assertEqual(git(self.root, 'rev-parse', 'topic'), self.initial_head)
        with patch.object(tx._pr, 'push', side_effect=AssertionError('duplicate push')):
            self.assertEqual(self.publish()['status'], 'published')
        self.assertEqual(self.permission_reads, 1)

    def test_revoked_permission_and_changed_identity_stop_before_effect(self):
        self.repair()
        for change in ('permission', 'identity'):
            with self.subTest(change=change), patch.object(tx._pr, 'push') as push:
                self.allow = change != 'permission'
                if change == 'identity':
                    self.pr['head']['repo']['id'] = 99
                result = self.publish(expected=2)
                self.assertEqual(result['code'], 'publication_blocked')
                push.assert_not_called()
        self.assertEqual(git(self.fork, 'rev-parse', 'refs/heads/topic'), self.initial_head)

    def test_timeout_after_success_is_reconciled(self):
        repaired = self.repair()
        real_push = tx._pr.push
        def ambiguous(*args):
            self.assertTrue(real_push(*args))
            raise OSError('simulated lost acknowledgement')
        with patch.object(tx._pr, 'push', side_effect=ambiguous):
            self.assertEqual(self.publish()['head'], repaired)

    def test_contributor_race_is_rejected_by_advertised_head_guard(self):
        self.repair()
        real_push = tx._pr.push
        def race(root, runner, destination, ref, expected, head):
            other = git(self.root, 'commit-tree', f'{expected}^{{tree}}', '-p', expected, '-m', 'concurrent contribution')
            git(self.root, 'push', str(self.fork), f'{other}:refs/heads/topic')
            self.concurrent = other
            return real_push(root, runner, destination, ref, expected, head)
        with patch.object(tx._pr, 'push', side_effect=race):
            self.assertEqual(self.publish(expected=2)['code'], 'history_changed')
        self.assertEqual(git(self.fork, 'rev-parse', 'refs/heads/topic'), self.concurrent)
        with patch.object(tx._pr, 'push') as push:
            self.assertEqual(self.publish(expected=2)['code'], 'history_changed')
            push.assert_not_called()
        _, state = self.state()
        self.assertIsNone(state['targets']['refs/heads/' + self.alias]['publication']['pending'])
        self.run_helper(*self.task_args('complete-checkout'), '--mode', 'pause')
        self.run_helper(*self.task_args('finish'), '--outcome', 'cancelled')

    def test_failed_push_can_retry_only_after_old_head_is_reproved(self):
        self.repair()
        with patch.object(tx._pr, 'push', return_value=False):
            self.assertEqual(self.publish(expected=2)['code'], 'effect_unknown')
        self.pr_prepare(expected=2)
        self.assertEqual(self.publish()['status'], 'published')

    def test_moving_base_and_stale_review_cannot_publish(self):
        self.repair()
        with patch.object(tx._pr, 'push') as push:
            self.assertEqual(self.publish(expected=2, head=self.initial_head)['code'], 'history_changed')
            advanced = git(self.root, 'commit-tree', f'{self.base_head}^{{tree}}', '-p', self.base_head, '-m', 'base advance')
            git(self.root, 'push', 'origin', f'{advanced}:refs/heads/trunk')
            self.assertEqual(self.publish(expected=2)['code'], 'history_changed')
            push.assert_not_called()

    def test_synchronized_base_can_publish_while_pr_base_snapshot_lags(self):
        previous_base = self.base_head
        advanced = git(self.root, 'commit-tree', f'{previous_base}^{{tree}}',
                       '-p', previous_base, '-m', 'base advance')
        git(self.root, 'push', 'origin', f'{advanced}:refs/heads/trunk')
        git(self.root, 'fetch', 'origin', 'trunk')
        self.sync_base()
        self.base_head = advanced
        self.assertEqual(self.pr['base']['sha'], previous_base)
        reviewed = git(self.root, 'rev-parse', 'HEAD')
        self.assertEqual(self.publish()['head'], reviewed)
        self.assertEqual(git(self.fork, 'rev-parse', 'refs/heads/topic'), reviewed)
        self.assertEqual(git(self.root, 'rev-parse', 'topic'), self.initial_head)

    def assert_post_push_closure_settles(self, merged):
        repaired = self.repair()
        real_push = tx._pr.push

        def close_after_push(*args):
            result = real_push(*args)
            self.pr.update(state='closed', merged=merged)
            return result

        with patch.object(tx._pr, 'push', side_effect=close_after_push) as push:
            self.assertEqual(self.publish()['head'], repaired)
            self.assertEqual(push.call_count, 1)
        with patch.object(tx._pr, 'push', side_effect=AssertionError('duplicate push')):
            self.assertEqual(self.publish()['head'], repaired)
        _, state = self.state()
        self.assertIsNone(state['targets']['refs/heads/' + self.alias]['publication']['pending'])
        self.run_helper(*self.task_args('complete-checkout'), '--mode', 'pause')
        self.run_helper(*self.task_args('finish'), '--outcome', 'cancelled')

    def test_closed_after_successful_push_settles_without_another_write(self):
        self.assert_post_push_closure_settles(merged=False)

    def test_merged_after_successful_push_settles_without_another_write(self):
        self.assert_post_push_closure_settles(merged=True)

    def test_closed_before_publication_rejects_all_writes(self):
        self.repair()
        self.pr.update(state='closed', merged=False)
        with patch.object(tx._pr, 'push') as push:
            self.assertEqual(self.publish(expected=2)['code'], 'publication_blocked')
            push.assert_not_called()

    def test_closed_after_unapplied_push_cannot_retry_write(self):
        self.repair()
        with patch.object(tx._pr, 'push', return_value=False):
            self.assertEqual(self.publish(expected=2)['code'], 'effect_unknown')
        self.pr.update(state='closed', merged=False)
        with patch.object(tx._pr, 'push') as push:
            self.assertEqual(self.publish(expected=2)['code'], 'publication_blocked')
            push.assert_not_called()
        _, state = self.state()
        self.assertIsNotNone(state['targets']['refs/heads/' + self.alias]['publication']['pending'])

    def test_transport_rewrite_is_rejected(self):
        self.repair()
        git(self.root, 'config', f'url.{self.origin}.insteadOf', str(self.fork))
        with patch.object(tx._pr, 'push') as push:
            self.assertEqual(self.publish(expected=2)['code'], 'publication_blocked')
            push.assert_not_called()

    def test_old_task_schema_does_not_gain_new_authority(self):
        path, _ = self.state()
        grant = json.loads(path.read_text())
        grant['schema'] = 'commit-transaction.task.v2'
        path.write_text(json.dumps(grant))
        self.publish(expected=2)

    def test_later_review_preserves_previous_alias(self):
        self.finish()
        self.authorize('Review this contributor PR again')
        later = 'pr/1/7/review02'
        self.bindings.write_text(json.dumps({'refs/heads/' + later: {'repository': 'org/base', 'number': 7}}))
        self.begin('create-pr', pr_base='trunk', target=[later], pr_bindings=str(self.bindings))
        self.run_helper(*self.task_args('checkout-target'), '--branch', later)
        self.assertEqual(git(self.root, 'rev-parse', self.alias), self.initial_head)
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD'), self.initial_head)

    def test_success_then_contributor_advance_reconciles_without_another_push(self):
        repaired = self.repair()
        real_push = tx._pr.push
        def advance(*args):
            self.assertTrue(real_push(*args))
            newer = git(self.root, 'commit-tree', f'{repaired}^{{tree}}', '-p', repaired, '-m', 'new contribution')
            git(self.root, 'push', str(self.fork), f'{newer}:refs/heads/topic')
            raise OSError('lost acknowledgement')
        with patch.object(tx._pr, 'push', side_effect=advance):
            self.assertEqual(self.publish(expected=2)['code'], 'effect_unknown')
        real_write = tx._atomic_json
        def interrupted(path, value):
            real_write(path, value)
            publication = value.get('targets', {}).get('refs/heads/' + self.alias, {}).get('publication', {})
            if publication.get('pending') is None and publication.get('published_head') == repaired:
                raise tx.TransactionError('simulated stop after durable settlement', 'effect_unknown')
        with patch.object(tx._pr, 'push', side_effect=AssertionError('duplicate push')):
            with patch.object(tx, '_atomic_json', side_effect=interrupted):
                self.assertEqual(self.publish(expected=2)['code'], 'effect_unknown')
        _, state = self.state()
        publication = state['targets']['refs/heads/' + self.alias]['publication']
        self.assertIsNone(publication['pending'])
        self.assertEqual(publication['published_head'], repaired)
        self.assertIsNotNone(publication['advanced_head'])
        self.pr_prepare(expected=2)
        self.publish(expected=2)
        self.run_helper(*self.task_args('finish'), '--outcome', 'completed', expected=2)
        self.run_helper(*self.task_args('complete-checkout'), '--mode', 'pause')
        self.run_helper(*self.task_args('finish'), '--outcome', 'cancelled')

    def test_push_url_rewrite_and_double_rewrite_stop(self):
        self.repair()
        git(self.root, 'config', f'url.{self.origin}.pushInsteadOf', str(self.fork))
        with patch.object(tx._pr, 'push') as push:
            self.assertEqual(self.publish(expected=2)['code'], 'publication_blocked')
            push.assert_not_called()
        git(self.root, 'config', '--unset', f'url.{self.origin}.pushInsteadOf')
        git(self.root, 'config', 'url.git@github.com:person/fork.git.insteadOf', str(self.fork))
        git(self.root, 'config', f'url.{self.origin}.insteadOf', 'git@github.com:person/fork.git')
        with patch.object(tx._pr, 'push') as push:
            self.assertEqual(self.publish(expected=2)['code'], 'publication_blocked')
            push.assert_not_called()


class OrdinaryPublicationTests(fixture.TransactionFixture):
    run_helper = PublicationTests.run_helper
    repair = PublicationTests.repair
    publish = PublicationTests.publish

    def setUp(self):
        super().setUp()
        self.origin = self.base / 'origin.git'
        git(self.root, 'init', '--bare', str(self.origin))
        git(self.root, 'remote', 'add', 'origin', str(self.origin))
        git(self.root, 'branch', 'trunk')
        git(self.root, 'push', 'origin', 'trunk')
        git(self.root, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk')
        self.base_head = git(self.root, 'rev-parse', 'HEAD')
        self.alias = 'feature/test'

    def intake(self):
        self.authorize('Create a PR for the selected branch')
        self.begin('create-pr', pr_base='trunk')

    def test_new_branch_requires_and_proves_publication(self):
        self.intake()
        head = self.repair()
        self.run_helper(*self.task_args('finish'), '--outcome', 'completed', expected=2)
        self.assertEqual(self.publish()['head'], head)
        self.assertEqual(git(self.origin, 'rev-parse', 'refs/heads/feature/test'), head)
        with patch.object(tx._pr, 'push', side_effect=AssertionError('duplicate push')):
            self.publish()
        self.run_helper(*self.task_args('complete-checkout'), '--mode', 'finalize')
        self.run_helper(*self.task_args('finish'), '--outcome', 'completed')

    def test_bound_same_repository_repair_publishes_original_pr(self):
        (self.root / 'contribution.txt').write_text('accepted contribution\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'contribution')
        git(self.root, 'push', 'origin', 'HEAD:refs/heads/feature/test')
        bindings = self.base / 'bindings.json'
        bindings.write_text(json.dumps({'refs/heads/' + self.alias: {'repository': 'org/base', 'number': 9}}))
        def api(path):
            if path == 'repos/org/base':
                return {'id': 1, 'default_branch': 'trunk', 'permissions': {'push': True}}
            self.assertEqual(path, 'repos/org/base/pulls/9')
            return {'number': 9, 'state': 'open', 'merged': False,
                    'base': {'ref': 'trunk', 'sha': self.base_head, 'repo': {'id': 1, 'full_name': 'org/base'}},
                    'head': {'ref': self.alias, 'sha': git(self.origin, 'rev-parse', 'refs/heads/' + self.alias),
                             'repo': {'id': 1, 'full_name': 'org/base'}}}
        with patch.object(tx._pr, 'api', side_effect=api), patch.object(tx._pr, 'repository_url', return_value=str(self.origin)):
            self.authorize('Review and repair the existing PR')
            self.begin('create-pr', pr_base='trunk', pr_bindings=str(bindings))
            repaired = self.repair()
            self.assertEqual(self.publish()['head'], repaired)
            self.assertEqual(git(self.origin, 'rev-parse', 'refs/heads/' + self.alias), repaired)

    def test_precommitted_unpublished_target_cannot_finish(self):
        (self.root / 'before.txt').write_text('before intake\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'before intake')
        self.intake()
        self.run_helper(*self.task_args('complete-checkout'), '--mode', 'finalize')
        self.assertEqual(self.run_helper(*self.task_args('finish'), '--outcome', 'completed', expected=2)['code'], 'effect_unknown')

    def test_multiple_push_destinations_block_before_effect(self):
        other = self.base / 'other.git'
        git(self.root, 'init', '--bare', str(other))
        git(self.root, 'config', '--add', 'remote.origin.pushurl', str(self.origin))
        git(self.root, 'config', '--add', 'remote.origin.pushurl', str(other))
        self.intake()
        self.repair()
        with patch.object(tx._pr, 'push') as push:
            self.assertEqual(self.publish(expected=2)['code'], 'publication_blocked')
            push.assert_not_called()

    def test_separate_push_repository_blocks_before_effect(self):
        other = self.base / 'other.git'
        git(self.root, 'init', '--bare', str(other))
        git(self.root, 'config', 'remote.origin.pushurl', str(other))
        self.intake()
        self.repair()
        with patch.object(tx._pr, 'push') as push:
            self.assertEqual(self.publish(expected=2)['code'], 'publication_blocked')
            push.assert_not_called()



class ReviewWorkspaceTests(fixture.TransactionFixture):
    setUp = PublicationTests.setUp
    api = PublicationTests.api
    run_helper = PublicationTests.run_helper
    repair = PublicationTests.repair
    publish = PublicationTests.publish

    def snapshot(self):
        return {
            "ref": git(self.source, "symbolic-ref", "HEAD"),
            "head": git(self.source, "rev-parse", "HEAD"),
            "refs": git(self.source, "show-ref"),
            "index": (self.source / ".git/index").read_bytes(),
            "files": {str(p.relative_to(self.source)): p.read_bytes()
                      for p in self.source.rglob("*") if p.is_file() and ".git" not in p.relative_to(self.source).parts},
        }

    def start_task(self):
        self.source = self.root
        (self.source / "staged.txt").write_text("user staged work\n")
        git(self.source, "add", "staged.txt")
        (self.source / "untracked.txt").write_text("user untracked work\n")
        self.before = self.snapshot()
        result = self.run_helper(*self.task_args("begin-review"), "--pr-base", "trunk", "--target", self.alias,
                                 "--pr-bindings", str(self.bindings))
        self.source_task = Path(result["task"])
        self.execution = result["executions"]["refs/heads/" + self.alias]
        self.root = Path(self.execution["repo_root"])
        self.task_contexts["session-1"] = self.execution

    def source_args(self, action):
        return (action, "--repo-root", str(self.source), "--session-id", "session-1",
                "--requested-action", "create-pr", "--intent-sha256", self.request_assertions["session-1"][-1])

    def close_source(self, *, expected=0):
        self.run_helper(*self.source_args("complete-checkout"), "--mode", "finalize")
        return self.run_helper(*self.source_args("finish"), "--outcome", "completed", expected=expected)

    def test_private_repair_publication_and_closure_preserve_concurrent_user_work(self):
        self.assertEqual(self.snapshot(), self.before)
        self.assertNotEqual(tx._common_dir(self.root), tx._common_dir(self.source))
        self.assertFalse((self.root / "staged.txt").exists())
        (self.source / "staged.txt").write_text("user kept editing\n")
        (self.source / "another.txt").write_text("more user work\n")
        expected = self.snapshot()
        repaired = self.repair()
        self.assertEqual(self.publish()["head"], repaired)
        closure = self.close_source()
        self.assertEqual(closure["heads"]["refs/heads/" + self.alias], repaired)
        self.assertEqual(self.snapshot(), expected)
        self.assertEqual(git(self.fork, "rev-parse", "topic"), repaired)
        self.run_helper(*self.source_args("cleanup-review"), "--branch", self.alias)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.snapshot(), expected)

    def test_retry_uses_registered_workspace_and_blocks_parent_mutation(self):
        again = self.run_helper(*self.source_args("begin-review"), "--pr-base", "trunk", "--target", self.alias,
                                "--pr-bindings", str(self.bindings))
        self.assertEqual(again["executions"]["refs/heads/" + self.alias]["repo_root"], str(self.root))
        blocked = self.run_helper(*self.source_args("checkout-target"), "--branch", self.alias, expected=2)
        self.assertEqual(blocked["code"], "scope_changed")
        self.assertEqual(self.snapshot(), self.before)

    def test_unpublished_repair_blocks_completion_and_retains_workspace(self):
        self.repair()
        self.assertEqual(self.close_source(expected=2)["code"], "effect_unknown")
        self.assertTrue(self.root.exists())
        self.assertEqual(self.snapshot(), self.before)

    def test_closed_original_revokes_execution(self):
        self.close_source()
        (self.root / "repair.txt").write_text("late edit\n")
        self.assertEqual(self.pr_prepare(expected=2)["code"], "task_closed")
        self.assertEqual(self.snapshot(), self.before)

    def test_execution_cannot_close_independently(self):
        result = self.run_helper(*self.task_args("finish"), "--outcome", "cancelled", expected=2)
        self.assertEqual(result["code"], "scope_changed")
        self.close_source()

    def test_forged_parent_binding_is_rejected(self):
        task = Path(self.execution["task"])
        grant = json.loads(task.read_text())
        grant["review_parent"]["sha256"] = "0" * 64
        tx._atomic_json(task, grant)
        (self.root / "repair.txt").write_text("repair\n")
        self.assertEqual(self.pr_prepare(expected=2)["code"], "scope_changed")

    def test_remote_advance_preserves_caller_and_never_overwrites(self):
        self.repair()
        self.allow = False
        self.assertEqual(self.publish(expected=2)["code"], "publication_blocked")
        self.assertEqual(self.snapshot(), self.before)

    def test_pause_never_switches_or_cleans_caller(self):
        self.run_helper(*self.source_args("complete-checkout"), "--mode", "pause")
        self.assertEqual(self.snapshot(), self.before)
        self.repair()
        self.publish()
        self.close_source()
        self.assertEqual(self.snapshot(), self.before)

    def test_cleanup_cannot_delete_unpublished_cancelled_repairs(self):
        self.repair()
        self.run_helper(*self.source_args("finish"), "--outcome", "cancelled")
        self.assertEqual(self.run_helper(*self.source_args("cleanup-review"), "--branch", self.alias, expected=2)["code"], "effect_unknown")
        self.assertTrue((self.root / "repair.txt").exists())

class ReviewHookTests(fixture.TransactionFixture):
    setUp = PublicationTests.setUp
    api = PublicationTests.api
    run_helper = PublicationTests.run_helper
    snapshot = ReviewWorkspaceTests.snapshot

    def start_task(self):
        self.source = self.root

    def reject_hook(self, hook_dir):
        hook_dir.mkdir(exist_ok=True)
        hook = hook_dir / "pre-commit"
        hook.write_text('#!/bin/sh\npwd > .git/hook-cwd\necho changed > "$(dirname "$0")/../../caller-sentinel"\nexit 0\n')
        hook.chmod(0o700)
        before = self.snapshot()
        result = self.run_helper(*self.task_args("begin-review"), "--pr-base", "trunk", "--target", self.alias,
                                 "--pr-bindings", str(self.bindings), expected=2)
        self.assertEqual(result["code"], "scope_changed")
        self.assertIn("executable trusted hooks", result["reason"])
        self.assertFalse((self.source / ".git/hook-cwd").exists())
        self.assertEqual(self.snapshot(), before)

    def test_source_relative_hook_writes_are_blocked_before_execution(self):
        self.reject_hook(self.root / ".git/hooks")

    def test_relative_hook_policy_is_blocked_before_execution(self):
        git(self.root, "config", "core.hooksPath", "operator-hooks")
        self.reject_hook(self.root / "operator-hooks")

    def test_new_hook_blocks_before_commit(self):
        ReviewWorkspaceTests.start_task(self)
        hook = self.source / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o700)
        (self.root / "repair.txt").write_text("repair\n")
        self.assertEqual(self.pr_prepare(expected=2)["code"], "scope_changed")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.initial_head)

    def test_private_hook_override_is_rejected(self):
        ReviewWorkspaceTests.start_task(self)
        git(self.root, "config", "core.hooksPath", str(self.source / ".git/hooks"))
        (self.root / "repair.txt").write_text("repair\n")
        self.assertEqual(self.pr_prepare(expected=2)["code"], "scope_changed")


class ReviewExecutableConfigTests(fixture.TransactionFixture):
    setUp = PublicationTests.setUp
    api = PublicationTests.api
    run_helper = PublicationTests.run_helper
    snapshot = ReviewWorkspaceTests.snapshot
    start_task = ReviewHookTests.start_task

    def reject_candidate_program(self, config, *, remote_condition=False):
        candidate = self.base / "candidate"
        git(self.source, "clone", "-b", "topic", str(self.fork), str(candidate))
        git(candidate, "config", "user.name", "Test User")
        git(candidate, "config", "user.email", "test@example.com")
        sentinel = self.codex_home / "candidate-executed"
        (candidate / "aa-candidate.sh").write_text(
            "#!/bin/sh\nprintf touched > " + shlex.quote(str(sentinel)) + "\ncat\n")
        (candidate / ".gitattributes").write_text("*.txt filter=reviewattack\n")
        git(candidate, "add", "-A")
        git(candidate, "commit", "-qm", "candidate program")
        git(candidate, "push", "origin", "topic")
        policy = self.base / "include-policy"
        command_policy = self.base / "candidate-policy"
        command_policy.write_text(config)
        condition = ("hasconfig:remote.*.url:" + str(self.origin) if remote_condition
                     else "gitdir:" + str(self.codex_home) + "/review-workspaces/")
        policy.write_text('[includeIf "' + condition + '"]\n'
                          ' path = ' + str(command_policy) + '\n')
        real = tx._run_git
        def with_policy(root, arguments, **kwargs):
            return real(root, ("-c", "include.path=" + str(policy), *arguments), **kwargs)
        before = self.snapshot()
        # A real Git includeIf is activated only in the private execution path.
        with patch.object(tx, "_run_git", side_effect=with_policy):
            result = self.run_helper(*self.task_args("begin-review"), "--pr-base", "trunk", "--target", self.alias,
                                     "--pr-bindings", str(self.bindings), expected=2)
        self.assertIn("executable Git", result["reason"])
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.snapshot(), before)

    def lfs_policy(self):
        sentinel = self.base / "lfs-executed"
        policy = self.base / "operator-lfs"
        command = "touch " + shlex.quote(str(sentinel))
        policy.write_text('[filter "lfs"]\n clean = ' + command + '\n smudge = ' + command
                          + '\n process = ' + command + '\n required = true\n')
        wrapper = self.base / "bin/git"
        wrapper.write_text(wrapper.read_text().replace("GIT_CONFIG_GLOBAL=/dev/null",
                           "GIT_CONFIG_GLOBAL=" + shlex.quote(str(policy))))
        return sentinel

    def test_unused_global_lfs_allows_private_repair_and_publication(self):
        sentinel = self.lfs_policy()
        ReviewWorkspaceTests.start_task(self)
        PublicationTests.repair(self)
        PublicationTests.publish(self)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.snapshot(), self.before)

    def test_lfs_in_exact_incoming_tree_is_rejected(self):
        candidate = self.base / "lfs-candidate"
        git(self.source, "clone", "-b", "topic", str(self.fork), str(candidate))
        git(candidate, "config", "user.name", "Test")
        git(candidate, "config", "user.email", "test@example.com")
        (candidate / ".gitattributes").write_text("*.txt filter=lfs\n")
        git(candidate, "add", "-A")
        git(candidate, "commit", "-qm", "LFS attributes")
        git(candidate, "push", "origin", "topic")
        sentinel = self.lfs_policy()
        before = self.snapshot()
        result = self.run_helper(*self.task_args("begin-review"), "--pr-base", "trunk", "--target", self.alias,
                                 "--pr-bindings", str(self.bindings), expected=2)
        self.assertIn("LFS content is not supported", result["reason"])
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.snapshot(), before)

    def test_lfs_working_and_index_attributes_block_repair(self):
        sentinel = self.lfs_policy()
        ReviewWorkspaceTests.start_task(self)
        attributes = self.root / ".gitattributes"
        attributes.write_text("*.txt filter=lfs\n")
        (self.root / "new.txt").write_text("new binary placeholder\n")
        result = self.pr_prepare(expected=2)
        self.assertIn("LFS content is not supported", result["reason"])
        blob = git(self.root, "hash-object", "-w", str(attributes))
        # Fixture-only index setup; never execute the ambient sentinel filter.
        git(self.root, "-c", "filter.lfs.process=", "-c", "filter.lfs.clean=",
            "-c", "filter.lfs.required=false", "update-index", "--add", "--cacheinfo",
            "100644," + blob + ",.gitattributes")
        attributes.unlink()
        result = self.pr_prepare(expected=2)
        self.assertIn("LFS content is not supported", result["reason"])
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.snapshot(), self.before)

    def test_required_lfs_driver_fences_missed_attribute_check(self):
        sentinel = self.lfs_policy()
        ReviewWorkspaceTests.start_task(self)
        (self.root / ".gitattributes").write_text("*.txt filter=lfs\n")
        (self.root / "new.txt").write_text("must not stage raw content\n")
        before = (self.root / ".git/index").read_bytes()
        with patch.object(tx._review, "no_lfs"):
            result = tx._run_git(self.root, ("-c", "filter.lfs.required=false", "add", "-A"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"filter", result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertEqual((self.root / ".git/index").read_bytes(), before)

    def test_private_path_filter_cannot_execute_candidate_with_operator_access(self):
        self.reject_candidate_program('[filter "reviewattack"]\n smudge = sh aa-candidate.sh\n')

    def test_private_path_fsmonitor_cannot_execute_candidate_with_operator_access(self):
        self.reject_candidate_program('[core]\n fsmonitor = sh aa-candidate.sh\n')

    def test_new_origin_conditional_filter_is_blocked_before_checkout(self):
        self.reject_candidate_program('[filter "reviewattack"]\n smudge = sh aa-candidate.sh\n', remote_condition=True)

    def test_later_filter_policy_is_rejected(self):
        ReviewWorkspaceTests.start_task(self)
        git(self.root, "config", "filter.reviewattack.process", "sh aa-candidate.sh")
        (self.root / "repair.txt").write_text("repair\n")
        self.assertEqual(self.pr_prepare(expected=2)["code"], "scope_changed")


class NestedReviewWorkspaceTests(fixture.TransactionFixture):
    setUp = PublicationTests.setUp
    api = PublicationTests.api
    run_helper = PublicationTests.run_helper
    repair = PublicationTests.repair
    publish = PublicationTests.publish
    snapshot = ReviewWorkspaceTests.snapshot
    source_args = ReviewWorkspaceTests.source_args
    close_source = ReviewWorkspaceTests.close_source

    def start_task(self):
        self.source = self.root
        parent = self.begin("create-pr", pr_base="trunk", target=[self.alias], pr_bindings=str(self.bindings))
        self.source_task = Path(parent["task"])
        (self.source / "user.txt").write_text("concurrent user work\n")
        self.before = self.snapshot()
        self.execution = self.run_helper(*self.task_args("open-review"), "--branch", self.alias)
        self.root = Path(self.execution["repo_root"])
        self.task_contexts["session-1"] = self.execution

    def test_nested_repair_closes_original_without_importing_new_head(self):
        self.repair()
        self.publish()
        proof = tx._review.verify_execution(tx._module(), Path(self.execution["task"]), self.source,
                                           "session-1", branch=self.alias, head=git(self.root,"rev-parse","HEAD"),
                                           repository="org/base", pr=7)
        self.assertEqual(proof["repo_root"], str(self.root))
        with self.assertRaises(tx.TransactionError):
            tx._review.verify_execution(tx._module(), Path(self.execution["task"]), self.source,
                                        "session-1", branch=self.alias, head=proof["head"], repository="org/base", pr=99)
        self.close_source()
        self.assertEqual(self.snapshot(), self.before)

    def test_registered_directory_substitution_is_rejected(self):
        replacement = self.root.parent / "replaced"
        self.root.rename(replacement)
        import shutil
        shutil.copytree(replacement, self.root)
        self.assertEqual(self.run_helper(*self.source_args("review-context"), "--branch", self.alias, expected=2)["code"], "scope_changed")
        self.assertEqual(self.snapshot(), self.before)

    def test_cleanup_replays_after_delete_before_checkpoint(self):
        self.close_source()
        real_write = tx._atomic_json
        def interrupt(path, value):
            if value.get("reviews", {}).get("refs/heads/" + self.alias, {}).get("phase") == "removed":
                raise RuntimeError("simulated helper interruption")
            return real_write(path, value)
        with patch.object(tx, "_atomic_json", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.run_helper(*self.source_args("cleanup-review"), "--branch", self.alias)
        self.assertFalse(self.root.exists())
        result = self.run_helper(*self.source_args("cleanup-review"), "--branch", self.alias)
        self.assertEqual(result["status"], "removed")
        self.assertEqual(self.snapshot(), self.before)

    def test_cleanup_rejects_replacement_after_removal_intent(self):
        self.close_source()
        with patch.object(tx._review.shutil, "rmtree", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.run_helper(*self.source_args("cleanup-review"), "--branch", self.alias)
        directory = self.root.parent
        original = directory.with_name(directory.name + "-original")
        directory.rename(original)
        directory.mkdir(mode=0o700)
        sentinel = directory / "unrelated.txt"
        sentinel.write_text("keep replacement")
        self.run_helper(*self.source_args("cleanup-review"), "--branch", self.alias, expected=2)
        self.assertEqual(sentinel.read_text(), "keep replacement")



class ReviewAllocationTests(fixture.TransactionFixture):
    setUp = PublicationTests.setUp
    api = PublicationTests.api
    run_helper = PublicationTests.run_helper
    start_task = PublicationTests.start_task
    # Only allocation tests run here; inherited publication regressions continue
    # on the original PublicationTests fixture below through default start_task.
    def test_allocation_failure_can_retry_from_owned_empty_directory(self):
        source = self.root
        # This existing fixture already checked out its target; review itself
        # must preserve that exact checkout, including dirty files.
        args = self.task_args("open-review")
        real_git = tx._run_git
        def fail_init(root, words, **kwargs):
            if words[0] == "init":
                raise tx.TransactionError("simulated allocation interruption")
            return real_git(root, words, **kwargs)
        with patch.object(tx, "_run_git", side_effect=fail_init):
            self.run_helper(*args, "--branch", self.alias, expected=2)
        result = self.run_helper(*args, "--branch", self.alias)
        self.assertNotEqual(result["repo_root"], str(source))

    def test_ordinary_parent_returns_to_original_after_private_review(self):
        original = "feature/test"
        self.assertEqual(git(self.root, "symbolic-ref", "--short", "HEAD"), self.alias)
        self.run_helper(*self.task_args("open-review"), "--branch", self.alias)
        completion = self.run_helper(*self.task_args("complete-checkout"), "--mode", "finalize")
        self.assertEqual(git(self.root, "symbolic-ref", "--short", "HEAD"), original)
        self.assertEqual(completion["checkout"], "refs/heads/" + original)
        self.run_helper(*self.task_args("finish"), "--outcome", "completed")

    def test_dirty_parent_blocks_its_own_switch_but_never_discards_edits(self):
        self.run_helper(*self.task_args("open-review"), "--branch", self.alias)
        (self.root / "user.txt").write_text("keep this\n")
        self.run_helper(*self.task_args("complete-checkout"), "--mode", "finalize", expected=2)
        self.assertEqual((self.root / "user.txt").read_text(), "keep this\n")
        self.assertEqual(git(self.root, "symbolic-ref", "--short", "HEAD"), self.alias)

    def test_failed_allocation_can_cancel_original_authority(self):
        with patch.object(tx._review, "allocate", side_effect=tx.TransactionError("fixture fetch failure")):
            self.run_helper(*self.task_args("open-review"), "--branch", self.alias, expected=2)
        result = self.run_helper(*self.task_args("finish"), "--outcome", "cancelled")
        self.assertEqual(result["status"], "cancelled")


if __name__ == '__main__':
    unittest.main()
