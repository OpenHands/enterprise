"""Worklog rules that need no database: slugs, titles, entry parsing, cursors."""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from server.services.worklog_service import (
    JSON_CONTENT,
    MARKDOWN,
    derive_markdown_title,
    encode_cursor,
    normalize_worklog_slug,
    parse_entry,
    parse_since,
    slugify_worklog_name,
)
from storage.worklog_store import EntryCursor


@pytest.mark.parametrize(
    ('name', 'slug'),
    [
        ('Release notes', 'release-notes'),
        ('  Weekly -- Review!  ', 'weekly-review'),
        ('Ünïcode 2', 'n-code-2'),
        ('a' * 100, 'a' * 63),
    ],
)
def test_a_slug_is_derived_from_the_name(name, slug):
    assert slugify_worklog_name(name) == slug


def test_a_name_with_nothing_to_derive_from_is_refused():
    with pytest.raises(ValueError, match='provide a slug'):
        slugify_worklog_name('!!!')


def test_a_supplied_slug_is_lowercased_and_trimmed():
    assert normalize_worklog_slug('  QA-One ') == 'qa-one'


@pytest.mark.parametrize('slug', ['a_b', '-a', 'a-', 'a--b', '', 'a' * 64, 'a b'])
def test_a_bad_slug_is_refused(slug):
    with pytest.raises(ValueError):
        normalize_worklog_slug(slug)


@pytest.mark.parametrize(
    ('body', 'title'),
    [
        (
            '## INSTRUCTION: stop launching workers\n\nmore',
            'INSTRUCTION: stop launching workers',
        ),
        ('# One\nbody', 'One'),
        ('###### Six', 'Six'),
        ('\n\n  ## Indented heading  \n', 'Indented heading'),
        ('Launched worker 3\nsecond line', 'Launched worker 3'),
        ('\n\nfirst non-empty line', 'first non-empty line'),
        ('#nospace is not a heading', '#nospace is not a heading'),
        ('x' * 300, 'x' * 120),
        ('## ' + 'y' * 300, 'y' * 120),
        ('', None),
        ('   \n\n  ', None),
        ('##', '##'),
    ],
)
def test_markdown_title_is_the_first_non_empty_line(body, title):
    assert derive_markdown_title(body) == title


def test_markdown_is_stored_verbatim_with_crlf_and_non_ascii_text():
    body = '## Plan\r\n\r\n```json\r\n{"a": 1}\r\n```\r\nnaïve – 日本語 🚀\r\n'

    parsed = parse_entry(MARKDOWN, body.encode('utf-8'), None)

    assert parsed.body == body
    assert parsed.content_type == 'markdown'
    assert parsed.title == 'Plan'


def test_json_is_stored_as_the_received_string_not_reserialized():
    body = '{ "b":1,   "a" : [1, 2,3] }\n'

    parsed = parse_entry(JSON_CONTENT, body.encode(), None)

    assert parsed.body == body
    assert parsed.title is None


def test_a_title_header_overrides_the_derived_title_for_both_types():
    markdown = parse_entry(MARKDOWN, b'## Derived', '  Header title ')
    json_entry = parse_entry(JSON_CONTENT, b'{}', 'Header title')

    assert markdown.title == 'Header title'
    assert json_entry.title == 'Header title'


def test_a_blank_title_header_is_ignored():
    assert parse_entry(MARKDOWN, b'## Derived', '   ').title == 'Derived'


def test_a_title_header_is_cut_to_255_characters():
    assert parse_entry(MARKDOWN, b'x', 'h' * 400).title == 'h' * 255


def test_invalid_json_is_422_with_the_parse_error_and_position():
    with pytest.raises(HTTPException) as exc:
        parse_entry(JSON_CONTENT, b'{"a": 1,\n  "b": }', None)

    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert detail['message'].startswith('The body is not valid JSON')
    assert (detail['line'], detail['column']) == (2, 8)
    assert detail['position'] == 16


@pytest.mark.parametrize('body', [b'NaN', b'[Infinity]', b'{"a": -Infinity}'])
def test_json_constants_that_are_not_json_are_422(body):
    with pytest.raises(HTTPException) as exc:
        parse_entry(JSON_CONTENT, body, None)

    assert exc.value.status_code == 422


def test_empty_json_is_422():
    with pytest.raises(HTTPException) as exc:
        parse_entry(JSON_CONTENT, b'', None)

    assert exc.value.status_code == 422


@pytest.mark.parametrize('content_type', [MARKDOWN, JSON_CONTENT])
def test_invalid_utf8_is_422_for_both_types(content_type):
    with pytest.raises(HTTPException) as exc:
        parse_entry(content_type, b'ok \xff\xfe', None)

    assert exc.value.status_code == 422
    assert 'UTF-8' in exc.value.detail


@pytest.mark.parametrize('content_type', [MARKDOWN, JSON_CONTENT])
def test_a_nul_character_is_422(content_type):
    with pytest.raises(HTTPException) as exc:
        parse_entry(content_type, b'a\x00b', None)

    assert exc.value.status_code == 422


def test_a_cursor_round_trips_to_the_same_position():
    created_at = datetime(2026, 11, 13, 5, 30, 1, 123456, tzinfo=timezone.utc)

    parsed = parse_since(encode_cursor(created_at, 42))

    assert parsed == EntryCursor(created_at, 42)


def test_since_accepts_an_iso_timestamp_with_an_offset():
    parsed = parse_since('2026-11-13T05:30:00+00:00')

    assert parsed == datetime(2026, 11, 13, 5, 30, tzinfo=timezone.utc)


def test_since_may_be_absent():
    assert parse_since(None) is None
    assert parse_since('') is None


@pytest.mark.parametrize('since', ['garbage!', '2026-11-13T05:30:00', 'Zm9v'])
def test_a_bad_since_is_422(since):
    with pytest.raises(HTTPException) as exc:
        parse_since(since)

    assert exc.value.status_code == 422
