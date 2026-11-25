"""Bounded, truthful workspace file discovery contract.

This mirrors the shared typed contract the SDK publishes (see
https://github.com/OpenHands/software-agent-sdk/issues/6079) and the Agent
Canvas frontend's local ``find`` listing (``src/utils/workspace-file-discovery.ts``)
so cloud/Enterprise and local backends discover files with the same options and
report the same outcomes.

Enterprise owns *authorization and bounded execution*: it normalizes caller
options against server ceilings, builds a shell-injection-safe ``find``
invocation that is always bounded by ``head -n``, and classifies the runtime
result into a typed status so the UI can distinguish a successful empty
workspace from a missing directory, a permission denial, a transport failure or
a truncated listing.

Non-goals: this never shells out to an unbounded ``find`` and never lets a
caller exclusion pattern or cap escape the command line. Exclusion patterns are
quoted; the cap is an integer clamped to a server maximum.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Mandatory bookkeeping exclusions kept independent of caller preferences, in
# sync with OpenHands/OpenHands#18108 and enterprise#684: heavy/build dirs and
# VCS metadata that must never be enumerated regardless of what a tenant asks
# for. Preferences may only *add* to these, never remove them.
MANDATORY_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        '.git',
        'node_modules',
        '.venv',
        'venv',
        '__pycache__',
        'dist',
        'build',
        '.next',
        '.cache',
        '.pytest_cache',
        '.mypy_cache',
        '.turbo',
        '.parcel-cache',
        'target',
    }
)

# Server ceiling on the number of files returned so a giant repo cannot
# overwhelm the UI or the runtime. Caller ``max_files`` is clamped to this.
WORKSPACE_LIST_MAX_FILES_CEILING = 2000

# How many extra paths to fetch beyond the cap so truncation is detectable
# (an exact-size result is not truncation).
_TRUNCATION_OVERFETCH = 1

# A pattern is a single path/name segment with no path separators, control
# chars or shell metacharacters. We allow ``/`` so callers can express path
# exclusions (e.g. ``src/generated``), but reject anything that could break out
# of single-quoting or inject options.
_VALID_PATTERN = re.compile(r'^[^\0\r\n<>|;&`$!*?\\]*[^\0\r\n<>|;&`$!*?\\]$')


class FileDiscoveryStatus(str, Enum):
    """Truthful outcome of a workspace listing request.

    ``OK`` covers both a populated and a genuinely empty workspace — the
    command ran, the directory exists and was readable. The other values
    distinguish the failure modes the old ``[]``-on-nonzero-exit conflated.
    """

    OK = 'ok'
    EMPTY = 'empty'  # directory exists and is readable but has no files
    MISSING = 'missing'  # the directory does not exist
    FORBIDDEN = 'forbidden'  # permission denied
    INVALID_RESPONSE = 'invalid_response'  # runtime returned non-JSON / wrong shape
    COMMAND_ERROR = 'command_error'  # find ran but exited nonzero for another reason
    TIMEOUT = 'timeout'  # the listing command timed out
    TRANSPORT_ERROR = 'transport_error'  # could not reach the runtime at all


class WorkspaceFileDiscoveryOptions(BaseModel):
    """Persisted, tenant-scoped discovery preferences.

    Matches ``WorkspaceFileDiscovery`` in the frontend contract:
    ``excludedPatterns``, ``maxFiles`` (0 == unlimited, clamped to the server
    ceiling), ``includeSymlinks``.
    """

    excluded_patterns: list[str] = Field(default_factory=list)
    max_files: int = 0
    include_symlinks: bool = False

    @field_validator('excluded_patterns')
    @classmethod
    def _validate_patterns(cls, patterns: list[str]) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern:
                continue
            if not _VALID_PATTERN.match(pattern):
                continue
            if pattern in seen:
                continue
            seen.add(pattern)
            cleaned.append(pattern)
        return cleaned

    @field_validator('max_files')
    @classmethod
    def _validate_max_files(cls, max_files: int) -> int:
        if not isinstance(max_files, int) or max_files < 0:
            return 0
        return max_files


def normalize_options(
    raw: Any, *, ceiling: int = WORKSPACE_LIST_MAX_FILES_CEILING
) -> WorkspaceFileDiscoveryOptions:
    """Normalize untrusted caller/persisted options into a safe, bounded form.

    Unknown shapes, bad types and out-of-range values fall back to defaults
    rather than raising, so a corrupt persisted preference can never break
    listing. ``max_files`` is clamped to ``ceiling``; ``0`` (unlimited from the
    caller's perspective) is re-interpreted as the ceiling so execution is
    always bounded server-side.
    """
    if raw is None:
        return WorkspaceFileDiscoveryOptions(max_files=ceiling)
    if isinstance(raw, WorkspaceFileDiscoveryOptions):
        data = raw.model_dump()
    elif isinstance(raw, dict):
        data = raw
    else:
        return WorkspaceFileDiscoveryOptions(max_files=ceiling)

    patterns = data.get('excluded_patterns')
    if not isinstance(patterns, list):
        patterns = []
    else:
        # Drop non-string entries here too (the model validator also rejects
        # them, but normalizing first keeps a corrupt persisted value from
        # raising instead of falling back).
        patterns = [p for p in patterns if isinstance(p, str)]
    max_files = data.get('max_files')
    if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 0:
        max_files = ceiling
    elif max_files == 0 or max_files > ceiling:
        max_files = ceiling
    include_symlinks = data.get('include_symlinks') is True

    return WorkspaceFileDiscoveryOptions(
        excluded_patterns=patterns,
        max_files=max_files,
        include_symlinks=include_symlinks,
    )


def resolved_excluded_dirs(options: WorkspaceFileDiscoveryOptions) -> list[str]:
    """The effective exclusion list: mandatory dirs plus caller additions.

    Mandatory security/bookkeeping exclusions are always present and cannot be
    removed by preferences. Caller patterns (bare names and path-style
    patterns alike) are appended; ones matching a mandatory dir are
    deduplicated.
    """
    merged: list[str] = list(MANDATORY_EXCLUDED_DIRS)
    for pattern in options.excluded_patterns:
        if pattern not in merged:
            merged.append(pattern)
    return merged


def _shell_quote(value: str) -> str:
    """Single-quote a value for a POSIX shell, escaping embedded quotes.

    This is the same scheme the frontend uses; it guarantees the value is
    treated as a single literal argument regardless of its contents, so an
    exclusion pattern can never inject operators or options into the ``find``
    command line.
    """
    return "'" + value.replace("'", "'\\''") + "'"


def build_workspace_list_command(
    options: WorkspaceFileDiscoveryOptions,
) -> str:
    """Build a bounded, shell-injection-safe ``find`` invocation.

    Mirrors ``buildWorkspaceFileListCommand`` in the frontend so cloud and
    local produce the same tree. Pruning uses ``-name`` for bare dir patterns
    and ``-path`` for patterns containing ``/``. The result is always bounded
    by ``head -n {cap + 1}`` (the +1 makes truncation detectable) and sorted.
    ``find``'s stderr is kept: the pipeline's exit code is ``head``'s, so
    stderr is the only trace of nested subtrees ``find`` could not read (see
    ``count_unreadable_subtrees``).
    """
    name_prunes: list[str] = []
    path_prunes: list[str] = []
    for pattern in resolved_excluded_dirs(options):
        if '/' in pattern:
            relative = pattern[2:] if pattern.startswith('./') else pattern
            path_prunes.append(f'-path {_shell_quote("./" + relative)}')
        else:
            name_prunes.append(f'-name {_shell_quote(pattern)}')

    prune_parts = name_prunes + path_prunes
    prune = (
        f'-type d \\( {" -o ".join(prune_parts)} \\) -prune -o ' if prune_parts else ''
    )
    # ``{}`` is find's -exec placeholder for the current path. This is a plain
    # string (not an f-string), so the braces are literal; do not escape them.
    types = (
        '\\( -type f -o \\( -type l -exec test -f {} \\; \\) \\)'
        if options.include_symlinks
        else '-type f'
    )
    cap = options.max_files
    limit = f' | head -n {cap + _TRUNCATION_OVERFETCH}' if cap > 0 else ''
    return f'find . {prune}{types} -print | sort{limit}'


def parse_workspace_file_list(stdout: str, max_files: int) -> tuple[list[str], bool]:
    """Parse find stdout into de-duped, ``./``-stripped relative paths.

    Returns ``(paths, is_truncated)``. ``is_truncated`` is True only when the
    overfetch produced more paths than the cap — an exact-size result is not
    truncation.
    """
    seen: set[str] = set()
    paths: list[str] = []
    for line in stdout.splitlines():
        rel = line.strip()
        if rel.startswith('./'):
            rel = rel[2:]
        if not rel or rel in seen:
            continue
        seen.add(rel)
        paths.append(rel)
    truncated = max_files > 0 and len(paths) > max_files
    if max_files > 0:
        paths = paths[:max_files]
    return paths, truncated


# Prefix the agent-server puts on stderr when it cannot start the command at
# all, e.g. ``chdir`` into the listing root failed with ``[Errno 2]`` (missing)
# or ``[Errno 13]`` (unreadable). This is the only root-level signal: ``find``'s
# own stderr lines describe nested subtrees.
_RUNTIME_EXEC_ERROR_PREFIX = 'Error executing command:'

# The agent-server reports a command it killed at its own timeout as exit -1
# with no ``Error executing command:`` line (``BashOutput`` has no timeout flag).
_RUNTIME_TIMEOUT_EXIT_CODE = -1

_FIND_ROOT_DENIED = re.compile(r"^find: ['\u2018]\.['\u2019]: permission denied", re.I)


def count_unreadable_subtrees(stderr: str) -> int:
    """Count nested directories ``find`` skipped because of a permission denial."""
    return sum(
        1
        for line in (stderr or '').splitlines()
        if line.startswith('find:')
        and 'permission denied' in line.lower()
        and not _FIND_ROOT_DENIED.match(line)
    )


def classify_listing_status(exit_code: int | None, stderr: str) -> FileDiscoveryStatus:
    """Classify an agent-server ``execute_bash_command`` result into a typed status.

    Only root-level signals map to ``missing``/``forbidden``: the agent-server's
    ``Error executing command:`` line (the subprocess could not ``chdir`` into
    the root) or ``find`` reporting the root ``.`` itself as unreadable. Nested
    ``find: ...: Permission denied`` lines never decide the status; on success
    they mark the listing partial (see ``count_unreadable_subtrees``).
    ``exit_code`` is ``None`` when the runtime did not return a parseable one.
    """
    if exit_code is None:
        return FileDiscoveryStatus.INVALID_RESPONSE
    lines = (stderr or '').splitlines()
    exec_errors = [
        line.lower() for line in lines if line.startswith(_RUNTIME_EXEC_ERROR_PREFIX)
    ]
    if exec_errors:
        if any(
            '[errno 2]' in e or 'no such file or directory' in e for e in exec_errors
        ):
            return FileDiscoveryStatus.MISSING
        if any('[errno 13]' in e or 'permission denied' in e for e in exec_errors):
            return FileDiscoveryStatus.FORBIDDEN
        return FileDiscoveryStatus.COMMAND_ERROR
    if any(_FIND_ROOT_DENIED.match(line) for line in lines):
        return FileDiscoveryStatus.FORBIDDEN
    if exit_code == 0:
        return FileDiscoveryStatus.OK
    if exit_code == _RUNTIME_TIMEOUT_EXIT_CODE:
        return FileDiscoveryStatus.TIMEOUT
    return FileDiscoveryStatus.COMMAND_ERROR


class WorkspaceFileListing(BaseModel):
    """Typed listing result returned by the ``/files`` endpoint.

    A structured payload lets the frontend render an honest empty state, a
    retryable load error, or a truncation notice instead of guessing from an
    empty ``[]``.
    """

    status: FileDiscoveryStatus
    files: list[str] = Field(default_factory=list)
    is_truncated: bool = False
    # The effective cap applied server-side, so the UI can show
    # "showing N of more than M".
    max_files: int = 0
    # The effective exclusion set, so the UI can reflect mandatory + caller prefs.
    excluded_dirs: list[str] = Field(default_factory=list)
    # True when the listing root was readable but some nested directories were
    # not, so their contents are missing from ``files``.
    is_partial: bool = False
    message: str | None = None
