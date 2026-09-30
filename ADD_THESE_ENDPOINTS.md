# Simple Budget Maintenance API - Just 2 Endpoints

## Add to `server/routes/org_models.py`

```python
class BudgetMaintenanceTaskResponse(BaseModel):
    """Response when triggering or polling budget maintenance."""
    task_id: int
    status: str  # PENDING, WORKING, COMPLETED, ERROR
    created_at: datetime
    updated_at: datetime | None = None
    info: dict[str, typing.Any] | None = None
```

## Add to `server/routes/orgs.py`

Add after line 1455 (after `_rejected_budget_change` function):

```python
# Add these imports at top of file
import json
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus

# Add these two endpoints

@org_router.post(
    '/{org_id}/budgets/maintenance',
    response_model=BudgetMaintenanceTaskResponse,
)
async def trigger_budget_maintenance(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
    db=Depends(get_db),
) -> BudgetMaintenanceTaskResponse:
    """Trigger budget maintenance for an organization.
    
    Returns task_id - poll GET /budgets/maintenance/{task_id} for status.
    """
    logger.info('Triggering budget maintenance', extra={'org_id': str(org_id), 'user_id': user_id})
    
    # Check if already running
    processor_type = "server.maintenance_task_processor.org_budget_maintenance_processor.OrgBudgetMaintenanceProcessor"
    existing = db.query(MaintenanceTask).filter(
        MaintenanceTask.processor_type == processor_type,
        MaintenanceTask.status.in_([MaintenanceTaskStatus.PENDING, MaintenanceTaskStatus.WORKING])
    ).first()
    
    if existing:
        processor_json = json.loads(existing.processor_json)
        if str(org_id) in processor_json.get('org_ids', []):
            raise HTTPException(409, f"Maintenance already running (task {existing.id})")
    
    # Create task
    task = MaintenanceTask(
        status=MaintenanceTaskStatus.PENDING,
        processor_type=processor_type,
        processor_json=json.dumps({"org_ids": [str(org_id)]}),
        delay=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    
    return BudgetMaintenanceTaskResponse(
        task_id=task.id,
        status=task.status.value,
        created_at=task.created_at,
    )


@org_router.get(
    '/{org_id}/budgets/maintenance/{task_id}',
    response_model=BudgetMaintenanceTaskResponse,
)
async def get_budget_maintenance_status(
    org_id: UUID,
    task_id: int,
    user_id: str = Depends(require_permission(Permission.EDIT_ORG_SETTINGS)),
    db=Depends(get_db),
) -> BudgetMaintenanceTaskResponse:
    """Poll budget maintenance task status."""
    task = db.query(MaintenanceTask).filter(MaintenanceTask.id == task_id).first()
    
    if not task:
        raise HTTPException(404, "Task not found")
    
    # Verify belongs to this org
    processor_json = json.loads(task.processor_json)
    if str(org_id) not in processor_json.get('org_ids', []):
        raise HTTPException(404, "Task not found")
    
    return BudgetMaintenanceTaskResponse(
        task_id=task.id,
        status=task.status.value,
        created_at=task.created_at,
        updated_at=task.updated_at,
        info=task.info,
    )
```

## That's It!

Tests can now:
1. **Trigger maintenance**: POST /budgets/maintenance
2. **Poll until done**: GET /budgets/maintenance/{task_id}
3. **Verify results**: Use existing GET /budgets endpoint

No test-mode endpoints needed. No state manipulation. Just trigger and observe.
