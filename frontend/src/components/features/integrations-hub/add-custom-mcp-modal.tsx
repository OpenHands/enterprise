import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import {
  HubModal,
  hubModalBodyClassName,
  hubModalFooterClassName,
} from "#/components/features/integrations-hub/hub-modal";
import { I18nKey } from "#/i18n/declaration";
import type { HubCustomMcpInput } from "#/types/integrations-hub";
import {
  formControlFieldClassName,
  formControlNativeSelectClassName,
} from "#/utils/form-control-classes";

function slugFromDisplayName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

interface AddCustomMcpModalProps {
  existingSlugs: string[];
  onClose: () => void;
  onRegister: (input: HubCustomMcpInput) => void;
}

export function AddCustomMcpModal({
  existingSlugs,
  onClose,
  onRegister,
}: AddCustomMcpModalProps) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [serverUrl, setServerUrl] = useState("");
  const [authStrategy, setAuthStrategy] =
    useState<HubCustomMcpInput["authStrategy"]>("bearer");
  const [error, setError] = useState("");

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedSlug = slug.trim();
    const trimmedUrl = serverUrl.trim();
    if (!trimmedName) {
      setError(t(I18nKey.INTEGRATIONS_HUB$REQUEST_NAME_REQUIRED));
      return;
    }
    if (!trimmedSlug) {
      setError(t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_SLUG_REQUIRED));
      return;
    }
    if (existingSlugs.includes(trimmedSlug)) {
      setError(t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_SLUG_TAKEN));
      return;
    }
    if (!trimmedUrl) {
      setError(t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_URL_REQUIRED));
      return;
    }
    onRegister({
      name: trimmedName,
      slug: trimmedSlug,
      serverUrl: trimmedUrl,
      authStrategy,
    });
  };

  return (
    <HubModal
      ariaLabel={t(I18nKey.INTEGRATIONS_HUB$ADD_CUSTOM_MCP)}
      testId="add-custom-mcp-modal"
      onClose={onClose}
    >
      <form className="flex min-h-0 flex-1 flex-col" onSubmit={handleSubmit}>
        <div className={hubModalBodyClassName}>
          <h2 className="pr-8 text-base font-semibold text-white">
            {t(I18nKey.INTEGRATIONS_HUB$ADD_CUSTOM_MCP)}
          </h2>
          <p className="mt-1 text-xs leading-5 text-tertiary-light">
            {t(I18nKey.INTEGRATIONS_HUB$ADD_CUSTOM_MCP_BODY)}
          </p>
          <div className="mt-6 grid gap-3">
            <label className="grid gap-1.5 text-sm text-white">
              <span className="font-medium">
                {t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_NAME)}
              </span>
              <input
                data-testid="custom-mcp-name"
                className={formControlFieldClassName}
                placeholder={t(
                  I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_NAME_PLACEHOLDER,
                )}
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  if (!slugTouched) {
                    setSlug(slugFromDisplayName(event.target.value));
                  }
                }}
              />
            </label>
            <label className="grid gap-1.5 text-sm text-white">
              <span className="font-medium">
                {t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_SLUG)}
              </span>
              <input
                data-testid="custom-mcp-slug"
                className={formControlFieldClassName}
                placeholder={t(
                  I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_SLUG_PLACEHOLDER,
                )}
                value={slug}
                onChange={(event) => {
                  setSlugTouched(true);
                  setSlug(event.target.value);
                }}
              />
              <span className="text-xs leading-5 text-tertiary-light">
                {t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_SLUG_HELP)}
              </span>
            </label>
            <label className="grid gap-1.5 text-sm text-white">
              <span className="font-medium">
                {t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_URL)}
              </span>
              <input
                data-testid="custom-mcp-url"
                className={formControlFieldClassName}
                placeholder={t(
                  I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_URL_PLACEHOLDER,
                )}
                value={serverUrl}
                onChange={(event) => setServerUrl(event.target.value)}
              />
            </label>
            <label className="grid gap-1.5 text-sm text-white">
              <span className="font-medium">
                {t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_AUTH)}
              </span>
              <select
                className={formControlNativeSelectClassName}
                value={authStrategy}
                onChange={(event) =>
                  setAuthStrategy(
                    event.target.value as HubCustomMcpInput["authStrategy"],
                  )
                }
              >
                <option value="bearer">
                  {t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_AUTH_BEARER)}
                </option>
                <option value="api_key">
                  {t(I18nKey.INTEGRATIONS_HUB$AUTH_API_KEY)}
                </option>
                <option value="none">
                  {t(I18nKey.INTEGRATIONS_HUB$CUSTOM_MCP_AUTH_NONE)}
                </option>
              </select>
            </label>
          </div>
          {error ? (
            <p className="mt-4 text-sm text-[var(--oh-danger)]" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className={hubModalFooterClassName}>
          <BrandButton type="button" variant="secondary" onClick={onClose}>
            {t(I18nKey.BUTTON$CANCEL)}
          </BrandButton>
          <BrandButton type="submit" variant="primary">
            {t(I18nKey.INTEGRATIONS_HUB$REGISTER)}
          </BrandButton>
        </div>
      </form>
    </HubModal>
  );
}
