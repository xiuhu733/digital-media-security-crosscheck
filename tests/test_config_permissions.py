import json
import stat

from crosscheck import config


def test_runtime_config_is_private_and_replaced_atomically(tmp_path, monkeypatch):
    path = tmp_path / "config.local.json"
    path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    settings = config.Settings(_env_file=None, exa_api_key="test-secret")

    config.save_runtime_settings(settings)

    assert json.loads(path.read_text(encoding="utf-8"))["exa_api_key"] == "test-secret"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".config-*"))
