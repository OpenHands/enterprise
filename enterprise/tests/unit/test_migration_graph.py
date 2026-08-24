"""Integrity checks for the Alembic revision graph.

Migrations here are numbered sequentially, so two branches opened off the
same parent independently pick the *same* next number. Each branch is
valid on its own -- and passes CI on its own -- but the second one to
merge lands a duplicate revision id and a second head on ``main``, which
breaks ``alembic upgrade head`` for every deploy.

These tests make that collision fail loudly on the second branch instead
of silently on ``main`` after the merge.
"""

import re
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[2] / 'migrations' / 'versions'

_REVISION_RE = re.compile(r'^revision(?::\s*[^=]+)?\s*=\s*[\'"]([^\'"]+)[\'"]', re.M)
_DOWN_REVISION_RE = re.compile(
    r'^down_revision(?::\s*[^=]+)?\s*=\s*(?:[\'"]([^\'"]+)[\'"]|None)', re.M
)


def _migrations() -> dict[str, tuple[str, str | None]]:
    """Map each migration file to its ``(revision, down_revision)`` pair."""
    found: dict[str, tuple[str, str | None]] = {}
    for path in sorted(VERSIONS_DIR.glob('*.py')):
        if path.name == '__init__.py':
            continue
        source = path.read_text()
        revision_match = _REVISION_RE.search(source)
        down_match = _DOWN_REVISION_RE.search(source)
        assert revision_match is not None, f'{path.name} declares no revision'
        assert down_match is not None, f'{path.name} declares no down_revision'
        found[path.name] = (revision_match.group(1), down_match.group(1))
    return found


def test_revision_ids_are_unique():
    """Two migrations must never claim the same revision id."""
    by_revision: dict[str, list[str]] = {}
    for filename, (revision, _) in _migrations().items():
        by_revision.setdefault(revision, []).append(filename)

    duplicates = {rev: files for rev, files in by_revision.items() if len(files) > 1}
    assert not duplicates, (
        'Duplicate Alembic revision ids: '
        f'{duplicates}. Another branch already merged this number -- '
        'renumber this migration to the next free revision.'
    )


def test_revision_graph_has_a_single_head():
    """Exactly one migration must be unreferenced as a parent."""
    migrations = _migrations()
    revisions = {revision for revision, _ in migrations.values()}
    parents = {down for _, down in migrations.values() if down is not None}

    heads = sorted(revisions - parents)
    assert len(heads) == 1, (
        f'Expected exactly one Alembic head, found {len(heads)}: {heads}. '
        'Concurrent migrations branched off the same parent -- rebase one '
        'onto the other so the chain stays linear.'
    )


def test_every_down_revision_exists():
    """No migration may point at a parent that is not in the tree."""
    migrations = _migrations()
    revisions = {revision for revision, _ in migrations.values()}

    orphans = {
        filename: down
        for filename, (_, down) in migrations.items()
        if down is not None and down not in revisions
    }
    assert not orphans, f'Migrations reference missing parents: {orphans}'


def test_no_two_migrations_share_a_parent():
    """A shared parent is a fork in the chain, even if one head still wins."""
    by_parent: dict[str, list[str]] = {}
    for filename, (_, down) in _migrations().items():
        if down is not None:
            by_parent.setdefault(down, []).append(filename)

    forks = {down: files for down, files in by_parent.items() if len(files) > 1}
    assert not forks, (
        f'Multiple migrations share a down_revision: {forks}. '
        'Chain them sequentially instead of branching.'
    )
