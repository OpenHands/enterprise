"""Pre-upgrade scan for LLM settings that relied on sandbox provider credentials.

Replicated installs used to inject the Azure, Bedrock and Vertex credentials into
every sandbox environment, so a profile whose model names the provider directly
(``azure/gpt-4o``) and carries no key of its own could authenticate from that
environment. The credentials now stay in the bundled LiteLLM proxy, so those
profiles can no longer authenticate and must move to the managed route.

Everything here is pure: callers load and decrypt the rows. No secret value is
read into a finding, only the model name and where it is stored.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from openhands.app_server.settings.llm_profiles import has_real_api_key

ARTIFACT_TYPE = 'direct_model_preflight'
ARTIFACT_VERSION = 1

# LiteLLM prefix -> how the bundled proxy names the route for that model
# (replicated/openhands.yaml: azure-<deployment>, bedrock <id>, vertex <name>).
_ROUTE_NAME_TEMPLATES = {
    'azure/': 'azure-{name}',
    'bedrock/': '{name}',
    'vertex_ai/': '{name}',
}
_PROVIDER_LABELS = {'azure/': 'Azure', 'bedrock/': 'Bedrock', 'vertex_ai/': 'Vertex AI'}

MAX_REPORTED_FINDINGS = 200


def suggested_model(model: str) -> str | None:
    """Return the managed ``openhands/<route>`` that replaces a direct model."""
    for prefix, template in _ROUTE_NAME_TEMPLATES.items():
        name = model.removeprefix(prefix)
        if model.startswith(prefix) and name:
            return f'openhands/{template.format(name=name)}'
    return None


def _provider(model: str) -> str | None:
    return next(
        (
            label
            for prefix, label in _PROVIDER_LABELS.items()
            if model.startswith(prefix)
        ),
        None,
    )


def iter_llm_configs(blob: Any, path: str = '') -> Iterator[tuple[str, Mapping]]:
    """Yield ``(path, dict)`` for every nested dict that looks like an LLM config."""
    if isinstance(blob, Mapping):
        if isinstance(blob.get('model'), str):
            yield path or '.', blob
        for key, value in blob.items():
            yield from iter_llm_configs(value, f'{path}.{key}' if path else str(key))
    elif isinstance(blob, list):
        for index, value in enumerate(blob):
            yield from iter_llm_configs(value, f'{path}[{index}]')


def find_direct_model_configs(
    source: str, row_id: str, blob: Any
) -> list[dict[str, str]]:
    """Return one finding per direct-provider LLM config that has no API key."""
    findings = []
    for path, llm in iter_llm_configs(blob):
        model = llm['model']
        replacement = suggested_model(model)
        if replacement is None or has_real_api_key(llm.get('api_key')):
            continue
        findings.append(
            {
                'source': source,
                'row_id': row_id,
                'path': path,
                'provider': _provider(model) or '',
                'model': model,
                'suggested_model': replacement,
            }
        )
    return findings


def build_report(
    *,
    generated_at: str,
    findings: list[dict[str, str]],
    skipped_sources: dict[str, str],
    error: str | None = None,
) -> dict[str, Any]:
    return {
        'artifact_type': ARTIFACT_TYPE,
        'artifact_version': ARTIFACT_VERSION,
        'generated_at': generated_at,
        'error': error,
        'summary': {
            'affected_configs': len(findings),
            'by_provider': _count_by(findings, 'provider'),
            'by_source': _count_by(findings, 'source'),
        },
        'skipped_sources': skipped_sources,
        'findings': findings[:MAX_REPORTED_FINDINGS],
        'findings_truncated': len(findings) > MAX_REPORTED_FINDINGS,
    }


def _count_by(findings: list[dict[str, str]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding[field]] = counts.get(finding[field], 0) + 1
    return dict(sorted(counts.items()))


def render_summary(report: Mapping[str, Any]) -> str:
    """Human-readable warning printed ahead of the JSON artifact."""
    lines = _summary_lines(report)
    if report['skipped_sources']:
        lines.append(
            'Not scanned (see skipped_sources in the JSON artifact): '
            + ', '.join(sorted(report['skipped_sources']))
        )
    return '\n'.join(lines)


def _summary_lines(report: Mapping[str, Any]) -> list[str]:
    if report['error']:
        return [
            f'Direct-model preflight could not complete ({report["error"]}). '
            'The upgrade is not blocked.'
        ]
    count = report['summary']['affected_configs']
    if not count:
        return [
            'Direct-model preflight: no LLM settings depend on sandbox provider '
            'credentials.'
        ]

    suggestions = sorted(
        {(f['model'], f['suggested_model']) for f in report['findings']}
    )
    return [
        f'WARNING: {count} LLM setting(s) use a provider-direct model with no API key '
        'of their own.',
        'Provider credentials are no longer injected into sandboxes, so these settings '
        'will fail to authenticate after this upgrade.',
        'To fix: switch each one to the managed route below. The route must be listed '
        'in the Admin Console model list for its provider (Azure OpenAI Deployments, '
        'Bedrock Model IDs or Vertex AI Models).',
        *(f'  {model}  ->  {replacement}' for model, replacement in suggestions),
        'Then rotate the provider credentials: sandboxes created before this upgrade '
        'still hold the old values.',
        'The upgrade is not blocked. Affected settings (table, row, path) are in the '
        'JSON artifact below.',
    ]
