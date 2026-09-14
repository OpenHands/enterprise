import { useState } from "react";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { GitLabWebhookManager } from "./gitlab-webhook-manager";
import { useMe } from "#/hooks/query/use-me";
import { ProjectManagementIntegration } from "../project-management/project-management-integration";
import { useGitConnections } from "#/hooks/query/use-git-connections";
import {
  useConnectGitProvider,
  useDisconnectGitConnection,
  useRefreshGitConnections,
  useSaveGitCredential,
  useGitHubInstallation,
} from "#/hooks/mutation/use-git-connections";
import {
  GitCapability,
  NATIVE_GIT_PROVIDERS,
  NativeGitProvider as NativeGitProviderName,
  GitConnection,
} from "#/api/git-connection-service/git-connection-service.api";
import { useConfig } from "#/hooks/query/use-config";
import { SettingsInput } from "../settings-input";
import { SettingsDropdownInput } from "../settings-dropdown-input";
import { KeyStatusIcon } from "../key-status-icon";
import { GitProviderConnection } from "./git-provider-connection";
import { GitHubTokenHelpAnchor } from "./github-token-help-anchor";
import { GitLabTokenHelpAnchor } from "./gitlab-token-help-anchor";
import { BitbucketTokenHelpAnchor } from "./bitbucket-token-help-anchor";
import { Typography } from "#/ui/typography";
import { BrandButton } from "#/components/features/settings/brand-button";
import { AuthError } from "#/components/features/native-auth/auth-form";

const providerNames: Record<NativeGitProviderName, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  bitbucket: "Bitbucket",
};

function NativeGitProvider({
  provider,
  capability,
  connections,
}: {
  provider: NativeGitProviderName;
  capability: GitCapability;
  connections: GitConnection[];
}): React.JSX.Element {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const [host, setHost] = useState(capability.default_host);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [editing, setEditing] = useState(false);
  const save = useSaveGitCredential();
  const connect = useConnectGitProvider();
  const disconnect = useDisconnectGitConnection();
  const install = useGitHubInstallation();
  const refresh = useRefreshGitConnections();
  const current = connections.find((connection) => connection.host === host);
  const showEditor = current?.status !== "connected" || editing;
  const accountLabel =
    current?.account.display_name ||
    current?.account.login ||
    current?.account.id;
  let statusKey = "NATIVE_GIT$NOT_CONNECTED";
  if (current)
    statusKey =
      current.status === "connected"
        ? "NATIVE_GIT$CONNECTED"
        : "NATIVE_GIT$RECONNECT_REQUIRED";
  const busy =
    save.isPending ||
    connect.isPending ||
    disconnect.isPending ||
    install.isPending;
  const manual =
    capability.methods.includes("pat") ||
    capability.methods.includes("api_token");
  const report = (cause: unknown): void =>
    setError(
      cause instanceof Error ? cause.message : t("NATIVE_GIT$UNAVAILABLE"),
    );
  const submit = async (
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setError(null);
    setSaved(false);
    try {
      await save.run({
        provider,
        host,
        token: String(fields.get("token")),
        ...(provider === "bitbucket"
          ? { email: String(fields.get("email")) }
          : {}),
      });
      form.reset();
      await refresh();
      setSaved(true);
      setEditing(false);
    } catch (cause) {
      form.reset();
      report(cause);
    }
  };
  const disconnectCurrent = async (): Promise<void> => {
    setError(null);
    setSaved(false);
    try {
      await disconnect.run({ provider, host });
      await refresh();
      setEditing(false);
    } catch (cause) {
      report(cause);
    }
  };
  const controls = (
    <>
      {capability.hosts.length > 1 ? (
        <SettingsDropdownInput
          testId={`${provider}-host`}
          name={`${provider}-host`}
          label={t("NATIVE_GIT$HOST")}
          selectedKey={host}
          wrapperClassName="w-full max-w-[680px]"
          isClearable={false}
          allowsCustomValue={false}
          isDisabled={busy}
          items={capability.hosts.map(
            (value: string): { key: string; label: string } => ({
              key: value,
              label: value,
            }),
          )}
          onSelectionChange={(key: React.Key | null): void => {
            if (typeof key === "string" && capability.hosts.includes(key)) {
              setHost(key);
              setEditing(false);
              setError(null);
              setSaved(false);
            }
          }}
        />
      ) : (
        <p className="text-sm text-tertiary-alt">{host}</p>
      )}
      {accountLabel && <p className="text-sm">{accountLabel}</p>}
      <AuthError message={error} />
      {saved && <p role="status">{t("NATIVE_GIT$SAVED")}</p>}
      {!showEditor && (manual || capability.methods.includes("oauth")) && (
        <BrandButton
          type="button"
          variant="secondary"
          className="w-55"
          isDisabled={busy}
          onClick={(): void => {
            setEditing(true);
            setSaved(false);
          }}
        >
          {t("NATIVE_GIT$RECONNECT")}
        </BrandButton>
      )}
      {showEditor && manual && (
        <form
          key={host}
          className="ph-no-capture ph-mask flex flex-col gap-4"
          onSubmit={submit}
        >
          {provider === "bitbucket" && (
            <SettingsInput
              className="w-full max-w-[680px]"
              label={t("NATIVE_GIT$BITBUCKET_EMAIL")}
              type="email"
              name="email"
              autoComplete="off"
              required
            />
          )}
          <SettingsInput
            className="w-full max-w-[680px]"
            label={t(
              provider === "bitbucket"
                ? "NATIVE_GIT$API_TOKEN"
                : "NATIVE_GIT$PAT",
            )}
            type="password"
            name="token"
            autoComplete="off"
            required
            startContent={
              <KeyStatusIcon isSet={current?.status === "connected"} />
            }
          />
          <p className="text-sm text-tertiary-alt">
            {t("NATIVE_GIT$TOKEN_HELP")}
          </p>
          {provider === "github" && <GitHubTokenHelpAnchor />}
          {provider === "gitlab" && <GitLabTokenHelpAnchor />}
          {provider === "bitbucket" && <BitbucketTokenHelpAnchor />}
          <BrandButton
            type="submit"
            variant="primary"
            className="w-55"
            isDisabled={busy}
          >
            {t(current ? "NATIVE_GIT$RECONNECT" : "NATIVE_GIT$CONNECT")}
          </BrandButton>
        </form>
      )}
      {showEditor && capability.methods.includes("oauth") && (
        <BrandButton
          type="button"
          className="w-55"
          variant="secondary"
          isDisabled={busy}
          onClick={async () => {
            setError(null);
            try {
              const result = await connect.run({ provider, host });
              window.location.assign(result.authorization_url);
            } catch (cause) {
              report(cause);
            }
          }}
        >
          {t("NATIVE_GIT$CONNECT_OAUTH", {
            provider: providerNames[provider] || provider,
          })}
        </BrandButton>
      )}
      {current?.status === "connected" && editing && (
        <BrandButton
          type="button"
          variant="secondary"
          className="w-55"
          isDisabled={busy}
          onClick={(): void => {
            setEditing(false);
            setError(null);
          }}
        >
          {t("BUTTON$CANCEL")}
        </BrandButton>
      )}
      {current && showEditor && (
        <p className="text-sm text-tertiary-alt">
          {t("NATIVE_GIT$DISCONNECT_HELP")}
        </p>
      )}
      {provider === "github" &&
        (capability.installation_available || config?.github_app_slug) &&
        current && (
          <BrandButton
            type="button"
            variant="secondary"
            className="w-55"
            isDisabled={busy}
            onClick={async () => {
              setError(null);
              try {
                const result = await install.run(undefined);
                window.location.assign(result.installation_url);
              } catch (cause) {
                report(cause);
              }
            }}
          >
            {t("GITHUB$CONFIGURE_REPOS")}
          </BrandButton>
        )}
      {provider === "gitlab" &&
        current?.status === "connected" &&
        current.host === capability.webhook_host && <GitLabWebhookManager />}
    </>
  );
  return (
    <>
      <section className="flex flex-col gap-4 pb-8">
        <Typography.H3 className="text-xl">
          {providerNames[provider]}
        </Typography.H3>
        <GitProviderConnection
          provider={provider}
          providerName={providerNames[provider]}
          isConnected={current?.status === "connected"}
          nativeControls={{
            content: controls,
            statusLabel: t(statusKey),
            isPending: busy,
            onDisconnect: current ? disconnectCurrent : undefined,
          }}
        />
      </section>
      <div className="w-1/2 border-b border-gray-200" />
    </>
  );
}

export function NativeGitSettings(): React.JSX.Element {
  const { t } = useTranslation();
  const connections = useGitConnections();
  const { data: me } = useMe();
  const { data: config } = useConfig();
  const showProjectManagement =
    (me?.role === "admin" || me?.role === "owner") &&
    (config?.feature_flags?.enable_jira ||
      config?.feature_flags?.enable_jira_dc ||
      config?.feature_flags?.enable_linear);
  const [params] = useSearchParams();
  const result = params.get("git_result");
  return (
    <div className="flex flex-col gap-6 pb-8">
      <p>{t("NATIVE_GIT$OPTIONAL_HELP")}</p>
      {result === "connected" && (
        <p role="status">{t("NATIVE_GIT$CONNECTED")}</p>
      )}
      {result === "cancelled" && (
        <p role="status">{t("NATIVE_GIT$CANCELLED")}</p>
      )}
      {result === "error" && (
        <AuthError message={t("NATIVE_GIT$OAUTH_ERROR")} />
      )}
      {connections.isPending && <p>{t("HOME$LOADING")}</p>}
      {connections.isError && (
        <>
          <AuthError message={t("NATIVE_GIT$UNAVAILABLE")} />
          <BrandButton
            type="button"
            variant="secondary"
            onClick={() => connections.refetch()}
          >
            {t("NATIVE_GIT$TRY_AGAIN")}
          </BrandButton>
        </>
      )}
      {NATIVE_GIT_PROVIDERS.map(
        (provider: NativeGitProviderName): React.JSX.Element | null => {
          const capability = connections.data?.capabilities[provider];
          if (!capability) return null;
          return (
            <NativeGitProvider
              key={provider}
              provider={provider}
              capability={capability}
              connections={
                connections.data?.connections.filter(
                  (connection: GitConnection): boolean =>
                    connection.provider === provider,
                ) ?? []
              }
            />
          );
        },
      )}
      {showProjectManagement && <ProjectManagementIntegration />}
      {connections.data &&
        Object.keys(connections.data.capabilities).length === 0 && (
          <p>{t("NATIVE_GIT$NO_PROVIDERS")}</p>
        )}
    </div>
  );
}
