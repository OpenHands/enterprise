// Settings, Secrets & Profiles
//
// Models the per-user settings / secrets / profile surface of the app server:
// openhands/app_server/settings/ (settings_router, llm_profiles, agent_profiles,
// provider_connections), openhands/app_server/secrets/ (secrets_router),
// storage/saas_secrets_store.py, storage/org_secrets_store.py,
// storage/encrypt_utils.py, storage/agent_profile_resolution.py and
// server/routes/org_profiles.py + org_provider_connections.py.
//
// COVERS: the personal LLM-profile machine end to end (save with its
// api_key-preservation branches and its per-user cap, delete with the fallback
// promotion Settings.delete_profile does and LLMProfiles.delete does not,
// activate, rename with the active pointer carried across, and the
// _skip_invalid_profiles / _reconcile_active pair that heals a stored active
// pointer naming nothing); the same machine again at org scope through
// OrgLLMProfileMutator, where the FK cascade repoints
// agent_profiles.*.llm_profile_ref on rename and find_referrers blocks a
// delete; AgentProfiles' id-keyed variant of the same reconcile; the
// write-time fixups a save silently applies (resolve_llm_base_url, the
// verify-and-rotate of a stale managed LiteLLM key with every swallowed-failure
// exit, and sync_active_profile_from_settings rewriting the active profile out
// from under the user); ProviderConnections create/update/delete with the
// in-use guard; the five secret endpoints behind _secrets_write_lock together
// with the load->mutate->store window that lock holds open;
// SaasSecretsStore.load's personal+org-shared merge with _resolve_unique_name's
// suffix dedup and SaasSecretsStore.store's delete-the-region-and-reinsert
// write; OrgSecretsStore's row-level shared secrets; git provider token
// check/store/unset; UserAppSettingsService's partial update where an explicit
// null clears; and decrypt_legacy_kwargs' except/pass.
//
// The known behaviours are MIRRORED, not fixed. invalidate_legacy_secrets_store
// runs on GET /settings, writes Secrets(provider_tokens=...) with an empty
// custom_secrets, and so deletes every personal custom-secret row -- and it
// does it outside _secrets_write_lock, so it can also land inside another
// request's load->store window. SaasSecretsStore.store writes back whatever
// load() returned, and load() returned the org-shared secrets merged in, so a
// personal write copies an org secret into a personal row. store_provider_tokens
// replaces the whole provider_tokens map with the payload's, dropping any
// provider the payload omits. decrypt_legacy_kwargs swallows InvalidToken and
// returns the raw ciphertext, so corrupt ciphertext and legacy plaintext are
// indistinguishable to the caller. save_profile_preserving_identity never checks
// that an Agent Profile's llm_profile_ref names a real LLM profile, and
// OrgLLMProfileMutator.delete is the raw mutator that delete_llm_profile's
// find_referrers guard wraps rather than one behind it, so a reference can be
// left dangling from either end. None of these are corrected here; each is
// stated as a property the simulator can violate.
//
// DOES NOT COVER: the ACP agent-kind branch of sync_active_profile_from_settings
// and ACPAgentProfile generally (this spec's profiles are all openhands-kind);
// marketplace composition beyond the duplicate-name write guard
// (compose_marketplaces' instance/org/user layering changes no secret or
// profile decision); LLMModelService's model/provider catalogue search and
// coerce_persisted_mcp_config / serialize_mcp_config, which are pure shape
// normalizers over data this component only carries; the response shaping on
// GET /settings (the api_key masking, and the base_url the frontend is not
// sent) and the pagination in search_custom_secret, neither of which changes
// stored state; the real JWE/Fernet ciphers (encrypt_value and decrypt_value
// are an injective pair here, so only the corrupt-input path is interesting);
// analytics emission; and RBAC, which rbac-authorization owns -- every action
// here is an already-authorized call. Profile insertion order is abstracted:
// Settings.delete_profile promotes "the first remaining profile", and this
// model promotes an arbitrary but deterministic remaining one, because no
// property here depends on which.
module settings_secrets_and_profiles {
  import basicSpells.* from "./spells/basicSpells"

  // ---------------------------------------------------------------- constants

  /// Identities are canonicalised at the log site onto these names: the spec
  /// picks them from pure vals, which domain tagging cannot widen.
  pure val USERS: Set[str] = Set("u1", "u2", "u3")
  /// A caller id with no User row -- UserNotFoundError.
  pure val ALL_USER_IDS: Set[str] = USERS.union(Set("u-unknown"))

  /// Provenance marker for a value that originated as an org-shared secret.
  pure val ORG: str = "org"

  pure val MANAGED_URL: str = "llm-proxy"
  pure val PROVIDER_DEFAULT_URL: str = "byok-default"
  pure val SELF_HOSTED_URL: str = "self-hosted"

  /// is_openhands_model(): these route to the managed LiteLLM proxy.
  pure val OPENHANDS_MODELS: Set[str] = Set("openhands/gpt")

  pure val NO_KEY: str = ""
  pure val MASKED: str = "**masked**"
  /// The key LiteLlmManager mints on rotation -- distinct from any key a caller
  /// can send, so a rotation is visible in the state.
  pure val FRESH_KEY: str = "k-rotated"
  pure val USER_KEY: str = "k-user"
  /// A second real key, so a profile can hold one the caller does not.
  pure val OTHER_KEY: str = "k-other"
  pure val KEYS: Set[str] = Set(NO_KEY, MASKED, USER_KEY, OTHER_KEY)

  /// Six. The baseline actions take powersets of this set several times over,
  /// so each extra name doubles the work of every simulated step; the tests
  /// that save more profiles than this (the cap tests save fifty) fold onto the
  /// last slot, which costs nothing the per-call conformance facts measure.
  pure val GOOD_PROFILE_NAMES: Set[str] =
    Set("p1", "p2", "p3", "p4", "p5", "p6")
  /// _NAME_PATTERN rejects this at the FastAPI Path layer before the handler
  /// runs. It stays in the domain so that contract stays falsifiable.
  pure val PROFILE_NAMES: Set[str] = GOOD_PROFILE_NAMES.union(Set("p/1"))

  pure val AGENT_IDS: Set[str] = Set("a1", "a2", "a3", "a4")
  pure val AGENT_NAMES: Set[str] = Set("ap1", "ap2", "ap3", "ap4")
  pure val AGENT_NAME_OF: str -> str =
    Map("a1" -> "ap1", "a2" -> "ap2", "a3" -> "ap3", "a4" -> "ap4")
  pure val CONN_IDS: Set[str] = Set("c1", "c2", "c3", "c4")

  pure val CODEX: str = "CODEX_AUTH_JSON"
  pure val BASE_SECRET_NAMES: Set[str] =
    Set("s1", "s2", "s3", "s4", "s5", "s6", CODEX)
  /// _resolve_unique_name appends _2 when an org-shared name collides with a
  /// personal one. Quint has no string concatenation, so the suffixed forms are
  /// a lookup table; a name the table does not carry keeps its own name, since
  /// the suffix RULE is what the tabulated entries model.
  pure val SUFFIXED: str -> str =
    Map("s1" -> "s1_2", "s2" -> "s2_2", "s3" -> "s3_2", "s4" -> "s4_2",
        "s5" -> "s5_2", "s6" -> "s6_2", "CODEX_AUTH_JSON" -> "CODEX_AUTH_JSON_2")
  pure val SECRET_NAMES: Set[str] = BASE_SECRET_NAMES.union(SUFFIXED.values())

  pure val PROVIDERS: Set[str] = Set("github", "gitlab")
  pure val HOSTS: Set[str] = Set("h1", "h2")
  pure val TOKENS: Set[str] = Set(NO_KEY, "t-github", "t-gitlab")
  /// validate_provider_token maps a token to the provider it really belongs to;
  /// a token that types as the wrong provider fails _check_token_type. An
  /// untabulated token is accepted for whichever provider it is stored under.
  pure val TOKEN_PROVIDER: str -> str =
    Map("t-github" -> "github", "t-gitlab" -> "gitlab")
  /// The inverse: the handle a provider's own token carries.
  pure val TOKEN_OF: str -> str =
    Map("github" -> "t-github", "gitlab" -> "t-gitlab")

  /// The org-scope caps DO gate their actions, so they are sized to what the
  /// suite actually creates rather than to what simulation can reach.
  pure val MAX_ORG_PROFILES: int = 10
  pure val MAX_AGENT_PROFILES: int = 4
  pure val MAX_CONNECTIONS: int = 4

  /// UserAppSettingsUpdate.language -- the nullable field standing in for the
  /// partial-update contract.
  pure val LANGUAGES: Set[str] = Set("en", "es", "fr", "ja")

  /// Names a user's own marketplace edit may not collide with.
  pure val INHERITED_MARKETPLACES: Set[str] = Set("m-org")

  // ------------------------------------------------------------------- types

  /// base_url as it arrives and as it is stored. BCleared is the UI's explicit
  /// empty string, which resolve_llm_base_url treats as "clear", never as
  /// "infer a default".
  type BaseUrl = BUnset | BCleared | BAt(str)

  type LlmCfg = {
    model: str,
    base_url: BaseUrl,
    key: str,
    // provider_connection_id
    conn: Option[str],
  }

  /// LLMProfiles / the org's llm_profiles column. ``broken`` are the entries
  /// whose persisted JSON no longer validates; _skip_invalid_profiles drops
  /// them at load, before _reconcile_active looks at ``active``.
  type Profiles = {
    cfgs: str -> LlmCfg,
    active: Option[str],
    broken: Set[str],
  }

  /// AgentProfiles: id-keyed, with the user-facing name inside the record and
  /// an llm_profile_ref FK into the LLM profiles.
  type AgentProf = { name: str, llm_ref: Option[str] }
  type AgentProfiles = { byId: str -> AgentProf, active: Option[str], broken: Set[str] }

  type Conn = { key: str, base_url: BaseUrl }

  /// A stored secret. ``owner`` is who stored the value -- a user id, or ORG
  /// for an org-shared one. Which ROW it lands in is a separate question, and
  /// the gap between the two is what no_foreign_secret_in_personal_store rules
  /// out. ``corrupt`` marks ciphertext this JWE key can no longer open.
  type Secret = { owner: str, corrupt: bool }

  type Token = { token: str, host: str }

  type Store = { custom: str -> Secret, tokens: str -> Token }

  type UserSettings = {
    present: bool,
    agent_llm: LlmCfg,
    profiles: Profiles,
    title_profile: Option[str],
    // settings.secrets_store.provider_tokens -- the legacy field
    // invalidate_legacy_secrets_store migrates out on GET /settings.
    legacy_tokens: str -> Token,
    marketplaces: Set[str],
  }

  /// The User row fields UserAppSettingsService writes. An omitted field is
  /// untouched; an explicitly null one is cleared.
  type AppSettings = { language: Option[str], sound: Option[bool] }

  /// One secret endpoint's load->mutate->store window, held open. Only one can
  /// be open at a time: that IS _secrets_write_lock.
  type Rmw = { user: str, baseline: Store, add: str }

  /// POST /api/v1/settings bodies, named rather than picked as records: a name
  /// is a plain string the instrumentation can log, so replay pins the pick
  /// instead of searching the family space.
  type SettingsPayload = {
    // a legacy nested key -- 422 before anything is loaded
    legacy_key: bool,
    // user_consents_to_analytics; anything but Some(true) is 422 in SaaS
    consent: Option[bool],
    // a registered_marketplaces edit; duplicate_marketplace_names rejects a
    // name already inherited
    marketplace: Option[str],
    // The agent_settings_diff's llm, when the payload carries one at all.
    // None is a save that touches something else and leaves the stored LLM
    // alone -- the deep merge the endpoint documents, which a model that
    // always overwrote the LLM got wrong on every non-LLM save.
    llm: Option[LlmCfg],
    // title_llm_profile: the profile used for conversation titling
    title: Option[str],
  }

  /// SaveProfileRequest's body. Its two booleans are logged separately, so a
  /// family names only the llm it carries.
  type SaveReq = { llm: Option[LlmCfg] }

  // ------------------------------------------------------------------- state

  type State = {
    settings: str -> UserSettings,
    // the StoredCustomSecrets rows with is_org_shared=false, per user
    personal: str -> Store,
    // the rows with is_org_shared=true; one org in this model
    org_shared: str -> Secret,
    org_llm: Profiles,
    org_agents: AgentProfiles,
    connections: str -> Conn,
    app: str -> AppSettings,
    // providers whose incoming token check_provider_tokens has just cleared
    checked: Set[str],
    inflight: Option[Rmw],

    // -- lineage ledgers: what was committed, and what was removed on purpose.
    // A name in stored_ever minus removed_on_purpose that is no longer in the
    // store was lost silently. Recorded when the write happens, compared
    // against a fresh read, so the two sides are computed independently.
    stored_ever: str -> Set[str],
    tokens_ever: str -> Set[str],
    removed_on_purpose: str -> Set[str],

    // -- ghost latches for behaviours that leave no other trace
    active_dropped_with_profiles_left: bool,
    ciphertext_served_as_plaintext: bool,
    legacy_migrated: bool,
    org_secret_copied_to_personal: bool,
    suffix_dedup_applied: bool,
    key_rotated: bool,
    rotation_skipped_after_verify_error: bool,
    rotation_reported_not_applied: bool,
    sync_rewrote_active_profile: bool,
    base_url_inferred: bool,
    base_url_cleared: bool,
    invalid_profile_dropped: bool,
    orphan_active_reconciled: bool,
    fallback_profile_promoted: bool,
    active_cleared_no_fallback: bool,
    rename_kept_active: bool,
    cascade_repointed_ref: bool,
    conn_delete_blocked_by_referrer: bool,
    token_host_revalidated: bool,
    tokens_dropped_by_partial_payload: bool,
    secret_renamed: bool,
    app_setting_cleared: bool,
    unlocked_write_overwritten: bool,
    managed_profile_key_overridden: bool,
    profile_cap_blocked_save: bool,
    profile_read_withheld_stored_key: bool,
  }

  var s: State

  // --------------------------------------------------------- pure: llm config

  pure def blank_llm(m: str): LlmCfg = { model: m, base_url: BUnset, key: NO_KEY, conn: None }

  pure def has_real_key(k: str): bool = k != NO_KEY

  /// resolve_llm_base_url: an explicit empty string clears, a set value is
  /// kept, an unset one infers the managed proxy for an OpenHands model and the
  /// provider default otherwise.
  pure def resolve_base_url(model: str, bu: BaseUrl): BaseUrl =
    match bu {
      | BCleared => BUnset
      | BAt(u) => BAt(u)
      | BUnset =>
          if (OPENHANDS_MODELS.contains(model)) BAt(MANAGED_URL) else BAt(PROVIDER_DEFAULT_URL)
    }

  pure def resolved(c: LlmCfg): LlmCfg = { ...c, base_url: resolve_base_url(c.model, c.base_url) }

  pure def routes_managed(c: LlmCfg): bool =
    resolve_base_url(c.model, c.base_url) == BAt(MANAGED_URL)

  /// _maybe_rotate_stale_managed_key. Every exit that is not "rotated" returns
  /// the config unchanged, including the two that swallow an exception.
  pure def rotate(c: LlmCfg, verdict: str): { cfg: LlmCfg, outcome: str } =
    if (not(has_real_key(c.key)) or c.key == MASKED or not(routes_managed(c)))
      { cfg: c, outcome: "skipped" }
    else if (verdict == "stale")
      { cfg: { ...c, key: FRESH_KEY }, outcome: "rotated" }
    else
      { cfg: c, outcome: verdict }

  /// resolve_profile_llm: a profile pointed at the managed proxy has its stored
  /// key OVERRIDDEN by the caller's effective key, not merely filled in,
  /// because a managed virtual key is rotated out from under the profile.
  pure def resolve_profile_llm(c: LlmCfg, fallback: str): LlmCfg = {
    val r = resolved(c)
    if (has_real_key(fallback) and (routes_managed(c) or not(has_real_key(r.key))))
      { ...r, key: fallback }
    else r
  }

  // ------------------------------------------------------- pure: profile box

  pure val empty_profiles: Profiles = { cfgs: Map(), active: None, broken: Set() }

  pure def profile_count(p: Profiles): int = p.cfgs.keys().size()

  pure def org_save_blocked_by_cap(p: Profiles, name: str): bool =
    not(p.cfgs.keys().contains(name)) and profile_count(p) >= MAX_ORG_PROFILES

  pure def profiles_save(p: Profiles, name: str, c: LlmCfg, include_secrets: bool): Profiles =
    { ...p, cfgs: p.cfgs.put(name, if (include_secrets) c else { ...c, key: NO_KEY }) }

  /// The LLM a save_profile call stores: request.llm or a snapshot of the
  /// active settings LLM, with the omitted-key and preserve-key branches, the
  /// base_url fixup, the managed-key rotation and include_secrets applied in
  /// the order the handler applies them.
  pure def saved_profile_cfg(
    us: UserSettings, name: str, r: SaveReq, include_secrets: bool, preserve_key: bool
  ): LlmCfg = {
    val existing = us.profiles.cfgs
    val had = existing.keys().contains(name)
    val base = match r.llm { | Some(c) => c | None => us.agent_llm }
    val filled =
      if (match r.llm { | Some(c) => c.key == NO_KEY | None => false }
          and had and existing.get(name).key != NO_KEY)
        { ...base, key: existing.get(name).key }
      else base
    val preserved =
      if (preserve_key and had) { ...filled, key: existing.get(name).key }
      else filled
    val rot = rotate(resolved(preserved), "valid")
    if (include_secrets) rot.cfg else { ...rot.cfg, key: NO_KEY }
  }

  /// LLMProfiles.delete: clears ``active`` when it named this profile. No
  /// fallback is promoted -- that is Settings.delete_profile's doing, one level
  /// up, and the org path never gets it.
  pure def profiles_delete(p: Profiles, name: str): Profiles =
    { cfgs: p.cfgs.mapRemove(name),
      active: if (p.active == Some(name)) None else p.active,
      broken: p.broken.exclude(Set(name)) }

  /// LLMProfiles.rename: keeps the slot and carries ``active`` across, having
  /// captured was_active before the dict is rebuilt.
  pure def profiles_rename(p: Profiles, old_name: str, new_name: str): Profiles =
    if (old_name == new_name) p
    else
      { cfgs: p.cfgs.mapRemove(old_name).put(new_name, p.cfgs.get(old_name)),
        active: if (p.active == Some(old_name)) Some(new_name) else p.active,
        broken: if (p.broken.contains(old_name))
                  p.broken.exclude(Set(old_name)).union(Set(new_name))
                else p.broken }

  /// The active profile's name, or "" for none -- the shape the code logs as a
  /// conformance fact after every profile write.
  pure def active_name_of(p: Profiles): str =
    match p.active { | None => "" | Some(n) => n }

  /// Total: `step` computes a conformance fact before the action's guard has
  /// ruled the inputs out, so these are defined for a missing name too.
  pure def active_name_after_rename(p: Profiles, old_name: str, new_name: str): str =
    if (p.cfgs.keys().contains(old_name)) active_name_of(profiles_rename(p, old_name, new_name))
    else active_name_of(p)

  /// An arbitrary but deterministic surviving profile. The code takes the first
  /// in insertion order; no property here depends on which.
  pure def some_profile(p: Profiles): Option[str] =
    p.cfgs.keys().fold(None, (acc, k) => match acc { | None => Some(k) | Some(x) => Some(x) })

  /// _skip_invalid_profiles then _reconcile_active. The field validator runs
  /// first, so reconcile sees the pruned map -- an ``active`` that pointed at a
  /// dropped profile is nulled rather than left dangling.
  pure def profiles_load(p: Profiles): Profiles = {
    val live = p.cfgs.mapRemoveAll(p.broken)
    { cfgs: live,
      active: match p.active {
        | None => None
        | Some(n) => if (live.keys().contains(n)) Some(n) else None
      },
      broken: Set() }
  }

  pure def reconcile_drops_active(p: Profiles): bool =
    match p.active {
      | None => false
      | Some(n) => not(p.cfgs.mapRemoveAll(p.broken).keys().contains(n))
    }

  // -------------------------------------------------- pure: agent profile box

  pure val empty_agents: AgentProfiles = { byId: Map(), active: None, broken: Set() }

  pure def agent_id_for_name(a: AgentProfiles, name: str): Option[str] =
    a.byId.keys().fold(None, (acc, pid) =>
      match acc {
        | Some(x) => Some(x)
        | None => if (a.byId.get(pid).name == name) Some(pid) else None
      })

  /// AgentProfiles._reconcile_active, over the id-keyed collection.
  pure def agents_load(a: AgentProfiles): AgentProfiles = {
    val live = a.byId.mapRemoveAll(a.broken)
    { byId: live,
      active: match a.active {
        | None => None
        | Some(pid) => if (live.keys().contains(pid)) Some(pid) else None
      },
      broken: Set() }
  }

  /// find_referrers: the Agent Profiles whose llm_profile_ref names this LLM
  /// profile. delete_llm_profile raises ProfileReferenced when it is non-empty.
  pure def llm_referrers(a: AgentProfiles, name: str): Set[str] =
    a.byId.keys().filter(pid => a.byId.get(pid).llm_ref == Some(name))

  /// cascade_rename: repoint every referring Agent Profile at the new name.
  pure def cascade_rename(a: AgentProfiles, old_name: str, new_name: str): AgentProfiles = {
    val refs = llm_referrers(a, old_name)
    { ...a,
      byId: refs.fold(a.byId, (m, pid) =>
        m.put(pid, { ...m.get(pid), llm_ref: Some(new_name) })) }
  }

  // ------------------------------------------------------------ pure: secrets

  pure val empty_store: Store = { custom: Map(), tokens: Map() }

  /// _resolve_unique_name: a shared name already taken by a personal one is
  /// surfaced under its _2 form. The stored row keeps the bare name.
  pure def effective_shared_name(name: str, taken: Set[str]): str =
    if (taken.contains(name)) SUFFIXED.getOrElse(name, name) else name

  /// SaasSecretsStore.load: personal rows first (they win the bare name), then
  /// the org's shared rows, suffixed on collision. The result is one flat
  /// Secrets object in which a shared secret is indistinguishable from a
  /// personal one -- which is why storing it back writes it as personal.
  pure def secrets_load(st: State, u: str): Store = {
    val mine = st.personal.get(u)
    val merged = st.org_shared.keys().fold(mine.custom, (acc, n) =>
      acc.put(effective_shared_name(n, mine.custom.keys()), st.org_shared.get(n)))
    { custom: merged, tokens: mine.tokens }
  }

  /// SaasSecretsStore.load_personal (storage/saas_secrets_store.py:106): the
  /// user's own rows only -- no org merge, no suffix dedup. EVERY write endpoint
  /// loads through this, and that is exactly what stops a personal write from
  /// re-creating an org-shared secret as a personal row of its own. The merged
  /// load() above is the RUNTIME read (and the listing API); it never feeds a
  /// store().
  pure def secrets_load_personal(st: State, u: str): Store = st.personal.get(u)

  pure def load_applies_suffix(st: State, u: str): bool =
    st.org_shared.keys().exists(n => st.personal.get(u).custom.keys().contains(n))

  /// A write-back carries a value the user did not store.
  pure def write_copies_org_secret(u: str, w: Store): bool =
    w.custom.keys().exists(n => w.custom.get(n).owner != u)

  /// SaasSecretsStore.store: deletes every personal, non-shared row for
  /// (user, org) and re-inserts the whole payload as personal rows. Nothing
  /// checks where a value came from, so a merged-in org secret lands in a
  /// personal row here.
  pure def secrets_store_write(st: State, u: str, w: Store): State =
    { ...st,
      personal: st.personal.put(u, w),
      stored_ever: st.stored_ever.put(u, st.stored_ever.get(u).union(w.custom.keys())),
      tokens_ever: st.tokens_ever.put(u, st.tokens_ever.get(u).union(w.tokens.keys())),
      org_secret_copied_to_personal:
        st.org_secret_copied_to_personal or write_copies_org_secret(u, w) }

  pure def mark_removed(st: State, u: str, names: Set[str]): State =
    { ...st,
      removed_on_purpose:
        st.removed_on_purpose.put(u, st.removed_on_purpose.get(u).union(names)) }

  /// is_valid_codex_auth: the CODEX_AUTH_JSON slot only accepts ChatGPT-mode
  /// auth json. Any other value is a 400.
  pure def codex_value_ok(name: str, value: str): bool =
    name != CODEX or value == "codex-chatgpt"

  // ---------------------------------------------------------- pure: settings

  pure val default_user_settings: UserSettings = {
    present: true,
    agent_llm: blank_llm("openhands/gpt"),
    profiles: empty_profiles,
    title_profile: None,
    legacy_tokens: Map(),
    marketplaces: Set(),
  }

  /// sync_active_profile_from_settings: if the active profile's config has
  /// drifted from the effective settings, the SETTINGS win and the stored
  /// profile is silently rewritten.
  /// The settings LLM a store_settings write lands on: the payload merged in,
  /// resolve_llm_base_url applied, then the managed-key verify-and-rotate.
  pure def store_settings_llm(us: UserSettings, p: SettingsPayload, verdict: str):
      { cfg: LlmCfg, outcome: str } = {
    val merged =
      match p.llm {
        | None => us.agent_llm
        | Some(c) => { ...us.agent_llm, model: c.model, base_url: c.base_url, key: c.key }
      }
    rotate({ ...merged, base_url: resolve_base_url(merged.model, merged.base_url) }, verdict)
  }

  /// Total: see active_name_after_rename.
  pure def key_set_after_activate(us: UserSettings, name: str, verdict: str): bool =
    if (not(us.profiles.cfgs.keys().contains(name))) has_real_key(us.agent_llm.key)
    else {
      val switched = switch_to(us, name)
      val fixed = { ...switched.agent_llm,
                    base_url: resolve_base_url(switched.agent_llm.model,
                                               switched.agent_llm.base_url) }
      has_real_key(rotate(fixed, verdict).cfg.key)
    }

  pure def sync_active(us: UserSettings): UserSettings =
    match us.profiles.active {
      | None => us
      | Some(n) =>
          if (not(us.profiles.cfgs.keys().contains(n)))
            { ...us, profiles: { ...us.profiles, active: None } }
          else if (us.profiles.cfgs.get(n) != us.agent_llm)
            { ...us, profiles: { ...us.profiles, cfgs: us.profiles.cfgs.put(n, us.agent_llm) } }
          else us
    }

  pure def sync_rewrites(us: UserSettings): bool =
    match us.profiles.active {
      | None => false
      | Some(n) =>
          us.profiles.cfgs.keys().contains(n) and us.profiles.cfgs.get(n) != us.agent_llm
    }

  /// Settings.switch_to_profile: copies the profile into agent_settings.llm so
  /// later fixups do not bleed back, and points ``active`` at it.
  pure def switch_to(us: UserSettings, name: str): UserSettings =
    { ...us,
      agent_llm: us.profiles.cfgs.get(name),
      profiles: { ...us.profiles, active: Some(name) } }

  /// Settings.delete_profile: unlike LLMProfiles.delete it promotes a surviving
  /// profile when the deleted one was active.
  pure def settings_delete_profile(us: UserSettings, name: str): UserSettings = {
    val was_active = us.profiles.active == Some(name)
    val p2 = profiles_delete(us.profiles, name)
    val us2 = { ...us,
                profiles: p2,
                title_profile: if (us.title_profile == Some(name)) None else us.title_profile }
    if (was_active and profile_count(p2) > 0)
      match some_profile(p2) { | None => us2 | Some(f) => switch_to(us2, f) }
    else us2
  }

  // ------------------------------------------------------------------- init

  /// Three starting shapes, all states the persisted rows really reach: a pair
  /// of fresh users (u2 and u3 have never saved, so their settings row is
  /// absent and the profile endpoints 404); a user still carrying a legacy
  /// provider token in settings.secrets_store next to a real custom secret,
  /// which is what invalidate_legacy_secrets_store was written for; and a
  /// returning user who already has two saved profiles with one active, a
  /// connected git provider, and an org connection a profile links to.
  pure val STARTS: Set[str] = Set("fresh", "legacy", "seeded")

  pure val SEEDED_P1: LlmCfg =
    { model: "openhands/gpt", base_url: BAt(MANAGED_URL), key: USER_KEY, conn: None }
  pure val SEEDED_P2: LlmCfg =
    { model: "byok/gpt", base_url: BAt(SELF_HOSTED_URL), key: OTHER_KEY, conn: None }

  pure def initial(start: str): State = {
    val legacy = start == "legacy"
    val seeded = start == "seeded"
    {
      settings: USERS.mapBy(u =>
        if (seeded and u == "u2")
          { ...default_user_settings,
            legacy_tokens: Map("gitlab" -> { token: "t-gitlab", host: "h1" }) }
        else if (legacy)
          { ...default_user_settings,
            legacy_tokens: Map("github" -> { token: "t-github", host: "h1" }) }
        else if (seeded and u == "u1")
          { ...default_user_settings,
            agent_llm: SEEDED_P1,
            profiles: { cfgs: Map("p1" -> SEEDED_P1, "p2" -> SEEDED_P2),
                        active: Some("p1"), broken: Set() } }
        // The common shape: one profile, and it is the active one. Deleting it
        // is the only way to legitimately end up with no active profile.
        else if (seeded and u == "u3")
          { ...default_user_settings,
            agent_llm: SEEDED_P2,
            profiles: { cfgs: Map("p3" -> SEEDED_P2), active: Some("p3"), broken: Set() } }
        // Outside the seeded start u2 has never saved settings: load() returns
        // None, which is the 404 the profile endpoints answer with.
        else if (u == "u2" and not(seeded)) { ...default_user_settings, present: false }
        else default_user_settings),
      personal: USERS.mapBy(u =>
        if (legacy)
          { custom: Map("s1" -> { owner: u, corrupt: false }), tokens: Map() }
        else if (seeded and u == "u1")
          { custom: Map(), tokens: Map("github" -> { token: "t-github", host: "h1" }) }
        else empty_store),
      org_shared: Map(),
      org_llm:
        if (seeded)
          { cfgs: Map("p1" -> { model: "byok/gpt", base_url: BAt(SELF_HOSTED_URL),
                                key: USER_KEY, conn: Some("c1") }),
            active: Some("p1"), broken: Set() }
        else empty_profiles,
      org_agents:
        // An Agent Profile pointing at the org's LLM profile -- the reference
        // the rename cascade and the delete guard both exist for.
        if (seeded)
          { byId: Map("a1" -> { name: "ap1", llm_ref: Some("p1") }),
            active: Some("a1"), broken: Set() }
        else empty_agents,
      connections:
        if (seeded) Map("c1" -> { key: USER_KEY, base_url: BAt(SELF_HOSTED_URL) })
        else Map(),
      app: USERS.mapBy(u =>
        if (u == "u1") { language: Some("en"), sound: None }
        else { language: None, sound: None }),
      checked: Set(),
      inflight: None,
      stored_ever: USERS.mapBy(_ => if (legacy) Set("s1") else Set()),
      tokens_ever: USERS.mapBy(u => if (seeded and u == "u1") Set("github") else Set()),
      removed_on_purpose: USERS.mapBy(_ => Set()),
      active_dropped_with_profiles_left: false,
      ciphertext_served_as_plaintext: false,
      legacy_migrated: false,
      org_secret_copied_to_personal: false,
      suffix_dedup_applied: false,
      key_rotated: false,
      rotation_skipped_after_verify_error: false,
      rotation_reported_not_applied: false,
      sync_rewrote_active_profile: false,
      base_url_inferred: false,
      base_url_cleared: false,
      invalid_profile_dropped: false,
      orphan_active_reconciled: false,
      fallback_profile_promoted: false,
      active_cleared_no_fallback: false,
      rename_kept_active: false,
      cascade_repointed_ref: false,
      conn_delete_blocked_by_referrer: false,
      token_host_revalidated: false,
      tokens_dropped_by_partial_payload: false,
      secret_renamed: false,
      app_setting_cleared: false,
      unlocked_write_overwritten: false,
      managed_profile_key_overridden: false,
      profile_cap_blocked_save: false,
      profile_read_withheld_stored_key: false,
    }
  }

  action init: bool = {
    nondet start = STARTS.oneOf()
    init_from(start)
  }

  action init_from(start: str): bool = s' = initial(start)

  // ----------------------------------------------------------------- domains

  /// POST /api/v1/settings bodies, named rather than picked as records: a name
  /// is a plain string the instrumentation can log, so replay pins the pick
  /// instead of searching the family space. "noop" is a save that carries no
  /// agent_settings_diff.llm at all, which is most of them.
  pure val PAYLOAD_BY_KEY: str -> SettingsPayload = Map(
    "noop" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: None, title: None },
    "llm_default" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: Some({ model: "openhands/gpt", base_url: BUnset, key: NO_KEY, conn: None }), title: None },
    "llm_byok_default" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: Some({ model: "byok/gpt", base_url: BUnset, key: NO_KEY, conn: None }), title: None },
    "llm_byok_base_url" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: Some({ model: "byok/gpt", base_url: BAt(SELF_HOSTED_URL), key: NO_KEY, conn: None }), title: None },
    "llm_base_url_cleared" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: Some({ model: "openhands/gpt", base_url: BCleared, key: NO_KEY, conn: None }), title: None },
    "llm_with_key" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: Some({ model: "openhands/gpt", base_url: BUnset, key: USER_KEY, conn: None }), title: None },
    "llm_with_other_key" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: Some({ model: "openhands/gpt", base_url: BUnset, key: OTHER_KEY, conn: None }), title: None },
    "llm_byok_with_key" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: Some({ model: "byok/gpt", base_url: BAt(SELF_HOSTED_URL), key: USER_KEY, conn: None }), title: None },
    "consent_true" ->
      { legacy_key: false, consent: Some(true), marketplace: None,
        llm: None, title: None },
    "consent_false" ->
      { legacy_key: false, consent: Some(false), marketplace: None,
        llm: None, title: None },
    "marketplace_personal" ->
      { legacy_key: false, consent: None, marketplace: Some("m-mine"),
        llm: None, title: None },
    "marketplace_inherited_clash" ->
      { legacy_key: false, consent: None, marketplace: Some("m-org"),
        llm: None, title: None },
    "legacy_nested_keys" ->
      { legacy_key: true, consent: None, marketplace: None,
        llm: None, title: None },
    "title_p1" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: None, title: Some("p1") },
    "title_p2" ->
      { legacy_key: false, consent: None, marketplace: None,
        llm: None, title: Some("p2") }
  )
  pure val PAYLOAD_KEYS: Set[str] = PAYLOAD_BY_KEY.keys()

  /// SaveProfileRequest families, named for the same reason.
  pure val SAVE_REQ_BY_KEY: str -> SaveReq = Map(
    "snapshot" -> { llm: None },
    "explicit_openhands_with_key" ->
      { llm: Some({ model: "openhands/gpt", base_url: BUnset, key: USER_KEY, conn: None }) },
    "explicit_openhands_without_key" ->
      { llm: Some({ model: "openhands/gpt", base_url: BUnset, key: NO_KEY, conn: None }) },
    "explicit_openhands_other_key" ->
      { llm: Some({ model: "openhands/gpt", base_url: BUnset, key: OTHER_KEY, conn: None }) },
    "explicit_byok_with_key" ->
      { llm: Some({ model: "byok/gpt", base_url: BUnset, key: USER_KEY, conn: None }) },
    "explicit_byok_without_key" ->
      { llm: Some({ model: "byok/gpt", base_url: BUnset, key: NO_KEY, conn: None }) },
    "explicit_byok_connection" ->
      { llm: Some({ model: "byok/gpt", base_url: BAt(SELF_HOSTED_URL), key: USER_KEY,
                    conn: Some("c1") }) }
  )
  pure val SAVE_REQ_KEYS: Set[str] = SAVE_REQ_BY_KEY.keys()

  /// The LLM configs the org profile routes are called with.
  pure val ORG_CFG_BY_KEY: str -> LlmCfg = Map(
    "managed_default" -> blank_llm("openhands/gpt"),
    "byok_connection" ->
      { model: "byok/gpt", base_url: BAt(SELF_HOSTED_URL), key: USER_KEY, conn: Some("c1") }
  )
  pure val ORG_CFG_KEYS: Set[str] = ORG_CFG_BY_KEY.keys()

  /// The outcomes _maybe_rotate_stale_managed_key can take once it decides the
  /// key is worth checking.
  pure val VERDICTS: Set[str] =
    Set("skipped", "valid", "stale", "verify_error", "rotate_error", "not_applied")

  // ------------------------------------------------------------ the actions
  //
  // ``inflight`` is _secrets_write_lock (secrets_router.py:101) and nothing
  // else. The profile endpoints take _user_profile_locks
  // (settings_router.py:517), a different mutex on a different dict, so a
  // profile write and a secret write never wait on each other -- and
  // invalidate_legacy_secrets_store, which writes BOTH, is serialized by
  // neither.

  /// POST /api/v1/settings. No ``present`` guard: the handler creates the row
  /// on first write.
  action store_settings(u: str, payload: str, verdict: str, agent_key_set: bool): bool = all {
    PAYLOAD_KEYS.contains(payload),
    // Conformance: whether the stored settings LLM carries a real key once the
    // fixups and the managed-key rotation have run.
    agent_key_set ==
      has_real_key(store_settings_llm(s.settings.get(u), PAYLOAD_BY_KEY.get(payload), verdict).cfg.key),
    // A legacy nested key is 422'd before anything is read.
    not(PAYLOAD_BY_KEY.get(payload).legacy_key),
    // SaaS refuses any analytics-consent value other than true.
    PAYLOAD_BY_KEY.get(payload).consent != Some(false),
    // duplicate_marketplace_names: a personal name may not collide with an
    // inherited one.
    match PAYLOAD_BY_KEY.get(payload).marketplace {
      | None => true
      | Some(m) => not(INHERITED_MARKETPLACES.contains(m))
    },
    s' = {
      val p = PAYLOAD_BY_KEY.get(payload)
      val us = { ...s.settings.get(u), present: true }
      val merged =
        match p.llm {
          | None => us.agent_llm
          | Some(c) => { ...us.agent_llm, model: c.model, base_url: c.base_url, key: c.key }
        }
      val rot = store_settings_llm(us, p, verdict)
      val us2 = { ...us,
                  agent_llm: rot.cfg,
                  title_profile: match p.title { | None => us.title_profile | Some(t) => Some(t) },
                  marketplaces: match p.marketplace {
                    | None => us.marketplaces
                    | Some(m) => us.marketplaces.union(Set(m))
                  } }
      { ...s,
        settings: s.settings.put(u, sync_active(us2)),
        key_rotated: s.key_rotated or rot.outcome == "rotated",
        rotation_skipped_after_verify_error:
          s.rotation_skipped_after_verify_error or rot.outcome == "verify_error",
        rotation_reported_not_applied:
          s.rotation_reported_not_applied or rot.outcome == "not_applied",
        sync_rewrote_active_profile: s.sync_rewrote_active_profile or sync_rewrites(us2),
        base_url_inferred: s.base_url_inferred or (merged.base_url == BUnset),
        base_url_cleared: s.base_url_cleared or
          (match p.llm { | None => false | Some(c) => c.base_url == BCleared }) }
    },
  }

  /// GET /api/v1/settings. The read path, which WRITES:
  /// invalidate_legacy_secrets_store moves settings.secrets_store into the
  /// secrets store and clears the settings field. It stores
  /// Secrets(provider_tokens=...) with no custom_secrets, and
  /// SaasSecretsStore.store deletes the whole personal region before
  /// re-inserting -- so every custom secret the user had goes with it. It also
  /// runs outside _secrets_write_lock, so it can land inside another request's
  /// open load->store window.
  action load_settings_migrates_legacy_secrets(u: str, personal_secret_count: int): bool = all {
    s.settings.get(u).present,
    s.settings.get(u).legacy_tokens.keys().size() > 0,
    // Conformance: the migration stores a Secrets with NO custom_secrets, and
    // the store deletes the personal region before re-inserting -- so every
    // personal custom secret goes with it. Zero is the fact that pins that.
    personal_secret_count == 0,
    s' = {
      val us = s.settings.get(u)
      val migrated: Store = { custom: Map(), tokens: us.legacy_tokens }
      val st2 = secrets_store_write(s, u, migrated)
      { ...st2,
        settings: st2.settings.put(u,
          { ...us, legacy_tokens: Map(), profiles: profiles_load(us.profiles) }),
        legacy_migrated: true,
        invalid_profile_dropped: st2.invalid_profile_dropped or us.profiles.broken.size() > 0,
        orphan_active_reconciled:
          st2.orphan_active_reconciled or reconcile_drops_active(us.profiles) }
    },
  }

  /// GET /api/v1/settings/profiles. The Settings it reads came back through
  /// _skip_invalid_profiles and _reconcile_active, so a stored pointer naming
  /// nothing is healed here rather than surfaced.
  action list_profiles(u: str): bool = all {
    s.settings.get(u).present,
    s' = {
      val us = s.settings.get(u)
      { ...s,
        settings: s.settings.put(u, { ...us, profiles: profiles_load(us.profiles) }),
        invalid_profile_dropped: s.invalid_profile_dropped or us.profiles.broken.size() > 0,
        orphan_active_reconciled:
          s.orphan_active_reconciled or reconcile_drops_active(us.profiles) }
    },
  }

  /// GET /api/v1/settings/profiles/{name} -- 404 on an unknown name. The
  /// response nulls config.api_key whatever is stored and reports only
  /// api_key_set beside it, so a GET-edit-POST round trip cannot write a mask
  /// back over the real key.
  action get_profile(u: str, name: str): bool = all {
    GOOD_PROFILE_NAMES.contains(name),
    s.settings.get(u).profiles.cfgs.keys().contains(name),
    s' = { ...s,
           profile_read_withheld_stored_key: s.profile_read_withheld_stored_key or
             has_real_key(s.settings.get(u).profiles.cfgs.get(name).key) },
  }

  /// POST /api/v1/settings/profiles/{name}.
  action save_profile(
    u: str, name: str, req: str, include_secrets: bool, preserve_key: bool,
    at_cap: bool, profile_key_set: bool
  ): bool = all {
    SAVE_REQ_KEYS.contains(req),
    GOOD_PROFILE_NAMES.contains(name),
    s.settings.get(u).present,
    // Conformance: whether the profile this save stores ends up holding a real
    // key -- the include_secrets / preserve_existing_api_key / omitted-key
    // branches are the whole content of this handler.
    at_cap or
      profile_key_set ==
        has_real_key(saved_profile_cfg(s.settings.get(u), name, SAVE_REQ_BY_KEY.get(req),
                                       include_secrets, preserve_key).key),
    s' = {
      val r = SAVE_REQ_BY_KEY.get(req)
      val us = s.settings.get(u)
      val was_active = us.profiles.active == Some(name)
      if (at_cap)
        { ...s, profile_cap_blocked_save: true }
      else {
        val p2 = { ...us.profiles,
                   cfgs: us.profiles.cfgs.put(
                     name, saved_profile_cfg(us, name, r, include_secrets, preserve_key)) }
        val us2 = { ...us, profiles: p2 }
        { ...s, settings: s.settings.put(u, if (was_active) switch_to(us2, name) else us2) }
      }
    },
  }

  /// DELETE /api/v1/settings/profiles/{name}. Idempotent, and the one delete
  /// path that promotes a surviving profile when the active one goes.
  action delete_profile(u: str, name: str, active_after: str): bool = all {
    GOOD_PROFILE_NAMES.contains(name),
    s.settings.get(u).present,
    // Conformance: WHICH profile the delete left active -- the fact that pins
    // the fallback promotion, not merely that one happened.
    active_after == active_name_of(settings_delete_profile(s.settings.get(u), name).profiles),
    s' = {
      val us = s.settings.get(u)
      if (not(us.profiles.cfgs.keys().contains(name))) s
      else {
        val was_active = us.profiles.active == Some(name)
        val us2 = settings_delete_profile(us, name)
        val left = profile_count(us2.profiles)
        { ...s,
          settings: s.settings.put(u, us2),
          fallback_profile_promoted:
            s.fallback_profile_promoted or (was_active and us2.profiles.active != None),
          active_cleared_no_fallback:
            s.active_cleared_no_fallback or (was_active and us2.profiles.active == None),
          active_dropped_with_profiles_left:
            s.active_dropped_with_profiles_left
              or (was_active and us2.profiles.active == None and left > 0) }
      }
    },
  }

  /// POST /api/v1/settings/profiles/{name}/activate.
  action activate_profile(u: str, name: str, verdict: str, agent_key_set: bool): bool = all {
    GOOD_PROFILE_NAMES.contains(name),
    s.settings.get(u).present,
    // switch_to_profile raises ProfileNotFoundError -> 404.
    s.settings.get(u).profiles.cfgs.keys().contains(name),
    agent_key_set == key_set_after_activate(s.settings.get(u), name, verdict),
    s' = {
      val us = s.settings.get(u)
      val switched = switch_to(us, name)
      val fixed = { ...switched.agent_llm,
                    base_url: resolve_base_url(switched.agent_llm.model,
                                               switched.agent_llm.base_url) }
      val rot = rotate(fixed, verdict)
      val us2 = { ...switched, agent_llm: rot.cfg }
      { ...s,
        settings: s.settings.put(u, sync_active(us2)),
        key_rotated: s.key_rotated or rot.outcome == "rotated",
        rotation_skipped_after_verify_error:
          s.rotation_skipped_after_verify_error or rot.outcome == "verify_error",
        rotation_reported_not_applied:
          s.rotation_reported_not_applied or rot.outcome == "not_applied",
        sync_rewrote_active_profile: s.sync_rewrote_active_profile or sync_rewrites(us2) }
    },
  }

  /// POST /api/v1/settings/profiles/{name}/rename.
  action rename_profile(u: str, old_name: str, new_name: str, active_after: str): bool = all {
    GOOD_PROFILE_NAMES.contains(old_name),
    GOOD_PROFILE_NAMES.contains(new_name),
    s.settings.get(u).present,
    // ProfileNotFoundError -> 404.
    s.settings.get(u).profiles.cfgs.keys().contains(old_name),
    // ProfileAlreadyExistsError -> 409. A same-name rename is a no-op.
    (new_name == old_name) or not(s.settings.get(u).profiles.cfgs.keys().contains(new_name)),
    active_after == active_name_after_rename(s.settings.get(u).profiles, old_name, new_name),
    s' = {
      val us = s.settings.get(u)
      val was_active = us.profiles.active == Some(old_name)
      val p2 = profiles_rename(us.profiles, old_name, new_name)
      { ...s,
        settings: s.settings.put(u, { ...us,
          profiles: p2,
          title_profile: if (us.title_profile == Some(old_name)) Some(new_name)
                         else us.title_profile }),
        rename_kept_active: s.rename_kept_active or (was_active and p2.active == Some(new_name)),
        active_dropped_with_profiles_left:
          s.active_dropped_with_profiles_left
            or (was_active and p2.active == None and profile_count(p2) > 0) }
    },
  }

  /// AgentProfiles._reconcile_active via load_agent_profiles: prune unparseable
  /// entries, then null an ``active`` that no longer names one.
  action AgentProfiles__reconcile_active: bool = all {
    s' = {
      val a2 = agents_load(s.org_agents)
      { ...s,
        org_agents: a2,
        invalid_profile_dropped: s.invalid_profile_dropped or s.org_agents.broken.size() > 0,
        orphan_active_reconciled:
          s.orphan_active_reconciled or (s.org_agents.active != None and a2.active == None) }
    },
  }

  /// The org LLM-profile CRUD surface (server/routes/org_profiles.py), all
  /// under one SELECT ... FOR UPDATE on the org row. ``op`` selects the verb.
  action LLMProfiles_createupdatedeleteactivaterename(
    op: str, name: str, cfg: str, at_cap: bool, materialized: bool
  ): bool = all {
    GOOD_PROFILE_NAMES.contains(name),
    ORG_CFG_KEYS.contains(cfg),
    or {
      all { op == "save", not(at_cap) },
      // activate resolves against materialize_default_llm_profile's view, so a
      // deployment default is activatable before it is stored; the handler then
      // writes the logical profile back.
      all { op == "activate", materialized or s.org_llm.cfgs.keys().contains(name) },
      all { op == "delete", s.org_llm.cfgs.keys().contains(name) },
    },
    s' =
      if (op == "save") {
        val c = ORG_CFG_BY_KEY.get(cfg)
        // include_secrets is forced off for a managed-proxy profile, so a
        // managed key is never persisted on an org profile.
        val include = not(routes_managed(c))
        { ...s, org_llm: profiles_save(s.org_llm, name, resolved(c), include) }
      } else if (op == "activate") {
        val cfgs2 =
          if (s.org_llm.cfgs.keys().contains(name)) s.org_llm.cfgs
          else s.org_llm.cfgs.put(name, resolved(ORG_CFG_BY_KEY.get(cfg)))
        { ...s, org_llm: { ...s.org_llm, cfgs: cfgs2, active: Some(name) } }
      } else {
        // delete_llm_profile: blocked while an Agent Profile still refers to it.
        if (llm_referrers(s.org_agents, name).size() > 0) s
        else {
          val was_active = s.org_llm.active == Some(name)
          val p2 = profiles_delete(s.org_llm, name)
          { ...s,
            org_llm: p2,
            active_dropped_with_profiles_left:
              s.active_dropped_with_profiles_left
                or (was_active and p2.active == None and profile_count(p2) > 0) }
        }
      },
  }

  /// LLMProfiles._reconcile_active via load_llm_profiles.
  action LLMProfiles__reconcile_active: bool = all {
    s' = {
      val p2 = profiles_load(s.org_llm)
      { ...s,
        org_llm: p2,
        invalid_profile_dropped: s.invalid_profile_dropped or s.org_llm.broken.size() > 0,
        orphan_active_reconciled:
          s.orphan_active_reconciled or reconcile_drops_active(s.org_llm) }
    },
  }

  /// A stored profile stops validating after a schema change -- the input
  /// _skip_invalid_profiles exists for, and the only way an ``active`` pointer
  /// comes to dangle without a direct DB write. Adversarial: no log site.
  action profile_schema_drift(name: str): bool = all {
    s.org_llm.cfgs.keys().contains(name),
    not(s.org_llm.broken.contains(name)),
    s' = { ...s, org_llm: { ...s.org_llm, broken: s.org_llm.broken.union(Set(name)) } },
  }

  /// OrgLLMProfileMutator.delete -- the FK cascade's write primitive, straight
  /// onto LLMProfiles.delete. It does NOT promote a fallback, and it is the
  /// mutator delete_llm_profile's find_referrers guard wraps rather than one
  /// sitting behind it.
  action OrgLLMProfileMutator_delete(name: str): bool = all {
    s.org_llm.cfgs.keys().contains(name),
    s' = {
      val was_active = s.org_llm.active == Some(name)
      val p2 = profiles_delete(s.org_llm, name)
      { ...s,
        org_llm: p2,
        active_dropped_with_profiles_left:
          s.active_dropped_with_profiles_left
            or (was_active and p2.active == None and profile_count(p2) > 0) }
    },
  }

  /// OrgLLMProfileMutator.rename, with rename_llm_profile's cascade repointing
  /// every agent profile that referred to the old name.
  action OrgLLMProfileMutator_rename(
    old_name: str, new_name: str, referrer_count: int
  ): bool = all {
    GOOD_PROFILE_NAMES.contains(new_name),
    // Conformance: how many Agent Profiles the cascade repointed.
    referrer_count == llm_referrers(s.org_agents, old_name).size(),
    s.org_llm.cfgs.keys().contains(old_name),
    (new_name == old_name) or not(s.org_llm.cfgs.keys().contains(new_name)),
    s' = {
      val refs = llm_referrers(s.org_agents, old_name)
      val was_active = s.org_llm.active == Some(old_name)
      val p2 = profiles_rename(s.org_llm, old_name, new_name)
      { ...s,
        org_llm: p2,
        org_agents: if (old_name == new_name) s.org_agents
                    else cascade_rename(s.org_agents, old_name, new_name),
        cascade_repointed_ref:
          s.cascade_repointed_ref or (refs.size() > 0 and old_name != new_name),
        rename_kept_active: s.rename_kept_active or (was_active and p2.active == Some(new_name)) }
    },
  }

  /// OrgLLMProfileLoader.load -- FileNotFoundError, which the resolver maps to
  /// ProfileNotFound, when the ref names no profile. Transition-only: the
  /// lookup guard is the whole contract.
  action OrgLLMProfileLoader_load(name: str): bool = all {
    s.org_llm.cfgs.keys().contains(name),
    s' = s,
  }

  /// AgentProfiles.save via save_profile_preserving_identity. It never checks
  /// that ``ref`` names a real LLM profile, which is what lets a reference
  /// dangle from the creating end.
  action save_agent_profile(pid: str, name: str, ref: str): bool = all {
    or {
      // Overwrite: the namesake keeps its stable id, so no cap applies.
      agent_id_for_name(s.org_agents, name) == Some(pid),
      // Create: a fresh id, a free name, and room under the cap.
      all {
        agent_id_for_name(s.org_agents, name) == None,
        not(s.org_agents.byId.keys().contains(pid)),
        s.org_agents.byId.keys().size() < MAX_AGENT_PROFILES,
      },
    },
    s' = { ...s, org_agents: { ...s.org_agents,
             byId: s.org_agents.byId.put(pid,
               { name: name, llm_ref: if (ref == "") None else Some(ref) }) } },
  }

  /// AgentProfiles.delete: idempotent, and it clears the active pointer without
  /// promoting anything in its place -- the asymmetry with
  /// Settings.delete_profile one level up.
  action delete_agent_profile(name: str): bool = all {
    s' =
      match agent_id_for_name(s.org_agents, name) {
        | None => s
        | Some(pid) => {
            val was_active = s.org_agents.active == Some(pid)
            val left = s.org_agents.byId.mapRemove(pid)
            { ...s,
              org_agents: { ...s.org_agents,
                            byId: left,
                            active: if (was_active) None else s.org_agents.active },
              active_dropped_with_profiles_left:
                s.active_dropped_with_profiles_left or (was_active and left.keys().size() > 0) }
          }
      },
  }

  /// AgentProfiles.rename: a name edit only. The stable id is preserved, so the
  /// active pointer and every LaunchedAgentProfile key survive untouched.
  action rename_agent_profile(old_name: str, new_name: str): bool = all {
    AGENT_NAMES.contains(new_name),
    agent_id_for_name(s.org_agents, old_name) != None,
    (new_name == old_name) or agent_id_for_name(s.org_agents, new_name) == None,
    s' = {
      val pid = unwrap(agent_id_for_name(s.org_agents, old_name))
      { ...s,
        org_agents: { ...s.org_agents,
          byId: s.org_agents.byId.put(pid, { ...s.org_agents.byId.get(pid), name: new_name }) },
        rename_kept_active: s.rename_kept_active or s.org_agents.active == Some(pid) }
    },
  }

  /// POST/PATCH/DELETE /orgs/{id}/provider-connections, under the org row lock.
  action ProviderConnections_createupdatedelete(op: str, cid: str, key: str): bool = all {
    CONN_IDS.contains(cid),
    or {
      all { op == "create",
            not(s.connections.keys().contains(cid)),
            s.connections.keys().size() < MAX_CONNECTIONS },
      all { op == "update", s.connections.keys().contains(cid) },
      all { op == "delete", s.connections.keys().contains(cid) },
    },
    s' =
      if (op == "delete") {
        // _referencing_profiles: 409 while an LLM profile still links here.
        val refs = s.org_llm.cfgs.keys().filter(n => s.org_llm.cfgs.get(n).conn == Some(cid))
        if (refs.size() > 0) { ...s, conn_delete_blocked_by_referrer: true }
        else { ...s, connections: s.connections.mapRemove(cid) }
      } else {
        { ...s, connections: s.connections.put(cid, { key: key, base_url: BAt(SELF_HOSTED_URL) }) }
      },
  }

  /// check_provider_tokens. Runs BEFORE the write lock is taken. A token must
  /// type as the provider it is being stored under; and when the host changes
  /// on a provider that already holds a token, the EXISTING token is
  /// re-validated against the new host.
  action check_provider_tokens(u: str, provider: str, token: str, host: str): bool = all {
    PROVIDERS.contains(provider),
    // _check_token_type on the incoming token.
    (token == NO_KEY) or (TOKEN_PROVIDER.getOrElse(token, provider) == provider),
    // _check_token_type on the existing token, when the host moved.
    {
      val cur = s.personal.get(u).tokens
      not(cur.has(provider)) or cur.get(provider).host == host
        or cur.get(provider).token == NO_KEY
        or TOKEN_PROVIDER.getOrElse(cur.get(provider).token, provider) == provider
    },
    s' = {
      val cur = s.personal.get(u).tokens
      { ...s,
        checked: s.checked.union(Set(provider)),
        token_host_revalidated: s.token_host_revalidated or
          (cur.has(provider) and cur.get(provider).host != host
           and cur.get(provider).token != NO_KEY) }
    },
  }

  /// POST /api/v1/secrets/git-providers. Merges a host-only update onto the
  /// stored token, then REPLACES the whole provider_tokens map with the
  /// payload's -- so a provider the payload leaves out is dropped. It loads the
  /// MERGED view, so it also rewrites org-shared secrets as personal rows.
  action store_provider_tokens(
    u: str, provider: str, token: str, host: str, token_stored: bool
  ): bool = all {
    s.inflight == None,
    PROVIDERS.contains(provider),
    // Conformance: whether this provider holds a real token afterwards. The
    // merge branch (an omitted token keeps the stored one) is what this pins.
    token_stored ==
      (token != NO_KEY or
       (secrets_load_personal(s, u).tokens.has(provider) and
        secrets_load_personal(s, u).tokens.get(provider).token != NO_KEY)),
    // No `checked` precondition: check_provider_tokens is called inline at the
    // top of this same handler, not as a separate request.
    s' = {
      val cur = secrets_load_personal(s, u)
      val kept =
        if (cur.tokens.has(provider) and token == NO_KEY
            and cur.tokens.get(provider).token != NO_KEY)
          cur.tokens.get(provider).token
        else token
      val dropped = cur.tokens.keys().exclude(Set(provider))
      val written: Store = { ...cur, tokens: Map(provider -> { token: kept, host: host }) }
      { ...secrets_store_write(s, u, written),
        checked: s.checked.exclude(Set(provider)),
        tokens_dropped_by_partial_payload:
          s.tokens_dropped_by_partial_payload or dropped.size() > 0 }
    },
  }

  /// DELETE /api/v1/secrets/git-providers -- clears every provider token.
  action unset_provider_tokens(u: str, token_stored: bool): bool = all {
    s.inflight == None,
    not(token_stored),
    s' = {
      val cur = secrets_load_personal(s, u)
      val st2 = mark_removed(s, u, cur.tokens.keys())
      secrets_store_write(st2, u, { ...cur, tokens: Map() })
    },
  }

  /// GET /api/v1/secrets/search. Lists the user's personal rows and the org's
  /// shared ones side by side, each tagged with its scope and suffixed on a
  /// name collision. Values never appear in the response.
  action search_custom_secret(u: str, limit: int): bool = all {
    // Query(gt=0, le=100)
    limit > 0,
    limit <= 100,
    s' = { ...s, suffix_dedup_applied: s.suffix_dedup_applied or load_applies_suffix(s, u) },
  }

  /// POST /api/v1/secrets -- create or overwrite one custom secret, inside the
  /// write lock. It loads the MERGED view and stores it back, so every
  /// org-shared secret the load pulled in is rewritten as a personal row.
  action create_custom_secret(
    u: str, name: str, value: str, secret_stored: bool
  ): bool = all {
    s.inflight == None,
    BASE_SECRET_NAMES.contains(name),
    // Conformance: the secret is readable from the personal rows afterwards.
    // A count would be a hostage to how many identities the model sizes for;
    // presence survives canonicalisation.
    secret_stored,
    // _validate_codex_auth_secret -> 400.
    codex_value_ok(name, value),
    s' = {
      val loaded = secrets_load_personal(s, u)
      val written: Store = { ...loaded, custom: loaded.custom.put(name, { owner: u, corrupt: false }) }
      secrets_store_write(s, u, written)
    },
  }

  /// PUT /api/v1/secrets/{secret_id} -- rename and/or re-describe. The value is
  /// carried across untouched, so provenance goes with it.
  action update_custom_secret(
    u: str, secret_id: str, new_name: str, secret_stored: bool
  ): bool = all {
    s.inflight == None,
    BASE_SECRET_NAMES.contains(new_name),
    // A rename moves the row; the value stays readable under the new name.
    secret_stored,
    // 404 when the id is unknown.
    secrets_load_personal(s, u).custom.keys().contains(secret_id),
    // 400 when the new name is already taken by a different secret.
    (new_name == secret_id) or not(secrets_load_personal(s, u).custom.keys().contains(new_name)),
    s' = {
      val loaded = secrets_load_personal(s, u)
      val moved = loaded.custom.get(secret_id)
      val written: Store = { ...loaded, custom: loaded.custom.mapRemove(secret_id).put(new_name, moved) }
      val st2 = if (new_name == secret_id) s else mark_removed(s, u, Set(secret_id))
      { ...secrets_store_write(st2, u, written),
        secret_renamed: s.secret_renamed or new_name != secret_id }
    },
  }

  /// DELETE /api/v1/secrets/{secret_id}.
  action delete_custom_secret(u: str, secret_id: str, secret_stored: bool): bool = all {
    s.inflight == None,
    not(secret_stored),
    secrets_load_personal(s, u).custom.keys().contains(secret_id),
    s' = {
      val loaded = secrets_load_personal(s, u)
      val written: Store = { ...loaded, custom: loaded.custom.mapRemove(secret_id) }
      val st2 = mark_removed(s, u, Set(secret_id))
      secrets_store_write(st2, u, written)
    },
  }

  /// SecretsStore.store(secrets) reached directly -- the abstract bulk writer
  /// every endpoint funnels into, and that a background caller can reach on its
  /// own with no lock and no load. Nothing here marks the names this write
  /// drops as intentionally removed: a direct region overwrite is exactly the
  /// write that loses them quietly.
  action SecretsStore_store(u: str, secrets: Set[str], tokens: Set[str]): bool = all {
    tokens.subseteq(PROVIDERS),
    s' = secrets_store_write(s, u,
           { custom: secrets.mapBy(_ => { owner: u, corrupt: false }),
             tokens: tokens.mapBy(pr => { token: TOKEN_OF.getOrElse(pr, ""), host: "h1" }) }),
  }

  /// A write path that loads through the MERGED load() instead of
  /// load_personal() -- the regression load_personal was added to prevent
  /// (storage/saas_secrets_store.py:106). It is not the identity: load merges the
  /// org's shared secrets into the user's view, and store writes that whole view
  /// back as personal rows, handing the member a private copy of a credential the
  /// org still believes it controls. Adversarial: no write endpoint calls load()
  /// today, so this has no log site -- it keeps
  /// no_foreign_secret_in_personal_store falsifiable, which is what makes that
  /// property a regression test rather than a restatement of the model.
  action SaasSecretsStore_loadstore(u: str): bool = all {
    s.inflight == None,
    s' = {
      val loaded = secrets_load(s, u)
      { ...secrets_store_write(s, u, loaded),
        suffix_dedup_applied: s.suffix_dedup_applied or load_applies_suffix(s, u) }
    },
  }

  /// SettingsStore.store(settings) -- the baseline-establishing write: a test
  /// fixture seeds its whole starting view through it, so it carries its WHOLE
  /// payload rather than a delta, and replay starts from the state that was
  /// actually persisted.
  action SettingsStore_store(
    u: str, profiles: Set[str], keyed: Set[str], managed: Set[str],
    active: str, resolved_view: bool, agent_key_set: bool, agent_managed: bool
  ): bool = all {
    // SaasSettingsStore.store refuses a resolved Agent-Profile launch view:
    // persisting it would bake the referenced LLM profile's key and the
    // ref-filtered mcp_config into the member row.
    not(resolved_view),
    s' = {
      val cfgs = profiles.mapBy(n =>
        { model: if (managed.contains(n)) "openhands/gpt" else "byok/gpt",
          base_url: if (managed.contains(n)) BAt(MANAGED_URL) else BAt(SELF_HOSTED_URL),
          key: if (keyed.contains(n)) USER_KEY else NO_KEY,
          conn: None })
      { ...s,
        settings: s.settings.put(u, { ...s.settings.get(u),
          present: true,
          agent_llm: { model: if (agent_managed) "openhands/gpt" else "byok/gpt",
                       base_url: if (agent_managed) BAt(MANAGED_URL) else BAt(SELF_HOSTED_URL),
                       key: if (agent_key_set) USER_KEY else NO_KEY,
                       conn: None },
          // An active pointer naming nothing survives the write: the validator
          // that heals it runs on load, not here.
          profiles: { cfgs: cfgs,
                      active: if (active == "") None else Some(active),
                      broken: Set() } }) }
    },
  }

  /// The org row's whole profile/connection state, as one transaction load.
  ///
  /// Baseline-establishing. The ``org.llm_profiles`` / ``org.agent_profiles``
  /// columns are seeded directly by fixtures, never through an endpoint, so
  /// without this the model never learns that an org profile exists and every
  /// org-scope guard fails on a name it has never seen. It carries the WHOLE
  /// collection rather than a delta, so its replay consumes no unguided budget.
  ///
  /// The collections are flattened to sets: the profiles that route to the
  /// managed proxy, the ones holding a real key, the ones linked to a provider
  /// connection (all to the same one -- an org with two linked connections is
  /// beyond what the suite builds), and the agent profiles carrying an
  /// llm_profile_ref (all to the same profile, for the same reason).
  action OrgStore_load(
    profiles: Set[str], managed: Set[str], keyed: Set[str], linked: Set[str],
    link_target: str, llm_active: str,
    agents: Set[str], refs: Set[str], ref_target: str, agent_active: str,
    connections: Set[str], dropped_any: bool
  ): bool = all {
    profiles.subseteq(GOOD_PROFILE_NAMES),
    agents.subseteq(AGENT_IDS),
    connections.subseteq(CONN_IDS),
    s' = { ...s,
      org_llm: {
        cfgs: profiles.mapBy(n =>
          { model: if (managed.contains(n)) "openhands/gpt" else "byok/gpt",
            base_url: if (managed.contains(n)) BAt(MANAGED_URL) else BAt(SELF_HOSTED_URL),
            key: if (keyed.contains(n)) USER_KEY else NO_KEY,
            conn: if (linked.contains(n) and link_target != "") Some(link_target) else None }),
        active: if (llm_active == "") None else Some(llm_active),
        broken: Set() },
      org_agents: {
        byId: agents.mapBy(pid =>
          { name: AGENT_NAME_OF.getOrElse(pid, pid),
            llm_ref: if (refs.contains(pid) and ref_target != "") Some(ref_target) else None }),
        active: if (agent_active == "") None else Some(agent_active),
        broken: Set() },
      connections: connections.mapBy(_ => { key: USER_KEY, base_url: BAt(SELF_HOSTED_URL) }),
      // _skip_invalid_profiles dropped at least one stored entry on the way in.
      invalid_profile_dropped: s.invalid_profile_dropped or dropped_any },
  }

  /// SettingsStore.load -- the persisted settings exactly as a reader saw them.
  ///
  /// Baseline-establishing, like SettingsStore_store, but for a caller that only
  /// READS: the sandbox profile seeder loads the user's profiles and resolves
  /// each one, and without this the model has no state for that resolve to stand
  /// on. It carries the whole collection, so its replay costs no unguided budget.
  action SettingsStore_load(
    u: str, profiles: Set[str], keyed: Set[str], managed: Set[str],
    active: str, agent_key_set: bool, agent_managed: bool
  ): bool = all {
    profiles.subseteq(GOOD_PROFILE_NAMES),
    s' = {
      val cfgs = profiles.mapBy(n =>
        { model: if (managed.contains(n)) "openhands/gpt" else "byok/gpt",
          base_url: if (managed.contains(n)) BAt(MANAGED_URL) else BAt(SELF_HOSTED_URL),
          key: if (keyed.contains(n)) USER_KEY else NO_KEY,
          conn: None })
      { ...s,
        settings: s.settings.put(u, { ...s.settings.get(u),
          present: true,
          agent_llm: { model: if (agent_managed) "openhands/gpt" else "byok/gpt",
                       base_url: if (agent_managed) BAt(MANAGED_URL) else BAt(SELF_HOSTED_URL),
                       key: if (agent_key_set) USER_KEY else NO_KEY,
                       conn: None },
          profiles: { cfgs: cfgs,
                      active: if (active == "") None else Some(active),
                      broken: Set() } }) }
    },
  }

  /// UserAppSettingsService.get_user_app_settings -- UserNotFoundError when no
  /// User row matches. Transition-only: the 404 boundary is the whole contract.
  action UserAppSettingsService_get_user_app_settings(user_id: str): bool = all {
    USERS.contains(user_id),
    s' = s,
  }

  /// UserAppSettingsService.update_user_app_settings: model_dump(exclude_unset)
  /// means an omitted field is left alone while an explicit null clears it.
  action UserAppSettingsService_update_user_app_settings(
    user_id: str, language_set: bool, language: str
  ): bool = all {
    USERS.contains(user_id),
    s' = {
      val cur = s.app.get(user_id)
      val next_language = if (language == "") None else Some(language)
      if (not(language_set)) s
      else { ...s,
             app: s.app.put(user_id, { ...cur, language: next_language }),
             app_setting_cleared:
               s.app_setting_cleared or (next_language == None and cur.language != None) }
    },
  }

  /// OrgSecretsStore.create_shared -- a row-level write that does not touch
  /// personal secrets.
  action OrgSecretsStore_create_shared(name: str): bool = all {
    BASE_SECRET_NAMES.contains(name),
    not(s.org_shared.keys().contains(name)),
    s' = { ...s, org_shared: s.org_shared.put(name, { owner: ORG, corrupt: false }) },
  }

  /// OrgSecretsStore.update_shared: name and description only. The value is
  /// write-once -- rotating it means delete and re-create.
  action OrgSecretsStore_update_shared(current_name: str, new_name: str): bool = all {
    BASE_SECRET_NAMES.contains(new_name),
    s.org_shared.keys().contains(current_name),
    (new_name == current_name) or not(s.org_shared.keys().contains(new_name)),
    s' = { ...s,
           org_shared:
             if (new_name == current_name) s.org_shared
             else s.org_shared.mapRemove(current_name).put(new_name, s.org_shared.get(current_name)) },
  }

  /// OrgSecretsStore.delete_shared. It removes the shared row only; any
  /// personal copy an earlier write-back made stays behind.
  action OrgSecretsStore_delete_shared(name: str): bool = all {
    s.org_shared.keys().contains(name),
    s' = { ...s, org_shared: s.org_shared.mapRemove(name) },
  }

  /// A stored ciphertext stops decrypting -- a rotated or re-derived JWE key,
  /// the case decrypt_legacy_kwargs' `except InvalidToken: pass` was written
  /// for. Adversarial: no log site.
  action ciphertext_goes_corrupt(u: str, name: str): bool = all {
    s.personal.get(u).custom.keys().contains(name),
    not(s.personal.get(u).custom.get(name).corrupt),
    s' = { ...s, personal: s.personal.put(u, { ...s.personal.get(u),
             custom: s.personal.get(u).custom.setBy(name, v => { ...v, corrupt: true }) }) },
  }

  /// decrypt_legacy_kwargs (storage/encrypt_utils.py:41). ``corrupt`` is the
  /// InvalidToken branch. The code cannot tell a value that was never encrypted
  /// from one this key can no longer open -- both land there and both leave the
  /// raw value in place -- so one flag carries the conflation.
  action decrypt_legacy_kwargs(corrupt: bool): bool = all {
    s' = { ...s, ciphertext_served_as_plaintext: s.ciphertext_served_as_plaintext or corrupt },
  }

  /// resolve_profile_llm at launch.
  action resolve_profile_llm_for_launch(u: str, name: str): bool = all {
    s.settings.get(u).profiles.cfgs.keys().contains(name),
    s' = {
      val prof = s.settings.get(u).profiles.cfgs.get(name)
      val out = resolve_profile_llm(prof, s.settings.get(u).agent_llm.key)
      { ...s,
        managed_profile_key_overridden:
          s.managed_profile_key_overridden
            or (routes_managed(prof) and has_real_key(prof.key) and out.key != prof.key) }
    },
  }

  // --------------------------------------------- the open load->store window
  //
  // The five secret endpoints each run load -> mutate -> store inside
  // _secrets_write_lock, which serializes them against each other. These two
  // actions are that window held open across a step, so the writers that do NOT
  // take the lock can be seen landing inside it. Modelled once rather than per
  // endpoint: the window has the same shape in all five. Sim-side: a single
  // handler completes its load and store within one request, so there is no
  // point in the code where "the window is open" is itself a transition.

  /// Acquire the write lock and snapshot the loaded view.
  action secrets_rmw_begin(u: str, name: str): bool = all {
    s.inflight == None,
    BASE_SECRET_NAMES.contains(name),
    s' = { ...s, inflight: Some({ user: u, baseline: secrets_load_personal(s, u), add: name }) },
  }

  /// Store the snapshot plus the new secret, and release. Whatever landed in
  /// between is overwritten: the payload is built from the BASELINE, not from a
  /// re-read.
  action secrets_rmw_commit: bool = all {
    s.inflight != None,
    s' = {
      val r = unwrap(s.inflight)
      val written: Store =
        { ...r.baseline, custom: r.baseline.custom.put(r.add, { owner: r.user, corrupt: false }) }
      val live = s.personal.get(r.user)
      val clobbered =
        live.tokens.keys().exists(p =>
          not(written.tokens.has(p)) or written.tokens.get(p) != live.tokens.get(p))
      { ...secrets_store_write(s, r.user, written),
        inflight: None,
        unlocked_write_overwritten: s.unlocked_write_overwritten or clobbered }
    },
  }

  // -------------------------------------------------------------------- step

  action step: bool = any {
    {
      nondet u = USERS.oneOf()
      nondet payload = PAYLOAD_KEYS.oneOf()
      nondet verdict = VERDICTS.oneOf()
      nondet agent_key_set = Set(has_real_key(
        store_settings_llm(s.settings.get(u), PAYLOAD_BY_KEY.get(payload), verdict).cfg.key)).oneOf()
      store_settings(u, payload, verdict, agent_key_set)
    },
    {
      nondet u = USERS.oneOf()
      nondet personal_secret_count = Set(0).oneOf()
      load_settings_migrates_legacy_secrets(u, personal_secret_count)
    },
    {
      nondet u = USERS.oneOf()
      list_profiles(u)
    },
    {
      nondet u = USERS.oneOf()
      nondet name = PROFILE_NAMES.oneOf()
      get_profile(u, name)
    },
    {
      nondet u = USERS.oneOf()
      nondet name = PROFILE_NAMES.oneOf()
      nondet req = SAVE_REQ_KEYS.oneOf()
      nondet include_secrets = Set(false, true).oneOf()
      nondet preserve_key = Set(false, true).oneOf()
      nondet at_cap = Set(false, true).oneOf()
      nondet profile_key_set = Set(has_real_key(
        saved_profile_cfg(s.settings.get(u), name, SAVE_REQ_BY_KEY.get(req),
                          include_secrets, preserve_key).key)).oneOf()
      save_profile(u, name, req, include_secrets, preserve_key, at_cap, profile_key_set)
    },
    {
      nondet u = USERS.oneOf()
      nondet name = PROFILE_NAMES.oneOf()
      nondet active_after =
        Set(active_name_of(settings_delete_profile(s.settings.get(u), name).profiles)).oneOf()
      delete_profile(u, name, active_after)
    },
    {
      nondet u = USERS.oneOf()
      nondet name = PROFILE_NAMES.oneOf()
      nondet verdict = VERDICTS.oneOf()
      nondet agent_key_set = Set(key_set_after_activate(s.settings.get(u), name, verdict)).oneOf()
      activate_profile(u, name, verdict, agent_key_set)
    },
    {
      nondet u = USERS.oneOf()
      nondet old_name = PROFILE_NAMES.oneOf()
      nondet new_name = PROFILE_NAMES.oneOf()
      nondet active_after =
        Set(active_name_after_rename(s.settings.get(u).profiles, old_name, new_name)).oneOf()
      rename_profile(u, old_name, new_name, active_after)
    },
    AgentProfiles__reconcile_active,
    {
      nondet op = Set("save", "activate", "delete").oneOf()
      nondet name = PROFILE_NAMES.oneOf()
      nondet cfg = ORG_CFG_KEYS.oneOf()
      nondet at_cap = Set(false, true).oneOf()
      nondet materialized = Set(false, true).oneOf()
      LLMProfiles_createupdatedeleteactivaterename(op, name, cfg, at_cap, materialized)
    },
    LLMProfiles__reconcile_active,
    {
      nondet name = PROFILE_NAMES.oneOf()
      profile_schema_drift(name)
    },
    {
      nondet name = PROFILE_NAMES.oneOf()
      OrgLLMProfileMutator_delete(name)
    },
    {
      nondet old_name = PROFILE_NAMES.oneOf()
      nondet new_name = PROFILE_NAMES.oneOf()
      nondet referrer_count = Set(llm_referrers(s.org_agents, old_name).size()).oneOf()
      OrgLLMProfileMutator_rename(old_name, new_name, referrer_count)
    },
    {
      nondet name = PROFILE_NAMES.oneOf()
      OrgLLMProfileLoader_load(name)
    },
    {
      nondet pid = AGENT_IDS.oneOf()
      nondet name = AGENT_NAMES.oneOf()
      nondet ref = GOOD_PROFILE_NAMES.union(Set("")).oneOf()
      save_agent_profile(pid, name, ref)
    },
    {
      nondet name = AGENT_NAMES.oneOf()
      delete_agent_profile(name)
    },
    {
      nondet old_name = AGENT_NAMES.oneOf()
      nondet new_name = AGENT_NAMES.oneOf()
      rename_agent_profile(old_name, new_name)
    },
    {
      nondet op = Set("create", "update", "delete").oneOf()
      nondet cid = CONN_IDS.oneOf()
      nondet key = KEYS.oneOf()
      ProviderConnections_createupdatedelete(op, cid, key)
    },
    {
      nondet u = USERS.oneOf()
      nondet provider = PROVIDERS.oneOf()
      nondet token = TOKENS.oneOf()
      nondet host = HOSTS.oneOf()
      check_provider_tokens(u, provider, token, host)
    },
    {
      nondet u = USERS.oneOf()
      nondet provider = PROVIDERS.oneOf()
      nondet token = TOKENS.oneOf()
      nondet host = HOSTS.oneOf()
      nondet token_stored = Set(
        token != NO_KEY or
        (secrets_load_personal(s, u).tokens.has(provider) and
         secrets_load_personal(s, u).tokens.get(provider).token != NO_KEY)).oneOf()
      store_provider_tokens(u, provider, token, host, token_stored)
    },
    {
      nondet u = USERS.oneOf()
      nondet token_stored = Set(false).oneOf()
      unset_provider_tokens(u, token_stored)
    },
    {
      nondet u = USERS.oneOf()
      nondet limit = Set(0, 1, 100, 101).oneOf()
      search_custom_secret(u, limit)
    },
    {
      nondet u = USERS.oneOf()
      nondet name = BASE_SECRET_NAMES.oneOf()
      nondet value = Set("v", "codex-chatgpt").oneOf()
      nondet secret_stored = Set(true).oneOf()
      create_custom_secret(u, name, value, secret_stored)
    },
    {
      nondet u = USERS.oneOf()
      nondet secret_id = SECRET_NAMES.oneOf()
      nondet new_name = BASE_SECRET_NAMES.oneOf()
      nondet secret_stored = Set(true).oneOf()
      update_custom_secret(u, secret_id, new_name, secret_stored)
    },
    {
      nondet u = USERS.oneOf()
      nondet secret_id = SECRET_NAMES.oneOf()
      nondet secret_stored = Set(false).oneOf()
      delete_custom_secret(u, secret_id, secret_stored)
    },
    {
      // The whole personal region, as a baseline write carries it.
      nondet u = USERS.oneOf()
      nondet secrets = BASE_SECRET_NAMES.powerset().oneOf()
      nondet tokens = PROVIDERS.powerset().oneOf()
      SecretsStore_store(u, secrets, tokens)
    },
    {
      nondet u = USERS.oneOf()
      SaasSecretsStore_loadstore(u)
    },
    {
      // One nondet per logged argument, over the whole space each can carry: a
      // baseline write persists an arbitrary collection, so anything narrower
      // rejects the set a test actually stored.
      nondet u = USERS.oneOf()
      nondet resolved_view = Set(false, true).oneOf()
      nondet profiles = GOOD_PROFILE_NAMES.powerset().oneOf()
      nondet keyed = profiles.powerset().oneOf()
      nondet managed = profiles.powerset().oneOf()
      nondet active = GOOD_PROFILE_NAMES.union(Set("")).oneOf()
      nondet agent_key_set = Set(false, true).oneOf()
      nondet agent_managed = Set(false, true).oneOf()
      SettingsStore_store(u, profiles, keyed, managed, active, resolved_view,
                          agent_key_set, agent_managed)
    },
    {
      nondet profiles = GOOD_PROFILE_NAMES.powerset().oneOf()
      nondet managed = profiles.powerset().oneOf()
      nondet keyed = profiles.powerset().oneOf()
      nondet linked = profiles.powerset().oneOf()
      nondet link_target = CONN_IDS.union(Set("")).oneOf()
      nondet llm_active = GOOD_PROFILE_NAMES.union(Set("")).oneOf()
      nondet agents = AGENT_IDS.powerset().oneOf()
      nondet refs = agents.powerset().oneOf()
      nondet ref_target = GOOD_PROFILE_NAMES.union(Set("")).oneOf()
      nondet agent_active = AGENT_IDS.union(Set("")).oneOf()
      nondet connections = CONN_IDS.powerset().oneOf()
      nondet dropped_any = Set(false, true).oneOf()
      OrgStore_load(profiles, managed, keyed, linked, link_target, llm_active,
                    agents, refs, ref_target, agent_active, connections, dropped_any)
    },
    {
      nondet u = USERS.oneOf()
      nondet profiles = GOOD_PROFILE_NAMES.powerset().oneOf()
      nondet keyed = profiles.powerset().oneOf()
      nondet managed = profiles.powerset().oneOf()
      nondet active = GOOD_PROFILE_NAMES.union(Set("")).oneOf()
      nondet agent_key_set = Set(false, true).oneOf()
      nondet agent_managed = Set(false, true).oneOf()
      SettingsStore_load(u, profiles, keyed, managed, active, agent_key_set, agent_managed)
    },
    {
      nondet user_id = ALL_USER_IDS.oneOf()
      UserAppSettingsService_get_user_app_settings(user_id)
    },
    {
      nondet user_id = ALL_USER_IDS.oneOf()
      nondet language = LANGUAGES.union(Set("")).oneOf()
      nondet language_set = Set(false, true).oneOf()
      UserAppSettingsService_update_user_app_settings(user_id, language_set, language)
    },
    {
      nondet name = BASE_SECRET_NAMES.oneOf()
      OrgSecretsStore_create_shared(name)
    },
    {
      nondet current_name = BASE_SECRET_NAMES.oneOf()
      nondet new_name = BASE_SECRET_NAMES.oneOf()
      OrgSecretsStore_update_shared(current_name, new_name)
    },
    {
      nondet name = BASE_SECRET_NAMES.oneOf()
      OrgSecretsStore_delete_shared(name)
    },
    {
      nondet u = USERS.oneOf()
      nondet name = SECRET_NAMES.oneOf()
      ciphertext_goes_corrupt(u, name)
    },
    {
      nondet corrupt = Set(false, true).oneOf()
      decrypt_legacy_kwargs(corrupt)
    },
    {
      nondet u = USERS.oneOf()
      nondet name = GOOD_PROFILE_NAMES.oneOf()
      resolve_profile_llm_for_launch(u, name)
    },
    {
      nondet u = USERS.oneOf()
      nondet name = BASE_SECRET_NAMES.oneOf()
      secrets_rmw_begin(u, name)
    },
    secrets_rmw_commit,
  }

  // -------------------------------------------------- correctness properties

  /// Every value sitting in a user's personal secret rows was stored by that
  /// user. An org-shared secret copied into a personal row survives the org
  /// revoking it, and hands one member a private, permanent copy of a
  /// credential the org still believes it controls.
  val no_foreign_secret_in_personal_store: bool =
    USERS.forall(u =>
      s.personal.get(u).custom.keys().forall(n => s.personal.get(u).custom.get(n).owner == u))

  /// A custom secret or a provider token that was committed and never deleted is
  /// still readable. Anything else is silent data loss: the user's agent loses
  /// the credential mid-run with nothing in the UI to explain it.
  val no_silent_secret_loss: bool =
    USERS.forall(u => {
      val live = secrets_load(s, u)
      val owed = s.stored_ever.get(u).exclude(s.removed_on_purpose.get(u))
      val owed_tokens = s.tokens_ever.get(u).exclude(s.removed_on_purpose.get(u))
      and {
        owed.forall(n => live.custom.keys().contains(n)),
        owed_tokens.forall(p => live.tokens.has(p)),
      }
    })

  /// Deleting or renaming the active profile leaves the user with an active
  /// profile whenever they still have one to be active. Dropping the pointer
  /// while profiles remain silently demotes the user to the raw default LLM on
  /// their next conversation.
  val delete_never_strands_a_user_without_an_active_profile: bool =
    not(s.active_dropped_with_profiles_left)

  /// A value that no longer decrypts is never handed back as though it were the
  /// plaintext. When it is, the agent is launched with ciphertext in the
  /// environment variable the credential should have been in, and the failure
  /// surfaces as an unrelated auth error from the provider.
  val corrupt_ciphertext_is_never_served_as_a_secret: bool =
    not(s.ciphertext_served_as_plaintext)

  /// Every Agent Profile's llm_profile_ref names an LLM profile that exists. A
  /// dangling ref fails the profile at launch with a ProfileNotFound the user
  /// cannot act on: the profile they picked simply refuses to start, and nothing
  /// in the UI says which reference broke.
  val agent_profile_llm_ref_never_dangles: bool =
    s.org_agents.byId.keys().forall(pid =>
      match s.org_agents.byId.get(pid).llm_ref {
        | None => true
        | Some(n) => s.org_llm.cfgs.keys().contains(n)
      })

  // -------------------------------------------------------------- observations

  /// GET /settings migrated the legacy settings.secrets_store into the secrets
  /// store.
  val legacy_secrets_migrated_on_read: bool = s.legacy_migrated

  /// A write-back put a value the user did not store into their personal rows.
  val org_secret_copied_into_personal_rows: bool = s.org_secret_copied_to_personal

  /// A shared secret collided with a personal one and was surfaced suffixed.
  val shared_name_collision_suffixed: bool = s.suffix_dedup_applied

  /// A stale managed LiteLLM key was verified and replaced at write time.
  val managed_key_rotated_on_write: bool = s.key_rotated

  /// verify_key raised and the rotation was skipped -- the key stays stale.
  val rotation_skipped_after_verify_error: bool = s.rotation_skipped_after_verify_error

  /// rotate_managed_llm_key returned a non-ROTATED status.
  val rotation_reported_not_applied: bool = s.rotation_reported_not_applied

  /// A settings save rewrote the stored active profile to match the edited
  /// settings.
  val settings_save_rewrote_active_profile: bool = s.sync_rewrote_active_profile

  /// An unset base_url was filled in with a provider or managed-proxy default.
  val base_url_inferred_from_model: bool = s.base_url_inferred

  /// An explicit empty base_url cleared the stored one instead of inferring.
  val base_url_cleared_by_empty_string: bool = s.base_url_cleared

  /// _skip_invalid_profiles dropped an entry that no longer validates.
  val invalid_profile_dropped_on_load: bool = s.invalid_profile_dropped

  /// _reconcile_active nulled an active pointer that named nothing.
  val orphan_active_pointer_reconciled: bool = s.orphan_active_reconciled

  /// Deleting the active profile promoted a surviving one.
  val fallback_profile_promoted_on_delete: bool = s.fallback_profile_promoted

  /// Deleting the last profile left no active pointer -- the legitimate case.
  val active_cleared_with_no_profile_left: bool = s.active_cleared_no_fallback

  /// A rename carried the active pointer onto the new name.
  val rename_carried_active_pointer: bool = s.rename_kept_active

  /// A rename cascaded onto an Agent Profile's llm_profile_ref.
  val rename_cascaded_to_agent_profile: bool = s.cascade_repointed_ref

  /// A connection delete was refused because a profile still linked to it.
  val connection_delete_blocked_while_referenced: bool = s.conn_delete_blocked_by_referrer

  /// A provider's host changed and the stored token was re-validated against it.
  val provider_host_change_revalidated_stored_token: bool = s.token_host_revalidated

  /// A git-provider write dropped a provider the payload did not mention.
  val provider_token_dropped_by_partial_payload: bool = s.tokens_dropped_by_partial_payload

  /// A custom secret was renamed through the PUT endpoint.
  val custom_secret_renamed: bool = s.secret_renamed

  /// An explicit null cleared a user app setting.
  val app_setting_cleared_by_explicit_null: bool = s.app_setting_cleared

  /// A commit built from a stale baseline overwrote a write that had landed
  /// without taking the lock.
  val unlocked_write_lost_to_stale_baseline: bool = s.unlocked_write_overwritten

  /// A managed-proxy profile's stored key was overridden by the caller's key.
  val managed_profile_key_overridden_at_launch: bool = s.managed_profile_key_overridden

  /// A save was refused because the user was already at the profile cap.
  val profile_save_refused_at_cap: bool = s.profile_cap_blocked_save

  /// A profile detail read withheld a key that really was stored.
  val profile_detail_withheld_stored_key: bool = s.profile_read_withheld_stored_key
}
