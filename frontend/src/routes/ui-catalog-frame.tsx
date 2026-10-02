/* eslint-disable i18next/no-literal-string */
import { useState } from "react";
import { Navigate, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { CreateOrganizationModal } from "#/components/features/org/create-organization-modal";
import { ChangeOrgNameModal } from "#/components/features/org/change-org-name-modal";
import { ConfirmRemoveMemberModal } from "#/components/features/org/confirm-remove-member-modal";
import { ConfirmUpdateRoleModal } from "#/components/features/org/confirm-update-role-modal";
import { DeleteOrgConfirmationModal } from "#/components/features/org/delete-org-confirmation-modal";
import { InviteOrganizationMemberModal } from "#/components/features/org/invite-organization-member-modal";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { MCPServerModal } from "#/components/features/settings/mcp-settings/mcp-server-modal";
import {
  HubModal,
  hubModalBodyClassName,
} from "#/components/features/settings/integrations/integration-modal";
import { HubProviderModalHeader } from "#/components/features/settings/integrations/integration-provider-modal-header";
import { SuperAdminGrantSelfAccessModal } from "#/components/features/super-admin/super-admin-grant-self-access-modal";
import { SuperAdminGroupSetupModal } from "#/components/features/super-admin/super-admin-group-setup-modal";
import {
  SUPER_ADMIN_USERS,
  type SuperAdminOrgRole,
} from "#/components/features/super-admin/super-admin-mock";
import {
  SuperAdminProvisionOrgList,
  SuperAdminUserGroupsModal,
} from "#/components/features/super-admin/super-admin-user-groups-modal";
import { BrandButton } from "#/components/features/settings/brand-button";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { ConfirmationModal } from "#/components/shared/modals/confirmation-modal";
import { I18nKey } from "#/i18n/declaration";
import { isSetupTestHarnessEnabled } from "#/utils/org/setup-test-harness";

const noop = () => undefined;

const PROVISION_ORGS = [
  { id: "acme", label: "Acme Corp" },
  { id: "hands", label: "All Hands AI" },
  { id: "beta", label: "Beta LLC" },
];

function ProvisionFrame() {
  const { t } = useTranslation();
  const [email, setEmail] = useState("ada@acme.org");
  const [password, setPassword] = useState("");
  const [roles, setRoles] = useState<
    Record<string, SuperAdminOrgRole | undefined>
  >({ acme: "member" });

  return (
    <OrgModal
      testId="catalog-provision"
      className="w-[36rem] max-w-[calc(100vw-2rem)]"
      title={t(I18nKey.SUPER_ADMIN$PROVISION_USER)}
      description={t(I18nKey.SUPER_ADMIN$PROVISION_USER_DESCRIPTION)}
      primaryButtonText={t(I18nKey.SUPER_ADMIN$PROVISION_USER)}
      onPrimaryClick={noop}
      onClose={noop}
      showCloseButton
    >
      <div className="flex w-full flex-col gap-3">
        <SettingsInput
          type="email"
          label={t(I18nKey.ORG$CONTACT_EMAIL)}
          value={email}
          placeholder={t(I18nKey.ORG$CONTACT_EMAIL_PLACEHOLDER)}
          onChange={setEmail}
        />
        <SettingsInput
          type="password"
          label={t(I18nKey.SUPER_ADMIN$PROVISION_PASSWORD_OPTIONAL)}
          value={password}
          placeholder={t(I18nKey.SUPER_ADMIN$PROVISION_PASSWORD_PLACEHOLDER)}
          onChange={setPassword}
        />
        <div className="flex flex-col gap-1.5 text-sm">
          <span>{t(I18nKey.SUPER_ADMIN$PROVISION_ORGS)}</span>
          <SuperAdminProvisionOrgList
            items={PROVISION_ORGS}
            roles={roles}
            onRoleChange={(id, nextRole) =>
              setRoles((current) => {
                if (!nextRole) {
                  const next = { ...current };
                  delete next[id];
                  return next;
                }
                return { ...current, [id]: nextRole };
              })
            }
          />
        </div>
      </div>
    </OrgModal>
  );
}

function ProvisionCredentialsFrame() {
  const { t } = useTranslation();
  return (
    <OrgModal
      testId="catalog-provision-credentials"
      className="w-[36rem] max-w-[calc(100vw-2rem)]"
      title={t(I18nKey.SUPER_ADMIN$PROVISION_CREDENTIALS_TITLE)}
      description={t(I18nKey.SUPER_ADMIN$PROVISION_CREDENTIALS_WARNING)}
      primaryButtonText={t(I18nKey.BUTTON$CLOSE)}
      onPrimaryClick={noop}
      onClose={noop}
      hideSecondaryButton
      showCloseButton
    >
      <div className="flex w-full flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-[var(--oh-muted)]">
              {t(I18nKey.SUPER_ADMIN$PROVISION_PASSWORD)}
            </span>
            <BrandButton type="button" variant="secondary" onClick={noop}>
              {t(I18nKey.BUTTON$COPY_TO_CLIPBOARD)}
            </BrandButton>
          </div>
          <div className="break-all rounded-lg border border-[var(--oh-border)] bg-base-secondary p-3 font-mono text-sm">
            example-password
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-[var(--oh-muted)]">
              {t(I18nKey.SUPER_ADMIN$PROVISION_API_KEY)}
            </span>
            <BrandButton type="button" variant="secondary" onClick={noop}>
              {t(I18nKey.BUTTON$COPY_TO_CLIPBOARD)}
            </BrandButton>
          </div>
          <div className="break-all rounded-lg border border-[var(--oh-border)] bg-base-secondary p-3 font-mono text-sm">
            sk-example
          </div>
        </div>
      </div>
    </OrgModal>
  );
}

function GrantAdminFrame() {
  const { t } = useTranslation();
  const [email, setEmail] = useState("riley@all-hands.dev");
  return (
    <OrgModal
      testId="catalog-grant-admin"
      title={t(I18nKey.SUPER_ADMIN$GRANT_ADMIN)}
      description={t(I18nKey.SUPER_ADMIN$GRANT_ADMIN_DESCRIPTION)}
      primaryButtonText={t(I18nKey.SUPER_ADMIN$GRANT_ADMIN)}
      onPrimaryClick={noop}
      onClose={noop}
    >
      <SettingsInput
        type="email"
        label={t(I18nKey.ORG$CONTACT_EMAIL)}
        value={email}
        placeholder={t(I18nKey.ORG$CONTACT_EMAIL_PLACEHOLDER)}
        onChange={setEmail}
      />
    </OrgModal>
  );
}

function IntegrationFrame() {
  const { t } = useTranslation();
  return (
    <HubModal
      ariaLabel="GitHub"
      testId="catalog-integration"
      width="xl"
      onClose={noop}
    >
      <div className={hubModalBodyClassName}>
        <HubProviderModalHeader
          provider="github"
          title={t(I18nKey.SETTINGS$RESOLVER_GITHUB)}
          subtitle={t(I18nKey.SETTINGS$RESOLVER_GITHUB_SUBLINE)}
        />
      </div>
    </HubModal>
  );
}

function FrameBody({ frameId }: { frameId: string }) {
  const { t } = useTranslation();
  switch (frameId) {
    case "starter":
      return <SuperAdminGroupSetupModal forceOpen />;
    case "provision":
      return <ProvisionFrame />;
    case "provision-credentials":
      return <ProvisionCredentialsFrame />;
    case "manage-user":
      return (
        <SuperAdminUserGroupsModal
          user={SUPER_ADMIN_USERS[1]}
          organizations={[
            { id: "2", name: "Acme Corp" },
            { id: "3", name: "Beta LLC" },
            { id: "4", name: "All Hands AI" },
            { id: "5", name: "Northwind Labs" },
          ]}
          onClose={noop}
        />
      );
    case "grant-self":
      return (
        <SuperAdminGrantSelfAccessModal
          orgId="2"
          orgName="Acme Corp"
          userId="u-1"
          onClose={noop}
          onGranted={noop}
        />
      );
    case "create-org":
      return (
        <CreateOrganizationModal
          contactName="Ada Lovelace"
          contactEmail="ada@acme.org"
          onClose={noop}
        />
      );
    case "grant-admin":
      return <GrantAdminFrame />;
    case "remove-setup":
      return (
        <ConfirmationModal
          text={t(I18nKey.SUPER_ADMIN$SETUP_REMOVE_CONFIRM)}
          onConfirm={noop}
          onCancel={noop}
        />
      );
    case "mcp-add":
      return (
        <MCPServerModal
          mode="add"
          existingServers={[]}
          onSubmit={noop}
          onClose={noop}
        />
      );
    case "mcp-delete":
      return (
        <ConfirmationModal
          text={t(I18nKey.SETTINGS$MCP_CONFIRM_DELETE)}
          onConfirm={noop}
          onCancel={noop}
        />
      );
    case "integration":
      return <IntegrationFrame />;
    case "invite":
      return <InviteOrganizationMemberModal onClose={noop} />;
    case "change-role":
      return (
        <ConfirmUpdateRoleModal
          memberEmail="alice@acme.org"
          newRole="admin"
          onConfirm={noop}
          onCancel={noop}
        />
      );
    case "remove-member":
      return (
        <ConfirmRemoveMemberModal
          memberEmail="alice@acme.org"
          onConfirm={noop}
          onCancel={noop}
        />
      );
    case "rename-org":
      return <ChangeOrgNameModal onClose={noop} />;
    case "delete-org":
      return <DeleteOrgConfirmationModal onClose={noop} />;
    default:
      return (
        <p className="p-8 text-sm text-[var(--oh-muted)]">Unknown frame.</p>
      );
  }
}

export default function UiCatalogFrame() {
  const { frameId } = useParams();
  if (!isSetupTestHarnessEnabled()) {
    return <Navigate to="/" replace />;
  }
  return (
    <main className="min-h-screen bg-base">
      <FrameBody frameId={frameId ?? ""} />
    </main>
  );
}
