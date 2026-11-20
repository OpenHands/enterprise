import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { ProfileNameInput } from "#/components/features/settings/profile-name-input";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { SettingsTextarea } from "#/components/features/settings/settings-textarea";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import type { MetaProfile } from "#/api/organization-service/org-model-routers-service.api";
import { useSaveOrgModelRouter } from "#/hooks/mutation/use-org-model-router-mutations";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { extractErrorMessage } from "#/utils/extract-error-message";
import { PROFILE_NAME_PATTERN } from "#/utils/derive-profile-name";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";

// Placeholders the SDK renders into a direct-routing prompt. Passed to i18n as
// values so the braces survive interpolation.
const INSTANCE_TEXT_PLACEHOLDER = /{{\s*instance_text\s*}}/;
const INSTANCE_TEXT_PLACEHOLDER_TEXT = "{{ instance_text }}";
const MODEL_TABLE_PLACEHOLDER_TEXT = "{{ model_table }}";
const PROMPT_TEMPLATE_PLACEHOLDER = `Return JSON with the best model for this task.
${MODEL_TABLE_PLACEHOLDER_TEXT}

Task:
${INSTANCE_TEXT_PLACEHOLDER_TEXT}`;

interface ModelRouterEditorProps {
  orgId: string;
  mode: "create" | "edit";
  /** Router being edited; omitted on create. */
  initialName?: string;
  initialConfig?: MetaProfile;
  /** Names already taken; a create with one of them would overwrite it. */
  existingNames: string[];
  /** Org LLM profile names offered for the classifier. */
  availableProfiles: string[];
  onSaved: () => void;
  onCancel: () => void;
}

/**
 * The Custom Model Router form: name, classifier LLM profile, direct-routing
 * prompt template and optional model table. No templates: OHE orgs describe
 * their own LLM profiles, so the router is always built from them.
 */
export function ModelRouterEditor({
  orgId,
  mode,
  initialName,
  initialConfig,
  existingNames,
  availableProfiles,
  onSaved,
  onCancel,
}: ModelRouterEditorProps) {
  const { t } = useTranslation();
  const isEdit = mode === "edit";
  const saveRouter = useSaveOrgModelRouter(orgId);

  const [name, setName] = useState(initialName ?? "");
  const [classifier, setClassifier] = useState(
    initialConfig?.classifier_model ?? "",
  );
  const [promptTemplate, setPromptTemplate] = useState(
    initialConfig?.prompt_template ?? "",
  );
  const [modelTable, setModelTable] = useState(
    initialConfig?.model_table ?? "",
  );
  const [promptTouched, setPromptTouched] = useState(false);

  // A stored classifier whose LLM profile has since been deleted must still
  // appear selected, so the admin sees what to fix instead of an empty field.
  const classifierItems = useMemo(() => {
    const items = availableProfiles.map((p) => ({ key: p, label: p }));
    const trimmed = classifier.trim();
    if (trimmed && !availableProfiles.includes(trimmed)) {
      items.push({ key: trimmed, label: trimmed });
    }
    return items;
  }, [availableProfiles, classifier]);

  const trimmedName = name.trim();
  const nameValid = PROFILE_NAME_PATTERN.test(trimmedName);
  const isDuplicateName = !isEdit && existingNames.includes(trimmedName);
  const promptValid = INSTANCE_TEXT_PLACEHOLDER.test(promptTemplate);
  const canSave =
    nameValid &&
    !isDuplicateName &&
    classifier.trim().length > 0 &&
    promptValid &&
    !saveRouter.isPending;

  const handleSave = async () => {
    if (!canSave) return;
    const config: MetaProfile = {
      classifier_model: classifier.trim(),
      classes: [],
      prompt_template: promptTemplate.trim(),
      model_table: modelTable.trim() || null,
    };
    try {
      await saveRouter.mutateAsync({ name: trimmedName, config });
      displaySuccessToast(
        t(I18nKey.SETTINGS$MODEL_ROUTER_SAVED, { name: trimmedName }),
      );
      onSaved();
    } catch (error) {
      displayErrorToast(extractErrorMessage(error, t(I18nKey.ERROR$GENERIC)));
    }
  };

  return (
    <div className="flex flex-col gap-6" data-testid="model-router-editor">
      <Typography.H3>
        {t(
          isEdit
            ? I18nKey.SETTINGS$MODEL_ROUTER_EDIT_TITLE
            : I18nKey.SETTINGS$MODEL_ROUTER_ADD,
        )}
      </Typography.H3>

      <Typography.Paragraph className="text-sm text-[var(--oh-muted)]">
        {t(I18nKey.SETTINGS$MODEL_ROUTER_LLM_PROFILES_HINT)}
      </Typography.Paragraph>

      <div className="flex flex-col gap-1">
        <ProfileNameInput
          testId="model-router-name"
          value={name}
          onChange={setName}
          isDisabled={isEdit}
        />
        {isDuplicateName ? (
          <Typography.Text
            className="text-xs text-red-400"
            testId="model-router-name-duplicate"
          >
            {t(I18nKey.SETTINGS$MODEL_ROUTER_NAME_IN_USE)}
          </Typography.Text>
        ) : null}
      </div>

      <div className="flex flex-col gap-2">
        <SettingsDropdownInput
          testId="model-router-classifier"
          name="classifier_model"
          label={t(I18nKey.SETTINGS$MODEL_ROUTER_CLASSIFIER_LABEL)}
          placeholder={t(I18nKey.SETTINGS$MODEL_ROUTER_CLASSIFIER_PLACEHOLDER)}
          items={classifierItems}
          selectedKey={classifier || null}
          onSelectionChange={(key) => setClassifier(key ? String(key) : "")}
          required
        />
        <Typography.Text className="text-xs text-[var(--oh-muted)]">
          {t(I18nKey.SETTINGS$MODEL_ROUTER_CLASSIFIER_HINT)}
        </Typography.Text>
      </div>

      <SettingsTextarea
        testId="model-router-prompt-template"
        name="prompt_template"
        label={t(I18nKey.SETTINGS$MODEL_ROUTER_PROMPT_LABEL)}
        hint={t(I18nKey.SETTINGS$MODEL_ROUTER_PROMPT_HINT, {
          instanceText: INSTANCE_TEXT_PLACEHOLDER_TEXT,
          modelTable: MODEL_TABLE_PLACEHOLDER_TEXT,
        })}
        placeholder={PROMPT_TEMPLATE_PLACEHOLDER}
        value={promptTemplate}
        onChange={setPromptTemplate}
        onBlur={() => setPromptTouched(true)}
        rows={12}
        required
        showRequiredTag
        error={
          promptTouched && !promptValid
            ? t(I18nKey.SETTINGS$MODEL_ROUTER_PROMPT_INVALID, {
                placeholder: INSTANCE_TEXT_PLACEHOLDER_TEXT,
              })
            : undefined
        }
      />

      <SettingsTextarea
        testId="model-router-model-table"
        name="model_table"
        label={t(I18nKey.SETTINGS$MODEL_ROUTER_MODEL_TABLE_LABEL)}
        hint={t(I18nKey.SETTINGS$MODEL_ROUTER_MODEL_TABLE_HINT)}
        placeholder={"- gpt-5.5\n- claude-sonnet-4-5"}
        value={modelTable}
        onChange={setModelTable}
        rows={6}
        showOptionalTag
      />

      <div className="flex gap-2">
        <BrandButton
          testId="model-router-save"
          type="button"
          variant="primary"
          onClick={handleSave}
          isDisabled={!canSave}
          aria-busy={saveRouter.isPending}
        >
          {saveRouter.isPending ? (
            <>
              <LoadingSpinner size="small" />
              <span className="sr-only">{t(I18nKey.BUTTON$SAVE)}</span>
            </>
          ) : (
            t(I18nKey.BUTTON$SAVE)
          )}
        </BrandButton>
        <BrandButton
          testId="model-router-cancel"
          type="button"
          variant="secondary"
          onClick={onCancel}
          isDisabled={saveRouter.isPending}
        >
          {t(I18nKey.BUTTON$CANCEL)}
        </BrandButton>
      </div>
    </div>
  );
}
