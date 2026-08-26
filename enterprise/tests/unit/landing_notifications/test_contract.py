import json
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import validate
from server.services.landing_notifications.models import (
    Environment,
    EnvironmentRelease,
    ReleaseArtifact,
    ReleaseComponent,
)

_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / '.github'
    / 'landing-checklist'
    / 'environment-release.schema.json'
)


def test_runtime_event_matches_published_schema() -> None:
    event = EnvironmentRelease(
        event_id='openhands-cloud:replicated-stable:1450',
        environment=Environment.REPLICATED_STABLE,
        released_at=datetime(2026, 8, 26, tzinfo=UTC),
        producer_repo='OpenHands/OpenHands-Cloud',
        producer_sha='a' * 40,
        run_url='https://github.com/OpenHands/OpenHands-Cloud/actions/runs/1',
        environment_url='https://stable.example.com',
        artifact=ReleaseArtifact(
            kind='replicated-release',
            version='1.2.3',
            sequence=1450,
            kots_cursor=271,
        ),
        components=(
            ReleaseComponent(
                repo='OpenHands/enterprise',
                previous_ref='1.2.2',
                released_ref='1.2.3',
            ),
        ),
    )
    schema = json.loads(_SCHEMA_PATH.read_text())

    validate(event.model_dump(mode='json'), schema)
    assert set(schema['properties']['environment']['enum']) == set(Environment)
