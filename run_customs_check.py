"""
GitHub Actions용 - 관세청 수출입 현황 게시물 체크 (1회 실행)
"""
import json
from pathlib import Path
from customs_monitor import CustomsMonitor

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
    monitor = CustomsMonitor()
    monitor.seen_posts = load_seen()
    try:
        monitor.check_new_posts()
    except Exception as e:
        print(f"[오류] 관세청 모니터링 실패: {e}")
    save_seen(monitor.seen_posts)


if __name__ == "__main__":
    main()
