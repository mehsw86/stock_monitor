"""
한국 공휴일 체크 유틸리티
- 법정 공휴일 (holidays 라이브러리)
- 임시공휴일 (수동 관리 + 환경변수)
"""

import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

import holidays

KST = ZoneInfo("Asia/Seoul")

# 임시공휴일 목록 (YYYY-MM-DD 형식)
# 정부 발표 시 여기에 추가
TEMPORARY_HOLIDAYS = [
    # "2026-06-04",  # 예시: 임시공휴일
]


def is_korean_holiday(target_date: date = None) -> bool:
    """한국 공휴일 여부 확인 (법정공휴일 + 임시공휴일)

    Args:
        target_date: 확인할 날짜. None이면 한국 시간 기준 오늘.

    Returns:
        공휴일이면 True
    """
    if target_date is None:
        target_date = datetime.now(KST).date()

    # 법정 공휴일 체크 (대체공휴일 포함)
    kr_holidays = holidays.KR(years=target_date.year)
    if target_date in kr_holidays:
        return True

    # 임시공휴일 체크
    temp_holidays = set()
    for d in TEMPORARY_HOLIDAYS:
        try:
            temp_holidays.add(date.fromisoformat(d))
        except ValueError:
            pass

    # 환경변수에서 추가 공휴일 로드 (쉼표 구분, 예: "2026-06-04,2026-10-02")
    extra = os.environ.get("EXTRA_HOLIDAYS", "")
    if extra:
        for d in extra.split(","):
            d = d.strip()
            if d:
                try:
                    temp_holidays.add(date.fromisoformat(d))
                except ValueError:
                    pass

    return target_date in temp_holidays
