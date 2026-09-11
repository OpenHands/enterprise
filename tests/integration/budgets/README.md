# Organization budget property probes

This directory tests Enterprise organization-budget behavior against real services rather than mocked provider accounting.

## Harness

The session-scoped harness starts:

- a migrated Enterprise PostgreSQL database, cloned per test;
- LiteLLM `v1.94.0` with its own PostgreSQL database;
- a deterministic OpenAI-compatible provider;
- a path-aware proxy between Enterprise management calls and LiteLLM.

The provider always returns 10 prompt tokens and 5 completion tokens. The generated LiteLLM configuration prices those tokens at exactly `$1.00` per accepted request. This makes team spend, member spend, successful requests, and provider calls directly comparable.

Enterprise creates a dedicated bootstrap LiteLLM team, sets `LITE_LLM_TEAM_ID` to that team for user provisioning, and removes it during teardown. Each scenario creates a real organization, two real members, real LiteLLM users, real team memberships, and real keys.

The management proxy can fail selected LiteLLM paths while ordinary inference still goes directly to LiteLLM. This supports partial-write and failed-readback probes without mocking either Enterprise persistence or LiteLLM accounting.

Docker must be available locally.

## Why Quint and Hypothesis are both necessary

`quint-specs/org-budget.qnt` is the finite abstract model. It explores policy transitions independently of Python control flow, HTTP behavior, database details, and LiteLLM implementation details. Its invariants define the intended contract: organization caps are absolute, overrides have precise semantics, successful synchronization means exact policy agreement, rejected requests have no accounting effects, and unverified policy fails closed.

Quint is necessary because generated integration examples cannot exhaustively explore every abstract ordering. It catches contradictions and missing cases in the state model before those assumptions become test code.

Hypothesis executes generated operation sequences against the real Enterprise service and LiteLLM. It checks that concrete database rows, management writes, readback values, inference responses, spend, and provider calls refine the abstract model. When implementation behavior diverges, Hypothesis minimizes the sequence to a small reproduction.

Hypothesis is necessary because a valid abstract model cannot prove that migrations, serialization, management endpoints, asynchronous accounting, or LiteLLM admission actually implement that model.

## Property-to-probe map

| Intended property | Quint invariant | Real-service coverage | Current status |
| --- | --- | --- | --- |
| Organization cap is absolute | `P1_organizationCapIsAbsolute` | Sequential and cross-member concurrency probes | Explicit safety gates |
| Default member cap applies | `P2_defaultUserCapApplies` | Generated request campaign | Explicit safety gate |
| Positive override replaces the default | `P3_positiveOverrideReplacesDefault` | Healthy policy state machine | Passing |
| Disabled override removes only the member cap | `P4_disabledOverrideRemovesOnlyMemberCap` | Disabled-override probe | Product-confirmed; known failing behavior |
| Verified policy exactly matches all applied caps | `P5_verifiedMeansExactAgreement` | Healthy state machine and LiteLLM contracts | Passing |
| Verification of an older policy version is invalid | `P6_staleVerificationIsInvalid` | No delayed/out-of-order concrete probe yet | Model only |
| Unverified policy fails closed before provider invocation | `P7_unverifiedPolicyFailsClosed` | Team-write, member-write, and readback variants in the fail-closed probe | Known failing behavior; target of PR #359 |
| Partial synchronization cannot report success | `P8_partialSynchronizationIsNotSuccess` | Partial member-write failure contract | Passing |
| Retry restores exact policy agreement | `P9_successfulRetryRepairsDivergence` | Partial-sync retry contract and recovery probe | Basic retry passes; spend-preserving recovery is an explicit safety gate |
| Rejection has no accounting side effects | `P10_rejectionHasNoAccountingSideEffects` | Sequential and concurrency probes | Explicit safety gates |
| Every accepted request has exactly one provider call and charge | `P11_admissionHasExactlyOneCharge` | Deterministic accounting contract plus sequential and concurrency probes | Basic contract passes; generated/concurrent behavior is unstable |
| Spend remains nonnegative and never moves backward | `P12_spendIsNonnegativeAndMonotonicByConstruction` | Accounting and recovery probes | Basic coverage; recovery is an explicit safety gate |
| Member accounting is isolated | `P13_userAccountingIsIsolated` | Cross-member concurrency probe | Explicit safety gate |
| Synchronization is idempotent | `P14_synchronizationIsIdempotent` | Generated maintenance and legacy-baseline second sync | Passing |
| Rejected retries never reach the provider | `P15_rejectedRetriesRemainHarmless` | Same-member and cross-member boundary probes | Explicit safety gates |
| Disabling the organization limit preserves independent member enforcement | Exact agreement after `disableOrganizationLimit` | Organization-limit disable contract | Passing |
| Zero and negative limits are rejected | Positive-limit transition domain | Healthy state machine | Passing |
| A missing known-member baseline recovers once without renewing allowance | Not modeled | Legacy-upgrade LiteLLM contract | Passing; added after validating PR #347 before and after |
| Concurrent admission overshoot is bounded | Not modeled | Same-member and cross-member concurrency probes | Explicit safety gates |

“Passing” rows are included in normal `test_*.py` collection. Explicit safety gates use the `probe_*.py` prefix so known product gaps remain executable without hiding them behind `xfail`.

## Campaigns

### Abstract model

```bash
npx --yes @informalsystems/quint@0.29.1 typecheck quint-specs/org-budget.qnt
npx --yes @informalsystems/quint@0.29.1 run \
  --invariant=allProperties \
  --max-steps=25 \
  --max-samples=10000 \
  --backend=rust \
  quint-specs/org-budget.qnt
```

### Management, accounting, synchronization, and recovery contracts

```bash
uv run pytest tests/integration/budgets/test_litellm_contract.py -n 0
```

The upgrade-recovery contract recreates the legacy migrated state where a member is already known to budget synchronization but has no persisted cycle baseline. It requires maintenance to anchor the missing baseline to live LiteLLM spend, replace the stale absolute cap, and reuse that baseline on later synchronization instead of renewing the allowance.

### Healthy policy state machine

```bash
uv run pytest tests/integration/budgets/test_org_budget_state_machine.py -n 0
```

The passing campaign runs 8 examples with 10 generated policy transitions each. Increase exploration without editing the test:

```bash
BUDGET_STATE_MACHINE_EXAMPLES=30 \
BUDGET_STATE_MACHINE_STEPS=20 \
uv run pytest tests/integration/budgets/test_org_budget_state_machine.py -n 0
```

The generated rules cover organization and default-member limit changes, positive overrides, override deletion, invalid limits, and idempotent maintenance while the organization budget is enabled.

### Sequential accounting safety probe

```bash
uv run pytest tests/integration/budgets/probe_sequential_accounting.py -n 0
```

This uses the same Hypothesis machine with request transitions enabled. It checks accepted and rejected responses, exact `$1.00` team/member accounting, and one provider call per accepted request. Repeated generated runs can currently expose a successful provider call whose spend remains unchanged, so this probe stays outside default collection while that nondeterminism is investigated.

### Concurrency safety probes

```bash
uv run pytest tests/integration/budgets/probe_same_member_concurrency.py -n 0
uv run pytest tests/integration/budgets/probe_cross_member_concurrency.py -n 0
```

These probes issue same-member and cross-member requests at an organization boundary. They verify deterministic accounting, provider-call correspondence, bounded request-boundary overshoot, organization-cap authority, and harmless rejected retries. They are outside default collection because concurrent successful completions can currently be observed without the corresponding spend update.

### Passing backend campaign

```bash
uv run pytest tests/integration/budgets/test_*.py -n 0
```

Run these probes serially. They share session-scoped LiteLLM and deterministic-provider observations, while each test receives an isolated Enterprise database and organization.

## Recovery safety gate

`probe_recovery.py` verifies that successful reconciliation preserves existing spend and reopens admission:

```bash
uv run pytest tests/integration/budgets/probe_recovery.py -n 0
```

This probe remains outside default collection because the pre-recovery completion can currently succeed without the expected spend update.

## Fail-closed safety gate

`probe_fail_closed.py` contains the currently failing OHE-3268 safety contract. It is deliberately outside pytest's default `test_*.py` collection so healthy policy exploration can continue without hiding the known defect behind `xfail`.

Run it explicitly when changing request admission or budget reconciliation:

```bash
uv run pytest tests/integration/budgets/probe_fail_closed.py -n 0
```

It injects team-write, member-write, and readback failures. Every case requires inference rejection before the provider is called. Once OHE-3268 is fixed, rename this file into default test collection and include it in the passing campaign.

## Independent organization and member enforcement

`test_disable_organization_limit.py` captures the product-confirmed transition where the organization limit is disabled while individual member budgets remain enforceable:

```bash
uv run pytest tests/integration/budgets/test_disable_organization_limit.py -n 0
```

The contract requires LiteLLM to remove the organization team cap, preserve each default member cap, accept a request below the member cap, and reject the next request before it reaches the provider. It is included in the passing backend campaign.

## Disabled-override safety gate

`probe_disabled_override.py` enables generated disabled-override transitions:

```bash
uv run pytest tests/integration/budgets/probe_disabled_override.py -n 0
```

The Quint contract treats a disabled member override as unlimited for that member while retaining the organization cap. The real-service probe currently observes the previous member cap after applying the override, so this transition remains outside the passing policy campaign.

## Same-member concurrency safety gate

`probe_same_member_concurrency.py` sends two and four simultaneous requests through one managed key:

```bash
uv run pytest tests/integration/budgets/probe_same_member_concurrency.py -n 0
```

It requires every successful provider call to produce exactly one team and member charge, allows at most one-request cost per concurrent request at the admission boundary, and requires later rejected retries to have no provider effect. The current LiteLLM-backed probe observes a successful completion and provider call without the corresponding spend update under same-member concurrency, so it remains outside the passing campaign.


## Changing budget backend code

Use this order:

1. Run Quint after changing policy semantics or invariants.
2. Run the focused contract file after changing LiteLLM management or accounting code.
3. Run the healthy state machine after changing settings, overrides, synchronization, or maintenance.
4. Run concurrency probes after changing caps or request-boundary behavior.
5. Run the fail-closed safety gate after changing reconciliation or inference admission.
6. Run the passing backend campaign and Python pre-commit checks before submitting the change.

A Quint failure usually means the intended contract is inconsistent. A minimized Hypothesis failure means real behavior diverged from that contract. A deterministic provider-call mismatch means a rejected request reached the provider or an accepted request was not charged exactly once.
