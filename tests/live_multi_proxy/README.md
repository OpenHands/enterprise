# Two-process native admission tests

This opt-in suite uses two real LiteLLM processes sharing PostgreSQL and
coordination Redis. Inference has a synthetic response with nonzero token cost;
native authentication, admission, counters and cache propagation are not mocked.
It is separate from the single-process controller suite in `tests/live`.

Use only the disposable Compose project below. No customer or shared installation
is supported: the tests verify both project labels and exact loopback bindings,
then create and remove unique native users/teams. The database uses temporary
storage; taking the stack down removes all remaining fixture state.

```sh
BUDGET_TEST_LITELLM_IMAGE=<verified-image-id-or-digest> \
  docker compose -p budget-control-cache -f tests/live_multi_proxy/compose.yaml up -d
# Wait for both /health/readiness endpoints on 127.0.0.1:41500 and :41501.
docker exec budget-control-cache-redis-1 redis-cli --raw \
  PUBSUB NUMSUB litellm_proxy.auth_cache_invalidation
# The candidate's native auth invalidation channel must report two subscribers.
TMPDIR=/tmp uv run pytest tests/live_multi_proxy -q --tb=short
BUDGET_TEST_LITELLM_IMAGE=<same-image> \
  docker compose -p budget-control-cache -f tests/live_multi_proxy/compose.yaml down
```

The configuration uses the candidate's `general_settings.coordination_redis`;
it is not a promise that older releases support the same configuration. Inspect
the exact image version and startup logs. Concurrent fresh migrations can fail
one process; retain that evidence and investigate/retry only the isolated test
process. A manual test retry does not certify automatic deployment recovery.

Each case warms both processes, changes policy through the primary, and requires
the same key to observe the new admission decision at the peer and primary.
Cases cover member-cap restoration, team unblocking and team blocking through
both the update route and dedicated block/unblock routes. The fixture requires
both Redis invalidation subscribers before making any test writes. A passing
management readback or eventual cache expiry does not pass the initial-request
assertion. Missing prerequisites fail; these tests are not unit-suite skips.

For diagnosis, `BUDGET_TEST_CACHE_OBSERVE_SECONDS=75` records subsequent admission
results after a failed first request. The failure remains a failure even if the
cache expires during observation. The default observation period is one second;
the limit is 90 seconds. Long observations generate additional *synthetic* paid
inference, not real-provider charges. Such a run characterizes a consistency
window; it does not alone decide the acceptable product consistency contract.
