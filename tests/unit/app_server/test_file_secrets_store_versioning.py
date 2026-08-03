import asyncio
import json
import multiprocessing
from queue import Empty
from unittest.mock import MagicMock

import pytest

from openhands.app_server.file_store.files import FileStore
from openhands.app_server.file_store.local import LocalFileStore
from openhands.app_server.file_store.memory import InMemoryFileStore
from openhands.app_server.integrations.provider import CustomSecret
from openhands.app_server.secrets.file_secrets_store import FileSecretsStore
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_store import CredentialVersionConflict

_NAME = 'CODEX_AUTH_JSON'
_ORIGINAL = '{"tokens":{"refresh_token":"r0"}}'
_ROTATED = '{"tokens":{"refresh_token":"r1"}}'


def _secrets(
    managed: str | None,
    other: str | None = None,
    description: str = 'Managed login',
) -> Secrets:
    values = {}
    if managed is not None:
        values[_NAME] = CustomSecret.from_value(
            {'secret': managed, 'description': description}
        )
    if other is not None:
        values['OTHER'] = CustomSecret.from_value({'secret': other, 'description': ''})
    return Secrets(custom_secrets=values)


def _replace_in_process(
    root: str,
    expected_version: str,
    value: str,
    results,
) -> None:
    async def replace() -> str:
        store = FileSecretsStore(LocalFileStore(root=root))
        return await store.replace_versioned(_NAME, expected_version, value)

    try:
        results.put(('ok', asyncio.run(replace())))
    except Exception as exc:
        results.put((type(exc).__name__, str(exc)))


@pytest.fixture
def store(tmp_path):
    return FileSecretsStore(LocalFileStore(root=str(tmp_path)))


@pytest.mark.asyncio
async def test_protected_write_creates_persistent_opaque_generation(store):
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')

    value, version = await store.load_versioned(_NAME)
    second = FileSecretsStore(store.file_store)

    assert value == _ORIGINAL
    assert version != _ORIGINAL
    assert await second.load_versioned(_NAME) == (_ORIGINAL, version)


@pytest.mark.asyncio
async def test_store_cannot_create_a_protected_credential(store):
    await store.store(_secrets(_ORIGINAL, 'other'))

    loaded = await store.load()
    assert loaded is not None
    assert _NAME not in loaded.custom_secrets
    assert loaded.custom_secrets['OTHER'].secret.get_secret_value() == 'other'


@pytest.mark.asyncio
async def test_store_cannot_overwrite_a_rotation_without_a_prior_load(store):
    """The guard must not depend on this instance having called ``load`` first.

    The baseline-tracking mechanism this replaces failed exactly here.
    """
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    _, version = await store.load_versioned(_NAME)
    successor = await store.replace_versioned(_NAME, version, _ROTATED)

    blind = FileSecretsStore(store.file_store)
    await blind.store(_secrets(_ORIGINAL, 'other'))

    assert await store.load_versioned(_NAME) == (_ROTATED, successor)


@pytest.mark.asyncio
async def test_whole_document_save_without_custom_secrets_keeps_the_credential(store):
    """Regression: ``invalidate_legacy_secrets_store`` saves exactly this shape."""
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    await store.store(_secrets(None, 'other'))
    _, version = await store.load_versioned(_NAME)

    await FileSecretsStore(store.file_store).store(Secrets())

    assert await store.load_versioned(_NAME) == (_ORIGINAL, version)


@pytest.mark.asyncio
async def test_protected_delete_removes_value_and_generation(store):
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    await store.delete_protected_credential(_NAME)

    loaded = await store.load()
    assert loaded is not None
    assert _NAME not in loaded.custom_secrets
    with pytest.raises(KeyError):
        await store.load_versioned(_NAME)
    raw = json.loads(store.file_store.read('secrets.json'))
    assert _NAME not in raw.get('_credential_versions', {})


@pytest.mark.asyncio
async def test_load_versioned_bootstraps_without_rewriting_raw_fields():
    raw = {
        'custom_secrets': {
            _NAME: {
                'secret': _ORIGINAL,
                'description': 'Managed login',
                'future': {'enabled': True},
            }
        },
        'future_top_level': ['keep'],
    }
    file_store = InMemoryFileStore(files={'secrets.json': json.dumps(raw)})
    store = FileSecretsStore(file_store)

    value, version = await store.load_versioned(_NAME)
    updated = json.loads(file_store.read('secrets.json'))
    versions = updated.pop('_credential_versions')

    assert (value, versions[_NAME]) == (_ORIGINAL, version)
    assert updated == raw


@pytest.mark.asyncio
async def test_replace_is_compare_and_swap_and_preserves_raw_fields():
    raw = {
        'custom_secrets': {
            _NAME: {
                'secret': _ORIGINAL,
                'description': 'Managed login',
                'future': True,
            }
        },
        'future_top_level': {'keep': True},
    }
    file_store = InMemoryFileStore(files={'secrets.json': json.dumps(raw)})
    store = FileSecretsStore(file_store)
    _, version = await store.load_versioned(_NAME)

    with pytest.raises(CredentialVersionConflict):
        await store.replace_versioned(_NAME, 'stale', _ROTATED)
    successor = await store.replace_versioned(_NAME, version, _ROTATED)
    updated = json.loads(file_store.read('secrets.json'))

    assert successor != version
    assert updated['future_top_level'] == raw['future_top_level']
    assert updated['custom_secrets'][_NAME] == {
        **raw['custom_secrets'][_NAME],
        'secret': _ROTATED,
    }


@pytest.mark.asyncio
async def test_delete_and_identical_recreate_rejects_old_generation(store):
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    _, original_version = await store.load_versioned(_NAME)

    await store.delete_protected_credential(_NAME)
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    _, recreated_version = await store.load_versioned(_NAME)

    assert recreated_version != original_version
    with pytest.raises(CredentialVersionConflict):
        await store.replace_versioned(_NAME, original_version, _ROTATED)


@pytest.mark.asyncio
async def test_stale_whole_save_preserves_rotation_and_unrelated_edits(store):
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    await store.store(_secrets(None, 'old'))
    raw = json.loads(store.file_store.read('secrets.json'))
    raw['custom_secrets'][_NAME]['future'] = 'keep'
    raw['future_top_level'] = {'keep': True}
    store.file_store.write('secrets.json', json.dumps(raw))

    stale_store = FileSecretsStore(store.file_store)
    stale = await stale_store.load()
    assert stale is not None
    _, version = await store.load_versioned(_NAME)
    successor = await store.replace_versioned(_NAME, version, _ROTATED)

    updated = dict(stale.custom_secrets)
    updated[_NAME] = CustomSecret.from_value(
        {'secret': _ORIGINAL, 'description': 'Updated description'}
    )
    updated['OTHER'] = CustomSecret.from_value({'secret': 'new', 'description': ''})
    await stale_store.store(stale.model_copy(update={'custom_secrets': updated}))

    loaded = await store.load()
    saved_raw = json.loads(store.file_store.read('secrets.json'))
    assert loaded is not None
    assert loaded.custom_secrets[_NAME].secret.get_secret_value() == _ROTATED
    # The whole-document save carries no authority over a protected entry, so
    # its description edit is dropped along with its value.
    assert loaded.custom_secrets[_NAME].description == 'Managed login'
    assert loaded.custom_secrets['OTHER'].secret.get_secret_value() == 'new'
    assert await store.load_versioned(_NAME) == (_ROTATED, successor)
    assert saved_raw['custom_secrets'][_NAME]['future'] == 'keep'
    assert saved_raw['future_top_level'] == {'keep': True}


@pytest.mark.asyncio
async def test_stale_whole_save_preserves_concurrent_deletion(store):
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    await store.store(_secrets(None, 'old'))
    stale_store = FileSecretsStore(store.file_store)
    stale = await stale_store.load()
    assert stale is not None

    await FileSecretsStore(store.file_store).delete_protected_credential(_NAME)

    updated = dict(stale.custom_secrets)
    updated['OTHER'] = CustomSecret.from_value({'secret': 'new', 'description': ''})
    await stale_store.store(stale.model_copy(update={'custom_secrets': updated}))

    loaded = await store.load()
    assert loaded is not None
    assert _NAME not in loaded.custom_secrets
    assert loaded.custom_secrets['OTHER'].secret.get_secret_value() == 'new'


@pytest.mark.asyncio
async def test_explicit_edit_after_rotation_mints_generation(store):
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    _, version = await store.load_versioned(_NAME)
    rotated_version = await store.replace_versioned(_NAME, version, _ROTATED)

    editing_store = FileSecretsStore(store.file_store)
    await editing_store.replace_protected_credential(
        _NAME, '{"tokens":{"refresh_token":"r2"}}'
    )
    value, changed_version = await store.load_versioned(_NAME)

    assert value == '{"tokens":{"refresh_token":"r2"}}'
    assert changed_version != rotated_version


@pytest.mark.asyncio
async def test_protected_edit_keeps_the_existing_description_when_omitted(store):
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    await store.replace_protected_credential(_NAME, _ROTATED)

    loaded = await store.load()
    assert loaded is not None
    assert loaded.custom_secrets[_NAME].description == 'Managed login'


@pytest.mark.asyncio
async def test_description_edit_does_not_touch_the_value_or_generation(store):
    """A metadata edit must not invalidate the runtime's compare-and-swap token."""
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    _, version = await store.load_versioned(_NAME)

    await store.set_protected_credential_description(_NAME, 'New description')

    loaded = await store.load()
    assert loaded is not None
    assert loaded.custom_secrets[_NAME].description == 'New description'
    assert await store.load_versioned(_NAME) == (_ORIGINAL, version)


@pytest.mark.asyncio
async def test_description_edit_cannot_clobber_a_concurrent_rotation(store):
    """The description path must not read-modify-write the value.

    Rewriting the loaded value to change a description reintroduced exactly the
    clobber this guard exists to prevent.
    """
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    editing = FileSecretsStore(store.file_store)
    loaded = await editing.load()
    assert loaded is not None

    _, version = await store.load_versioned(_NAME)
    successor = await store.replace_versioned(_NAME, version, _ROTATED)

    await editing.set_protected_credential_description(_NAME, 'New description')

    assert await store.load_versioned(_NAME) == (_ROTATED, successor)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['local', 'memory'])
async def test_concurrent_replacements_have_one_winner(kind, tmp_path):
    file_store: FileStore
    if kind == 'local':
        file_store = LocalFileStore(root=str(tmp_path))
    else:
        file_store = InMemoryFileStore()
    store = FileSecretsStore(file_store)
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    _, version = await store.load_versioned(_NAME)

    results = await asyncio.gather(
        *(
            FileSecretsStore(file_store).replace_versioned(
                _NAME,
                version,
                f'{{"tokens":{{"refresh_token":"r{i}"}}}}',
            )
            for i in range(8)
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, str) for result in results) == 1
    assert sum(isinstance(result, CredentialVersionConflict) for result in results) == 7


@pytest.mark.asyncio
async def test_local_compare_and_swap_is_cross_process(store, tmp_path):
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    _, version = await store.load_versioned(_NAME)
    context = multiprocessing.get_context('spawn')
    results = context.Queue()
    processes = [
        context.Process(
            target=_replace_in_process,
            args=(str(tmp_path), version, f'value-{index}', results),
        )
        for index in range(4)
    ]

    for process in processes:
        process.start()
    for process in processes:
        # Four spawn processes re-import the app tree and then serialise on the
        # cross-process lock, against `-n auto --forked --cov` on a 2-CPU runner.
        process.join(timeout=120)
        assert process.exitcode == 0

    received = []
    for _ in processes:
        try:
            received.append(results.get(timeout=5))
        except Empty:
            pytest.fail('A replacement process returned no result')

    assert sum(kind == 'ok' for kind, _ in received) == 1
    assert (
        sum(kind == 'CredentialVersionConflict' for kind, _ in received)
        == len(processes) - 1
    )


@pytest.mark.asyncio
async def test_ordinary_load_needs_no_write_access(store, tmp_path):
    """``load`` must not take the update lock: it would need write access to
    create the lock sidecar, and would serialise every read."""
    await store.replace_protected_credential(_NAME, _ORIGINAL, 'Managed login')
    for lock in tmp_path.glob('*.lock'):
        lock.unlink()

    secrets_file = tmp_path / 'secrets.json'
    secrets_file.chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        loaded = await FileSecretsStore(store.file_store).load()
    finally:
        tmp_path.chmod(0o755)
        secrets_file.chmod(0o644)

    assert loaded is not None
    assert loaded.custom_secrets[_NAME].secret.get_secret_value() == _ORIGINAL
    # Also holds when the test runs as root, where the chmod above is advisory.
    assert not list(tmp_path.glob('*.lock'))


@pytest.mark.asyncio
async def test_unsupported_file_store_has_no_compare_and_swap():
    file_store = MagicMock(spec=FileStore)
    file_store.supports_locked_update = False
    file_store.read.return_value = _secrets(_ORIGINAL).model_dump_json(
        context={'expose_secrets': True}
    )
    store = FileSecretsStore(file_store)

    assert await store.load() == _secrets(_ORIGINAL)
    with pytest.raises(NotImplementedError):
        await store.load_versioned(_NAME)
    with pytest.raises(NotImplementedError):
        await store.replace_versioned(_NAME, 'version', _ROTATED)


@pytest.mark.asyncio
async def test_unsupported_file_store_still_protects_the_credential():
    """No lock to take, but ``store`` must still not write a protected name."""
    file_store = MagicMock(spec=FileStore)
    file_store.supports_locked_update = False
    file_store.read.return_value = _secrets(_ORIGINAL).model_dump_json(
        context={'expose_secrets': True}
    )
    store = FileSecretsStore(file_store)

    await store.store(_secrets(_ROTATED, 'new'))
    written = json.loads(file_store.write.call_args.args[1])

    assert written['custom_secrets'][_NAME]['secret'] == _ORIGINAL
    assert written['custom_secrets']['OTHER']['secret'] == 'new'
