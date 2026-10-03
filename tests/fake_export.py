"""Build a synthetic Meta export zip (all content is made up). Usable by tests or by hand:
    python tests/fake_export.py fake.zip
"""
import datetime as dt
import json
import sys
import zipfile

UTC = dt.timezone.utc


def ts(y, mo, d, h=12, mi=0):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp())


def meta_str(s: str) -> str:
    """Mimic Meta's bug: UTF-8 bytes stored as latin1 characters."""
    return s.encode("utf-8").decode("latin1")


def _dump(obj) -> bytes:
    # Meta writes \uXXXX escapes (ensure_ascii=True)
    return json.dumps(obj, ensure_ascii=True).encode("utf-8")


# (timestamp, text, media uris)
IG_1 = [
    (ts(2023, 5, 1, 1, 0), "안녕하세요 첫 번째 게시물입니다", ["media/posts/202305/a.jpg"]),
    (ts(2023, 5, 2, 3, 30), "연락처 010-1234-5678 로 주세요", []),
    (ts(2024, 1, 10, 6, 0), "메일은 hello.user@example.com 입니다", []),
]
IG_2 = [
    (ts(2024, 2, 3, 9, 0), "계좌 110-123-456789 로 보내주세요 @fake_friend 고마워요", []),
    (ts(2024, 2, 4, 9, 0), "그냥 평범한 글 @my_own_handle", []),
]
THREADS = [
    (ts(2024, 3, 1, 0, 0), "스레드 글 하나", False),
    (ts(2024, 3, 2, 0, 0), "스레드 답글 하나", True),
]


def build(path, only_posts=False, with_threads=True, big_posts=0):
    def ig_items(rows):
        out = []
        for t, text, uris in rows:
            out.append(
                {
                    "media": [
                        {"uri": u, "creation_timestamp": t, "title": meta_str(text)} for u in uris
                    ]
                    or [{"creation_timestamp": t, "title": meta_str(text)}],
                    "title": meta_str(text),
                    "creation_timestamp": t,
                }
            )
        return out

    ig1 = ig_items(IG_1)
    for i in range(big_posts):  # many long posts in 2022 to force bundle splitting
        t = ts(2022, 1, 1, 0, 0) + i * 60
        ig1.append({"title": meta_str("긴 글 %d " % i + "가" * 300), "creation_timestamp": t, "media": []})
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("your_instagram_activity/media/posts_1.json", _dump(ig1))
        z.writestr("your_instagram_activity/media/posts_2.json", _dump(ig_items(IG_2)))
        if with_threads and not only_posts:
            items = [
                {
                    "media": [
                        {
                            "creation_timestamp": t,
                            "title": meta_str(text),
                            "text_app_post": {"is_reply": reply},
                        }
                    ]
                }
                for t, text, reply in THREADS
            ]
            z.writestr(
                "your_instagram_activity/threads/threads_and_replies.json",
                _dump({"text_post_app_text_posts": items}),
            )
    return path


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "fake_export.zip"))
