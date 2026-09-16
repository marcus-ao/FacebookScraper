"""Read-only GitHub Actions polling with explicit artifact provenance."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from deployment.release import ARTIFACT_NAME, REPOSITORY


class GitHubError(RuntimeError):
    """Only sanitized error codes cross the controller boundary."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, headers):
    try:
        with build_opener(_NoRedirect).open(Request(url, headers=headers), timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), b''
    except (URLError, OSError, ValueError):
        raise GitHubError('github_transport') from None


class GitHub:
    def __init__(self, token_file: Path, *, request=request):
        self.token_file = Path(token_file)
        self.request = request
        self.base = 'https://api.github.com/repos/' + REPOSITORY

    def _api(self, path, *, raw=False):
        try:
            token = self.token_file.read_text(encoding='utf-8').strip()
            if not token or '\n' in token or '\r' in token:
                raise ValueError()
        except (OSError, ValueError):
            raise GitHubError('github_token_missing') from None
        result = self.request(self.base + path, {
            'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'FBScraper-deployment'})
        status, headers, body = result
        if raw:
            return result
        if status != 200:
            raise GitHubError('github_http_' + str(status))
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, TypeError):
            raise GitHubError('github_invalid_json') from None

    def head(self):
        sha = self._api('/commits/main').get('sha', '')
        if not re.fullmatch('[0-9a-f]{40}', sha):
            raise GitHubError('github_invalid_head')
        return sha

    def candidate(self):
        sha = self.head()
        runs = self._api('/actions/workflows/release.yml/runs?branch=main&event=push&status=success&per_page=100')
        for run in runs.get('workflow_runs', []):
            if (run.get('head_sha') != sha or run.get('head_branch') != 'main'
                    or run.get('event') != 'push' or run.get('status') != 'completed'
                    or run.get('conclusion') != 'success'
                    or run.get('repository', {}).get('full_name') != REPOSITORY
                    or run.get('path') != '.github/workflows/release.yml'
                    or type(run.get('id')) is not int):
                continue
            run_id = run['id']
            artifacts = self._api(f'/actions/runs/{run_id}/artifacts?per_page=100')
            for artifact in artifacts.get('artifacts', []):
                owner = artifact.get('workflow_run', {})
                if (artifact.get('name') != ARTIFACT_NAME or artifact.get('expired') is not False
                        or owner.get('id') != run_id or owner.get('head_sha') != sha
                        or owner.get('head_branch') != 'main' or type(artifact.get('id')) is not int
                        or not re.fullmatch('sha256:[0-9a-f]{64}', artifact.get('digest', ''))):
                    continue
                return {'sha': sha, 'repository': REPOSITORY, 'workflow': 'release.yml',
                        'run_id': run_id, 'run_attempt': run.get('run_attempt', 1),
                        'artifact_id': artifact['id'], 'digest': artifact['digest']}
        return None

    def download(self, candidate, destination: Path):
        artifact_id = candidate.get('artifact_id')
        if type(artifact_id) is not int or not re.fullmatch('sha256:[0-9a-f]{64}', candidate.get('digest', '')):
            raise GitHubError('github_artifact_identity')
        status, headers, body = self._api(f'/actions/artifacts/{artifact_id}/zip', raw=True)
        if status == 302:
            location = next((v for k, v in headers.items() if k.lower() == 'location'), '')
            parts = urlsplit(location)
            host = parts.hostname or ''
            if (parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443)
                    or not (host.endswith('.blob.core.windows.net')
                            or host.endswith('.actions.githubusercontent.com')
                            or host.endswith('.githubusercontent.com'))):
                raise GitHubError('github_artifact_redirect')
            # Artifact storage is a different authority: never carry the GitHub token.
            status, _headers, body = self.request(location, {'User-Agent': 'FBScraper-deployment'})
        if status != 200:
            raise GitHubError('github_http_' + str(status))
        if 'sha256:' + hashlib.sha256(body).hexdigest() != candidate['digest']:
            raise GitHubError('artifact_digest')
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(body)
