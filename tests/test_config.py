import os

from cd_monitor.config import load_config


def test_load_config_reads_yaml_and_env_override(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
app:
  db_path: "data/from-file.db"
browser:
  enabled: false
cost:
  wameiji_exchange_rate: 0.05
notify:
  feishu_webhook_url: ""
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CD_MONITOR_DB_PATH", "data/from-env.db")
    monkeypatch.setenv("BROWSER_ENABLED", "true")

    config = load_config(config_path)

    assert config.app.db_path == "data/from-env.db"
    assert config.browser.enabled is True
    assert config.cost.wameiji_exchange_rate == 0.05
    assert os.environ["CD_MONITOR_DB_PATH"] == "data/from-env.db"


def test_load_config_reads_xianyu_state_file_from_yaml_and_env(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
browser:
  enabled: true
  xianyu_state_file: "data/from-yaml-state.json"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XIANYU_STATE_FILE", "data/from-env-state.json")

    config = load_config(config_path)

    assert config.browser.xianyu_state_file == "data/from-env-state.json"


def test_load_config_accepts_goofish_state_file_alias(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("browser:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.delenv("XIANYU_STATE_FILE", raising=False)
    monkeypatch.setenv("GOOFISH_STATE_FILE", "data/goofish-state.json")

    config = load_config(config_path)

    assert config.browser.xianyu_state_file == "data/goofish-state.json"


def test_load_config_accepts_goofish_profile_dir_alias(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("browser:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.delenv("XIANYU_PROFILE_DIR", raising=False)
    monkeypatch.setenv("GOOFISH_PROFILE_DIR", "data/browser_profiles/goofish")

    config = load_config(config_path)

    assert config.browser.xianyu_profile_dir == "data/browser_profiles/goofish"
