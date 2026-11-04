import json
from unittest.mock import MagicMock, patch

import pytest

from core import parser as parser_mod
from core.parser import TransactionParser

DIVIDEND_LINE = "2026-11-03 토스증권기본계좌 KODEX 코스피 배당 13688원 입금"


@pytest.fixture(autouse=True)
def _cf_credentials(monkeypatch):
    monkeypatch.setattr(parser_mod, "CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setattr(parser_mod, "CLOUDFLARE_API_TOKEN", "token")


def _ok(result: dict) -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.json.return_value = {"success": True, "result": result, "errors": []}
    return resp


def test_dividend_line_resolves_account_and_stock():
    # Gemma 가 코드블록으로 감싸고 종목코드를 틀리게 줘도 보정되어야 한다
    reply = (
        "```json\n"
        + json.dumps(
            [
                {
                    "trans_date": "2026-11-03",
                    "account_name": "토스증권기본계좌",
                    "ticker_name": "KODEX 코스피",
                    "ticker_code": "999999",
                    "action_type": "DIVIDEND",
                    "quantity": 0.0,
                    "unit_price": 0.0,
                    "total_amount": 13688.0,
                    "currency": "KRW",
                }
            ],
            ensure_ascii=False,
        )
        + "\n```"
    )
    with patch.object(parser_mod.requests, "post", return_value=_ok({"response": reply})) as post:
        items = TransactionParser().parse_text(DIVIDEND_LINE, reference_date="2026-11-03")

    assert len(items) == 1
    item = items[0]
    assert item["action_type"] == "DIVIDEND"
    assert item["total_amount"] == 13688.0
    assert item["account_name"] == "토스 일반"
    assert item["ticker_code"] == "226490"
    url = post.call_args.args[0]
    assert "/accounts/acct/ai/run/@cf/google/gemma-4-26b-a4b-it" in url
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer token"


def test_response_object_instead_of_string():
    # JSON 모드 등으로 response 가 이미 객체로 오는 경우
    obj = [{"action_type": "DEPOSIT", "total_amount": 1000, "account_name": None}]
    with patch.object(parser_mod.requests, "post", return_value=_ok({"response": obj})):
        items = TransactionParser().parse_text("입금")
    assert items[0]["action_type"] == "DEPOSIT"


def test_openai_compatible_choices_shape():
    result = {"choices": [{"message": {"content": '{"action_type": "BUY", "account_name": null}'}}]}
    with patch.object(parser_mod.requests, "post", return_value=_ok(result)):
        items = TransactionParser().parse_text("매수")
    assert items[0]["action_type"] == "BUY"


def test_api_failure_returns_empty_and_logs(caplog):
    with patch.object(parser_mod.requests, "post", side_effect=RuntimeError("boom")):
        assert TransactionParser().parse_text(DIVIDEND_LINE) == []
    assert "Workers AI 파싱 오류" in caplog.text


def test_empty_response_logs_warning(caplog):
    with patch.object(parser_mod.requests, "post", return_value=_ok({"response": ""})):
        assert TransactionParser().parse_text("안녕") == []
    assert "거래를 추출하지 못함" in caplog.text


def test_missing_credentials_returns_empty(monkeypatch):
    monkeypatch.setattr(parser_mod, "CLOUDFLARE_API_TOKEN", "")
    assert TransactionParser().parse_text(DIVIDEND_LINE) == []


def test_parse_audio_transcribes_then_parses(tmp_path):
    audio = tmp_path / "v.ogg"
    audio.write_bytes(b"fake")
    responses = [
        _ok({"text": " 토스 삼전 5주 7만원에 매수 "}),
        _ok(
            {
                "response": '[{"action_type":"BUY","account_name":"토스","ticker_name":"삼성전자","quantity":5,"unit_price":70000,"total_amount":350000}]'
            }
        ),
    ]
    with patch.object(parser_mod.requests, "post", side_effect=responses) as post:
        items = TransactionParser().parse_audio(str(audio), reference_date="2026-11-03")
    assert items[0]["account_name"] == "토스 일반"
    assert "whisper" in post.call_args_list[0].args[0]
    assert post.call_args_list[0].kwargs["json"]["language"] == "ko"


def test_http_error_body_is_logged(caplog):
    resp = MagicMock()
    resp.ok = False
    resp.status_code = 403
    resp.text = '{"errors":[{"code":10000,"message":"Authentication error"}]}'
    with patch.object(parser_mod.requests, "post", return_value=resp):
        assert TransactionParser().parse_text("안녕") == []
    assert "HTTP 403" in caplog.text
    assert "Authentication error" in caplog.text


def test_empty_response_logs_diagnostics(caplog):
    result = {
        "choices": [{"message": {"content": None}, "finish_reason": "length"}],
        "usage": {"total_tokens": 2048},
    }
    with patch.object(parser_mod.requests, "post", return_value=_ok(result)):
        assert TransactionParser().parse_text("안녕") == []
    assert "finish_reason=length" in caplog.text
