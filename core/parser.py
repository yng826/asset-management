import json
import re
from datetime import datetime

from google import genai
from google.genai import types

from config.constants import ACCOUNT_ALIASES, ASSET_MAP, DEFAULT_ACCOUNT, TICKER_MAP
from config.settings import GEMINI_API_KEY
from core.account_hints import apply_account_hints


class TransactionParser:
    def __init__(self):
        # 최신 구글 공식 genai SDK 클라이언트 초기화
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model_name = "gemini-2.5-flash"

    def _build_system_instruction(self, reference_date: str = None) -> str:
        # 기존에 쓰시던 계좌/종목 목록을 프롬프트 힌트로 주입
        known_accounts = list(ASSET_MAP.keys())
        known_tickers = list(TICKER_MAP.keys())
        today_str = reference_date or datetime.now().strftime("%Y-%m-%d")

        return f"""
당신은 개인 금융 기록을 분석하여 정확한 DB 트랜잭션 데이터 리스트로 변환하는 AI 비서입니다.
오늘 기준일은 [{today_str}] 입니다.
사용자의 음성 전사 내용 또는 자연어 텍스트(단건 또는 다건 거래)를 분석하여 반드시 아래 JSON 리스트 포맷으로만 응답하세요.

[사용 가능한 계좌 힌트]
{", ".join(known_accounts)}

[기존 종목명 힌트]
{", ".join(known_tickers)}

[규칙]
1. action_type: 'BUY', 'SELL', 'DIVIDEND', 'DEPOSIT', 'WITHDRAW' 중 하나.
2. 날짜 규칙:
    - '오늘'이면 [{today_str}]
    - '어제', '그저께', '지난주 금요일', '3일 전' 등 상대적인 표현은 기준일([{today_str}])로부터 정확히 계산하여 반드시 'YYYY-MM-DD' 형식으로 입력.
    - **중요(날짜 상속)**: 한 문장에 여러 종목/거래가 나열된 경우, 문장 앞부분(또는 첫머리)에 지정된 상대 일자(예: '어제', '3일 전', '지난주 금요일')는 개별 날짜가 각 항목마다 따로 언급되지 않는 한 뒤따르는 모든 거래 항목에 공통으로 상속되어 적용됩니다.
    - 문장 전체에 날짜에 대한 언급이 전혀 없다면 [{today_str}]을 기본값으로 사용.
3. 통화(currency) 및 계좌(account_name) 기본값 규칙:
    - 원화 거래는 'KRW', 달러 등 외화 거래는 'USD' (기본값 'KRW').
    - 계좌명은 반드시 [사용 가능한 계좌 힌트] 중 하나로 입력 (예: "토스" → "토스 일반", "카카오 연금저축" → "카카오 연금저축").
    - 계좌명이 문장에 명시되지 않은 경우: 국내/해외 구분 없이 "{DEFAULT_ACCOUNT}"로 기본 매핑
4. 티커 및 종목명 규칙:
    - 한국 주식/국내 상장 ETF(6자리 코드) 확인 시 ticker_code에 기입.
    - 미국 주식/ETF의 경우 한글 입력("엔비디아", "테슬라" 등)이라도 ticker_code에 정식 대문자 티커(NVDA, TSLA 등) 매핑 (ticker_name은 한글 가능).
    - 입출금(`DEPOSIT`, `WITHDRAW`) 시 종목명이 없을 경우 통화에 따라 `ticker_name`에 "KRW_CASH"(USD인 경우 "USD_CASH"), `ticker_code`에 "CASH_KRW"(USD인 경우 "CASH_USD")를 반환하고 `quantity`는 0.0으로 설정.
5. total_amount는 배당/입출금일 땐 해당 금액, 매수/매도일 땐 quantity * unit_price.
6. 단일 거래든 여러 건의 거래든 반드시 JSON 리스트(Array) 형태 `[ {{...}}, {{...}} ]`로 반환.
7. 증권사 알림 메시지(카톡·문자 붙여넣기)는 여러 건이 섞여 있을 수 있으니 알림마다 한 건씩 변환:
    - 계좌번호가 `*` 로 가려진 채 적혀 있으면(예: `210-22-****213`) 해당 알림의 `account_hint` 에 그 문자열을 한 글자도 바꾸지 말고 그대로 기입. 증권사 이름만으로 계좌를 추측하지 말 것. 번호가 없으면 `account_hint` 는 생략.
    - '입금'·'출금' 알림은 DEPOSIT·WITHDRAW (입금액·출금액), 입금 후 '잔액' 은 무시.
    - 배당(분배금) 입금 알림은 DIVIDEND. 세후 금액이 있으면 세후, 세전 금액만 있으면 세전 금액을 total_amount 로. 외화 배당은 currency 'USD' 와 해당 통화 금액.
    - 국내 종목코드가 `A001740` 처럼 알파벳 접두어로 시작하면 접두어를 떼고 6자리 `001740` 만 ticker_code 로.
    - '2주 구매 성공, 주당 14,665원' 같은 체결 알림은 BUY (quantity=2, unit_price=14665, total_amount=quantity*unit_price).
    - 금액이 적혀 있지 않은 알림(예: 예탁금 이용료 입금 안내)은 변환하지 말고 건너뜀.

[출력 JSON 예시]
[
  {{
    "trans_date": "{today_str}",
    "account_name": "{DEFAULT_ACCOUNT}",
    "ticker_name": "삼성전자",
    "ticker_code": "005930",
    "action_type": "BUY",
    "quantity": 5.0,
    "unit_price": 71000.0,
    "total_amount": 355000.0,
    "currency": "KRW"
  }},
  {{
    "trans_date": "{today_str}",
    "account_name": "{DEFAULT_ACCOUNT}",
    "ticker_name": "KRW_CASH",
    "ticker_code": "CASH_KRW",
    "action_type": "DEPOSIT",
    "quantity": 0.0,
    "unit_price": 0.0,
    "total_amount": 1000000.0,
    "currency": "KRW"
  }}
]
"""

    def _parse_response_to_list(self, response_text: str) -> list[dict]:
        """응답 텍스트를 파싱하여 무조건 list[dict] 형태로 반환 (마크다운 코드블록 및 단일 dict 자동 래핑 지원)"""
        if not response_text:
            return []
        text = response_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
            elif isinstance(data, dict):
                return [data]
        except Exception as e:
            print(f"❌ JSON 파싱 오류: {e}, 원문: {response_text}")
            try:
                match = re.search(r"\[.*\]", text, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    if isinstance(data, list):
                        return [item for item in data if isinstance(item, dict)]
                else:
                    match_obj = re.search(r"\{.*\}", text, re.DOTALL)
                    if match_obj:
                        data = json.loads(match_obj.group(0))
                        if isinstance(data, dict):
                            return [data]
            except Exception as ex:
                print(f"❌ 정규식 JSON 복구 실패: {ex}")
        return []

    def _normalize_items(self, items: list[dict]) -> list[dict]:
        """LLM 출력 보정: 계좌명 정식화, 알려진 종목코드 강제, 수량×단가≠총액인 단가 재계산"""
        for item in items:
            # 1) 계좌명: 이표기 → 정식 계좌명, 미지정 시 기본 계좌
            account = (item.get("account_name") or "").strip()
            account = ACCOUNT_ALIASES.get(account, account)
            item["account_name"] = account or DEFAULT_ACCOUNT

            # 2) 종목코드: TICKER_MAP 에 있는 종목명은 LLM 이 준 코드 대신 정답 코드 사용 (".KS" 접미사 제거)
            known_code = TICKER_MAP.get((item.get("ticker_name") or "").strip())
            if known_code:
                item["ticker_code"] = known_code.split(".")[0]

            # 3) 증권사 알림 종목코드의 알파벳 접두어 제거 (A001740 → 001740)
            code = (item.get("ticker_code") or "").strip()
            if re.fullmatch(r"A\d{6}", code):
                item["ticker_code"] = code[1:]

            # 4) 단가: 매수/매도에서 수량×단가가 총액과 1% 넘게 어긋나면 총액 기준으로 단가 재계산
            if str(item.get("action_type", "")).upper() in ("BUY", "SELL"):
                try:
                    qty = float(item.get("quantity") or 0)
                    price = float(item.get("unit_price") or 0)
                    total = float(item.get("total_amount") or 0)
                except (TypeError, ValueError):
                    continue
                if qty > 0 and total > 0 and abs(qty * price - total) > total * 0.01:
                    item["unit_price"] = round(total / qty, 4)
        return items

    def parse_text(
        self, text: str, reference_date: str = None, hints: list[tuple[str, str]] | None = None
    ) -> list[dict]:
        """자연어 텍스트를 JSON 리스트로 파싱. hints([(계좌명, 마스킹 계좌번호)])가 있으면 알림의 계좌번호로 계좌 확정"""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=f"사용자 입력: {text}",
                config=types.GenerateContentConfig(
                    system_instruction=self._build_system_instruction(reference_date),
                    response_mime_type="application/json",
                    temperature=0.1,
                    # 자동 툴 호출 비활성화 (순수 JSON 파싱용)
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            items = self._normalize_items(self._parse_response_to_list(response.text))
            return apply_account_hints(items, text, hints or [])
        except Exception as e:
            print(f"❌ Gemini 파싱 오류: {e}")
            return []

    def parse_audio(self, audio_file_path: str, reference_date: str = None) -> list[dict]:
        """음성 파일(.ogg, .mp3 등)을 직접 넘겨 텍스트 변환 없이 한 번에 JSON 리스트로 추출"""
        try:
            # 텔레그램에서 받은 음성 파일을 구글 API로 업로드
            uploaded_file = self.client.files.upload(file=audio_file_path)

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    uploaded_file,
                    "이 음성을 듣고 주식/금융 거래 정보를 JSON 리스트 규격에 맞춰 추출해줘.",
                ],
                config=types.GenerateContentConfig(
                    system_instruction=self._build_system_instruction(reference_date),
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            # 업로드한 임시 오디오 파일 정리
            self.client.files.delete(name=uploaded_file.name)
            return self._normalize_items(self._parse_response_to_list(response.text))
        except Exception as e:
            print(f"❌ 음성 처리 오류: {e}")
            return []


if __name__ == "__main__":
    # 간단 파싱 테스트
    parser = TransactionParser()
    sample = "오늘 토스에서 삼전 5주 7만1천원에 샀어"
    print(f"입력: {sample}")
    result = parser.parse_text(sample)
    print("결과 JSON:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
