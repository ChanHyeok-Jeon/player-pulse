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
    posts = [{"id": f"dc-{i}", "source": "디시", "title": "서버 터짐 접속 불가" if i < 3 else "스킨 예쁘다", "text": "", "url": "u",
              "views": 100, "comments": 5, "recommends": 1, "vote": None} for i in range(6)]
    topics = [{"title": "서버 튕김 제보", "dept": "클라이언트·서버", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2", "dc-999"]},
              {"title": "스킨 호평", "dept": "BM·상점", "sentiment": "긍정", "ids": ["dc-3", "dc-4", "dc-5"]}]
    out, dropped = score_topics(topics, posts)
    assert dropped == 1
    assert out[0]["title"] == "서버 튕김 제보" and out[0]["level"] == 5 and out[0]["outage"] == 3
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


def _mk(i, t, v=100, c=3):
    return {"id": f"dc-{i}", "source": "디시", "title": t, "text": "", "url": "u", "views": v, "comments": c,
            "recommends": 0, "vote": None}


def test_personal_ban_questions_are_not_issues():
    posts = [_mk(i, t, 150, 10) for i, t in enumerate(["2주정지먹어서 힘듦", "노합류도 정지 먹음?", "욕설정지 많이빡셈?"])]
    topics = [{"target": "정지", "title": "정지·제재 불만", "kind": "질문", "dept": "운영·제재", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2"]}]
    out, _ = score_topics(topics, posts)
    assert out == []


def test_target_must_appear_in_post():
    # '알렉스 버프 요구'에 다른 실험체 버프 글이 섞이면 빠진다
    posts = [_mk(0, "히스이 W1 버프좀 해줘"), _mk(1, "엠마하디 버프까지 6시간"), _mk(2, "발밟기 버프는 뭔 버프임")]
    topics = [{"target": "알렉스", "title": "실험체 알렉스 버프 요구", "kind": "밸런스", "dept": "밸런스", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2"]}]
    out, _ = score_topics(topics, posts)
    assert out == []


def test_balance_capped_and_bug_reports_high():
    posts = [_mk(i, f"알렉스 너프해라 {i}", 2000, 80) for i in range(30)] + \
            [_mk(100, "q 누르다 탭키 눌러지는 버그"), _mk(101, "엠마 모자궁 버그"), _mk(102, "상점 버그로 구매 안됨 버그")]
    topics = [{"target": "알렉스", "title": "실험체 알렉스가 세다는 불만", "kind": "밸런스", "dept": "밸런스", "sentiment": "부정",
               "ids": [f"dc-{i}" for i in range(30)]},
              {"target": "여러 버그", "title": "여러 버그 제보", "kind": "버그", "dept": "클라이언트·서버", "sentiment": "부정",
               "ids": ["dc-100", "dc-101", "dc-102"]}]
    out, _ = score_topics(topics, posts)
    bal = next(t for t in out if t["kind"] == "밸런스")
    bug = next(t for t in out if t["kind"] == "버그")
    assert bal["level"] == 3 and bug["level"] == 4      # 글이 아무리 많아도 밸런스는 보통, 버그 3건은 높음
    assert out[0] is bug and bug["reason"]


def test_quiet_day_top_topic_not_high():
    # 순위가 아니라 고정 기준: 조용한 날 1위라도 글 3개짜리 의견이면 낮음
    posts = [_mk(i, "코발트 매칭 대기 길다") for i in range(3)]
    topics = [{"target": "코발트", "title": "코발트 모드 매칭 대기 불만", "kind": "UX", "dept": "매칭·랭크", "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2"]}]
    out, _ = score_topics(topics, posts)
    assert out[0]["level"] == 2


def test_verify_falls_back_to_titles_when_blocked():
    from pulse.analyze import verify
    posts = [_mk(i, "알렉스 너프해라") for i in range(4)] + [_mk(9, "알렉스 할만함?")]
    topics, _ = score_topics([{"target": "알렉스", "title": "실험체 알렉스 너프 요구", "kind": "밸런스", "dept": "밸런스",
                               "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2", "dc-3", "dc-9"]}], posts)
    calls = []
    fetch = lambda p: calls.append(p) or None             # 사이트가 빈 응답만 준다
    judge = lambda lines: {int(l.split(" | ")[0]): "할만함" not in l for l in lines}
    out = verify(topics, posts, fetch, delay=0, judge=judge)
    assert len(calls) == 3                                # 3번 막히면 더 요청하지 않는다
    assert out[0]["verify"] == "title" and (out[0]["confirmed"], out[0]["checked"]) == (4, 5)


def test_single_crash_reports_are_not_outage():
    # 각자 튕긴 글은 '높음'까지, 여러 명이 2시간 안에 서버 장애를 말할 때만 '긴급'
    mk = lambda i, t, h: {**_mk(i, t), "created": f"2026-10-28T{h:02d}:00:00+09:00"}
    posts = [mk(0, "나 튕겼어", 1), mk(1, "또 튕김", 5), mk(2, "게임 튕기네", 9),
             mk(3, "서버 터졌냐", 20), mk(4, "접속 안됨", 20), mk(5, "서버 다운?", 21)]
    solo, _ = score_topics([{"target": "서버·접속 장애", "title": "튕김·접속 문제 제보", "kind": "장애", "dept": "클라이언트·서버",
                             "sentiment": "부정", "ids": ["dc-0", "dc-1", "dc-2"]}], posts)
    wave, _ = score_topics([{"target": "서버·접속 장애", "title": "튕김·접속 문제 제보", "kind": "장애", "dept": "클라이언트·서버",
                             "sentiment": "부정", "ids": ["dc-3", "dc-4", "dc-5"]}], posts)
    assert solo[0]["level"] == 4 and wave[0]["level"] == 5
