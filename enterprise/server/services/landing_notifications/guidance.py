import re
from pathlib import PurePosixPath
from urllib.parse import quote

from server.services.landing_notifications.consumer_models import PullRequestRecord
from server.services.landing_notifications.guidance_models import (
    GuidanceKind,
    GuidanceSource,
    LinearIssueContext,
    TestGuidance,
    TestInstruction,
)

_MAX_SUGGESTIONS = 5
_SECTION_PATTERN = re.compile(r'^#{1,6}\s+(.+?)\s*$', re.MULTILINE)


def build_test_guidance(
    pull_request: PullRequestRecord,
    linear_issue: LinearIssueContext | None = None,
) -> TestGuidance:
    instructions = [
        *_primary_e2e_instructions(pull_request),
        *_changed_e2e_instructions(pull_request),
        *_section_suggestions(
            pull_request.body,
            {'how to test', 'testing', 'test plan'},
            GuidanceSource.PR_HOW_TO_TEST,
            pull_request.url,
        ),
    ]
    if linear_issue:
        instructions.extend(
            _section_suggestions(
                linear_issue.description,
                {'acceptance criteria', 'how to test', 'test plan'},
                GuidanceSource.LINEAR_ACCEPTANCE_CRITERIA,
                linear_issue.url,
            )
        )

    unique = _deduplicate(instructions)
    if not unique:
        unique = (
            TestInstruction(
                kind=GuidanceKind.SUGGESTED,
                source=GuidanceSource.PR_SUMMARY,
                text=(
                    f'Suggested: open the target environment and exercise the '
                    f'behavior described by “{pull_request.title}”.'
                ),
                url=pull_request.url,
            ),
        )
    return TestGuidance(instructions=unique[:_MAX_SUGGESTIONS])


def _primary_e2e_instructions(
    pull_request: PullRequestRecord,
) -> tuple[TestInstruction, ...]:
    value = _field_value(pull_request.body, 'Primary E2E test')
    if not value or value.casefold() in {'tbd', 'n/a'}:
        return ()
    path, _, test_name = value.partition('::')
    path = path.strip()
    if _is_http_url(path):
        url = path
    elif _looks_like_e2e_path(path):
        url = _github_blob_url(pull_request, path)
    else:
        return ()
    label = test_name.strip() or PurePosixPath(path).name
    return (
        TestInstruction(
            kind=GuidanceKind.VERIFIED_E2E,
            source=GuidanceSource.PRIMARY_E2E,
            text=f'Run the declared E2E test: {label}',
            url=url,
        ),
    )


def _changed_e2e_instructions(
    pull_request: PullRequestRecord,
) -> tuple[TestInstruction, ...]:
    return tuple(
        TestInstruction(
            kind=GuidanceKind.VERIFIED_E2E,
            source=GuidanceSource.CHANGED_E2E,
            text=f'Review and run the released E2E coverage in {path}',
            url=_github_blob_url(pull_request, path),
        )
        for path in sorted(set(pull_request.changed_files))
        if _looks_like_e2e_path(path)
    )


def _section_suggestions(
    markdown: str,
    headings: set[str],
    source: GuidanceSource,
    url: str,
) -> tuple[TestInstruction, ...]:
    body = _first_matching_section(markdown, headings)
    steps = _meaningful_lines(body)
    return tuple(
        TestInstruction(
            kind=GuidanceKind.SUGGESTED,
            source=source,
            text=f'Suggested: {step}',
            url=url,
        )
        for step in steps
    )


def _first_matching_section(markdown: str, headings: set[str]) -> str:
    matches = list(_SECTION_PATTERN.finditer(markdown))
    for index, match in enumerate(matches):
        if match.group(1).strip().casefold() not in headings:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        return markdown[start:end]
    return ''


def _meaningful_lines(markdown: str) -> tuple[str, ...]:
    lines: list[str] = []
    in_code_block = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith('```'):
            in_code_block = not in_code_block
            continue
        if not line or line.startswith('<!--') or line.startswith('-->'):
            continue
        cleaned = re.sub(r'^[-*\d.)\s]+', '', line).strip('` ')
        if not cleaned:
            continue
        prefix = 'Run' if in_code_block else cleaned
        value = f'{prefix} `{cleaned}`' if in_code_block else cleaned
        lines.append(value)
    return tuple(lines)


def _field_value(markdown: str, label: str) -> str:
    pattern = re.compile(
        rf'^\s*-?\s*{re.escape(label)}:\s*`?([^`\n]+)',
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(markdown)
    return match.group(1).strip() if match else ''


def _looks_like_e2e_path(path: str) -> bool:
    normalized = path.casefold()
    suffix = PurePosixPath(normalized).suffix
    return (
        suffix in {'.py', '.ts', '.tsx', '.js', '.jsx'}
        and 'e2e' in PurePosixPath(normalized).parts
    )


def _github_blob_url(pull_request: PullRequestRecord, path: str) -> str:
    return (
        f'https://github.com/{pull_request.repo}/blob/{pull_request.merge_sha}/'
        f'{quote(path.strip(), safe="/")}'
    )


def _is_http_url(value: str) -> bool:
    return value.startswith(('https://', 'http://'))


def _deduplicate(
    instructions: list[TestInstruction],
) -> tuple[TestInstruction, ...]:
    seen: set[tuple[GuidanceKind, str, str]] = set()
    unique: list[TestInstruction] = []
    for instruction in instructions:
        key = (instruction.kind, instruction.text.casefold(), instruction.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(instruction)
    return tuple(unique)
