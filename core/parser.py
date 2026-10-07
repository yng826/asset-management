import json
import re
from datetime import datetime

from google import genai
from google.genai import types

from config.constants import ASSET_MAP, TICKER_MAP
from config.settings import GEMINI_API_KEY


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
    - 계좌명이 문장에 명시되지 않은 경우:
        * USD / 해외 주식 / 해외 ETF 거래인 경우: 반드시 "한투일반계좌"로 기본 매핑
        * KRW / 국내 주식 / 국내 ETF 거래인 경우: 반드시 "토스증권기본계좌"로 기본 매핑
4. 티커 및 종목명 규칙:
    - 한국 주식/국내 상장 ETF(6자리 코드) 확인 시 ticker_code에 기입.
    - 미국 주식/ETF의 경우 한글 입력("엔비디아", "테슬라" 등)이라도 ticker_code에 정식 대문자 티커(NVDA, TSLA 등) 매핑 (ticker_name은 한글 가능).
    - 입출금(`DEPOSIT`, `WITHDRAW`) 시 종목명이 없을 경우 통화에 따라 `ticker_name`에 "KRW_CASH"(USD인 경우 "USD_CASH"), `ticker_code`에 "CASH_KRW"(USD인 경우 "CASH_USD")를 반환하고 `quantity`는 0.0으로 설정.
5. total_amount는 배당/입출금일 땐 해당 금액, 매수/매도일 땐 quantity * unit_price.
6. 단일 거래든 여러 건의 거래든 반드시 JSON 리스트(Array) 형태 `[ {{...}}, {{...}} ]`로 반환.

[출력 JSON 예시]
[
  {{
    "trans_date": "{today_str}",
    "account_name": "토스증권기본계좌",
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
    "account_name": "토스증권기본계좌",
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

    def parse_text(self, text: str, reference_date: str = None) -> list[dict]:
        """자연어 텍스트를 JSON 리스트로 파싱"""
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
            return self._parse_response_to_list(response.text)
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
            return self._parse_response_to_list(response.text)
        except Exception as e:
            print(f"❌ 음성 처리 오류: {e}")
            return []


if __name__ == "__main__":
    # 간단 파싱 테스트
    parser = TransactionParser()
    sample = "오늘 토스증권기본계좌에서 삼전 5주 7만1천원에 샀어"
    print(f"입력: {sample}")
    result = parser.parse_text(sample)
    print("결과 JSON:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
