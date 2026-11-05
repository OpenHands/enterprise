from storage.org_default_settings import apply_org_condenser_max_tokens_default


def test_applies_to_legacy_llm_agent_kind_when_missing():
    settings = {'agent_kind': 'llm', 'condenser': {'max_tokens': None}}

    result = apply_org_condenser_max_tokens_default(settings, max_tokens=200000)

    assert result['condenser']['max_tokens'] == 200000


def test_preserves_existing_value_without_overwrite():
    settings = {'condenser': {'max_tokens': 123456, 'max_size': 240}}

    result = apply_org_condenser_max_tokens_default(settings, max_tokens=200000)

    assert result['condenser'] == {'max_tokens': 123456, 'max_size': 240}


def test_overwrites_existing_value_when_requested():
    settings = {'condenser': {'max_tokens': 123456, 'max_size': 240}}

    result = apply_org_condenser_max_tokens_default(
        settings, max_tokens=200000, overwrite=True
    )

    assert result['condenser'] == {'max_tokens': 200000, 'max_size': 240}


def test_skips_acp_agent_settings():
    settings = {'agent_kind': 'acp'}

    result = apply_org_condenser_max_tokens_default(settings, max_tokens=200000)

    assert result == {'agent_kind': 'acp'}


def test_skips_no_op_condenser():
    settings = {'condenser': {'condenser_kind': 'no_op'}}

    result = apply_org_condenser_max_tokens_default(settings, max_tokens=200000)

    assert result == {'condenser': {'condenser_kind': 'no_op'}}


def test_repairs_malformed_applicable_condenser():
    settings = {'agent_kind': 'openhands', 'condenser': 'bad'}

    result = apply_org_condenser_max_tokens_default(settings, max_tokens=200000)

    assert result['condenser'] == {
        'condenser_kind': 'llm_summarizing',
        'max_tokens': 200000,
    }
