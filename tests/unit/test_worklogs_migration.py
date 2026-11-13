from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

migration = import_module('migrations.versions.216_add_worklogs')

FACTORY_ID = '9b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d'


def _tables(connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def _insert_worklog(connection, org_id, *, slug='default', name='Default', default):
    connection.execute(
        text("""
            INSERT INTO worklogs (id, org_id, factory_id, slug, name, is_default)
            VALUES (gen_random_uuid(), :org, :factory, :slug, :name, :default)
        """),
        {
            'org': org_id,
            'factory': FACTORY_ID,
            'slug': slug,
            'name': name,
            'default': default,
        },
    )


def test_downgrade_drops_both_tables_and_upgrade_restores_them(engine):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert not {'worklogs', 'worklog_entries'} & _tables(connection)

            migration.upgrade()
            assert {'worklogs', 'worklog_entries'} <= _tables(connection)


def test_the_migration_follows_215(engine):
    assert migration.revision == '216'
    assert migration.down_revision == '215'


def test_a_factory_may_have_only_one_default_worklog(engine, create_org):
    org = create_org()
    with engine.begin() as connection:
        _insert_worklog(connection, org.id, default=True)
        with pytest.raises(IntegrityError, match='uq_worklogs_one_default'):
            with connection.begin_nested():
                _insert_worklog(
                    connection, org.id, slug='other', name='Other', default=True
                )
        _insert_worklog(connection, org.id, slug='more', name='More', default=False)


def test_entry_columns_reject_unknown_content_and_author_kinds(engine, create_org):
    org = create_org()
    with engine.begin() as connection:
        _insert_worklog(connection, org.id, default=True)
        worklog_id = connection.execute(text('SELECT id FROM worklogs')).scalar_one()

        def insert(content_type, author_kind):
            with connection.begin_nested():
                connection.execute(
                    text("""
                        INSERT INTO worklog_entries
                            (worklog_id, content_type, author_kind, body)
                        VALUES (:worklog, :content_type, :author_kind, 'x')
                    """),
                    {
                        'worklog': worklog_id,
                        'content_type': content_type,
                        'author_kind': author_kind,
                    },
                )

        insert('markdown', 'user')
        insert('json', 'automation')
        with pytest.raises(DBAPIError, match='ck_worklog_entries_content_type'):
            insert('html', 'user')
        with pytest.raises(DBAPIError, match='ck_worklog_entries_author_kind'):
            insert('markdown', 'robot')
