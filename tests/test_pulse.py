from datetime import datetime

from pulse.analyze import score_topics, window
from pulse.collect import KST
from pulse.slack import dots


def test_window_weekday_and_monday():
    wd = [0, 1, 2, 3, 4]
    # 목요일 9:05 → 수 9:00 ~ 목 9:00
    s, e = window(datetime(2026, 10, 29, 9, 5, tzinfo=KST), "09:00", wd)
    assert (s.day, e.day, e.hour) == (28, 29, 9)
    # 월요일 9:00 → 금 9:00 ~ 월 9:00 (주말 포함)
    s, e = window(datetime(2026, 11, 2, 9, 0, tzinfo=KST), "09:00", wd)
    assert (s.weekday(), s.day, e.day) == (4, 30, 2)


def test_dots():
    assert dots(5) == "●●●●●" and dots(2) == "●●○○○"


def test_scoring_drops_unknown_ids_and_caps_positive():
    posts = [{"id": f"dc-{i}", "source": "디시", "title": "서버 튕김" if i < 3 else "스킨 예쁘다", "text": "", "url": "u",
              "views": 100, "comments": 5, "recommends": 1, "vote": None} for i in range(6)]
    topics = [{"title": "접속 튕김 제보", "dept": "클라이언트·서버", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2", "dc-999"]},
              {"title": "신규 스킨 호평", "dept": "BM·상점", "sentiment": "긍정", "ids": ["dc-3", "dc-4", "dc-5"]}]
    out, dropped = score_topics(topics, posts)
    assert dropped == 1
    assert out[0]["title"] == "접속 튕김 제보" and out[0]["level"] == 5 and out[0]["severe"] == 3
    assert out[1]["level"] <= 2


def test_drops_vague_and_chatter_topics():
    posts = [{"id": f"dc-{i}", "source": "디시", "title": "x", "text": "", "url": "u", "views": 10, "comments": 1,
              "recommends": 0, "vote": None} for i in range(6)]
    topics = [{"title": "특정 버그 불만", "dept": "클라이언트·서버", "sentiment": "부정", "ids": ["dc-0", "dc-1"]},
              {"title": "스트리머 밈", "dept": "커뮤니티·기타", "sentiment": "혼재", "ids": ["dc-2", "dc-3"]},
              {"title": "알렉스 너프 요구", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-4", "dc-5"]}]
    out, _ = score_topics(topics, posts)
    assert [t["title"] for t in out] == ["알렉스 너프 요구"]


def test_post_counted_once():
    posts = [{"id": f"dc-{i}", "source": "디시", "title": "x", "text": "", "url": "u", "views": 10, "comments": 1,
              "recommends": 0, "vote": None} for i in range(4)]
    topics = [{"title": "알렉스 밸런스 불만", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2"]},
              {"title": "알렉스 스킬셋 불만", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2"]},
              {"title": "코렐 밸런스 불만", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-2", "dc-3", "dc-1"]}]
    out, _ = score_topics(topics, posts)
    assert [t["title"] for t in out] == ["알렉스 밸런스 불만"]
