"""
GitHub Actions용 - 일일 요약 발송 (1회 실행)
"""
import json
from datetime import datetime
from pathlib import Path
from stock_monitor import StockMonitor
from holiday_checker import is_korean_holiday

SUMMARY_STATE_FILE = "summary_state.json"


def main():
    if is_korean_holiday():
        print("[주식] 오늘은 공휴일 - 일일 요약 스킵")
        return

    today = datetime.now().strftime("%Y-%m-%d")

    if Path(SUMMARY_STATE_FILE).exists():
        with open(SUMMARY_STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("last_summary_date") == today:
            print(f"[주식] {today} 일일 요약 이미 발송 - 스킵")
            return

    monitor = StockMonitor()
    monitor.send_daily_summary()

    with open(SUMMARY_STATE_FILE, "w") as f:
        json.dump({"last_summary_date": today}, f)


if __name__ == "__main__":
    main()
