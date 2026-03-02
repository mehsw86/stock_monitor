"""
GitHub Actions용 - DRAM 가격 체크 (1회 실행)
"""
from dram_monitor import DramMonitor
from holiday_checker import is_korean_holiday


def main():
    if is_korean_holiday():
        print("[DRAM] 오늘은 공휴일 - 스킵")
        return

    monitor = DramMonitor()
    try:
        monitor.run()
    except Exception as e:
        print(f"[오류] DRAM 모니터링 실패: {e}")


if __name__ == "__main__":
    main()
