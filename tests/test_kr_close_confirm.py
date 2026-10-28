import sys
from datetime import datetime
from types import ModuleType
from zoneinfo import ZoneInfo

import pandas as pd

# 테스트 환경에 MariaDB C 커넥터가 없어도 import 가능하도록 스텁 처리
if "mariadb" not in sys.modules:
    try:
        import mariadb  # noqa: F401
    except ImportError:
        stub = ModuleType("mariadb")
        stub.Error = Exception
        stub.connect = lambda *a, **k: None
        sys.modules["mariadb"] = stub

from core import calculator  # noqa: E402
from core.fetcher.kr_stock import drop_unconfirmed_today  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


def _df():
    idx = pd.to_datetime(["2026-10-26", "2026-10-27", "2026-10-28"])
    return pd.DataFrame({"Close": [289000.0, 291500.0, 296000.0]}, index=idx)


def test_장중에는_오늘_행을_제외한다():
    now = datetime(2026, 10, 28, 9, 5, tzinfo=KST)
    out = drop_unconfirmed_today(_df(), now)
    assert list(out["Close"]) == [289000.0, 291500.0]


def test_확정_시각_이후에는_오늘_행을_유지한다():
    now = datetime(2026, 10, 28, 16, 0, tzinfo=KST)
    out = drop_unconfirmed_today(_df(), now)
    assert list(out["Close"]) == [289000.0, 291500.0, 296000.0]


def test_장전에도_오늘_행이_없으면_그대로다():
    now = datetime(2026, 10, 29, 8, 30, tzinfo=KST)
    out = drop_unconfirmed_today(_df(), now)
    assert len(out) == 3


def test_전일_종가_조회는_기준일_미만만_쓴다(monkeypatch):
    captured = {}

    def fake_fetch_all(query, params=()):
        captured["params"] = params
        return [("005380", "2026-10-27", 291500)]

    monkeypatch.setattr(calculator, "fetch_all", fake_fetch_all)
    out = calculator.get_previous_close_map("2026-10-28")
    assert captured["params"] == ("2026-10-28",)
    assert out["005380"]["close_price"] == 291500.0
