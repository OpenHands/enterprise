import { ComboBox, Header, Input, ListBox, Spinner } from "@heroui/react";
import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { mapProvider } from "#/utils/map-provider";
import { extractModelAndProvider } from "#/utils/extract-model-and-provider";
import { cn } from "#/utils/utils";
import { HelpLink } from "#/ui/help-link";
import { PRODUCT_URL } from "#/utils/constants";
import { useSearchProviders } from "#/hooks/query/use-search-providers";
import { useProviderModels } from "#/hooks/query/use-provider-models";
import { useAppMode } from "#/hooks/use-app-mode";

interface ModelSelectorProps {
  isDisabled?: boolean;
  currentModel?: string;
  onChange?: (provider: string | null, model: string | null) => void;
  onDefaultValuesChanged?: (
    provider: string | null,
    model: string | null,
  ) => void;
  wrapperClassName?: string;
  labelClassName?: string;
}

export function ModelSelector({
  isDisabled,
  currentModel,
  onChange,
  onDefaultValuesChanged,
  wrapperClassName,
  labelClassName,
}: ModelSelectorProps) {
  const [, setLitellmId] = React.useState<string | null>(null);
  const [selectedProvider, setSelectedProvider] = React.useState<string | null>(
    null,
  );
  const [selectedModel, setSelectedModel] = React.useState<string | null>(null);

  const { data: providers = [] } = useSearchProviders();
  const {
    data: providerModels = [],
    isLoading: isLoadingModels,
    error: modelsError,
  } = useProviderModels(selectedProvider);
  // The OpenHands-account CTA points at the cloud product; only show it there.
  const { isEnterpriseCloud } = useAppMode();

  const verifiedProviders = React.useMemo(
    () => providers.filter((p) => p.verified),
    [providers],
  );
  const unverifiedProviders = React.useMemo(
    () => providers.filter((p) => !p.verified),
    [providers],
  );

  // Hidden models (e.g. legacy alias routes a managed proxy still serves
  // after a rename) are never offered as dropdown options, but they do
  // count as available for the saved-model check below.
  const dropdownModels = React.useMemo(
    () => providerModels.filter((m) => !m.hidden),
    [providerModels],
  );
  const verifiedModels = React.useMemo(
    () => dropdownModels.filter((m) => m.verified),
    [dropdownModels],
  );
  const unverifiedModels = React.useMemo(
    () => dropdownModels.filter((m) => !m.verified),
    [dropdownModels],
  );

  // Truthful-but-gentle signal that the displayed model no longer exists in
  // the provider's model list (e.g. an admin removed it from a managed
  // proxy). Only shown when the list actually loaded non-empty — a fetch
  // error or an unknown provider must not cast doubt on a working config.
  // Hidden models count as available: the proxy still serves them.
  const isSelectedModelUnavailable = React.useMemo(
    () =>
      !!selectedModel &&
      !isLoadingModels &&
      !modelsError &&
      providerModels.length > 0 &&
      !providerModels.some((m) => m.name === selectedModel),
    [selectedModel, isLoadingModels, modelsError, providerModels],
  );

  React.useEffect(() => {
    if (currentModel) {
      const { provider, model } = extractModelAndProvider(currentModel);

      setLitellmId(currentModel);
      setSelectedProvider(provider || null);
      setSelectedModel(model);
      onDefaultValuesChanged?.(provider || null, model);
    }
  }, [currentModel]);

  // With a single provider (e.g. managed OHE's bundled proxy) there is nothing
  // to pick — auto-select it so the picker below can be hidden.
  React.useEffect(() => {
    if (providers.length === 1 && !selectedProvider && !currentModel) {
      const provider = providers[0].name;
      setSelectedProvider(provider);
      setLitellmId(`${provider}/`);
      onChange?.(provider, null);
    }
  }, [providers, selectedProvider, currentModel]);

  const handleChangeProvider = (provider: string) => {
    setSelectedProvider(provider);
    setSelectedModel(null);
    setLitellmId(`${provider}/`);
    onChange?.(provider, null);
  };

  const handleChangeModel = (model: string) => {
    let fullModel = `${selectedProvider}/${model}`;
    if (selectedProvider === "openai") {
      // LiteLLM lists OpenAI models without the openai/ prefix
      fullModel = model;
    }
    setLitellmId(fullModel);
    setSelectedModel(model);
    onChange?.(selectedProvider, model);
  };

  const clear = () => {
    setSelectedProvider(null);
    setLitellmId(null);
  };

  const { t } = useTranslation();

  return (
    <div
      className={cn(
        "flex flex-col md:flex-row w-full max-w-[680px] justify-between gap-4 md:gap-[46px]",
        wrapperClassName,
      )}
    >
      {providers.length !== 1 ? (
        <fieldset className="flex flex-col gap-2.5 w-full">
          <label className={cn("text-sm", labelClassName)}>
            {t(I18nKey.LLM$PROVIDER)}
          </label>
          <ComboBox
            isRequired
            isDisabled={isDisabled}
            aria-label={t(I18nKey.LLM$PROVIDER)}
            onSelectionChange={(e) => {
              if (e?.toString()) handleChangeProvider(e.toString());
            }}
            onInputChange={(value) => !value && clear()}
            defaultSelectedKey={selectedProvider ?? undefined}
            selectedKey={selectedProvider}
          >
            <ComboBox.InputGroup className="bg-tertiary border border-[#717888] h-10 w-full rounded-sm p-2">
              <Input
                data-testid="llm-provider-input"
                name="llm-provider-input"
                placeholder={t(I18nKey.LLM$SELECT_PROVIDER_PLACEHOLDER)}
                className="placeholder:italic"
                onMouseDown={(e) => {
                  if (document.activeElement === e.currentTarget) {
                    e.currentTarget.blur();
                  }
                }}
              />
            </ComboBox.InputGroup>
            <ComboBox.Popover className="bg-tertiary rounded-xl border border-[#717888]">
              <ListBox>
                <ListBox.Section>
                  {unverifiedProviders.length > 0 ? (
                    <Header>{t(I18nKey.MODEL_SELECTOR$VERIFIED)}</Header>
                  ) : null}
                  {verifiedProviders.map((provider) => (
                    <ListBox.Item
                      data-testid={`provider-item-${provider.name}`}
                      id={provider.name}
                      key={provider.name}
                      textValue={mapProvider(provider.name)}
                    >
                      {mapProvider(provider.name)}
                    </ListBox.Item>
                  ))}
                </ListBox.Section>
                {unverifiedProviders.length > 0 ? (
                  <ListBox.Section>
                    {verifiedProviders.length > 0 ? (
                      <Header>{t(I18nKey.MODEL_SELECTOR$OTHERS)}</Header>
                    ) : null}
                    {unverifiedProviders.map((provider) => (
                      <ListBox.Item
                        id={provider.name}
                        key={provider.name}
                        textValue={mapProvider(provider.name)}
                      >
                        {mapProvider(provider.name)}
                      </ListBox.Item>
                    ))}
                  </ListBox.Section>
                ) : null}
              </ListBox>
            </ComboBox.Popover>
          </ComboBox>
        </fieldset>
      ) : null}

      {selectedProvider === "openhands" && isEnterpriseCloud && (
        <HelpLink
          testId="openhands-account-help"
          text={t(I18nKey.SETTINGS$NEED_OPENHANDS_ACCOUNT)}
          linkText={t(I18nKey.SETTINGS$CLICK_HERE)}
          href={PRODUCT_URL.PRODUCTION}
          size="settings"
          linkColor="white"
        />
      )}

      <fieldset className="flex flex-col gap-2.5 w-full">
        <label className={cn("text-sm", labelClassName)}>
          {t(I18nKey.LLM$MODEL)}
        </label>
        <ComboBox
          isRequired
          aria-label={t(I18nKey.LLM$MODEL)}
          onSelectionChange={(e) => {
            if (e?.toString()) handleChangeModel(e.toString());
          }}
          isDisabled={isDisabled || !selectedProvider}
          selectedKey={selectedModel}
          defaultSelectedKey={selectedModel ?? undefined}
        >
          <ComboBox.InputGroup className="bg-tertiary border border-[#717888] h-10 w-full rounded-sm p-2">
            <Input
              data-testid="llm-model-input"
              name="llm-model-input"
              placeholder={t(I18nKey.LLM$SELECT_MODEL_PLACEHOLDER)}
              className="placeholder:italic"
              onMouseDown={(e) => {
                if (document.activeElement === e.currentTarget) {
                  e.currentTarget.blur();
                }
              }}
            />
            {isLoadingModels ? <Spinner size="sm" /> : null}
          </ComboBox.InputGroup>
          <ComboBox.Popover className="bg-tertiary rounded-xl border border-[#717888]">
            <ListBox>
              <ListBox.Section>
                {unverifiedModels.length > 0 ? (
                  <Header>{t(I18nKey.MODEL_SELECTOR$VERIFIED)}</Header>
                ) : null}
                {verifiedModels.map((model) => (
                  <ListBox.Item
                    id={model.name}
                    key={model.name}
                    textValue={model.name}
                  >
                    {model.name}
                  </ListBox.Item>
                ))}
              </ListBox.Section>
              {unverifiedModels.length > 0 ? (
                <ListBox.Section>
                  {verifiedModels.length > 0 ? (
                    <Header>{t(I18nKey.MODEL_SELECTOR$OTHERS)}</Header>
                  ) : null}
                  {unverifiedModels.map((model) => (
                    <ListBox.Item
                      data-testid={`model-item-${model.name}`}
                      id={model.name}
                      key={model.name}
                      textValue={model.name}
                    >
                      {model.name}
                    </ListBox.Item>
                  ))}
                </ListBox.Section>
              ) : null}
            </ListBox>
          </ComboBox.Popover>
        </ComboBox>
        {modelsError && (
          <p data-testid="models-error" className="text-danger text-xs">
            {t(I18nKey.CONFIGURATION$ERROR_FETCH_MODELS)}
          </p>
        )}
        {isSelectedModelUnavailable && (
          <p
            data-testid="model-unavailable-warning"
            className="text-yellow-400 text-xs"
          >
            {t(I18nKey.SETTINGS$MODEL_NO_LONGER_AVAILABLE)}
          </p>
        )}
      </fieldset>
    </div>
  );
}
