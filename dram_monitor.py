"""
DRAMeXchange Spot Price 모니터링
DDR5, GDDR6, NAND TLC Session Average 가격 추적
Google Sheet 업데이트 + Slack 알림
"""

import os
import re
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

DRAM_URL = "https://www.dramexchange.com/"

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#stock_management")

# 모니터링 대상 모델
TARGET_ITEMS = [
    "DDR5 16Gb (2Gx8) 4800/5600",
    "DDR5 16Gb (2Gx8) eTT",
    "GDDR6 8Gb",
    "512Gb TLC",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# Google Sheets 설정
SPREADSHEET_ID = os.environ.get(
    "GSHEET_SPREADSHEET_ID",
    "1i_q1mMAEU8ucq7JIhGXmuGDN2Ku4MsR4GyKVFxzlwZI",
)
SHEET_NAME = "DRAM"


class DramMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

    def fetch_prices(self):
        """DRAMeXchange에서 대상 모델 Session Average 가격 추출"""
        resp = self.session.get(DRAM_URL, timeout=15)
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        prices = {}
        seen = set()

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            # 헤더에 Session Average가 있는 테이블만 처리
            header_text = rows[0].get_text() if rows else ""
            if "Session Average" not in header_text:
                continue

            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 7:
                    continue

                item_name = cells[0].get_text(strip=True)
                # 공백 정규화
                item_normalized = re.sub(r"\s+", " ", item_name)

                for target in TARGET_ITEMS:
                    target_normalized = re.sub(r"\s+", " ", target)
                    if target_normalized == item_normalized and target not in seen:
                        session_avg = cells[5].get_text(strip=True)
                        session_change = cells[6].get_text(strip=True)
                        prices[target] = {
                            "session_avg": session_avg,
                            "session_change": session_change,
                        }
                        seen.add(target)

        return prices

    def _get_gsheet_client(self):
        """Google Sheet 클라이언트 생성"""
        try:
            import gspread
            creds_json = os.environ.get("GSHEET_CREDENTIALS")
            if creds_json:
                import json
                import tempfile
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
            sheet = gsheet_client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
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
            for item in TARGET_ITEMS:
                headers.extend([item, f"{item} Change"])
            sheet.append_row(headers)
            all_values = [headers]

        # 중복 날짜 체크
        existing_dates = [row[0] for row in all_values[1:]]
        if today in existing_dates:
            print(f"[GSheet] {today} 데이터 이미 존재 - 스킵")
            # 기존 데이터에서 변동률 읽기
            for row in all_values[1:]:
                if row[0] == today:
                    for i, item in enumerate(TARGET_ITEMS):
                        col = 1 + i * 2 + 1  # Change 컬럼
                        if col < len(row) and row[col]:
                            changes[item] = row[col]
            return changes

        # 전일 가격 가져오기 (마지막 데이터 행)
        prev_prices = {}
        if len(all_values) > 1:
            last_row = all_values[-1]
            for i, item in enumerate(TARGET_ITEMS):
                col = 1 + i * 2  # 가격 컬럼
                if col < len(last_row) and last_row[col]:
                    try:
                        prev_prices[item] = float(last_row[col])
                    except ValueError:
                        pass

        # 변동률 계산 + 행 추가
        row = [today]
        for item in TARGET_ITEMS:
            price_str = prices.get(item, {}).get("session_avg", "N/A")
            change = ""
            try:
                price = float(price_str)
                if item in prev_prices and prev_prices[item] > 0:
                    change = f"{(price - prev_prices[item]) / prev_prices[item] * 100:+.2f}%"
                    changes[item] = change
            except (ValueError, TypeError):
                pass
            row.extend([price_str, change])

        sheet.append_row(row)
        print(f"[GSheet] {today} 가격 업데이트 완료")
        return changes

    def send_slack_alert(self, prices, changes):
        """Slack 알림 발송"""
        today = datetime.now().strftime("%Y-%m-%d")

        lines = [
            f"💾 *DRAM/NAND Spot Price Update*",
            f"날짜: {today}",
            "",
            "```",
            f"{'Item':<35} {'Avg ($)':>8} {'Change':>8}",
            f"{'-'*53}",
        ]

        for item in TARGET_ITEMS:
            data = prices.get(item, {})
            avg = data.get("session_avg", "N/A")
            change = changes.get(item, "-")
            lines.append(f"{item:<35} {avg:>8} {change:>8}")

        lines.append("```")
        lines.append(f"_Source: DRAMeXchange_")

        message = "\n".join(lines)

        if self.slack_client:
            try:
                self.slack_client.chat_postMessage(
                    channel=SLACK_CHANNEL,
                    text=message,
                )
                print(f"[Slack] DRAM 가격 알림 발송 완료")
            except SlackApiError as e:
                print(f"[Slack 오류] {e.response['error']}")
        else:
            print(message.replace("*", ""))

    def run(self, gsheet_client=None):
        """가격 조회 → Google Sheet 업데이트 → Slack 알림"""
        print("[DRAM] 가격 조회 중...")
        prices = self.fetch_prices()

        if not prices:
            print("[DRAM] 가격 데이터 없음")
            return

        print(f"[DRAM] {len(prices)}개 모델 가격 조회 완료")
        for item, data in prices.items():
            print(f"  {item}: ${data['session_avg']}")

        changes = self.update_google_sheet(prices, gsheet_client)
        self.send_slack_alert(prices, changes)
