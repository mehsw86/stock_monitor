"""
전세계 유가 모니터링 (WTI, Brent, Dubai)
Google Sheet 업데이트 + Slack 알림
"""

import os
import tempfile
from datetime import datetime

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#stock_management")

# 유종별 설정
OIL_TYPES = ["WTI", "Brent", "Dubai"]

YF_TICKERS = {
    "WTI": "CL=F",
    "Brent": "BZ=F",
}

OILPRICE_URL = "https://oilprice.com/oil-price-charts/46"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# Google Sheets 설정
SPREADSHEET_ID = os.environ.get(
    "GSHEET_SPREADSHEET_ID",
    "1i_q1mMAEU8ucq7JIhGXmuGDN2Ku4MsR4GyKVFxzlwZI",
)
SHEET_NAME = "Oil Prices"


class OilMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

    def fetch_prices(self):
        """WTI/Brent는 yfinance, Dubai는 웹 스크래핑으로 가격 조회"""
        prices = {}

        # WTI, Brent - yfinance
        for name, ticker_symbol in YF_TICKERS.items():
            try:
                ticker = yf.Ticker(ticker_symbol)
                hist = ticker.history(period="5d")
                if hist.empty:
                    print(f"[Oil] {name} 데이터 없음 (yfinance)")
                    continue
                close = hist["Close"].iloc[-1]
                prices[name] = {"price": round(float(close), 2)}
                print(f"[Oil] {name}: ${prices[name]['price']}")
            except Exception as e:
                print(f"[Oil] {name} 조회 실패: {e}")

        # Dubai - oilprice.com 스크래핑
        try:
            resp = self.session.get(OILPRICE_URL, timeout=15)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            found = False
            for row in soup.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 3:
                    name_text = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                    if name_text == "Dubai":
                        price_td = cells[2]
                        price_text = price_td.get_text(strip=True)
                        prices["Dubai"] = {"price": round(float(price_text), 2)}
                        print(f"[Oil] Dubai: ${prices['Dubai']['price']}")
                        found = True
                        break
            if not found:
                print("[Oil] Dubai 행을 찾을 수 없음")
        except Exception as e:
            print(f"[Oil] Dubai 조회 실패: {e}")

        return prices

    def _get_gsheet_client(self):
        """Google Sheet 클라이언트 생성"""
        try:
            import gspread
            creds_json = os.environ.get("GSHEET_CREDENTIALS")
            if creds_json:
                import json
                creds_path = tempfile.NamedTemporaryFile(
                    suffix=".json", delete=False, mode="w"
                )
                creds_path.write(creds_json)
                creds_path.close()
                client = gspread.service_account(filename=creds_path.name)
                os.unlink(creds_path.name)
                return client
            else:
                print("[GSheet] GSHEET_CREDENTIALS 미설정 - 시트 업데이트 스킵")
                return None
        except ImportError:
            print("[GSheet] gspread 미설치 - 시트 업데이트 스킵")
            return None

    def update_google_sheet(self, prices, gsheet_client=None):
        """Google Sheet에 가격 기록 + 전일대비 변동률 계산. 변동률 dict 반환"""
        changes = {}

        if not gsheet_client:
            gsheet_client = self._get_gsheet_client()
            if not gsheet_client:
                return changes

        try:
            spreadsheet = gsheet_client.open_by_key(SPREADSHEET_ID)
            try:
                sheet = spreadsheet.worksheet(SHEET_NAME)
            except Exception:
                # 탭이 없으면 자동 생성
                sheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=10)
                print(f"[GSheet] '{SHEET_NAME}' 시트탭 생성 완료")
        except Exception as e:
            print(f"[GSheet 오류] 시트 열기 실패: {e}")
            return changes

        today = datetime.now().strftime("%Y-%m-%d")

        # 헤더 확인/생성
        try:
            all_values = sheet.get_all_values()
        except Exception:
            all_values = []

        if not all_values:
            headers = ["Date"]
            for oil_type in OIL_TYPES:
                headers.extend([f"{oil_type} ($)", f"{oil_type} Change(%)"])
            sheet.append_row(headers)
            all_values = [headers]

        # 중복 날짜 체크
        existing_dates = [row[0] for row in all_values[1:]]
        if today in existing_dates:
            print(f"[GSheet] {today} 데이터 이미 존재 - 스킵")
            for row in all_values[1:]:
                if row[0] == today:
                    for i, oil_type in enumerate(OIL_TYPES):
                        col = 1 + i * 2 + 1  # Change 컬럼
                        if col < len(row) and row[col]:
                            changes[oil_type] = row[col]
            return changes

        # 전일 가격 가져오기 (마지막 데이터 행)
        prev_prices = {}
        if len(all_values) > 1:
            last_row = all_values[-1]
            for i, oil_type in enumerate(OIL_TYPES):
                col = 1 + i * 2  # 가격 컬럼
                if col < len(last_row) and last_row[col]:
                    try:
                        prev_prices[oil_type] = float(last_row[col])
                    except ValueError:
                        pass

        # 변동률 계산 + 행 추가
        row = [today]
        for oil_type in OIL_TYPES:
            price_val = prices.get(oil_type, {}).get("price", "N/A")
            change = ""
            try:
                price = float(price_val)
                price_str = f"{price:.2f}"
                if oil_type in prev_prices and prev_prices[oil_type] > 0:
                    change = f"{(price - prev_prices[oil_type]) / prev_prices[oil_type] * 100:+.2f}%"
                    changes[oil_type] = change
            except (ValueError, TypeError):
                price_str = str(price_val)
            row.extend([price_str, change])

        sheet.append_row(row)
        print(f"[GSheet] {today} 유가 업데이트 완료")
        return changes

    def send_slack_alert(self, prices, changes):
        """Slack 알림 발송"""
        today = datetime.now().strftime("%Y-%m-%d")

        lines = [
            "\U0001f6e2\ufe0f *Oil Price Update*",
            f"날짜: {today}",
            "",
            "```",
            f"{'Oil Type':<15} {'Price ($)':>12} {'Change':>10}",
            f"{'-'*39}",
        ]

        for oil_type in OIL_TYPES:
            data = prices.get(oil_type, {})
            price = data.get("price", "N/A")
            if isinstance(price, (int, float)):
                price_str = f"{price:.2f}"
            else:
                price_str = str(price)
            change = changes.get(oil_type, "-")
            lines.append(f"{oil_type:<15} {price_str:>12} {change:>10}")

        lines.append("```")
        lines.append("_Source: WTI/Brent via Yahoo Finance, Dubai via OilPrice.com_")

        message = "\n".join(lines)

        if self.slack_client:
            try:
                self.slack_client.chat_postMessage(
                    channel=SLACK_CHANNEL,
                    text=message,
                )
                print("[Slack] 유가 알림 발송 완료")
            except SlackApiError as e:
                print(f"[Slack 오류] {e.response['error']}")
        else:
            print(message.replace("*", ""))

    def run(self, gsheet_client=None):
        """가격 조회 → Google Sheet 업데이트 → Slack 알림"""
        print("[Oil] 유가 조회 중...")
        prices = self.fetch_prices()

        if not prices:
            print("[Oil] 유가 데이터 없음")
            return

        print(f"[Oil] {len(prices)}개 유종 가격 조회 완료")

        changes = self.update_google_sheet(prices, gsheet_client)
        self.send_slack_alert(prices, changes)
