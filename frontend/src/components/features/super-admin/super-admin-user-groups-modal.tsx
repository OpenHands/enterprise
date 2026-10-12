import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { FaChevronLeft } from "react-icons/fa6";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { OrgModal } from "#/components/shared/modals/org-modal";
import {
  useRemoveSuperAdminUser,
  useUpdateSuperAdminUserGroups,
  useUpdateSuperAdminUserStatus,
} from "#/hooks/mutation/use-super-admin-mutations";
import { I18nKey } from "#/i18n/declaration";
import {
  dropdownMenuPanelPaddingClassName,
  dropdownMenuRowClassName,
} from "#/utils/dropdown-classes";
import { cn } from "#/utils/utils";
import { Dropdown } from "#/ui/dropdown/dropdown";
import type { DropdownOption } from "#/ui/dropdown/types";
import type { SuperAdminOrgRole, SuperAdminUserRow } from "./super-admin-types";

type MembershipAction = "suspend" | "resume" | "remove";

interface ChecklistItem {
  id: string;
  label: string;
  meta?: string;
  role?: SuperAdminOrgRole;
  suspended?: boolean;
}

/**
 * Checkbox list of organizations. Callers own the selected ids so one list
 * can drive suspend/remove and another can drive add.
 */
function optionLabel(label: string) {
  return label.charAt(0).toLocaleUpperCase() + label.slice(1);
}

function userInitials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) {
    return "?";
  }
  if (parts.length === 1) {
    return parts[0].slice(0, 1).toUpperCase();
  }
  return (
    parts[0].slice(0, 1) + parts[parts.length - 1].slice(0, 1)
  ).toUpperCase();
}

function roleOptions(t: (key: I18nKey) => string): DropdownOption[] {
  return [
    { value: "member", label: optionLabel(t(I18nKey.ORG$ROLE_MEMBER)) },
    { value: "admin", label: optionLabel(t(I18nKey.ORG$ROLE_ADMIN)) },
    { value: "owner", label: optionLabel(t(I18nKey.ORG$ROLE_OWNER)) },
  ];
}

function RoleSelect({
  value,
  suspended,
  testId,
  disabled,
  onChange,
  onAction,
}: {
  value: SuperAdminOrgRole;
  suspended?: boolean;
  testId: string;
  disabled?: boolean;
  onChange: (role: SuperAdminOrgRole) => void;
  onAction?: (action: MembershipAction) => void;
}) {
  const { t } = useTranslation();
  const options = roleOptions(t);
  const statusAction: MembershipAction = suspended ? "resume" : "suspend";
  return (
    <div className="relative w-[6.25rem] shrink-0">
      <Dropdown
        key={value}
        testId={testId}
        searchable={false}
        className="h-7 min-h-7 pr-1"
        inputClassName="text-xs"
        menuMinWidth={148}
        showSelectionCheck
        disabled={disabled}
        options={options}
        defaultValue={options.find((option) => option.value === value)}
        onChange={(item) => {
          if (!item || item.value === value) {
            return;
          }
          onChange(item.value as SuperAdminOrgRole);
        }}
        footer={
          <div className="flex flex-col">
            <button
              type="button"
              data-testid={`${testId}-status`}
              className={dropdownMenuRowClassName}
              onClick={() => onAction?.(statusAction)}
            >
              {optionLabel(
                t(
                  suspended
                    ? I18nKey.SUPER_ADMIN$ACTIVATE
                    : I18nKey.SUPER_ADMIN$SUSPEND,
                ),
              )}
            </button>
            <div
              role="separator"
              className="my-1 border-t border-[var(--oh-border)]"
            />
            <button
              type="button"
              data-testid={`${testId}-remove`}
              className={dropdownMenuRowClassName}
              onClick={() => onAction?.("remove")}
            >
              {optionLabel(t(I18nKey.SUPER_ADMIN$REMOVE))}
            </button>
          </div>
        }
      />
    </div>
  );
}

function AddRoleButton({
  orgId,
  disabled,
  open,
  onOpenChange,
  onSelect,
  testId,
  menuTestId,
}: {
  orgId: string;
  disabled?: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (role: SuperAdminOrgRole) => void;
  testId?: string;
  menuTestId?: string;
}) {
  const { t } = useTranslation();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>();

  useLayoutEffect(() => {
    if (!open) {
      return undefined;
    }
    const update = () => {
      const rect = buttonRef.current?.getBoundingClientRect();
      if (!rect) {
        return;
      }
      setMenuStyle({
        position: "fixed",
        zIndex: 9999,
        top: rect.bottom + 4,
        left: rect.right,
        transform: "translateX(-100%)",
        minWidth: Math.max(rect.width, 140),
      });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        buttonRef.current?.contains(target) ||
        menuRef.current?.contains(target)
      ) {
        return;
      }
      onOpenChange(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onOpenChange(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onOpenChange]);

  return (
    <>
      <BrandButton
        ref={buttonRef}
        type="button"
        variant="primary"
        testId={testId ?? `super-admin-group-add-${orgId}`}
        isDisabled={disabled}
        className="h-7 min-h-7 px-2.5 text-xs"
        onClick={() => onOpenChange(!open)}
      >
        {`+ ${t(I18nKey.BUTTON$ADD)}`}
      </BrandButton>
      {open
        ? createPortal(
            <div
              ref={menuRef}
              role="menu"
              data-testid={menuTestId ?? `super-admin-group-add-role-${orgId}`}
              style={menuStyle}
              className={cn(
                "flex flex-col bg-tertiary text-white rounded-[6px] context-menu-box-shadow",
                dropdownMenuPanelPaddingClassName,
              )}
            >
              {roleOptions(t).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="menuitem"
                  data-testid={`${menuTestId ?? `super-admin-group-add-role-${orgId}`}-${option.value}`}
                  className={dropdownMenuRowClassName}
                  onClick={() => onSelect(option.value as SuperAdminOrgRole)}
                >
                  {option.label}
                </button>
              ))}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

export function SuperAdminOrgChecklist({
  items,
  selectedIds,
  onToggle,
  onRoleChange,
  onMembershipAction,
  testIdPrefix,
  emptyMessage,
  disabled,
}: {
  items: ChecklistItem[];
  selectedIds: string[];
  onToggle: (id: string) => void;
  onRoleChange?: (id: string, role: SuperAdminOrgRole) => void;
  onMembershipAction?: (id: string, action: MembershipAction) => void;
  testIdPrefix: string;
  emptyMessage?: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  if (items.length === 0) {
    return emptyMessage ? (
      <p className="text-sm text-[var(--oh-muted)]">{emptyMessage}</p>
    ) : null;
  }

  return (
    <ul className="divide-y divide-[var(--oh-border)] overflow-visible rounded-lg border border-[var(--oh-border)]">
      {items.map((item) => {
        const checked = selectedIds.includes(item.id);
        return (
          <li
            key={item.id}
            className={cn(
              "flex items-center gap-3 px-3 py-2 text-sm",
              checked && "bg-[var(--oh-interactive-hover-low)]",
            )}
          >
            <input
              id={`${testIdPrefix}-${item.id}`}
              type="checkbox"
              data-testid={`${testIdPrefix}-${item.id}`}
              className="h-4 w-4 accent-white"
              checked={checked}
              disabled={disabled}
              onChange={() => onToggle(item.id)}
            />
            <label
              htmlFor={`${testIdPrefix}-${item.id}`}
              className="min-w-0 flex-1 truncate"
            >
              {item.label}
            </label>
            {item.role && onRoleChange ? (
              <div className="flex shrink-0 items-center gap-2">
                {item.suspended ? (
                  <span
                    data-testid={`${testIdPrefix}-suspended-${item.id}`}
                    className="rounded-full border border-[var(--oh-border)] px-2 py-0.5 text-xs text-[var(--oh-muted)]"
                  >
                    {t(I18nKey.SUPER_ADMIN$MEMBERSHIP_SUSPENDED)}
                  </span>
                ) : null}
                <RoleSelect
                  value={item.role}
                  suspended={item.suspended}
                  testId={`${testIdPrefix}-role-${item.id}`}
                  disabled={disabled}
                  onChange={(role) => onRoleChange(item.id, role)}
                  onAction={(action) => onMembershipAction?.(item.id, action)}
                />
              </div>
            ) : null}
            {!(item.role && onRoleChange) && item.meta ? (
              <span className="shrink-0 text-xs capitalize text-[var(--oh-muted)]">
                {item.meta}
              </span>
            ) : null}
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

function AccountStatus({ status }: { status: string }) {
  const { t } = useTranslation();
  const suspended = status === "inactive" || status === "suspended";
  return (
    <span
      className={cn(
        "shrink-0 rounded-full border px-2 py-0.5 text-xs",
        suspended
          ? "border-[var(--oh-border)] text-[var(--oh-muted)]"
          : "border-[var(--oh-foreground)] text-foreground",
      )}
    >
      {suspended
        ? t(I18nKey.SUPER_ADMIN$ACCOUNT_INACTIVE)
        : t(I18nKey.SETTINGS$API_KEY_STATUS_ACTIVE)}
    </span>
  );
}

export function SuperAdminUserGroupsModal({
  user,
  organizations,
  isSelf = false,
  onClose,
}: {
  user: SuperAdminUserRow;
  organizations: { id: string; name: string }[];
  /** The signed-in Super Admin is managing their own account. */
  isSelf?: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const updateGroups = useUpdateSuperAdminUserGroups();
  const updateStatus = useUpdateSuperAdminUserStatus();
  const removeUser = useRemoveSuperAdminUser();
  const [selectedCurrent, setSelectedCurrent] = useState<string[]>([]);
  const [bulkMenuKey, setBulkMenuKey] = useState(0);
  const [addingGroup, setAddingGroup] = useState(false);
  const [rolePickerOrgId, setRolePickerOrgId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [pendingRemoveIds, setPendingRemoveIds] = useState<string[] | null>(
    null,
  );
  const busy =
    updateGroups.isPending || updateStatus.isPending || removeUser.isPending;
  const accountSuspended =
    user.status === "inactive" || user.status === "suspended";

  const memberIds = new Set(
    user.memberships.map((membership) => membership.orgId),
  );
  // The user's personal workspace shares their id. It is not a group the
  // dashboard manages, so it gets no role, suspend, or remove controls.
  const currentItems: ChecklistItem[] = user.memberships
    .filter((membership) => membership.orgId !== user.id)
    .map((membership) => {
      const suspended =
        membership.status === "inactive" || membership.status === "suspended";
      return {
        id: membership.orgId,
        label: membership.orgName,
        role: membership.role,
        suspended,
      };
    });
  const addItems: ChecklistItem[] = organizations
    .filter((org) => !memberIds.has(org.id))
    .map((org) => ({ id: org.id, label: org.name }));

  const run = (
    action: "suspend" | "resume" | "remove" | "add" | "set_role",
    orgIds: string[],
    onDone: () => void,
    nextRole?: SuperAdminOrgRole,
  ) => {
    if (orgIds.length === 0 || updateGroups.isPending) {
      return;
    }
    const assignedRole = nextRole ?? "member";
    updateGroups.mutate(
      {
        userId: user.id,
        action,
        orgIds,
        ...(action === "add" || action === "set_role"
          ? { role: assignedRole }
          : {}),
      },
      { onSuccess: onDone },
    );
  };

  const setAccountStatus = (status: "active" | "inactive") => {
    if (busy) {
      return;
    }
    updateStatus.mutate({ userId: user.id, status });
  };

  const deleteUser = () => {
    if (busy) {
      return;
    }
    removeUser.mutate({ userId: user.id }, { onSuccess: onClose });
  };

  const confirmRemove = () => {
    if (!pendingRemoveIds) {
      return;
    }
    run("remove", pendingRemoveIds, () => {
      setPendingRemoveIds(null);
      setSelectedCurrent((current) =>
        current.filter((id) => !pendingRemoveIds.includes(id)),
      );
    });
  };

  return (
    <OrgModal
      testId="super-admin-groups-modal"
      className="max-h-[85vh] w-[36rem] max-w-[calc(100vw-2rem)] overflow-visible"
      title={t(I18nKey.SUPER_ADMIN$MANAGE_USER)}
      ariaLabel={user.name}
      primaryButtonText={t(I18nKey.BUTTON$CLOSE)}
      onPrimaryClick={onClose}
      onClose={onClose}
      hideTitle
      hideButtonGroup
      isLoading={busy}
    >
      <div className="flex w-full flex-col gap-5">
        <div className="relative -mx-6 border-b border-[var(--oh-border)] px-6 pb-4">
          <div className="flex min-h-9 items-center gap-3 pr-8">
            {addingGroup ? (
              <button
                type="button"
                data-testid="super-admin-groups-add-back"
                className="inline-flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 text-sm font-medium text-[var(--oh-muted)] hover:text-white"
                onClick={() => {
                  setRolePickerOrgId(null);
                  setAddingGroup(false);
                }}
              >
                <FaChevronLeft size={10} aria-hidden />
                {t(I18nKey.COMMON$BACK)}
              </button>
            ) : (
              <>
                <span
                  aria-hidden
                  className="flex size-9 shrink-0 items-center justify-center rounded-full border border-[var(--oh-border)] bg-[var(--oh-surface)] text-xs font-medium"
                >
                  {userInitials(user.name)}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-medium">
                      {user.name}
                    </span>
                    <AccountStatus status={user.status} />
                  </div>
                  {user.email ? (
                    <p className="truncate text-sm text-[var(--oh-muted)]">
                      {user.email}
                    </p>
                  ) : null}
                </div>
              </>
            )}
          </div>
          {addingGroup ? (
            <h4 className="pointer-events-none absolute inset-x-0 top-0 flex min-h-9 items-center justify-center text-sm font-medium">
              {t(I18nKey.SUPER_ADMIN$ADD_TO_GROUPS)}
            </h4>
          ) : null}
          <button
            type="button"
            data-testid="super-admin-groups-modal-close"
            aria-label={t(I18nKey.BUTTON$CLOSE)}
            disabled={busy}
            onClick={onClose}
            className="absolute top-0 right-6 flex cursor-pointer items-center justify-center rounded-sm border-0 bg-transparent p-1 text-tertiary-alt transition-colors hover:bg-surface-raised hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>

        <div className="overflow-hidden">
          <div
            className={cn(
              "flex w-[200%] transition-transform duration-200 ease-out motion-reduce:transition-none",
              addingGroup && "-translate-x-1/2",
            )}
          >
            <div
              className="flex w-1/2 flex-col gap-5"
              inert={addingGroup}
              aria-hidden={addingGroup}
            >
              <section className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-3">
                  <h4 className="text-sm font-medium">
                    {t(I18nKey.SUPER_ADMIN$GROUP_ACCESS)}
                  </h4>
                  <div className="flex items-center gap-2">
                    <div className="relative w-36 shrink-0">
                      <Dropdown
                        key={bulkMenuKey}
                        testId="super-admin-groups-bulk"
                        searchable={false}
                        disabled={selectedCurrent.length === 0 || busy}
                        placeholder={optionLabel(
                          t(I18nKey.SUPER_ADMIN$BULK_ACTIONS),
                        )}
                        // The label is a placeholder, so it stays muted unless
                        // checked organizations make the menu usable.
                        inputClassName={
                          selectedCurrent.length > 0
                            ? "placeholder:text-white"
                            : undefined
                        }
                        options={[
                          {
                            value: "suspend",
                            label: optionLabel(t(I18nKey.SUPER_ADMIN$SUSPEND)),
                          },
                          {
                            value: "resume",
                            label: optionLabel(t(I18nKey.SUPER_ADMIN$ACTIVATE)),
                          },
                          {
                            value: "remove",
                            label: optionLabel(t(I18nKey.SUPER_ADMIN$REMOVE)),
                            divider: true,
                          },
                        ]}
                        onChange={(item) => {
                          if (!item) {
                            return;
                          }
                          if (item.value === "remove") {
                            setPendingRemoveIds([...selectedCurrent]);
                          } else {
                            run(
                              item.value as MembershipAction,
                              selectedCurrent,
                              () => setSelectedCurrent([]),
                            );
                          }
                          setBulkMenuKey((key) => key + 1);
                        }}
                      />
                    </div>
                    <BrandButton
                      type="button"
                      variant="primary"
                      testId="super-admin-groups-add"
                      isDisabled={addItems.length === 0 || busy}
                      onClick={() => {
                        setRolePickerOrgId(null);
                        setAddingGroup(true);
                      }}
                    >
                      {t(I18nKey.SUPER_ADMIN$ADD_TO_ORG)}
                    </BrandButton>
                  </div>
                </div>
                {pendingRemoveIds ? (
                  <div className="flex flex-col gap-3 rounded-lg bg-[var(--oh-interactive-hover-low)] p-3">
                    <p className="text-sm">
                      {t(I18nKey.SUPER_ADMIN$REMOVE_MEMBERSHIPS_CONFIRM, {
                        name: user.name,
                        orgs: currentItems
                          .filter((item) => pendingRemoveIds.includes(item.id))
                          .map((item) => item.label)
                          .join(", "),
                      })}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <BrandButton
                        type="button"
                        variant="danger"
                        testId="super-admin-groups-remove-confirm"
                        isDisabled={busy}
                        onClick={confirmRemove}
                      >
                        {t(I18nKey.SUPER_ADMIN$REMOVE)}
                      </BrandButton>
                      <BrandButton
                        type="button"
                        variant="secondary"
                        testId="super-admin-groups-remove-cancel"
                        isDisabled={busy}
                        onClick={() => setPendingRemoveIds(null)}
                      >
                        {t(I18nKey.BUTTON$CANCEL)}
                      </BrandButton>
                    </div>
                  </div>
                ) : null}
                <SuperAdminOrgChecklist
                  items={currentItems}
                  selectedIds={selectedCurrent}
                  onToggle={(id) =>
                    setSelectedCurrent((current) => toggleId(current, id))
                  }
                  onRoleChange={(id, nextRole) =>
                    run("set_role", [id], () => undefined, nextRole)
                  }
                  onMembershipAction={(id, action) =>
                    action === "remove"
                      ? setPendingRemoveIds([id])
                      : run(action, [id], () => undefined)
                  }
                  testIdPrefix="super-admin-group-current"
                  emptyMessage={t(I18nKey.SUPER_ADMIN$NO_GROUPS)}
                  disabled={busy}
                />
              </section>
              <section className="flex flex-col gap-3 rounded-xl border border-[var(--oh-border)] p-3">
                <div>
                  <h4 className="text-sm font-medium">
                    {t(I18nKey.SUPER_ADMIN$ACCOUNT)}
                  </h4>
                  <p className="mt-1 text-sm leading-5 text-[var(--oh-muted)]">
                    {t(I18nKey.SUPER_ADMIN$ACCOUNT_HINT)}
                  </p>
                </div>
                {confirmDelete ? (
                  <div className="flex flex-col gap-3 rounded-lg bg-[var(--oh-interactive-hover-low)] p-3">
                    <p className="text-sm">
                      {t(I18nKey.SUPER_ADMIN$DELETE_USER_CONFIRM)}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <BrandButton
                        type="button"
                        variant="danger"
                        testId="super-admin-user-delete-confirm"
                        isDisabled={busy}
                        onClick={deleteUser}
                      >
                        {t(I18nKey.SUPER_ADMIN$DELETE_USER)}
                      </BrandButton>
                      <BrandButton
                        type="button"
                        variant="secondary"
                        testId="super-admin-user-delete-cancel"
                        isDisabled={busy}
                        onClick={() => setConfirmDelete(false)}
                      >
                        {t(I18nKey.BUTTON$CANCEL)}
                      </BrandButton>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {accountSuspended ? (
                      <BrandButton
                        type="button"
                        variant="secondary"
                        testId="super-admin-user-activate"
                        isDisabled={busy}
                        onClick={() => setAccountStatus("active")}
                      >
                        {t(I18nKey.SUPER_ADMIN$ACTIVATE_ACCOUNT)}
                      </BrandButton>
                    ) : (
                      <BrandButton
                        type="button"
                        variant="secondary"
                        testId="super-admin-user-suspend"
                        isDisabled={busy || isSelf}
                        onClick={() => setAccountStatus("inactive")}
                      >
                        {t(I18nKey.SUPER_ADMIN$SUSPEND_ACCOUNT)}
                      </BrandButton>
                    )}
                    <BrandButton
                      type="button"
                      variant="danger"
                      testId="super-admin-user-delete"
                      isDisabled={busy || isSelf}
                      onClick={() => setConfirmDelete(true)}
                    >
                      {t(I18nKey.SUPER_ADMIN$DELETE_USER)}
                    </BrandButton>
                  </div>
                )}
              </section>
            </div>
            <div
              className="flex w-1/2 flex-col gap-3"
              inert={!addingGroup}
              aria-hidden={!addingGroup}
            >
              {addItems.length === 0 ? (
                <p className="text-sm text-[var(--oh-muted)]">
                  {t(I18nKey.SUPER_ADMIN$NO_OTHER_GROUPS)}
                </p>
              ) : (
                <ul className="divide-y divide-[var(--oh-border)] overflow-visible rounded-lg border border-[var(--oh-border)]">
                  {addItems.map((item) => (
                    <li
                      key={item.id}
                      className="flex items-center gap-3 px-3 py-2 text-sm"
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {item.label}
                      </span>
                      <AddRoleButton
                        orgId={item.id}
                        disabled={busy}
                        open={rolePickerOrgId === item.id}
                        onOpenChange={(next) =>
                          setRolePickerOrgId(next ? item.id : null)
                        }
                        onSelect={(role) => {
                          setRolePickerOrgId(null);
                          run("add", [item.id], () => undefined, role);
                        }}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
        <div className="-mx-6 -mb-6 flex justify-end border-t border-[var(--oh-border)] px-6 py-4">
          <BrandButton
            type="button"
            variant="primary"
            testId={
              addingGroup
                ? "super-admin-groups-footer-done"
                : "super-admin-groups-footer-close"
            }
            isDisabled={busy}
            onClick={() => {
              if (addingGroup) {
                setRolePickerOrgId(null);
                setAddingGroup(false);
                return;
              }
              onClose();
            }}
          >
            {addingGroup
              ? t(I18nKey.PRODUCT_TOUR$DONE)
              : t(I18nKey.BUTTON$CLOSE)}
          </BrandButton>
        </div>
      </div>
    </OrgModal>
  );
}
