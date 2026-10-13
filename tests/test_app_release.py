"""Exercise the release workflow's digest gate without cloud credentials."""

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


def tag(name, digest=DIGEST):
    return {'tag': f'projects/p/locations/l/repositories/r/packages/bot/tags/{name}',
            'version': f'projects/p/locations/l/repositories/r/packages/bot/versions/{digest}'}


def step_script(workflow, name):
    step = workflow.read_text().split(f'      - name: {name}\n', 1)[1]
    return textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - ', 1)[0])


class DigestGateTest(unittest.TestCase):
    def run_gate(self, tags, version=COMMIT, registry_status=0, attested=True):
        script = step_script(WORKFLOW, 'Resolve and verify digests')
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


class PamGrantTest(unittest.TestCase):
    def run_gate(self, state):
        script = step_script(WORKFLOW, 'Await PAM approval')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'gcloud'
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:3] == ['pam', 'grants', 'list']:
    print('old-grant ' + os.environ['OLD_STATE'])
elif args[:4] == ['pam', 'grants', 'describe', 'old-grant']:
    print('ACTIVE')
elif args[:3] == ['pam', 'grants', 'create']:
    print('new-grant')
elif args[:4] == ['pam', 'grants', 'describe', 'new-grant']:
    print('ACTIVE')
elif args[:2] == ['storage', 'cat']:
    pass
else:
    print('unexpected gcloud call: ' + repr(args), file=sys.stderr)
    sys.exit(99)
''')
            fake.chmod(0o755)
            (root / 'git').write_text('#!/bin/sh\nprintf "replacement release\\n"\n')
            (root / 'git').chmod(0o755)
            (root / 'want.env').write_text('')
            summary = root / 'summary'
            calls = root / 'calls'
            env = os.environ | {
                'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'CALLS': str(calls), 'OLD_STATE': state,
                'ENTITLEMENT': 'app-deploy', 'RUNTIME': 'bot-vm',
                'CONFIG_FILE': '', 'CONFIG_BUCKET': 'bucket',
                'RUNNER_TEMP': directory, 'GITHUB_STEP_SUMMARY': str(summary),
                'GITHUB_ENV': str(root / 'environment'),
                'GITHUB_SERVER_URL': 'https://github.com',
                'GITHUB_REPOSITORY': 'org/app', 'GITHUB_RUN_ID': '123',
                'GITHUB_ACTOR': 'operator', 'GITHUB_REF_NAME': 'master',
                'CONFIG_REUSE': '', 'CONFIG_PREVIOUS': '', 'CONFIG_DIFFSTAT': '',
                'SERVICE': '', 'PROJECT': 'production', 'IMAGES': '', 'VERSION': '',
            }
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                    env=env, text=True, capture_output=True)
            recorded = [json.loads(line) for line in calls.read_text().splitlines()]
            return result, recorded

    def test_active_grant_is_reused(self):
        result, calls = self.run_gate('ACTIVE')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(call[:3] == ['pam', 'grants', 'revoke'] for call in calls))
        self.assertFalse(any(call[:3] == ['pam', 'grants', 'create'] for call in calls))

    def test_pending_grant_is_reused(self):
        result, calls = self.run_gate('APPROVAL_AWAITED')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(call[:3] == ['pam', 'grants', 'revoke'] for call in calls))
        self.assertFalse(any(call[:3] == ['pam', 'grants', 'create'] for call in calls))


class DeployWindowReleaseTest(unittest.TestCase):
    """A release inside an approved deploy window neither asks nor waits."""

    def run_gate(self, windows=(), list_error='', window_entitlement='deploy-window', ref='', image_tag='',
                 github_ref='refs/heads/main', head=COMMIT, tag_commit=''):
        script = step_script(WORKFLOW, 'Await PAM approval')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'gcloud'
            fake.write_text('''#!/usr/bin/env python3
import datetime, json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:3] == ['pam', 'grants', 'list'] and '--entitlement=deploy-window' in args:
    if os.environ['LIST_ERROR']:
        print(os.environ['LIST_ERROR'], file=sys.stderr)
        sys.exit(1)
    now = datetime.datetime.now(datetime.timezone.utc)
    grants = []
    for number, (state, left) in enumerate(json.loads(os.environ['WINDOWS'])):
        started = now - datetime.timedelta(minutes=240 - left)
        events = [] if state == 'SCHEDULED' else [
            {'eventTime': started.strftime('%Y-%m-%dT%H:%M:%S.123456Z'), 'activated': {}}]
        grants.append({'name': f'window-{number}', 'state': state, 'requestedDuration': '14400s',
                       'timeline': {'events': events}})
    print(json.dumps(grants))
elif args[:3] == ['pam', 'grants', 'list']:
    pass
elif args[:3] == ['pam', 'grants', 'create']:
    print('new-grant')
elif args[:4] == ['pam', 'grants', 'describe', 'new-grant']:
    print('ACTIVE')
elif args[:2] == ['storage', 'cat']:
    pass
else:
    print('unexpected gcloud call: ' + repr(args), file=sys.stderr)
    sys.exit(99)
''')
            fake.chmod(0o755)
            (root / 'git').write_text('#!/bin/sh\n'
                                      'case "$*" in "rev-parse HEAD") exec printf "%s\\n" "$HEAD_SHA" ;;\n'
                                      '  "rev-parse -q --verify refs/tags/"*) exec printf "%s\\n" "$TAG_SHA" ;; esac\n'
                                      'printf "replacement release\\n"\n')
            (root / 'git').chmod(0o755)
            (root / 'want.env').write_text('')
            calls = root / 'calls'
            environment = root / 'environment'
            env = os.environ | {
                'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'CALLS': str(calls), 'WINDOWS': json.dumps(windows), 'LIST_ERROR': list_error,
                'ENTITLEMENT': 'app-deploy', 'WINDOW_ENTITLEMENT': window_entitlement, 'RUNTIME': 'bot-vm',
                'REF_INPUT': ref, 'IMAGE_TAG_INPUT': image_tag, 'DEFAULT_BRANCH': 'main',
                'GITHUB_REF': github_ref, 'GITHUB_SHA': COMMIT, 'HEAD_SHA': head, 'TAG_SHA': tag_commit,
                'CONFIG_FILE': '', 'CONFIG_BUCKET': 'bucket',
                'RUNNER_TEMP': directory, 'GITHUB_STEP_SUMMARY': str(root / 'summary'),
                'GITHUB_ENV': str(environment),
                'GITHUB_SERVER_URL': 'https://github.com',
                'GITHUB_REPOSITORY': 'org/app', 'GITHUB_RUN_ID': '123',
                'GITHUB_ACTOR': 'operator', 'GITHUB_REF_NAME': 'main',
                'CONFIG_REUSE': '', 'CONFIG_PREVIOUS': '', 'CONFIG_DIFFSTAT': '',
                'SERVICE': '', 'PROJECT': 'production', 'IMAGES': '', 'VERSION': '',
            }
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                    env=env, text=True, capture_output=True)
            recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            exported = environment.read_text() if environment.exists() else ''
            return result, recorded, exported

    def assert_skipped(self, result, calls, exported):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Inside an approved deploy window', result.stdout)
        self.assertFalse(any(call[:3] == ['pam', 'grants', 'create'] for call in calls))
        self.assertNotIn('PAM_GRANT=', exported)

    def assert_asked(self, result, calls, exported):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any(call[:3] == ['pam', 'grants', 'create'] for call in calls))
        self.assertIn('PAM_GRANT=new-grant', exported)

    def test_active_window_skips_the_approval(self):
        self.assert_skipped(*self.run_gate([('ACTIVE', 180)]))

    def test_longest_active_window_counts(self):
        result, calls, exported = self.run_gate([('ACTIVE', 5), ('ACTIVE', 180)])
        self.assert_skipped(result, calls, exported)
        self.assertIn('window-1', result.stdout)

    def test_window_ending_soon_asks_as_usual(self):
        result, calls, exported = self.run_gate([('ACTIVE', 20)])
        self.assert_asked(result, calls, exported)
        self.assertIn('under 30 minutes', result.stdout)

    def test_closed_window_is_not_open(self):
        self.assert_asked(*self.run_gate([('REVOKED', 180)]))

    def test_window_covers_a_run_releasing_its_own_tag_or_default_branch(self):
        for case in (dict(github_ref='refs/tags/v1.2.0'),
                     dict(github_ref='refs/tags/v1.2.0', ref=COMMIT),
                     dict(ref='main'),
                     dict(image_tag='v1.2.0'),
                     dict(ref='refs/tags/v1.1.0', image_tag='v1.1.0', head='d' * 40, tag_commit='d' * 40),
                     dict(ref='v1.1.0', image_tag='v1.1.0', head='d' * 40, tag_commit='d' * 40)):
            with self.subTest(**case):
                self.assert_skipped(*self.run_gate([('ACTIVE', 180)], **case))

    def test_window_does_not_cover_other_code_or_config(self):
        for case in (dict(github_ref='refs/heads/feature'),
                     dict(ref='v1-feature', head='b' * 40),
                     dict(ref='refs/tags/v1.1.0', head='b' * 40, tag_commit='d' * 40),
                     dict(image_tag='c' * 40)):
            with self.subTest(**case):
                result, calls, exported = self.run_gate([('ACTIVE', 180)], **case)
                self.assert_asked(result, calls, exported)
                self.assertIn('outside what a deploy window covers', result.stdout)

    def test_window_entitlement_must_differ_from_the_one_off(self):
        for name in ('app-deploy', 'projects/production/locations/global/entitlements/app-deploy'):
            with self.subTest(name):
                result, calls, _ = self.run_gate([('ACTIVE', 180)], window_entitlement=name)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('needs its own entitlement', result.stdout)
                self.assertEqual(calls, [])

    def test_scheduled_window_is_not_open_yet(self):
        self.assert_asked(*self.run_gate([('SCHEDULED', 180)]))

    def test_unreadable_windows_warn_and_ask(self):
        result, calls, exported = self.run_gate(list_error='PERMISSION_DENIED: grants.list')
        self.assert_asked(result, calls, exported)
        self.assertIn('::warning::could not list deploy windows', result.stdout)

    def test_missing_window_entitlement_asks_quietly(self):
        result, calls, exported = self.run_gate(list_error='NOT_FOUND: entitlement')
        self.assert_asked(result, calls, exported)
        self.assertNotIn('::warning::', result.stdout)

    def test_no_window_entitlement_keeps_the_old_flow(self):
        result, calls, exported = self.run_gate([('ACTIVE', 180)], window_entitlement='')
        self.assert_asked(result, calls, exported)
        self.assertFalse(any('--entitlement=deploy-window' in call for call in calls))


DEPLOY_WINDOW = Path(__file__).resolve().parents[1] / '.github/workflows/deploy-window.yml'


class DeployWindowRequestTest(unittest.TestCase):
    """The request side refuses what PAM would refuse, before asking it."""

    def check(self, start_in_minutes=60 * 24, hours='4', ref='refs/heads/main', start=None):
        import datetime
        script = step_script(DEPLOY_WINDOW, 'Check the window')
        when = start or (datetime.datetime.now(datetime.timezone.utc)
                         + datetime.timedelta(minutes=start_in_minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'output'
            env = os.environ | {
                'PROJECT': 'production', 'ENTITLEMENT': 'deploy-window',
                'START_IN': when, 'HOURS_IN': hours,
                'REQUESTER_IN': 'operator', 'REASON_IN': 'Thursday release, approvers away',
                'DEFAULT_BRANCH': 'main', 'GITHUB_REF': ref, 'GITHUB_ACTOR': 'operator',
                'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_REPOSITORY': 'org/app', 'GITHUB_RUN_ID': '9',
                'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': str(root / 'summary'),
            }
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                    env=env, text=True, capture_output=True)
            return result, output.read_text() if output.exists() else ''

    def test_good_request_names_the_window_and_the_asker(self):
        result, output = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('seconds=14400', output)
        self.assertIn('ASKED BY: @operator', output)
        self.assertIn('Istanbul', output)

    def test_refused_requests(self):
        cases = {
            'past': dict(start_in_minutes=-10),
            'too soon': dict(start_in_minutes=2),
            'beyond a week': dict(start_in_minutes=8 * 24 * 60),
            'zero hours': dict(hours='0'),
            'too long': dict(hours='13'),
            'not a number': dict(hours='4h'),
            'not UTC': dict(start='2026-10-22T16:00:00+03:00'),
            'other branch': dict(ref='refs/heads/feature'),
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                result, output = self.check(**kwargs)
                self.assertNotEqual(result.returncode, 0, label)
                self.assertNotIn('seconds=', output)


if __name__ == '__main__':
    unittest.main()
