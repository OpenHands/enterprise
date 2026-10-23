from integrations.gitlab.hook_identity import hook_identity, hook_url, points_at
from integrations.utils import GITLAB_WEBHOOK_URL


def test_an_identity_url_round_trips_its_uuid():
    url = hook_url('uuid-1')

    assert url.startswith(f'{GITLAB_WEBHOOK_URL}?')
    assert hook_identity(url) == 'uuid-1'
    assert points_at(url, GITLAB_WEBHOOK_URL)


def test_a_legacy_hook_points_at_our_url_without_an_identity():
    assert points_at(GITLAB_WEBHOOK_URL, GITLAB_WEBHOOK_URL)
    assert hook_identity(GITLAB_WEBHOOK_URL) is None


def test_other_urls_are_not_ours():
    assert not points_at(f'{GITLAB_WEBHOOK_URL}/other', GITLAB_WEBHOOK_URL)
    assert not points_at('https://someone-else/hook?oh_hook=x', GITLAB_WEBHOOK_URL)
    assert not points_at(None, GITLAB_WEBHOOK_URL)
