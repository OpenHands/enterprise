export type BudgetControlMode = "managed" | "external" | "needs_adoption";

export interface BudgetNotificationPreferences {
  thresholds: {
    percentage: number;
    email_enabled: boolean;
    slack_enabled: boolean;
  }[];
  slack_channel: string | null;
  slack_team_id: string | null;
}

export interface BudgetNotificationState extends BudgetNotificationPreferences {
  fingerprint: string;
  email_configured: boolean;
  slack_account_linked: boolean;
}

export interface BudgetNotificationUpdate extends BudgetNotificationPreferences {
  expected_fingerprint: string;
}

export interface BudgetControlState {
  control_mode: BudgetControlMode;
  generation: number;
}

export interface NativeBudgetPolicy {
  max_budget: number | null;
  soft_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
  models: string[] | null;
  allowed_models: string[] | null;
  blocked: boolean | null;
  rpm_limit: number | null;
  tpm_limit: number | null;
  max_parallel_requests: number | null;
  has_additional_budget_windows: boolean;
  has_model_budgets: boolean;
}

export interface BudgetPreview extends BudgetControlState {
  fingerprint: string;
  observed_at: string;
  pending_operation_id: string | null;
  team_spend: number;
  team_policy: NativeBudgetPolicy;
  default_member_policy: NativeBudgetPolicy;
  members: {
    user_id: string;
    in_organization: boolean;
    lifetime_spend: number;
    enforcement_spend: number;
    max_budget: number | null;
    counter_source: "membership" | "new_membership";
    budget_source: "private_member" | "default_member" | "team";
    budget_duration: string | null;
    budget_reset_at: string | null;
    policy: NativeBudgetPolicy;
  }[];
  keys: { user_id: string | null; policy: NativeBudgetPolicy }[];
}

export interface BudgetAdoptionRequest {
  preview_fingerprint: string;
  idempotency_key: string;
  current_team_allowance: number;
  current_default_member_allowance: number | null;
  current_member_allowances?: Record<string, number | null>;
  future_monthly_limit: number;
  future_default_member_limit: number | null;
  future_member_limits?: Record<string, number | null>;
  reset_day: 1 | 15;
  replace_native_reset_schedules: boolean;
}

export interface BudgetPolicyUpdate {
  idempotency_key: string;
  expected_generation: number;
  enabled: boolean;
  current_cycle_team_allowance: number;
  current_cycle_default_member_allowance: number | null;
  current_cycle_member_allowances: Record<string, number | null>;
  future_monthly_limit: number;
  future_default_member_limit: number | null;
  future_member_limits: Record<string, number | null>;
  reset_day: 1 | 15;
}

export interface BudgetOperation extends BudgetControlState {
  operation_id: string;
  operation_generation: number;
  kind: "adopt" | "settings" | "rollover" | "repair";
  status: "pending" | "applied" | "abandoned";
  error: string | null;
  created_at: string;
  finished_at: string | null;
  current_allowances: {
    team: number | null;
    default_member: number | null;
    members: Record<string, number | null>;
  };
  future_policy: {
    monthly_limit: number;
    default_user_monthly_limit: number | null;
    member_limits: Record<string, number | null>;
    reset_day: 1 | 15;
  };
  cycle_end_at: string;
  team_block: {
    initial: boolean | null;
    target: boolean | null;
    budget_owned: boolean;
  } | null;
}
