from dk_ncaab.config.settings import get_settings


def test_env_overrides_yaml_for_nested_api_docs(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("DKNCAAB_API__ENABLE_DOCS", "true")

    try:
        assert get_settings().api.enable_docs is True
    finally:
        get_settings.cache_clear()
