"""Slack Block Kit 메시지 만들기와 Incoming Webhook 발송."""
import json
import urllib.request

WEEK = "월화수목금토일"


def dots(level):
    return "●" * level + "○" * (5 - level)


def _d(dt):
    return f"{dt.month}/{dt.day}({WEEK[dt.weekday()]}) {dt:%H:%M}"


def build(start, end, stats, topics, page_url="", reviewed=False):
    neg = f" · 스팀 리뷰 부정 {round(100 * stats['steam_neg'] / stats['steam'])}%" if stats["steam"] else ""
    head = "주말 동향" if (end - start).days >= 2 else "금일 반응"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📋 이터널 리턴 유저 동향 · {head}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{_d(start)} ~ {_d(end)}* · 수집 {stats['total']}건 (디시 {stats['dc']} · 스팀 {stats['steam']}){neg}"}]},
        {"type": "divider"},
    ]
    topics = sorted([t for t in topics if not t.get("removed")], key=lambda t: (-t["level"], -t["score"]))  # 관리자가 뺀 주제 제외
    for t in topics[:10]:
        ex = " · ".join(f"<{e['url']}|{e['title'].replace('<', '').replace('>', '').replace('|', '/')}>" for e in t["examples"])
        how = "본문·댓글" if t.get("verify") == "full" else "제목"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
            f"`{dots(t['level'])}` *{t['title']}*  _{t.get('kind', '')} · {t['dept']} · {t['sentiment']}_\n"
            f"왜 이 단계: {t.get('reason', '')}\n"
            + (f"PM 조정: `{dots(t['pm']['from'])}` → `{dots(t['level'])}` · {t['pm']['reason']}\n" if t.get("pm") else "")
            + (f"📝 {t['note']}\n" if t.get("note") else "")
            + f"근거: 글 {t['count']}개" + (f" 중 {how} 확인 {t['confirmed']}/{t['checked']}" if t.get('checked') else "")
            + f" · 조회 {t['views']:,} · 댓글 {t['comments']} · 추천 {t['recommends']}"
            + (f" · 장애성 글 {t['critical']}개" if t.get('critical') else "")
            + (f" · 스팀 비추천 {t['steam_down']}" if t['steam_down'] else "")
            + (f"\n> {ex}" if ex else "")}})
    if topics and all(t.get("verify") == "title" for t in topics):
        blocks.insert(2, {"type": "context", "elements": [{"type": "mrkdwn", "text":
            "⚠ 이번 리포트는 커뮤니티 본문을 열지 못해 제목으로만 해당 여부를 확인했습니다. 원문 링크를 함께 확인해 주세요."}]})
    if not topics:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
            "조치가 필요한 주요 이슈가 없습니다. (같은 주제로 확인된 글이 2개 이상인 이슈 없음)"}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
        "✅ PM 검토 후 발송" if reviewed else "🤖 검토 전 자동 발송 (중요도는 코드 기준 그대로)"}]})
    blocks.append({"type": "divider"})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
        "●●●●● 긴급: 서버·접속·결제 장애 글 2시간 안 3건 이상 · ●●●●○ 높음: 버그 제보 3건 이상 확인 · ●●●○○ 보통 · ●●○○○ 낮음 · ●○○○○ 참고  |  밸런스·의견은 보통까지, 질문·잡담은 제외. 단계는 코드가 고정 기준으로 정합니다."
        + (f"  |  <{page_url}|설정·미리보기>" if page_url else "")}]})
    return {"text": f"이터널 리턴 유저 동향 {_d(start)} ~ {_d(end)}", "blocks": blocks}


def send(webhook, payload):
    req = urllib.request.Request(webhook, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read().decode()
