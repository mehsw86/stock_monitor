"""
GitHub Actions용 - 일일 요약 발송 (1회 실행)
"""
from stock_monitor import StockMonitor


def main():
    monitor = StockMonitor()
    monitor.send_daily_summary()


if __name__ == "__main__":
    main()
