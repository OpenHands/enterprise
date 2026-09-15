import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { HubModalCloseButton } from "#/components/features/integrations-hub/hub-modal";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import { I18nKey } from "#/i18n/declaration";
import type {
  HubPermissionProfile,
  HubPermissionProfileCreateInput,
  HubPermissionProfileCreateSource,
} from "#/types/integrations-hub";
import {
  formControlFieldClassName,
  formControlNativeSelectClassName,
} from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

interface SaveProfileModalProps {
  profiles?: HubPermissionProfile[];
  onClose: () => void;
  onSave: (input: HubPermissionProfileCreateInput) => void;
}

export function SaveProfileModal({
  profiles = [],
  onClose,
  onSave,
}: SaveProfileModalProps) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [source, setSource] =
    useState<HubPermissionProfileCreateSource>("duplicate");
  const [sourceProfileId, setSourceProfileId] = useState(
    () => profiles[0]?.id ?? "",
  );
  const canSubmit =
    name.trim().length > 0 &&
    (source !== "duplicate" || sourceProfileId.length > 0);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    onSave({
      name: name.trim(),
      source,
      sourceProfileId: source === "duplicate" ? sourceProfileId : undefined,
    });
  };

  const sources: Array<{
    value: HubPermissionProfileCreateSource;
    title: I18nKey;
    description: I18nKey;
  }> = [
    {
      value: "duplicate",
      title: I18nKey.INTEGRATIONS_HUB$PROFILE_SOURCE_DUPLICATE,
      description: I18nKey.INTEGRATIONS_HUB$PROFILE_SOURCE_DUPLICATE_DESC,
    },
    {
      value: "scratch",
      title: I18nKey.INTEGRATIONS_HUB$PROFILE_SOURCE_SCRATCH,
      description: I18nKey.INTEGRATIONS_HUB$PROFILE_SOURCE_SCRATCH_DESC,
    },
  ];

  return (
    <ModalBackdrop
      aria-label={t(I18nKey.INTEGRATIONS_HUB$NEW_PROFILE)}
      onClose={onClose}
    >
      <form
        data-testid="save-permissions-modal"
        onSubmit={handleSubmit}
        className={cn(
          "relative flex max-h-[85vh] w-[520px] max-w-[90vw] flex-col overflow-hidden",
          "rounded-xl border border-[var(--oh-border)] bg-base-secondary",
        )}
      >
        <HubModalCloseButton
          onClose={onClose}
          testId="save-permissions-modal-close"
        />
        <div className="custom-scrollbar-always flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-6 pb-4">
          <h2 className="pr-6 text-base font-semibold text-white">
            {t(I18nKey.INTEGRATIONS_HUB$NEW_PROFILE)}
          </h2>
          <p className="text-sm leading-5 text-tertiary-light">
            {t(I18nKey.INTEGRATIONS_HUB$NEW_PROFILE_BODY)}
          </p>
          <label className="flex w-full min-w-0 flex-col gap-2.5">
            <span className="text-sm">
              {t(I18nKey.INTEGRATIONS_HUB$PROFILE_NAME)}
            </span>
            <input
              data-testid="save-profile-name"
              className={cn(formControlFieldClassName, "w-full min-w-0")}
              placeholder={t(I18nKey.INTEGRATIONS_HUB$PROFILE_NAME_PLACEHOLDER)}
              maxLength={80}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <fieldset className="flex w-full min-w-0 flex-col gap-2">
            <legend className="text-sm">
              {t(I18nKey.INTEGRATIONS_HUB$PROFILE_SOURCE)}
            </legend>
            <div
              role="radiogroup"
              aria-label={t(I18nKey.INTEGRATIONS_HUB$PROFILE_SOURCE)}
              className="flex flex-col gap-2"
            >
              {sources.map((option) => {
                const selected = source === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    data-testid={`save-permissions-source-${option.value}`}
                    onClick={() => setSource(option.value)}
                    className={cn(
                      "flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-lg border px-3 py-2.5 text-left",
                      selected
                        ? "border-white/40 bg-white/[0.06]"
                        : "border-[var(--oh-border)] hover:bg-[var(--oh-surface-raised)]",
                    )}
                  >
                    <span className="text-sm font-medium text-white">
                      {t(option.title)}
                    </span>
                    <span className="text-xs leading-5 text-[var(--oh-text-dim)]">
                      {t(option.description)}
                    </span>
                  </button>
                );
              })}
            </div>
          </fieldset>
          {source === "duplicate" ? (
            <label className="flex w-full min-w-0 flex-col gap-2.5">
              <span className="text-sm">
                {t(I18nKey.INTEGRATIONS_HUB$PROFILE_SOURCE_PICK)}
              </span>
              <select
                data-testid="save-permissions-source-profile"
                className={formControlNativeSelectClassName}
                value={sourceProfileId}
                onChange={(event) => setSourceProfileId(event.target.value)}
              >
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <p className="text-xs leading-5 text-[var(--oh-text-dim)]">
            {t(I18nKey.INTEGRATIONS_HUB$PROFILE_FOOTNOTE)}
          </p>
        </div>
        <div className="flex justify-end gap-2 border-t border-[var(--oh-border)] px-6 py-4">
          <BrandButton type="button" variant="secondary" onClick={onClose}>
            {t(I18nKey.BUTTON$CANCEL)}
          </BrandButton>
          <BrandButton type="submit" variant="primary" isDisabled={!canSubmit}>
            {source === "scratch"
              ? t(I18nKey.INTEGRATIONS_HUB$PROFILE_CONTINUE)
              : t(I18nKey.INTEGRATIONS_HUB$PROFILE_CREATE)}
          </BrandButton>
        </div>
      </form>
    </ModalBackdrop>
  );
}
