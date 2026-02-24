"""
관세청 보도자료 게시판 모니터링
구분 '정보데이터' + 제목 '수출입 현황' 게시물 감지 시 Slack 알림
PDF 첨부파일에서 수출입 핵심 수치 추출
"""

import os
import re
import time
import tempfile
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pdfplumber
from bs4 import BeautifulSoup
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

BOARD_URL = "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do"
DETAIL_URL = "https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do"
BOARD_PARAMS = {"mi": "2891", "bbsId": "1362"}

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#stock_management")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


class CustomsMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        retry = Retry(total=3, backoff_factor=5, status_forcelist=[500, 502, 503])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None
        self.seen_posts = {}  # {ntt_sn: title}

    def fetch_board_list(self):
        """게시판 목록에서 '정보데이터' + '수출입 현황' 게시물 추출"""
        resp = self.session.get(BOARD_URL, params=BOARD_PARAMS, timeout=15)
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        posts = []

        for link in soup.find_all("a", class_="nttInfoBtn"):
            row = link.find_parent("tr")
            if not row:
                continue

            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            category = cells[1].get_text(strip=True)
            title = link.get_text(strip=True).replace("새글", "")
            ntt_sn = link.get("data-id", "")
            ntt_sn_url = link.get("data-url", "")
            date = cells[-2].get_text(strip=True) if len(cells) >= 5 else ""

            if "정보데이터" in category and "수출입 현황" in title:
                posts.append({
                    "ntt_sn": ntt_sn,
                    "ntt_sn_url": ntt_sn_url,
                    "title": title,
                    "date": date,
                })

        return posts

    def fetch_post_detail(self, ntt_sn, ntt_sn_url):
        """게시물 상세 페이지에서 PDF 다운로드 URL 추출"""
        self.session.get(BOARD_URL, params=BOARD_PARAMS, timeout=15)

        form_data = {
            "bbsId": "1362",
            "nttSn": ntt_sn,
            "nttSnUrl": ntt_sn_url,
            "mi": "2891",
            "currPage": "1",
            "searchValue": "",
        }

        resp = self.session.post(DETAIL_URL, data=form_data, timeout=15)
        resp.encoding = "utf-8"

        if "존재하지않습니다" in resp.text or "유효하지 않은" in resp.text:
            print(f"[오류] 게시물 상세 조회 실패: nttSn={ntt_sn}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # PDF 첨부파일 링크 찾기
        pdf_info = None
        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "")
            text = a_tag.get_text(strip=True)
            if "nttFileDownload" in href and ".pdf" in text.lower():
                pdf_info = {
                    "url": "https://www.customs.go.kr" + href,
                    "filename": re.sub(r"\s*\[.*", "", text).strip(),
                }
                break

        return pdf_info

    def download_pdf(self, pdf_url):
        """PDF 파일 다운로드 후 임시 파일 경로 반환"""
        resp = self.session.get(pdf_url, timeout=30)
        if resp.status_code != 200:
            print(f"[오류] PDF 다운로드 실패: {resp.status_code}")
            return None

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(resp.content)
        tmp.close()
        return tmp.name

    def extract_pdf_summary(self, pdf_path):
        """PDF에서 당월 수출 실적, 연간누계 실적, 반도체 수출 실적 추출"""
        texts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    texts.append(text)

        full_text = "\n".join(texts)
        summary = {}

        # 1. 당월 수출/수입/무역수지 (페이지 1 요약 표)
        # "수출 435억 달러" 패턴
        m = re.search(r"수출은?\s*([\d,.]+)억 달러.*?(\d+\.\d+)%\s*증가", full_text.replace("\n", " "))
        if m:
            summary["당월_수출"] = f"{m.group(1)}억 달러 (전년동기대비 +{m.group(2)}%)"

        m = re.search(r"수입은?\s*([\d,.]+)억?\s*달러.*?(\d+\.\d+)%\s*증가", full_text.replace("\n", " "))
        if m:
            summary["당월_수입"] = f"{m.group(1)}억 달러 (전년동기대비 +{m.group(2)}%)"

        m = re.search(r"무역수지는?\s*([\d,.]+)억 달러\s*(흑자|적자)", full_text.replace("\n", " "))
        if m:
            summary["무역수지"] = f"{m.group(1)}억 달러 {m.group(2)}"

        # 2. 표에서 전월/당월/연간누계 추출
        # 열 순서: 2025당월, 2025연간누계, 2026전월, 2026당월, 2026연간누계
        m = re.search(
            r"수\s*출\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)",
            full_text,
        )
        if m:
            export_prev = int(m.group(3).replace(",", ""))
            export_cur = int(m.group(4).replace(",", ""))
            export_annual = int(m.group(5).replace(",", ""))
            mom_rate = (export_cur - export_prev) / export_prev * 100
            summary["전월_수출"] = f"{export_prev/100:.1f}억 달러"
            summary["전월대비_수출"] = f"{mom_rate:+.1f}%"
            summary["연간누계_수출"] = f"{export_annual/100:.1f}억 달러"

        m = re.search(
            r"수\s*입\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)",
            full_text,
        )
        if m:
            import_prev = int(m.group(3).replace(",", ""))
            import_cur = int(m.group(4).replace(",", ""))
            import_annual = int(m.group(5).replace(",", ""))
            mom_rate = (import_cur - import_prev) / import_prev * 100
            summary["전월_수입"] = f"{import_prev/100:.1f}억 달러"
            summary["전월대비_수입"] = f"{mom_rate:+.1f}%"
            summary["연간누계_수입"] = f"{import_annual/100:.1f}억 달러"

        # 연간누계 증감률
        rates = re.findall(r"\(전년동기대비증감률\)\s*\([\d.△]+\)\s*\([\d.△]+\)\s*\([\d.△]+\)\s*\([\d.△]+\)\s*\(([\d.△]+)\)", full_text)
        if len(rates) >= 2:
            summary["연간누계_수출_증감률"] = f"+{rates[0]}%"
            summary["연간누계_수입_증감률"] = f"+{rates[1]}%"

        # 3. 반도체 수출 실적 (붙임 표)
        m = re.search(r"반\s*도\s*체\s+([\d,]+)\s+([\d.△]+)", full_text)
        if m:
            semi_amount = int(m.group(1).replace(",", ""))
            semi_rate = m.group(2)
            summary["반도체_수출"] = f"{semi_amount/100:.1f}억 달러 (+{semi_rate}%)"

        # 반도체 수출 비중
        m = re.search(r"반도체 수출 비중은?\s*([\d.]+)%", full_text)
        if m:
            summary["반도체_비중"] = f"{m.group(1)}%"

        return summary

    def format_slack_message(self, title, date, summary):
        """Slack 메시지 포맷팅"""
        lines = [
            f"📢 *관세청 수출입 현황 발표*",
            f"*{title}*",
            f"등록일: {date}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "*📊 당월 수출입 실적*",
        ]

        if "당월_수출" in summary:
            lines.append(f"  🔺 수출: {summary['당월_수출']}")
        if "당월_수입" in summary:
            lines.append(f"  🔽 수입: {summary['당월_수입']}")
        if "무역수지" in summary:
            lines.append(f"  💰 무역수지: {summary['무역수지']}")

        lines.append("")
        lines.append("*📅 전월 수출입 실적*")
        if "전월_수출" in summary:
            lines.append(f"  🔺 수출: {summary['전월_수출']}")
        if "전월_수입" in summary:
            lines.append(f"  🔽 수입: {summary['전월_수입']}")
        if "전월대비_수출" in summary:
            lines.append(f"  📊 전월대비 증감: 수출 {summary['전월대비_수출']}, 수입 {summary.get('전월대비_수입', 'N/A')}")

        lines.append("")
        lines.append("*📈 연간누계 실적*")
        if "연간누계_수출" in summary:
            rate = summary.get("연간누계_수출_증감률", "")
            lines.append(f"  🔺 수출: {summary['연간누계_수출']} {rate}")
        if "연간누계_수입" in summary:
            rate = summary.get("연간누계_수입_증감률", "")
            lines.append(f"  🔽 수입: {summary['연간누계_수입']} {rate}")

        lines.append("")
        lines.append("*🔬 반도체 수출*")
        if "반도체_수출" in summary:
            lines.append(f"  수출액: {summary['반도체_수출']}")
        if "반도체_비중" in summary:
            lines.append(f"  수출 비중: {summary['반도체_비중']}")

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🔗 <https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362|관세청 보도자료 바로가기>")

        return "\n".join(lines)

    def _resolve_channel_id(self):
        """채널 이름으로 채널 ID 조회 (chat_postMessage 활용)"""
        try:
            resp = self.slack_client.chat_postMessage(channel=SLACK_CHANNEL, text=".")
            channel_id = resp["channel"]
            self.slack_client.chat_delete(channel=channel_id, ts=resp["ts"])
            return channel_id
        except SlackApiError:
            return None

    def send_slack_alert(self, title, date, summary, pdf_path=None, pdf_filename=None):
        """Slack 알림 발송 (PDF 첨부 포함)"""
        message = self.format_slack_message(title, date, summary)

        if self.slack_client:
            try:
                if pdf_path and pdf_filename:
                    channel_id = self._resolve_channel_id()
                    if channel_id:
                        self.slack_client.files_upload_v2(
                            channel=channel_id,
                            file=pdf_path,
                            filename=pdf_filename,
                            initial_comment=message,
                        )
                        print(f"[Slack] 관세청 알림 발송 완료 (PDF 첨부): {title}")
                        return

                self.slack_client.chat_postMessage(
                    channel=SLACK_CHANNEL,
                    text=message,
                )
                print(f"[Slack] 관세청 알림 발송 완료: {title}")
            except SlackApiError as e:
                print(f"[Slack 오류] {e.response['error']}")
        else:
            print(f"\n{'='*50}")
            print(message.replace("*", ""))
            print(f"{'='*50}\n")

    def check_new_posts(self):
        """신규 게시물 확인 및 알림"""
        print("[관세청] 게시판 확인 중...")

        posts = self.fetch_board_list()
        print(f"[관세청] '수출입 현황' 게시물 {len(posts)}건 발견")

        new_count = 0
        for post in posts:
            ntt_sn = post["ntt_sn"]

            if ntt_sn in self.seen_posts:
                print(f"  [이미 알림] {post['title']}")
                continue

            print(f"  [신규] {post['title']} - 상세 조회 중...")
            pdf_info = self.fetch_post_detail(ntt_sn, post["ntt_sn_url"])

            pdf_path = None
            pdf_filename = None
            summary = {}

            if pdf_info:
                print(f"  [PDF] 다운로드 중: {pdf_info['filename']}")
                pdf_path = self.download_pdf(pdf_info["url"])
                pdf_filename = pdf_info["filename"]

                if pdf_path:
                    print(f"  [PDF] 수치 추출 중...")
                    summary = self.extract_pdf_summary(pdf_path)

            if not summary:
                summary = {"당월_수출": "데이터 추출 실패 - 첨부파일 확인 필요"}

            self.send_slack_alert(post["title"], post["date"], summary, pdf_path, pdf_filename)

            # 임시 파일 정리
            if pdf_path:
                os.unlink(pdf_path)

            self.seen_posts[ntt_sn] = post["title"]
            new_count += 1

        print(f"[관세청] 신규 알림 {new_count}건 발송 완료")
