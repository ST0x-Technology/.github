"""Exercise the release workflow's digest gate without cloud credentials."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/app-release.yml'
AR_REPO = 'europe-west3-docker.pkg.dev/test-project/test-repo'
COMMIT = 'a' * 40
DIGEST = 'sha256:' + '1' * 64
OTHER_DIGEST = 'sha256:' + '2' * 64
STATE = b'{"schema_version":1,"proposal_id":"proposal-1","overrides":[]}'


def tag(name, digest=DIGEST):
    return {'tag': f'projects/p/locations/l/repositories/r/packages/bot/tags/{name}',
            'version': f'projects/p/locations/l/repositories/r/packages/bot/versions/{digest}'}


class DigestGateTest(unittest.TestCase):
    def run_gate(self, tags, version=COMMIT, registry_status=0, attested=True):
        step = WORKFLOW.read_text().split('      - name: Resolve and verify digests\n', 1)[1]
        script = textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'gcloud'
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args == ['artifacts', 'docker', 'tags', 'list', os.environ['AR_REPO'] + '/bot', '--format=json']:
    if int(os.environ['REGISTRY_STATUS']):
        print('PERMISSION_DENIED: registry unavailable', file=sys.stderr)
        sys.exit(int(os.environ['REGISTRY_STATUS']))
    print(os.environ['TAGS'])
elif args[:4] == ['beta', 'container', 'binauthz', 'attestations']:
    assert '--artifact-url=' + os.environ['AR_REPO'] + '/bot@' + os.environ['EXPECTED_DIGEST'] in args
    if os.environ['ATTESTED'] == '1':
        print('projects/p/occurrences/verified')
else:
    print('unexpected gcloud call: ' + repr(args), file=sys.stderr)
    sys.exit(99)
''')
            fake.chmod(0o755)
            (root / 'git').write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_COMMIT"\n')
            (root / 'git').chmod(0o755)
            env = os.environ | {
                'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'AR_REPO': AR_REPO, 'IMAGES': 'bot=BOT_IMAGE',
                'ATTESTOR': 'projects/test-project/attestors/test',
                'VERSION': version, 'TEST_COMMIT': COMMIT,
                'RUNNER_TEMP': directory, 'GITHUB_OUTPUT': str(root / 'output'),
                'TAGS': json.dumps(tags), 'CALLS': str(root / 'calls'),
                'REGISTRY_STATUS': str(registry_status), 'ATTESTED': str(int(attested)),
                'EXPECTED_DIGEST': DIGEST,
            }
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                    env=env, text=True, capture_output=True)
            want = root / 'want.env'
            return result, want.read_text() if want.exists() else None

    def test_commit_resolves_and_checks_attestation(self):
        result, want = self.run_gate([tag(COMMIT), tag(COMMIT + '-extra', OTHER_DIGEST)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(want, f'BOT_IMAGE={AR_REPO}/bot@{DIGEST}\n')

    def test_version_must_match_built_commit(self):
        result, want = self.run_gate([tag('v1.11.0'), tag(COMMIT)], version='v1.11.0')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNotNone(want)

    def test_moved_version_is_rejected(self):
        result, want = self.run_gate([tag('v1.11.0'), tag(COMMIT, OTHER_DIGEST)], version='v1.11.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('version label has been moved', result.stdout)
        self.assertIsNone(want)

    def test_registry_denial_stops_release(self):
        result, want = self.run_gate([tag(COMMIT)], registry_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PERMISSION_DENIED', result.stdout)
        self.assertIsNone(want)

    def test_missing_ambiguous_and_invalid_digests_are_rejected(self):
        for tags in ([], [tag(COMMIT), tag(COMMIT)], [tag(COMMIT, 'invalid')]):
            with self.subTest(tags=tags):
                result, want = self.run_gate(tags)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(want)

    def test_unattested_image_is_rejected(self):
        result, want = self.run_gate([tag(COMMIT)], attested=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('NO attestation', result.stdout)
        self.assertIsNone(want)


LEGACY_LINE = 'RUNTIME STATE: legacy rollback to v1.0.0; validation disabled'


class PamGrantTest(unittest.TestCase):
    def run_gate(self, state, legacy=False, justification='', poll_state='ACTIVE',
                 config_secret=''):
        step = WORKFLOW.read_text().split('      - name: Await PAM approval\n', 1)[1]
        script = textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'gcloud'
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:3] == ['pam', 'grants', 'list']:
    if os.environ['OLD_STATE']:
        print('old-grant ' + os.environ['OLD_STATE'])
elif args[:3] == ['pam', 'grants', 'describe'] and 'justification' in args[-1]:
    print(os.environ['OLD_JUSTIFICATION'])
elif args[:3] == ['pam', 'grants', 'describe']:
    print(os.environ['POLL_STATE'])
elif args[:3] == ['pam', 'grants', 'create']:
    print('new-grant')
elif args[:2] == ['storage', 'cat']:
    pass
else:
    print('unexpected gcloud call: ' + repr(args), file=sys.stderr)
    sys.exit(99)
''')
            fake.chmod(0o755)
            (root / 'git').write_text('#!/bin/sh\nprintf "replacement release\\n"\n')
            (root / 'git').chmod(0o755)
            (root / 'sleep').write_text('#!/bin/sh\nexit 0\n')
            (root / 'sleep').chmod(0o755)
            (root / 'want.env').write_text('')
            calls = root / 'calls'
            env = os.environ | {
                'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'CALLS': str(calls), 'OLD_STATE': state,
                'OLD_JUSTIFICATION': justification, 'POLL_STATE': poll_state,
                'ENTITLEMENT': 'app-deploy', 'RUNTIME': 'bot-vm',
                'CONFIG_FILE': 'config.toml' if config_secret else '',
                'CONFIG_SECRET': config_secret, 'CONFIG_BUCKET': 'bucket',
                'RUNNER_TEMP': directory, 'GITHUB_STEP_SUMMARY': str(root / 'summary'),
                'GITHUB_ENV': str(root / 'environment'),
                'GITHUB_SERVER_URL': 'https://github.com',
                'GITHUB_REPOSITORY': 'org/app', 'GITHUB_RUN_ID': '123',
                'GITHUB_ACTOR': 'operator', 'GITHUB_REF_NAME': 'master',
                'CONFIG_REUSE': '', 'CONFIG_PREVIOUS': '1', 'CONFIG_DIFFSTAT': 'unchanged',
                'SERVICE': '', 'PROJECT': 'production', 'IMAGES': '',
                'VERSION': 'v1.0.0' if legacy else '',
                'RUNTIME_STATE_LEGACY_ROLLBACK': 'true' if legacy else 'false',
            }
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                    env=env, text=True, capture_output=True)
            recorded = [json.loads(line) for line in calls.read_text().splitlines()]
            return result, recorded

    @staticmethod
    def creates(calls):
        return [call for call in calls if call[:3] == ['pam', 'grants', 'create']]

    def test_open_grants_are_reused(self):
        for state in ('ACTIVE', 'APPROVAL_AWAITED', 'SCHEDULED', 'ACTIVATING'):
            with self.subTest(state=state):
                result, calls = self.run_gate(state)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.creates(calls), [])

    def test_terminal_and_unknown_states_stop_release(self):
        for state in ('DENIED', 'REVOKED', 'ENDED', 'EXPIRED', 'WITHDRAWN',
                      'ACTIVATION_FAILED', 'SOMETHING_NEW', ''):
            with self.subTest(state=state):
                result, _ = self.run_gate('', poll_state=state)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('deploy refused', result.stdout)

    def test_legacy_rollback_reuses_grant_that_discloses_it(self):
        justification = 'PRODUCTION DEPLOY bot VM in production from org/app.\n' + LEGACY_LINE
        result, calls = self.run_gate('ACTIVE', legacy=True, justification=justification)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.creates(calls), [])

    def test_legacy_rollback_refuses_grant_without_disclosure(self):
        result, calls = self.run_gate('ACTIVE', legacy=True,
                                      justification='PRODUCTION DEPLOY bot VM in production.')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not disclose a legacy rollback to v1.0.0', result.stdout)
        self.assertEqual(self.creates(calls), [])

    def test_legacy_rollback_ignores_disclosure_text_outside_line_two(self):
        justification = 'PRODUCTION DEPLOY bot VM in production from org/app.\n' + \
            'Approving lets the releaser roll the plane once.\n\nCHANGE: ' + LEGACY_LINE
        result, calls = self.run_gate('ACTIVE', legacy=True, justification=justification)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not disclose a legacy rollback to v1.0.0', result.stdout)
        self.assertEqual(self.creates(calls), [])

    def test_legacy_disclosure_survives_justification_cut(self):
        result, calls = self.run_gate('', legacy=True, config_secret='s' * 1000)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        [create] = self.creates(calls)
        justification = next(arg for arg in create if arg.startswith('--justification='))
        self.assertLessEqual(len(justification), len('--justification=') + 950)
        self.assertEqual(justification.split('\n')[1], LEGACY_LINE)


class InputsSanityTest(unittest.TestCase):
    def run_sanity(self, image_tag, allowlist='v0.9.0 v1.0.0', pointer='gs://bucket/active.json'):
        step = WORKFLOW.read_text().split('      - name: Inputs sanity\n', 1)[1]
        script = textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'gcloud').write_text('#!/bin/sh\nexit 0\n')
            (root / 'gcloud').chmod(0o755)
            env = os.environ | {
                'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'RUNTIME': 'cloud-run', 'SERVICE': 'app', 'REGION': 'europe-west3',
                'CONFIG_FILE': '', 'CONFIG_SECRET': '', 'CONFIG_MOUNT': '', 'CONFIG_BUCKET': '',
                'RUNTIME_STATE_POINTER': pointer, 'RUNTIME_STATE_SECRET': '',
                'RUNTIME_STATE_VALIDATE_COMMAND': '',
                'RUNTIME_STATE_LEGACY_ROLLBACK': 'true',
                'RUNTIME_STATE_LEGACY_VERSIONS': allowlist,
                'IMAGE_TAG_INPUT': image_tag, 'AR_REPO': AR_REPO,
                'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_NAME': 'main',
                'GITHUB_ENV': str(root / 'environment'),
            }
            return subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                  env=env, text=True, capture_output=True)

    def test_allowlisted_tag_may_skip_validation(self):
        result = self.run_sanity('v1.0.0')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('runtime-state validation is disabled for v1.0.0', result.stdout)

    def test_tag_outside_allowlist_is_rejected(self):
        for tag in ('v1.1.0', 'v1.0'):
            with self.subTest(tag=tag):
                result = self.run_sanity(tag)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('is not an allowed pre-runtime-state release', result.stdout)

    def test_legacy_rollback_needs_tag_and_pointer(self):
        result = self.run_sanity('')
        self.assertNotEqual(result.returncode, 0)
        result = self.run_sanity('v1.0.0', pointer='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('legacy runtime-state rollback needs runtime_state_pointer', result.stdout)


class CloudRunDeployTest(unittest.TestCase):
    def run_deploy(self, generation_after_denial):
        step = WORKFLOW.read_text().split('      - name: Deploy (Cloud Run)\n', 1)[1]
        script = textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'gcloud'
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
generation = Path(os.environ['GENERATION_FILE'])
if args[:3] == ['storage', 'objects', 'describe']:
    print(generation.read_text())
elif args[:3] == ['run', 'services', 'update']:
    if not os.path.exists(os.environ['DENIED']):
        open(os.environ['DENIED'], 'w').close()
        generation.write_text(os.environ['GENERATION_AFTER_DENIAL'])
        print('PERMISSION_DENIED: grant not propagated', file=sys.stderr)
        sys.exit(1)
elif args[:3] == ['run', 'services', 'describe']:
    print('revision-2 ' + os.environ['IMAGE'])
else:
    print('unexpected gcloud call: ' + repr(args), file=sys.stderr)
    sys.exit(99)
''')
            fake.chmod(0o755)
            (root / 'sleep').write_text('#!/bin/sh\nexit 0\n')
            (root / 'sleep').chmod(0o755)
            image = 'registry/app@sha256:' + 'a' * 64
            (root / 'want.env').write_text(f'IMAGE={image}\n')
            (root / 'generation').write_text('42')
            calls = root / 'calls'
            env = os.environ | {
                'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'CALLS': str(calls), 'GENERATION_FILE': str(root / 'generation'),
                'GENERATION_AFTER_DENIAL': generation_after_denial,
                'DENIED': str(root / 'denied'), 'IMAGE': image,
                'RUNNER_TEMP': directory, 'GITHUB_STEP_SUMMARY': str(root / 'summary'),
                'SERVICE': 'app', 'REGION': 'europe-west3', 'CONFIG_FILE': '',
                'ENTITLEMENT': 'app-deploy', 'CONFIG_VERSION': '',
                'RUNTIME_STATE_POINTER': 'gs://bucket/active.json',
                'RUNTIME_STATE_GENERATION': '42',
                'RUNTIME_STATE_LEGACY_ROLLBACK': 'false',
            }
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                    env=env, text=True, capture_output=True)
            recorded = [json.loads(line) for line in calls.read_text().splitlines()]
            updates = [call for call in recorded if call[:3] == ['run', 'services', 'update']]
            return result, updates

    def test_retry_deploys_when_pointer_is_unchanged(self):
        result, updates = self.run_deploy('42')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(updates), 2)

    def test_pointer_change_during_retry_blocks_next_update(self):
        result, updates = self.run_deploy('43')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('runtime-state pointer changed after validation', result.stdout)
        self.assertEqual(len(updates), 1)


class RuntimeStateGateTest(unittest.TestCase):
    def run_gate(self, expected_digest, validator_status=0):
        step = WORKFLOW.read_text().split('      - name: Fetch and validate active runtime state\n', 1)[1]
        script = textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'gcloud'
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(['gcloud'] + args) + '\\n')
if args[:3] == ['storage', 'objects', 'describe']:
    print('42')
elif args[:2] == ['storage', 'cat']:
    print(json.dumps({'schema_version': 1, 'secret_version': '7',
                      'sha256': os.environ['EXPECTED_DIGEST']}))
elif args[:3] == ['secrets', 'versions', 'access']:
    sys.stdout.buffer.write(open(os.environ['STATE_FILE'], 'rb').read())
else:
    print('unexpected gcloud call: ' + repr(args), file=sys.stderr)
    sys.exit(99)
''')
            fake.chmod(0o755)
            docker = root / 'docker'
            docker.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(['docker'] + sys.argv[1:]) + '\\n')
if sys.argv[1] == 'run':
    sys.exit(int(os.environ['VALIDATOR_STATUS']))
''')
            docker.chmod(0o755)
            calls = root / 'calls'
            state_file = root / 'state.json'
            state_file.write_bytes(STATE)
            config_file = root / 'config.toml'
            config_file.write_text('port = 8080\n')
            env = os.environ | {
                'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'EXPECTED_DIGEST': expected_digest,
                'VALIDATOR_STATUS': str(validator_status),
                'STATE_FILE': str(state_file), 'RUNNER_TEMP': directory,
                'RUNTIME_STATE_POINTER': 'gs://bucket/active.json',
                'RUNTIME_STATE_SECRET': 'active-snapshots',
                'RUNTIME_STATE_VALIDATE_COMMAND': '/bin/app --validate-spread-overrides /runtime-state.json',
                'IMAGE_REF': 'registry/app@sha256:' + 'a' * 64,
                'VALIDATE_ENTRYPOINT': '', 'CONFIG_FILE': 'config.toml',
                'GITHUB_ENV': str(root / 'environment'), 'CALLS': str(calls),
            }
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                    env=env, text=True, capture_output=True, cwd=root)
            recorded = [json.loads(line) for line in calls.read_text().splitlines()]
            environment = (root / 'environment').read_text() if (root / 'environment').exists() else ''
            return result, recorded, environment, root

    def test_generation_pinned_state_is_validated(self):
        digest = hashlib.sha256(STATE).hexdigest()
        result, calls, environment, root = self.run_gate(digest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(['gcloud', 'storage', 'cat', 'gs://bucket/active.json#42'], calls)
        self.assertIn(['gcloud', 'secrets', 'versions', 'access', '7', '--secret', 'active-snapshots'], calls)
        run = next(call for call in calls if call[:2] == ['docker', 'run'])
        self.assertIn(f'{root.resolve()}/config.toml:/candidate.toml:ro', run)
        self.assertIn(f'{root}/runtime-state.json:/runtime-state.json:ro', run)
        self.assertEqual(run[-4:], ['registry/app@sha256:' + 'a' * 64, '/bin/app',
                                    '--validate-spread-overrides', '/runtime-state.json'])
        self.assertIn('RUNTIME_STATE_GENERATION=42\n', environment)

    def test_digest_mismatch_stops_release(self):
        result, calls, _, _ = self.run_gate('0' * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('digest does not match', result.stdout)
        self.assertFalse(any(call[0] == 'docker' for call in calls))

    def test_validator_rejection_stops_release(self):
        digest = hashlib.sha256(STATE).hexdigest()
        result, _, environment, _ = self.run_gate(digest, validator_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('RUNTIME_STATE_GENERATION', environment)

    def test_legacy_rollback_skips_the_step(self):
        self.assertIn("if: inputs.runtime_state_pointer != '' && !inputs.runtime_state_legacy_rollback",
                      WORKFLOW.read_text())


if __name__ == '__main__':
    unittest.main()
