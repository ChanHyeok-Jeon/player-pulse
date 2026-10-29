"""수집 글 → 주제 묶기(AI) → 중요도 5단계(코드).

AI는 비슷한 글을 묶고 이름·부서·감정만 붙인다. 글 수, 반응(조회·댓글·추천), 부정 비율,
심각 키워드로 중요도를 계산하는 것은 코드가 한다. AI가 든 글 번호는 실제 수집 글인지 다시 확인한다.
"""
import json
import os
import re
from datetime import datetime, timedelta

import boto3

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "apac.amazon.nova-pro-v1:0")
DEPTS = ["밸런스", "클라이언트·서버", "매칭·랭크", "운영·제재", "콘텐츠·이벤트", "BM·상점", "UX·신규 유저", "커뮤니티·기타"]
# 짧은 단어는 다른 낱말 안에 들어가기 쉬워 경계를 둔다 (알'렉'스, 보이스타이'핑', '핵'심 등)
LAG = r"(?<!알)(?<!디)렉(?!스|터|션)"
PING = r"(?<!타이)(?<!티니)핑[^.?!\n]{0,6}?(?:튀|높|폭|씹|ms|\d|렉|끊)"
HACK = r"(?<![가-힣])핵(?!심|펀치|폭|과|가족|노잼)"
SEVERE = re.compile(r"튕|크래시|강종|접속\s*(?:불가|안)|서버\s*(?:다운|터|불안|문제|렉)|" + LAG + "|" + PING +
                    r"|프레임|버그|오류|" + HACK + r"|치트|결제|환불|먹튀|매칭\s*(?:안|오류)|무한\s*로딩|롤백|계정|보안")
WORD = {"렉": LAG, "핑": r"(?<!타이)(?<!티니)핑", "핵": HACK}
# ●●●●●(긴급)는 장애성 글이 실제로 있을 때만
CRITICAL = re.compile(r"서버\s*(?:다운|터|폭파|죽|마비|불안)|접속\s*(?:불가|안\s*[됨돼되])|튕|크래시|강종|강제\s*종료|먹통|무한\s*로딩|"
                      r"결제\s*(?:오류|안\s*[됨돼되]|실패)|환불|(?:서버|계정|데이터)\s*롤백|계정\s*(?:삭제|증발|날아|해킹)|데이터\s*(?:날아|증발|초기화)|점검\s*연장|접속\s*장애")
# ●●●●●는 개인의 튕김이 아니라 여러 사람이 동시에 겪는 장애일 때만: 2시간 안에 이런 글이 3건 이상
OUTAGE = re.compile(r"서버\s*(?:다운|터|폭파|죽|마비|불안)|접속\s*(?:불가|안\s*[됨돼되])|(?:다|전부|단체로|다같이|모두|전원)\s*튕|무한\s*로딩|무한\s*로그인|"
                    r"로그인\s*(?:안\s*[됨돼되]|불가)|결제\s*(?:오류|안\s*[됨돼되]|실패)|(?:서버|계정|데이터)\s*롤백|계정\s*(?:삭제|증발|날아|해킹)|"
                    r"데이터\s*(?:날아|증발|초기화)|점검\s*연장|긴급\s*점검|접속\s*장애")
CRUDE = re.compile(r"씨\s*발|시발|ㅅㅂ|ㅆㅂ|좆|졷|존나|ㅈㄴ|병신|ㅄ|새끼|개빡|빡통|ㅈ버그|응아|븅신")  # 근거 링크로는 욕설 없는 글을 먼저 보여 준다
SITE = re.compile(r"디시|공앱|갤러리|갤에|주딱|념글|개념글|글삭|차단당")  # 커뮤니티 사이트 자체 이야기는 게임 이슈가 아니다
GAME = {"-", "이리", "이터리", "이터널리턴", "이터널 리턴", "겜", "게임", "스팀", "이 게임"}
ISSUE_WORDS = {"버그", "핑", "렉", "정지", "제재", "매칭", "튕김", "서버", "결제", "환불", "핵", "로딩", "프레임", "랭크"}
STOP = {"제보", "증상", "현상", "발생", "불만", "요구", "요청", "문제", "관련", "논의", "신고", "불편", "이슈", "반응", "의견", "및", "대한", "많음", "증가", "호평", "비판", "질문", "개선"}
VAGUE = re.compile(r"특정|일부 |게임 내 |기타|잡담|밈|팁|공략|가이드|빌드 추천|질문 모음")
LEVEL = {5: "긴급", 4: "높음", 3: "보통", 2: "낮음", 1: "참고"}
# 글의 성격. 질문·잡담은 조치할 이슈가 아니므로 리포트에서 뺀다
KINDS = ["장애", "버그", "네트워크", "운영", "BM", "UX", "밸런스", "콘텐츠", "질문", "잡담"]
GENERIC_TARGET = re.compile(r"^(?:여러|각종|기타|전체|개별)?\s*(?:버그|오류|장애|서버·접속 장애|네트워크 지연|서버|클라이언트|운영|상점|이벤트)$")

def window(now, send_time, weekdays):
    """이번 발송이 다룰 기간: 직전 발송 시각 ~ 지금. 월요일이면 금요일 발송 이후 주말 전체."""
    h, m = map(int, send_time.split(":"))
    end = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=1)
    start = end - timedelta(days=1)
    while start.weekday() not in weekdays and (end - start).days < 7:
        start -= timedelta(days=1)
    return start, end


def _engagement(p):
    return p["views"] / 50 + p["comments"] * 2 + p["recommends"] * 3


def pick(posts, limit=600):
    """AI에 보낼 글 고르기: 반응이 큰 글과 심각 키워드 글 우선."""
    scored = sorted(posts, key=lambda p: (bool(SEVERE.search(p["title"] + p["text"])), _engagement(p)), reverse=True)
    return scored[:limit]


CLASSIFY = """당신은 이터널 리턴 개발팀 PM을 돕는 보조입니다. 커뮤니티 글 제목을 하나씩 읽고 성격을 분류하세요.
종류 (하나만):
  장애: 서버 다운·접속 불가·튕김·결제 오류·계정 손실 / 버그: 게임이 의도와 다르게 동작한다는 제보 /
  네트워크: 핑·렉·끊김 같은 지연 문제 / 운영: 핵 방치·오탐 정지·제재 기준처럼 운영 조치가 필요한 내용 /
  BM: 상점·가격·과금 / UX: 불편한 UI·조작·매칭 대기·신규 유저 진입 장벽 / 밸런스: 실험체·아이템이 세다·약하다며 조정을 요구 /
  콘텐츠: 이벤트·패치·신규 콘텐츠에 대한 반응 / 질문: 공략·빌드·사용법·"이것도 정지임?" 같은 질문 / 잡담: 플레이 자랑·농담·감상·특정 개인 이야기
- 본인이 정지당한 이유를 묻거나 하소연하는 글은 운영이 아니라 질문입니다. 게임 안 신호(핑 찍기) 이야기는 잡담입니다.
- 스킬로 적을 튕겨 내는 기술 이야기는 장애가 아닙니다. 디시인사이드 앱·디시콘·갤러리 운영 이야기는 게임과 무관하므로 잡담입니다.
- 대상: 제목에 실제로 쓰인 실험체·아이템·시스템·모드 이름 하나 (예: 알렉스, 레니, 코발트, 랭크, 상점). 없으면 -
- 감정: 부정·긍정·중립
- 요지: 무엇이 어떻다는 글인지 15자 이내. 욕설은 옮기지 마세요.
각 글마다 한 줄로만 답하세요: 번호 | 종류 | 대상 | 감정 | 요지"""
NAME = """각 묶음의 글 요지를 보고, 개발팀이 바로 알아볼 수 있는 주제 이름을 한 줄로 지으세요.
- 대상 앞에 종류를 붙이세요 (예: "실험체 알렉스 무기 슬롯이 자동으로 바뀌는 불편", "랭크 매칭 대기 시간 증가 불만", "KT 회선 핑 튐 불만").
- '특정', '일부', '관련 논의' 같은 막연한 말은 쓰지 말고, 욕설은 옮기지 마세요. 개인 이름은 넣지 마세요.
각 묶음마다 한 줄로만 답하세요: 묶음 번호 | 주제 이름"""
CLINE = re.compile(r"^\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(.+?)\s*\|\s*(부정|긍정|중립)\s*\|\s*(.*)$")
KIND_DEPT = {"장애": "클라이언트·서버", "버그": "클라이언트·서버", "네트워크": "클라이언트·서버", "운영": "운영·제재", "BM": "BM·상점",
             "UX": "UX·신규 유저", "밸런스": "밸런스", "콘텐츠": "콘텐츠·이벤트"}
KIND_LABEL = {"장애": "서버·접속 장애", "버그": "여러 버그", "네트워크": "네트워크 지연"}  # 대상이 제각각이어도 한데 모아 보는 종류
KIND_TITLE = {"장애": "튕김·접속 문제 제보", "버그": "버그 제보 모음", "네트워크": "네트워크 지연(핑·렉) 제보"}


def _ask(client, system, text, max_tokens=4000):
    res = client.converse(modelId=MODEL_ID, system=[{"text": system}], messages=[{"role": "user", "content": [{"text": text}]}],
                          inferenceConfig={"maxTokens": max_tokens, "temperature": 0})
    return "".join(c.get("text", "") for c in res["output"]["message"]["content"])


def classify(posts, batch=100):
    """AI가 글마다 종류·대상·감정·요지를 붙인다. 한 번에 수백 개를 묶게 하면 모델이 큰 덩어리 하나로 뭉개서, 글 단위 분류로 바꿨다."""
    from concurrent.futures import ThreadPoolExecutor
    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))

    def run(k):
        part = posts[k:k + batch]
        text = "\n".join(f"{i + 1} | {p['title'][:70]}" for i, p in enumerate(part))
        got = {}
        for _ in range(2):
            for ln in _ask(client, CLASSIFY, text).splitlines():
                m = CLINE.match(ln)
                if m and 0 < int(m.group(1)) <= len(part):
                    got[part[int(m.group(1)) - 1]["id"]] = {"kind": m.group(2), "target": m.group(3).strip(), "sentiment": m.group(4), "gist": m.group(5).strip()}
            if len(got) >= len(part) * .7:
                break
        return got

    tags = {}
    with ThreadPoolExecutor(6) as ex:
        for got in ex.map(run, range(0, len(posts), batch)):
            tags.update(got)
    return tags


def cluster(posts):
    """글 단위 분류 → 같은 종류·대상끼리 코드가 묶음 → AI가 묶음마다 이름만 짓는다."""
    tags = classify(posts)
    if not tags:
        raise RuntimeError("AI가 글을 분류하지 못했습니다.")
    groups = {}
    for p in posts:
        g = tags.get(p["id"])
        if not g or g["kind"] not in KIND_DEPT or SITE.search(p["title"]):
            continue  # 질문·잡담·분류 실패·사이트 이야기는 이슈가 아니다
        target = re.sub(r"^(?:실험체|아이템|시스템|모드)\s*", "", g["target"]).strip() or "-"
        target = "-" if target in GAME else target  # '이리'(게임 줄임말)는 대상이 아니다
        groups.setdefault((g["kind"], target), []).append(p["id"])
    # 대상이 제각각인 버그·장애·네트워크 글, 대상이 없는 글은 종류별로 한데 모은다
    merged = {}
    for (kind, target), ids in groups.items():
        if kind in KIND_LABEL and (len(ids) < 2 or target == "-"):
            key = (kind, KIND_LABEL[kind])
        elif target == "-":
            continue
        else:
            key = (kind, target)
        merged.setdefault(key, []).extend(ids)
    merged = {k: v for k, v in merged.items() if len(v) >= 2}
    keys = sorted(merged, key=lambda k: -len(merged[k]))[:20]
    ask = "\n\n".join(f"묶음 {n + 1} (종류 {k[0]}, 대상 {k[1]})\n" + "\n".join(f"- {tags[i]['gist']}" for i in merged[k][:8])
                       for n, k in enumerate(keys))
    names = {}
    if keys:
        client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
        for ln in _ask(client, NAME, ask, 2000).splitlines():
            m = re.match(r"^\s*(?:묶음\s*)?(\d+)\s*\|\s*(.+)$", ln)
            if m:
                names[int(m.group(1))] = m.group(2).strip(" *")
    topics = []
    for n, (kind, target) in enumerate(keys):
        ids = merged[(kind, target)]
        sents = [tags[i]["sentiment"] for i in ids]
        sent = "부정" if sents.count("부정") > len(ids) / 2 else "긍정" if sents.count("긍정") > len(ids) / 2 else "혼재"
        dept = "매칭·랭크" if re.search(r"매칭|랭크", target) else KIND_DEPT[kind]
        title = KIND_TITLE[kind] if target == KIND_LABEL.get(kind) else names.get(n + 1) or f"{target} {kind}"
        topics.append({"target": target, "title": title, "kind": kind,
                       "dept": dept, "sentiment": sent, "ids": ids})
    return topics


def _burst(items, hours=2):
    """장애 글이 가장 몰린 시간대의 건수 (2시간 창)."""
    times = sorted(datetime.fromisoformat(p["created"]) for p in items if p.get("created") and OUTAGE.search(p["title"] + p["text"]))
    if not times and any(OUTAGE.search(p["title"] + p["text"]) for p in items):
        return sum(1 for p in items if OUTAGE.search(p["title"] + p["text"]))  # 시각이 없는 테스트 데이터
    best, j = 0, 0
    for i, t in enumerate(times):
        while t - times[j] > timedelta(hours=hours):
            j += 1
        best = max(best, i - j + 1)
    return best


def _has(word, txt):
    return bool(re.search(WORD[word], txt)) if word in WORD else word in txt


def score_topics(topics, posts):
    """AI 묶음 → 코드가 검증·집계·중요도 계산."""
    by_id = {p["id"]: p for p in posts}
    out, dropped, used = [], 0, set()  # 글 하나는 한 주제에만 센다 (중복 집계 방지)
    for t in topics:
        if not t.get("title") or not isinstance(t.get("ids"), list):
            continue  # 형식이 깨진 묶음은 버린다
        if t.get("dept") == "커뮤니티·기타" or t.get("kind") in ("질문", "잡담") or VAGUE.search(t["title"]):
            continue  # 질문·잡담·밈과 대상이 불분명한 주제는 리포트에서 뺀다
        t.setdefault("dept", "커뮤니티·기타")
        t.setdefault("sentiment", "혼재")
        t.setdefault("kind", "버그" if re.search(r"버그|오류", t["title"]) else "장애" if CRITICAL.search(t["title"]) else "밸런스")
        target = re.sub(r"^(?:실험체|아이템|시스템|이벤트)\s*", "", (t.get("target") or "").strip())
        ids = [i for i in dict.fromkeys(t["ids"]) if i in by_id]
        dropped += len(t["ids"]) - len(ids)
        ids = [i for i in ids if i not in used]
        # 주제 이름의 핵심 단어가 제목·본문에 실제로 있는 글만 센다 (AI가 관계없는 글을 섞는 경우를 걸러냄)
        keys = [w for w in re.split(r"[\s·,/()]+", t["title"]) if w and w not in STOP]
        must = [k for k in keys if k in ISSUE_WORDS]  # 이슈 단어(버그·핑·정지 등)가 주제 이름에 있으면 그중 하나는 꼭 있어야 한다
        if target and not GENERIC_TARGET.match(target):
            # 대상 이름(실험체·아이템·시스템)이 실제로 들어간 글만 센다. '버프'처럼 반응 단어만 겹치는 다른 실험체 글을 막는다
            ok = lambda txt: target.replace(" ", "") in txt.replace(" ", "") and (not must or any(_has(k, txt) for k in must))
        elif target:
            ok = lambda txt: True  # '여러 버그'처럼 모은 묶음은 글마다 AI가 이미 분류했고, 아래 검증에서 다시 판정한다
        else:
            others = [k for k in keys if k not in ISSUE_WORDS]
            ok = lambda txt: (not must or any(_has(k, txt) for k in must)) and (not others or any(k in txt for k in others))
        ids = [i for i in ids if ok(by_id[i]["title"] + by_id[i]["text"])]
        used.update(ids)
        if len(ids) < 2:
            continue
        items = [by_id[i] for i in ids]
        n = len(items)
        eng = sum(_engagement(p) for p in items)
        severe = sum(1 for p in items if SEVERE.search(p["title"] + p["text"]))
        critical = sum(1 for p in items if CRITICAL.search(p["title"] + p["text"]))
        steam = [p for p in items if p["vote"]]
        neg = sum(1 for p in steam if p["vote"] == "down")
        parts = {"글 수": n * 1.0, "반응": eng * 0.15, "심각 키워드": severe * 2.0,
                 "부정 감정": n * 1.5 if t["sentiment"] == "부정" else 0.0, "스팀 비추천": neg * 1.5}
        raw = sum(parts.values())
        top = sorted(items, key=_engagement, reverse=True)[:2]  # 위에서 핵심 단어로 거른 글 중 반응이 큰 글
        out.append({"title": t["title"], "kind": t["kind"], "dept": t["dept"], "sentiment": t["sentiment"], "count": n,
                    "ids": [p["id"] for p in sorted(items, key=_engagement, reverse=True)],
                    "severe": severe, "critical": critical, "outage": _burst(items), "views": sum(p["views"] for p in items), "comments": sum(p["comments"] for p in items),
                    "score": round(raw, 1), "parts": {k: round(v, 1) for k, v in parts.items() if v},
                    "recommends": sum(p["recommends"] for p in items), "steam_down": neg, "examples": [{"title": p["title"][:60], "url": p["url"]} for p in top]})
    assign_levels(out)
    out.sort(key=lambda x: (-x["level"], -x["score"]))
    return out, dropped


def assign_levels(out):
    """중요도는 같은 날 주제끼리의 순위가 아니라 고정된 기준으로 정한다 (조용한 날 1위가 자동으로 '높음'이 되지 않게).
    확인된 글 수(c)는 본문·제목 확인을 통과한 글 수, 확인을 안 했으면 키워드로 거른 글 수."""
    for t in out:
        c = t.get("confirmed", t["count"])
        crit, k = t["critical"], t["kind"]
        if t.get("outage", 0) >= 3:
            lv, why = 5, f"2시간 안에 서버·접속·결제 장애 글 {t['outage']}건 (여러 유저가 동시에 겪음)"
        elif k in ("장애", "버그", "네트워크") and c >= 3:
            lv, why = 4, f"오작동 제보 {c}건 확인 → QA 재현 확인 필요"
        elif crit or k in ("장애", "버그", "네트워크"):
            lv, why = 3, f"오작동 제보 {c}건 (3건 이상이면 높음)"
        elif k in ("운영", "BM") and c >= 5:
            lv, why = 3, f"운영·BM 조치 요구 {c}건"
        elif c >= 8 and t["sentiment"] == "부정":
            lv, why = 3, f"같은 불만 {c}건 (밸런스·의견은 보통까지)"
        elif c >= 3:
            lv, why = 2, f"관련 글 {c}건"
        else:
            lv, why = 1, f"관련 글 {c}건"
        if t["sentiment"] == "긍정" and lv > 2:
            lv, why = 2, why + " · 긍정 반응은 낮음까지"
        t["level"], t["reason"] = lv, why
    out.sort(key=lambda x: (-x["level"], -x["score"]))
    return out


SCORE_RULE = ("중요도는 고정 기준입니다. ●●●●● 서버 다운·접속 불가·로그인 불가·결제 오류·계정 손실 같은 장애 글이 2시간 안에 3건 이상(여러 유저가 동시에 겪는 장애). "
              "●●●●○ 버그·장애 제보가 3건 이상 확인됨. ●●●○○ 버그 제보 1~2건, 운영·BM 요구 5건 이상, 같은 불만 8건 이상. "
              "●●○○○ 관련 글 3건 이상. ●○○○○ 그 외. 밸런스·의견은 아무리 많아도 ●●●○○까지, 긍정 반응은 ●●○○○까지. "
              "질문·잡담·본인 정지 사유 문의는 이슈가 아니므로 뺍니다. 글은 대상 이름(실험체·아이템·시스템)이 실제로 들어간 것만 세고, "
              "AI가 글마다 주제에 정말 해당하는지 다시 판정해 해당 글이 2개 미만이면 주제를 뺍니다. "
              "같은 단계 안에서는 점수(글 수 + 반응×0.15 + 심각 키워드 글×2 + 부정이면 글 수×1.5 + 스팀 비추천×1.5) 순서입니다.")


def summary_stats(posts):
    steam = [p for p in posts if p["vote"]]
    return {"total": len(posts), "dc": sum(1 for p in posts if p["source"].startswith("디시")),
            "steam": len(steam), "steam_neg": sum(1 for p in steam if p["vote"] == "down")}




# ── 본문·댓글로 주제 해당 여부 검증 ──────────────────────────
VERIFY_SYSTEM = """당신은 커뮤니티 글이 주어진 주제에 정말 해당하는지 엄격하게 판정합니다. 제목만 보지 말고 본문과 댓글 분위기까지 보세요.
- 대상과 반응이 모두 맞아야 Y입니다. 주제가 '불만'이면 글이 실제로 불만·비판이어야 하고, 단순 질문·팁·칭찬·잡담이면 N입니다.
  주제가 '버그'면 실제 오작동 제보여야 하고, '너프/버프 요구'면 실제로 조정을 요구해야 합니다.
- '핑'은 네트워크 지연(핑 수치, 렉, 끊김)과 게임 안의 신호(핑 찍기)를 구분하세요. 주제와 다른 뜻이면 N입니다.
- 반어·농담·칭찬(예: 잘한 플레이를 보고 '핵 같다'), 다른 게임 이야기, 주제와 관계없는 잡담이면 N입니다.
- 본문이 거의 없고 사진·영상 위주인 글은 영상을 볼 수 없으므로, 제목과 댓글로 주제가 분명할 때만 Y입니다.
- 주제의 대상(실험체·아이템·시스템)과 다른 대상 이야기면 N입니다.
- 공략·빌드·사용법 질문("할만함?", "무기 뭐 씀?"), 본인이 정지당한 이유를 묻거나 하소연하는 글은 N입니다.
- 본문이 '(확인 못 함)'이면 제목만으로 판단하세요. 제목에 주제의 내용이 드러나면 Y, 애매하면 N입니다.
- 제목·본문·댓글로 주제에 해당한다고 볼 근거가 있으면 Y로 판정하세요.
각 글마다 한 줄로만 답하세요: 번호 | Y 또는 N | 이유(10자 이내)"""
VLINE = re.compile(r"^\s*(\d+)\s*\|\s*([YN])\s*\|?\s*(.*)$")


def verify(topics, posts, fetch, per_topic=8, top_n=12, delay=1.0, judge=None, fetch_n=4):
    """주제마다 반응 큰 글을 열어(본문·사진/영상 수·댓글) AI가 해당 여부를 판정한다.
    사이트가 막혀 본문을 못 가져오면 더 요청하지 않고 제목만으로 판정한다. 해당 글이 2개 미만이면 주제를 뺀다.
    fetch(post) → {body, media, comments} 또는 None. judge(lines) → {번호: Y/N} (테스트용)."""
    import time as _t
    by_id = {p["id"]: p for p in posts}
    order = {k: i for i, k in enumerate(KINDS)}
    topics = sorted(topics, key=lambda t: (order.get(t.get("kind"), 9), -t["score"]))[:top_n]  # 버그·장애를 먼저 확인
    checks, lines, n, media_only, failed, opened = [], [], 0, set(), 0, set()
    for ti, t in enumerate(topics):
        for j, pid in enumerate(t["ids"][:per_topic]):
            p = by_id[pid]
            d = None
            if p["source"].startswith("디시") and failed < 3 and j < fetch_n:  # 사이트 부담을 줄이려 주제마다 앞의 몇 개만 연다
                try:
                    d = fetch(p)
                except Exception:
                    d = None
                if d is None:
                    failed += 1  # 3번 막히면 접근 제한으로 보고 더 요청하지 않는다
                _t.sleep(delay)
            elif not p["source"].startswith("디시"):
                d = {"body": p["text"], "media": 0, "comments": []}
            if d is not None:
                opened.add(ti)
            d = d or {"body": "", "media": 0, "comments": [], "unknown": True}
            n += 1
            checks.append((n, ti, pid))
            if len(d["body"]) < 10 and d["media"]:
                media_only.add(pid)
            body = "(확인 못 함)" if d.get("unknown") else (d["body"][:200] or "(없음)")
            lines.append(f"{n} | 주제: {t['title']} | 제목: {p['title'][:60]} | 본문: {body}"
                         f"{' | 사진·영상 ' + str(d['media']) + '개' if d['media'] else ''} | 댓글: {' / '.join(d['comments'][:8])[:300] or '(없음)'}")
        t["verify"] = "full" if ti in opened and failed < 3 else "title"
    if not lines:
        return topics
    verdict = (judge or _judge)(lines)
    kept = []
    for ti, t in enumerate(topics):
        mine = [(k, pid) for k, i, pid in checks if i == ti]
        yes = [pid for k, pid in mine if verdict.get(k)]
        t["checked"], t["confirmed"] = len(mine), len(yes)
        if len(yes) < 2:
            continue
        t["score"] = round(t["score"] * len(yes) / len(mine), 1)
        t["critical"] = sum(1 for i in yes if CRITICAL.search(by_id[i]["title"] + by_id[i]["text"]))
        t["outage"] = _burst([by_id[i] for i in yes])
        # 근거 링크는 글로 내용을 확인할 수 있는 것 우선 (영상·사진만 있는 글은 뒤로)
        ordered = sorted(yes, key=lambda i: (bool(CRUDE.search(by_id[i]["title"])), i in media_only))
        t["examples"] = [{"title": by_id[i]["title"][:60], "url": by_id[i]["url"]} for i in ordered[:3]]
        kept.append(t)
    return kept


def _judge(lines):
    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
    verdict = {}
    for k in range(0, len(lines), 60):
        res = client.converse(modelId=MODEL_ID, system=[{"text": VERIFY_SYSTEM}],
                              messages=[{"role": "user", "content": [{"text": "\n".join(lines[k:k + 60])}]}],
                              inferenceConfig={"maxTokens": 3000, "temperature": 0})
        for ln in "".join(c.get("text", "") for c in res["output"]["message"]["content"]).splitlines():
            m = VLINE.match(ln)
            if m:
                verdict[int(m.group(1))] = m.group(2) == "Y"
    return verdict
