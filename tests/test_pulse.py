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
    topics = [{"title": "서버 튕김 제보", "dept": "클라이언트·서버", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2", "dc-999"]},
              {"title": "스킨 호평", "dept": "BM·상점", "sentiment": "긍정", "ids": ["dc-3", "dc-4", "dc-5"]}]
    out, dropped = score_topics(topics, posts)
    assert dropped == 1
    assert out[0]["title"] == "서버 튕김 제보" and out[0]["level"] == 5 and out[0]["critical"] == 3
    assert out[1]["level"] <= 2


def test_drops_vague_and_chatter_topics():
    posts = [{"id": f"dc-{i}", "source": "디시", "title": "알렉스 버그 밈", "text": "", "url": "u", "views": 10, "comments": 1,
              "recommends": 0, "vote": None} for i in range(6)]
    topics = [{"title": "특정 버그 불만", "dept": "클라이언트·서버", "sentiment": "부정", "ids": ["dc-0", "dc-1"]},
              {"title": "스트리머 밈", "dept": "커뮤니티·기타", "sentiment": "혼재", "ids": ["dc-2", "dc-3"]},
              {"title": "알렉스 너프 요구", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-4", "dc-5"]}]
    out, _ = score_topics(topics, posts)
    assert [t["title"] for t in out] == ["알렉스 너프 요구"]


def test_post_counted_once():
    posts = [{"id": f"dc-{i}", "source": "디시", "title": "알렉스 코렐 밸런스", "text": "", "url": "u", "views": 10, "comments": 1,
              "recommends": 0, "vote": None} for i in range(4)]
    topics = [{"title": "알렉스 밸런스 불만", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2"]},
              {"title": "알렉스 스킬셋 불만", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2"]},
              {"title": "코렐 밸런스 불만", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-2", "dc-3", "dc-1"]}]
    out, _ = score_topics(topics, posts)
    assert [t["title"] for t in out] == ["알렉스 밸런스 불만"]


def test_unrelated_posts_and_balance_not_urgent():
    mk = lambda i, t: {"id": f"dc-{i}", "source": "디시", "title": t, "text": "", "url": "u", "views": 500, "comments": 30,
                       "recommends": 5, "vote": None}
    posts = [mk(0, "알렉스 너무 셈"), mk(1, "알렉스 너프 언제"), mk(2, "솥단지 파일 이름 웃기네"), mk(3, "알렉스 사기")]
    topics = [{"title": "알렉스 밸런스 불만", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2", "dc-3"]}]
    out, _ = score_topics(topics, posts)
    assert out[0]["count"] == 3                      # 관계없는 글(dc-2)은 빠진다
    assert all("솥단지" not in e["title"] for e in out[0]["examples"])
    assert out[0]["level"] <= 4                      # 밸런스 불만은 긴급이 아니다


def test_issue_word_required_and_balance_rollback_not_urgent():
    mk = lambda i, t: {"id": f"dc-{i}", "source": "디시", "title": t, "text": "", "url": "u", "views": 100, "comments": 3,
                       "recommends": 0, "vote": None}
    posts = [mk(0, "이바 딜 너프하라"), mk(1, "이바 모델링 버그"), mk(2, "이바 버그 또 남"),
             mk(3, "알렉스 롤백해라"), mk(4, "알렉스 패치 롤백 좀"), mk(5, "알렉스 롤백 언제")]
    topics = [{"title": "이바 버그 불만", "dept": "클라이언트·서버", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2"]},
              {"title": "알렉스 롤백 요청", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-3", "dc-4", "dc-5"]}]
    out, _ = score_topics(topics, posts)
    bug = next(t for t in out if t["title"] == "이바 버그 불만")
    assert bug["count"] == 2
    assert all(t["level"] <= 4 for t in out)
