"""한 번만 돌려 보는 샘플 리포트. 최근 N시간 글을 모아 리포트를 JSON으로 저장한다 (Slack 발송·DB 저장 없음).

예) PULSE_GAME=bdm AWS_PROFILE=newaccount python3 sample.py --hours 168 --out data/bdm_sample.json
"""
import argparse
import json
import time
from datetime import datetime, timedelta

from pulse import analyze
from pulse.collect import KST, SOURCE, collect, fetch_detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--pages", type=int, default=40)
    ap.add_argument("--out", default="data/sample.json")
    a = ap.parse_args()

    end = datetime.now(KST).replace(second=0, microsecond=0)
    start = end - timedelta(hours=a.hours)
    print(f"수집: {SOURCE} {start:%m/%d %H:%M} ~ {end:%m/%d %H:%M}")
    posts = collect(start, max_pages=a.pages, delay=1.5)
    print(f"  글 {len(posts)}건")
    if not posts:
        raise SystemExit("수집된 글이 없습니다.")

    t0 = time.time()
    topics, _ = analyze.score_topics(analyze.cluster(analyze.pick(posts)), posts)
    topics = analyze.assign_levels(analyze.verify(topics, posts, fetch_detail, delay=1.0))
    secs = round(time.time() - t0, 1)

    out = {"game": analyze.PROFILE["name"], "source": SOURCE, "start": start.isoformat(), "end": end.isoformat(),
           "stats": analyze.summary_stats(posts), "seconds": secs,
           "topics": [{k: t.get(k) for k in ("title", "kind", "dept", "sentiment", "level", "reason", "count", "confirmed",
                                              "checked", "views", "comments", "examples")} for t in topics]}
    with open(a.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"  정리 {secs}초, 주제 {len(topics)}개 → {a.out}")
    for t in topics:
        print(f"  {'●' * t['level']}{'○' * (5 - t['level'])} {t['title']} · {t['reason']}")


if __name__ == "__main__":
    main()
