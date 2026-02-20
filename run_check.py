"""
GitHub Actions용 - 종목 체크 (1회 실행)
"""
import os
import json
from pathlib import Path
from stock_monitor import StockMonitor

# 알림 기록 파일 (GitHub Actions 캐시용)
ALERT_FILE = "alerts_today.json"


def load_alerts():
    """오늘 알림 기록 로드"""
    if Path(ALERT_FILE).exists():
        with open(ALERT_FILE, "r") as f:
            return json.load(f)
    return {}


def save_alerts(alerts):
    """알림 기록 저장"""
    with open(ALERT_FILE, "w") as f:
        json.dump(alerts, f)


def main():
    monitor = StockMonitor()

    # 이전 알림 기록 로드
    monitor.alerted_stocks = load_alerts()

    # 종목 체크
    monitor.check_stocks()

    # 알림 기록 저장
    save_alerts(monitor.alerted_stocks)


if __name__ == "__main__":
    main()
