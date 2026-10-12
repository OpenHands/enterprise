module org_budgets_recovery_test {
  import org_budgets.* from "./org-budgets"
  import basicSpells.* from "./spells/basicSpells"

  run recoverKnownMemberTest = init_from(Migrated).expect({
    val sg = { ...settings_or_default(s), default_user_monthly_limit: Some(30) }
    val st = { ...s, litellm: { ...s.litellm,
      members: START_MEMBERS.mapBy(u => { spend: 8, max_budget: Some(5), uses_shared: false }) } }
    val recovered = sync_with(st, sg, live_snapshot(st))
    val later = { ...st, litellm: { ...recovered.litellm,
      members: START_MEMBERS.mapBy(u => { ...recovered.litellm.members.get(u), spend: 11 }) } }
    val repeated = sync_with(later, recovered.settings, live_snapshot(later))
    recovered.reported_success and repeated.reported_success and
    START_MEMBERS.forall(u =>
      recovered.settings.user_cycle_start_spend.get(u) == 8 and
      recovered.litellm.members.get(u).max_budget == Some(38) and
      repeated.settings.user_cycle_start_spend.get(u) == 8 and
      repeated.litellm.members.get(u).max_budget == Some(38))
  })
}
