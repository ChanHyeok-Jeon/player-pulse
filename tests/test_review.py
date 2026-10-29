import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from pulse import store
    monkeypatch.setattr(store, "DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PULSE_ADMIN_KEY", "k")
    monkeypatch.setenv("PULSE_SCHEDULER", "0")
    import app
    monkeypatch.setattr(app, "SETTINGS", str(tmp_path / "s.json"))
    app.save_settings({**app.load_settings(), "webhook": "https://hooks.slack.com/x"})
    sent = []
    monkeypatch.setattr(app.slack, "send", lambda hook, payload: sent.append(payload) or (200, "ok"))
    store.save([{"id": f"dc-{i}", "source": "디시", "title": f"이바 버그 {i}", "text": "", "url": f"u{i}", "created": "2026-10-29T01:00:00+09:00",
                 "views": 10, "recommends": 0, "comments": 1, "vote": None} for i in range(4)])
    t = {"title": "버그 제보 모음", "kind": "버그", "dept": "클라이언트·서버", "sentiment": "부정", "count": 4, "ids": [f"dc-{i}" for i in range(4)],
         "severe": 4, "critical": 0, "views": 40, "comments": 4, "recommends": 0, "steam_down": 0, "score": 9.0, "level": 4, "auto_level": 4,
         "reason": "오작동 제보 4건", "examples": [{"title": "이바 버그 0", "url": "u0"}, {"title": "이바 버그 1", "url": "u1"}]}
    with app._db() as c:
        c.execute("INSERT INTO reports (slot,start,end,created,sent,payload,topics,stats,seconds,status,log) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  ("custom-1", "2026-10-28T09:00:00+09:00", "2026-10-29T09:00:00+09:00", "", "", "{}", json.dumps([t]),
                   json.dumps({"total": 4, "dc": 4, "steam": 0, "steam_neg": 0}), 1.0, "draft", "[]"))
    return TestClient(app.app), app, sent


def edit(level, reason="", examples=("u0",), removed=False):
    return {"key": "k", "topics": [{"idx": 0, "level": level, "title": "이바 모델링 버그", "removed": removed,
                                    "examples": list(examples), "note": "재현 확인 중", "reason": reason}]}


def test_level_change_needs_reason_and_is_logged(env):
    client, app, sent = env
    assert client.post("/api/reports/custom-1/edit", json=edit(3)).status_code == 400
    assert client.post("/api/reports/custom-1/edit", json=edit(3, "재현 안 됨", examples=("u0", "u3"))).status_code == 200
    rep = app.get_report("custom-1")
    t = rep["topics"][0]
    assert t["level"] == 3 and t["pm"] == {"from": 4, "level": 3, "reason": "재현 안 됨"}
    assert [e["url"] for e in t["examples"]] == ["u0", "u3"]       # 링크 빼고, 그 주제의 다른 글로 바꾸기
    assert {l["field"] for l in rep["log"]} >= {"중요도", "주제 이름", "근거 링크", "메모"}
    assert not sent                                                 # 검토 완료 전에는 보내지 않는다


def test_approve_sends_once_with_pm_marks(env):
    client, app, sent = env
    client.post("/api/reports/custom-1/edit", json=edit(3, "재현 안 됨"))
    r = client.post("/api/reports/custom-1/approve", json={"key": "k"}).json()
    assert r["sent"] and len(sent) == 1
    text = json.dumps(sent[0], ensure_ascii=False)
    assert "PM 조정" in text and "재현 안 됨" in text and "PM 검토 후 발송" in text
    assert client.post("/api/reports/custom-1/approve", json={"key": "k"}).status_code == 409
    assert client.post("/api/reports/custom-1/edit", json=edit(2, "x")).status_code == 409


def test_removed_topic_not_sent(env):
    client, app, sent = env
    client.post("/api/reports/custom-1/edit", json=edit(4, removed=True))
    client.post("/api/reports/custom-1/approve", json={"key": "k"})
    assert "이바 모델링 버그" not in json.dumps(sent[0], ensure_ascii=False)


def test_review_without_key_but_send_needs_key(env):
    client, app, sent = env
    assert client.post("/api/reports/custom-1/edit", json={**edit(3, "재현 안 됨"), "key": ""}).status_code == 200
    assert app.get_report("custom-1")["log"][0]["by"] == "키 없음"
    assert client.post("/api/reports/custom-1/approve", json={"key": ""}).status_code == 403
    assert not sent
