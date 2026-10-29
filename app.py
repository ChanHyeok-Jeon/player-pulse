"""유저 동향 아침 브리핑. 매시간 수집, 정해진 요일·시각에 Slack 발송, 웹에서 기간을 정해 리포트 받기."""
import json
import os
import secrets
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pulse import analyze, slack, store
from pulse.collect import KST, collect_dc, collect_steam, fetch_dc_detail

BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(BASE, "data", "settings.json")
PAGE_URL = os.environ.get("PULSE_PAGE_URL", "https://chanhyeok-pm.cloud/pulse/")
KEEP_DAYS = 7  # 최근 일주일만 다룬다
app = FastAPI(title="유저 동향 아침 브리핑")
_lock = threading.Lock()


# ── 설정 (Webhook은 서버 파일에만, 화면에는 가려서) ──────────────
def load_settings():
    s = {"time": "09:00", "weekdays": [0, 1, 2, 3, 4], "webhook": "", "enabled": True}
    if os.path.exists(SETTINGS):
        s.update(json.load(open(SETTINGS, encoding="utf-8")))
    return s


def save_settings(s):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    json.dump(s, open(SETTINGS, "w", encoding="utf-8"), ensure_ascii=False)
    os.chmod(SETTINGS, 0o600)


def admin_ok(key):
    real = os.environ.get("PULSE_ADMIN_KEY", "")
    return bool(real) and secrets.compare_digest(key or "", real)


# ── 리포트 기록 ──────────────────────────────────────────
def _db():
    c = sqlite3.connect(store.DB)
    c.execute("CREATE TABLE IF NOT EXISTS reports (slot TEXT PRIMARY KEY, start TEXT, end TEXT, created TEXT, sent TEXT, payload TEXT, topics TEXT, stats TEXT, seconds REAL)")
    return c


def make_report(start, end, slot=None):
    """기간 → 주제 묶기·점수 → Slack 메시지. 걸린 시간도 기록한다."""
    t0 = time.time()
    posts = store.between(start, end)
    if not posts:
        raise ValueError("이 기간에 수집된 글이 없습니다. 최근 일주일 안에서 골라 주세요.")
    topics, _ = analyze.score_topics(analyze.cluster(analyze.pick(posts)), posts)
    # 제목만으로는 반어·농담·다른 게임 이야기를 구분할 수 없어, 본문·댓글을 열어 해당 여부를 다시 판정한다
    topics = analyze.assign_levels(analyze.verify(topics, posts, lambda p: fetch_dc_detail(p["id"])))
    stats = analyze.summary_stats(posts)
    payload = slack.build(start, end, stats, topics, PAGE_URL)
    secs = round(time.time() - t0, 1)
    rep = {"slot": slot or f"custom-{start:%m%d%H%M}-{end:%m%d%H%M}", "start": start.isoformat(), "end": end.isoformat(),
           "created": datetime.now(KST).isoformat(timespec="seconds"), "sent": "", "payload": payload,
           "topics": topics, "stats": stats, "seconds": secs}
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?,?,?,?)",
                  (rep["slot"], rep["start"], rep["end"], rep["created"], "", json.dumps(payload, ensure_ascii=False),
                   json.dumps(topics, ensure_ascii=False), json.dumps(stats), secs))
    return rep


def mark_sent(slot):
    with _db() as c:
        c.execute("UPDATE reports SET sent=? WHERE slot=?", (datetime.now(KST).isoformat(timespec="seconds"), slot))


def send_report(rep):
    hook = load_settings().get("webhook")
    if not hook:
        raise ValueError("Slack Webhook이 설정되지 않았습니다.")
    slack.send(hook, rep["payload"])
    mark_sent(rep["slot"])


# ── 백그라운드: 매시간 수집, 예약 시각 발송 ───────────────────────
def collect_once(hours_back=3):
    since = datetime.now(KST) - timedelta(hours=hours_back)
    n = store.save(collect_dc(since, max_pages=40)) + store.save(collect_steam(since, max_pages=3))
    store.prune(datetime.now(KST) - timedelta(days=KEEP_DAYS + 1))
    return n


def scheduler():
    last_collect = 0
    while True:
        try:
            now = datetime.now(KST)
            if time.time() - last_collect > 3600:
                with _lock:
                    collect_once()
                last_collect = time.time()
            s = load_settings()
            h, m = map(int, s["time"].split(":"))
            slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            slot = f"auto-{slot_time:%Y%m%d%H%M}"
            if s["enabled"] and s["webhook"] and now.weekday() in s["weekdays"] and slot_time <= now < slot_time + timedelta(minutes=30):
                with _db() as c:
                    done = c.execute("SELECT sent FROM reports WHERE slot=?", (slot,)).fetchone()
                if not (done and done[0]):
                    with _lock:
                        collect_once(hours_back=2)  # 발송 직전 최신 글 반영
                        start, end = analyze.window(now, s["time"], s["weekdays"])
                        send_report(make_report(start, end, slot))
        except Exception:
            traceback.print_exc()
        time.sleep(30)


@app.on_event("startup")
def _start():
    if os.environ.get("PULSE_SCHEDULER", "1") == "1":
        threading.Thread(target=scheduler, daemon=True).start()


# ── API ──────────────────────────────────────────────────
class RangeIn(BaseModel):
    start: str
    end: str
    send: bool = False
    key: str = ""


class SettingsIn(BaseModel):
    key: str
    time: str
    weekdays: list[int]
    webhook: str = ""
    enabled: bool = True


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "web", "index.html"))


@app.get("/api/state")
def state():
    s = load_settings()
    with _db() as c:
        rows = c.execute("SELECT slot,start,end,created,sent,topics,stats,seconds FROM reports ORDER BY end DESC LIMIT 12").fetchall()
    first = store.oldest()
    return {"settings": {"time": s["time"], "weekdays": s["weekdays"], "enabled": s["enabled"],
                         "webhook_set": bool(s["webhook"])},
            "score_rule": analyze.SCORE_RULE, "data_from": first,
            "reports": [{"slot": r[0], "start": r[1], "end": r[2], "created": r[3], "sent": r[4],
                         "topics": json.loads(r[5]), "stats": json.loads(r[6]), "seconds": r[7]} for r in rows]}


@app.post("/api/report")
def custom_report(body: RangeIn):
    """기간을 정해 동향 받기. Slack 발송은 관리자 키가 있어야 한다."""
    try:
        start = datetime.fromisoformat(body.start).replace(tzinfo=KST)
        end = datetime.fromisoformat(body.end).replace(tzinfo=KST)
    except ValueError:
        raise HTTPException(400, "날짜 형식이 올바르지 않습니다.")
    if end <= start or end - start > timedelta(days=KEEP_DAYS):
        raise HTTPException(400, "기간은 7일 이내로 골라 주세요.")
    if body.send and not admin_ok(body.key):
        raise HTTPException(403, "Slack 발송은 관리자만 할 수 있습니다.")
    try:
        with _lock:
            rep = make_report(start, end)
            if body.send:
                send_report(rep)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {k: rep[k] for k in ("slot", "start", "end", "topics", "stats", "seconds")} | {"sent": body.send}


@app.post("/api/settings")
def update_settings(body: SettingsIn):
    if not admin_ok(body.key):
        raise HTTPException(403, "관리자 키가 필요합니다.")
    s = load_settings()
    s.update(time=body.time, weekdays=sorted(set(body.weekdays)), enabled=body.enabled)
    if body.webhook:
        if not body.webhook.startswith("https://hooks.slack.com/"):
            raise HTTPException(400, "Slack Incoming Webhook 주소가 아닙니다.")
        s["webhook"] = body.webhook
    save_settings(s)
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}
