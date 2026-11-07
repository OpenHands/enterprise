import json

from openhands.app_server.static import describe_agent_canvas_bundle


def test_absent_bundle_says_canvas_is_not_served(tmp_path):
    message = describe_agent_canvas_bundle(str(tmp_path / 'canvas'))

    assert message.startswith('No Agent Canvas bundle at ')
    assert 'this app does not serve /canvas' in message


def test_bundle_without_manifest_has_unknown_ref(tmp_path):
    (tmp_path / 'index.html').write_text('<html></html>')

    message = describe_agent_canvas_bundle(str(tmp_path))

    assert 'bundle of unknown ref' in message


def test_bundle_with_unreadable_manifest_has_unknown_ref(tmp_path):
    (tmp_path / 'agent-canvas-build.json').write_text('{not json')

    assert 'bundle of unknown ref' in describe_agent_canvas_bundle(str(tmp_path))


def test_bundle_with_non_object_manifest_has_unknown_ref(tmp_path):
    (tmp_path / 'agent-canvas-build.json').write_text('["v1.35.0"]')

    assert 'bundle of unknown ref' in describe_agent_canvas_bundle(str(tmp_path))


def test_bundle_manifest_names_ref_and_commit(tmp_path):
    (tmp_path / 'agent-canvas-build.json').write_text(
        json.dumps({'ref': 'v1.35.0', 'commit': 'abc123'})
    )

    message = describe_agent_canvas_bundle(str(tmp_path))

    assert message == (
        f'Serving Agent Canvas ref=v1.35.0 commit=abc123 at /canvas from {tmp_path}'
    )


def test_prefetched_bundle_manifest_has_unknown_commit(tmp_path):
    (tmp_path / 'agent-canvas-build.json').write_text(
        json.dumps({'ref': 'main', 'commit': None})
    )

    assert 'ref=main commit=unknown' in describe_agent_canvas_bundle(str(tmp_path))
