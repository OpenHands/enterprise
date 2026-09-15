import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { HubBadge } from "#/components/features/integrations-hub/hub-badge";
import { HubModalCloseButton } from "#/components/features/integrations-hub/hub-modal";
import { PERSONAL_INTEGRATIONS_PATHS } from "#/components/features/integrations-hub/integrations-hub-paths";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import { I18nKey } from "#/i18n/declaration";
import { formControlFieldClassName } from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

const DEFAULT_HOURS = 8;
const MAX_HOURS = 100;

interface ApproveSelectedModalProps {
  selectedCount: number;
  onClose: () => void;
  onConfirm: (durationHours: number) => void;
}

export function ApproveSelectedModal({
  selectedCount,
  onClose,
  onConfirm,
}: ApproveSelectedModalProps) {
  const { t } = useTranslation();
  const [durationHours, setDurationHours] = useState(DEFAULT_HOURS);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onConfirm(durationHours);
  };

  return (
    <ModalBackdrop
      aria-label={t(I18nKey.INTEGRATIONS_HUB$APPROVE_MODAL_ARIA)}
      onClose={onClose}
    >
      <form
        data-testid="approve-selected-modal"
        onSubmit={handleSubmit}
        className={cn(
          "relative flex max-h-[85vh] w-[520px] max-w-[90vw] flex-col gap-4 overflow-y-auto",
          "rounded-xl border border-[var(--oh-border)] bg-base-secondary p-6",
        )}
      >
        <HubModalCloseButton
          onClose={onClose}
          testId="approve-selected-modal-close"
        />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pr-6">
          <h2 className="text-base font-semibold text-white">
            {t(I18nKey.INTEGRATIONS_HUB$DECIDE_SELECTED_TITLE)}
          </h2>
          <HubBadge>
            {t(I18nKey.INTEGRATIONS_HUB$SELECTED_COUNT, {
              count: selectedCount,
            })}
          </HubBadge>
        </div>
        <p className="text-sm leading-5 text-tertiary-light">
          {t(I18nKey.INTEGRATIONS_HUB$DECIDE_SELECTED_BODY, {
            defaultHours: DEFAULT_HOURS,
            maxHours: MAX_HOURS,
          })}{" "}
          {t(I18nKey.INTEGRATIONS_HUB$LONG_DURATION_PREFIX)}{" "}
          <Link
            to={PERSONAL_INTEGRATIONS_PATHS.integrations}
            className="text-white hover:opacity-80"
          >
            {t(I18nKey.INTEGRATIONS_HUB$LONG_DURATION_LINK)}
          </Link>
          {t(I18nKey.INTEGRATIONS_HUB$LONG_DURATION_SUFFIX)}
        </p>
        <label className="flex w-full min-w-0 flex-col gap-2.5">
          <span className="text-sm">
            {t(I18nKey.INTEGRATIONS_HUB$DURATION_LABEL)}
          </span>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={1}
              max={MAX_HOURS}
              step={1}
              value={durationHours}
              aria-label={t(I18nKey.INTEGRATIONS_HUB$DURATION_ARIA)}
              data-testid="approvals-duration-hours"
              onChange={(event) => {
                const next = Number(event.target.value);
                if (!Number.isNaN(next)) {
                  setDurationHours(Math.min(MAX_HOURS, Math.max(1, next)));
                }
              }}
              className={cn(formControlFieldClassName, "h-9 w-24 px-3 py-2")}
            />
            <span className="text-sm text-tertiary-light">
              {t(I18nKey.INTEGRATIONS_HUB$HOURS_LABEL)}
            </span>
          </div>
        </label>
        <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
          <BrandButton type="button" variant="secondary" onClick={onClose}>
            {t(I18nKey.BUTTON$CANCEL)}
          </BrandButton>
          <BrandButton type="submit" variant="primary" testId="confirm-button">
            {t(I18nKey.INTEGRATIONS_HUB$APPROVE)}
          </BrandButton>
        </div>
      </form>
    </ModalBackdrop>
  );
}
