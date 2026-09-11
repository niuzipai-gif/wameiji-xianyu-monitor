from __future__ import annotations

from cd_monitor.core.reference_memory import (
    ReferenceProductEvidence,
    build_candidate_evidence,
    score_candidate_against_product,
)


def test_exact_jan_is_a_full_strength_reference_match() -> None:
    product = ReferenceProductEvidence(
        product_id=7,
        stable_key="jan:4547366558180",
        barcode="4547366558180",
        tokens=frozenset({"milet", "walkin", "lane"}),
    )

    result = score_candidate_against_product(
        build_candidate_evidence(title="milet Walkin In My Lane", jan="4547366558180"),
        product,
    )

    assert result.product_id == 7
    assert result.match_kind == "exact_barcode"
    assert result.score == 1.0
    assert result.evidence == {"matched_barcode": "4547366558180"}


def test_two_distinct_shared_words_make_an_explainable_fallback_match() -> None:
    product = ReferenceProductEvidence(
        product_id=8,
        stable_key="sample:abc",
        barcode=None,
        tokens=frozenset({"burnout", "syndromes", "special", "edition"}),
    )

    result = score_candidate_against_product(
        build_candidate_evidence(title="BURNOUT SYNDROMES special edition CD"), product
    )

    assert result.product_id == 8
    assert result.match_kind == "token_overlap"
    assert result.score >= 0.55
    assert result.score < 1.0
    assert result.evidence["shared_tokens"] == ["burnout", "special", "syndromes"]


def test_generic_media_word_alone_is_not_a_reference_match() -> None:
    product = ReferenceProductEvidence(
        product_id=9,
        stable_key="sample:def",
        barcode=None,
        tokens=frozenset({"milet", "walkin", "lane", "cd"}),
    )

    result = score_candidate_against_product(build_candidate_evidence(title="CD"), product)

    assert result.product_id is None
    assert result.match_kind == "no_match"
    assert result.score == 0.0


def test_structured_candidate_identity_ignores_storefront_boilerplate_raw_text() -> None:
    """A full marketplace page must not add unrelated identity evidence."""

    candidate = build_candidate_evidence(
        title="Milet Walkin In My Lane",
        raw_text="Universal Music Like City shop rules, shipping, and returns",
    )

    assert candidate.tokens == frozenset({"milet", "walkin", "in", "my", "lane"})


def test_label_words_and_short_ocr_fragments_do_not_make_a_fallback_match() -> None:
    product = ReferenceProductEvidence(
        product_id=11,
        stable_key="sample:ocr-noise",
        barcode=None,
        tokens=frozenset(
            {
                "universal",
                "music",
                "city",
                "for",
                "ch",
                "mw",
                "live",
                "ray",
                "all",
                "best",
                "yoasobi",
            }
        ),
    )

    result = score_candidate_against_product(
        build_candidate_evidence(title="Universal Music City For CH MW Live Ray All Best"), product
    )

    assert result.product_id is None
    assert result.match_kind == "no_match"
    assert result.score == 0.0
    assert result.evidence == {"shared_tokens": []}


def test_repeated_xianyu_shell_words_do_not_create_a_product_match() -> None:
    product = ReferenceProductEvidence(
        product_id=10,
        stable_key="sample:ui-shell",
        barcode=None,
        tokens=frozenset(
            {
                "明星",
                "角色",
                "存储",
                "介质",
                "闲鱼",
                "交易",
                "限定",
                "cd",
            }
        ),
    )

    result = score_candidate_against_product(
        build_candidate_evidence(raw_text="明星 角色 存储 介质 闲鱼 交易 限定 CD"),
        product,
    )

    assert result.match_kind == "no_match"
    assert result.score == 0.0
    assert result.evidence == {"shared_tokens": []}


def test_price_text_is_not_identity_evidence_or_a_negative_label() -> None:
    product = ReferenceProductEvidence(
        product_id=10,
        stable_key="sample:price-independent",
        barcode=None,
        tokens=frozenset({"milet", "walkin", "lane"}),
    )

    result = score_candidate_against_product(
        build_candidate_evidence(
            title="milet Walkin In My Lane",
            raw_text="当前采购价 999999 日元，暂未找到低价货源",
        ),
        product,
    )

    assert result.match_kind == "token_overlap"
    assert result.product_id == 10
    assert result.score > 0.0
