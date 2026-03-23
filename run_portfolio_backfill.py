"""
1회성 백필: 포트폴리오 보유가치 과거 데이터 (2026-02-19 ~ 2026-03-19)
"""
from portfolio_tracker import PortfolioTracker


def main():
    tracker = PortfolioTracker()
    tracker.backfill("20260219", "20260319")


if __name__ == "__main__":
    main()
