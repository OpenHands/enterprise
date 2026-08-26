from typing import Any

import httpx
from pydantic import BaseModel, Field
from server.services.landing_notifications.consumer_models import FeatureReleaseUpdate
from server.services.landing_notifications.models import EnvironmentRelease

_LINEAR_API_URL = 'https://api.linear.app/graphql'


class LinearCommentPlan(BaseModel):
    issue_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    body: str = Field(min_length=1)


def plan_linear_comment(
    release: EnvironmentRelease,
    update: FeatureReleaseUpdate,
) -> LinearCommentPlan:
    transition = ''
    if update.became_testable:
        transition = (
            '\n\nThis feature is now ready for its planned environment testing.'
        )
    if update.became_production_enabled:
        transition = '\n\nAll configured final targets are ready; production enablement is unblocked.'

    return LinearCommentPlan(
        issue_id=update.linear_issue_id,
        event_id=release.event_id,
        body=(
            f'<!-- environment-release:{release.event_id} -->\n'
            f'**{release.environment.value} is ready**\n\n'
            f'- Stage: `{update.previous_stage.value}` → `{update.current_stage.value}`\n'
            f'- Artifact: `{release.artifact.version}`\n'
            f'- Environment: {release.environment_url}\n'
            f'- Release evidence: {release.run_url}'
            f'{transition}'
        ),
    )


class LinearClient:
    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(timeout=20)
        self._owns_client = client is None
        self._headers = {
            'Authorization': api_key,
            'Content-Type': 'application/json',
        }

    def create_comment(self, plan: LinearCommentPlan) -> str:
        response = self._client.post(
            _LINEAR_API_URL,
            headers=self._headers,
            json={
                'query': (
                    'mutation($input: CommentCreateInput!) {'
                    ' commentCreate(input: $input) {'
                    ' success comment { id }'
                    ' }'
                    '}'
                ),
                'variables': {'input': {'issueId': plan.issue_id, 'body': plan.body}},
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if errors := payload.get('errors'):
            raise RuntimeError(f'Linear comment creation failed: {errors}')
        result = payload['data']['commentCreate']
        if not result['success']:
            raise RuntimeError('Linear comment creation was not successful')
        return str(result['comment']['id'])

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> 'LinearClient':
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
