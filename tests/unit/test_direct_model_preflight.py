"""Tests for the direct-model upgrade preflight scan and its report contract."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

import run_direct_model_preflight
from server.services.direct_model_preflight import (
    ARTIFACT_TYPE,
    MAX_REPORTED_FINDINGS,
    build_report,
    find_direct_model_configs,
    render_summary,
    suggested_model,
)


@pytest.mark.parametrize(
    ('model', 'expected'),
    [
        ('azure/gpt-4o', 'openhands/azure-gpt-4o'),
        (
            'bedrock/us.anthropic.claude-sonnet-4-5-v1:0',
            'openhands/us.anthropic.claude-sonnet-4-5-v1:0',
        ),
        ('vertex_ai/gemini-2.5-flash', 'openhands/gemini-2.5-flash'),
        ('openhands/gemini-2.5-flash', None),
        ('litellm_proxy/claude-sonnet-4-5', None),
        ('anthropic/claude-sonnet-4-5', None),
        ('azure/', None),
    ],
)
def test_suggested_model_maps_to_the_proxy_route_name(model, expected):
    assert suggested_model(model) == expected


def test_flags_direct_models_without_a_key_and_reports_where():
    blob = {
        'llm': {'model': 'azure/gpt-4o', 'base_url': None},
        'profiles': {
            'Default': {'model': 'openhands/claude'},
            'Work': {'model': 'vertex_ai/gemini-2.5-flash', 'api_key': ''},
        },
    }

    findings = find_direct_model_configs('org.llm_profiles', 'org-1', blob)

    assert [(f['path'], f['model'], f['suggested_model']) for f in findings] == [
        ('llm', 'azure/gpt-4o', 'openhands/azure-gpt-4o'),
        (
            'profiles.Work',
            'vertex_ai/gemini-2.5-flash',
            'openhands/gemini-2.5-flash',
        ),
    ]
    assert {f['source'] for f in findings} == {'org.llm_profiles'}
    assert {f['row_id'] for f in findings} == {'org-1'}
    assert [f['provider'] for f in findings] == ['Azure', 'Vertex AI']


def test_a_config_with_its_own_key_is_not_flagged():
    blob = {'llm': {'model': 'azure/gpt-4o', 'api_key': 'sk-user-supplied'}}

    assert find_direct_model_configs('org.agent_settings', 'org-1', blob) == []


def test_findings_never_contain_key_material():
    blob = [{'model': 'bedrock/anthropic.claude-v2', 'api_key': None, 'secret': 'x'}]

    findings = find_direct_model_configs('user.llm_profiles', 'u-1', blob)

    assert findings[0]['path'] == '[0]'
    assert 'x' not in json.dumps(findings)


def test_report_truncates_findings_but_keeps_the_true_count():
    findings = [
        {
            'source': 'org.agent_settings',
            'row_id': str(i),
            'path': 'llm',
            'provider': 'Azure',
            'model': 'azure/gpt-4o',
            'suggested_model': 'openhands/azure-gpt-4o',
        }
        for i in range(MAX_REPORTED_FINDINGS + 5)
    ]

    report = build_report(generated_at='t', findings=findings, skipped_sources={})

    assert report['artifact_type'] == ARTIFACT_TYPE
    assert report['summary']['affected_configs'] == MAX_REPORTED_FINDINGS + 5
    assert len(report['findings']) == MAX_REPORTED_FINDINGS
    assert report['findings_truncated'] is True


def test_summary_tells_the_admin_what_to_do():
    findings = find_direct_model_configs(
        'org.agent_settings', 'o', {'llm': {'model': 'azure/gpt-4o'}}
    )
    report = build_report(generated_at='t', findings=findings, skipped_sources={})

    summary = render_summary(report)

    assert 'WARNING: 1 LLM setting(s)' in summary
    assert 'azure/gpt-4o  ->  openhands/azure-gpt-4o' in summary
    assert 'Azure OpenAI Deployments' in summary
    assert 'rotate the provider credentials' in summary
    assert 'not blocked' in summary


def test_clean_install_summary_is_quiet_and_skipped_sources_are_named():
    clean = build_report(generated_at='t', findings=[], skipped_sources={})
    assert 'no LLM settings depend' in render_summary(clean)
    assert 'Not scanned' not in render_summary(clean)

    partial = build_report(
        generated_at='t', findings=[], skipped_sources={'user.llm_profiles': 'boom'}
    )
    assert 'Not scanned' in render_summary(partial)
    assert 'user.llm_profiles' in render_summary(partial)


@pytest.fixture
def database(monkeypatch):
    """In-memory SQLite stand-in for the Postgres tables the scan reads.

    The scan uses only portable SQL. ``decrypt_value`` is replaced with identity
    because the real one needs the app's JWT key service.
    """
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(
            text('CREATE TABLE org (id TEXT, agent_settings TEXT, llm_profiles TEXT)')
        )
        conn.execute(text('CREATE TABLE "user" (id TEXT, llm_profiles TEXT)'))
        conn.execute(
            text(
                'CREATE TABLE org_member (org_id TEXT, user_id TEXT, agent_settings_diff TEXT)'
            )
        )
        conn.execute(
            text(
                'CREATE TABLE user_settings (keycloak_user_id TEXT, agent_settings TEXT)'
            )
        )
        conn.execute(
            text('INSERT INTO org VALUES (:i, :a, :p)'),
            {
                'i': 'org-1',
                'a': json.dumps({'llm': {'model': 'azure/gpt-4o'}}),
                'p': json.dumps({'Work': {'model': 'bedrock/anthropic.claude-v2'}}),
            },
        )
        conn.execute(
            text('INSERT INTO org_member VALUES (:o, :u, :d)'),
            {
                'o': 'org-1',
                'u': 'user-9',
                'd': json.dumps({'llm': {'model': 'vertex_ai/gemini-2.5-flash'}}),
            },
        )
        conn.execute(
            text('INSERT INTO user_settings VALUES (:k, :a)'),
            {'k': 'kc-1', 'a': json.dumps({'llm': {'model': 'openhands/claude'}})},
        )
    monkeypatch.setattr(run_direct_model_preflight, 'decrypt_value', lambda v: v)
    monkeypatch.setattr(
        run_direct_model_preflight, 'session_maker', lambda: _Session(engine)
    )
    return engine


class _Session:
    def __init__(self, engine):
        self._connection = engine.connect()

    def __enter__(self):
        return self._connection

    def __exit__(self, *exc):
        self._connection.close()


def test_scan_reads_every_source_and_reports_the_row_ids(database):
    findings, skipped = run_direct_model_preflight.scan()

    assert skipped == {}
    assert sorted((f['source'], f['row_id'], f['model']) for f in findings) == [
        ('org.agent_settings', 'org-1', 'azure/gpt-4o'),
        ('org.llm_profiles', 'org-1', 'bedrock/anthropic.claude-v2'),
        (
            'org_member.agent_settings_diff',
            'org-1/user-9',
            'vertex_ai/gemini-2.5-flash',
        ),
    ]


def test_a_missing_table_is_skipped_not_fatal(database):
    with database.begin() as conn:
        conn.execute(text('DROP TABLE user_settings'))

    findings, skipped = run_direct_model_preflight.scan()

    assert len(findings) == 3
    assert list(skipped) == ['user_settings.agent_settings']


def test_main_prints_summary_then_json_and_never_fails_the_upgrade(database, capsys):
    assert run_direct_model_preflight.main() == 0

    lines = capsys.readouterr().out.strip().splitlines()
    artifact = json.loads(lines[-1])
    assert artifact['artifact_type'] == ARTIFACT_TYPE
    assert artifact['summary']['affected_configs'] == 3
    assert any(line.startswith('WARNING: 3 LLM setting(s)') for line in lines)


def test_main_exits_zero_even_when_the_scan_itself_crashes(monkeypatch, capsys):
    def boom():
        raise RuntimeError('database down')

    monkeypatch.setattr(run_direct_model_preflight, 'scan', boom)

    assert run_direct_model_preflight.main() == 0

    out = capsys.readouterr().out
    assert 'could not complete (RuntimeError: database down)' in out
    assert (
        json.loads(out.strip().splitlines()[-1])['error']
        == 'RuntimeError: database down'
    )
