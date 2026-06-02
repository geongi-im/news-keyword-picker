import os
import urllib.parse
from urllib.request import urlopen

from dotenv import load_dotenv


load_dotenv()


class TelegramUtil:
    """설명: 환경 변수 기반으로 Telegram Bot API 메시지를 보내는 유틸리티입니다.
    입력: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_CHAT_TEST_ID 환경 변수를 사용합니다.
    출력: send_message 또는 send_test_message 호출로 메시지 전송을 수행합니다.
    """

    def __init__(self):
        """설명: 텔레그램 전송에 필요한 환경 변수 값을 객체에 적재합니다.
        입력: 별도 인자 없이 현재 프로세스 환경 변수를 읽습니다.
        출력: 초기화된 TelegramUtil 인스턴스를 구성합니다.
        """
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.chat_test_id = os.getenv("TELEGRAM_CHAT_TEST_ID")

    def send_message(self, message):
        """설명: 기본 텔레그램 채팅방으로 메시지를 전송합니다.
        입력: message는 Telegram HTML 메시지 문자열입니다.
        출력: 전송 성공 시 None을 반환하고, 설정 또는 네트워크 오류가 있으면 예외를 발생시킵니다.
        """
        self._send_message(self.chat_id, message)

    def send_test_message(self, message):
        """설명: 테스트용 텔레그램 채팅방으로 메시지를 전송합니다.
        입력: message는 Telegram HTML 메시지 문자열입니다.
        출력: 전송 성공 시 None을 반환하고, 설정 또는 네트워크 오류가 있으면 예외를 발생시킵니다.
        """
        self._send_message(self.chat_test_id, message)

    def _send_message(self, chat_id, message):
        """설명: Telegram Bot API sendMessage 엔드포인트를 호출합니다.
        입력: chat_id는 수신 채팅방 ID, message는 전송할 HTML 메시지입니다.
        출력: 전송 성공 시 None을 반환하고, 필수 설정 누락 시 ValueError를 발생시킵니다.
        """
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set.")
        if not chat_id:
            raise ValueError("Telegram chat id is not set.")

        query = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "parse_mode": "html",
                "text": message,
            }
        )
        urlopen(f"https://api.telegram.org/bot{self.bot_token}/sendMessage?{query}")
