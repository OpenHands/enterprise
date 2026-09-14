"""Reuse isolated PostgreSQL fixtures for opt-in local service integration tests."""

from tests.unit.conftest import async_engine as async_engine_fixture
from tests.unit.conftest import async_session_maker as async_session_maker_fixture
from tests.unit.conftest import create_org as create_org_fixture
from tests.unit.conftest import create_user as create_user_fixture
from tests.unit.conftest import engine as engine_fixture
from tests.unit.conftest import postgres_server as postgres_server_fixture
from tests.unit.conftest import postgres_template as postgres_template_fixture
from tests.unit.conftest import session_maker as session_maker_fixture
from tests.unit.conftest import test_database as test_database_fixture

async_engine = async_engine_fixture
async_session_maker = async_session_maker_fixture
create_org = create_org_fixture
create_user = create_user_fixture
engine = engine_fixture
postgres_server = postgres_server_fixture
postgres_template = postgres_template_fixture
session_maker = session_maker_fixture
test_database = test_database_fixture
