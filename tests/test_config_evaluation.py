from cd_monitor.config import load_config


def test_load_config_maps_xianyu_and_alert_sections_to_evaluation_config(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
xianyu:
  min_valid_price_cny: 20
  max_valid_price_cny: 1500
  sample_limit: 12
  negotiation_discount: 0.8
  liquidity_discount_default: 0.85
  fee_rate_default: 0.01
  fee_cap_cny: 50
alert:
  strong_profit_min_cny: 40
  strong_margin_min: 0.4
  weak_profit_min_cny: 60
  weak_margin_min: 0.3
  min_match_confidence_strong: 0.9
  min_match_confidence_weak: 0.8
  min_xianyu_samples_strong: 4
  min_xianyu_samples_weak: 3
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.evaluation.negotiation_discount == 0.8
    assert config.evaluation.liquidity_discount_default == 0.85
    assert config.evaluation.min_valid_price_cny == 20
    assert config.evaluation.max_valid_price_cny == 1500
    assert config.evaluation.sample_limit == 12
    assert config.evaluation.xianyu_fee_rate == 0.01
    assert config.evaluation.xianyu_fee_cap_cny == 50
    assert config.evaluation.strong_profit_min_cny == 40
    assert config.evaluation.min_xianyu_samples_weak == 3
