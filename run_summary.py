"""
GitHub Actions용 - 일일 요약 발송 (1회 실행)
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from stock_monitor import StockMonitor
from holiday_checker import is_korean_holiday
from portfolio_tracker import PortfolioTracker

KST = ZoneInfo("Asia/Seoul")
SUMMARY_STATE_FILE = "summary_state.json"


def main():
    if is_korean_holiday():
        print("[주식] 오늘은 공휴일 - 일일 요약 스킵")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")

    if Path(SUMMARY_STATE_FILE).exists():
        with open(SUMMARY_STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("last_summary_date") == today:
            print(f"[주식] {today} 일일 요약 이미 발송 - 스킵")
            return

    monitor = StockMonitor()
    success = monitor.send_daily_summary()

    if not success:
        print(f"[주식] 일일 요약 발송 실패 - 다음 실행에서 재시도")
        return

    # 포트폴리오 보유가치 업데이트
    try:
        tracker = PortfolioTracker()
        tracker.run()
    except Exception as e:
        print(f"[Portfolio] 포트폴리오 업데이트 실패: {e}")

    with open(SUMMARY_STATE_FILE, "w") as f:
        json.dump({"last_summary_date": today}, f)


if __name__ == "__main__":
    main()
