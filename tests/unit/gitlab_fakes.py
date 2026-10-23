"""An in-memory GitLab for the webhook installer's tests.

It keeps a list of hooks per resource, so tests can assert how many hooks a
resource ends up with and that a delivery signed with a hook's credentials
authenticates.
"""

from integrations.utils import GITLAB_WEBHOOK_URL
from storage.gitlab_webhook import WebhookStatus
from storage.gitlab_webhook_store import GitlabWebhookStore

USER = 'user-1'


class FakeGitLab:
    def __init__(self):
        self.hooks: dict[str, list[dict]] = {}
        self.external_auth_id = USER
        self.resource_exists = True
        self.is_admin = True
        self.delete_status = None
        self.lose_create_response = False
        self.fail_create = False
        self.on_resource_check = None
        self.on_admin_check = None
        self.before_create = None
        self._next_id = 1

    def for_user(self, external_auth_id):
        """Stand-in for ``GitLabServiceImpl(external_auth_id=...)``."""
        self.external_auth_id = external_auth_id
        return self

    def add_hook(self, resource_id, secret='old-secret', uuid='old-uuid', user=USER):
        hook = {
            'id': self._next_id,
            'url': GITLAB_WEBHOOK_URL,
            'secret': secret,
            'uuid': uuid,
            'user': user,
        }
        self._next_id += 1
        self.hooks.setdefault(resource_id, []).append(hook)
        return hook

    async def check_resource_exists(self, resource_type, resource_id):
        if self.on_resource_check:
            await self.on_resource_check()
        return self.resource_exists, None

    async def check_user_has_admin_access_to_resource(self, resource_type, resource_id):
        if self.on_admin_check:
            await self.on_admin_check()
        return self.is_admin, None

    async def check_webhook_exists_on_resource(
        self, resource_type, resource_id, webhook_url
    ):
        exists = any(h['url'] == webhook_url for h in self.hooks.get(resource_id, []))
        return exists, None

    async def install_webhook(
        self,
        *,
        resource_type,
        resource_id,
        webhook_name,
        webhook_url,
        webhook_secret,
        webhook_uuid,
        scopes,
    ):
        if self.before_create:
            await self.before_create()
        if self.fail_create:
            raise RuntimeError('pod stopped before the create')
        hook = self.add_hook(
            resource_id, webhook_secret, webhook_uuid, self.external_auth_id
        )
        if self.lose_create_response:
            return None, WebhookStatus.INVALID
        return str(hook['id']), None

    async def delete_webhooks_with_url(self, resource_type, resource_id, webhook_url):
        if self.delete_status:
            return 0, self.delete_status
        hooks = self.hooks.get(resource_id, [])
        keep = [h for h in hooks if h['url'] != webhook_url]
        self.hooks[resource_id] = keep
        return len(hooks) - len(keep), None


async def assert_delivery_authenticates(store: GitlabWebhookStore, hook: dict):
    assert await store.get_webhook_secret(hook['uuid'], hook['user']) == hook['secret']
