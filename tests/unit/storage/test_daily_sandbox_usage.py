from sqlalchemy import inspect

from storage.daily_sandbox_usage import DailySandboxUsage
from storage.user import User


def test_user_has_nullable_daily_sandbox_limit(engine):
    column = next(
        column
        for column in inspect(engine).get_columns(User.__tablename__)
        if column['name'] == 'daily_sandbox_limit'
    )

    assert column['nullable'] is True


def test_daily_usage_has_unique_user_date_constraint(engine):
    constraints = inspect(engine).get_unique_constraints(
        DailySandboxUsage.__tablename__
    )

    assert any(
        constraint['column_names'] == ['user_id', 'usage_date']
        for constraint in constraints
    )
