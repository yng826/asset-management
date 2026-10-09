"""
core/rebalance.py
- 자산군 목표 비중 관리 및 리밸런싱 이탈 감지.
- 현재 비중: 최신 결산 스냅샷의 자산군 뷰(v_daily_asset_class_summary, /chart alloc 과 같은 분류)
- 목표 비중: rebalance_targets 테이블 (텔레그램 /target 으로 설정)
- 허용폭: detector_settings 'rebalance_band_pct' (목표 대비 절대 %p, 기본 5.0)
- 알림: 매일 16:10 결산 후 허용폭 초과 자산군을 알림. 같은 자산군·방향은 REALERT_DAYS 일 동안 재알림하지 않음
"""

import logging
import re

from database.connection import execute, fetch_all
from database.repository import AssetRepository

DEFAULT_BAND_PCT = 5.0
REALERT_DAYS = 7
ALERT_EVENT = "REBALANCE_DRIFT"

ASSET_CLASSES = [
    "가상자산",
    "국내 개별주",
    "국내추종 ETF",
    "해외주식",
    "해외추종 ETF",
    "펀드/퇴직예치",
    "현금/예수금",
]

# 명령어 입력용 별칭 (공백·'/' 제거, 소문자 비교)
_ALIASES = {
    "가상자산": ["가상자산", "코인", "crypto"],
    "국내 개별주": ["국내개별주", "국내주식", "개별주", "krstock"],
    "국내추종 ETF": ["국내추종etf", "국내etf", "kretf"],
    "해외주식": ["해외주식", "미국주식", "usstock"],
    "해외추종 ETF": ["해외추종etf", "해외etf", "globaletf"],
    "펀드/퇴직예치": ["펀드퇴직예치", "펀드", "퇴직", "예치", "fund"],
    "현금/예수금": ["현금예수금", "현금", "예수금", "cash"],
}
_ALIAS_MAP = {alias: cls for cls, aliases in _ALIASES.items() for alias in aliases}


def resolve_asset_class(text: str) -> str | None:
    """사용자 입력 자산군 이름 → 표준 자산군명 (못 찾으면 None)."""
    key = re.sub(r"[\s/]", "", str(text or "")).lower()
    return _ALIAS_MAP.get(key)


def ensure_rebalance_table() -> None:
    """rebalance_targets 테이블이 없으면 생성 (schema.sql 9번과 동일 DDL)."""
    execute(
        """
        CREATE TABLE IF NOT EXISTS rebalance_targets (
            asset_class VARCHAR(30) NOT NULL PRIMARY KEY,
            target_pct DECIMAL(6, 2) NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """,
    )


def get_targets() -> dict[str, float]:
    ensure_rebalance_table()
    return {
        cls: float(pct) for cls, pct in fetch_all("SELECT asset_class, target_pct FROM rebalance_targets")
    }


def set_targets(targets: dict[str, float]) -> None:
    ensure_rebalance_table()
    for cls, pct in targets.items():
        execute(
            """
            INSERT INTO rebalance_targets (asset_class, target_pct) VALUES (?, ?)
            ON DUPLICATE KEY UPDATE target_pct = VALUES(target_pct)
            """,
            (cls, pct),
        )


def clear_targets(asset_classes: list[str] | None = None) -> None:
    """목표 삭제 (None 이면 전체)."""
    ensure_rebalance_table()
    if asset_classes is None:
        execute("DELETE FROM rebalance_targets")
        return
    for cls in asset_classes:
        execute("DELETE FROM rebalance_targets WHERE asset_class = ?", (cls,))


def get_band_pct() -> float:
    rows = fetch_all("SELECT setting_value FROM detector_settings WHERE setting_key = 'rebalance_band_pct'")
    return float(rows[0][0]) if rows else DEFAULT_BAND_PCT


def set_band_pct(value: float) -> None:
    execute(
        """
        INSERT INTO detector_settings (setting_key, setting_value, description)
        VALUES ('rebalance_band_pct', ?, '리밸런싱: 목표 비중 대비 허용 이탈폭 (절대 %p)')
        ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)
        """,
        (value,),
    )


def get_rebalance_status() -> dict:
    """
    최신 결산 스냅샷 기준 목표 대비 현황.
    Returns:
        snapshot_date, total_eval, band_pct, target_sum
        rows : [{"asset_class", "current_pct", "current_eval", "target_pct"(없으면 None), "diff_pct", "adjust_amount", "out_of_band"}]
               adjust_amount: 목표 비중까지 필요한 매수(+)/매도(-) 금액
    """
    rows = fetch_all(
        """
        SELECT snapshot_date, asset_class, class_eval FROM v_daily_asset_class_summary
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM daily_snapshots)
        """
    )
    targets = get_targets()
    band = get_band_pct()
    if not rows:
        return {"snapshot_date": None, "total_eval": 0.0, "band_pct": band, "target_sum": 0.0, "rows": []}

    snapshot_date = str(rows[0][0])[:10]
    evals = {cls: float(val) for _, cls, val in rows}
    total = sum(evals.values())
    result_rows = []
    for cls in ASSET_CLASSES + sorted(set(evals) - set(ASSET_CLASSES)):
        current_eval = evals.get(cls, 0.0)
        current_pct = current_eval / total * 100 if total else 0.0
        target = targets.get(cls)
        if target is None and current_eval == 0:
            continue
        diff = current_pct - target if target is not None else None
        result_rows.append(
            {
                "asset_class": cls,
                "current_pct": current_pct,
                "current_eval": current_eval,
                "target_pct": target,
                "diff_pct": diff,
                "adjust_amount": (target - current_pct) / 100 * total if target is not None else None,
                "out_of_band": diff is not None and abs(diff) > band,
            }
        )
    return {
        "snapshot_date": snapshot_date,
        "total_eval": total,
        "band_pct": band,
        "target_sum": sum(targets.values()),
        "rows": result_rows,
    }


def _recently_alerted(asset_class: str, direction: str) -> bool:
    return AssetRepository().has_recent_anomaly_alert(
        f"REBAL:{asset_class}:{direction}", ALERT_EVENT, REALERT_DAYS
    )


def _record_alert(asset_class: str, direction: str, diff: float) -> None:
    AssetRepository().record_anomaly_alert(f"REBAL:{asset_class}:{direction}", ALERT_EVENT, diff)


def find_new_drifts() -> tuple[dict, list[dict]]:
    """허용폭을 넘었고 최근 REALERT_DAYS 일 내 같은 방향으로 알리지 않은 자산군. (status, 대상 rows)"""
    status = get_rebalance_status()
    drifts = []
    for row in status["rows"]:
        if not row["out_of_band"]:
            continue
        direction = "OVER" if row["diff_pct"] > 0 else "UNDER"
        if not _recently_alerted(row["asset_class"], direction):
            drifts.append({**row, "direction": direction})
    return status, drifts


def mark_alerted(drifts: list[dict]) -> None:
    for row in drifts:
        _record_alert(row["asset_class"], row["direction"], row["diff_pct"])


def format_drift_line(row: dict) -> str:
    """'가상자산 29.8% (목표 25.0%, +4.8%p → 2,700만 매도)' 형식."""
    amount = row["adjust_amount"]
    action = "매수" if amount > 0 else "매도"
    return (
        f"{row['asset_class']} {row['current_pct']:.1f}% (목표 {row['target_pct']:.1f}%, "
        f"{row['diff_pct']:+.1f}%p → {abs(amount) / 10_000:,.0f}만 {action})"
    )


async def check_rebalance_drift(application, chat_id: str) -> None:
    """[매일 16:10] 목표 비중 이탈 알림 (목표 미설정 시 아무것도 하지 않음)."""
    import html

    try:
        status, drifts = find_new_drifts()
    except Exception as e:
        logging.error(f"❌ 리밸런싱 이탈 점검 실패: {e}", exc_info=True)
        return
    if not drifts:
        return

    lines = [
        f"⚖️ <b>목표 비중 이탈</b> ({status['snapshot_date'][5:]} 결산, 허용폭 ±{status['band_pct']:.1f}%p)",
        "",
    ]
    lines += [
        ("🔺 " if row["direction"] == "OVER" else "🔻 ") + html.escape(format_drift_line(row))
        for row in drifts
    ]
    lines.append("")
    lines.append(f"<i>/target 으로 전체 현황 확인 · 같은 방향 이탈은 {REALERT_DAYS}일간 재알림 안 함</i>")
    await application.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
    mark_alerted(drifts)
    logging.info(f"⚖️ 리밸런싱 이탈 알림 발송: {[row['asset_class'] for row in drifts]}")
