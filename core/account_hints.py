"""core/account_hints.py - 증권사 알림의 마스킹 계좌번호로 계좌를 특정

- 알림에는 '210-22-****213', '64****89-29' 처럼 일부가 '*' 로 가려진 번호만 온다.
- account_hints 테이블에 알림에 보이는 표기 그대로 저장해 두고, 새 알림의 번호와 '*' 를 와일드카드로 비교한다.
- 원본 계좌번호는 저장하지 않는다 (마스킹된 메시지와는 어차피 비교할 수 없다).
"""

import re

from config.constants import ASSET_MAP

# 숫자와 '*' 로 이뤄지고 하이픈이 있는 토큰 (날짜 '2026-10-19' 는 '*' 가 없어 제외)
_ACCOUNT_TOKEN = re.compile(r"(?<![\w*-])[\d*]+(?:-[\d*]+)+(?![\w*-])")


def extract_account_patterns(text: str) -> list[str]:
    """텍스트에서 마스킹 계좌번호 형태 토큰을 등장 순서대로(중복 제거) 추출."""
    found: list[str] = []
    for token in _ACCOUNT_TOKEN.findall(text or ""):
        if "*" in token and token not in found:
            found.append(token)
    return found


def normalize_pattern(pattern: str | None) -> str | None:
    """계좌번호 표기가 아니면 None, 맞으면 공백을 제거한 값."""
    candidate = (pattern or "").strip()
    found = extract_account_patterns(candidate)
    return found[0] if found and found[0] == candidate else None


def patterns_match(a: str, b: str) -> bool:
    """같은 길이이고 각 자리가 같거나 어느 한쪽이 '*' 이면 같은 계좌로 본다."""
    return len(a) == len(b) and all(x == y or "*" in (x, y) for x, y in zip(a, b, strict=True))


def match_accounts(pattern: str, hints: list[tuple[str, str]]) -> list[str]:
    """pattern 과 맞는 hint 의 계좌명 목록 (ASSET_MAP 에 없는 계좌명은 제외, 중복 제거)."""
    accounts: list[str] = []
    for account, hint in hints:
        if account in ASSET_MAP and patterns_match(pattern, hint) and account not in accounts:
            accounts.append(account)
    return accounts


def apply_account_hints(items: list[dict], text: str, hints: list[tuple[str, str]]) -> list[dict]:
    """파싱 결과의 계좌를 알림의 계좌번호로 덮어쓴다.

    - 항목의 account_hint(LLM 이 알림에서 그대로 옮긴 번호)를 쓰고, 없으면 텍스트에 번호가 정확히 하나일 때 그 번호를 쓴다.
    - hint 와 하나의 계좌로 매칭되면 account_name 을 확정하고, 매칭이 없거나 둘 이상이면 'unresolved_hint' 에 번호를 남긴다.
    - 번호가 전혀 없는 항목은 건드리지 않는다.
    """
    patterns = extract_account_patterns(text)
    for item in items:
        pattern = normalize_pattern(item.pop("account_hint", None)) or (
            patterns[0] if len(patterns) == 1 else None
        )
        if not pattern:
            continue
        accounts = match_accounts(pattern, hints)
        if len(accounts) == 1:
            item["account_name"] = accounts[0]
        else:
            item["unresolved_hint"] = pattern
    return items
