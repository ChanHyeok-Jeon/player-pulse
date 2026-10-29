"""공개 커뮤니티 수집. robots.txt가 허용한 공개 목록만, 낮은 빈도로 가져오고 작성자 정보는 저장하지 않는다."""
import hashlib
import html
import re
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; player-pulse/1.0; portfolio demo; +https://chanhyeok-pm.cloud/pulse/)"
DC_GALLERY = "bser"  # 디시인사이드 이터널 리턴 마이너 갤러리
STEAM_APP = "1049590"  # 이터널 리턴


try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _CTX = ssl.create_default_context()


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
        return r.read().decode("utf-8", errors="ignore")


def _text(s):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()


def collect_dc(since, max_pages=30, delay=1.0, start_page=1):
    """since 이후 글을 목록 페이지에서 가져온다 (공지 제외). 제목·조회·추천·댓글·시각만 저장."""
    posts = []
    for page in range(start_page, start_page + max_pages):
        s = _get(f"https://gall.dcinside.com/mgallery/board/lists/?id={DC_GALLERY}&page={page}")
        rows = re.findall(r'<tr class="ub-content us-post"[^>]*data-no="(\d+)"[^>]*data-type="([^"]+)"(.*?)</tr>', s, re.S)
        oldest = None
        for no, typ, body in rows:
            if "notice" in typ:
                continue
            m = re.search(r'class="gall_date" title="([^"]+)"', body)
            if not m:
                continue
            created = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
            oldest = created if oldest is None else min(oldest, created)
            if created < since:
                continue
            title = re.search(r'<td class="gall_tit ub-word">\s*<a[^>]*>(.*?)</a>', body, re.S)
            num = lambda cls: int(re.sub(r"\D", "", (re.search(rf'class="{cls}">(.*?)</td>', body, re.S) or [None, "0"])[1]) or 0)
            reply = re.search(r'class="reply_num">\[(\d+)', body)
            posts.append({
                "id": f"dc-{no}", "source": "디시 이터널리턴 갤러리",
                "title": _text(title.group(1)) if title else "", "text": "",
                "url": f"https://gall.dcinside.com/mgallery/board/view/?id={DC_GALLERY}&no={no}",
                "created": created.isoformat(), "views": num("gall_count"), "recommends": num("gall_recommend"),
                "comments": int(reply.group(1)) if reply else 0, "vote": None,
            })
        if oldest is None or oldest < since:
            break
        time.sleep(delay)
    return posts


KO_DATE = re.compile(r"(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일")
EN_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"], 1)}
EN_DATE = re.compile(r"(?:Posted:\s*)?([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?|(\d{1,2})\s+([A-Za-z]+)(?:,?\s*(\d{4}))?")


def _parse_date(text, now):
    m = KO_DATE.search(text)
    if m:
        return int(m.group(1) or now.year), int(m.group(2)), int(m.group(3))
    m = EN_DATE.search(text)
    if m:
        if m.group(1) and m.group(1).lower() in EN_MONTHS:
            return int(m.group(3) or now.year), EN_MONTHS[m.group(1).lower()], int(m.group(2))
        if m.group(5) and m.group(5).lower() in EN_MONTHS:
            return int(m.group(6) or now.year), EN_MONTHS[m.group(5).lower()], int(m.group(4))
    return None


def collect_steam(since, lang="koreana", max_pages=5, delay=1.0):
    """스팀 커뮤니티 최신 리뷰 (날짜 단위). 추천/비추천과 본문 앞부분만 저장, 프로필 주소는 해시로만."""
    posts, now = [], datetime.now(KST)
    for page in range(1, max_pages + 1):
        url = (f"https://steamcommunity.com/app/{STEAM_APP}/homecontent/?userreviewsoffset={(page-1)*10}&p={page}"
               f"&numperpage=10&browsefilter=mostrecent&appid={STEAM_APP}&appHubSubSection=10&l={lang}&filterLanguage={lang}")
        s = _get(url)
        cards = re.findall(r'data-modal-content-url="([^"]+)"(.*?)(?=data-modal-content-url=|\Z)', s, re.S)
        if not cards:
            break
        stop = False
        for link, body in cards:
            d = re.search(r'<div class="date_posted">(.*?)</div>', body, re.S)
            ymd = _parse_date(_text(d.group(1)), now) if d else None
            if not ymd:
                continue
            created = datetime(*ymd, 12, 0, tzinfo=KST)  # 날짜만 있어 정오로 둔다
            if created.date() < since.date():
                stop = True
                continue
            txt = re.search(r'<div class="apphub_CardTextContent">(.*?)</div>\s*</div>', body, re.S)
            text = _text(re.sub(r'<div class="date_posted">.*?</div>', "", txt.group(1), flags=re.S)) if txt else ""
            up = '<div class="title">추천</div>' in body or '<div class="title">Recommended</div>' in body
            posts.append({
                "id": "st-" + hashlib.sha1(link.encode()).hexdigest()[:12], "source": "스팀 리뷰",
                "title": text[:80], "text": text[:400], "url": f"https://store.steampowered.com/app/{STEAM_APP}/",
                "created": created.isoformat(), "views": 0, "recommends": 0, "comments": 0, "vote": "up" if up else "down",
            })
        if stop:
            break
        time.sleep(delay)
    return posts
