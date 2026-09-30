import { useState } from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useUpdateSuperAdminUserGroups } from "#/hooks/mutation/use-super-admin-mutations";
import { I18nKey } from "#/i18n/declaration";
import type { SuperAdminOrgRole, SuperAdminUserRow } from "./super-admin-mock";

interface ChecklistItem {
  id: string;
  label: string;
  meta?: string;
}

/**
 * Checkbox list of organizations. Callers own the selected ids so one list
 * can drive suspend/remove and another can drive add.
 */
export function SuperAdminOrgChecklist({
  items,
  selectedIds,
  onToggle,
  testIdPrefix,
  emptyMessage,
}: {
  items: ChecklistItem[];
  selectedIds: string[];
  onToggle: (id: string) => void;
  testIdPrefix: string;
  emptyMessage?: string;
}) {
  if (items.length === 0) {
    return emptyMessage ? (
      <p className="text-sm text-[var(--oh-muted)]">{emptyMessage}</p>
    ) : null;
  }

  return (
    <ul className="flex max-h-40 flex-col gap-2 overflow-y-auto">
      {items.map((item) => {
        const checked = selectedIds.includes(item.id);
        return (
          <li key={item.id}>
            <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-[var(--oh-border)] px-3 py-2 text-sm">
              <input
                type="checkbox"
                data-testid={`${testIdPrefix}-${item.id}`}
                className="h-4 w-4 accent-white"
                checked={checked}
                onChange={() => onToggle(item.id)}
              />
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              {item.meta ? (
                <span className="shrink-0 text-xs capitalize text-[var(--oh-muted)]">
                  {item.meta}
                </span>
              ) : null}
            </label>
          </li>
        );
      })}
    </ul>
  );
}

function toggleId(current: string[], id: string) {
  return current.includes(id)
    ? current.filter((item) => item !== id)
    : [...current, id];
}

function membershipMeta(
  role: string,
  status: string | null | undefined,
  suspendedLabel: string,
) {
  const suspended = status === "inactive" || status === "suspended";
  return suspended ? `${role} · ${suspendedLabel}` : role;
}

export function SuperAdminUserGroupsModal({
  user,
  organizations,
  onClose,
}: {
  user: SuperAdminUserRow;
  organizations: { id: string; name: string }[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const updateGroups = useUpdateSuperAdminUserGroups();
  const [selectedCurrent, setSelectedCurrent] = useState<string[]>([]);
  const [selectedAdd, setSelectedAdd] = useState<string[]>([]);
  const [role, setRole] = useState<SuperAdminOrgRole>("member");

  const memberIds = new Set(
    user.memberships.map((membership) => membership.orgId),
  );
  const currentItems: ChecklistItem[] = user.memberships.map((membership) => ({
    id: membership.orgId,
    label: membership.orgName,
    meta: membershipMeta(
      membership.role,
      membership.status,
      t(I18nKey.SUPER_ADMIN$MEMBERSHIP_SUSPENDED),
    ),
  }));
  const addItems: ChecklistItem[] = organizations
    .filter((org) => !memberIds.has(org.id))
    .map((org) => ({ id: org.id, label: org.name }));

  const run = (
    action: "suspend" | "resume" | "remove" | "add",
    orgIds: string[],
    onDone: () => void,
  ) => {
    if (orgIds.length === 0 || updateGroups.isPending) {
      return;
    }
    updateGroups.mutate(
      {
        userId: user.id,
        action,
        orgIds,
        ...(action === "add" ? { role } : {}),
      },
      { onSuccess: onDone },
    );
  };

  return (
    <OrgModal
      testId="super-admin-groups-modal"
      className="max-h-[80vh] w-[32rem] max-w-[calc(100vw-2rem)] overflow-y-auto"
      title={t(I18nKey.SUPER_ADMIN$MANAGE_GROUPS)}
      description={t(I18nKey.SUPER_ADMIN$MANAGE_GROUPS_DESCRIPTION)}
      primaryButtonText={t(I18nKey.BUTTON$CLOSE)}
      onPrimaryClick={onClose}
      onClose={onClose}
      hideSecondaryButton
      isLoading={updateGroups.isPending}
    >
      <div className="flex w-full flex-col gap-4">
        <p className="truncate text-sm text-[var(--oh-muted)]">
          {user.name}
          {user.email ? ` · ${user.email}` : ""}
        </p>
        <section className="flex flex-col gap-2">
          <h4 className="text-sm font-medium">
            {t(I18nKey.SUPER_ADMIN$CURRENT_GROUPS)}
          </h4>
          <SuperAdminOrgChecklist
            items={currentItems}
            selectedIds={selectedCurrent}
            onToggle={(id) =>
              setSelectedCurrent((current) => toggleId(current, id))
            }
            testIdPrefix="super-admin-group-current"
            emptyMessage={t(I18nKey.SUPER_ADMIN$NO_GROUPS)}
          />
          <div className="flex flex-wrap gap-2">
            <BrandButton
              type="button"
              variant="secondary"
              testId="super-admin-groups-suspend"
              isDisabled={
                selectedCurrent.length === 0 || updateGroups.isPending
              }
              onClick={() =>
                run("suspend", selectedCurrent, () => setSelectedCurrent([]))
              }
            >
              {t(I18nKey.SUPER_ADMIN$SUSPEND_SELECTED)}
            </BrandButton>
            <BrandButton
              type="button"
              variant="secondary"
              testId="super-admin-groups-resume"
              isDisabled={
                selectedCurrent.length === 0 || updateGroups.isPending
              }
              onClick={() =>
                run("resume", selectedCurrent, () => setSelectedCurrent([]))
              }
            >
              {t(I18nKey.SUPER_ADMIN$RESUME_SELECTED)}
            </BrandButton>
            <BrandButton
              type="button"
              variant="danger"
              testId="super-admin-groups-remove"
              isDisabled={
                selectedCurrent.length === 0 || updateGroups.isPending
              }
              onClick={() =>
                run("remove", selectedCurrent, () => setSelectedCurrent([]))
              }
            >
              {t(I18nKey.SUPER_ADMIN$REMOVE_SELECTED)}
            </BrandButton>
          </div>
        </section>
        <section className="flex flex-col gap-2">
          <h4 className="text-sm font-medium">
            {t(I18nKey.SUPER_ADMIN$ADD_TO_GROUPS)}
          </h4>
          <SuperAdminOrgChecklist
            items={addItems}
            selectedIds={selectedAdd}
            onToggle={(id) =>
              setSelectedAdd((current) => toggleId(current, id))
            }
            testIdPrefix="super-admin-group-add"
            emptyMessage={t(I18nKey.SUPER_ADMIN$NO_OTHER_GROUPS)}
          />
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-[var(--oh-muted)]">
              {t(I18nKey.SUPER_ADMIN$PROVISION_ROLE)}
            </span>
            <select
              data-testid="super-admin-groups-role"
              className="rounded-lg border border-[var(--oh-border)] bg-[var(--oh-surface)] px-3 py-2 text-foreground"
              value={role}
              onChange={(event) =>
                setRole(event.target.value as SuperAdminOrgRole)
              }
            >
              <option value="member">{t(I18nKey.ORG$ROLE_MEMBER)}</option>
              <option value="admin">{t(I18nKey.ORG$ROLE_ADMIN)}</option>
              <option value="owner">{t(I18nKey.ORG$ROLE_OWNER)}</option>
            </select>
          </label>
          <BrandButton
            type="button"
            variant="primary"
            testId="super-admin-groups-add"
            isDisabled={selectedAdd.length === 0 || updateGroups.isPending}
            onClick={() => run("add", selectedAdd, () => setSelectedAdd([]))}
          >
            {t(I18nKey.SUPER_ADMIN$ADD_SELECTED)}
          </BrandButton>
        </section>
      </div>
    </OrgModal>
  );
}
