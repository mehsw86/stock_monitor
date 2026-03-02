"""
GitHub Actions용 - 관세청 수출입 현황 게시물 체크 (1회 실행)
"""
import json
from datetime import datetime
from pathlib import Path
from customs_monitor import CustomsMonitor
from holiday_checker import is_korean_holiday

SEEN_FILE = "customs_seen.json"


def load_seen():
    if Path(SEEN_FILE).exists():
        with open(SEEN_FILE, "r") as f:
            return json.load(f)
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, ensure_ascii=False)


def main():
    if is_korean_holiday():
        print("[관세청] 오늘은 공휴일 - 스킵")
        return

    data = load_seen()
    seen_posts = data.get("posts", data if isinstance(data, dict) and "last_run_date" not in data else {})
    last_run_date = data.get("last_run_date", "")
    today = datetime.now().strftime("%Y-%m-%d")

    if last_run_date == today:
        print(f"[관세청] {today} 이미 체크 완료 - 스킵")
        return

    monitor = CustomsMonitor()
    monitor.seen_posts = seen_posts
    try:
        monitor.check_new_posts()
    except Exception as e:
        print(f"[오류] 관세청 모니터링 실패: {e}")

    save_seen({"posts": monitor.seen_posts, "last_run_date": today})


if __name__ == "__main__":
    main()
