"""
포트폴리오 보유가치 추적
일간 총 평가금액을 Google Sheet에 기록하고 Slack으로 알림
"""

import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pykrx import stock
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

KST = ZoneInfo("Asia/Seoul")

# 보유 종목 (종목코드: {종목명, 보유수량})
HOLDINGS = {
    "091160": {"name": "KODEX 반도체", "shares": 12188},
    "491820": {"name": "HANARO 전력설비투자", "shares": 30212},
    "449450": {"name": "Plus K방산", "shares": 11157},
    "466920": {"name": "SOL 조선TOP3플러스", "shares": 10037},
    "000660": {"name": "SK하이닉스", "shares": 204},
    "005930": {"name": "삼성전자", "shares": 967},
}

STOCK_ORDER = list(HOLDINGS.keys())

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#stock_management")

SPREADSHEET_ID = os.environ.get(
    "GSHEET_SPREADSHEET_ID",
    "1i_q1mMAEU8ucq7JIhGXmuGDN2Ku4MsR4GyKVFxzlwZI",
)
SHEET_NAME = "Portfolio"


class PortfolioTracker:
    def __init__(self):
        self.slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

    def _get_gsheet_client(self):
        """Google Sheet 클라이언트 생성"""
        try:
            import gspread
            creds_json = os.environ.get("GSHEET_CREDENTIALS")
            if creds_json:
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
                print("[Portfolio] GSHEET_CREDENTIALS 미설정 - 시트 업데이트 스킵")
                return None
        except ImportError:
            print("[Portfolio] gspread 미설치 - 시트 업데이트 스킵")
            return None

    def fetch_bulk_prices(self, start_date, end_date):
        """기간 내 전 종목 종가 일괄 조회.

        Args:
            start_date: "YYYYMMDD" 형식
            end_date: "YYYYMMDD" 형식

        Returns:
            {날짜("YYYY-MM-DD"): {종목코드: 종가(int)}}
        """
        all_prices = {}
        for ticker in STOCK_ORDER:
            df = stock.get_market_ohlcv(start_date, end_date, ticker)
            for date_idx, row in df.iterrows():
                date_key = date_idx.strftime("%Y-%m-%d")
                if date_key not in all_prices:
                    all_prices[date_key] = {}
                all_prices[date_key][ticker] = int(row["종가"])
            time.sleep(0.5)
        return all_prices

    def calculate_portfolio(self, prices):
        """종목별 평가액 및 총 평가금액 계산.

        Args:
            prices: {종목코드: 종가(int)}

        Returns:
            {"stocks": {종목코드: {"name", "shares", "price", "value"}}, "total_value": int}
        """
        stocks = {}
        total = 0
        for ticker in STOCK_ORDER:
            info = HOLDINGS[ticker]
            price = prices.get(ticker, 0)
            value = price * info["shares"]
            stocks[ticker] = {
                "name": info["name"],
                "shares": info["shares"],
                "price": price,
                "value": value,
            }
            total += value
        return {"stocks": stocks, "total_value": total}

    def update_google_sheet(self, date_str, portfolio_data, gsheet_client=None):
        """Google Sheet에 포트폴리오 데이터 기록. 전일대비 변동률 반환.

        Args:
            date_str: "YYYY-MM-DD" 형식
            portfolio_data: calculate_portfolio() 반환값
        """
        change_info = {"total_change_pct": "", "total_change_amt": 0}

        if not gsheet_client:
            gsheet_client = self._get_gsheet_client()
            if not gsheet_client:
                return change_info

        try:
            spreadsheet = gsheet_client.open_by_key(SPREADSHEET_ID)
            try:
                sheet = spreadsheet.worksheet(SHEET_NAME)
            except Exception:
                sheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=15)
        except Exception as e:
            print(f"[Portfolio] 시트 열기 실패: {e}")
            return change_info

        try:
            all_values = sheet.get_all_values()
        except Exception:
            all_values = []

        # 헤더 생성 (첫 행이 "Date"가 아니면 헤더 없음으로 판단)
        headers = ["Date"]
        for ticker in STOCK_ORDER:
            headers.append(HOLDINGS[ticker]["name"])
        headers.extend(["총 평가금액", "전일대비(%)"])

        if not all_values or (all_values and all_values[0][0] != "Date"):
            sheet.insert_row(headers, index=1)
            all_values.insert(0, headers)

        # 중복 날짜 체크
        existing_dates = [row[0] for row in all_values[1:]]
        if date_str in existing_dates:
            print(f"[Portfolio] {date_str} 데이터 이미 존재 - 스킵")
            for row in all_values[1:]:
                if row[0] == date_str:
                    total_col = 1 + len(STOCK_ORDER)
                    change_col = total_col + 1
                    if total_col < len(row) and row[total_col]:
                        try:
                            change_info["total_change_amt"] = 0
                        except ValueError:
                            pass
                    if change_col < len(row) and row[change_col]:
                        change_info["total_change_pct"] = row[change_col]
            return change_info

        # 전일 총 평가금액
        prev_total = 0
        if len(all_values) > 1:
            last_row = all_values[-1]
            total_col = 1 + len(STOCK_ORDER)
            if total_col < len(last_row) and last_row[total_col]:
                try:
                    prev_total = int(last_row[total_col].replace(",", ""))
                except (ValueError, AttributeError):
                    prev_total = 0

        # 변동률 계산
        total_value = portfolio_data["total_value"]
        change_pct = ""
        if prev_total > 0:
            pct = (total_value - prev_total) / prev_total * 100
            change_pct = f"{pct:+.2f}%"
            change_info["total_change_pct"] = change_pct
            change_info["total_change_amt"] = total_value - prev_total

        # 행 추가
        row = [date_str]
        for ticker in STOCK_ORDER:
            row.append(portfolio_data["stocks"][ticker]["value"])
        row.extend([total_value, change_pct])

        sheet.append_row(row, value_input_option="USER_ENTERED")

        # 숫자 셀에 콤마 서식 적용 (B~H열)
        row_num = len(all_values) + 1
        sheet.format(f"B{row_num}:H{row_num}", {
            "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}
        })
        print(f"[Portfolio] {date_str} 데이터 기록 완료")
        return change_info

    def send_slack_alert(self, date_str, portfolio_data, change_info):
        """Slack에 포트폴리오 현황 발송"""
        total = portfolio_data["total_value"]
        stocks = portfolio_data["stocks"]

        lines = [f"💰 *포트폴리오 일일 현황* ({date_str})", "```"]

        # 종목별 현황
        header = f"{'종목':<16} {'종가':>10} {'평가금액':>16} {'비중':>6}"
        lines.append(header)
        lines.append("─" * 52)

        for ticker in STOCK_ORDER:
            s = stocks[ticker]
            weight = (s["value"] / total * 100) if total > 0 else 0
            lines.append(
                f"{s['name']:<14} {s['price']:>10,}원 {s['value']:>14,}원 {weight:>5.1f}%"
            )

        lines.append("─" * 52)
        lines.append(f"{'총 평가금액':<14} {'':>10} {total:>14,}원")

        if change_info.get("total_change_pct"):
            amt = change_info.get("total_change_amt", 0)
            lines.append(f"{'전일대비':<14} {'':>10} {amt:>+14,}원 ({change_info['total_change_pct']})")

        lines.append("```")
        message = "\n".join(lines)

        if self.slack_client:
            try:
                self.slack_client.chat_postMessage(
                    channel=SLACK_CHANNEL,
                    text=message,
                )
                print("[Portfolio] Slack 발송 완료")
            except SlackApiError as e:
                print(f"[Portfolio Slack 오류] {e.response['error']}")
        else:
            print(message.replace("*", ""))

    def run(self):
        """오늘 날짜 기준 포트폴리오 업데이트 (일간 실행용)"""
        today = datetime.now(KST)
        date_str = today.strftime("%Y-%m-%d")
        start_pykrx = (today - timedelta(days=7)).strftime("%Y%m%d")
        end_pykrx = today.strftime("%Y%m%d")

        print(f"[Portfolio] {date_str} 포트폴리오 업데이트 시작")

        # 최근 7일 범위로 조회 (당일 데이터 미확정 시 최신 거래일 사용)
        prices = {}
        actual_date = None
        for ticker in STOCK_ORDER:
            df = stock.get_market_ohlcv(start_pykrx, end_pykrx, ticker)
            if not df.empty:
                latest_date = df.index[-1].strftime("%Y-%m-%d")
                if actual_date is None:
                    actual_date = latest_date
                prices[ticker] = int(df["종가"].iloc[-1])
            time.sleep(0.3)

        if not prices:
            print("[Portfolio] 종가 데이터 없음 - 스킵")
            return

        # pykrx에서 반환된 실제 최신 거래일 사용
        if actual_date and actual_date != date_str:
            print(f"[Portfolio] 당일 데이터 미확정, 최신 거래일 사용: {actual_date}")
            date_str = actual_date

        portfolio_data = self.calculate_portfolio(prices)
        change_info = self.update_google_sheet(date_str, portfolio_data)
        self.send_slack_alert(date_str, portfolio_data, change_info)

    def backfill(self, start_date, end_date):
        """과거 데이터 일괄 기록.

        Args:
            start_date: "YYYYMMDD" 형식
            end_date: "YYYYMMDD" 형식
        """
        print(f"[Portfolio] 백필 시작: {start_date} ~ {end_date}")

        gsheet_client = self._get_gsheet_client()
        if not gsheet_client:
            print("[Portfolio] Google Sheet 연결 불가 - 백필 중단")
            return

        # 종목별 종가 일괄 조회
        all_prices = self.fetch_bulk_prices(start_date, end_date)
        if not all_prices:
            print("[Portfolio] 데이터 없음")
            return

        sorted_dates = sorted(all_prices.keys())
        count = 0

        for date_str in sorted_dates:
            prices = all_prices[date_str]
            portfolio_data = self.calculate_portfolio(prices)
            self.update_google_sheet(date_str, portfolio_data, gsheet_client=gsheet_client)
            count += 1
            time.sleep(0.3)

        print(f"[Portfolio] 백필 완료: {count}일 데이터 기록")
