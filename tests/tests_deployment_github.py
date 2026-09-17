"""GitHub identity and transport tests never contact remote services."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.github import GitHub, GitHubError, _NoRedirect


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token = Path(self.temp.name) / 'github.token'
        self.token.write_text('private-test-token')
        self.sha = 'a' * 40
        self.calls = []
        self.run = {'id': 123, 'run_attempt': 1, 'head_sha': self.sha, 'head_branch': 'main',
                    'event': 'push', 'status': 'completed', 'conclusion': 'success',
                    'repository': {'full_name': 'marcus-ao/FacebookScraper'},
                    'path': '.github/workflows/release.yml'}
        self.body = b'archive bytes'
        self.artifact = {'id': 456, 'name': 'fbscraper-windows', 'expired': False,
                         'digest': 'sha256:' + hashlib.sha256(self.body).hexdigest(),
                         'workflow_run': {'id': 123, 'head_sha': self.sha, 'head_branch': 'main'}}

    def request(self, url, headers):
        self.calls.append((url, headers))
        if url.endswith('/commits/main'):
            value = {'sha': self.sha}
        elif 'workflows/release.yml/runs?' in url:
            value = {'workflow_runs': [self.run]}
        elif url.endswith('/runs/123/artifacts?per_page=100'):
            value = {'artifacts': [self.artifact]}
        elif url.endswith('/artifacts/456/zip'):
            return 302, {'Location': 'https://fixture.blob.core.windows.net/download?secret=1'}, b''
        elif url.startswith('https://fixture.blob.core.windows.net/'):
            return 200, {}, self.body
        else:
            self.fail(url)
        return 200, {}, json.dumps(value).encode()

    def test_exact_head_artifact_and_redirect_never_forward_token(self):
        client = GitHub(self.token, request=self.request)
        candidate = client.candidate()
        self.assertEqual(candidate['sha'], self.sha)
        target = Path(self.temp.name) / 'artifact.zip'
        client.download(candidate, target)
        self.assertEqual(target.read_bytes(), self.body)
        self.assertNotIn('Authorization', self.calls[-1][1])
        self.assertIn('Authorization', self.calls[0][1])

    def test_reject_wrong_head_repository_event_workflow_expired_or_digest(self):
        for field, value in [('head_sha', 'b' * 40), ('event', 'workflow_dispatch'),
                             ('repository', {'full_name': 'attacker/repo'}),
                             ('path', '.github/workflows/other.yml')]:
            original = self.run[field]
            self.run[field] = value
            self.assertIsNone(GitHub(self.token, request=self.request).candidate())
            self.run[field] = original
        self.artifact['expired'] = True
        self.assertIsNone(GitHub(self.token, request=self.request).candidate())
        self.artifact['expired'] = False
        client = GitHub(self.token, request=self.request)
        candidate = client.candidate()
        self.body = b'corrupt'
        with self.assertRaisesRegex(GitHubError, 'artifact_digest'):
            client.download(candidate, Path(self.temp.name) / 'bad.zip')
        self.assertFalse((Path(self.temp.name) / 'bad.zip').exists())

    def test_error_message_does_not_contain_body_or_token(self):
        def failure(url, headers):
            return 403, {}, b'private-test-token very secret error'
        with self.assertRaises(GitHubError) as caught:
            GitHub(self.token, request=failure).head()
        self.assertEqual(str(caught.exception), 'github_http_403')

    def test_default_http_handler_never_follows_redirect(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))

    def test_artifact_run_binding_and_digest_are_mandatory(self):
        original = dict(self.artifact['workflow_run'])
        for key, value in [('id', 999), ('head_sha', 'b' * 40), ('head_branch', 'other')]:
            self.artifact['workflow_run'] = dict(original, **{key: value})
            self.assertIsNone(GitHub(self.token, request=self.request).candidate())
        self.artifact['workflow_run'] = original
        self.artifact['digest'] = ''
        self.assertIsNone(GitHub(self.token, request=self.request).candidate())


if __name__ == '__main__':
    unittest.main()
