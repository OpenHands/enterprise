from unittest.mock import Mock
from uuid import uuid4

from storage.jira_dc_integration_store import workspace_available_install_wide
from storage.jira_dc_workspace import JiraDcWorkspace


def _workspace(org_id=None, admin_user_id='admin-1', status='active'):
    workspace = Mock(spec=JiraDcWorkspace)
    workspace.org_id = org_id
    workspace.admin_user_id = admin_user_id
    workspace.status = status
    return workspace


class TestWorkspaceAvailableInstallWide:
    """The one rule behind GET /workspaces/status and the enricher's token
    injection: the install's active connection serves every org; its org_id is
    the owning/admin org, not a visibility boundary."""

    def test_team_org_stamp_is_available_to_everyone(self):
        workspace = _workspace(org_id=uuid4(), admin_user_id='admin-1')
        assert workspace_available_install_wide(workspace) is True

    def test_personal_org_stamp_is_available_to_everyone(self):
        admin_id = uuid4()
        workspace = _workspace(org_id=admin_id, admin_user_id=str(admin_id))
        assert workspace_available_install_wide(workspace) is True

    def test_workspace_without_org_is_available_to_everyone(self):
        assert workspace_available_install_wide(_workspace(org_id=None)) is True

    def test_inactive_workspace_is_not_available(self):
        workspace = _workspace(org_id=uuid4(), status='inactive')
        assert workspace_available_install_wide(workspace) is False
