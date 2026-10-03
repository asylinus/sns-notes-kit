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


# ---- fake saved / liked / collections (all made up) ----
OWNERS = {"one": ("stranger_one", "Stranger One"), "two": ("stranger_two", "Stranger Two")}
CAP_A = "맛집 추천 @someone_else 님 글 연락 010-9999-8888 stranger_one 계정 #여행"


def _post(code, caption, owner, tags, t):
    uname, name = OWNERS[owner]
    url = "https://www.instagram.com/p/%s/" % code
    lv = [
        {"label": "URL", "value": url, "href": url},
        {"label": "Caption", "value": meta_str(caption)},
        {
            "dict": [
                {
                    "dict": [
                        {"label": "URL", "value": "https://www.instagram.com/" + uname},
                        {"label": "Name", "value": meta_str(name)},
                        {"label": "Username", "value": uname},
                    ],
                    "title": "",
                }
            ],
            "title": "Owner",
        },
        {
            "dict": [{"dict": [{"label": "Name", "value": meta_str(h)}], "title": ""} for h in tags],
            "title": "Hashtags",
        },
    ]
    return {"timestamp": t, "media": [], "label_values": lv, "fbid": code}


TASTE_SAVED = [
    _post("AAA111", CAP_A, "one", ["여행", "맛집"], ts(2021, 3, 1)),
    _post("BBB222", "저장만 한 글 mail me@example.org", "two", [], ts(2022, 4, 2)),
]
TASTE_LIKED = [
    _post("AAA111", CAP_A, "one", ["여행", "맛집"], ts(2021, 3, 5)),
    _post("CCC333", "좋아요만 한 글", "one", ["카페"], ts(2023, 6, 7)),
]


def _collections():
    def coll(name, posts, t):
        lv = [
            {"label": "Name", "value": meta_str(name)},
            {"label": "Type", "value": "Default"},
            {"label": "Update time", "timestamp_value": t},
            {"dict": [{"dict": p["label_values"]} for p in posts], "title": ""},
        ]
        return {"timestamp": t, "media": [], "label_values": lv}

    return [coll("여행 모음", TASTE_SAVED, ts(2021, 3, 2)), coll("빈 컬렉션", [], ts(2021, 3, 3))]


def _link_items(key, rows):
    return {
        key: [
            {"title": meta_str(n), "string_list_data": [{"href": h, "value": "", "timestamp": t}]}
            for n, h, t in rows
        ]
    }


def add_taste(z):
    base = "your_instagram_activity/"
    z.writestr(base + "saved/saved_posts.json", _dump(TASTE_SAVED))
    z.writestr(base + "saved/saved_collections.json", _dump(_collections()))
    z.writestr(base + "likes/liked_posts.json", _dump(TASTE_LIKED))
    z.writestr(
        base + "likes/liked_comments.json",
        _dump(_link_items("likes_comment_likes",
                          [("stranger_two", "https://www.instagram.com/p/DDD444/c/17900000000000001/", ts(2019, 1, 2))])),
    )
    z.writestr(
        base + "threads/liked_threads.json",
        _dump(_link_items("text_post_app_media_likes",
                          [("Stranger One", "https://www.threads.net/@stranger_one/post/EEE555", ts(2024, 5, 6))])),
    )
    music = [{
        "timestamp": ts(2022, 2, 2), "media": [], "fbid": "1",
        "label_values": [{"label": "Title", "value": meta_str("노래")}, {"label": "Artist", "value": meta_str("가수")}],
    }]
    z.writestr(base + "saved/saved_music.json", _dump(music))


def build_taste_only(path):
    with zipfile.ZipFile(path, "w") as z:
        add_taste(z)
    return path


def build_html(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("start_here.html", "<html></html>")
        z.writestr("your_instagram_activity/media/posts_1.html", "<html></html>")
    return path


def build(path, only_posts=False, with_threads=True, big_posts=0, taste=False):
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
        if taste:
            add_taste(z)
    return path


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "fake_export.zip"))
