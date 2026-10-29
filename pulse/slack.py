"""Slack Block Kit 메시지 만들기와 Incoming Webhook 발송."""
import json
import urllib.request

WEEK = "월화수목금토일"


def dots(level):
    return "●" * level + "○" * (5 - level)


def _d(dt):
    return f"{dt.month}/{dt.day}({WEEK[dt.weekday()]}) {dt:%H:%M}"


def build(start, end, stats, topics, page_url=""):
    neg = f" · 스팀 리뷰 부정 {round(100 * stats['steam_neg'] / stats['steam'])}%" if stats["steam"] else ""
    head = "주말 동향" if (end - start).days >= 2 else "금일 반응"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📋 이터널 리턴 유저 동향 · {head}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{_d(start)} ~ {_d(end)}* · 수집 {stats['total']}건 (디시 {stats['dc']} · 스팀 {stats['steam']}){neg}"}]},
        {"type": "divider"},
    ]
    for t in topics[:10]:
        ex = " · ".join(f"<{e['url']}|{e['title'].replace('<', '').replace('>', '').replace('|', '/')}>" for e in t["examples"])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
            f"`{dots(t['level'])}` *{t['title']}*  _{t['dept']} · {t['sentiment']}_\n"
            f"근거: 글 {t['count']}개 · 조회 {t['views']:,} · 댓글 {t['comments']} · 추천 {t['recommends']}"
            + (f" · 심각 키워드 글 {t['severe']}개" if t['severe'] else "") + (f" · 스팀 비추천 {t['steam_down']}" if t['steam_down'] else "")
            + f" → 점수 {t['score']}\n> {ex}"}})
    blocks.append({"type": "divider"})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
        "●●●●● 긴급 · ●●●●○ 높음 · ●●●○○ 보통 · ●●○○○ 낮음 · ●○○○○ 참고  |  점수는 글 수·반응·심각 키워드·부정 반응으로 코드가 계산하고, AI는 비슷한 글을 묶기만 합니다."
        + (f"  |  <{page_url}|설정·미리보기>" if page_url else "")}]})
    return {"text": f"이터널 리턴 유저 동향 {_d(start)} ~ {_d(end)}", "blocks": blocks}


def send(webhook, payload):
    req = urllib.request.Request(webhook, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read().decode()
