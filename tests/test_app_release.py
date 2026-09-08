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


if __name__ == '__main__':
    unittest.main()
