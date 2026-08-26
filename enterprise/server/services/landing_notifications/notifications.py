from html import escape

from server.services.landing_notifications.consumer_models import (
    ContributorRelease,
    PullRequestRecord,
)
from server.services.landing_notifications.guidance import build_test_guidance
from server.services.landing_notifications.guidance_models import LinearIssueContext
from server.services.landing_notifications.models import (
    Environment,
    EnvironmentRelease,
)
from server.services.landing_notifications.notification_models import (
    DeliveryAttempt,
    DeliveryChannel,
    NotificationContent,
    RecipientProfile,
)

_ENVIRONMENT_LABELS = {
    Environment.SAAS_STAGING: 'SaaS staging',
    Environment.SAAS_PRODUCTION: 'SaaS production',
    Environment.REPLICATED_UNSTABLE: 'Replicated unstable',
    Environment.REPLICATED_BETA: 'Replicated beta',
    Environment.REPLICATED_STABLE: 'Replicated stable',
}
_TEST_ENVIRONMENTS = {
    Environment.SAAS_STAGING,
    Environment.REPLICATED_UNSTABLE,
    Environment.REPLICATED_BETA,
    Environment.REPLICATED_STABLE,
}


def render_notification(
    release: EnvironmentRelease,
    contributor: ContributorRelease,
    pull_requests: list[PullRequestRecord],
    linear_issues: dict[str, LinearIssueContext] | None = None,
) -> NotificationContent:
    linear_issues = linear_issues or {}
    records = {
        (pull_request.repo, pull_request.number): pull_request
        for pull_request in pull_requests
    }
    included = [
        records[(reference.repo, reference.number)]
        for reference in contributor.pull_requests
        if (reference.repo, reference.number) in records
    ]
    label = _ENVIRONMENT_LABELS[release.environment]
    status = (
        'ready to test in' if release.environment in _TEST_ENVIRONMENTS else 'live in'
    )
    subject = f'Your changes are {status} {label}'

    text_lines = [
        subject,
        '',
        f'Environment: {release.environment_url}',
        f'Release: {release.artifact.version}',
        f'Evidence: {release.run_url}',
        '',
    ]
    html_sections = [
        f'<h1>{escape(subject)}</h1>',
        f'<p><a href="{escape(release.environment_url)}">Open {escape(label)}</a> · '
        f'<a href="{escape(release.run_url)}">Release evidence</a></p>',
    ]
    slack_blocks: list[dict[str, object]] = [
        {
            'type': 'header',
            'text': {'type': 'plain_text', 'text': subject, 'emoji': True},
        },
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': (
                    f'<{release.environment_url}|Open {label}> · '
                    f'<{release.run_url}|Release evidence> · '
                    f'Version `{release.artifact.version}`'
                ),
            },
        },
    ]

    for pull_request in included:
        issue = (
            linear_issues.get(pull_request.linear_identifier)
            if pull_request.linear_identifier
            else None
        )
        guidance = build_test_guidance(pull_request, issue)
        text_lines.extend(
            [
                f'{pull_request.repo}#{pull_request.number}: {pull_request.title}',
                pull_request.url,
            ]
        )
        html_items: list[str] = []
        slack_items: list[str] = []
        for instruction in guidance.instructions:
            marker = (
                'Verified E2E'
                if instruction.kind.value == 'verified-e2e'
                else 'Suggested check'
            )
            text_lines.append(f'- [{marker}] {instruction.text}: {instruction.url}')
            html_items.append(
                f'<li><strong>{escape(marker)}:</strong> '
                f'<a href="{escape(instruction.url)}">{escape(instruction.text)}</a></li>'
            )
            slack_items.append(
                f'• *{marker}:* <{instruction.url}|{_escape_slack(instruction.text)}>'
            )
        text_lines.append('')
        html_sections.append(
            f'<h2><a href="{escape(pull_request.url)}">'
            f'{escape(pull_request.repo)}#{pull_request.number}: '
            f'{escape(pull_request.title)}</a></h2><ul>{"".join(html_items)}</ul>'
        )
        slack_blocks.append(
            {
                'type': 'section',
                'text': {
                    'type': 'mrkdwn',
                    'text': (
                        f'*<{pull_request.url}|{pull_request.repo}#{pull_request.number}: '
                        f'{_escape_slack(pull_request.title)}>*\n'
                        + '\n'.join(slack_items)
                    ),
                },
            }
        )

    return NotificationContent(
        event_id=release.event_id,
        recipient_login=contributor.login,
        subject=subject,
        text='\n'.join(text_lines).rstrip(),
        html=''.join(html_sections),
        slack_blocks=tuple(slack_blocks),
    )


def plan_delivery_attempts(
    release: EnvironmentRelease,
    recipient: RecipientProfile,
    content: NotificationContent,
    delivered_keys: set[str],
    email_from: str = 'OpenHands Releases <releases@openhands.dev>',
) -> tuple[DeliveryAttempt, ...]:
    attempts: list[DeliveryAttempt] = []
    channels = recipient.channels.get(release.environment, frozenset())
    for channel in sorted(channels):
        key = f'{release.event_id}:{recipient.github_login}:{channel}'
        if key in delivered_keys:
            continue
        if channel == DeliveryChannel.EMAIL and recipient.email:
            attempts.append(
                DeliveryAttempt(
                    channel=channel,
                    delivery_key=key,
                    destination=recipient.email,
                    payload={
                        'from': email_from,
                        'to': [recipient.email],
                        'subject': content.subject,
                        'html': content.html,
                        'text': content.text,
                    },
                )
            )
        if channel == DeliveryChannel.SLACK and recipient.slack_user_id:
            attempts.append(
                DeliveryAttempt(
                    channel=channel,
                    delivery_key=key,
                    destination=recipient.slack_user_id,
                    payload={
                        'channel': recipient.slack_user_id,
                        'text': content.subject,
                        'blocks': list(content.slack_blocks),
                    },
                )
            )
    return tuple(attempts)


def _escape_slack(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
