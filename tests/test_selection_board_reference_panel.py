import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _brace_block(source: str, start: int) -> str:
    opening = source.index("{", start)
    depth = 0
    for position in range(opening, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[start : position + 1]
    raise AssertionError("unterminated block")


def _function_block(source: str, name: str) -> str:
    match = re.search(
        rf"^  (?:async )?function {re.escape(name)}\([^)]*\) \{{",
        source,
        flags=re.MULTILINE,
    )
    assert match, f"missing function: {name}"
    return _brace_block(source, match.start())


def _css_rule(styles: str, selector: str) -> str:
    return _brace_block(styles, styles.index(selector + " {"))


def _media_block(styles: str, width: int) -> str:
    reference_panel = styles.index("body.kuro .reference-memory-panel")
    media_start = styles.index(f"@media (max-width: {width}px)", reference_panel)
    return _brace_block(styles, media_start)


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _profile_article_template(profile_renderer: str) -> str:
    start = profile_renderer.index("return [")
    end = profile_renderer.index('].join("");', start) + len('].join("");')
    return profile_renderer[start:end]


def _assert_reference_panel_contract(html: str, script: str, styles: str) -> None:
    assert 'id="referenceXianyuLatest"' in html
    assert 'id="referenceWameijiLatest"' in html
    assert 'id="referenceProfileList"' in html

    refresh_board = _function_block(script, "refreshBoard")
    assert 'apiGet("/api/reference-memory/profiles?limit=3").catch(() => null)' in refresh_board
    assert (
        "view.referenceProfiles = referenceProfilePayload && "
        "Array.isArray(referenceProfilePayload.items) ? "
        "referenceProfilePayload.items : null;"
    ) in _compact(refresh_board)

    reference_memory = _function_block(script, "renderReferenceMemory")
    assert "market_observation_states" not in reference_memory
    for forbidden_value in (
        "item.price",
        "item.currency",
        "priceText",
        "toLocaleString",
        "暂不合算",
    ):
        assert forbidden_value not in reference_memory
    reference_state_label = _function_block(script, "referenceStateLabel")
    assert 'price_unfavorable: "信息待复核"' in reference_state_label
    assert "暂不合算" not in reference_state_label
    null_status = _brace_block(reference_memory, reference_memory.index("if (!status) {"))
    assert 'setText("referenceXianyuLatest", "--");' in null_status
    assert 'setText("referenceWameijiLatest", "--");' in null_status
    assert "profilesElement.innerHTML" in null_status
    assert "身份与待核验信息暂不可用" in null_status

    coverage_start = reference_memory.index("const latestMarketCoverage")
    coverage_end = reference_memory.index("if (stateElement)", coverage_start)
    latest_coverage = reference_memory[coverage_start:coverage_end]
    assert "status.latest_market_coverage" in latest_coverage
    assert "latestMarketCoverage.xianyu" in latest_coverage
    assert "latestMarketCoverage.wameiji" in latest_coverage
    assert "const loginPending = Number(xianyuStates.login_required) || 0;" in latest_coverage

    missing_evidence_formatter = _function_block(script, "referenceMissingEvidenceLabel")
    for raw_value, label in (
        ("barcode", "条码"),
        ("xianyu:market_observation", "闲鱼市场观察"),
        ("wameiji:market_observation", "挖煤姬市场观察"),
    ):
        assert f'"{raw_value}": "{label}"' in missing_evidence_formatter
    assert 'return labels[String(value)] || "其他待核验信息";' in missing_evidence_formatter

    profiles_start = reference_memory.rindex("if (profilesElement) {")
    profiles_end = reference_memory.index("if (!observationsElement) return;", profiles_start)
    profile_renderer = reference_memory[profiles_start:profiles_end]
    assert "profiles.slice(0, 3)" in profile_renderer
    assert "referenceStateLabel(xianyuState)" in profile_renderer
    assert "referenceStateLabel(wameijiState)" in profile_renderer
    assert "仍待确认：" in profile_renderer
    assert "if (!Array.isArray(profiles)) {" in profile_renderer
    assert "身份与待核验信息暂不可用" in profile_renderer
    assert "尚无参考身份档案" in profile_renderer
    assert "missingEvidence.map(referenceMissingEvidenceLabel).join(\"、\")" in profile_renderer
    assert "missingEvidence.map((value) => String(value))" not in profile_renderer
    assert "missingEvidence.join" not in profile_renderer
    for profile_field in (
        "profile.stable_key",
        "profile.barcode",
        "profile.sample_count",
        "profile.catalog_numbers",
        "profile.markets",
        "profile.missing_evidence",
    ):
        assert profile_field in profile_renderer
    profile_article = _profile_article_template(profile_renderer)
    expected_profile_article = _compact(
        '''
        return [
          '<article class="reference-profile">',
          '<b>身份：' + esc(identity) + "</b>",
          '<p>样本：' + esc(sampleCount) + esc(catalogText) + "</p>",
          '<p>当前市场：闲鱼 ' + esc(referenceStateLabel(xianyuState)) + " · 挖煤姬 " + esc(referenceStateLabel(wameijiState)) + "</p>",
          '<p>仍待确认：' + esc(missingText) + "</p>",
          "</article>",
        ].join("");
        '''
    )
    assert _compact(profile_article) == expected_profile_article
    assert "profile." not in profile_article
    for escaped_value in (
        "esc(identity)",
        "esc(sampleCount)",
        "esc(catalogText)",
        "esc(referenceStateLabel(xianyuState))",
        "esc(referenceStateLabel(wameijiState))",
        "esc(missingText)",
    ):
        assert escaped_value in profile_renderer
    assert not re.search(r"<a(?:\s|>)", profile_article)
    for forbidden_value in (
        "profile.price",
        "<button",
        "data-action",
        "onclick",
        "href",
        "source_url",
        "safeHttpUrl",
        ".href",
        "window.open",
        "location.",
        "买入",
        "购买",
        "拒绝",
        "淘汰",
        "推荐购买",
    ):
        assert forbidden_value not in profile_renderer

    metrics_rule = _css_rule(styles, "body.kuro .reference-memory-metrics")
    profiles_rule = _css_rule(styles, "body.kuro .reference-profiles")
    assert "grid-template-columns: repeat(6, 1fr);" in metrics_rule
    assert "grid-template-columns: repeat(3, 1fr);" in profiles_rule

    media_900 = _compact(_media_block(styles, 900))
    media_560 = _compact(_media_block(styles, 560))
    assert (
        "body.kuro .reference-memory-metrics, body.kuro .reference-profiles "
        "{ grid-template-columns: repeat(2, 1fr); }"
    ) in media_900
    assert (
        "body.kuro .reference-memory-metrics, body.kuro .reference-profiles "
        "{ grid-template-columns: 1fr; }"
    ) in media_560


def test_reference_panel_has_latest_market_and_profile_contract() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "discovery-ui.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "styles" / "kuro.css").read_text(encoding="utf-8")

    _assert_reference_panel_contract(html, script, styles)


def test_reference_panel_renders_non_decisive_direction_evidence() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "discovery-ui.js").read_text(encoding="utf-8")

    assert 'id="referenceDirectionList"' in html
    refresh_board = _function_block(script, "refreshBoard")
    assert 'apiGet("/api/reference-memory/directions").catch(() => null)' in refresh_board
    assert (
        'apiGet("/api/reference-memory/candidate-directions?limit=200").catch(() => null)'
        in refresh_board
    )
    reference_memory = _function_block(script, "renderReferenceMemory")
    assert "正样本方向证据，仍需详情核验" in reference_memory
    for forbidden_value in ("item.price", "item.availability", "买入", "购买", "拒绝", "淘汰"):
        assert forbidden_value not in reference_memory

    direction_markup = _function_block(script, "candidateDirectionMarkup")
    assert "positive_direction_covered" in direction_markup
    assert "no_direction_evidence" not in direction_markup
    assert "正样本方向证据，仍需详情核验" in direction_markup
    for forbidden_value in ("item.price", "item.availability", "买入", "购买", "拒绝", "淘汰"):
        assert forbidden_value not in direction_markup


def test_home_prioritizes_opportunity_feed_over_secondary_evidence() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert '<details class="secondary-evidence">' in html
    assert html.index('id="homeFeed"') < html.index('id="referenceMemoryTitle"')
    assert 'class="mascot-stage"' not in html
    assert "Design Direction" not in html
    assert "<b>Mascot</b>" not in html
