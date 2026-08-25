"""
route_search.py のユニットテスト。

実PDFを使わず、駅の発車時刻だけを持つ最小限の PageData を組み立てて検証する。
これまでの会話で見つかった不具合（0分接続駅での自己ヒット、行き止まり列車への
振り分け、普通→普通乗換の除外漏れ）を、そのまま回帰テストとして固定している。
"""
from __future__ import annotations

from app.pdf_extract import PageData
from app.route_search import search_routes


def _page(rows_spec: list[tuple[str, str, list[str | None]]], types: list[str], dests: list[str]) -> PageData:
    n = len(types)
    meta = {
        "type": types,
        "nickname": [None] * n,
        "dest": dests,
        "formation": [None] * n,
    }
    return PageData(n_cols=n, meta=meta, rows=rows_spec)


def test_simple_direct_route_to_watanabe():
    """香里園発の列車がそのまま中之島直通なら、乗換なしで到達できる"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00"]),
            ("京橋", "発", ["09:15"]),
            ("渡辺橋", "発", ["09:23"]),
        ],
        types=["準急"],
        dests=["中之島"],
    )
    chains = search_routes([page], target="渡辺橋", window_start="06:00", window_end="10:00")
    assert len(chains) == 1
    assert chains[0].depart == "09:00"
    assert chains[0].arrive == "09:23"
    assert chains[0].legs == []


def test_zero_buffer_station_does_not_self_match():
    """0分接続駅（守口市）で、自分自身を「次の列車」として拾ってしまわないこと"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00", "09:05"]),
            ("守口市", "発", ["09:10", "09:10"]),  # 2本が同時刻に守口市発
            ("京橋", "発", [None, "09:15"]),
            ("渡辺橋", "発", [None, "09:23"]),
        ],
        types=["普通", "快速急行"],
        dests=["淀屋橋", "中之島"],
    )
    chains = search_routes([page], target="渡辺橋", window_start="06:00", window_end="10:00")
    # 09:00発(1本目, 淀屋橋止まり)は守口市で「快速急行(2本目)」に乗り換えて到達できるはず
    matches = [c for c in chains if c.depart == "09:00"]
    assert matches, "守口市での同時刻乗換が探索できていない（自己ヒットで打ち切られている可能性）"
    assert matches[0].arrive == "09:23"
    assert matches[0].legs[0].station == "守口市"


def test_dead_end_train_is_skipped():
    """香里園に停まらない種別（特急など）に行く手を阻まれず、その先の普通に接続できること"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00", None, None]),
            ("京橋", "発", ["09:18", "09:20", "09:23"]),
            ("渡辺橋", "発", [None, None, "09:31"]),
        ],
        types=["快速急行", "特急", "普通"],
        # 2本目(特急)は淀屋橋止まりでこの先どの駅にも停まらない(=行き止まり)
        dests=["淀屋橋", "淀屋橋", "中之島"],
    )
    chains = search_routes([page], target="渡辺橋", window_start="06:00", window_end="10:00")
    assert len(chains) == 1
    assert chains[0].arrive == "09:31"
    assert chains[0].legs[0].type == "普通"  # 特急ではなく、その次の普通に接続できている


def test_local_to_local_transfer_is_excluded():
    """普通→普通の乗換は候補から除外されること"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00", None]),
            ("寝屋川市", "発", ["09:05", "09:07"]),
            ("渡辺橋", "発", [None, "09:30"]),
        ],
        types=["普通", "普通"],
        dests=["淀屋橋", "中之島"],
    )
    chains = search_routes([page], target="渡辺橋", window_start="06:00", window_end="10:00")
    assert chains == []  # 普通→普通しかルートがないので、到達できる経路はゼロ件になる


def test_yodoyabashi_uses_arrival_row_not_departure():
    """淀屋橋は終点（着のみ）なので、着の行から到達時刻を読めること"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00"]),
            ("淀屋橋", "着", ["09:35"]),
        ],
        types=["準急"],
        dests=["淀屋橋"],
    )
    chains = search_routes([page], target="淀屋橋", window_start="06:00", window_end="10:00")
    assert len(chains) == 1
    assert chains[0].arrive == "09:35"
    assert chains[0].legs == []
