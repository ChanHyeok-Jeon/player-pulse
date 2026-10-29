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
SEVERE = re.compile(r"튕|크래시|강종|접속\s*불가|서버|렉|핑|프레임|버그|오류|핵|치트|결제|환불|먹튀|매칭\s*안|로딩|롤백|계정|보안|정지")
VAGUE = re.compile(r"특정|일부 |게임 내 |기타|잡담|밈")
LEVEL = {5: "긴급", 4: "높음", 3: "보통", 2: "낮음", 1: "참고"}

TOOL = {"toolSpec": {
    "name": "report_topics", "description": "게시글을 주제별로 묶어 보고한다",
    "inputSchema": {"json": {"type": "object", "properties": {"topics": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string", "description": "주제를 한 줄로 (예: '아비게일 너프 이후 픽률 급감 불만')"},
        "dept": {"type": "string", "enum": DEPTS},
        "sentiment": {"type": "string", "enum": ["부정", "긍정", "혼재"]},
        "ids": {"type": "array", "items": {"type": "string"}, "description": "이 주제에 속하는 글 번호들"},
    }, "required": ["title", "dept", "sentiment", "ids"]}}}, "required": ["topics"]}}}}

SYSTEM = """당신은 게임 개발팀 PM을 돕는 보조입니다. 이터널 리턴 커뮤니티 글 목록을 받아, 개발·운영팀이 알아야 하거나 조치할 수 있는 주제로 묶으세요.
- 다룰 주제: 실험체·아이템 밸런스, 버그·서버·클라이언트 문제, 매칭·랭크, 정지·제재·핵 신고 같은 운영 이슈, 이벤트·콘텐츠, 상점·결제·BM, 신규 유저·UI 불편.
- 제외할 글: 특정 개인(유저·스트리머·직원 등)에 대한 이야기, 친목·잡담·밈·대회 응원, 의미를 알 수 없는 글. 개인 이름은 주제 이름에 절대 넣지 마세요.
- 한 주제는 하나의 구체적인 대상(특정 실험체·아이템·시스템·버그·이벤트)에 대한 반응이어야 합니다. "UI 불편 전반"처럼 넓게 묶지 마세요.
- 주제는 8~14개로 만들고, 글이 1개뿐인 주제는 만들지 마세요.
- 주제 이름에는 대상의 실제 이름(실험체·아이템·시스템·이벤트 이름)을 꼭 넣으세요. "특정", "일부", "게임 내" 같은 막연한 말은 쓰지 말고, 대상을 알 수 없으면 그 주제를 만들지 마세요.
- 주제 이름은 "무엇에 대해 어떤 반응인지" 구체적으로 쓰세요. 예: "코렐 스킬셋이 과하다는 불만", "랭크 매칭 대기 시간 증가 불만". "~관련 논의"처럼 막연하게 쓰지 마세요.
- ids에는 목록에 있는 글 번호만 쓰세요. 지어내지 마세요.
- 욕설이나 비하 표현은 옮기지 말고 중립적으로 바꾸세요."""


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


def pick(posts, limit=320):
    """AI에 보낼 글 고르기: 반응이 큰 글과 심각 키워드 글 우선."""
    scored = sorted(posts, key=lambda p: (bool(SEVERE.search(p["title"] + p["text"])), _engagement(p)), reverse=True)
    return scored[:limit]


LINE = re.compile(r"^\s*(?:[-*\d.)]+\s*)?(.+?)\s*\|\s*(.+?)\s*\|\s*(부정|긍정|혼재)\s*\|\s*([\d,\s]+)\s*$")


def cluster(posts):
    """글 번호를 짧은 순번으로 보내고, 한 줄 형식(주제|부서|감정|번호들)으로 받아 코드가 읽는다.

    도구 호출(JSON) 형식은 글이 많을 때 모델이 형식을 자주 깨뜨려, 읽기 쉬운 줄 형식으로 바꿨다.
    """
    idx = {str(i + 1): p["id"] for i, p in enumerate(posts)}
    lines = "\n".join(f"{i + 1} | {p['source'][:2]} | {('👍' if p['vote'] == 'up' else '👎') if p['vote'] else ''} {p['title'][:60]}"
                      for i, p in enumerate(posts))
    ask = ("아래 글 목록을 주제로 묶어, 주제마다 한 줄씩 다음 형식으로만 답하세요. 다른 말은 쓰지 마세요.\n"
           "주제 이름 | 부서 | 감정 | 글 번호들(쉼표로)\n"
           f"부서는 다음 중 하나: {', '.join(DEPTS)}\n감정은 부정·긍정·혼재 중 하나\n\n글 목록 (번호 | 출처 | 제목)\n" + lines)
    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
    for _ in range(3):
        res = client.converse(modelId=MODEL_ID, system=[{"text": SYSTEM}],
                              messages=[{"role": "user", "content": [{"text": ask}]}],
                              inferenceConfig={"maxTokens": 4000, "temperature": 0})
        text = "".join(c.get("text", "") for c in res["output"]["message"]["content"])
        topics = []
        for ln in text.splitlines():
            m = LINE.match(ln)
            if not m:
                continue
            dept = m.group(2) if m.group(2) in DEPTS else "커뮤니티·기타"
            ids = [idx.get(x.strip(), f"unknown-{x.strip()}") for x in m.group(4).split(",") if x.strip()]
            topics.append({"title": m.group(1).strip(" *"), "dept": dept, "sentiment": m.group(3), "ids": ids})
        if topics:
            return topics
    raise RuntimeError("AI가 주제를 묶지 못했습니다.")


def score_topics(topics, posts):
    """AI 묶음 → 코드가 검증·집계·중요도 계산."""
    by_id = {p["id"]: p for p in posts}
    out, dropped, used = [], 0, set()  # 글 하나는 한 주제에만 센다 (중복 집계 방지)
    for t in topics:
        if not t.get("title") or not isinstance(t.get("ids"), list):
            continue  # 형식이 깨진 묶음은 버린다
        if t.get("dept") == "커뮤니티·기타" or VAGUE.search(t["title"]):
            continue  # 잡담·밈과 대상이 불분명한 주제는 리포트에서 뺀다
        t.setdefault("dept", "커뮤니티·기타")
        t.setdefault("sentiment", "혼재")
        ids = [i for i in dict.fromkeys(t["ids"]) if i in by_id]
        dropped += len(t["ids"]) - len(ids)
        ids = [i for i in ids if i not in used]
        used.update(ids)
        if len(ids) < 2:
            continue
        items = [by_id[i] for i in ids]
        n = len(items)
        eng = sum(_engagement(p) for p in items)
        severe = sum(1 for p in items if SEVERE.search(p["title"] + p["text"]))
        steam = [p for p in items if p["vote"]]
        neg = sum(1 for p in steam if p["vote"] == "down")
        parts = {"글 수": n * 1.0, "반응": eng * 0.15, "심각 키워드": severe * 2.0,
                 "부정 감정": n * 1.5 if t["sentiment"] == "부정" else 0.0, "스팀 비추천": neg * 1.5}
        raw = sum(parts.values())
        top = sorted(items, key=_engagement, reverse=True)[:2]
        out.append({"title": t["title"], "dept": t["dept"], "sentiment": t["sentiment"], "count": n,
                    "severe": severe, "views": sum(p["views"] for p in items), "comments": sum(p["comments"] for p in items),
                    "score": round(raw, 1), "parts": {k: round(v, 1) for k, v in parts.items() if v},
                    "recommends": sum(p["recommends"] for p in items), "steam_down": neg, "examples": [{"title": p["title"][:60], "url": p["url"]} for p in top]})
    out.sort(key=lambda x: -x["score"])
    # 같은 기간 주제끼리의 상대 순위로 5단계: 상위 10% 긴급, 30% 높음, 60% 보통, 85% 낮음, 나머지 참고
    for i, t in enumerate(out):
        q = i / max(len(out), 1)
        lv = 5 if q < .1 else 4 if q < .3 else 3 if q < .6 else 2 if q < .85 else 1
        if t["sentiment"] == "긍정":
            lv = min(lv, 2)  # 긍정 동향은 '낮음'까지만
        t["level"] = lv
    out.sort(key=lambda x: (-x["level"], -x["score"]))
    return out, dropped


def summary_stats(posts):
    steam = [p for p in posts if p["vote"]]
    return {"total": len(posts), "dc": sum(1 for p in posts if p["source"].startswith("디시")),
            "steam": len(steam), "steam_neg": sum(1 for p in steam if p["vote"] == "down")}


SCORE_RULE = ("점수 = 글 수×1 + 반응(조회/50 + 댓글×2 + 추천×3)×0.15 + 심각 키워드 글×2 + 부정 주제면 글 수×1.5 + 스팀 비추천×1.5. "
              "같은 기간 주제끼리 점수 순위로 상위 10% ●●●●●, 30% ●●●●○, 60% ●●●○○, 85% ●●○○○, 나머지 ●○○○○. "
              "긍정 동향은 ●●○○○까지만, 잡담·밈·대상이 불분명한 주제는 제외.")
