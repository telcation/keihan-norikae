"""
香里園を起点に、指定した目的駅（渡辺橋・淀屋橋など）までの経路を、
乗換0〜MAX_TRANSFERS回まで網羅的に探索するモジュール。

探索方針（これまでの会話でのすり合わせを反映）:
- 対象乗換駅は香里園〜京橋間の14駅（天満橋は対象外）
- 乗換に必要な時間は1分以上を基本とし、萱島・守口市は同一時刻（0分）での乗換も可とする
- 各乗換駅では「その先へ進める最初の列車」を接続先とする
  （香里園に停まらない種別＝行き止まりの列車に探索を止められないよう除外する）
- 普通→普通の乗換は時間短縮にならないため除外する
- 「座れる可能性」の判断はしない（人間が経験則で判断する）。
  あくまで期限内に到着できる経路を網羅的に列挙することが目的
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass

from app.pdf_extract import PageData

ORIGIN_STATION = "香里園"

# 探索対象駅（香里園〜京橋、天満橋は対象外）
TRANSFER_STATIONS = [
    "香里園", "寝屋川市", "萱島", "大和田", "古川橋", "門真市", "西三荘", "守口市",
    "土居", "滝井", "千林", "森小路", "関目", "野江", "京橋",
]
STATION_ORDER = {s: i for i, s in enumerate(TRANSFER_STATIONS)}

ZERO_BUFFER_STATIONS = {"萱島", "守口市"}
DEFAULT_BUFFER_MIN = 1
MAX_TRANSFERS = 2
CANDIDATES_PER_STOP = 1

# サポートする目的駅: 行き先ラベル(dest) -> 到達時刻を読む行 (駅名, 着/発)
DESTINATIONS: dict[str, tuple[str, str]] = {
    "渡辺橋": ("渡辺橋", "発"),   # 中之島線直通。渡辺橋は通過駅のため「発」で代用
    "淀屋橋": ("淀屋橋", "着"),   # 京阪本線の終点
}
DEST_LABEL: dict[str, str] = {
    "渡辺橋": "中之島",
    "淀屋橋": "淀屋橋",
}


def _buffer(station: str) -> int:
    return 0 if station in ZERO_BUFFER_STATIONS else DEFAULT_BUFFER_MIN


def _tmin(t: str | None) -> int | None:
    if t is None or t in ("レ", "*****", "↴"):
        return None
    h, m = t.split(":")
    h, m = int(h), int(m)
    if h < 4:
        h += 24
    return h * 60 + m


def _fmt(m: int) -> str:
    h = (m // 60) % 24
    mm = m % 60
    return f"{h:02d}:{mm:02d}"


@dataclass
class Train:
    type: str
    dest: str
    stops: dict[str, int]
    target_arrive: int | None


@dataclass
class Leg:
    station: str
    arrive: str
    depart: str
    type: str


@dataclass
class Chain:
    depart: str
    type: str
    legs: list[Leg]
    arrive: str


def _build_trains(pages: list[PageData], target: str) -> list[Train]:
    dest_station, dest_subtype = DESTINATIONS[target]
    dest_label = DEST_LABEL[target]

    trains: list[Train] = []
    for page in pages:
        rowmap = {(st, sub): vals for (st, sub, vals) in page.rows}
        target_row = rowmap.get((dest_station, dest_subtype))
        n = page.n_cols
        for ci in range(n):
            dest = page.meta["dest"][ci]
            typ = page.meta["type"][ci]
            stops: dict[str, int] = {}
            for st in TRANSFER_STATIONS:
                row = rowmap.get((st, "発"))
                if row is None:
                    continue
                t = _tmin(row[ci])
                if t is not None:
                    stops[st] = t
            target_arrive = None
            if dest == dest_label and target_row is not None:
                target_arrive = _tmin(target_row[ci])
            if stops or target_arrive is not None:
                trains.append(Train(type=typ, dest=dest, stops=stops, target_arrive=target_arrive))
    return trains


def _is_viable(train: Train, at_station: str) -> bool:
    if train.target_arrive is not None:
        return True
    return any(STATION_ORDER.get(st, -1) > STATION_ORDER[at_station] for st in train.stops)


def search_routes(
    pages: list[PageData],
    target: str,
    window_start: str = "06:00",
    window_end: str = "10:00",
    max_transfers: int = MAX_TRANSFERS,
    candidates_per_stop: int = CANDIDATES_PER_STOP,
) -> list[Chain]:
    """指定した目的駅までの全経路（乗換0〜max_transfers回）を探索して返す"""
    if target not in DESTINATIONS:
        raise ValueError(f"未対応の目的駅です: {target}（対応: {list(DESTINATIONS)}）")

    trains = _build_trains(pages, target)

    station_trains: dict[str, list[tuple[int, Train]]] = defaultdict(list)
    for tr in trains:
        for st, t in tr.stops.items():
            station_trains[st].append((t, tr))
    for st in station_trains:
        station_trains[st].sort(key=lambda x: x[0])

    def next_departures(station: str, after_time: int, exclude: Train, from_type: str):
        lst = station_trains.get(station, [])
        times = [x[0] for x in lst]
        idx = bisect.bisect_left(times, after_time)
        out = []
        while idx < len(lst) and len(out) < candidates_per_stop:
            t, tr = lst[idx]
            if tr is not exclude and _is_viable(tr, station):
                if not (from_type == "普通" and tr.type == "普通"):
                    out.append((t, tr))
            idx += 1
        return out

    raw_results: list[dict] = []

    def dfs(train: Train, board_station: str, legs: list[Leg], transfers_used: int, seen: set[str]):
        if train.target_arrive is not None:
            raw_results.append({"legs": list(legs), "arrive": train.target_arrive})
        if transfers_used >= max_transfers:
            return
        for st in TRANSFER_STATIONS:
            if st not in train.stops:
                continue
            if STATION_ORDER[st] <= STATION_ORDER[board_station]:
                continue
            if st in seen:
                continue
            t_here = train.stops[st]
            for conn_time, conn_train in next_departures(st, t_here + _buffer(st), train, train.type):
                new_leg = Leg(station=st, arrive=_fmt(t_here), depart=_fmt(conn_time), type=conn_train.type)
                dfs(conn_train, st, legs + [new_leg], transfers_used + 1, seen | {st})

    ws, we = _tmin(window_start), _tmin(window_end)
    out: list[Chain] = []
    for tr in trains:
        if ORIGIN_STATION not in tr.stops:
            continue
        kdep = tr.stops[ORIGIN_STATION]
        if not (ws <= kdep <= we):
            continue
        before = len(raw_results)
        dfs(tr, ORIGIN_STATION, [], 0, {ORIGIN_STATION})
        for r in raw_results[before:]:
            out.append(Chain(depart=_fmt(kdep), type=tr.type, legs=r["legs"], arrive=_fmt(r["arrive"])))

    seen_keys = set()
    dedup: list[Chain] = []
    for c in out:
        key = (c.depart, tuple((leg.station, leg.depart) for leg in c.legs), c.arrive)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        dedup.append(c)

    dedup.sort(key=lambda c: (_tmin(c.depart), len(c.legs), tuple(leg.station for leg in c.legs)))
    return dedup


def chains_to_dict(chains: list[Chain]) -> list[dict]:
    return [
        {
            "type": c.type,
            "depart": c.depart,
            "legs": [{"station": leg.station, "arrive": leg.arrive, "depart": leg.depart, "type": leg.type} for leg in c.legs],
            "arrive": c.arrive,
        }
        for c in chains
    ]
