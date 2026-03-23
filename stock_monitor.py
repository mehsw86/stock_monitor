"""
한국 주식 실시간 모니터링 프로그램
일일 변동률이 +-3% 이상일 경우 Slack 알림 발송
"""

import time
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from pykrx import stock
from holiday_checker import is_korean_holiday

KST = ZoneInfo("Asia/Seoul")

# 모니터링할 종목 리스트 (종목코드: 종목명)
STOCK_LIST = {
    "091160": "KODEX 반도체",
    "491820": "HANARO 전력설비투자",
    "449450": "Plus K방산",
    "466920": "SOL 조선TOP3플러스",
    "000660": "SK하이닉스",
    "005930": "삼성전자",
}

# 변동률 임계값 (%)
THRESHOLD = 3.0

# 체크 주기 (초)
CHECK_INTERVAL = 1800  # 30분마다 체크

# Slack 설정
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#stock_management")


class StockMonitor:
    def __init__(self):
        self.slack_client = None
        if SLACK_BOT_TOKEN:
            self.slack_client = WebClient(token=SLACK_BOT_TOKEN)
        self.alerted_stocks = {}  # 이미 알림 보낸 종목 추적 (종목_날짜: "up" 또는 "down")
        self.daily_summary_sent = None  # 일일 요약 발송 날짜

    def get_stock_data(self, ticker: str) -> Optional[dict]:
        """주식 데이터 조회 (pykrx 사용)"""
        try:
            # 오늘 날짜와 7일 전 날짜 (주말/공휴일 고려), KST 기준
            today = datetime.now(KST)
            start_date = (today - timedelta(days=7)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")

            # OHLCV 데이터 조회
            df = stock.get_market_ohlcv(start_date, end_date, ticker)

            if df.empty or len(df) < 1:
                print(f"[오류] {ticker}: 데이터 없음")
                return None

            # 최근 거래일 데이터
            current_price = df['종가'].iloc[-1]

            # 전일 종가 계산
            if len(df) >= 2:
                prev_close = df['종가'].iloc[-2]
            else:
                prev_close = df['시가'].iloc[-1]

            change_pct = ((current_price - prev_close) / prev_close) * 100

            return {
                "ticker": ticker,
                "name": STOCK_LIST.get(ticker, ticker),
                "current_price": current_price,
                "prev_close": prev_close,
                "change_pct": change_pct,
            }
        except Exception as e:
            print(f"[오류] {ticker} 데이터 조회 실패: {e}")
            return None

    def send_slack_alert(self, stock_data: dict):
        """Slack 알림 발송"""
        emoji = "📈" if stock_data["change_pct"] > 0 else "📉"
        color = "#36a64f" if stock_data["change_pct"] > 0 else "#ff0000"

        message = (
            f"{emoji} *{stock_data['name']}* ({stock_data['ticker']})\n"
            f"현재가: {stock_data['current_price']:,.0f}원\n"
            f"전일종가: {stock_data['prev_close']:,.0f}원\n"
            f"변동률: *{stock_data['change_pct']:+.2f}%*"
        )

        if self.slack_client:
            try:
                self.slack_client.chat_postMessage(
                    channel=SLACK_CHANNEL,
                    text=message,
                    attachments=[
                        {
                            "color": color,
                            "text": f"변동률 {THRESHOLD}% 초과 알림",
                        }
                    ],
                )
                print(f"[Slack] 알림 발송 완료: {stock_data['name']}")
            except SlackApiError as e:
                print(f"[Slack 오류] {e.response['error']}")
        else:
            # Slack 토큰 없으면 콘솔 출력
            print(f"\n{'='*50}")
            print(f"🚨 변동률 알림 🚨")
            print(message.replace('*', ''))
            print(f"{'='*50}\n")

    def send_daily_summary(self) -> bool:
        """일일 종목 요약 발송. 성공 시 True 반환."""
        print(f"\n[{datetime.now(KST).strftime('%H:%M:%S')}] 일일 요약 생성 중...")

        results = []
        for ticker in STOCK_LIST.keys():
            stock_data = self.get_stock_data(ticker)
            if stock_data:
                results.append(stock_data)

        if not results:
            print("[오류] 요약 데이터 없음 - 재시도 필요")
            return False

        # 수익률 기준 정렬 (높은 순)
        results.sort(key=lambda x: x["change_pct"], reverse=True)

        # 메시지 생성
        lines = ["📊 *일일 종목 요약* (장 마감)"]
        lines.append("─" * 30)

        for data in results:
            emoji = "🔺" if data["change_pct"] > 0 else "🔽" if data["change_pct"] < 0 else "➖"
            lines.append(
                f"{emoji} {data['name']}: {data['prev_close']:,.0f}원 → {data['current_price']:,.0f}원 ({data['change_pct']:+.2f}%)"
            )

        lines.append("─" * 30)

        # 평균 수익률 계산
        avg_change = sum(d["change_pct"] for d in results) / len(results)
        lines.append(f"평균 수익률: {avg_change:+.2f}%")

        message = "\n".join(lines)

        if self.slack_client:
            try:
                self.slack_client.chat_postMessage(
                    channel=SLACK_CHANNEL,
                    text=message,
                )
                print("[Slack] 일일 요약 발송 완료")
                return True
            except SlackApiError as e:
                print(f"[Slack 오류] {e.response['error']}")
                return False
        else:
            print(f"\n{'='*50}")
            print(message.replace('*', ''))
            print(f"{'='*50}\n")
            return True

    def check_stocks(self):
        """모든 종목 체크"""
        today = datetime.now().strftime("%Y-%m-%d")

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 종목 체크 중...")

        for ticker in STOCK_LIST.keys():
            stock_data = self.get_stock_data(ticker)

            if stock_data is None:
                continue

            change_pct = stock_data["change_pct"]

            print(f"  {stock_data['name']}: {stock_data['current_price']:,.0f}원 ({change_pct:+.2f}%)")

            # 변동률이 임계값 초과인지 확인
            if abs(change_pct) >= THRESHOLD:
                alert_key = f"{ticker}_{today}"
                current_direction = "up" if change_pct > 0 else "down"
                last_direction = self.alerted_stocks.get(alert_key)

                # 알림 조건: 오늘 첫 알림이거나, 방향이 반대로 바뀐 경우
                if last_direction is None or last_direction != current_direction:
                    self.send_slack_alert(stock_data)
                    self.alerted_stocks[alert_key] = current_direction

    def is_market_hours(self) -> bool:
        """한국 주식시장 운영 시간 확인 (09:00 ~ 15:30, 공휴일 제외)"""
        now = datetime.now()

        # 주말 제외
        if now.weekday() >= 5:
            return False

        # 공휴일 제외 (임시공휴일 포함)
        if is_korean_holiday(now.date()):
            return False

        current_time = now.hour * 100 + now.minute
        return 900 <= current_time <= 1530

    def run(self):
        """모니터링 시작"""
        print("=" * 60)
        print("한국 주식 실시간 모니터링 시작")
        print(f"모니터링 종목: {', '.join(STOCK_LIST.values())}")
        print(f"알림 기준: 일일 변동률 ±{THRESHOLD}%")
        print(f"체크 주기: {CHECK_INTERVAL}초")
        print(f"Slack 채널: {SLACK_CHANNEL}")
        print("=" * 60)

        while True:
            try:
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                current_time = now.hour * 100 + now.minute

                if self.is_market_hours():
                    self.check_stocks()

                    # 15:30 일일 요약 발송 (15:30 ~ 15:59 사이, 하루 1회)
                    if 1530 <= current_time < 1600 and self.daily_summary_sent != today:
                        self.send_daily_summary()
                        self.daily_summary_sent = today
                else:
                    print(f"[{now.strftime('%H:%M:%S')}] 장외 시간 - 대기 중...")
                    # 장외 시간에는 알림 기록 초기화
                    self.alerted_stocks.clear()

                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                print("\n모니터링 종료")
                break
            except Exception as e:
                print(f"[오류] {e}")
                time.sleep(CHECK_INTERVAL)


def main():
    monitor = StockMonitor()
    monitor.run()


if __name__ == "__main__":
    main()
