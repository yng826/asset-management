"""토스 응답 변환 점검 (네트워크·DB·키 불필요). 스펙 예시 JSON 으로 변환 함수만 확인.

사용법: python scripts/check_broker_toss.py [스펙 JSON 경로]
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.broker.toss import _parse_closes, _parse_executions  # noqa: E402

DEFAULT_SPEC = "/mnt/project-files/plans/broker-api/toss-openapi-v1.2.24.json"


def _example(spec: dict, path: str, name: str) -> dict:
    return spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["examples"][name][
        "value"
    ]


def main() -> int:
    spec = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SPEC).read_text(encoding="utf-8"))

    closes = _parse_closes(_example(spec, "/api/v1/candles", "dailyCandles"))
    assert closes == [
        {"price_date": date(2026, 3, 24), "close_price": 71600.0},
        {"price_date": date(2026, 3, 25), "close_price": 72000.0},
    ], closes

    orders = _example(spec, "/api/v1/orders", "pendingMixed")["result"]["orders"]
    execs = _parse_executions(orders)
    assert len(execs) == 1, execs  # 미체결(체결 0) 주문은 제외
    e = execs[0]
    assert (e["symbol"], e["side"], e["quantity"], e["avg_price"], e["currency"]) == (
        "AAPL",
        "SELL",
        2.0,
        185.25,
        "USD",
    ), e
    assert e["trade_date"] == date(2026, 3, 29) and e["account_name"] == "토스 일반", e
    print("OK: 캔들·체결 변환 점검 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
