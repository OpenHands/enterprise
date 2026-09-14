/* eslint-disable i18next/no-literal-string */
import { useState } from "react";
import { isAxiosError } from "axios";
import type { BudgetNotificationState } from "#/api/budget-service/budget-service.types";
import { useBudgetNotifications } from "#/hooks/query/use-budget-control";
import { useUpdateBudgetNotifications } from "#/hooks/mutation/use-budget-control";

function NotificationForm({
  orgId,
  initial,
  reload,
}: {
  orgId: string;
  initial: BudgetNotificationState;
  reload: () => void;
}) {
  const [thresholds, setThresholds] = useState(initial.thresholds);
  const [channel, setChannel] = useState(initial.slack_channel ?? "");
  const [workspace, setWorkspace] = useState(initial.slack_team_id ?? "");
  const [fingerprint, setFingerprint] = useState(initial.fingerprint);
  const save = useUpdateBudgetNotifications();
  const unique = new Set(thresholds.map((row) => row.percentage));
  const valid =
    unique.size === thresholds.length &&
    thresholds.every(
      (row) =>
        Number.isInteger(row.percentage) &&
        row.percentage >= 1 &&
        row.percentage <= 100,
    );
  const update = (
    index: number,
    changes: Partial<BudgetNotificationState["thresholds"][number]>,
  ) => {
    save.reset();
    setThresholds((rows) =>
      rows.map((row, position) =>
        position === index ? { ...row, ...changes } : row,
      ),
    );
  };
  const conflict =
    isAxiosError(save.error) && save.error.response?.status === 409;
  return (
    <form
      aria-label="Budget notifications"
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!valid || save.isPending) return;
        save.mutate(
          {
            orgId,
            request: {
              expected_fingerprint: fingerprint,
              thresholds,
              slack_channel: channel || null,
              slack_team_id: workspace || null,
            },
          },
          { onSuccess: (result) => setFingerprint(result.fingerprint) },
        );
      }}
    >
      <h2 className="text-xl font-semibold">Budget notifications</h2>
      <p>
        Alert preferences do not change limits or adopt budget control. Alerts
        apply when OpenHands manages a positive current-cycle allowance.
      </p>
      {!initial.email_configured && (
        <p role="status">
          Email delivery is not configured on this installation.
        </p>
      )}
      {!initial.slack_account_linked && (
        <p>
          To enable Slack alerts, first link your Slack account with this
          organization selected. The workspace is verified when you save.
        </p>
      )}
      <fieldset disabled={save.isPending} className="space-y-4">
        {thresholds.map((row, index) => (
          <div key={index} className="flex flex-wrap items-center gap-4">
            <label>
              Threshold {index + 1} (%)
              <input
                aria-label={`Alert threshold ${index + 1}`}
                type="number"
                min={1}
                max={100}
                step={1}
                required
                value={row.percentage || ""}
                onChange={(event) =>
                  update(index, { percentage: Number(event.target.value) })
                }
                className="ml-2 w-24 rounded border border-neutral-600 bg-transparent p-2"
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={row.email_enabled}
                onChange={(event) =>
                  update(index, { email_enabled: event.target.checked })
                }
              />{" "}
              Email admins at {row.percentage}%
            </label>
            <label>
              <input
                type="checkbox"
                checked={row.slack_enabled}
                disabled={!initial.slack_account_linked && !row.slack_enabled}
                onChange={(event) =>
                  update(index, { slack_enabled: event.target.checked })
                }
              />{" "}
              Slack at {row.percentage}%
            </label>
            <button
              type="button"
              onClick={() => {
                save.reset();
                setThresholds((rows) =>
                  rows.filter((_, position) => position !== index),
                );
              }}
            >
              Remove {row.percentage}% alert
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={thresholds.length >= 100}
          onClick={() => {
            save.reset();
            const percentage = [
              80,
              90,
              100,
              ...Array.from({ length: 100 }, (_, i) => i + 1),
            ].find((value) => !unique.has(value));
            if (percentage !== undefined)
              setThresholds((rows) => [
                ...rows,
                { percentage, email_enabled: true, slack_enabled: false },
              ]);
          }}
        >
          Add alert threshold
        </button>
        <div className="flex flex-wrap gap-4">
          <label>
            Slack workspace ID
            <input
              value={workspace}
              onChange={(event) => {
                save.reset();
                setWorkspace(event.target.value);
              }}
              placeholder="T0123456789"
              maxLength={80}
              pattern="T[A-Z0-9]+"
              className="ml-2 rounded border border-neutral-600 bg-transparent p-2"
            />
          </label>
          <label>
            Slack channel
            <input
              value={channel}
              onChange={(event) => {
                save.reset();
                setChannel(event.target.value);
              }}
              placeholder="#budget-alerts"
              maxLength={80}
              className="ml-2 rounded border border-neutral-600 bg-transparent p-2"
            />
          </label>
        </div>
        {!valid && (
          <p role="alert">Use unique whole-number thresholds from 1 to 100.</p>
        )}
        <button type="submit" disabled={!valid || save.isPending}>
          {save.isPending ? "Saving notifications…" : "Save notifications"}
        </button>
      </fieldset>
      {save.isSuccess && <p role="status">Notification preferences saved.</p>}
      {save.isError && (
        <p role="alert">
          {conflict
            ? "Alert preferences changed. Reload saved preferences before editing again."
            : "Notification preferences could not be confirmed. Check your Slack connection if enabled, or reload saved preferences before retrying."}
        </p>
      )}
      <button type="button" disabled={save.isPending} onClick={reload}>
        Reload saved preferences (discards edits)
      </button>
    </form>
  );
}

export function BudgetNotifications({ orgId }: { orgId: string }) {
  const preferences = useBudgetNotifications(orgId);
  const [reloadVersion, setReloadVersion] = useState(0);
  if (preferences.isFetching || preferences.isPending)
    return <p>Loading notification preferences…</p>;
  if (preferences.isError)
    return (
      <div>
        <p role="alert">Notification preferences are unavailable.</p>
        <button type="button" onClick={() => preferences.refetch()}>
          Retry loading notifications
        </button>
      </div>
    );
  return (
    <NotificationForm
      key={`${orgId}:${reloadVersion}`}
      orgId={orgId}
      initial={preferences.data}
      reload={async () => {
        const result = await preferences.refetch();
        if (result.isSuccess) setReloadVersion((version) => version + 1);
      }}
    />
  );
}
