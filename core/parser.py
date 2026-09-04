import os
import json
from datetime import datetime
from google import genai
from google.genai import types
from config.settings import GEMINI_API_KEY
from config.constants import ASSET_MAP, TICKER_MAP


class TransactionParser:
    def __init__(self):
        # 최신 구글 공식 genai SDK 클라이언트 초기화
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model_name = "gemini-2.5-flash"

    def _build_system_instruction(self) -> str:
        # 기존에 쓰시던 계좌/종목 목록을 프롬프트 힌트로 주입
        known_accounts = list(ASSET_MAP.keys())
        known_tickers = list(TICKER_MAP.keys())
        today_str = datetime.now().strftime("%Y-%m-%d")

        return f"""
당신은 개인 금융 기록을 분석하여 정확한 DB 트랜잭션 데이터로 변환하는 AI 비서입니다.
오늘 날짜 기준은 [{today_str}] 입니다.
사용자의 음성 전사 내용 또는 자연어 텍스트를 분석하여 아래 JSON 포맷으로만 응답하세요.

[사용 가능한 계좌 힌트]
{', '.join(known_accounts)}

[기존 종목명 힌트]
{', '.join(known_tickers)}

[규칙]1. action_type: 'BUY', 'SELL', 'DIVIDEND', 'DEPOSIT', 'WITHDRAW' 중 하나.
2. 날짜 규칙:
    - '오늘'이면 [{today_str}]
    - '어제', '그저께', '지난주 금요일', '3일 전' 등 상대적인 표현은 기준일([{today_str}])로부터 정확히 계산하여 반드시 'YYYY-MM-DD' 형식으로 입력.
    - 날짜에 대한 언급이 전혀 없다면 [{today_str}]을 기본값으로 사용.
3. 한국 주식 티커(6자리) 확인 시 ticker_code에 기입, 모르면 null.
4. total_amount는 배당/입출금일 땐 해당 금액, 매수/매도일 땐 quantity * unit_price.

[출력 JSON 예시]
{{
    "trans_date": "{today_str}",
    "account_name": "토스증권기본계좌",
    "ticker_name": "삼성전자",
    "ticker_code": "005930",
    "action_type": "BUY",
    "quantity": 5.0,
    "unit_price": 71000.0,
    "total_amount": 355000.0
}}
"""

    def parse_text(self, text: str) -> dict:
        """자연어 텍스트를 JSON으로 파싱"""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=f"사용자 입력: {text}",
                config=types.GenerateContentConfig(
                    system_instruction=self._build_system_instruction(),
                    response_mime_type="application/json",
                    temperature=0.1,
                    # 자동 툴 호출 비활성화 (순수 JSON 파싱용)
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"❌ Gemini 파싱 오류: {e}")
            return None

    def parse_audio(self, audio_file_path: str) -> dict:
        """음성 파일(.ogg, .mp3 등)을 직접 넘겨 텍스트 변환 없이 한 번에 JSON으로 추출"""
        try:
            # 텔레그램에서 받은 음성 파일을 구글 API로 업로드
            uploaded_file = self.client.files.upload(file=audio_file_path)

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    uploaded_file,
                    "이 음성을 듣고 주식/금융 거래 정보를 JSON 규격에 맞춰 추출해줘.",
                ],
                config=types.GenerateContentConfig(
                    system_instruction=self._build_system_instruction(),
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            # 업로드한 임시 오디오 파일 정리
            self.client.files.delete(name=uploaded_file.name)
            return json.loads(response.text)
        except Exception as e:
            print(f"❌ 음성 처리 오류: {e}")
            return None


if __name__ == "__main__":
    # 간단 파싱 테스트
    parser = TransactionParser()
    sample = "오늘 토스증권기본계좌에서 삼전 5주 7만1천원에 샀어"
    print(f"입력: {sample}")
    result = parser.parse_text(sample)
    print("결과 JSON:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
