"""Source and artifact selection of frontend/scripts/build-agent-canvas.sh.

The script runs for real against a throwaway local Canvas repository: git
clones it, `npm ci` installs its empty dependency tree and `npm run build`
runs a tiny build that writes build/index.html. The script locates its output
directory from its own path, so each test runs a copy under ``tmp_path``.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / 'frontend' / 'scripts' / 'build-agent-canvas.sh'

pytestmark = pytest.mark.skipif(
    not (shutil.which('git') and shutil.which('npm') and shutil.which('node')),
    reason='needs git, node and npm',
)

_BUILD_JS = """
const fs = require('fs');
if (fs.existsSync('BROKEN')) {
  console.error('fake canvas build failed');
  process.exit(1);
}
fs.mkdirSync('build', { recursive: true });
fs.writeFileSync(
  'build/index.html',
  `canvas ${fs.readFileSync('VERSION', 'utf8').trim()} base=${process.env.VITE_BASE_PATH}`,
);
"""


def _default_ref() -> str:
    match = re.search(
        r'CANVAS_REF="\$\{AGENT_CANVAS_REF:-([^}]+)\}"', SCRIPT.read_text()
    )
    assert match, 'build-agent-canvas.sh no longer declares a default ref'
    return match.group(1)


def _git_env(home: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(('AGENT_CANVAS_', 'VITE_', 'GIT_'))
    }
    env.update(
        HOME=str(home),
        GIT_CONFIG_NOSYSTEM='1',
        GIT_AUTHOR_NAME='test',
        GIT_AUTHOR_EMAIL='test@example.com',
        GIT_COMMITTER_NAME='test',
        GIT_COMMITTER_EMAIL='test@example.com',
        NO_UPDATE_NOTIFIER='1',
        npm_config_cache=str(home / 'npm-cache'),
        npm_config_update_notifier='false',
    )
    return env


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(
        ['git', '-C', str(repo), *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture(scope='module')
def canvas_repo(tmp_path_factory):
    """A local Canvas repo with the default ref, v9.9.9 and a broken ref."""
    root = tmp_path_factory.mktemp('canvas')
    env = _git_env(root / 'home')
    (root / 'home').mkdir()
    repo = root / 'repo'
    repo.mkdir()
    (repo / 'package.json').write_text(
        json.dumps(
            {
                'name': 'fake-canvas',
                'version': '0.0.0',
                'private': True,
                'scripts': {'build': 'node build.js'},
            }
        )
    )
    (repo / 'package-lock.json').write_text(
        json.dumps(
            {
                'name': 'fake-canvas',
                'version': '0.0.0',
                'lockfileVersion': 3,
                'requires': True,
                'packages': {'': {'name': 'fake-canvas', 'version': '0.0.0'}},
            }
        )
    )
    (repo / 'build.js').write_text(_BUILD_JS)
    _git(repo, env, 'init', '-q', '-b', 'main')

    commits = {}
    for ref, broken in ((_default_ref(), False), ('v9.9.9', False), ('v-broken', True)):
        (repo / 'VERSION').write_text(ref)
        if broken:
            (repo / 'BROKEN').write_text('')
        _git(repo, env, 'add', '-A')
        _git(repo, env, 'commit', '-q', '-m', ref)
        _git(repo, env, 'tag', ref)
        commits[ref] = _git(repo, env, 'rev-parse', 'HEAD')
    return {'url': repo.as_uri(), 'path': repo, 'commits': commits, 'env': env}


@pytest.fixture
def frontend(tmp_path):
    scripts = tmp_path / 'frontend' / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copy(SCRIPT, scripts / SCRIPT.name)
    return tmp_path / 'frontend'


def _build(frontend: Path, canvas_repo, **overrides: str | None):
    env = dict(canvas_repo['env'])
    env.update(
        AGENT_CANVAS_REPO=canvas_repo['url'],
        AGENT_CANVAS_CACHE_DIR=str(frontend / '.cache' / 'agent-canvas'),
    )
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        ['bash', str(frontend / 'scripts' / SCRIPT.name)],
        env=env,
        capture_output=True,
        text=True,
    )


def _manifest(frontend: Path) -> dict:
    return json.loads(
        (frontend / 'public' / 'canvas' / 'agent-canvas-build.json').read_text()
    )


def test_omitted_ref_builds_the_pinned_default(frontend, canvas_repo):
    default_ref = _default_ref()

    result = _build(frontend, canvas_repo)

    assert result.returncode == 0, result.stderr
    assert _manifest(frontend) == {
        'repository': canvas_repo['url'],
        'ref': default_ref,
        'commit': canvas_repo['commits'][default_ref],
        'source': 'clone',
        'base_path': '/canvas',
        'lock_to_cloud': None,
    }
    index = (frontend / 'public' / 'canvas' / 'index.html').read_text()
    assert index == f'canvas {default_ref} base=/canvas'


def test_requested_ref_is_built_and_recorded(frontend, canvas_repo):
    result = _build(
        frontend,
        canvas_repo,
        AGENT_CANVAS_REF='v9.9.9',
        AGENT_CANVAS_LOCK_TO_CLOUD='http://localhost:9043',
    )

    assert result.returncode == 0, result.stderr
    manifest = _manifest(frontend)
    assert manifest['ref'] == 'v9.9.9'
    assert manifest['commit'] == canvas_repo['commits']['v9.9.9']
    assert manifest['lock_to_cloud'] == 'http://localhost:9043'
    index = (frontend / 'public' / 'canvas' / 'index.html').read_text()
    assert index.startswith('canvas v9.9.9 ')
    assert '"ref":"v9.9.9"' in result.stdout


def test_rebuild_of_same_ref_reuses_checkout(frontend, canvas_repo):
    assert _build(frontend, canvas_repo, AGENT_CANVAS_REF='v9.9.9').returncode == 0

    result = _build(frontend, canvas_repo, AGENT_CANVAS_REF='v9.9.9')

    assert result.returncode == 0, result.stderr
    assert _manifest(frontend)['source'] == 'cached-clone'
    assert _manifest(frontend)['commit'] == canvas_repo['commits']['v9.9.9']


def test_prefetched_source_records_ref_without_commit(frontend, canvas_repo, tmp_path):
    # BuildKit's `ADD <repo>#<ref>` yields a tree without .git.
    source = tmp_path / 'agent-canvas-source'
    subprocess.run(
        ['git', 'clone', '-q', '--branch', 'v9.9.9', canvas_repo['url'], str(source)],
        env=canvas_repo['env'],
        check=True,
    )
    shutil.rmtree(source / '.git')

    result = _build(
        frontend,
        canvas_repo,
        AGENT_CANVAS_REF='v9.9.9',
        AGENT_CANVAS_SOURCE_DIR=str(source),
    )

    assert result.returncode == 0, result.stderr
    manifest = _manifest(frontend)
    assert manifest['ref'] == 'v9.9.9'
    assert manifest['commit'] is None
    assert manifest['source'] == 'prefetched'


def test_prefetched_source_without_requested_ref_records_no_ref(
    frontend, canvas_repo, tmp_path
):
    # A local checkout of a branch the script defaults know nothing about.
    source = tmp_path / 'agent-canvas-source'
    subprocess.run(
        ['git', 'clone', '-q', '--branch', 'v9.9.9', canvas_repo['url'], str(source)],
        env=canvas_repo['env'],
        check=True,
    )

    result = _build(
        frontend,
        canvas_repo,
        AGENT_CANVAS_REPO=None,
        AGENT_CANVAS_SOURCE_DIR=str(source),
    )

    assert result.returncode == 0, result.stderr
    manifest = _manifest(frontend)
    assert manifest['repository'] is None
    assert manifest['ref'] is None
    assert manifest['commit'] == canvas_repo['commits']['v9.9.9']
    assert manifest['source'] == 'prefetched'


def test_failed_build_without_staged_bundle_reports_absence(frontend, canvas_repo):
    result = _build(frontend, canvas_repo, AGENT_CANVAS_REF='v-broken')

    assert result.returncode != 0
    assert (
        'Agent Canvas build of v-broken failed; no bundle is staged, '
        'so this app will not serve /canvas'
    ) in result.stderr
    assert not (frontend / 'public' / 'canvas').exists()


def test_failed_build_keeps_and_names_previous_bundle(frontend, canvas_repo):
    assert _build(frontend, canvas_repo, AGENT_CANVAS_REF='v9.9.9').returncode == 0

    result = _build(frontend, canvas_repo, AGENT_CANVAS_REF='v-broken')

    assert result.returncode != 0
    assert (
        'Agent Canvas build of v-broken failed; public/canvas still holds the '
        'previously staged bundle'
    ) in result.stderr
    assert '"ref":"v9.9.9"' in result.stderr
    assert _manifest(frontend)['ref'] == 'v9.9.9'


def test_unreachable_repository_fails_without_prompting(
    frontend, canvas_repo, tmp_path
):
    result = _build(
        frontend,
        canvas_repo,
        AGENT_CANVAS_REPO=(tmp_path / 'missing').as_uri(),
    )

    assert result.returncode != 0
    assert 'no bundle is staged' in result.stderr


def test_enterprise_canvas_pins_agree():
    """The embedded build, the opt-in Docker build and Compose ship one ref."""
    default_ref = _default_ref()
    dockerfile = (REPO_ROOT / 'containers' / 'app' / 'Dockerfile').read_text()
    compose = (REPO_ROOT / 'docker-compose.yml').read_text()

    docker_refs = re.findall(r'^ARG AGENT_CANVAS_REF=(\S+)$', dockerfile, re.M)
    compose_tags = re.findall(r'ghcr\.io/openhands/agent-canvas:([^}\s]+)', compose)

    assert docker_refs == [default_ref]
    assert compose_tags == [default_ref.removeprefix('v')]
