"""Exercise the latest downgrade, including the explicit ownership fence."""

from alembic import command
from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory

from migrations.exceptions import BudgetOwnershipDowngradeError


def current_heads(config: Config) -> tuple[str, ...]:
    heads: tuple[str, ...] = ()

    def observe(revisions, context):
        nonlocal heads
        heads = tuple(revisions)
        return []

    script = ScriptDirectory.from_config(config)
    with EnvironmentContext(config, script, fn=observe):
        script.run_env()
    return heads


def check_roundtrip(config: Config) -> None:
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    if current_heads(config) != (head,):
        raise AssertionError('The test database must already be at migration head')

    if head == '163':
        try:
            command.downgrade(config, '-1')
        except BudgetOwnershipDowngradeError:
            pass
        else:
            raise AssertionError('The ownership downgrade fence did not refuse')
        expected = ('163',)
    else:
        command.downgrade(config, '-1')
        revision = script.get_revision(head)
        assert revision is not None
        expected = (revision.down_revision,) if revision.down_revision else ()

    if current_heads(config) != expected:
        raise AssertionError('Downgrade left an unexpected database revision')
    command.upgrade(config, 'head')
    if current_heads(config) != (head,):
        raise AssertionError('Re-upgrade did not restore migration head')


if __name__ == '__main__':
    check_roundtrip(Config('alembic.ini'))
    print('Migration round-trip contract passed')
