// -*- mode: Bluespec; -*-

// Sandbox resume session-key ordering: PR #333
//
// A remote runtime rotates its session API key when it resumes. The app server
// stages the new key hash in its request-scoped SQLAlchemy session. A second
// request can use the new raw key only after that hash is committed.
//
// The original implementation registered Git restoration as a FastAPI
// BackgroundTask. Starlette runs that task before yield-dependency teardown, so
// the database commit and release of request-scoped dependencies waited for the
// resumed runtime and Git configuration. This creates the buggy ordering:
//
//   rotate key -> stage hash -> return response -> start restore -> commit hash
//
// PR #333 now commits immediately, then schedules a detached asyncio task that
// creates fresh dependencies:
//
//   rotate key -> stage hash -> commit hash -> schedule restore -> return response
//
// The model contrasts both orderings and checks the externally relevant safety
// property: once resume returns, the rotated key is committed and can authorize
// a concurrent app-server secret request.
//
// Reproduce:
//   quint test spec/sandbox_resume_session_key.qnt
//   quint run spec/sandbox_resume_session_key.qnt \
//     --main=sandbox_resume_session_key --step=stepBuggy \
//     --invariant=newKeyWorksAfterResponse --max-steps=8 --max-samples=20000
//   quint run spec/sandbox_resume_session_key.qnt \
//     --main=sandbox_resume_session_key --step=stepFixed \
//     --invariant=newKeyWorksAfterResponse --max-steps=8 --max-samples=20000
//   quint verify spec/sandbox_resume_session_key.qnt \
//     --main=sandbox_resume_session_key --step=stepFixed \
//     --invariant=newKeyWorksAfterResponse --max-steps=8

module sandbox_resume_session_key {

  // The committed hash accepted by app-server session authentication.
  var committedHash: str
  // The hash staged in the resume request's transaction.
  var stagedHash: str
  // The raw key exposed by the resumed runtime.
  var runtimeKey: str
  var responseReturned: bool
  var restoreScheduled: bool
  var restoreStarted: bool
  var gitRestored: bool
  // Whether the restore task captured request-scoped dependencies.
  var restoreUsesRequestResources: bool

  action unchangedExcept(
    nextCommittedHash: str,
    nextStagedHash: str,
    nextRuntimeKey: str,
    nextResponseReturned: bool,
    nextRestoreScheduled: bool,
    nextRestoreStarted: bool,
    nextGitRestored: bool,
    nextRestoreUsesRequestResources: bool,
  ): bool = all {
    committedHash' = nextCommittedHash,
    stagedHash' = nextStagedHash,
    runtimeKey' = nextRuntimeKey,
    responseReturned' = nextResponseReturned,
    restoreScheduled' = nextRestoreScheduled,
    restoreStarted' = nextRestoreStarted,
    gitRestored' = nextGitRestored,
    restoreUsesRequestResources' = nextRestoreUsesRequestResources,
  }

  action init: bool = unchangedExcept(
    "none", "none", "old", false, false, false, false, false
  )

  /// The runtime rotates its key and the ORM stages its hash in the transaction.
  action resumeRuntime: bool = all {
    runtimeKey == "old",
    unchangedExcept(
      committedHash, "new", "new", responseReturned, restoreScheduled,
      restoreStarted, gitRestored, restoreUsesRequestResources
    ),
  }

  /// Fixed ordering: make the new key usable before any background work.
  action commitBeforeRestore: bool = all {
    stagedHash == "new",
    committedHash != "new",
    unchangedExcept(
      "new", stagedHash, runtimeKey, responseReturned, restoreScheduled,
      restoreStarted, gitRestored, restoreUsesRequestResources
    ),
  }

  /// The old FastAPI BackgroundTask captured request-scoped services.
  action scheduleBuggyRestore: bool = all {
    stagedHash == "new",
    not(restoreScheduled),
    unchangedExcept(
      committedHash, stagedHash, runtimeKey, responseReturned, true,
      restoreStarted, gitRestored, true
    ),
  }

  /// The fixed detached task receives only value data and opens fresh services.
  action scheduleFixedRestore: bool = all {
    committedHash == "new",
    not(restoreScheduled),
    unchangedExcept(
      committedHash, stagedHash, runtimeKey, responseReturned, true,
      restoreStarted, gitRestored, false
    ),
  }

  action returnResponse: bool = all {
    restoreScheduled,
    not(responseReturned),
    unchangedExcept(
      committedHash, stagedHash, runtimeKey, true, restoreScheduled,
      restoreStarted, gitRestored, restoreUsesRequestResources
    ),
  }

  action startRestore: bool = all {
    restoreScheduled,
    not(restoreStarted),
    unchangedExcept(
      committedHash, stagedHash, runtimeKey, responseReturned, restoreScheduled,
      true, gitRestored, restoreUsesRequestResources
    ),
  }

  action finishRestore: bool = all {
    restoreStarted,
    not(gitRestored),
    unchangedExcept(
      committedHash, stagedHash, runtimeKey, responseReturned, restoreScheduled,
      restoreStarted, true, restoreUsesRequestResources
    ),
  }

  /// Buggy yield-dependency teardown commits only after background work finishes.
  action commitAfterBuggyRestore: bool = all {
    gitRestored,
    restoreUsesRequestResources,
    committedHash != "new",
    unchangedExcept(
      "new", stagedHash, runtimeKey, responseReturned, restoreScheduled,
      restoreStarted, gitRestored, restoreUsesRequestResources
    ),
  }

  action stutter: bool = unchangedExcept(
    committedHash, stagedHash, runtimeKey, responseReturned, restoreScheduled,
    restoreStarted, gitRestored, restoreUsesRequestResources
  )

  action stepBuggy: bool = any {
    resumeRuntime,
    scheduleBuggyRestore,
    returnResponse,
    startRestore,
    finishRestore,
    commitAfterBuggyRestore,
    stutter,
  }

  action stepFixed: bool = any {
    resumeRuntime,
    commitBeforeRestore,
    scheduleFixedRestore,
    returnResponse,
    startRestore,
    finishRestore,
    stutter,
  }

  /// SAFETY: after a successful resume response, the runtime's current key is
  /// accepted by app-server session auth. Violated by the old ordering; holds
  /// under the PR's commit-before-schedule ordering.
  val newKeyWorksAfterResponse: bool =
    responseReturned implies committedHash == runtimeKey

  /// SAFETY: restore never starts before the rotated key is committed.
  val restoreStartsAfterCommit: bool =
    restoreStarted implies committedHash == runtimeKey

  /// SAFETY: detached restore work does not capture request dependencies.
  val restoreOwnsFreshResources: bool =
    restoreScheduled implies not(restoreUsesRequestResources)

  /// WITNESS (negated): the old flow can return while the new key still fails
  /// app-server authentication. A violation is the expected reachability proof.
  val canRejectNewKeyAfterResponse: bool =
    not(responseReturned and committedHash != runtimeKey)

  /// WITNESS (negated): the fixed flow can complete Git restoration.
  val canRestoreGitIdentity: bool = not(gitRestored)

  run buggyRaceTest =
    init
      .then(resumeRuntime)
      .then(scheduleBuggyRestore)
      .then(returnResponse)
      .then(all {
        assert(not(newKeyWorksAfterResponse)),
        committedHash' = committedHash,
        stagedHash' = stagedHash,
        runtimeKey' = runtimeKey,
        responseReturned' = responseReturned,
        restoreScheduled' = restoreScheduled,
        restoreStarted' = restoreStarted,
        gitRestored' = gitRestored,
        restoreUsesRequestResources' = restoreUsesRequestResources,
      })

  run fixedOrderingTest =
    init
      .then(resumeRuntime)
      .then(commitBeforeRestore)
      .then(scheduleFixedRestore)
      .then(returnResponse)
      .then(startRestore)
      .then(finishRestore)
      .then(all {
        assert(newKeyWorksAfterResponse),
        assert(restoreStartsAfterCommit),
        assert(restoreOwnsFreshResources),
        assert(gitRestored),
        committedHash' = committedHash,
        stagedHash' = stagedHash,
        runtimeKey' = runtimeKey,
        responseReturned' = responseReturned,
        restoreScheduled' = restoreScheduled,
        restoreStarted' = restoreStarted,
        gitRestored' = gitRestored,
        restoreUsesRequestResources' = restoreUsesRequestResources,
      })
}
