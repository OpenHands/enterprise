module org_budget {
  pure val noOverride = 0
  pure val positiveOverride = 1
  pure val disabledOverride = 2

  pure val syncNever = 0
  pure val syncPending = 1
  pure val syncVerified = 2
  pure val syncFailed = 3

  type State = {
    budgetEnabled: bool,
    organizationLimit: int,
    defaultUserLimit: int,
    overrideModeA: int,
    overrideModeB: int,
    overrideLimitA: int,
    overrideLimitB: int,
    organizationBaseline: int,
    userBaselineA: int,
    userBaselineB: int,
    policyVersion: int,
    verifiedPolicyVersion: int,
    syncStatus: int,
    enforcedTeamCap: int,
    enforcedMemberCapA: int,
    enforcedMemberCapB: int,
    recordedTeamSpend: int,
    recordedMemberSpendA: int,
    recordedMemberSpendB: int,
    admittedRequestsA: int,
    admittedRequestsB: int,
    rejectedRequestsA: int,
    rejectedRequestsB: int,
    providerCallsA: int,
    providerCallsB: int,
  }

  var state: State

  pure def expectedMemberCap(
    budgetOn: bool,
    defaultLimit: int,
    mode: int,
    limit: int,
    baseline: int,
  ): int =
    if (not(budgetOn) or mode == disabledOverride) -1
    else if (mode == positiveOverride) baseline + limit
    else baseline + defaultLimit

  val expectedTeamCap =
    if (state.budgetEnabled)
      state.organizationBaseline + state.organizationLimit
    else -1

  val expectedMemberCapA = expectedMemberCap(
    state.budgetEnabled,
    state.defaultUserLimit,
    state.overrideModeA,
    state.overrideLimitA,
    state.userBaselineA,
  )

  val expectedMemberCapB = expectedMemberCap(
    state.budgetEnabled,
    state.defaultUserLimit,
    state.overrideModeB,
    state.overrideLimitB,
    state.userBaselineB,
  )

  pure def belowCap(spend: int, cap: int): bool = cap < 0 or spend < cap

  val policyIsVerified =
    state.syncStatus == syncVerified
      and state.verifiedPolicyVersion == state.policyVersion

  val canAdmitA =
    policyIsVerified
      and belowCap(state.recordedTeamSpend, state.enforcedTeamCap)
      and belowCap(state.recordedMemberSpendA, state.enforcedMemberCapA)

  val canAdmitB =
    policyIsVerified
      and belowCap(state.recordedTeamSpend, state.enforcedTeamCap)
      and belowCap(state.recordedMemberSpendB, state.enforcedMemberCapB)

  action init = state' = {
    budgetEnabled: false,
    organizationLimit: 1,
    defaultUserLimit: 1,
    overrideModeA: noOverride,
    overrideModeB: noOverride,
    overrideLimitA: 1,
    overrideLimitB: 1,
    organizationBaseline: 0,
    userBaselineA: 0,
    userBaselineB: 0,
    policyVersion: 0,
    verifiedPolicyVersion: -1,
    syncStatus: syncNever,
    enforcedTeamCap: -1,
    enforcedMemberCapA: -1,
    enforcedMemberCapB: -1,
    recordedTeamSpend: 0,
    recordedMemberSpendA: 0,
    recordedMemberSpendB: 0,
    admittedRequestsA: 0,
    admittedRequestsB: 0,
    rejectedRequestsA: 0,
    rejectedRequestsB: 0,
    providerCallsA: 0,
    providerCallsB: 0,
  }

  action configureBudget(orgLimit: int, userLimit: int): bool = state' = {
    ...state,
    budgetEnabled: true,
    organizationLimit: orgLimit,
    defaultUserLimit: userLimit,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action disableBudget = state' = {
    ...state,
    budgetEnabled: false,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action setOrganizationLimit(limit: int): bool = state' = {
    ...state,
    organizationLimit: limit,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action setDefaultUserLimit(limit: int): bool = state' = {
    ...state,
    defaultUserLimit: limit,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action setPositiveOverrideA(limit: int): bool = state' = {
    ...state,
    overrideModeA: positiveOverride,
    overrideLimitA: limit,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action setPositiveOverrideB(limit: int): bool = state' = {
    ...state,
    overrideModeB: positiveOverride,
    overrideLimitB: limit,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action disableUserLimitA = state' = {
    ...state,
    overrideModeA: disabledOverride,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action disableUserLimitB = state' = {
    ...state,
    overrideModeB: disabledOverride,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action deleteOverrideA = state' = {
    ...state,
    overrideModeA: noOverride,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action deleteOverrideB = state' = {
    ...state,
    overrideModeB: noOverride,
    policyVersion: state.policyVersion + 1,
    syncStatus: syncPending,
  }

  action synchronizeSuccessfully = state' = {
    ...state,
    enforcedTeamCap: expectedTeamCap,
    enforcedMemberCapA: expectedMemberCapA,
    enforcedMemberCapB: expectedMemberCapB,
    verifiedPolicyVersion: state.policyVersion,
    syncStatus: syncVerified,
  }

  action synchronizeTeamOnly = state' = {
    ...state,
    enforcedTeamCap: expectedTeamCap,
    syncStatus: syncFailed,
  }

  action synchronizeMemberAOnly = state' = {
    ...state,
    enforcedMemberCapA: expectedMemberCapA,
    syncStatus: syncFailed,
  }

  action synchronizeWithFailure = state' = { ...state, syncStatus: syncFailed }

  action sendRequestA =
    state' = if (canAdmitA) {
      ...state,
      recordedTeamSpend: state.recordedTeamSpend + 1,
      recordedMemberSpendA: state.recordedMemberSpendA + 1,
      admittedRequestsA: state.admittedRequestsA + 1,
      providerCallsA: state.providerCallsA + 1,
    } else {
      ...state,
      rejectedRequestsA: state.rejectedRequestsA + 1,
    }

  action sendRequestB =
    state' = if (canAdmitB) {
      ...state,
      recordedTeamSpend: state.recordedTeamSpend + 1,
      recordedMemberSpendB: state.recordedMemberSpendB + 1,
      admittedRequestsB: state.admittedRequestsB + 1,
      providerCallsB: state.providerCallsB + 1,
    } else {
      ...state,
      rejectedRequestsB: state.rejectedRequestsB + 1,
    }

  action step = any {
    configureBudget(1, 1),
    configureBudget(2, 1),
    configureBudget(3, 2),
    disableBudget,
    setOrganizationLimit(1),
    setOrganizationLimit(3),
    setDefaultUserLimit(1),
    setDefaultUserLimit(2),
    setPositiveOverrideA(1),
    setPositiveOverrideA(3),
    setPositiveOverrideB(1),
    setPositiveOverrideB(3),
    disableUserLimitA,
    disableUserLimitB,
    deleteOverrideA,
    deleteOverrideB,
    synchronizeSuccessfully,
    synchronizeTeamOnly,
    synchronizeMemberAOnly,
    synchronizeWithFailure,
    sendRequestA,
    sendRequestB,
  }

  val P1_organizationCapIsAbsolute =
    (state.enforcedTeamCap >= 0
      and state.recordedTeamSpend >= state.enforcedTeamCap)
      implies (not(canAdmitA) and not(canAdmitB))

  val P2_defaultUserCapApplies =
    ((state.overrideModeA == noOverride
      and state.enforcedMemberCapA >= 0
      and state.recordedMemberSpendA >= state.enforcedMemberCapA)
      implies not(canAdmitA))
    and ((state.overrideModeB == noOverride
      and state.enforcedMemberCapB >= 0
      and state.recordedMemberSpendB >= state.enforcedMemberCapB)
      implies not(canAdmitB))

  val P3_positiveOverrideReplacesDefault =
    not(policyIsVerified and state.budgetEnabled)
      or ((state.overrideModeA != positiveOverride
          or state.enforcedMemberCapA == state.userBaselineA + state.overrideLimitA)
        and (state.overrideModeB != positiveOverride
          or state.enforcedMemberCapB == state.userBaselineB + state.overrideLimitB))

  val P4_disabledOverrideRemovesOnlyMemberCap =
    not(policyIsVerified)
      or ((state.overrideModeA != disabledOverride
          or state.enforcedMemberCapA == -1)
        and (state.overrideModeB != disabledOverride
          or state.enforcedMemberCapB == -1))

  val P5_verifiedMeansExactAgreement =
    not(policyIsVerified)
      or (state.enforcedTeamCap == expectedTeamCap
        and state.enforcedMemberCapA == expectedMemberCapA
        and state.enforcedMemberCapB == expectedMemberCapB)

  val P6_staleVerificationIsInvalid =
    state.syncStatus != syncVerified
      or state.verifiedPolicyVersion == state.policyVersion

  val P7_unverifiedPolicyFailsClosed =
    policyIsVerified or (not(canAdmitA) and not(canAdmitB))

  val P8_partialSynchronizationIsNotSuccess =
    state.syncStatus != syncVerified or P5_verifiedMeansExactAgreement

  val P9_successfulRetryRepairsDivergence = P5_verifiedMeansExactAgreement

  val P10_rejectionHasNoAccountingSideEffects =
    state.recordedTeamSpend
      == state.recordedMemberSpendA + state.recordedMemberSpendB

  val P11_admissionHasExactlyOneCharge =
    state.recordedMemberSpendA == state.providerCallsA
      and state.recordedMemberSpendB == state.providerCallsB
      and state.recordedTeamSpend == state.providerCallsA + state.providerCallsB

  val P12_spendIsNonnegativeAndMonotonicByConstruction =
    state.recordedTeamSpend >= 0
      and state.recordedMemberSpendA >= 0
      and state.recordedMemberSpendB >= 0

  val P13_userAccountingIsIsolated =
    state.admittedRequestsA == state.recordedMemberSpendA
      and state.admittedRequestsB == state.recordedMemberSpendB

  val P14_synchronizationIsIdempotent =
    not(policyIsVerified) or P5_verifiedMeansExactAgreement

  val P15_rejectedRetriesRemainHarmless =
    state.providerCallsA == state.admittedRequestsA
      and state.providerCallsB == state.admittedRequestsB

  val allProperties = all {
    P1_organizationCapIsAbsolute,
    P2_defaultUserCapApplies,
    P3_positiveOverrideReplacesDefault,
    P4_disabledOverrideRemovesOnlyMemberCap,
    P5_verifiedMeansExactAgreement,
    P6_staleVerificationIsInvalid,
    P7_unverifiedPolicyFailsClosed,
    P8_partialSynchronizationIsNotSuccess,
    P9_successfulRetryRepairsDivergence,
    P10_rejectionHasNoAccountingSideEffects,
    P11_admissionHasExactlyOneCharge,
    P12_spendIsNonnegativeAndMonotonicByConstruction,
    P13_userAccountingIsIsolated,
    P14_synchronizationIsIdempotent,
    P15_rejectedRetriesRemainHarmless,
  }
}
