"""
route_search.py のユニットテスト。

実PDFを使わず、駅の発車時刻だけを持つ最小限の PageData を組み立てて検証する。
これまでの会話で見つかった不具合（0分接続駅での自己ヒット、行き止まり列車への
振り分け、普通→普通乗換の除外漏れ）や、乗換駅を寝屋川市・萱島・守口市・京橋の
4駅に限定する仕様を、そのまま回帰テストとして固定している。
"""
from __future__ import annotations

from app.pdf_extract import PageData
from app.route_search import build_trains, find_baseline, find_feeders, train_to_dict


def _page(rows_spec, types, dests):
    n = len(types)
    meta = {"type": types, "nickname": [None] * n, "dest": dests, "formation": [None] * n}
    return PageData(n_cols=n, meta=meta, rows=rows_spec)


def test_simple_direct_route_to_watanabe():
    """香里園発の列車がそのまま中之島直通(渡辺橋停車)なら、乗換なしで到達できる"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00"]),
            ("京橋", "発", ["09:15"]),
            ("渡辺橋", "発", ["09:23"]),
        ],
        types=["準急"],
        dests=["中之島"],
    )
    trains = build_trains([page])
    baseline = find_baseline(trains, target="渡辺橋", deadline="09:30", window_start="06:00", window_end="10:00")
    assert baseline is not None
    depart, root_train, t_final, arrive, board_station = baseline
    assert depart == "09:00"
    assert arrive == "09:23"
    assert t_final.type == "準急"


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
    trains = build_trains([page])
    baseline = find_baseline(trains, target="渡辺橋", deadline="09:30", window_start="06:00", window_end="10:00")
    assert baseline is not None
    depart, root_train, t_final, arrive, board_station = baseline
    # 09:00発(普通)は守口市で0分接続により09:05発(快速急行)へ乗換可能で同じ09:23着になるが、
    # 同着の場合はより遅く出発できる方(09:05発)が基準ルートとして選ばれる。
    assert depart == "09:05"
    assert arrive == "09:23"


def test_dead_end_train_is_skipped():
    """香里園に停まらない種別（特急など）に行く手を阻まれず、その先の普通に接続できること"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00", None, None]),
            ("京橋", "発", ["09:18", "09:20", "09:23"]),
            ("渡辺橋", "発", [None, None, "09:31"]),
        ],
        types=["快速急行", "特急", "普通"],
        dests=["淀屋橋", "淀屋橋", "中之島"],  # 2本目(特急)はこの先どの駅にも停まらない行き止まり
    )
    trains = build_trains([page])
    baseline = find_baseline(trains, target="渡辺橋", deadline="09:40", window_start="06:00", window_end="10:00")
    assert baseline is not None
    _, _root, t_final, arrive, _board = baseline
    assert arrive == "09:31"
    assert t_final.type == "普通"  # 特急ではなく、その次の普通に接続できている


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
    trains = build_trains([page])
    baseline = find_baseline(trains, target="渡辺橋", deadline="09:30", window_start="06:00", window_end="10:00")
    assert baseline is None  # 普通→普通しかルートがないので、到達できる経路はゼロ件になる


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
    trains = build_trains([page])
    baseline = find_baseline(trains, target="淀屋橋", deadline="09:40", window_start="06:00", window_end="10:00")
    assert baseline is not None
    depart, root_train, t_final, arrive, board_station = baseline
    assert arrive == "09:35"
    assert t_final.stops == {"香里園": 540, "淀屋橋": 575}


def test_display_stations_limited_to_four_transfer_points():
    """乗換駅（表示駅）は寝屋川市・萱島・守口市・京橋に限定され、他の駅は保持しないこと"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00"]),
            ("寝屋川市", "発", ["09:03"]),
            ("萱島", "発", ["09:06"]),
            ("大和田", "発", ["09:08"]),   # 対象外の駅
            ("守口市", "発", ["09:12"]),
            ("天満橋", "発", ["09:20"]),   # 対象外の駅
            ("渡辺橋", "発", ["09:23"]),
        ],
        types=["普通"],
        dests=["中之島"],
    )
    trains = build_trains([page])
    assert trains[0].stops == {
        "香里園": 540, "寝屋川市": 543, "萱島": 546, "守口市": 552, "渡辺橋": 563,
    }


def test_find_feeders_no_minimum_buffer_and_excludes_local_to_local():
    """接続可能な列車の探索は、乗換時間の制限なし(同一時刻OK)・普通→普通は除外"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["08:50", "08:55", "09:00"]),
            ("京橋", "発", ["09:10", "09:10", "09:15"]),
            ("渡辺橋", "発", [None, None, "09:23"]),
        ],
        types=["普通", "準急", "普通"],
        dests=["淀屋橋", "淀屋橋", "中之島"],
    )
    trains = build_trains([page])
    t_final = trains[2]  # 09:00発、渡辺橋09:23着の列車
    feeders = find_feeders(trains, t_final, target="渡辺橋")
    types = {f["type"] for f in feeders}
    assert "普通" not in types  # 普通(t_finalと同種別)→普通の乗換は除外される
    assert "準急" in types      # 京橋09:10着(同一時刻ではないが余裕あり)は候補に入る
    feeder = next(f for f in feeders if f["type"] == "準急")
    assert feeder["transfer_points"] == [
        {"station": "京橋", "feeder_time": "09:10", "final_time": "09:15", "wait_min": 5}
    ]


def test_board_train_type_is_kept_when_transfer_happens():
    """乗換を挟む経路では、乗車時(root_train)と降車時(t_final)の種別が別々に分かること"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["08:57", None]),
            ("京橋", "発", ["09:14", "09:15"]),
            ("渡辺橋", "発", [None, "09:23"]),
        ],
        types=["準急", "普通"],
        dests=["淀屋橋", "中之島"],
    )
    trains = build_trains([page])
    baseline = find_baseline(trains, target="渡辺橋", deadline="09:30", window_start="06:00", window_end="10:00")
    assert baseline is not None
    depart, root_train, t_final, arrive, board_station = baseline
    assert depart == "08:57"
    assert root_train.type == "準急"  # 乗車時は準急
    assert t_final.type == "普通"     # 実際に渡辺橋まで運ぶのは京橋乗換後の普通


def test_feeder_stops_exclude_other_destination():
    """目的駅が渡辺橋のとき、フィーダー列車が淀屋橋行きでも淀屋橋の欄は表示に含めない"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["08:50", "09:00"]),
            ("京橋", "発", ["09:05", "09:15"]),
            ("淀屋橋", "着", ["09:10", None]),
            ("渡辺橋", "発", [None, "09:23"]),
        ],
        types=["準急", "普通"],
        dests=["淀屋橋", "中之島"],
    )
    trains = build_trains([page])
    t_final = trains[1]
    feeders = find_feeders(trains, t_final, target="渡辺橋")
    assert len(feeders) == 1
    assert "淀屋橋" not in feeders[0]["stops"]
    assert feeders[0]["stops"] == {"香里園": "08:50", "京橋": "09:05"}


def test_transfer_wait_over_15_minutes_is_rejected_in_baseline():
    """基準ルート探索で、乗換の待ち時間が15分を超える接続は採用されないこと"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00", None]),
            ("京橋", "発", ["09:10", "09:26"]),  # 待ち時間16分(不可)
            ("渡辺橋", "発", [None, "09:35"]),
        ],
        types=["準急", "普通"],
        dests=["淀屋橋", "中之島"],
    )
    trains = build_trains([page])
    baseline = find_baseline(trains, target="渡辺橋", deadline="09:40", window_start="06:00", window_end="10:00")
    assert baseline is None  # 16分待ちの接続しかないため、到達できる経路はゼロ件になる


def test_transfer_wait_exactly_15_minutes_is_rejected():
    """乗換の待ち時間がちょうど15分は「15分未満」の範囲外なので接続できないこと"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00", None]),
            ("京橋", "発", ["09:10", "09:25"]),  # 待ち時間15分(不可、15分未満のみ許容)
            ("渡辺橋", "発", [None, "09:34"]),
        ],
        types=["準急", "普通"],
        dests=["淀屋橋", "中之島"],
    )
    trains = build_trains([page])
    baseline = find_baseline(trains, target="渡辺橋", deadline="09:40", window_start="06:00", window_end="10:00")
    assert baseline is None


def test_transfer_wait_14_minutes_is_accepted():
    """乗換の待ち時間が14分（15分未満）なら接続できること"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["09:00", None]),
            ("京橋", "発", ["09:10", "09:24"]),  # 待ち時間14分(可)
            ("渡辺橋", "発", [None, "09:33"]),
        ],
        types=["準急", "普通"],
        dests=["淀屋橋", "中之島"],
    )
    trains = build_trains([page])
    baseline = find_baseline(trains, target="渡辺橋", deadline="09:40", window_start="06:00", window_end="10:00")
    assert baseline is not None
    assert baseline[3] == "09:33"


def test_feeder_transfer_wait_over_15_minutes_is_excluded():
    """接続可能な列車の探索でも、待ち時間15分超の乗換駅は候補に数えないこと"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["08:50", "09:00"]),
            ("京橋", "発", ["08:55", "09:15"]),  # t_finalの京橋発09:15に対し、待ち20分
            ("渡辺橋", "発", [None, "09:23"]),
        ],
        types=["準急", "普通"],
        dests=["淀屋橋", "中之島"],
    )
    trains = build_trains([page])
    t_final = trains[1]
    feeders = find_feeders(trains, t_final, target="渡辺橋")
    assert feeders == []  # 待ち時間20分は上限15分を超えるため候補に入らない


def test_feeder_transfer_wait_exactly_15_minutes_is_excluded():
    """接続可能な列車の探索で、待ち時間がちょうど15分の場合も「15分未満」に含まれず除外されること"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["08:50", "09:00"]),
            ("京橋", "発", ["09:00", "09:15"]),  # t_finalの京橋発09:15に対し、待ちちょうど15分
            ("渡辺橋", "発", [None, "09:23"]),
        ],
        types=["準急", "普通"],
        dests=["淀屋橋", "中之島"],
    )
    trains = build_trains([page])
    t_final = trains[1]
    feeders = find_feeders(trains, t_final, target="渡辺橋")
    assert feeders == []


def test_final_train_stops_exclude_section_before_boarding():
    """到着列車の表示は、乗換前（乗車前）の区間を含まないこと
    （香里園より前に発車する駅の時刻が、乗換駅より前に表示されてしまう不具合の回帰テスト）"""
    page = _page(
        rows_spec=[
            ("香里園", "発", ["08:57", None]),
            ("萱島", "発", [None, "08:51"]),   # t_final自身は萱島からすでに走っている
            ("守口市", "発", [None, "09:01"]),
            ("京橋", "発", ["09:14", "09:15"]),
            ("渡辺橋", "発", [None, "09:23"]),
        ],
        types=["準急", "普通"],
        dests=["淀屋橋", "中之島"],
    )
    trains = build_trains([page])
    baseline = find_baseline(trains, target="渡辺橋", deadline="09:30", window_start="06:00", window_end="10:00")
    assert baseline is not None
    depart, root_train, t_final, arrive, board_station = baseline
    assert board_station == "京橋"  # 実際に乗換で乗り込むのは京橋
    displayed = train_to_dict(t_final, target="渡辺橋", from_station=board_station)
    # 萱島(08:51)・守口市(09:01)は乗換(京橋)より前の区間なので表示に含まれない
    assert displayed["stops"] == {"京橋": "09:15", "渡辺橋": "09:23"}
