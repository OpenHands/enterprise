// Org Budgets
//
// Models server/services/org_budget_service.py (OrgBudgetService: the monthly
// cycle roll, the LiteLLM financial read with its retry/stale-cache fallback,
// the budget sync with read-back verification, the per-user overrides and the
// percentage threshold alerts) together with storage/org_budget_store.py (the
// row writers the service calls -- and that a caller can reach directly,
// without the service's checks).
//
// COVERS: the OrgBudgetSettings row's whole lifecycle (created on demand by
// _get_or_create_settings, or directly by OrgBudgetStore.create_settings with
// an unconstrained reset_day); _current_cycle_start / _next_cycle_start
// including the reset_day values that name a day the month does not have;
// _roll_cycle_if_needed and the concurrent second roll the code has no lock
// against; _get_financial_snapshot's live / stale / unavailable outcomes over a
// failable LiteLLM read (retryable-transient, retryable-persistent, fatal,
// malformed) with _cached_financial_snapshot as the stale fallback;
// _repair_missing_members_for_cycle; _sync_litellm_budgets end to end including
// the read-back verification, the per-member cycle baselines and the caps a
// negative override produces; _maybe_send_alerts with last_triggered_cycle_start
// as the once-per-cycle latch and the email/Slack fan-out; the override
// upsert/delete on both the service and the store; the users_search term reaching
// ILIKE through _escape_ilike; and OrgMemberFinancialService.get_org_members_financial_data
// with its page_id contract, its unescaped email_filter, its swallow-and-report-zero
// fallback and its next_page_id.
//
// DOES NOT COVER: _budget_row_matches_status' eight status filters and the
// users_page / users_per_page slicing (the route pins both to ge=1, and neither
// changes any budget decision); the exact text of a sync error string (the model
// carries only whether the sync reported success); the SMTP and Slack clients
// themselves and _resolve_slack_team_id's multi-team ambiguity (email-notifications
// owns delivery -- alerts are latched at the point the service hands them off);
// LiteLLM's own spend accounting, which litellm-proxy-integration owns and this
// component only reads back; and the retry SLEEP length, which no property here
// depends on. Money is modelled in whole units, so _budget_values_match's 1e-6
// tolerance is exact equality.
//
// The known behaviours are MIRRORED, not fixed: a roll re-anchors to
// _current_cycle_start(now) and so can skip more than one reset period at once;
// OrgBudgetStore.replace_thresholds drops the rows that carry
// last_triggered_cycle_start, re-arming every alert inside the live cycle; and
// get_user_budget_row never calls _reject_personal_org, so it creates budget
// settings for a personal workspace that every other entry point refuses. So is the
// member financial listing's next_page_id, which compares the offset against the
// has_more boolean the caller bound as `total_count`.
module org_budgets {
  import basicSpells.* from "./spells/basicSpells"

  // --------------------------------------------------------------- constants

  /// The org whose budget rows this model holds, and a personal workspace: a
  /// User row shares its id, which is what _is_personal_org detects.
  pure val ORG: str = "org"
  pure val PERSONAL_ORG: str = "personal"
  pure val ORG_IDS: Set[str] = Set(ORG, PERSONAL_ORG)

  /// u1/u2 start as org members; u3 never is, so it can appear in the LiteLLM
  /// team as an unmanaged member.
  pure val USERS: Set[str] = Set("u1", "u2", "u3")

  pure val BOOLS: Set[bool] = Set(false, true)

  pure val SUCCESS: str = "success"
  pure val ERROR: str = "error"
  pure val SKIPPED: str = "skipped"

  // -- calendar ---------------------------------------------------------------

  /// Month lengths for a non-leap window. Indices -1..5 are carried so that a
  /// cycle start one month either side of the clock window is still a real
  /// calendar month: an invalid date in this model means the reset_day itself
  /// names a day the month does not have, never a missing table entry.
  pure val DAYS_IN_MONTH: int -> int =
    Map(-1 -> 31, 0 -> 31, 1 -> 28, 2 -> 31, 3 -> 30, 4 -> 31, 5 -> 30)

  type Date = { mi: int, d: int }

  /// A total order on dates. Ghosts record this rather than the record so that
  /// lineage comparisons stay plain integer comparisons.
  pure def dkey(dt: Date): int = dt.mi * 32 + dt.d

  /// datetime(year, month, day) succeeds only for a day the month really has.
  pure def valid_date(dt: Date): bool =
    DAYS_IN_MONTH.has(dt.mi) and dt.d >= 1 and dt.d <= DAYS_IN_MONTH.get(dt.mi)

  /// The wall-clock values the model steps through: the two reset days the API
  /// allows plus the month ends that make a stored reset_day of 29-31 bite.
  pure val CLOCK: Set[Date] =
    Set(0, 1, 2, 3)
      .fold(Set(), (acc, m) =>
        acc.union(Set(1, 14, 15, 16, 28, 29, 30, 31).map(d => { mi: m, d: d })))
      .filter(dt => valid_date(dt))

  /// _current_cycle_start: this month's reset day if it has already passed,
  /// otherwise last month's.
  pure def current_cycle_start(nw: Date, reset_day: int): Date =
    if (nw.d >= reset_day) { mi: nw.mi, d: reset_day }
    else { mi: nw.mi - 1, d: reset_day }

  /// _next_cycle_start: one month on from the cycle start, at reset_day.
  pure def next_cycle_start(cs: Date, reset_day: int): Date =
    { mi: cs.mi + 1, d: reset_day }

  // -- money and the values a caller can supply --------------------------------

  /// OrgBudgetSettingsUpdate pins monthly_limit to gt=0, so a PATCH can only set
  /// a positive cap or clear it outright.
  pure val LIMITS: Set[int] = Set(100, 200)

  /// OrgUserBudgetOverride.monthly_limit is a plain nullable Float column: the
  /// store writes whatever it is handed, including the zero and the negative the
  /// route's validator would have refused.
  pure val OVERRIDE_LIMITS: Set[int] = Set(-100, 0, 100, 300)

  /// Spend LiteLLM records between our reads. Against a 200 cap these land under the
  /// lowest threshold, just under it, and past every one of them.
  pure val SPEND_STEPS: Set[int] = Set(30, 150, 400)

  /// _validate_reset_day allows 1 and 15 only. OrgBudgetStore.create_settings
  /// takes the column's full range.
  pure val STORED_RESET_DAYS: Set[int] = Set(1, 15, 29, 31)

  pure val THRESHOLD_SETS: Set[Set[int]] = Set(Set(50), Set(80, 90, 100), Set())

  // -- LiteLLM ------------------------------------------------------------------

  type Member = { spend: int, max_budget: Option[int], uses_shared: bool }

  /// The LiteLLM proxy's view. `ll_users` is every user LiteLlmManager.user_exists
  /// would confirm; `members` is the subset actually on this org's team.
  type LiteLlm = {
    team_spend: int,
    team_max_budget: Option[int],
    members: str -> Member,
    ll_users: Set[str],
  }

  /// What the next LiteLLM financial read does. _is_retryable_litellm_read_error
  /// separates the first two (429/5xx/transport, retried up to
  /// LITELLM_FINANCIAL_READ_MAX_ATTEMPTS) from FatalError, which is raised on the
  /// first attempt. Malformed passes the fetch and fails
  /// _parse_litellm_financial_snapshot, which is never retried.
  type ReadFault =
    NoFault | TransientRetryable | PersistentRetryable | FatalError | Malformed

  pure val READ_FAULTS: Set[ReadFault] =
    Set(NoFault, TransientRetryable, PersistentRetryable, FatalError, Malformed)

  pure def is_retryable(f: ReadFault): bool =
    f == TransientRetryable or f == PersistentRetryable

  /// _fetch_litellm_financial_data's loop: an error is re-attempted only while
  /// _is_retryable_litellm_read_error says so, and only inside
  /// LITELLM_FINANCIAL_READ_MAX_ATTEMPTS -- so a transient retryable error clears, a
  /// persistent one exhausts the attempts, and a fatal one is raised on the first.
  pure def fetch_ok(f: ReadFault): bool =
    f == NoFault or f == Malformed or (is_retryable(f) and f == TransientRetryable)

  /// What a LiteLLM write does. WriteDropped is the write that returns 200 and
  /// leaves the proxy's stored budget alone -- the case the read-back exists for.
  type WriteMode = WriteOk | WriteDropped | WriteRaises
  pure val WRITE_MODES: Set[WriteMode] = Set(WriteOk, WriteDropped, WriteRaises)

  type Snapshot = {
    team_spend: int,
    team_max_budget: Option[int],
    members: str -> Member,
    observed_at: Date,
  }

  type SpendStatus = Live | Stale | Unavailable

  // -- users_search --------------------------------------------------------------

  type Meta = Pct | Under | Bslash

  /// A users_search term: `lit` names it, `metas` are the ILIKE metacharacters it
  /// contains, and `hits` is the ground truth -- the members whose email or
  /// git_user_name literally contains the term.
  type Term = { lit: str, metas: Set[Meta], hits: Set[str] }

  pure val TERMS: Set[Term] = Set(
    { lit: "", metas: Set(), hits: USERS },
    { lit: "alice", metas: Set(), hits: Set("u1") },
    { lit: "%", metas: Set(Pct), hits: Set("u3") },
    { lit: "_", metas: Set(Under), hits: Set() },
    { lit: "\\", metas: Set(Bslash), hits: Set() },
  )

  /// _escape_ilike backslash-escapes exactly these three.
  pure val ESCAPED_METAS: Set[Meta] = Set(Bslash, Pct, Under)

  /// _escape_ilike followed by ILIKE(pattern, escape='\\'). A metacharacter the
  /// escape function covers matches itself; one it misses keeps its wildcard
  /// meaning, and `%` alone then matches every row in the org.
  pure def ilike_rows(t: Term): Set[str] =
    if (t.metas.exclude(ESCAPED_METAS) == Set()) t.hits else USERS

  /// OrgMemberStore.get_org_members_paginated interpolates email_filter straight into
  /// `ilike(f'%{email_filter}%')` -- no _escape_ilike, no escape= -- so every wildcard
  /// the caller sends keeps its wildcard meaning.
  pure def raw_ilike_rows(t: Term): Set[str] =
    if (t.metas.intersect(Set(Pct, Under)) == Set()) t.hits else USERS

  /// The member listing's `order_by(OrgMember.user_id)`.
  pure val USER_LIST: List[str] = List("u1", "u2", "u3")

  /// The page_id a caller sends: absent, an offset, or something int() cannot parse.
  type PageId = NoPage | Page(int) | Unparsable
  pure val PAGE_IDS: Set[PageId] = Set(NoPage, Page(0), Page(1), Page(-1), Unparsable)
  pure val PAGE_LIMITS: Set[int] = Set(1, 10)

  /// An absent email_filter: `if email_filter:` is falsy, so no filter is applied.
  pure val NO_FILTER: Term = { lit: "", metas: Set(), hits: USERS }

  /// The second element of get_org_members_paginated's tuple, as the caller binds it.
  /// The store declares it `has_more: bool`, so in production it only ever arrives as
  /// False or True -- 0 or 1 once the caller compares an offset against it. A larger
  /// value is what a caller gets only when the collaborator returns a real row count.
  /// 0 and 1 are the two values the declared `bool` can arrive as; 5 and 25 are the
  /// real row counts a collaborator returning an actual count supplies.
  pure val TOTAL_COUNTS: Set[int] = Set(0, 1, 5, 25)

  /// The documented ValueError: page_id must parse as an integer, and be non-negative.
  pure def page_id_ok(p: PageId): bool =
    match p { | NoPage => true | Unparsable => false | Page(o) => o >= 0 }

  pure def page_offset(p: PageId): int =
    match p { | NoPage => 0 | Page(o) => o | Unparsable => 0 }

  /// `.offset(offset).limit(limit + 1)` -- one row past the page, to detect has_more.
  pure def page_window(l: List[str], offset: int, lim: int): List[str] =
    l.foldl({ i: 0, out: List() }, (a, u) =>
      if (a.i >= offset and a.out.length() < lim + 1) { i: a.i + 1, out: a.out.append(u) }
      else { i: a.i + 1, out: a.out }).out

  // -- persisted rows --------------------------------------------------------------

  type Settings = {
    enabled: bool,
    monthly_limit: Option[int],
    reset_day: int,
    default_user_monthly_limit: Option[int],
    slack_channel: Option[str],
    cycle_start_at: Date,
    cycle_start_spend: int,
    user_cycle_start_spend: str -> int,
    sync_status: Option[str],
    // _cache_financial_snapshot's last-known-good copy
    cached_at: Option[Date],
    cached_team_spend: Option[int],
    cached_member_spend: str -> int,
    known_member_ids: Set[str],
  }

  /// last_cycle is last_triggered_cycle_start as a dkey: the once-per-cycle latch.
  type Threshold = { email: bool, slack: bool, last_cycle: Option[int] }

  type Override = { monthly_limit: Option[int], is_disabled: bool }

  pure val NO_THRESHOLDS: int -> Threshold = Map()
  pure val NO_OVERRIDES: str -> Override = Map()
  pure val NO_BASELINES: str -> int = Map()
  pure val NO_CAPS: str -> Option[int] = Map()

  pure val DEFAULT_THRESHOLDS: int -> Threshold = Map(
    80 -> { email: true, slack: false, last_cycle: None },
    90 -> { email: true, slack: true, last_cycle: None },
    100 -> { email: true, slack: true, last_cycle: None })

  /// One PATCH body. A field left None was absent from model_fields_set, so
  /// update_budget_settings leaves the stored value alone.
  type Update = {
    lbl: str,
    enabled: Option[bool],
    monthly_limit: Option[Option[int]],
    reset_day: Option[int],
    default_user_limit: Option[Option[int]],
    slack_channel: Option[Option[str]],
    thresholds: Option[Set[int]],
  }

  pure val NO_UPDATE: Update = {
    lbl: "", enabled: None, monthly_limit: None, reset_day: None,
    default_user_limit: None, slack_channel: None, thresholds: None }

  pure val UPDATES: Set[Update] = Set(
    { ...NO_UPDATE, lbl: "enable", enabled: Some(true), monthly_limit: Some(Some(200)) },
    { ...NO_UPDATE, lbl: "enable_only", enabled: Some(true) },
    { ...NO_UPDATE, lbl: "disable", enabled: Some(false) },
    { ...NO_UPDATE, lbl: "clear_limit", monthly_limit: Some(None) },
    { ...NO_UPDATE, lbl: "limit_100", monthly_limit: Some(Some(100)) },
    { ...NO_UPDATE, lbl: "reset_day_15", reset_day: Some(15) },
    { ...NO_UPDATE, lbl: "reset_day_1", reset_day: Some(1) },
    { ...NO_UPDATE, lbl: "default_user_limit", default_user_limit: Some(Some(100)) },
    { ...NO_UPDATE, lbl: "slack_channel", slack_channel: Some(Some("#ops")) },
    { ...NO_UPDATE, lbl: "thresholds_50", thresholds: Some(Set(50)) },
  )

  pure val OVERRIDE_WRITES: Set[Override] = Set(
    { monthly_limit: Some(100), is_disabled: false },
    { monthly_limit: Some(-100), is_disabled: false },
    { monthly_limit: Some(0), is_disabled: false },
    { monthly_limit: None, is_disabled: true },
    { monthly_limit: None, is_disabled: false },
  )

  // ------------------------------------------------------------------- ghosts

  /// One committed cycle roll, recorded where it happens so a property can
  /// compare the new cycle start against a recomputation from the old one.
  type Roll = { from_key: int, to_key: int, reset_day: int }

  /// One spend figure handed back to a caller, with the label the code put on it.
  /// `real` records whether a LiteLLM read actually produced the numbers, or whether
  /// they were filled in after a failed read.
  type Presented = { status: SpendStatus, observed_key: int, at_key: int, real: bool }

  /// One page of the member financial listing: whether more rows remained, and
  /// whether the response offered a next_page_id for them.
  type PageTrace = { has_more: bool, next_offered: bool }

  /// One finished _sync_litellm_budgets: the caps it meant to write, the snapshot
  /// it read back, and whether it reported success.
  type SyncTrace = {
    reported_success: bool,
    exp_team: Option[int],
    exp_members: str -> Option[int],
    readback: Option[Snapshot],
  }

  type SearchTrace = { hits: Set[str], rows: Set[str] }

  type State = {
    now: Date,
    settings: Option[Settings],
    thresholds: int -> Threshold,
    overrides: str -> Override,
    org_members: Set[str],
    litellm: LiteLlm,
    read_fault: ReadFault,
    write_mode: WriteMode,

    // --- ghosts: lineage and silent-outcome latches, no counterpart in the DB
    last_roll: Option[Roll],
    last_presented: Option[Presented],
    last_sync: Option[SyncTrace],
    last_search: Option[SearchTrace],
    last_page: Option[PageTrace],
    financial_zeroed: bool,
    financial_auth_raised: bool,
    alerts_sent: Set[(int, int)],
    alert_duplicated: bool,
    cap_below_baseline: bool,
    settings_created: bool,
    personal_settings_created: bool,
    date_crash: bool,
    retry_recovered: bool,
    fatal_no_retry: bool,
    parse_rejected: bool,
    repair_ran: bool,
    repair_failed: bool,
    stale_used: bool,
    unavailable_used: bool,
    unmapped_reported: bool,
    sync_skipped: bool,
    sync_succeeded: bool,
    sync_errored: bool,
    member_missing_reported: bool,
    baseline_missing_reported: bool,
    rolled: bool,
    last_rolled: bool,
    concurrent_rolled: bool,
    maint_skipped_personal: bool,
    maint_skipped_unavailable: bool,
    maint_skipped_repair: bool,
    enable_needed_fresh_spend: bool,
    override_delete_noop: bool,
    store_write_unsynced: bool,
    thresholds_replaced: bool,
    alert_sent: bool,
    alert_email_sent: bool,
    alert_slack_sent: bool,
    alert_slack_suppressed: bool,
  }

  var s: State

  // ------------------------------------------------------------- pure helpers

  pure def is_personal(org_id: str): bool = org_id == PERSONAL_ORG

  pure def or_zero(o: Option[int]): int = match o { | None => 0 | Some(v) => v }

  pure def opt_of(m: str -> Override, u: str): Option[Override] =
    if (m.has(u)) Some(m.get(u)) else None

  /// _effective_user_budget_limit: a disabled override means no cap at all, an
  /// override with a limit wins over the org default, and no override falls back
  /// to it.
  pure def effective_limit(ov: Option[Override], default_limit: Option[int]): Option[int] =
    match ov {
      | None => default_limit
      | Some(o) => if (o.is_disabled) None else o.monthly_limit
    }

  pure def is_disabled_for(ov: Option[Override]): bool =
    match ov { | None => false | Some(o) => o.is_disabled }

  pure def default_settings(nw: Date): Settings = {
    enabled: false,
    monthly_limit: None,
    reset_day: 1,
    default_user_monthly_limit: None,
    slack_channel: None,
    cycle_start_at: current_cycle_start(nw, 1),
    cycle_start_spend: 0,
    user_cycle_start_spend: NO_BASELINES,
    sync_status: None,
    cached_at: None,
    cached_team_spend: None,
    cached_member_spend: NO_BASELINES,
    known_member_ids: Set(),
  }

  /// The OrgBudgetSettings row a deployment starts this component from. `Fresh` is an
  /// org that has never opened the budgets page. `Configured` is the row the enable
  /// flow leaves behind. `Migrated` is the row the litellm_known_member_ids backfill
  /// leaves behind: members already counted as known, whose per-member cycle baselines
  /// were never written -- the state
  /// tests/unit/test_org_budget_service.py::test_sync_preserves_known_member_cap_when_cycle_baseline_is_missing
  /// and ::test_sync_initializes_baseline_for_member_added_after_migration set up.
  /// `Cached` is a Configured row that also carries the last-known-good spend copy
  /// _cache_financial_snapshot left behind (litellm_last_spend_snapshot_at /
  /// litellm_last_team_spend / litellm_last_member_spend), which is what makes a
  /// `stale` read reachable on the very first call.
  type Start = Fresh | Configured | Migrated | Cached
  pure val STARTS: Set[Start] = Set(Fresh, Configured, Migrated, Cached)

  pure val START_NOW: Date = { mi: 0, d: 1 }
  pure val START_MEMBERS: Set[str] = Set("u1", "u2")

  pure def start_settings(start: Start): Option[Settings] =
    match start {
      | Fresh => None
      | Configured => Some({ ...default_settings(START_NOW),
          enabled: true,
          monthly_limit: Some(200),
          slack_channel: Some("#ops"),
          user_cycle_start_spend: START_MEMBERS.mapBy(u => 0),
          known_member_ids: START_MEMBERS })
      | Migrated => Some({ ...default_settings(START_NOW),
          enabled: true,
          monthly_limit: Some(200),
          known_member_ids: START_MEMBERS })
      | Cached => Some({ ...default_settings(START_NOW),
          enabled: true,
          monthly_limit: Some(200),
          user_cycle_start_spend: START_MEMBERS.mapBy(u => 0),
          known_member_ids: START_MEMBERS,
          cached_at: Some(START_NOW),
          cached_team_spend: Some(0),
          cached_member_spend: START_MEMBERS.mapBy(u => 0) })
    }

  /// The clock a replayed call may observe: unchanged, or exactly the moment the
  /// current cycle ends -- the only jump that changes whether a roll is due.
  pure def clock_choices(st: State): Set[Date] = {
    val sg = settings_or_default(st)
    val nc = next_cycle_start(sg.cycle_start_at, sg.reset_day)
    if (valid_date(nc) and dkey(nc) > dkey(st.now)) Set(st.now, nc) else Set(st.now)
  }

  /// The last-known-good copy the row carried when a call ran. Neither the LiteLLM
  /// read's outcome nor the row's cached copy is logged, and the copy is written by
  /// earlier requests this model never saw -- or by a test seeding the row directly --
  /// so a replayed read takes it as an input rather than inferring it.
  pure def cache_for(sg: Settings, nw: Date, present: bool): Settings =
    if (present)
      { ...sg,
        cached_at: Some({ mi: nw.mi - 1, d: 1 }),
        cached_team_spend: Some(sg.cycle_start_spend),
        cached_member_spend: sg.user_cycle_start_spend }
    else
      { ...sg, cached_at: None, cached_team_spend: None, cached_member_spend: NO_BASELINES }

  /// _get_or_create_settings, read half.
  pure def settings_or_default(st: State): Settings =
    match st.settings { | None => default_settings(st.now) | Some(g) => g }

  /// _get_or_create_settings, write half: a fresh row also gets DEFAULT_THRESHOLDS.
  pure def with_settings_created(st: State): State =
    match st.settings {
      | Some(_) => st
      | None => { ...st,
          settings: Some(default_settings(st.now)),
          thresholds: DEFAULT_THRESHOLDS,
          settings_created: true }
    }

  pure def live_snapshot(st: State): Snapshot = {
    team_spend: st.litellm.team_spend,
    team_max_budget: st.litellm.team_max_budget,
    members: st.litellm.members,
    observed_at: st.now,
  }

  /// _cached_financial_snapshot. The cache keeps spend only, so every member comes
  /// back with no cap and uses_shared_budget true -- it is a spend record, never a
  /// budget record.
  pure def cached_snapshot(sg: Settings): Option[Snapshot] =
    match sg.cached_at {
      | None => None
      | Some(at) =>
        match sg.cached_team_spend {
          | None => None
          | Some(ts) => Some({
              team_spend: ts,
              team_max_budget: None,
              members: sg.cached_member_spend.keys().mapBy(u =>
                { spend: sg.cached_member_spend.get(u), max_budget: None, uses_shared: true }),
              observed_at: at })
        }
    }

  pure def cache_into(sg: Settings, snap: Snapshot): Settings = {
    ...sg,
    cached_at: Some(snap.observed_at),
    cached_team_spend: Some(snap.team_spend),
    cached_member_spend: snap.members.keys().mapBy(u => snap.members.get(u).spend),
  }

  type SnapResult = {
    snap: Option[Snapshot],
    status: SpendStatus,
    settings: Settings,
    parse_failed: bool,
    retried: bool,
    fatal: bool,
    incomplete: bool,
  }

  /// _get_financial_snapshot. A clean read with complete membership is also cached;
  /// a read that is missing an org member is still returned live but is NOT cached.
  /// require_complete_membership turns that missing member into the read error
  /// itself, and allow_stale is what lets the last-known-good copy stand in.
  pure def get_financial_snapshot(
    st: State, sg: Settings, allow_stale: bool, require_complete: bool
  ): SnapResult = {
    val snap = live_snapshot(st)
    val missing = st.org_members.exclude(snap.members.keys())
    val read_failed = not(fetch_ok(st.read_fault)) or st.read_fault == Malformed
    val failed = read_failed or (missing != Set() and require_complete)
    if (not(failed))
      { snap: Some(snap),
        status: Live,
        settings: if (missing == Set()) cache_into(sg, snap) else sg,
        parse_failed: false,
        retried: st.read_fault == TransientRetryable,
        fatal: false,
        incomplete: missing != Set() }
    else {
      val fallback = if (allow_stale) cached_snapshot(sg) else None
      val parse_failed = read_failed and st.read_fault == Malformed
      val fatal = read_failed and st.read_fault == FatalError
      match fallback {
        | Some(c) => { snap: Some(c), status: Stale, settings: sg,
                       parse_failed: parse_failed, retried: false,
                       fatal: fatal, incomplete: false }
        | None => { snap: None, status: Unavailable, settings: sg,
                    parse_failed: parse_failed, retried: false,
                    fatal: fatal, incomplete: false }
      }
    }
  }

  pure def presented_of(r: SnapResult, nw: Date): Presented = {
    status: r.status,
    observed_key: match r.snap { | None => -1 | Some(sn) => dkey(sn.observed_at) },
    at_key: dkey(nw),
    real: true,
  }

  /// The read outcomes worth latching, applied wherever a read happens.
  pure def note_read(st: State, r: SnapResult): State = { ...st,
    parse_rejected: st.parse_rejected or r.parse_failed,
    retry_recovered: st.retry_recovered or r.retried,
    fatal_no_retry: st.fatal_no_retry or r.fatal,
    stale_used: st.stale_used or r.status == Stale,
    unavailable_used: st.unavailable_used or r.status == Unavailable,
  }

  /// _litellm_cycle_spend: live team spend minus the baseline taken at cycle start.
  pure def cycle_spend(sg: Settings, snap: Snapshot): int =
    max(snap.team_spend - sg.cycle_start_spend, 0)

  /// _litellm_unmapped_cycle_spend's member count: LiteLLM team members this org
  /// does not know about.
  pure def unmapped_count(snap: Snapshot, org_ids: Set[str]): int =
    snap.members.keys().exclude(org_ids).size()

  // -- LiteLLM writes -----------------------------------------------------------

  pure def apply_team_write(ll: LiteLlm, wm: WriteMode, cap: Option[int]): LiteLlm =
    if (wm == WriteOk) { ...ll, team_max_budget: cap } else ll

  pure def apply_member_write(ll: LiteLlm, wm: WriteMode, u: str, cap: Option[int]): LiteLlm =
    if (wm != WriteOk or not(ll.members.has(u))) ll
    else { ...ll, members: ll.members.put(u,
      { ...ll.members.get(u), max_budget: cap, uses_shared: cap == None }) }

  /// LiteLlmManager.add_user_to_team during the pre-roll membership repair.
  pure def add_to_team(ll: LiteLlm, wm: WriteMode, u: str, cap: Option[int]): LiteLlm =
    if (wm != WriteOk) ll
    else { ...ll, members: ll.members.put(u,
      { spend: 0, max_budget: cap, uses_shared: cap == None }) }

  /// _budget_sync_readback_errors: the read-back must show exactly the caps the
  /// sync meant to write, and a member left uncapped must read back as
  /// uses_shared_budget.
  pure def readback_mismatch(
    rb: Snapshot, exp_team: Option[int], exp: str -> Option[int]
  ): bool =
    rb.team_max_budget != exp_team or
    exp.keys().exists(u =>
      not(rb.members.has(u)) or
      (match exp.get(u) {
        | None => not(rb.members.get(u).uses_shared)
        | Some(b) => rb.members.get(u).uses_shared or rb.members.get(u).max_budget != Some(b)
      }))

  // -- _sync_litellm_budgets ------------------------------------------------------

  type SyncOut = {
    litellm: LiteLlm,
    settings: Settings,
    snap: Option[Snapshot],
    exp_team: Option[int],
    exp_members: str -> Option[int],
    readback: Option[Snapshot],
    reported_success: bool,
    skipped: bool,
    cap_below_baseline: bool,
    member_missing: bool,
    baseline_missing: bool,
  }

  type MemberAcc = {
    ll: LiteLlm,
    active: str -> int,
    exp: str -> Option[int],
    below: bool,
    base_missing: bool,
    user_err: bool,
  }

  /// The body of _sync_litellm_budgets once it holds a snapshot: write the team
  /// cap, write every governed member's cap anchored on that member's cycle
  /// baseline, rewrite the baselines, then read everything back and verify.
  pure def sync_with(st: State, sg: Settings, snapshot: Snapshot): SyncOut = {
    val org_ids = st.org_members
    val ll_ids = snapshot.members.keys()
    val known = sg.known_member_ids
    val new_ids = org_ids.exclude(known)
    val missing = org_ids.exclude(ll_ids)
    val unmanaged = ll_ids.exclude(org_ids)
    val governed = org_ids.intersect(ll_ids)
    val baselines = sg.user_cycle_start_spend

    // Anchor the team cap on cycle_start_spend so LiteLLM's cumulative counter
    // and our cycle stay aligned.
    val capped = sg.enabled and or_zero(sg.monthly_limit) != 0
    val exp_team =
      if (capped) Some(sg.cycle_start_spend + or_zero(sg.monthly_limit)) else None
    val ll1 = apply_team_write(st.litellm, st.write_mode, exp_team)
    val team_err = st.write_mode == WriteRaises

    val acc: MemberAcc = governed.fold(
      { ll: ll1, active: NO_BASELINES, exp: NO_CAPS,
        below: false, base_missing: false, user_err: false },
      (a, u) => {
        val info = snapshot.members.get(u)
        val has_base = baselines.has(u)
        if (not(has_base) and sg.enabled and not(new_ids.contains(u)))
          // member_cycle_baseline_missing: skipped entirely, cap left as it is
          { ...a, base_missing: true }
        else {
          val baseline = if (has_base) baselines.get(u) else info.spend
          val ov = opt_of(st.overrides, u)
          val eff = effective_limit(ov, sg.default_user_monthly_limit)
          val cap =
            if (is_disabled_for(ov)) None
            else match eff { | None => None | Some(l) => Some(baseline + l) }
          { ll: apply_member_write(a.ll, st.write_mode, u, cap),
            active: a.active.put(u, baseline),
            exp: a.exp.put(u, cap),
            below: a.below or (match cap { | None => false | Some(c) => c < baseline }),
            base_missing: a.base_missing,
            user_err: a.user_err or st.write_mode == WriteRaises }
        }
      })

    // Baselines for members outside the governed set survive only if they already
    // had one; everyone else is dropped from the map.
    val active1 = missing.union(unmanaged).fold(acc.active, (m, u) =>
      if (baselines.has(u)) m.put(u, baselines.get(u)) else m)

    val sg1 = { ...sg,
      user_cycle_start_spend: active1,
      known_member_ids: known.intersect(org_ids).union(governed) }

    val rb = get_financial_snapshot({ ...st, litellm: acc.ll }, sg1, false, false)
    val errors =
      team_err or acc.user_err or acc.base_missing or missing != Set() or
      (match rb.snap {
        | None => true
        | Some(r) => readback_mismatch(r, exp_team, acc.exp)
      })

    { litellm: acc.ll,
      settings: rb.settings,
      snap: match rb.snap { | None => Some(snapshot) | Some(r) => Some(r) },
      exp_team: exp_team,
      exp_members: acc.exp,
      readback: rb.snap,
      reported_success: not(errors),
      skipped: false,
      cap_below_baseline: acc.below,
      member_missing: missing != Set(),
      baseline_missing: acc.base_missing }
  }

  /// _sync_litellm_budgets. A team that is off and was not just switched off is
  /// recorded 'skipped' and left alone; otherwise the caller's snapshot is used, or
  /// a fresh one is read and a failure recorded as an error with nothing written.
  pure def sync_litellm_budgets(
    st: State, sg: Settings, clear_disabled: bool, given: Option[Snapshot]
  ): SyncOut = {
    val idle: SyncOut = {
      litellm: st.litellm, settings: sg, snap: given,
      exp_team: None, exp_members: NO_CAPS, readback: None,
      reported_success: false, skipped: true, cap_below_baseline: false,
      member_missing: false, baseline_missing: false }
    if (not(sg.enabled) and not(clear_disabled)) idle
    else match given {
      | Some(sn) => sync_with(st, sg, sn)
      | None => {
          val r = get_financial_snapshot(st, sg, false, false)
          match r.snap {
            | None => { ...idle, settings: r.settings, snap: None, skipped: false }
            | Some(sn) => sync_with(st, r.settings, sn)
          }
        }
    }
  }

  /// Commit one sync's effects, including the litellm_last_sync_status the UI reads.
  pure def apply_sync(st: State, sy: SyncOut): State = { ...st,
    litellm: sy.litellm,
    settings: Some({ ...sy.settings,
      sync_status: Some(
        if (sy.skipped) SKIPPED else if (sy.reported_success) SUCCESS else ERROR) }),
    last_sync: if (sy.skipped) st.last_sync
               else Some({ reported_success: sy.reported_success,
                           exp_team: sy.exp_team,
                           exp_members: sy.exp_members,
                           readback: sy.readback }),
    sync_skipped: st.sync_skipped or sy.skipped,
    sync_succeeded: st.sync_succeeded or (not(sy.skipped) and sy.reported_success),
    sync_errored: st.sync_errored or (not(sy.skipped) and not(sy.reported_success)),
    cap_below_baseline: st.cap_below_baseline or sy.cap_below_baseline,
    member_missing_reported: st.member_missing_reported or sy.member_missing,
    baseline_missing_reported: st.baseline_missing_reported or sy.baseline_missing,
  }

  // -- alerts ---------------------------------------------------------------------

  /// _maybe_send_alerts' selection: every threshold the spend has reached that has
  /// not already fired for this cycle_start.
  pure def due_thresholds(
    sg: Settings, ths: int -> Threshold, spend: int, cycle_key: int
  ): Set[int] =
    if (not(sg.enabled) or or_zero(sg.monthly_limit) <= 0) Set()
    else ths.keys().filter(p =>
      spend * 100 >= p * or_zero(sg.monthly_limit) and
      ths.get(p).last_cycle != Some(cycle_key))

  /// _maybe_send_alerts + _send_alerts + _send_slack_alert. Slack needs a channel;
  /// without one the alert is silently dropped for that channel.
  pure def send_alerts(st: State, sg: Settings, spend: int, cycle_key: int): State = {
    val due = due_thresholds(sg, st.thresholds, spend, cycle_key)
    if (due == Set()) st
    else { ...st,
      thresholds: st.thresholds.keys().mapBy(p =>
        if (due.contains(p)) { ...st.thresholds.get(p), last_cycle: Some(cycle_key) }
        else st.thresholds.get(p)),
      alerts_sent: st.alerts_sent.union(due.map(p => (p, cycle_key))),
      alert_duplicated:
        st.alert_duplicated or due.exists(p => st.alerts_sent.contains((p, cycle_key))),
      alert_sent: true,
      alert_email_sent: st.alert_email_sent or due.exists(p => st.thresholds.get(p).email),
      alert_slack_sent: st.alert_slack_sent or
        due.exists(p => st.thresholds.get(p).slack and sg.slack_channel != None),
      alert_slack_suppressed: st.alert_slack_suppressed or
        due.exists(p => st.thresholds.get(p).slack and sg.slack_channel == None) }
  }

  // -- the cycle roll ---------------------------------------------------------------

  type RollOut = { st: State, sg: Settings }

  /// The committing half of _roll_cycle_if_needed: re-anchor the cycle and every
  /// baseline to the snapshot, clear every threshold latch, then sync.
  pure def roll_now(st: State, sg: Settings, snap: Snapshot): RollOut = {
    val new_start = current_cycle_start(st.now, sg.reset_day)
    val sg1 = { ...sg,
      cycle_start_at: new_start,
      cycle_start_spend: snap.team_spend,
      user_cycle_start_spend: snap.members.keys().mapBy(u => snap.members.get(u).spend),
      known_member_ids: st.org_members }
    val st1 = { ...st,
      settings: Some(sg1),
      thresholds: st.thresholds.keys().mapBy(p =>
        { ...st.thresholds.get(p), last_cycle: None }),
      last_roll: Some({ from_key: dkey(sg.cycle_start_at),
                        to_key: dkey(new_start),
                        reset_day: sg.reset_day }),
      rolled: true,
      last_rolled: true }
    val sy = sync_litellm_budgets(st1, sg1, false, Some(snap))
    { st: apply_sync(st1, sy), sg: sy.settings }
  }

  pure def roll_is_due(sg: Settings, nw: Date): bool =
    dkey(nw) >= dkey(next_cycle_start(sg.cycle_start_at, sg.reset_day))

  // -- membership repair ---------------------------------------------------------

  type RepairOut = { st: State, sg: Settings, snap: Option[Snapshot], ran: bool }

  /// _repair_missing_members_for_cycle: every org member LiteLLM has lost is put
  /// back on the team at its effective cap, then the whole read is re-verified with
  /// require_complete_membership. A member LiteLlmManager.user_exists cannot find
  /// needs an explicit key repair, so the whole rollover is abandoned.
  pure def repair_missing(st: State, sg: Settings, snap: Snapshot): RepairOut = {
    val missing = st.org_members.exclude(snap.members.keys())
    if (missing == Set()) { st: st, sg: sg, snap: Some(snap), ran: false }
    else if (missing.exists(u => not(st.litellm.ll_users.contains(u))) or
             st.write_mode == WriteRaises)
      { st: st, sg: sg, snap: None, ran: true }
    else {
      val ll1 = missing.fold(st.litellm, (ll, u) => {
        val ov = opt_of(st.overrides, u)
        val cap =
          if (is_disabled_for(ov)) None
          else effective_limit(ov, sg.default_user_monthly_limit)
        add_to_team(ll, st.write_mode, u, cap)
      })
      val st1 = { ...st, litellm: ll1 }
      val r = get_financial_snapshot(st1, sg, false, true)
      { st: note_read(st1, r), sg: r.settings, snap: r.snap, ran: true }
    }
  }

  // -- run_budget_maintenance -------------------------------------------------------

  /// The tail of run_budget_maintenance: roll if the cycle is over (which syncs),
  /// alert on the spend that leaves, and otherwise sync.
  pure def maintenance_tail(st: State, sg: Settings, snap: Snapshot, repaired: bool): State = {
    val due = roll_is_due(sg, st.now)
    if (due and not(valid_date(current_cycle_start(st.now, sg.reset_day))))
      { ...st, date_crash: true }
    else {
      val r = if (due) roll_now(st, sg, snap) else { st: st, sg: sg }
      val spend = cycle_spend(r.sg, snap)
      val st1 = send_alerts({ ...r.st, repair_ran: r.st.repair_ran or repaired },
                            r.sg, spend, dkey(r.sg.cycle_start_at))
      if (due) st1
      else apply_sync(st1, sync_litellm_budgets(st1, r.sg, false, Some(snap)))
    }
  }

  /// run_budget_maintenance. A personal workspace is reported skipped rather than
  /// refused; a spend read that cannot be refreshed stops the run before the roll,
  /// with allow_stale=False so the cache is deliberately not consulted.
  pure def do_run_maintenance(st00: State, org_id: str): State = {
    val st0 = { ...st00, last_rolled: false }
    if (is_personal(org_id)) { ...st0, maint_skipped_personal: true }
    else {
      val sg = settings_or_default(st0)
      val st = with_settings_created(st0)
      if (not(valid_date(next_cycle_start(sg.cycle_start_at, sg.reset_day))))
        { ...st0, date_crash: true }
      else {
        val r = get_financial_snapshot(st, sg, false, false)
        val st1 = note_read({ ...st, settings: Some(r.settings) }, r)
        match r.snap {
          | None => { ...st1,
              settings: Some({ ...r.settings, sync_status: Some(ERROR) }),
              maint_skipped_unavailable: true }
          | Some(snap) => {
              val rep = if (roll_is_due(r.settings, st.now))
                          repair_missing(st1, r.settings, snap)
                        else { st: st1, sg: r.settings, snap: Some(snap), ran: false }
              match rep.snap {
                | None => { ...rep.st,
                    settings: Some({ ...rep.sg, sync_status: Some(ERROR) }),
                    maint_skipped_repair: true, repair_ran: true, repair_failed: true }
                | Some(sn) => maintenance_tail(rep.st, rep.sg, sn, rep.ran)
              }
            }
        }
      }
    }
  }

  // -- update_budget_settings ---------------------------------------------------------

  pure def upd_enabled(sg: Settings, up: Update): bool =
    match up.enabled { | None => sg.enabled | Some(b) => b }

  pure def upd_limit(sg: Settings, up: Update): Option[int] =
    match up.monthly_limit { | None => sg.monthly_limit | Some(v) => v }

  pure def upd_reset_day(sg: Settings, up: Update): int =
    match up.reset_day { | None => sg.reset_day | Some(v) => v }

  /// The 400: monthly_limit is required, and positive, whenever budgets are on.
  pure def update_is_valid(sg: Settings, up: Update): bool =
    not(upd_enabled(sg, up)) or or_zero(upd_limit(sg, up)) > 0

  pure def apply_update_fields(sg: Settings, up: Update): Settings = { ...sg,
    enabled: upd_enabled(sg, up),
    monthly_limit: upd_limit(sg, up),
    reset_day: upd_reset_day(sg, up),
    default_user_monthly_limit:
      match up.default_user_limit { | None => sg.default_user_monthly_limit | Some(v) => v },
    slack_channel: match up.slack_channel { | None => sg.slack_channel | Some(v) => v },
  }

  /// update_budget_settings. Changing reset_day, or switching budgets on, re-anchors
  /// the cycle and every baseline -- and demands a complete, fresh LiteLLM read to do
  /// it, refusing with 503 rather than anchoring on stale numbers.
  pure def do_update_settings(st0: State, up: Update): State = {
    val sg0 = settings_or_default(st0)
    val st = with_settings_created(st0)
    val sg1 = apply_update_fields(sg0, up)
    val reanchor = up.reset_day != None or (not(sg0.enabled) and sg1.enabled)
    if (reanchor and not(valid_date(current_cycle_start(st.now, sg1.reset_day))))
      { ...st0, date_crash: true }
    else {
      val r = if (reanchor) Some(get_financial_snapshot(st, sg1, false, true)) else None
      val baseline = match r { | None => None | Some(x) => x.snap }
      if (reanchor and baseline == None)
        // 503: the whole PATCH is refused, settings row included.
        { ...st0, enable_needed_fresh_spend: true }
      else {
        val sg2 = match baseline {
          | None => sg1
          | Some(b) => { ...sg1,
              cycle_start_at: current_cycle_start(st.now, sg1.reset_day),
              cycle_start_spend: b.team_spend,
              user_cycle_start_spend: b.members.keys().mapBy(u => b.members.get(u).spend),
              known_member_ids: st.org_members }
        }
        val st1 = match up.thresholds {
          | None => st
          | Some(ps) => { ...st,
              thresholds: ps.mapBy(p => { email: true, slack: false, last_cycle: None }),
              thresholds_replaced: true }
        }
        val st2 = { ...st1, settings: Some(sg2) }
        val sy = sync_litellm_budgets(st2, sg2, sg0.enabled and not(sg2.enabled), baseline)
        val st3 = apply_sync(st2, sy)
        val sg3 = match st3.settings { | None => sg2 | Some(g) => g }
        if (not(valid_date(next_cycle_start(sg3.cycle_start_at, sg3.reset_day))))
          { ...st0, date_crash: true }
        else {
          val shown = match sy.snap {
            | Some(sn) => { snap: Some(sn), status: Live, settings: sg3,
                            parse_failed: false, retried: false,
                            fatal: false, incomplete: false }
            | None => get_financial_snapshot(st3, sg3, true, false)
          }
          val st4 = note_read({ ...st3, settings: Some(shown.settings) }, shown)
          { ...st4,
            last_presented: Some(presented_of(shown, st.now)),
            unmapped_reported: st4.unmapped_reported or
              (match shown.snap
                { | None => false | Some(sn) => unmapped_count(sn, st.org_members) > 0 }) }
        }
      }
    }
  }

  /// The read half of get_budget_state / get_user_budget_row: a stale-tolerant
  /// snapshot handed straight to the caller alongside its status label.
  pure def do_present(st0: State, term: Option[Term], cache_override: Option[bool]): State = {
    val sg0 = settings_or_default(st0)
    val sg = match cache_override {
      | None => sg0
      | Some(present) => cache_for(sg0, st0.now, present)
    }
    val created = st0.settings == None
    val st = { ...st0,
      settings: Some(sg),
      thresholds: if (created) DEFAULT_THRESHOLDS else st0.thresholds,
      settings_created: st0.settings_created or created }
    if (not(valid_date(next_cycle_start(sg.cycle_start_at, sg.reset_day))))
      { ...st0, date_crash: true }
    else {
      val r = get_financial_snapshot(st, sg, true, false)
      val st1 = note_read({ ...st, settings: Some(r.settings) }, r)
      { ...st1,
        last_presented: Some(presented_of(r, st.now)),
        last_search: match term
          { | None => st1.last_search
            | Some(t) => Some({ hits: t.hits, rows: ilike_rows(t).intersect(st.org_members) }) },
        unmapped_reported: st1.unmapped_reported or
          (match r.snap
            { | None => false | Some(sn) => unmapped_count(sn, st.org_members) > 0 }) }
    }
  }

  /// OrgMemberFinancialService.get_org_members_financial_data
  /// (server/services/org_member_financial_service.py:21). Three things happen here
  /// that nothing else in this component does: a LiteLLM read that is not a 401/403 is
  /// swallowed and every member's spend is reported as a plain zero; the email filter
  /// reaches ILIKE with no escaping at all; and get_org_members_paginated returns
  /// (rows, has_more) while the caller binds the flag as `total_count`, so next_page_id
  /// compares an offset against a boolean.
  pure def do_member_financial_page(
    st: State, page: PageId, lim: int, filt: Term, total_count: int
  ): State = {
    val offset = page_offset(page)
    val matched = USER_LIST.select(u =>
      st.org_members.contains(u) and raw_ilike_rows(filt).contains(u))
    val window = page_window(matched, offset, lim)
    val has_more = window.length() > lim
    val shown = window.foldl({ i: 0, out: Set() }, (a, u) =>
      if (a.i < lim) { i: a.i + 1, out: a.out.union(Set(u)) }
      else { i: a.i + 1, out: a.out }).out
    if (shown == Set())
      // the empty-members early return: LiteLLM is never read at all
      { ...st,
        last_search: Some({ hits: filt.hits, rows: Set() }),
        last_page: Some({ has_more: false, next_offered: false }) }
    else if (st.read_fault == FatalError)
      // a 401/403 is deliberately re-raised: the request dies, nothing is reported
      { ...st, financial_auth_raised: true }
    else {
      val zeroed = not(st.read_fault == NoFault or st.read_fault == Malformed)
      { ...st,
        last_search: Some({ hits: filt.hits, rows: shown }),
        // next_page_id compares the offset against whatever the caller bound as
        // `total_count` -- the store's has_more flag in production, a real count only
        // when a collaborator supplies one.
        last_page: Some({ has_more: has_more, next_offered: offset + lim < total_count }),
        financial_zeroed: st.financial_zeroed or zeroed,
        last_presented: Some({ status: Live, observed_key: dkey(st.now),
                               at_key: dkey(st.now), real: not(zeroed) }) }
    }
  }

  /// The service-level override writers both resync LiteLLM straight afterwards.
  pure def resync_after_override(st0: State): State = {
    val sg = settings_or_default(st0)
    val st = with_settings_created(st0)
    apply_sync(st, sync_litellm_budgets(st, sg, false, None))
  }

  // ------------------------------------------- state-conformance fact readers
  //
  // Each is read off the model's POST-state and guarded on equality with the same
  // fact the code logs after the operation, so a model whose writes drift from the
  // code's stops replaying green.

  /// OrgBudgetSettings.enabled after the write.
  pure def enabled_of(st: State): bool =
    match st.settings { | None => false | Some(g) => g.enabled }

  /// The number of OrgUserBudgetOverride rows the org has after the write.
  pure def override_count_of(st: State): int = st.overrides.keys().size()

  /// The `spend_status` the response carries.
  pure def spend_status_of(st: State): str =
    match st.last_presented {
      | None => "none"
      | Some(p) =>
        match p.status { | Live => "live" | Stale => "stale" | Unavailable => "unavailable" }
    }

  /// Whether the member financial page handed back a next_page_id.
  pure def next_offered_of(st: State): bool =
    match st.last_page { | None => false | Some(p) => p.next_offered }

  // ------------------------------------------------------------------- actions

  action init: bool = {
    nondet start = STARTS.oneOf()
    init_from(start)
  }

  action init_from(start: Start): bool = s' = {
    now: START_NOW,
    settings: start_settings(start),
    thresholds: if (start == Fresh) NO_THRESHOLDS else DEFAULT_THRESHOLDS,
    overrides: NO_OVERRIDES,
    org_members: START_MEMBERS,
    litellm: {
      team_spend: 0,
      team_max_budget: None,
      members: START_MEMBERS.mapBy(u => { spend: 0, max_budget: None, uses_shared: true }),
      ll_users: USERS },
    read_fault: NoFault,
    write_mode: WriteOk,

    last_roll: None,
    last_presented: None,
    last_sync: None,
    last_search: None,
    last_page: None,
    financial_zeroed: false,
    financial_auth_raised: false,
    alerts_sent: Set(),
    alert_duplicated: false,
    cap_below_baseline: false,
    settings_created: false,
    personal_settings_created: false,
    date_crash: false,
    retry_recovered: false,
    fatal_no_retry: false,
    parse_rejected: false,
    repair_ran: false,
    repair_failed: false,
    stale_used: false,
    unavailable_used: false,
    unmapped_reported: false,
    sync_skipped: false,
    sync_succeeded: false,
    sync_errored: false,
    member_missing_reported: false,
    baseline_missing_reported: false,
    rolled: false,
    last_rolled: false,
    concurrent_rolled: false,
    maint_skipped_personal: false,
    maint_skipped_unavailable: false,
    maint_skipped_repair: false,
    enable_needed_fresh_spend: false,
    override_delete_noop: false,
    store_write_unsynced: false,
    thresholds_replaced: false,
    alert_sent: false,
    alert_email_sent: false,
    alert_slack_sent: false,
    alert_slack_suppressed: false,
  }

  /// GET /orgs/{org_id}/budgets. Call site: server/routes/orgs.py:1241 ->
  /// OrgBudgetService.get_budget_state (org_budget_service.py:294). Creates the
  /// settings row on first read, and refuses a personal workspace outright.
  /// `spend_status` is the read outcome this call observed, and it is an INPUT rather
  /// than a conformance fact: `live` is the fetch and the parse both succeeding,
  /// `stale` is a read that did not while the row still held a last-known-good copy,
  /// `unavailable` is a read that did not with nothing cached. The model cannot
  /// derive any of that -- the read's outcome and the row's cache are environment
  /// this component neither controls nor logs -- so the call is told which branch it
  /// took, and the properties then judge what it did with it.
  action get_budget_state(org_id: str, users_search: Term, spend_status: str): bool = all {
    not(is_personal(org_id)),
    s' = do_present(
      { ...s, read_fault: if (spend_status == "live") NoFault else FatalError },
      Some(users_search),
      Some(spend_status == "stale")),
  }

  /// The per-user row the override endpoints return. Call site:
  /// server/routes/orgs.py:1305 -> get_user_budget_row (org_budget_service.py:868).
  /// It is the one budget entry point that never calls _reject_personal_org.
  action get_user_budget_row(org_id: str, user_id: str): bool =
    if (is_personal(org_id)) s' = { ...s, personal_settings_created: true }
    else s' = do_present(s, None, None)

  /// The maintenance task's per-org call. Call site:
  /// server/maintenance_task_processor/org_budget_maintenance_processor.py:14 ->
  /// run_budget_maintenance (org_budget_service.py:344).
  /// Conformance fact: `cycle_rolled`, the flag the run reports (org_budget_service.py:431).
  /// `now_at` and `read_fault` are the clock and the LiteLLM read this run observed.
  /// Neither is logged, so replay searches them -- without them the model's clock
  /// never leaves the cycle it started in and no replayed run can ever roll.
  action run_budget_maintenance(
    org_id: str, now_at: Date, read_fault: ReadFault, cycle_rolled: bool
  ): bool = {
    val post = do_run_maintenance({ ...s, now: now_at, read_fault: read_fault }, org_id)
    all {
      cycle_rolled == post.last_rolled,
      s' = post,
    }
  }

  /// PATCH /orgs/{org_id}/budgets. Call site: server/routes/orgs.py:1269 ->
  /// update_budget_settings (org_budget_service.py:435).
  /// Conformance fact: `budget_enabled`, the settings row's flag after the write.
  action update_budget_settings(
    org_id: str, update_data: Update, budget_enabled: bool
  ): bool = all {
    not(is_personal(org_id)),
    update_is_valid(settings_or_default(s), update_data),
    {
      val post = do_update_settings(s, update_data)
      all {
        budget_enabled == enabled_of(post),
        s' = post,
      }
    },
  }

  /// PUT /orgs/{org_id}/budgets/overrides/{user_id}. Call site:
  /// server/routes/orgs.py:1299 -> upsert_user_override (org_budget_service.py:556).
  /// The service itself checks nothing about monthly_limit.
  /// Conformance fact: `override_count`, the org's override rows after the write.
  action upsert_user_override(
    org_id: str, user_id: str, ovr: Override, override_count: int
  ): bool = all {
    not(is_personal(org_id)),
    {
      val post = resync_after_override({ ...s, overrides: s.overrides.put(user_id, ovr) })
      all {
        override_count == override_count_of(post),
        s' = post,
      }
    },
  }

  /// DELETE /orgs/{org_id}/budgets/overrides/{user_id}. Call site:
  /// server/routes/orgs.py:1332 -> delete_user_override (org_budget_service.py:576).
  /// A missing row returns early, so LiteLLM is never resynced.
  /// Conformance fact: `override_count`, the org's override rows after the delete --
  /// unchanged when the row was already gone.
  action delete_user_override(org_id: str, user_id: str, override_count: int): bool = all {
    not(is_personal(org_id)),
    {
      val post =
        if (not(s.overrides.has(user_id))) { ...s, override_delete_noop: true }
        else resync_after_override({ ...s, overrides: s.overrides.mapRemove(user_id) })
      all {
        override_count == override_count_of(post),
        s' = post,
      }
    },
  }

  /// _roll_cycle_if_needed (org_budget_service.py:626) on its own, as
  /// run_budget_maintenance reaches it: a fresh snapshot, then roll or no-op.
  action _roll_cycle_if_needed: bool = all {
    s.settings != None,
    {
      val sg = settings_or_default(s)
      if (not(valid_date(next_cycle_start(sg.cycle_start_at, sg.reset_day))))
        s' = { ...s, date_crash: true }
      else {
        val r = get_financial_snapshot(s, sg, false, false)
        match r.snap {
          | None => s' = note_read(s, r)
          | Some(snap) =>
            if (not(roll_is_due(sg, s.now))) s' = note_read({ ...s, settings: Some(r.settings) }, r)
            else if (not(valid_date(current_cycle_start(s.now, sg.reset_day))))
              s' = { ...s, date_crash: true }
            else {
              val out = roll_now(note_read({ ...s, settings: Some(r.settings) }, r), r.settings, snap)
              s' = out.st
            }
        }
      }
    },
  }

  /// _sync_litellm_budgets (org_budget_service.py:995) on its own. clear_disabled is
  /// what update_budget_settings passes when the org has just been switched off.
  action _sync_litellm_budgets(clear_disabled: bool): bool = all {
    s.settings != None,
    {
      val sg = settings_or_default(s)
      s' = apply_sync(s, sync_litellm_budgets(s, sg, clear_disabled, None))
    },
  }

  /// _maybe_send_alerts / _send_alerts / _send_slack_alert (org_budget_service.py:1153)
  /// on the spend the current cycle has accrued.
  action _maybe_send_alerts: bool = all {
    s.settings != None,
    {
      val sg = settings_or_default(s)
      val r = get_financial_snapshot(s, sg, false, false)
      match r.snap {
        | None => s' = note_read(s, r)
        | Some(snap) =>
          s' = send_alerts(note_read({ ...s, settings: Some(r.settings) }, r),
                           r.settings, cycle_spend(r.settings, snap),
                           dkey(sg.cycle_start_at))
      }
    },
  }

  /// OrgBudgetStore.create_settings (storage/org_budget_store.py:28) reached directly.
  /// reset_day is a plain Integer column with no check constraint, so a day the month
  /// may not have goes straight in -- and org_id is unique, so a second row cannot.
  action OrgBudgetStore_create_settings(reset_day: int): bool = all {
    s.settings == None,
    s' = { ...s,
      settings: Some({ ...default_settings(s.now),
        reset_day: reset_day,
        cycle_start_at: current_cycle_start(s.now, reset_day) }),
      thresholds: DEFAULT_THRESHOLDS,
      settings_created: true },
  }

  /// OrgBudgetStore.replace_thresholds (storage/org_budget_store.py:66): a whole-region
  /// write that deletes every threshold row for the org and inserts the new set. The
  /// deleted rows carry last_triggered_cycle_start, so every alert is re-armed inside
  /// the live cycle.
  action OrgBudgetStore_replace_thresholds(percentages: Set[int]): bool =
    s' = { ...s,
      thresholds: percentages.mapBy(p => { email: true, slack: false, last_cycle: None }),
      thresholds_replaced: true }

  /// OrgBudgetStore.upsert_override (storage/org_budget_store.py:100) reached directly:
  /// the row changes and nothing resyncs LiteLLM, so the proxy keeps the old cap.
  action OrgBudgetStore_upsert_override(user_id: str, ovr: Override): bool =
    s' = { ...s, overrides: s.overrides.put(user_id, ovr), store_write_unsynced: true }

  /// OrgBudgetStore.delete_override (storage/org_budget_store.py:122) reached directly.
  action OrgBudgetStore_delete_override(user_id: str): bool = all {
    s.overrides.has(user_id),
    s' = { ...s, overrides: s.overrides.mapRemove(user_id), store_write_unsynced: true },
  }

  /// GET /orgs/{org_id}/members/financial. Call site: server/routes/orgs.py:1110 ->
  /// OrgMemberFinancialService.get_org_members_financial_data
  /// (server/services/org_member_financial_service.py:21). page_id must parse as a
  /// non-negative integer or the call raises ValueError.
  /// `total_count` is the second element get_org_members_paginated handed back, logged
  /// because the caller cannot derive it. Conformance fact: `has_next`, whether the
  /// page handed back a next_page_id.
  action OrgMemberFinancialService_get_org_members_financial_data(
    org_id: str, page_id: PageId, limit: int, email_filter: Term,
    total_count: int, has_next: bool
  ): bool = all {
    page_id_ok(page_id),
    {
      val post = do_member_financial_page(s, page_id, limit, email_filter, total_count)
      all {
        has_next == next_offered_of(post),
        s' = post,
      }
    },
  }

  // -- adversary surface: sim-side machinery with no code call site ----------------

  /// A second maintenance run that read OrgBudgetSettings before the first committed
  /// its roll. Nothing locks the row and nothing compares-and-sets cycle_start_at, so
  /// it still sees the pre-roll cycle and rolls a second time: baselines re-anchor to
  /// the newer spend and every threshold latch is cleared again.
  action concurrent_maintenance_roll: bool = all {
    s.last_roll != None,
    s.settings != None,
    {
      val sg = settings_or_default(s)
      val stale_start = match s.last_roll {
        | None => sg.cycle_start_at
        | Some(r) => { mi: r.from_key / 32, d: r.from_key % 32 }
      }
      val as_read = { ...sg, cycle_start_at: stale_start }
      val r = get_financial_snapshot(s, sg, false, false)
      match r.snap {
        | None => s' = note_read(s, r)
        | Some(snap) =>
          if (not(roll_is_due(as_read, s.now)) or
              not(valid_date(current_cycle_start(s.now, sg.reset_day))))
            s' = note_read(s, r)
          else {
            val out = roll_now({ ...s, settings: Some(r.settings), concurrent_rolled: true },
                               as_read, snap)
            s' = { ...out.st, settings: Some({ ...match out.st.settings {
                     | None => r.settings | Some(g) => g }, reset_day: sg.reset_day }) }
          }
      }
    },
  }

  /// The clock between invocations. No code call site -- it is what makes a cycle end,
  /// and a stored reset_day of 29-31, reachable. Time only ever moves forward.
  action env_advance_time(nw: Date): bool = all {
    dkey(nw) > dkey(s.now),
    s' = { ...s, now: nw },
  }

  /// The LiteLLM read's next outcome.
  action env_litellm_read_fault(f: ReadFault): bool =
    s' = { ...s, read_fault: f }

  /// The LiteLLM write's next outcome, including the write that reports success and
  /// changes nothing.
  action env_litellm_write_mode(wm: WriteMode): bool =
    s' = { ...s, write_mode: wm }

  /// Spend accruing on the proxy between our reads. litellm-proxy-integration owns
  /// this; org-budgets only ever reads it back.
  action env_litellm_spend(user_id: str, amount: int): bool = all {
    s.litellm.members.has(user_id),
    s' = { ...s, litellm: { ...s.litellm,
      team_spend: s.litellm.team_spend + amount,
      members: s.litellm.members.setBy(user_id, m => { ...m, spend: m.spend + amount }) } },
  }

  /// Org membership changing under us; org-api-surface owns it.
  action env_org_membership(user_id: str, joined: bool): bool =
    s' = { ...s, org_members:
      if (joined) s.org_members.union(Set(user_id)) else s.org_members.exclude(Set(user_id)) }

  /// LiteLLM-side membership drifting from ours: on the team, existing but off the
  /// team (what the pre-roll repair puts back), or gone entirely (what it cannot).
  action env_litellm_membership(user_id: str, on_team: bool, user_exists: bool): bool =
    s' = { ...s, litellm: { ...s.litellm,
      ll_users: if (user_exists or on_team) s.litellm.ll_users.union(Set(user_id))
                else s.litellm.ll_users.exclude(Set(user_id)),
      members: if (on_team)
                 s.litellm.members.put(user_id,
                   if (s.litellm.members.has(user_id)) s.litellm.members.get(user_id)
                   else { spend: 0, max_budget: None, uses_shared: true })
               else s.litellm.members.mapRemove(user_id) } }

  // ---------------------------------------------------------------------- step

  action step: bool = any {
    {
      nondet org_id = ORG_IDS.oneOf()
      nondet users_search = TERMS.oneOf()
      nondet spend_status = Set("live", "stale", "unavailable").oneOf()
      get_budget_state(org_id, users_search, spend_status)
    },
    {
      nondet org_id = ORG_IDS.oneOf()
      nondet user_id = USERS.oneOf()
      get_user_budget_row(org_id, user_id)
    },
    {
      nondet org_id = ORG_IDS.oneOf()
      nondet now_at = clock_choices(s).oneOf()
      nondet read_fault = READ_FAULTS.oneOf()
      nondet cycle_rolled = Set(do_run_maintenance(
        { ...s, now: now_at, read_fault: read_fault }, org_id).last_rolled).oneOf()
      run_budget_maintenance(org_id, now_at, read_fault, cycle_rolled)
    },
    {
      nondet org_id = ORG_IDS.oneOf()
      nondet update_data = UPDATES.oneOf()
      nondet budget_enabled = Set(enabled_of(do_update_settings(s, update_data))).oneOf()
      update_budget_settings(org_id, update_data, budget_enabled)
    },
    {
      nondet org_id = ORG_IDS.oneOf()
      nondet user_id = USERS.oneOf()
      nondet ovr = OVERRIDE_WRITES.oneOf()
      nondet override_count = Set(override_count_of(
        resync_after_override({ ...s, overrides: s.overrides.put(user_id, ovr) }))).oneOf()
      upsert_user_override(org_id, user_id, ovr, override_count)
    },
    {
      nondet org_id = ORG_IDS.oneOf()
      nondet user_id = USERS.oneOf()
      nondet override_count = Set(override_count_of(
        if (not(s.overrides.has(user_id))) s
        else resync_after_override({ ...s, overrides: s.overrides.mapRemove(user_id) }))).oneOf()
      delete_user_override(org_id, user_id, override_count)
    },
    _roll_cycle_if_needed,
    {
      nondet clear_disabled = BOOLS.oneOf()
      _sync_litellm_budgets(clear_disabled)
    },
    _maybe_send_alerts,
    {
      nondet reset_day = STORED_RESET_DAYS.oneOf()
      OrgBudgetStore_create_settings(reset_day)
    },
    {
      nondet percentages = THRESHOLD_SETS.oneOf()
      OrgBudgetStore_replace_thresholds(percentages)
    },
    {
      nondet user_id = USERS.oneOf()
      nondet ovr = OVERRIDE_WRITES.oneOf()
      OrgBudgetStore_upsert_override(user_id, ovr)
    },
    {
      nondet user_id = USERS.oneOf()
      OrgBudgetStore_delete_override(user_id)
    },
    {
      nondet org_id = ORG_IDS.oneOf()
      nondet page_id = PAGE_IDS.oneOf()
      nondet total_count = TOTAL_COUNTS.oneOf()
      nondet has_next = Set(next_offered_of(
        do_member_financial_page(s, page_id, 1, NO_FILTER, total_count))).oneOf()
      OrgMemberFinancialService_get_org_members_financial_data(
        org_id, page_id, 1, NO_FILTER, total_count, has_next)
    },
    {
      nondet org_id = ORG_IDS.oneOf()
      nondet email_filter = TERMS.oneOf()
      nondet total_count2 = TOTAL_COUNTS.oneOf()
      nondet has_next2 = Set(next_offered_of(
        do_member_financial_page(s, NoPage, 10, email_filter, total_count2))).oneOf()
      OrgMemberFinancialService_get_org_members_financial_data(
        org_id, NoPage, 10, email_filter, total_count2, has_next2)
    },
    concurrent_maintenance_roll,
    {
      nondet nw = CLOCK.oneOf()
      env_advance_time(nw)
    },
    {
      nondet f = READ_FAULTS.oneOf()
      env_litellm_read_fault(f)
    },
    {
      nondet wm = WRITE_MODES.oneOf()
      env_litellm_write_mode(wm)
    },
    {
      nondet user_id = USERS.oneOf()
      nondet amount = SPEND_STEPS.oneOf()
      env_litellm_spend(user_id, amount)
    },
    {
      nondet user_id = USERS.oneOf()
      nondet joined = BOOLS.oneOf()
      env_org_membership(user_id, joined)
    },
    {
      nondet user_id = USERS.oneOf()
      nondet on_team = BOOLS.oneOf()
      nondet user_exists = BOOLS.oneOf()
      env_litellm_membership(user_id, on_team, user_exists)
    },
  }

  // ------------------------------------------------------ correctness properties

  /// A committed roll moves the cycle start forward by exactly one reset period.
  /// A roll that jumps two periods at once silently forgives a month of spend --
  /// the baseline is re-anchored to today's total, so everything spent in the
  /// month nobody rolled is never counted against any cap.
  val cycle_advances_by_one_reset_period: bool =
    match s.last_roll {
      | None => true
      | Some(r) =>
        r.to_key > r.from_key and
        r.to_key == dkey(next_cycle_start({ mi: r.from_key / 32, d: r.from_key % 32 },
                                          r.reset_day))
    }

  /// A spend figure labelled `live` came from a read that happened, and happened now.
  /// A cached snapshot relabelled live -- or a zero standing in for a read that
  /// failed -- shows an admin a number the org never spent, and every cap and alert
  /// decision taken from it is short by whatever ran in between.
  val stale_spend_is_never_presented_as_live: bool =
    match s.last_presented {
      | None => true
      | Some(p) => p.status == Live implies (p.real and p.observed_key == p.at_key)
    }

  /// A listing that has more rows to give offers the page id that fetches them.
  /// Without it the remaining members are unreachable through the API: an org large
  /// enough to page can only ever see its first page of spend.
  val paged_listing_offers_the_remaining_rows: bool =
    match s.last_page {
      | None => true
      | Some(p) => p.has_more implies p.next_offered
    }

  /// Each threshold alerts once per cycle. A second alert for the same threshold in
  /// the same cycle pages the same admins again for spend they already acknowledged,
  /// and trains them to ignore the next one.
  val alert_fires_at_most_once_per_cycle: bool = not(s.alert_duplicated)

  /// A budget sync only reports success when the read-back showed the caps it wrote.
  /// A sync that claims success on a write LiteLLM dropped leaves the org running
  /// with no enforced cap while the settings page says the budget is applied.
  val unverified_budget_write_is_reported: bool =
    match s.last_sync {
      | None => true
      | Some(sy) =>
        match sy.readback {
          | None => not(sy.reported_success)
          | Some(rb) =>
            sy.reported_success implies
              not(readback_mismatch(rb, sy.exp_team, sy.exp_members))
        }
    }

  /// No member is ever given a LiteLLM cap below the baseline their cycle started
  /// from. Such a cap is already exceeded the moment it is written, so the member is
  /// locked out of the product for the rest of the month with no spend of their own.
  val member_cap_never_below_cycle_baseline: bool = not(s.cap_below_baseline)

  /// A members search only ever returns members whose details literally contain the
  /// term. A metacharacter that survives _escape_ilike turns one admin's search box
  /// into a pattern that matches the whole org.
  val search_never_matches_beyond_literal_term: bool =
    match s.last_search {
      | None => true
      | Some(t) => t.rows.subseteq(t.hits)
    }

  // ---------------------------------------------------------------- observations

  /// A budget settings row was created on demand by a read or a maintenance run.
  val settings_created_on_demand: bool = s.settings_created

  /// A maintenance run rolled the cycle to a new period.
  val cycle_rolled: bool = s.rolled

  /// A second concurrent maintenance run rolled the same cycle again off a stale read.
  val concurrent_roll_reanchored_cycle: bool = s.concurrent_rolled

  /// A maintenance run returned skipped=personal_org instead of refusing.
  val maintenance_skipped_for_personal_org: bool = s.maint_skipped_personal

  /// A maintenance run stopped at skipped=litellm_spend_unavailable: no fresh spend,
  /// and allow_stale=False, so no roll and no sync.
  val maintenance_skipped_without_fresh_spend: bool = s.maint_skipped_unavailable

  /// A rollover was abandoned because the pre-roll membership repair failed.
  val maintenance_skipped_after_repair_failure: bool = s.maint_skipped_repair

  /// The pre-roll repair put an org member LiteLLM had lost back onto the team.
  val litellm_membership_repaired_before_roll: bool =
    s.repair_ran and not(s.repair_failed)

  /// A retryable LiteLLM read error was recovered inside the three attempts.
  val spend_read_recovered_by_retry: bool = s.retry_recovered

  /// A non-retryable LiteLLM read error was raised on the first attempt.
  val spend_read_failed_without_retry: bool = s.fatal_no_retry

  /// A malformed LiteLLM response was rejected by the parser rather than read as zero.
  val malformed_spend_response_rejected: bool = s.parse_rejected

  /// A caller was served the last-known-good snapshot under allow_stale.
  val stale_snapshot_presented: bool = s.stale_used

  /// A caller was told the spend is unavailable: no live read and no usable cache.
  val spend_unavailable_presented: bool = s.unavailable_used

  /// Spend was attributed to LiteLLM team members this org does not know about.
  val unmapped_litellm_spend_reported: bool = s.unmapped_reported

  /// A sync was recorded 'skipped' because the org's budget is off.
  val budget_sync_skipped_while_disabled: bool = s.sync_skipped

  /// A sync verified every cap it wrote and was recorded 'success'.
  val budget_sync_reported_success: bool = s.sync_succeeded

  /// A sync was recorded 'error'.
  val budget_sync_reported_error: bool = s.sync_errored

  /// A sync found an org member absent from the LiteLLM team.
  val member_missing_from_litellm_reported: bool = s.member_missing_reported

  /// A sync found a governed member with no cycle baseline and skipped their cap.
  val member_cycle_baseline_missing_reported: bool = s.baseline_missing_reported

  /// A threshold alert was handed off for delivery.
  val threshold_alert_sent: bool = s.alert_sent

  /// A threshold alert reached the admin email recipients.
  val threshold_alert_emailed: bool = s.alert_email_sent

  /// A threshold alert reached Slack.
  val threshold_alert_slack_delivered: bool = s.alert_slack_sent

  /// A Slack-enabled threshold fired with no slack_channel set, so nothing was posted.
  val threshold_alert_slack_suppressed: bool = s.alert_slack_suppressed

  /// A stored reset_day named a day the month does not have, so the cycle arithmetic
  /// raised and the whole request died.
  val invalid_reset_day_crashed_cycle_math: bool = s.date_crash

  /// A search term carrying an ILIKE metacharacter was escaped to a literal match.
  val wildcard_search_term_escaped: bool =
    match s.last_search {
      | None => false
      | Some(t) => t.rows != USERS and t.rows.subseteq(t.hits) and t.hits != USERS
    }

  /// get_user_budget_row created budget settings for a personal workspace that every
  /// other entry point refuses.
  val personal_org_settings_created_by_user_row_read: bool = s.personal_settings_created

  /// A delete hit no override row and returned early, leaving LiteLLM unsynced.
  val override_delete_on_missing_row: bool = s.override_delete_noop

  /// An override row was written through the store, so LiteLLM still holds the old cap.
  val override_written_without_litellm_sync: bool = s.store_write_unsynced

  /// The threshold rows were bulk-replaced, dropping every once-per-cycle latch.
  val thresholds_replaced_clearing_alert_latches: bool = s.thresholds_replaced

  /// Enabling a budget was refused with 503 because no complete, fresh LiteLLM read
  /// was available to anchor the cycle on.
  val enable_rejected_without_fresh_spend: bool = s.enable_needed_fresh_spend

  /// The member financial listing swallowed a LiteLLM read failure and reported every
  /// member's spend as zero.
  val member_spend_reported_zero_after_read_failure: bool = s.financial_zeroed

  /// A LiteLLM 401/403 was re-raised out of the member financial listing rather than
  /// being swallowed like every other read error.
  val member_listing_auth_error_propagated: bool = s.financial_auth_raised

  /// A member financial page had more rows behind it than it returned.
  val member_listing_page_had_more_rows: bool =
    match s.last_page { | None => false | Some(p) => p.has_more }
}
