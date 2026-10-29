"""유저 동향 아침 브리핑. 매시간 수집 → 발송 30분 전 초안 → 관리자 검토·수정 → 정해진 시각에 Slack 발송.
자동 전송을 켜 두면 검토하지 않은 초안도 예약 시각에 그대로 보낸다."""
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
from fastapi.staticfiles import StaticFiles
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
    s = {"time": "09:00", "weekdays": [0, 1, 2, 3, 4], "webhook": "", "enabled": True, "precollect_minutes": 60,
         "draft_minutes": 30, "auto_send": False}
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
    cols = {r[1] for r in c.execute("PRAGMA table_info(reports)")}
    if "status" not in cols:  # draft(검토 대기) → approved(검토 완료) → sent
        c.execute("ALTER TABLE reports ADD COLUMN status TEXT DEFAULT 'draft'")
    if "log" not in cols:  # 관리자 수정 기록
        c.execute("ALTER TABLE reports ADD COLUMN log TEXT DEFAULT '[]'")
    return c


def get_report(slot):
    with _db() as c:
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM reports WHERE slot=?", (slot,)).fetchone()
    if not r:
        return None
    r = dict(r)
    for k in ("payload", "topics", "stats", "log"):
        r[k] = json.loads(r[k] or ("[]" if k == "log" else "{}"))
    return r


def rebuild(rep):
    """수정·검토 결과로 Slack 메시지를 다시 만들어 저장한다."""
    start, end = datetime.fromisoformat(rep["start"]), datetime.fromisoformat(rep["end"])
    rep["payload"] = slack.build(start, end, rep["stats"], rep["topics"], PAGE_URL, reviewed=rep["status"] == "approved")
    with _db() as c:
        c.execute("UPDATE reports SET payload=?, topics=?, status=?, log=? WHERE slot=?",
                  (json.dumps(rep["payload"], ensure_ascii=False), json.dumps(rep["topics"], ensure_ascii=False),
                   rep["status"], json.dumps(rep["log"], ensure_ascii=False), rep["slot"]))
    return rep


def make_report(start, end, slot=None, check=True):
    """기간 → 주제 묶기·점수 → Slack 메시지. 걸린 시간도 기록한다."""
    t0 = time.time()
    posts = store.between(start, end)
    if not posts:
        raise ValueError("이 기간에 수집된 글이 없습니다. 최근 일주일 안에서 골라 주세요.")
    topics, _ = analyze.score_topics(analyze.cluster(analyze.pick(posts)), posts)
    # 글마다 주제에 정말 해당하는지 AI가 다시 판정한다. check=False거나 사이트가 막히면 새 요청 없이 제목만으로 판정
    fetch = (lambda p: fetch_dc_detail(p["id"])) if check else (lambda p: None)
    topics = analyze.assign_levels(analyze.verify(topics, posts, fetch, delay=1.0 if check else 0))
    for t in topics:
        t["auto_level"] = t["level"]  # 코드가 정한 단계 (관리자가 바꾸면 Slack에 함께 표시)
    stats = analyze.summary_stats(posts)
    payload = slack.build(start, end, stats, topics, PAGE_URL)
    secs = round(time.time() - t0, 1)
    rep = {"slot": slot or f"custom-{start:%m%d%H%M}-{end:%m%d%H%M}", "start": start.isoformat(), "end": end.isoformat(),
           "created": datetime.now(KST).isoformat(timespec="seconds"), "sent": "", "payload": payload,
           "topics": topics, "stats": stats, "seconds": secs, "status": "draft", "log": []}
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO reports (slot,start,end,created,sent,payload,topics,stats,seconds,status,log) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (rep["slot"], rep["start"], rep["end"], rep["created"], "", json.dumps(payload, ensure_ascii=False),
                   json.dumps(topics, ensure_ascii=False), json.dumps(stats), secs, "draft", "[]"))
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


def send_once(slot):
    """같은 리포트가 두 번 나가지 않게, 보내기 직전에 다시 확인한다. (_lock 안에서 부른다)"""
    rep = get_report(slot)
    if rep and not rep["sent"]:
        send_report(rep)
        return True
    return False


def slot_time_of(slot):
    """auto-YYYYMMDDHHMM → 예약 시각. 예약 리포트가 아니면 None."""
    if not slot.startswith("auto-"):
        return None
    return datetime.strptime(slot[5:], "%Y%m%d%H%M").replace(tzinfo=KST)


# ── 백그라운드: 매시간 수집, 예약 시각 발송 ───────────────────────
def collect_once(hours_back=3):
    since = datetime.now(KST) - timedelta(hours=hours_back)
    n = 0
    for name, job in (("디시", lambda: collect_dc(since, max_pages=40)), ("스팀", lambda: collect_steam(since, max_pages=3))):
        try:
            n += store.save(job())
        except Exception as e:  # 한 곳이 막혀도 나머지 수집과 발송은 계속한다
            print(f"[collect] {name} 실패: {e}", flush=True)
    store.prune(datetime.now(KST) - timedelta(days=KEEP_DAYS + 1))
    return n


def catch_up(max_pages=150):
    """마지막으로 모은 글 이후부터 따라잡는다 (발송 1시간 전 실행)."""
    last = store.newest()
    since = datetime.fromisoformat(last) - timedelta(minutes=10) if last else datetime.now(KST) - timedelta(hours=24)
    n = 0
    try:
        n += store.save(collect_dc(since, max_pages=max_pages, delay=1.5))
    except Exception as e:
        print(f"[catch-up] 디시 실패: {e}", flush=True)
    try:
        n += store.save(collect_steam(since, max_pages=5))
    except Exception as e:
        print(f"[catch-up] 스팀 실패: {e}", flush=True)
    print(f"[catch-up] {since:%m-%d %H:%M} 이후 {n}건", flush=True)
    return n


def scheduler():
    last_collect = 0
    while True:
        try:
            now = datetime.now(KST)
            s = load_settings()
            paused = s.get("collect_paused_until") and now < datetime.fromisoformat(s["collect_paused_until"])
            if not paused and time.time() - last_collect > 3600:
                with _lock:
                    collect_once()
                last_collect = time.time()
            h, m = map(int, s["time"].split(":"))
            slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            slot = f"auto-{slot_time:%Y%m%d%H%M}"
            pre = slot_time - timedelta(minutes=s.get("precollect_minutes", 60))
            # 발송 1시간 전: 밀린 글 따라잡기
            if s["enabled"] and now.weekday() in s["weekdays"] and pre <= now < pre + timedelta(minutes=20) and s.get("last_catch_up") != slot:
                with _lock:
                    catch_up()
                s = load_settings(); s["last_catch_up"] = slot; save_settings(s)
                last_collect = time.time()
            today = s["enabled"] and now.weekday() in s["weekdays"]
            # 발송 30분 전: 초안을 만들어 관리자 검토를 기다린다
            draft_at = slot_time - timedelta(minutes=s.get("draft_minutes", 30))
            if today and draft_at <= now < slot_time + timedelta(minutes=30) and not get_report(slot):
                with _lock:
                    start, end = analyze.window(slot_time, s["time"], s["weekdays"])
                    make_report(start, end, slot)
            # 예약 시각: 검토 완료된 초안을 보낸다. 자동 전송이 켜져 있으면 검토 전 초안도 그대로 보낸다
            if today and s["webhook"] and slot_time <= now < slot_time + timedelta(minutes=30):
                rep = get_report(slot)
                if rep and not rep["sent"] and (rep["status"] == "approved" or s.get("auto_send")):
                    with _lock:
                        send_once(slot)
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
    auto_send: bool = False


class TopicEdit(BaseModel):
    idx: int
    level: int
    title: str
    removed: bool = False
    examples: list[str] = []  # 남길 근거 글 주소 (그 주제에 묶인 글 중에서)
    note: str = ""
    reason: str = ""


class EditIn(BaseModel):
    key: str = ""
    topics: list[TopicEdit]


class ApproveIn(BaseModel):
    key: str
    send_now: bool = False


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "web", "index.html"))


SHOTS = os.path.join(BASE, "web", "shots")
os.makedirs(SHOTS, exist_ok=True)
app.mount("/shots", StaticFiles(directory=SHOTS), name="shots")


@app.get("/api/shots")
def shots():
    """공유 결과 이미지 (실제 Slack 수신 화면). 설명은 shots.json에 {파일명: 설명}으로."""
    meta = os.path.join(SHOTS, "shots.json")
    cap = json.load(open(meta, encoding="utf-8")) if os.path.exists(meta) else {}
    files = sorted(f for f in os.listdir(SHOTS) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
    return [{"src": f"shots/{f}", "caption": cap.get(f, "")} for f in files]


@app.get("/api/state")
def state():
    s = load_settings()
    with _db() as c:
        rows = c.execute("SELECT slot,start,end,created,sent,topics,stats,seconds,status,log FROM reports ORDER BY end DESC, created DESC LIMIT 60").fetchall()
    first = store.oldest()
    reports = []
    for r in rows:
        topics = json.loads(r[5])
        if not r[4]:  # 아직 안 보낸 리포트는 근거 글 후보를 함께 내려 준다 (관리자가 링크를 바꿀 수 있게)
            for t in topics:
                t["cands"] = [{"title": p["title"][:60], "url": p["url"]} for p in store.get_many(t.get("ids", [])[:12])]
        reports.append({"slot": r[0], "start": r[1], "end": r[2], "created": r[3], "sent": r[4], "topics": topics,
                        "stats": json.loads(r[6]), "seconds": r[7], "status": r[8] or "draft", "log": json.loads(r[9] or "[]")})
    return {"settings": {"time": s["time"], "weekdays": s["weekdays"], "enabled": s["enabled"],
                         "webhook_set": bool(s["webhook"]), "auto_send": s.get("auto_send", False),
                         "draft_minutes": s.get("draft_minutes", 30)},
            "score_rule": analyze.SCORE_RULE, "data_from": first, "reports": reports}


@app.post("/api/reports/{slot}/edit")
def edit_report(slot: str, body: EditIn):
    """보내기 전에 중요도·주제 이름·근거 링크·메모를 고치거나 주제를 뺀다. 검토는 키 없이도 할 수 있고,
    Slack 발송(검토 완료)만 관리자 키가 필요하다. 누가 고쳤는지(관리자/키 없음) 기록으로 남긴다."""
    by = "관리자" if admin_ok(body.key) else "키 없음"
    with _lock:
        rep = get_report(slot)
        if not rep:
            raise HTTPException(404, "리포트가 없습니다.")
        if rep["sent"]:
            raise HTTPException(409, "이미 보낸 리포트는 고칠 수 없습니다.")
        now = datetime.now(KST).isoformat(timespec="seconds")
        for e in body.topics:
            if not 0 <= e.idx < len(rep["topics"]) or not 1 <= e.level <= 5:
                raise HTTPException(400, "잘못된 수정입니다.")
            t = rep["topics"][e.idx]
            auto = t.get("auto_level", t["level"])
            if e.level != auto and not e.reason.strip() and not (t.get("pm") and t["pm"]["level"] == e.level):
                raise HTTPException(400, f"'{t['title']}'의 중요도를 바꾼 이유를 적어 주세요.")
            changes = []
            if e.level != t["level"]:
                changes.append(("중요도", t["level"], e.level))
            if e.title.strip() and e.title.strip() != t["title"]:
                changes.append(("주제 이름", t["title"], e.title.strip()))
            if e.removed != bool(t.get("removed")):
                changes.append(("주제", "포함" if not e.removed else "제외", "제외" if e.removed else "포함"))
            cands = {p["url"]: p["title"][:60] for p in store.get_many(t.get("ids", []))} | {x["url"]: x["title"] for x in t["examples"]}
            ex = [{"title": cands[u], "url": u} for u in dict.fromkeys(e.examples) if u in cands][:3]
            if [x["url"] for x in ex] != [x["url"] for x in t["examples"]]:
                changes.append(("근거 링크", len(t["examples"]), len(ex)))
            if e.note.strip() != t.get("note", ""):
                changes.append(("메모", t.get("note", ""), e.note.strip()))
            t["level"], t["title"], t["removed"], t["examples"], t["note"] = e.level, e.title.strip() or t["title"], e.removed, ex, e.note.strip()
            if e.level != auto:
                t["pm"] = {"from": auto, "level": e.level, "reason": e.reason.strip() or t.get("pm", {}).get("reason", "")}
            else:
                t.pop("pm", None)
            for field, a, b in changes:
                rep["log"].append({"at": now, "topic": t["title"], "field": field, "from": a, "to": b, "reason": e.reason.strip(), "by": by})
        rebuild(rep)
    return {"ok": True, "log": rep["log"]}


@app.post("/api/reports/{slot}/approve")
def approve_report(slot: str, body: ApproveIn):
    """검토 완료. 예약 시각 전이면 그 시각에 보내고, 지났거나 '지금 보내기'면 바로 보낸다."""
    if not admin_ok(body.key):
        raise HTTPException(403, "관리자 키가 필요합니다.")
    with _lock:
        rep = get_report(slot)
        if not rep:
            raise HTTPException(404, "리포트가 없습니다.")
        if rep["sent"]:
            raise HTTPException(409, "이미 보낸 리포트입니다.")
        rep["status"] = "approved"
        rep["log"].append({"at": datetime.now(KST).isoformat(timespec="seconds"), "topic": "", "field": "검토 완료", "from": "", "to": "", "reason": ""})
        rebuild(rep)
        at = slot_time_of(slot)
        now_send = body.send_now or at is None or datetime.now(KST) >= at
        if now_send:
            try:
                send_once(slot)
            except ValueError as e:
                raise HTTPException(400, str(e))
    return {"ok": True, "sent": now_send, "scheduled": None if now_send else at.isoformat()}


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
    if body.send:
        raise HTTPException(400, "보내기 전에 리포트 카드에서 검토를 마쳐 주세요.")
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
    s.update(time=body.time, weekdays=sorted(set(body.weekdays)), enabled=body.enabled, auto_send=body.auto_send)
    if body.webhook:
        if not body.webhook.startswith("https://hooks.slack.com/"):
            raise HTTPException(400, "Slack Incoming Webhook 주소가 아닙니다.")
        s["webhook"] = body.webhook
    save_settings(s)
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}
