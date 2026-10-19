from core.account_hints import (
    apply_account_hints,
    candidate_accounts,
    extract_account_patterns,
    match_accounts,
    normalize_item_account,
    normalize_pattern,
    patterns_match,
)

HINTS = [
    ("한투 IRP", "64****89-29"),
    ("미래에셋 일반", "210-22-****213"),
    ("카카오 연금저축", "020-0*-***500"),
]

MIRAE_DEPOSIT = "[미래에셋증권] 입금\n210-22-****213\n입금액: 100,000\n잔액: 247,518\n이혁 하나은행 17:20"
KIS_IRP = "* 계좌번호 : 64****89-29\n* 입금액 : 8,397원\n* 입금일자 : 2026년10월19일"
KAKAO = "- 금액 : 36,144원(세전)\n- 계좌 : 020-0*-***500"


def test_extract_account_patterns():
    assert extract_account_patterns(MIRAE_DEPOSIT) == ["210-22-****213"]
    assert extract_account_patterns(KIS_IRP) == ["64****89-29"]
    assert extract_account_patterns(KAKAO) == ["020-0*-***500"]


def test_extract_ignores_dates_and_plain_numbers():
    assert extract_account_patterns("2026-10-19 에 100000원 입금") == []
    assert extract_account_patterns("12345678-01") == []  # '*' 없는 번호는 알림 표기가 아님


def test_patterns_match_wildcard_both_ways():
    assert patterns_match("210-22-****213", "210-22-****213")
    assert patterns_match("210-22-****213", "210-22-1234213")
    assert not patterns_match("210-22-****213", "210-22-****214")
    assert not patterns_match("210-22-****213", "210-22-***213")  # 길이가 다르면 다른 표기


def test_match_accounts_ignores_unknown_account_names():
    assert match_accounts("64****89-29", HINTS) == ["한투 IRP"]
    assert match_accounts("64****89-29", [("없는 계좌", "64****89-29")]) == []


def test_normalize_pattern():
    assert normalize_pattern(" 64****89-29 ") == "64****89-29"
    assert normalize_pattern("한투 IRP") is None
    assert normalize_pattern(None) is None


def test_apply_resolves_from_text_when_single_pattern():
    items = [{"account_name": "한투 일반", "action_type": "DIVIDEND"}]
    assert apply_account_hints(items, KIS_IRP, HINTS)[0]["account_name"] == "한투 IRP"


def test_apply_uses_item_hint_when_text_has_several_accounts():
    text = MIRAE_DEPOSIT + "\n" + KAKAO
    items = [
        {"account_name": "토스 일반", "account_hint": "210-22-****213"},
        {"account_name": "토스 일반", "account_hint": "020-0*-***500"},
    ]
    result = apply_account_hints(items, text, HINTS)
    assert [i["account_name"] for i in result] == ["미래에셋 일반", "카카오 연금저축"]
    assert all("unresolved_hint" not in i and "account_hint" not in i for i in result)


def test_apply_marks_unknown_number_unresolved():
    items = [{"account_name": "한투 일반"}]
    result = apply_account_hints(items, "계좌 111-22-***333 입금", HINTS)
    assert result[0]["unresolved_hint"] == "111-22-***333"
    assert result[0]["account_name"] == "한투 일반"  # 확정 전 추측값은 그대로 두고 호출부가 되묻는다


def test_apply_marks_ambiguous_number_unresolved():
    hints = [("한투 일반", "64****89-01"), ("한투 IRP", "64****89-01")]
    result = apply_account_hints([{"account_name": "x"}], "64****89-01", hints)
    assert result[0]["unresolved_hint"] == "64****89-01"


def test_apply_leaves_items_without_number_alone():
    items = [{"account_name": "토스 일반"}]
    assert apply_account_hints(items, "토스 삼전 5주 매수", HINTS) == [{"account_name": "토스 일반"}]


def test_candidate_accounts_narrows_by_broker_name():
    assert candidate_accounts("[미래에셋증권]") == ["미래에셋 일반"]
    assert candidate_accounts("한국투자증권") == ["한투 일반", "한투 ISA", "한투 연금저축", "한투 IRP"]
    assert candidate_accounts("한투") == ["한투 일반", "한투 ISA", "한투 연금저축", "한투 IRP"]
    assert candidate_accounts("카카오페이증권") == ["카카오 연금저축"]


def test_candidate_accounts_returns_all_without_clue_or_match():
    from config.constants import ASSET_MAP

    assert candidate_accounts(None) == list(ASSET_MAP)
    assert candidate_accounts("") == list(ASSET_MAP)
    assert candidate_accounts("한") == list(ASSET_MAP)  # 한 글자는 단서로 쓰지 않는다
    assert candidate_accounts("없는증권") == list(ASSET_MAP)


def test_normalize_item_account_keeps_known_and_resolves_alias():
    assert normalize_item_account({"account_name": "한투 ISA"})["account_name"] == "한투 ISA"
    assert normalize_item_account({"account_name": "토스"})["account_name"] == "토스 일반"


def test_normalize_item_account_has_no_default_account():
    assert normalize_item_account({})["account_name"] is None
    assert normalize_item_account({"account_name": ""})["account_name"] is None
    item = normalize_item_account({"account_name": "카카오페이증권"})
    assert item["account_name"] is None and item["broker_hint"] == "카카오페이증권"
    item = normalize_item_account({"account_name": "알수없음", "broker_hint": "미래에셋증권"})
    assert item["broker_hint"] == "미래에셋증권"  # 파서가 준 증권사 단서는 덮어쓰지 않는다
