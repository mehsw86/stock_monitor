"""
GitHub Actions용 - 유가 체크 (1회 실행)
유가는 주식과 달리 공휴일에도 평일이면 기록
"""
from oil_monitor import OilMonitor


def main():
    monitor = OilMonitor()
    try:
        monitor.run()
    except Exception as e:
        print(f"[오류] 유가 모니터링 실패: {e}")


if __name__ == "__main__":
    main()
