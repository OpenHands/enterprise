import { useState, useSyncExternalStore } from "react";
import { AxiosError } from "axios";
import {
  Bot,
  ChevronDown,
  Layers,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { InteractiveOpenHandsIcon } from "#/components/features/setup/interactive-openhands-icon";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { PixelField } from "#/components/features/super-admin/pixel-field";
import {
  StarterLlmFields,
  type StarterLlmSelection,
} from "#/components/features/super-admin/starter-llm-fields";
import { notifySuperAdminSetupStep } from "#/components/features/super-admin/super-admin-setup";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import {
  useActivateOrgLlmProfile,
  useSaveOrgLlmProfile,
} from "#/hooks/mutation/use-org-llm-profile-mutations";
import { useSaveSettings } from "#/hooks/mutation/use-save-settings";
import { I18nKey } from "#/i18n/declaration";
import { deriveProfileNameFromModel } from "#/utils/derive-profile-name";
import {
  clearSuperAdminNuxStarterModal,
  readSuperAdminNux,
  subscribeSuperAdminNux,
} from "#/utils/org/super-admin-nux";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { cn } from "#/utils/utils";
import "#/routes/super-admin-install-welcome.css";

const STARTER_STEPS: {
  id: string;
  label: I18nKey;
  detail: I18nKey;
  icon: LucideIcon;
}[] = [
  {
    id: "llm",
    label: I18nKey.SA_NUX$STARTER_LLM,
    detail: I18nKey.SA_NUX$STARTER_LLM_DETAIL,
    icon: Bot,
  },
  {
    id: "automation",
    label: I18nKey.SA_NUX$STARTER_AUTOMATION,
    detail: I18nKey.SA_NUX$STARTER_AUTOMATION_DETAIL,
    icon: Workflow,
  },
  {
    id: "others",
    label: I18nKey.SA_NUX$STARTER_OTHERS,
    detail: I18nKey.SA_NUX$STARTER_OTHERS_DETAIL,
    icon: Layers,
  },
];

/**
 * First visit to org LLM settings after install. Explains the basic setup
 * for the organization that was just created.
 */
export function SuperAdminGroupSetupModal() {
  const { t } = useTranslation();
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [pinnedId, setPinnedId] = useState<string | null>("llm");
  const nux = useSyncExternalStore(
    subscribeSuperAdminNux,
    readSuperAdminNux,
    readSuperAdminNux,
  );
  const [llm, setLlm] = useState<StarterLlmSelection>({
    provider: null,
    model: null,
    apiKey: "",
  });
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { organizationId } = useSelectedOrganizationId();
  const { mutateAsync: saveSettings } = useSaveSettings("org");
  const { mutateAsync: saveProfile } = useSaveOrgLlmProfile(organizationId);
  const { mutateAsync: activateProfile } =
    useActivateOrgLlmProfile(organizationId);

  if (!nux.starterModalPending) {
    return null;
  }

  // Same calls as the org LLM form (llm-settings.tsx): save the org defaults,
  // save a profile that copies them, then make that profile active.
  const saveLlm = async () => {
    const model = `${llm.provider}/${llm.model}`;
    const name = deriveProfileNameFromModel(model);
    if (!name) {
      return;
    }
    const apiKey = llm.apiKey.trim();
    setError(null);
    setIsSaving(true);
    try {
      await saveSettings({
        agent_settings_diff: {
          // A null base_url lets the server fill in the provider's default.
          llm: {
            model,
            base_url: null,
            ...(apiKey ? { api_key: apiKey } : {}),
          },
        },
      });
      await saveProfile({
        name,
        request: { include_secrets: true, preserve_existing_api_key: !apiKey },
      });
      await activateProfile(name);
    } catch (saveError) {
      setError(
        retrieveAxiosErrorMessage(saveError as AxiosError) ||
          t(I18nKey.ERROR$GENERIC),
      );
      setIsSaving(false);
      return;
    }
    notifySuperAdminSetupStep("add-llm");
    clearSuperAdminNuxStarterModal();
  };

  const title = t(I18nKey.SA_NUX$STARTER_TITLE);
  const welcomeLine = t(I18nKey.SA_NUX$STARTER_LETS_GO);
  const welcomeBrand = "OpenHands";
  const welcomeRest = welcomeLine.startsWith(welcomeBrand)
    ? welcomeLine.slice(welcomeBrand.length).trimStart()
    : null;

  return (
    <OrgModal
      testId="sa-nux-starter-modal"
      title={title}
      ariaLabel={title}
      hideTitle
      className="w-[30rem] overflow-hidden"
      primaryButtonText={t(I18nKey.SA_NUX$STARTER_CONFIRM)}
      primaryButtonTestId="sa-nux-starter-confirm"
      secondaryButtonText={t(I18nKey.SA_NUX$STARTER_SKIP)}
      secondaryButtonTestId="sa-nux-starter-skip"
      secondaryButtonClassName="bg-transparent hover:bg-transparent hover:border-white"
      isLoading={isSaving}
      isPrimaryDisabled={!llm.model || !organizationId}
      onPrimaryClick={saveLlm}
      onClose={clearSuperAdminNuxStarterModal}
    >
      <div
        className="relative -mx-6 -mt-6 h-56 overflow-hidden"
        data-testid="sa-nux-starter-pixel-field"
      >
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "linear-gradient(to bottom, #070707 0%, #101010 42%, var(--oh-color-base-secondary, #202020) 100%)",
          }}
        />
        <PixelField />
        <div className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center gap-2">
          <div className="relative size-20">
            <div className="absolute left-1/2 top-1/2 origin-center -translate-x-1/2 -translate-y-1/2 scale-50">
              <div className="oh-starter-icon-pulse-in">
                <InteractiveOpenHandsIcon
                  label={t(I18nKey.BRANDING$OPENHANDS_LOGO)}
                />
              </div>
            </div>
          </div>
          <p
            className="px-6 text-center text-2xl leading-tight tracking-tight"
            style={{
              color: "#fff",
              textShadow:
                "0 2px 6px rgba(0,0,0,0.95), 0 8px 18px rgba(0,0,0,0.8)",
            }}
          >
            {welcomeRest == null ? (
              <span className="oh-welcome-blur-in oh-welcome-blur-in--delay-1 inline-block font-medium">
                {welcomeLine}
              </span>
            ) : (
              <>
                <span className="oh-welcome-blur-in oh-welcome-blur-in--delay-1 inline-block font-bold">
                  {welcomeBrand}
                </span>{" "}
                <span className="oh-welcome-blur-in oh-welcome-blur-in--delay-2 inline-block font-medium">
                  {welcomeRest}
                </span>
              </>
            )}
          </p>
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <h3 className="text-xl font-semibold leading-7 tracking-tight text-white">
          {title}
        </h3>
        <p className="text-sm leading-5 text-modal-muted">
          {t(I18nKey.SA_NUX$STARTER_BODY)}
        </p>
      </div>
      <ol
        className="mt-4 overflow-hidden rounded-xl border border-[var(--oh-border)]"
        data-testid="sa-nux-starter-steps"
      >
        {STARTER_STEPS.map((step, index) => {
          const open = step.id === pinnedId || step.id === hoverId;
          const panelId = `sa-nux-starter-panel-${step.id}`;
          return (
            <li
              key={step.id}
              className="border-b border-[var(--oh-border)] last:border-b-0"
              style={
                open
                  ? {
                      background:
                        "linear-gradient(to bottom, #2e2e2e 0%, #202020 100%)",
                    }
                  : undefined
              }
              onMouseEnter={() => {
                setHoverId(step.id);
                setPinnedId((current) => current ?? step.id);
              }}
              onMouseLeave={() =>
                setHoverId((current) => (current === step.id ? null : current))
              }
              onFocus={() => {
                setHoverId(step.id);
                setPinnedId((current) => current ?? step.id);
              }}
              onBlur={(event) => {
                const next = event.relatedTarget;
                if (
                  next instanceof Node &&
                  event.currentTarget.contains(next)
                ) {
                  return;
                }
                setHoverId((current) => (current === step.id ? null : current));
              }}
            >
              <button
                type="button"
                className="flex w-full items-center gap-3 px-3.5 py-3 text-left text-sm text-white hover:bg-white/5"
                aria-expanded={open}
                aria-controls={panelId}
                data-testid={`sa-nux-starter-step-${step.id}`}
                onClick={() => {
                  setPinnedId(step.id);
                  setHoverId(null);
                }}
              >
                <span className="w-4 shrink-0 text-sm font-medium tabular-nums">
                  {index + 1}
                </span>
                <step.icon
                  className="size-4 shrink-0 text-[#FFFF8B]"
                  strokeWidth={1.75}
                  aria-hidden
                />
                <span className="min-w-0 flex-1">{t(step.label)}</span>
                <ChevronDown
                  className={cn(
                    "size-4 shrink-0 text-[#979797] transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none",
                    open && "rotate-180",
                  )}
                  aria-hidden
                />
              </button>
              <div
                id={panelId}
                aria-hidden={!open}
                className={cn(
                  "grid transition-[grid-template-rows,opacity] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none",
                  open
                    ? "grid-rows-[1fr] opacity-100"
                    : "grid-rows-[0fr] opacity-0",
                )}
              >
                <div className="overflow-hidden">
                  <div
                    className={
                      step.id === "llm"
                        ? "flex flex-col gap-3 px-3.5 pb-3"
                        : "pr-3.5 pb-3 pl-[4.4rem] text-xs leading-5"
                    }
                    style={
                      step.id === "llm"
                        ? undefined
                        : { color: "var(--cool-grey-400, #979797)" }
                    }
                    onPointerDown={
                      step.id === "llm" ? () => setPinnedId("llm") : undefined
                    }
                  >
                    <div
                      className="text-xs leading-5"
                      style={{ color: "var(--cool-grey-400, #979797)" }}
                    >
                      {t(step.detail)}
                    </div>
                    {step.id === "llm" ? (
                      <StarterLlmFields value={llm} onChange={setLlm} />
                    ) : null}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      {error ? (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      ) : null}
    </OrgModal>
  );
}
