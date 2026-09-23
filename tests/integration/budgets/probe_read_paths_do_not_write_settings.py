from __future__ import annotations

import pytest
from sqlalchemy import func, select

from storage.org_budget_settings import OrgBudgetSettings
from tests.integration.budgets.adapter import BudgetTestAdapter


async def _settings_row_count(adapter: BudgetTestAdapter) -> int:
    result = await adapter.session.execute(
        select(func.count())
        .select_from(OrgBudgetSettings)
        .where(OrgBudgetSettings.org_id == adapter.org_id)
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_read_paths_do_not_create_settings_row(
    budget_adapter: BudgetTestAdapter,
) -> None:
    """Reading an unconfigured org must never persist a settings row.

    ``_get_or_create_settings`` inserts an ``OrgBudgetSettings`` row as a side
    effect of a read, so before this fix every read entry point had to remember
    the personal-workspace guard by hand and any read on an org that had never
    configured budgets silently wrote the row migration 148 exists to delete.
    The fix routes the read paths through ``_get_settings_for_read``, which
    returns a transient defaults object that is never added to the session.

    This drives all three read paths against real LiteLLM and a migrated
    PostgreSQL: the org's team exists, so ``get_budget_state`` and
    ``get_user_budget_row`` take a real financial snapshot whose
    ``_cache_financial_snapshot`` -> ``store.flush()`` would persist a settings
    row that was added to the session. It reproduces the regression reported on
    PR #480: pre-fix a read leaves a settings row behind, post-fix it does not.
    """
    adapter = budget_adapter

    # An org that has never enabled budgets carries no settings row.
    assert await _settings_row_count(adapter) == 0

    # Exercise every read entry point that used to create-on-read.
    await adapter.service.get_budget_state(adapter.org_id)
    await adapter.service.get_user_budget_row(adapter.org_id, adapter.user_ids[0])
    await adapter.service.get_reconciliation_state(adapter.org_id)
    await adapter.session.commit()

    assert await _settings_row_count(adapter) == 0, (
        'read paths must not persist an OrgBudgetSettings row for an '
        'unconfigured org'
    )

    # A write path still materialises the row and its default thresholds.
    await adapter.configure_budget(5, 3)
    assert await _settings_row_count(adapter) == 1, (
        'configuring budgets must create the settings row'
    )
