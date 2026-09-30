# Budget Test API Implementation - Ready to Merge

## Changes Made

### 1. Configuration (✅ DONE)
- Added `test_mode` to `server/config.py` (SaaSServerConfig class)
- Reads from `TEST_MODE` environment variable
- Defaults to `false` for safety

### 2. Files to Create/Modify

The implementation is split into logical chunks that can be added to existing files.

## File: server/routes/org_models.py

Add these new response models to the existing file:

```python
# Add to imports at top
from datetime import datetime

# Add these model classes anywhere in the file

class BudgetMaintenanceTaskCreate(BaseModel):
    """Response when triggering budget maintenance."""
    task_id: int
    status: str
    created_at: datetime


class BudgetMaintenanceTaskStatus(BaseModel):
    """Budget maintenance task status."""
    id: int
    status: str  # PENDING, WORKING, COMPLETED, ERROR
    created_at: datetime
    updated_at: datetime
    info: dict[str, typing.Any] | None = None


class BudgetCycleStateResponse(BaseModel):
    """Budget cycle state for testing/verification."""
    cycle_start_at: datetime
    cycle_end_at: datetime
    cycle_start_spend: float
    user_cycle_start_spend: dict[str, float]
    litellm_last_sync_at: datetime | None
    litellm_last_sync_status: str | None
    litellm_last_sync_error: str | None
    litellm_last_spend_snapshot_at: datetime | None
    litellm_last_team_spend: float | None
    litellm_last_member_spend: dict[str, float]
    litellm_known_member_ids: list[str]


class RestoreCycleStateRequest(BaseModel):
    """Request to restore budget cycle state (test mode only)."""
    cycle_start_at: datetime
    cycle_start_spend: float
    user_cycle_start_spend: dict[str, float]
    litellm_last_sync_at: datetime | None
    litellm_last_sync_status: str | None
    litellm_last_sync_error: str | None
    litellm_last_spend_snapshot_at: datetime | None
    litellm_last_team_spend: float | None
    litellm_last_member_spend: dict[str, float]
    litellm_known_member_ids: list[str]


class SeedMembersRequest(BaseModel):
    """Request to seed test members (test mode only)."""
    count: int = Field(ge=1, le=100, description="Number of test members to create")


class SeedMembersResponse(BaseModel):
    """Response from seeding test members."""
    user_ids: list[str]
    created_at: datetime


class RemoveMembersRequest(BaseModel):
    """Request to remove test members (test mode only)."""
    user_ids: list[str]


class RemoveMembersResponse(BaseModel):
    """Response from removing test members."""
    deleted_count: int
    deleted_at: datetime
```

## File: server/routes/orgs.py

Add these routes after the existing budget routes (after line ~1450):

```python
# Import additional dependencies at top
from server.config import get_config
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from storage.org_budget_settings import OrgBudgetSettings
import json
import uuid

# Add after existing budget routes

def require_test_mode():
    """Dependency that verifies TEST_MODE is enabled."""
    config = get_config()
    if not config.test_mode:
        # Return 404 to hide existence of test endpoints in production
        raise HTTPException(status_code=404, detail="Not found")


@org_router.post(
    '/{org_id}/budgets/maintenance',
    response_model=BudgetMaintenanceTaskCreate,
)
async def trigger_budget_maintenance(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
    db=Depends(get_db),
) -> BudgetMaintenanceTaskCreate:
    """
    Trigger budget maintenance for an organization.
    
    Creates a maintenance task that will be processed by the background worker.
    Use the returned task_id to poll for completion via GET /budgets/maintenance/{task_id}.
    """
    logger.info(
        'Triggering budget maintenance',
        extra={'org_id': str(org_id), 'user_id': user_id},
    )
    
    # Check if maintenance already running
    processor_type = "server.maintenance_task_processor.org_budget_maintenance_processor.OrgBudgetMaintenanceProcessor"
    existing = db.query(MaintenanceTask).filter(
        MaintenanceTask.processor_type == processor_type,
        MaintenanceTask.status.in_([
            MaintenanceTaskStatus.PENDING,
            MaintenanceTaskStatus.WORKING
        ])
    ).first()
    
    if existing:
        # Check if it's for this org
        try:
            processor_json = json.loads(existing.processor_json)
            if str(org_id) in processor_json.get('org_ids', []):
                raise HTTPException(
                    status_code=409,
                    detail=f"Maintenance task {existing.id} already running for this organization"
                )
        except (json.JSONDecodeError, KeyError):
            pass
    
    # Create maintenance task
    task = MaintenanceTask(
        status=MaintenanceTaskStatus.PENDING,
        processor_type=processor_type,
        processor_json=json.dumps({"org_ids": [str(org_id)]}),
        delay=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    
    logger.info(
        'Budget maintenance task created',
        extra={'org_id': str(org_id), 'task_id': task.id, 'user_id': user_id},
    )
    
    return BudgetMaintenanceTaskCreate(
        task_id=task.id,
        status=task.status.value,
        created_at=task.created_at,
    )


@org_router.get(
    '/{org_id}/budgets/maintenance/{task_id}',
    response_model=BudgetMaintenanceTaskStatus,
)
async def get_budget_maintenance_status(
    org_id: UUID,
    task_id: int,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
    db=Depends(get_db),
) -> BudgetMaintenanceTaskStatus:
    """
    Get status of a budget maintenance task.
    
    Poll this endpoint to check if maintenance has completed.
    """
    task = db.query(MaintenanceTask).filter(MaintenanceTask.id == task_id).first()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Verify task belongs to this org (security check)
    try:
        processor_json = json.loads(task.processor_json)
        org_ids = processor_json.get('org_ids', [])
        if str(org_id) not in org_ids:
            raise HTTPException(status_code=404, detail="Task not found")
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(status_code=404, detail="Task not found")
    
    return BudgetMaintenanceTaskStatus(
        id=task.id,
        status=task.status.value,
        created_at=task.created_at,
        updated_at=task.updated_at,
        info=task.info,
    )


@org_router.get(
    '/{org_id}/budgets/cycle-state',
    response_model=BudgetCycleStateResponse,
)
async def get_budget_cycle_state(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
    db=Depends(get_db),
) -> BudgetCycleStateResponse:
    """
    Get current budget cycle state for an organization.
    
    Returns cycle dates, sync status, and spend information for verification in tests.
    """
    settings = db.query(OrgBudgetSettings).filter(
        OrgBudgetSettings.org_id == org_id
    ).first()
    
    if not settings:
        raise HTTPException(
            status_code=404,
            detail="Budget settings not found for this organization"
        )
    
    return BudgetCycleStateResponse(
        cycle_start_at=settings.cycle_start_at,
        cycle_end_at=settings.cycle_end_at,
        cycle_start_spend=float(settings.cycle_start_spend),
        user_cycle_start_spend={k: float(v) for k, v in settings.user_cycle_start_spend.items()},
        litellm_last_sync_at=settings.litellm_last_sync_at,
        litellm_last_sync_status=settings.litellm_last_sync_status,
        litellm_last_sync_error=settings.litellm_last_sync_error,
        litellm_last_spend_snapshot_at=settings.litellm_last_spend_snapshot_at,
        litellm_last_team_spend=float(settings.litellm_last_team_spend) if settings.litellm_last_team_spend else None,
        litellm_last_member_spend={k: float(v) for k, v in settings.litellm_last_member_spend.items()},
        litellm_known_member_ids=settings.litellm_known_member_ids,
    )


# Test-mode endpoints (require TEST_MODE=true)

@org_router.post(
    '/{org_id}/budgets/test/make-cycle-stale',
    dependencies=[Depends(require_test_mode)],
)
async def make_cycle_stale(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
    db=Depends(get_db),
):
    """
    Make the budget cycle stale (40 days old) for testing rollover.
    
    **TEST_MODE only** - Returns 404 when TEST_MODE=false.
    """
    logger.warning(
        'TEST: Making budget cycle stale',
        extra={'org_id': str(org_id), 'user_id': user_id},
    )
    
    result = db.execute(
        text("""
            UPDATE org_budget_settings
            SET cycle_start_at = CURRENT_TIMESTAMP - INTERVAL '40 days'
            WHERE org_id = :org_id
            RETURNING cycle_start_at
        """),
        {"org_id": str(org_id)}
    )
    row = result.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Budget settings not found"
        )
    
    db.commit()
    
    return {
        "success": True,
        "new_cycle_start": row[0],
        "days_moved": 40,
    }


@org_router.post(
    '/{org_id}/budgets/test/restore-cycle-state',
    dependencies=[Depends(require_test_mode)],
)
async def restore_cycle_state(
    org_id: UUID,
    request: RestoreCycleStateRequest,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
    db=Depends(get_db),
):
    """
    Restore budget cycle to a previous state.
    
    **TEST_MODE only** - Returns 404 when TEST_MODE=false.
    Used to clean up after destructive tests.
    """
    logger.warning(
        'TEST: Restoring budget cycle state',
        extra={'org_id': str(org_id), 'user_id': user_id},
    )
    
    result = db.execute(
        text("""
            UPDATE org_budget_settings
            SET cycle_start_at = :cycle_start_at,
                cycle_start_spend = :cycle_start_spend,
                user_cycle_start_spend = :user_cycle_start_spend,
                litellm_last_sync_at = :litellm_last_sync_at,
                litellm_last_sync_status = :litellm_last_sync_status,
                litellm_last_sync_error = :litellm_last_sync_error,
                litellm_last_spend_snapshot_at = :litellm_last_spend_snapshot_at,
                litellm_last_team_spend = :litellm_last_team_spend,
                litellm_last_member_spend = :litellm_last_member_spend,
                litellm_known_member_ids = :litellm_known_member_ids
            WHERE org_id = :org_id
        """),
        {
            "org_id": str(org_id),
            "cycle_start_at": request.cycle_start_at,
            "cycle_start_spend": request.cycle_start_spend,
            "user_cycle_start_spend": json.dumps(request.user_cycle_start_spend),
            "litellm_last_sync_at": request.litellm_last_sync_at,
            "litellm_last_sync_status": request.litellm_last_sync_status,
            "litellm_last_sync_error": request.litellm_last_sync_error,
            "litellm_last_spend_snapshot_at": request.litellm_last_spend_snapshot_at,
            "litellm_last_team_spend": request.litellm_last_team_spend,
            "litellm_last_member_spend": json.dumps(request.litellm_last_member_spend),
            "litellm_known_member_ids": json.dumps(request.litellm_known_member_ids),
        }
    )
    
    if result.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="Budget settings not found"
        )
    
    db.commit()
    
    return {
        "success": True,
        "restored_at": datetime.now(timezone.utc),
    }


@org_router.post(
    '/{org_id}/test/seed-members',
    response_model=SeedMembersResponse,
    dependencies=[Depends(require_test_mode)],
)
async def seed_test_members(
    org_id: UUID,
    request: SeedMembersRequest,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
    db=Depends(get_db),
) -> SeedMembersResponse:
    """
    Create test members for pagination testing.
    
    **TEST_MODE only** - Returns 404 when TEST_MODE=false.
    Creates throwaway users that only exist in OpenHands DB (not LiteLLM).
    """
    logger.warning(
        'TEST: Seeding test members',
        extra={'org_id': str(org_id), 'count': request.count, 'user_id': user_id},
    )
    
    # Get member role
    from storage.role import Role
    from storage.user import User
    from storage.org_member import OrgMember
    
    member_role = db.query(Role).filter(Role.name == "member").first()
    if not member_role:
        raise HTTPException(status_code=500, detail="Member role not found")
    
    user_ids = []
    try:
        for i in range(request.count):
            user_id_val = str(uuid.uuid4())
            email = f"e2e-pagination-{user_id_val}@example.invalid"
            
            # Create user
            user = User(
                id=UUID(user_id_val),
                current_org_id=org_id,
                email=email,
            )
            db.add(user)
            
            # Create org member
            org_member = OrgMember(
                org_id=org_id,
                user_id=UUID(user_id_val),
                role_id=member_role.id,
                _llm_api_key=f"e2e-pagination-{user_id_val}",
                agent_settings_diff={},
                conversation_settings_diff={},
                has_custom_llm_api_key=False,
                managed_llm_key_ownership_version=1,
            )
            db.add(org_member)
            
            user_ids.append(user_id_val)
        
        db.commit()
        
        logger.warning(
            'TEST: Test members seeded',
            extra={'org_id': str(org_id), 'user_ids': user_ids},
        )
        
        return SeedMembersResponse(
            user_ids=user_ids,
            created_at=datetime.now(timezone.utc),
        )
        
    except Exception as e:
        db.rollback()
        # Clean up any partially created users
        if user_ids:
            db.execute(
                text("DELETE FROM org_member WHERE org_id = :org_id AND user_id = ANY(:user_ids)"),
                {"org_id": str(org_id), "user_ids": user_ids}
            )
            db.execute(
                text('DELETE FROM "user" WHERE id = ANY(:user_ids)'),
                {"user_ids": user_ids}
            )
            db.commit()
        raise HTTPException(status_code=500, detail=str(e))


@org_router.delete(
    '/{org_id}/test/members',
    response_model=RemoveMembersResponse,
    dependencies=[Depends(require_test_mode)],
)
async def remove_test_members(
    org_id: UUID,
    request: RemoveMembersRequest,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
    db=Depends(get_db),
) -> RemoveMembersResponse:
    """
    Remove test members created by seed-members endpoint.
    
    **TEST_MODE only** - Returns 404 when TEST_MODE=false.
    Cleans up throwaway test users.
    """
    if not request.user_ids:
        return RemoveMembersResponse(deleted_count=0, deleted_at=datetime.now(timezone.utc))
    
    logger.warning(
        'TEST: Removing test members',
        extra={'org_id': str(org_id), 'count': len(request.user_ids), 'user_id': user_id},
    )
    
    # Delete org_member entries
    db.execute(
        text("DELETE FROM org_member WHERE org_id = :org_id AND user_id = ANY(:user_ids)"),
        {"org_id": str(org_id), "user_ids": request.user_ids}
    )
    
    # Delete user entries
    result = db.execute(
        text('DELETE FROM "user" WHERE id = ANY(:user_ids) RETURNING id'),
        {"user_ids": request.user_ids}
    )
    deleted_count = len(result.fetchall())
    
    db.commit()
    
    logger.warning(
        'TEST: Test members removed',
        extra={'org_id': str(org_id), 'deleted_count': deleted_count},
    )
    
    return RemoveMembersResponse(
        deleted_count=deleted_count,
        deleted_at=datetime.now(timezone.utc),
    )
```

## Testing

After adding these endpoints:

1. **Local Testing**:
```bash
export TEST_MODE=true
python saas_server.py

# Test production endpoints
curl -X POST http://localhost:8000/api/organizations/$ORG_ID/budgets/maintenance \
  -H "Authorization: Bearer $TOKEN"

# Test test-mode endpoints
curl -X POST http://localhost:8000/api/organizations/$ORG_ID/budgets/test/make-cycle-stale \
  -H "Authorization: Bearer $TOKEN"
```

2. **E2E Tests**:
- Update OpenHands-Cloud e2e tests to use these endpoints
- See `docs/budget-test-migration-guide.md` in OpenHands-Cloud repo

## Deployment

1. Deploy backend to beta with `TEST_MODE=true`
2. Update e2e tests in OpenHands-Cloud repo
3. Verify tests pass in ReportPortal
4. Deploy to production with `TEST_MODE=false`

## See Also

- PR #1325 in OpenHands-Cloud (specification)
- `/workspace/DATABASE_VALUE_ANALYSIS.md` (why we need this)
- OpenHands-Cloud `docs/budget-test-api-README.md` (complete overview)
