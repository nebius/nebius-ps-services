#!/usr/bin/env python3
"""Real disposable Git transports with mocked GitHub identity and permissions."""

from __future__ import annotations

from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
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
        super().setUp()
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


if __name__ == '__main__':
    unittest.main()
