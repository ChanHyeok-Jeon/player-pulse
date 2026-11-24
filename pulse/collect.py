"""공개 커뮤니티 수집. robots.txt가 허용한 공개 목록만, 낮은 빈도로 가져오고 작성자 정보는 저장하지 않는다."""
import hashlib
import html
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; player-pulse/1.0; portfolio demo; +https://chanhyeok-pm.cloud/pulse/)"
# 수집 대상 게임. 기본은 이터널 리턴(디시 갤러리 + 스팀 리뷰), PULSE_GAME=bdm이면 검은사막 모바일 공식 포럼
GAME = os.environ.get("PULSE_GAME", "er")
DC_GALLERY, DC_PATH, DC_TYPE, DC_SOURCE, STEAM_APP = "bser", "mgallery/board", "M", "디시 이터널리턴 갤러리", "1049590"
FORUM = "https://forum.blackdesertm.com"
FORUM_SOURCE = "검은사막 모바일 공식 포럼"
FORUM_BOARDS = {12: "자유게시판", 16: "클래스 게시판", 84: "건의 및 제보"}  # 공지·이벤트·갤러리 게시판은 뺀다
SOURCE = FORUM_SOURCE if GAME == "bdm" else DC_SOURCE


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


def collect_dc(since, max_pages=30, delay=1.2, start_page=1):
    """since 이후 글을 목록 페이지에서 가져온다 (공지 제외). 제목·조회·추천·댓글·시각만 저장."""
    posts = []
    for page in range(start_page, start_page + max_pages):
        s = _get(f"https://gall.dcinside.com/{DC_PATH}/lists/?id={DC_GALLERY}&page={page}")
        if not s:
            raise BlockedError("디시 목록이 빈 응답입니다 (접근 제한 가능성)")
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
                "id": f"dc-{no}", "source": DC_SOURCE,
                "title": _text(title.group(1)) if title else "", "text": "",
                "url": f"https://gall.dcinside.com/{DC_PATH}/view/?id={DC_GALLERY}&no={no}",
                "created": created.isoformat(), "views": num("gall_count"), "recommends": num("gall_recommend"),
                "comments": int(reply.group(1)) if reply else 0, "vote": None,
            })
        if oldest is None or oldest < since:
            break
        time.sleep(delay)
    return posts


def collect_forum(since, max_pages=15, delay=1.5):
    """검은사막 모바일 공식 포럼 게시판별 목록 (상단 고정 공지 제외). 제목·조회·추천·댓글·시각만 저장."""
    posts = []
    for board, name in FORUM_BOARDS.items():
        for page in range(1, max_pages + 1):
            s = _get(f"{FORUM}/Board?boardNo={board}&pageNo={page}")
            if not s:
                raise BlockedError("포럼 목록이 빈 응답입니다")
            oldest = None
            for it in re.findall(r'<li class="text_list_item[^"]*"[^>]*>(.*?)</li>', s, re.S):
                a = re.search(r'href="/Board/Detail\?boardNo=(\d+)&(?:amp;)?contentNo=(\d+)', it)
                d = re.search(r'class="date only-pc" data-src="([^"]+)"', it)
                if not a or not d or int(a.group(1)) != board:
                    continue  # 모든 게시판 위에 붙는 전체공지
                created = datetime.strptime(d.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
                oldest = created if oldest is None else min(oldest, created)
                if created < since:
                    continue
                sub = re.search(r'class="subject">(.*?)</span>', it, re.S)
                num = lambda pat: int((re.search(pat, it) or [None, "0"])[1])
                posts.append({
                    "id": f"fm-{board}-{a.group(2)}", "source": f"{FORUM_SOURCE} {name}",
                    "title": _text(sub.group(1)) if sub else "", "text": "",
                    "url": f"{FORUM}/Board/Detail?boardNo={board}&contentNo={a.group(2)}",
                    "created": created.isoformat(), "views": num(r'class="view">(\d+)'), "recommends": num(r'class="like">(\d+)'),
                    "comments": num(r'class="replyM only-m">\s*<b>(\d+)'), "vote": None,
                })
            time.sleep(delay)
            if oldest is None or oldest < since:
                break
    return posts


def collect(since, max_pages=40, delay=1.5):
    """게임에 맞는 출처에서 모은다."""
    if GAME == "bdm":
        return collect_forum(since, max_pages=max_pages, delay=delay)
    return collect_dc(since, max_pages=max_pages, delay=delay) + collect_steam(since, max_pages=3)


class BlockedError(RuntimeError):
    """사이트가 빈 응답을 줄 때 (일시적 접근 제한)"""


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
    if not STEAM_APP:
        return []  # 스팀에 없는 게임
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


# ── 글 상세 (본문·댓글) : 리포트에 올릴 후보 글만, 낮은 빈도로 ─────────────
import http.cookiejar
import json as _json

AD_COMMENT = re.compile(r"…|\.\.\.|\"[^\"]{8,}\"|광고|구독|무료\s*나눔|https?://")


def fetch_dc_detail(post_id, max_comments=20):
    """디시 글의 본문 텍스트, 사진·영상 수, 댓글(광고·뉴스성 댓글 제외)을 가져온다."""
    no = post_id.split("-", 1)[1]
    url = f"https://gall.dcinside.com/{DC_PATH}/view/?id={DC_GALLERY}&no={no}"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=_CTX))
    s = opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20).read().decode("utf-8", errors="ignore")
    if not s:
        return None  # 빈 응답: 접근 제한 중
    i = s.find('<div class="write_div"')
    seg = s[i:i + 8000] if i >= 0 else ""
    if '<div class="btn_recommend_box' in seg:
        seg = seg[:seg.find('<div class="btn_recommend_box')]
    media = len(re.findall(r"<img |<video|<iframe", seg))
    seg = re.sub(r"<script.*?</script>", " ", seg, flags=re.S)
    body = _text(seg).replace("- dc official App", "").strip()
    comments = []
    m = re.search(r'name="e_s_n_o" value="([^"]+)"', s)
    if m:
        data = urllib.parse.urlencode({"id": DC_GALLERY, "no": no, "cmt_id": DC_GALLERY, "cmt_no": no, "e_s_n_o": m.group(1),
                                       "comment_page": 1, "sort": "", "prevCnt": "", "board_type": "", "_GALLTYPE_": DC_TYPE}).encode()
        req = urllib.request.Request("https://gall.dcinside.com/board/comment/", data=data,
                                     headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest", "Referer": url})
        try:
            for c in (_json.loads(opener.open(req, timeout=20).read().decode("utf-8", errors="ignore")).get("comments") or []):
                memo = _text(c.get("memo") or "").replace("- dc App", "").strip()
                if memo and not AD_COMMENT.search(memo) and len(memo) <= 120:
                    comments.append(memo)
        except (ValueError, OSError):
            pass
    return {"body": body[:500], "media": media, "comments": comments[:max_comments]}


def fetch_forum_detail(post_id, max_comments=20):
    """포럼 글의 본문 텍스트, 사진·영상 수, 댓글 첫 페이지를 가져온다. 작성자·IP는 읽지 않는다."""
    _, board, no = post_id.split("-", 2)
    url = f"{FORUM}/Board/Detail?boardNo={board}&contentNo={no}"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=_CTX))
    s = opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20).read().decode("utf-8", errors="ignore")
    if not s:
        return None
    i = s.find('<div class="contents_area')
    seg = s[i:s.find('<div class="infoArea', i)] if i >= 0 else ""
    media = len(re.findall(r"<img |<video|<iframe", seg))
    body = _text(re.sub(r"<script.*?</script>", " ", seg, flags=re.S))
    comments = []
    j = s.find('id="frmCommentList"')
    if j >= 0:
        form = s[s.rfind("<form", 0, j):s.find("</form>", j)]
        data = urllib.parse.urlencode(dict(re.findall(r'name="([^"]+)" type="hidden" value="([^"]*)"', form))).encode()
        req = urllib.request.Request(f"{FORUM}/Board/CommentList", data=data,
                                     headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest", "Referer": url})
        try:
            views = _json.loads(opener.open(req, timeout=20).read().decode("utf-8", errors="ignore")).get("views") or ""
            for c in re.findall(r'<div class="replyText"[^>]*>(.*?)</div>', views, re.S):
                memo = _text(c)
                if memo and not AD_COMMENT.search(memo) and len(memo) <= 120:
                    comments.append(memo)
        except (ValueError, OSError):
            pass
    return {"body": body[:500], "media": media, "comments": comments[:max_comments]}


def fetch_detail(post):
    """글 출처에 맞게 본문·댓글을 연다."""
    return fetch_forum_detail(post["id"]) if post["id"].startswith("fm-") else fetch_dc_detail(post["id"])
