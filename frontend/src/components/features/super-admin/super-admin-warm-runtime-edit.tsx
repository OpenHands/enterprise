import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";
import { FaChevronLeft } from "react-icons/fa6";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import {
  useCreateWarmRuntime,
  useUpdateWarmRuntime,
} from "#/hooks/mutation/use-warm-runtime-mutations";
import { useWarmRuntimes } from "#/hooks/query/use-warm-runtimes";
import type { WarmRuntime } from "#/api/warm-runtimes-service/warm-runtimes.types";
import { cn } from "#/utils/utils";
import { I18nKey } from "#/i18n/declaration";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import {
  BrandButton,
  SettingsInput,
  SettingsTextarea,
  SuperAdminPageHeader,
} from "./super-admin-chrome";
import {
  EMPTY_WARM_RUNTIME_FORM,
  type WarmRuntimeFormErrors,
  type WarmRuntimeFormState,
  buildWarmRuntimePayload,
  toFormState,
  validateWarmRuntimeForm,
} from "./warm-runtime-form";

function SectionTitle({ children }: { children: string }) {
  return <h3 className="text-sm font-semibold text-white">{children}</h3>;
}

function WarmRuntimeForm({ runtime }: { runtime: WarmRuntime | null }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const createRuntime = useCreateWarmRuntime();
  const updateRuntime = useUpdateWarmRuntime();
  const isNew = runtime === null;
  const initialForm = runtime ? toFormState(runtime) : EMPTY_WARM_RUNTIME_FORM;
  const [form, setForm] = useState<WarmRuntimeFormState>(initialForm);

  const errors = validateWarmRuntimeForm(form, { isNew });
  const isDirty = JSON.stringify(form) !== JSON.stringify(initialForm);
  const isPending = createRuntime.isPending || updateRuntime.isPending;
  const canSubmit = Object.keys(errors).length === 0 && isDirty;

  const setField =
    <K extends keyof WarmRuntimeFormState>(key: K) =>
    (value: WarmRuntimeFormState[K]) =>
      setForm((prev) => ({ ...prev, [key]: value }));

  // A missing value only disables Save; a malformed one is explained.
  const fieldError = (
    key: Exclude<keyof WarmRuntimeFormErrors, "environmentLine">,
    messages: { invalid?: string; integer?: string },
  ) => {
    const kind = errors[key];
    return kind && kind !== "required" ? messages[kind] : undefined;
  };
  const integerMessage = t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_INTEGER_INVALID);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    const payload = buildWarmRuntimePayload(form);
    if (isNew) {
      createRuntime.mutate(
        { name: form.name.trim(), ...payload },
        {
          onSuccess: () => navigate(SUPER_ADMIN_PATHS.warmRuntimes),
        },
      );
    } else {
      updateRuntime.mutate({ name: runtime.name, ...payload });
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      data-testid="super-admin-warm-runtime-form"
      className="flex max-w-2xl flex-col gap-6"
    >
      {runtime?.source === "file" ? (
        <p
          data-testid="super-admin-warm-runtime-configmap-hint"
          className="text-xs text-tertiary-alt"
        >
          {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_EDIT_CONFIGMAP_HINT)}
        </p>
      ) : null}
      <div className="flex flex-col gap-4">
        <SettingsInput
          testId="super-admin-warm-runtime-name"
          label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_NAME)}
          type="text"
          value={form.name}
          isDisabled={!isNew}
          onChange={setField("name")}
          error={fieldError("name", {
            invalid: t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_NAME_INVALID),
          })}
          showRequiredTag={isNew}
          autoComplete="off"
        />
        <SettingsInput
          testId="super-admin-warm-runtime-image"
          label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_IMAGE)}
          type="text"
          value={form.image}
          onChange={setField("image")}
          showRequiredTag
          autoComplete="off"
        />
        <SettingsInput
          testId="super-admin-warm-runtime-working-dir"
          label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_WORKING_DIR)}
          type="text"
          value={form.workingDir}
          onChange={setField("workingDir")}
          showRequiredTag
          autoComplete="off"
        />
        <SettingsTextarea
          testId="super-admin-warm-runtime-command"
          label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_COMMAND)}
          value={form.command}
          description={
            <p className="text-xs text-tertiary-alt">
              {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_COMMAND_HINT)}
            </p>
          }
          onChange={setField("command")}
          rows={2}
          showOptionalTag
        />
        <SettingsTextarea
          testId="super-admin-warm-runtime-args"
          label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_ARGS)}
          value={form.args}
          description={
            <p className="text-xs text-tertiary-alt">
              {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_ARGS_HINT)}
            </p>
          }
          onChange={setField("args")}
          rows={2}
          showOptionalTag
        />
        <SettingsTextarea
          testId="super-admin-warm-runtime-environment"
          label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_ENVIRONMENT)}
          value={form.environment}
          description={
            <p className="text-xs text-tertiary-alt">
              {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_ENVIRONMENT_HINT)}
            </p>
          }
          onChange={setField("environment")}
          error={
            errors.environment
              ? t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_ENVIRONMENT_INVALID, {
                  line: errors.environmentLine,
                })
              : undefined
          }
          rows={5}
          showOptionalTag
        />
      </div>

      <div className="flex flex-col gap-4">
        <SectionTitle>
          {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_RESOURCES)}
        </SectionTitle>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SettingsInput
            testId="super-admin-warm-runtime-cpu-request"
            label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_CPU_REQUEST)}
            type="text"
            placeholder="500m"
            value={form.cpuRequest}
            onChange={setField("cpuRequest")}
            showOptionalTag
            autoComplete="off"
          />
          <SettingsInput
            testId="super-admin-warm-runtime-cpu-limit"
            label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_CPU_LIMIT)}
            type="text"
            placeholder="2"
            value={form.cpuLimit}
            onChange={setField("cpuLimit")}
            showOptionalTag
            autoComplete="off"
          />
          <SettingsInput
            testId="super-admin-warm-runtime-memory-request"
            label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_MEMORY_REQUEST)}
            type="text"
            placeholder="2Gi"
            value={form.memoryRequest}
            onChange={setField("memoryRequest")}
            showOptionalTag
            autoComplete="off"
          />
          <SettingsInput
            testId="super-admin-warm-runtime-memory-limit"
            label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_MEMORY_LIMIT)}
            type="text"
            placeholder="4Gi"
            value={form.memoryLimit}
            onChange={setField("memoryLimit")}
            showOptionalTag
            autoComplete="off"
          />
          <SettingsInput
            testId="super-admin-warm-runtime-ephemeral-storage-request"
            label={t(
              I18nKey.SUPER_ADMIN$WARM_RUNTIME_EPHEMERAL_STORAGE_REQUEST,
            )}
            type="text"
            placeholder="10Gi"
            value={form.ephemeralStorageRequest}
            onChange={setField("ephemeralStorageRequest")}
            showOptionalTag
            autoComplete="off"
          />
          <SettingsInput
            testId="super-admin-warm-runtime-ephemeral-storage-limit"
            label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_EPHEMERAL_STORAGE_LIMIT)}
            type="text"
            placeholder="10Gi"
            value={form.ephemeralStorageLimit}
            onChange={setField("ephemeralStorageLimit")}
            showOptionalTag
            autoComplete="off"
          />
        </div>
        <p className="text-xs text-tertiary-alt">
          {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_RESOURCES_HINT)}
        </p>
      </div>

      <div className="flex flex-col gap-4">
        <SectionTitle>
          {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_LIFECYCLE)}
        </SectionTitle>
        <SettingsInput
          testId="super-admin-warm-runtime-idle-seconds"
          label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_IDLE_SECONDS)}
          type="text"
          value={form.idleSeconds}
          description={
            <p className="text-xs text-tertiary-alt">
              {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_IDLE_SECONDS_HINT)}
            </p>
          }
          onChange={setField("idleSeconds")}
          error={fieldError("idleSeconds", { integer: integerMessage })}
          showOptionalTag
          autoComplete="off"
        />
        <SettingsInput
          testId="super-admin-warm-runtime-count"
          label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_COUNT)}
          type="text"
          value={form.count}
          description={
            <p className="text-xs text-tertiary-alt">
              {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_COUNT_HINT)}
            </p>
          }
          onChange={setField("count")}
          error={fieldError("count", { integer: integerMessage })}
          showOptionalTag
          autoComplete="off"
        />
        <SettingsInput
          testId="super-admin-warm-runtime-persistent-storage-size"
          label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_PERSISTENT_STORAGE_SIZE)}
          type="text"
          placeholder="10Gi"
          value={form.persistentStorageSize}
          description={
            <p className="text-xs text-tertiary-alt">
              {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_PERSISTENT_STORAGE_SIZE_HINT)}
            </p>
          }
          onChange={setField("persistentStorageSize")}
          showOptionalTag
          autoComplete="off"
        />
        <SettingsSwitch
          testId="super-admin-warm-runtime-fuse-s3-mount"
          isToggled={form.fuseS3Mount}
          onToggle={setField("fuseS3Mount")}
          hint={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_FUSE_S3_MOUNT_HINT)}
        >
          {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_FUSE_S3_MOUNT)}
        </SettingsSwitch>
      </div>

      <div className="flex flex-col gap-4">
        <SectionTitle>
          {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_SECURITY)}
        </SectionTitle>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <SettingsInput
            testId="super-admin-warm-runtime-run-as-user"
            label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_RUN_AS_USER)}
            type="text"
            value={form.runAsUser}
            onChange={setField("runAsUser")}
            error={fieldError("runAsUser", { integer: integerMessage })}
            showOptionalTag
            autoComplete="off"
          />
          <SettingsInput
            testId="super-admin-warm-runtime-run-as-group"
            label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_RUN_AS_GROUP)}
            type="text"
            value={form.runAsGroup}
            onChange={setField("runAsGroup")}
            error={fieldError("runAsGroup", { integer: integerMessage })}
            showOptionalTag
            autoComplete="off"
          />
          <SettingsInput
            testId="super-admin-warm-runtime-fs-group"
            label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_FS_GROUP)}
            type="text"
            value={form.fsGroup}
            onChange={setField("fsGroup")}
            error={fieldError("fsGroup", { integer: integerMessage })}
            showOptionalTag
            autoComplete="off"
          />
        </div>
      </div>

      <p className="text-xs text-tertiary-alt">
        {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_PROPAGATION_HINT)}
      </p>
      <BrandButton
        testId="super-admin-warm-runtime-form-submit"
        type="submit"
        variant="primary"
        className="self-start"
        isDisabled={!canSubmit || isPending}
      >
        {isNew
          ? t(I18nKey.SUPER_ADMIN$CREATE_WARM_RUNTIME)
          : t(I18nKey.SETTINGS$SAVE_CHANGES)}
      </BrandButton>
    </form>
  );
}

/** Super Admin page that creates a warm runtime (`/super-admin/warm-runtimes/new`)
 * or edits one (`/super-admin/warm-runtimes/edit/{name}`). */
export function SuperAdminWarmRuntimeEdit() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { name } = useParams<{ name: string }>();
  const isNew = name === undefined;
  const { data, isLoading, isError } = useWarmRuntimes();
  const runtime = isNew ? null : data?.find((item) => item.name === name);

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="super-admin-warm-runtime-edit"
    >
      <button
        type="button"
        data-testid="super-admin-warm-runtime-edit-back"
        onClick={() => navigate(SUPER_ADMIN_PATHS.warmRuntimes)}
        className={cn(
          "inline-flex w-fit shrink-0 items-center gap-1.5",
          "text-xs font-medium text-[var(--oh-muted)] hover:text-white",
          "transition-colors",
        )}
      >
        <FaChevronLeft size={10} aria-hidden="true" />
        {t(I18nKey.SUPER_ADMIN$BACK_TO_WARM_RUNTIMES)}
      </button>
      <SuperAdminPageHeader
        title={
          isNew
            ? t(I18nKey.SUPER_ADMIN$ADD_WARM_RUNTIME)
            : t(I18nKey.SUPER_ADMIN$EDIT_WARM_RUNTIME)
        }
        subtitle={isNew ? "" : (name ?? "")}
      />
      {isNew ? <WarmRuntimeForm runtime={null} /> : null}
      {!isNew && isLoading ? (
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$LOADING)}
        </p>
      ) : null}
      {!isNew && isError ? (
        <p className="text-sm text-red-400">
          {t(I18nKey.SUPER_ADMIN$LOAD_ERROR)}
        </p>
      ) : null}
      {!isNew && data && !runtime ? (
        <p
          data-testid="super-admin-warm-runtime-not-found"
          className="text-sm text-red-400"
        >
          {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_NOT_FOUND)}
        </p>
      ) : null}
      {runtime ? (
        <WarmRuntimeForm key={JSON.stringify(runtime)} runtime={runtime} />
      ) : null}
    </div>
  );
}
