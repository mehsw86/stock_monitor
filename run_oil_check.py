"""
GitHub Actions용 - 유가 체크 (1회 실행)
"""
import sys
from oil_monitor import OilMonitor
from holiday_checker import is_korean_holiday


def main():
    force = "--force" in sys.argv
    if not force and is_korean_holiday():
        print("[Oil] 오늘은 공휴일 - 스킵")
        return

    monitor = OilMonitor()
    try:
        monitor.run()
    except Exception as e:
        print(f"[오류] 유가 모니터링 실패: {e}")


if __name__ == "__main__":
    main()
