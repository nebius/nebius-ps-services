"""Synthetic-only regression checks for information boundaries."""
import contextlib
import gzip
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / 'tools/check_boundaries.py'
spec = importlib.util.spec_from_file_location('check_boundaries', TOOL)
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)


class BoundaryTests(unittest.TestCase):
    def codes(self, name, content, destination='public', values=()):
        scanner = b.Scanner(destination, values)
        scanner.content(name, content.encode() if isinstance(content, str) else content)
        return {f.code for f in scanner.findings}

    def invoke(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = b.main(args)
        return code, json.loads(out.getvalue())

    def test_synthetic_customer_shaped_content_is_not_classified(self):
        payload = json.dumps({'name': 'Alex Example', 'email': 'alex@example.org',
                              'message': 'My workload failed', 'account_number': '1234567'})
        self.assertEqual(self.codes('fixture.json', payload), set())

    def test_public_placeholders_and_secret_references(self):
        for value in ('<project-id>', 'EXAMPLE_PROJECT', '${PROJECT_ID}'):
            self.assertEqual(self.codes('example.yaml', f'project_id: "{value}"\n'), set())
        self.assertEqual(self.codes('example.json', json.dumps({'secret_ref': {'name': '<secret-name>', 'key': 'token'}})), set())

    def test_private_configuration_is_private(self):
        text = 'project_id: project-example-deployment\n'
        self.assertIn('PRIVATE_CONFIGURATION', self.codes('config.yaml', text))
        self.assertEqual(self.codes('clusters/dev/config.yaml', text, 'private'), set())

    def test_compact_json_and_private_environment_path(self):
        text = json.dumps({'nested': {'project_id': 'synthetic-project-id'}})
        self.assertIn('PRIVATE_CONFIGURATION', self.codes('config.json', text))
        self.assertIn('PRIVATE_ENVIRONMENT_PATH', self.codes('shared.json', text, 'private'))
        text = json.dumps({'environment': 'prod'})
        self.assertIn('ENVIRONMENT_PATH_MISMATCH', self.codes('clusters/dev/config.json', text, 'private'))

    def test_yaml_environment_variants(self):
        for text in ('environment: prod # target', '"environment": "prod"',
                     "{'environment': 'prod', 'note': 'synthetic'}"):
            self.assertIn('ENVIRONMENT_PATH_MISMATCH', self.codes('clusters/dev/config.yaml', text, 'private'))

    def test_malformed_binding_types_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            desired = {key: 'synthetic-' + key for key in ('environment', 'project_id', 'cluster_id',
                       'state_namespace', 'object_namespace', 'backup_namespace', 'secret_namespace')}
            for invalid in ([], {}, None, 12):
                desired['environment'] = invalid
                path = Path(directory) / 'binding.json'
                path.write_text(json.dumps(desired))
                code, result = self.invoke(['verify-target', '--desired', str(path), '--observed', str(path)])
                self.assertEqual(code, 1)
                self.assertEqual(result['codes'], ['BINDING_SHAPE'])

    def test_known_private_value_is_redacted(self):
        needle = 'synthetic-' + 'private-value-canary'
        scanner = b.Scanner('public', (needle,))
        scanner.content(needle + '.txt', needle.encode())
        encoded = json.dumps([b.asdict(f) for f in scanner.findings])
        self.assertNotIn(needle, encoded)
        self.assertIn('PRIVATE_VALUE', encoded)

    def test_secret_payload_in_each_destination(self):
        payload = json.dumps({'api' + '_key': 'deliberately-invalid-canary'})
        for destination in ('public', 'private', 'application-bundle'):
            self.assertIn('CREDENTIAL_VALUE', self.codes('application.json', payload, destination))

    def test_nested_json_credentials(self):
        payload = json.dumps({'items': [{'client_' + 'secret': 'invalid-canary'}]})
        self.assertIn('CREDENTIAL_VALUE', self.codes('values.json', payload, 'private'))

    def test_private_key_in_arbitrary_source(self):
        text = 'prefix\n-----BEGIN ' + 'PRIVATE KEY-----\ninvalid-canary'
        self.assertIn('PRIVATE_KEY', self.codes('example.py', text, 'private'))

    def test_kubernetes_secret_payload(self):
        text = 'kind: Secret\nstringData:\n  arbitrary: invalid-canary\n'
        self.assertIn('SECRET_PAYLOAD_REVIEW_REQUIRED', self.codes('secret.yaml', text, 'private'))

    def test_yaml_secret_serialization_variants(self):
        kind = 'Sec' + 'ret'
        for text in (f'kind: "{kind}"\ndata: {{arbitrary: invalid-canary}}',
                     f"- kind: '{kind}' # comment\n  stringData: {{}}",
                     json.dumps({'kind': kind, 'data': {'arbitrary': 'invalid-canary'}})):
            self.assertIn('SECRET_PAYLOAD_REVIEW_REQUIRED', self.codes('secret.yaml', text, 'private'))

    def test_compressed_tar_expansion_is_bounded(self):
        compressed = gzip.compress(b'x' * 2048)
        with mock.patch.object(b, 'MAX_ARCHIVE_BYTES', 1024):
            self.assertIn('UNINSPECTED_ARCHIVE_LIMIT', self.codes('large.tgz', compressed))

    def test_archive_policy_uses_member_path(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('wireguard-clients/client.conf', 'synthetic')
        self.assertIn('FORBIDDEN_RUNTIME_FILE', self.codes('build.zip', buffer.getvalue()))

    def test_unreadable_directory_is_not_silently_skipped(self):
        def walk(root, **kwargs):
            kwargs['onerror'](PermissionError('private path'))
            return iter([])
        scanner = b.Scanner('public')
        with mock.patch.object(b.os, 'walk', side_effect=walk):
            b.scan_tree(scanner, Path('/synthetic'), False)
        self.assertEqual({f.code for f in scanner.findings}, {'UNINSPECTED_DIRECTORY'})

    def test_git_disables_lazy_fetch(self):
        with mock.patch.object(b.subprocess, 'run') as run:
            run.return_value.stdout = b''
            b.git(Path('.'), 'status')
            self.assertEqual(run.call_args.kwargs['env']['GIT_NO_LAZY_FETCH'], '1')

    def test_private_history_uses_original_policy_path(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['git', 'init', '-q', directory], check=True)
            root = Path(directory)
            path = root / 'clusters/dev/config.yaml'
            path.parent.mkdir(parents=True)
            path.write_text('project_id: synthetic-deployment\n')
            subprocess.run(['git', '-C', directory, 'add', '-A'], check=True)
            subprocess.run(['git', '-C', directory, '-c', 'user.name=Synthetic Test',
                            '-c', 'user.email=test@example.org', 'commit', '-qm', 'fixture'], check=True)
            scanner = b.Scanner('private')
            b.scan_history(scanner, root, 'HEAD')
            self.assertEqual(scanner.findings, set())

    def test_merged_branch_deleted_content_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, '-c', 'user.name=Synthetic Test',
                                                '-c', 'user.email=test@example.org', *args])
            git('init', '-q', '-b', 'main')
            (root / 'product').mkdir()
            (root / 'product/README.md').write_text('synthetic')
            git('add', '-A')
            git('commit', '-qm', 'base')
            base = git('rev-parse', 'HEAD').decode().strip()
            git('checkout', '-qb', 'side')
            (root / 'product/saved.tfstate').write_text('{}')
            git('add', '-A')
            git('commit', '-qm', 'unsafe')
            (root / 'product/saved.tfstate').unlink()
            git('add', '-A')
            git('commit', '-qm', 'remove')
            git('checkout', '-q', 'main')
            git('merge', '--no-ff', '-qm', 'merge', 'side')
            (root / 'product/README.md').write_text('changed')
            git('add', '-A')
            git('commit', '-qm', 'visible')
            scanner = b.Scanner('public')
            b.scan_history(scanner, root / 'product', base + '..HEAD')
            self.assertIn('FORBIDDEN_RUNTIME_FILE', {f.code for f in scanner.findings})

    def test_forbidden_runtime_files_in_both_repositories(self):
        for destination in ('public', 'private'):
            for name in ('state.tfstate', 'state.tfstate.backup', 'saved.tfplan', 'plan.out',
                         'data.dump', 'kubeconfig', 'key.p12', 'runtime.log', '.env',
                         'wireguard-clients/client.conf', '.terraform/credentials.json'):
                with self.subTest(destination=destination, name=name):
                    self.assertIn('FORBIDDEN_RUNTIME_FILE', self.codes(name, '{}', destination))

    def test_detached_release_selection_not_in_configuration_payload(self):
        for suffix in ('.json', '.yaml', '.yml'):
            self.assertIn('BUNDLE_PATH_NOT_ALLOWED', self.codes(
                'release' + suffix, '{}', 'application-bundle'))
        self.assertEqual(self.codes('application.json', '{}', 'application-bundle'), set())

    def test_private_generated_infrastructure_not_application_bundle(self):
        text = 'module "cluster" { source = "./modules/cluster" }\n'
        name = 'clusters/dev/example/generated/infra/main.tf'
        self.assertEqual(self.codes(name, text, 'private'), set())
        self.assertIn('BUNDLE_PATH_NOT_ALLOWED', self.codes(name, text, 'application-bundle'))
        self.assertIn('BUNDLE_EXECUTABLE', self.codes(name, text, 'application-bundle'))

    def test_binary_and_oversize_fail_closed(self):
        self.assertIn('UNINSPECTED_BINARY', self.codes('blob', b'\x00\xff'))
        self.assertIn('UNINSPECTED_SIZE_LIMIT', self.codes('large', b'x' * (b.MAX_BYTES + 1)))

    def test_private_endpoints_and_url_passwords(self):
        for value in ('https://' + '10.2.3.4/path', 'https://' + 'service.internal/path',
                      'https://' + 'user:invalid-canary@example.org/path'):
            self.assertIn('PRIVATE_OR_CREDENTIALED_URL', self.codes('doc.md', value))

    def test_zip_nested_payload_and_path_traversal(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('config.json', json.dumps({'password': 'invalid-canary'}))
            archive.writestr('../escape', 'synthetic')
        codes = self.codes('build.zip', buffer.getvalue())
        self.assertIn('CREDENTIAL_VALUE', codes)
        self.assertIn('UNSAFE_ARCHIVE_MEMBER', codes)

    def test_tar_layer_is_scanned_without_extraction(self):
        buffer = io.BytesIO()
        payload = ('-----BEGIN ' + 'PRIVATE KEY-----').encode()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            member = tarfile.TarInfo('removed-later/key.txt')
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        self.assertIn('PRIVATE_KEY', self.codes('layer.tar', buffer.getvalue()))

    def test_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'link').symlink_to('/etc/passwd')
            scanner = b.Scanner('public')
            b.scan_tree(scanner, root, False)
            self.assertEqual({f.code for f in scanner.findings}, {'SYMLINK_NOT_ALLOWED'})

    def test_tracked_ignored_file_is_still_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', directory], check=True)
            (root / 'saved.tfstate').write_text('{}')
            subprocess.run(['git', '-C', directory, 'add', 'saved.tfstate'], check=True)
            (root / '.gitignore').write_text('*.tfstate\n')
            scanner = b.Scanner('private')
            b.scan_tree(scanner, root, True)
            self.assertIn('FORBIDDEN_RUNTIME_FILE', {f.code for f in scanner.findings})

    def test_history_checks_deleted_sensitive_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', directory], check=True)
            def commit():
                subprocess.run(['git', '-C', directory, 'add', '-A'], check=True)
                subprocess.run(['git', '-C', directory, '-c', 'user.name=Synthetic Test',
                                '-c', 'user.email=test@example.org', 'commit', '-qm', 'fixture'], check=True)
            (root / 'saved.tfstate').write_text('{}')
            commit()
            (root / 'saved.tfstate').unlink()
            (root / 'README.md').write_text('safe')
            commit()
            scanner = b.Scanner('public')
            b.scan_history(scanner, root, 'HEAD')
            self.assertIn('FORBIDDEN_RUNTIME_FILE', {f.code for f in scanner.findings})

    def test_binding_mismatch_for_each_identity(self):
        desired = {k: 'synthetic-' + k for k in ('project_id', 'cluster_id', 'state_namespace',
                   'object_namespace', 'backup_namespace', 'secret_namespace')}
        desired['environment'] = 'dev'
        self.assertEqual(b.binding_errors(desired, dict(desired)), [])
        for key in desired:
            observed = dict(desired)
            observed[key] = 'prod' if key == 'environment' else 'different-' + key
            self.assertEqual(b.binding_errors(desired, observed), ['BINDING_MISMATCH'])

    def test_unconfigured_and_unknown_binding_rejected(self):
        self.assertEqual(b.binding_errors({'environment': 'prod'}, {}), ['BINDING_SHAPE'])
        self.assertEqual(b.binding_errors([], {}), ['BINDING_SHAPE'])

    def test_invalid_input_reports_no_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = root / 'canary.json'
            values.write_text('confidential-invalid-json')
            code, result = self.invoke(['scan', '--root', directory, '--destination', 'public', '--private-values', str(values)])
            self.assertEqual(code, 2)
            self.assertEqual(result, {'codes': ['CHECK_INPUT_ERROR']})

    def test_tool_scans_its_own_source(self):
        self.assertEqual(self.codes('check_boundaries.py', TOOL.read_text()), set())


if __name__ == '__main__':
    unittest.main()
