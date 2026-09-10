"""
香里園を起点とした経路探索モジュール。

【この仕様に至る経緯・方針】
以前は「期限内に到着できる経路（直通・乗換1〜2回）を全パターン列挙する」実装だったが、
以下の理由から「基準列車1本の全時刻表 + それに接続可能な全列車の全時刻表」を
表示する方式に変更した。

- 基準ルート（期限内で最も遅く出発できる経路）を検索し、実際に目的駅まで運んでくれる
  「到着列車」を1本特定する
- その到着列車について、実際に乗客が乗っている区間（乗換駅以降。乗換が無ければ
  香里園から）の停車駅・時刻のみを表示する（乗換前・乗車前の区間は表示しない）
- その到着列車に乗り換え可能な列車（香里園発の全列車が対象）も、香里園以降の
  停車駅・時刻を全て表示する
  - 普通→普通の乗換は時間短縮にならないため除外する
  - 乗換に必要な待ち時間は0〜MAX_TRANSFER_WAIT_MIN分の範囲に限定する
    （0分＝同一時刻の乗換も可とするが、待ちすぎる乗換は現実的でないため上限を設ける）
- 「座れる可能性」の判断はしない（人間が経験則で判断する）。あくまで機械的に
  接続可能な全列車を洗い出すことが目的
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass

from app.pdf_extract import PageData

ORIGIN_STATION = "香里園"

# 乗換駅（表示駅）：発着駅（香里園・目的駅）以外は、寝屋川市・萱島・守口市・京橋の
# 4駅に限定する（大和田・古川橋・門真市・西三荘・土居・滝井・千林・森小路・関目・野江・
# 天満橋・北浜・なにわ橋・大江橋・中之島は対象外）。
FULL_STATIONS = ["香里園", "寝屋川市", "萱島", "守口市", "京橋", "渡辺橋", "淀屋橋"]
STATION_ORDER = {s: i for i, s in enumerate(FULL_STATIONS)}

# 探索時に乗換駅として使ってよいのは香里園・寝屋川市・萱島・守口市・京橋の5駅のみ
TRANSFER_STATIONS = ["香里園", "寝屋川市", "萱島", "守口市", "京橋"]

# 各駅で読むべき行のsubtype（着/発）。基本は発、終着駅だけ着。
SUBTYPE_OF = {"淀屋橋": "着"}

# 目的駅名 -> 列車のdest欄に入る行き先ラベル
DESTINATIONS: dict[str, str] = {"渡辺橋": "中之島", "淀屋橋": "淀屋橋"}

MAX_TRANSFERS = 2
MAX_TRANSFER_WAIT_MIN = 15  # 乗換の待ち時間の上限（分）。0分（同一時刻）は許容する。


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
    stops: dict[str, int]  # 駅名 -> 分（0:00起点）


def build_trains(pages: list[PageData]) -> list[Train]:
    """PDF解析結果から、香里園以降の全駅の停車時刻を持つ列車一覧を作る"""
    trains: list[Train] = []
    for page in pages:
        rowmap = {(st, sub): vals for (st, sub, vals) in page.rows}
        n = page.n_cols
        for ci in range(n):
            dest = page.meta["dest"][ci]
            typ = page.meta["type"][ci]
            stops: dict[str, int] = {}
            for st in FULL_STATIONS:
                sub = SUBTYPE_OF.get(st, "発")
                row = rowmap.get((st, sub))
                if row is None:
                    continue
                t = _tmin(row[ci])
                if t is not None:
                    stops[st] = t
            if stops:
                trains.append(Train(type=typ, dest=dest, stops=stops))
    return trains


def _is_viable(train: Train, at_station: str, dest_label: str, target: str) -> bool:
    """at_station から見て、この列車がこの先（目的駅到着 or さらに先の駅）に進めるか"""
    if train.dest == dest_label and target in train.stops:
        return True
    return any(STATION_ORDER.get(s, -1) > STATION_ORDER[at_station] for s in train.stops)


def find_baseline(
    trains: list[Train],
    target: str,
    deadline: str,
    window_start: str = "06:00",
    window_end: str = "10:00",
) -> tuple[str, Train, Train, str, str] | None:
    """
    期限内で最も遅く香里園を出発できる経路を探索し、実際に目的駅まで運んでくれる
    「到着列車」を特定する。

    - 乗換は最大 MAX_TRANSFERS 回まで（各乗換駅では、行き止まりの列車を除いた
      最初の1本を接続先とする）
    - 乗換の待ち時間は0〜MAX_TRANSFER_WAIT_MIN分（同一時刻の乗換も可、待ちすぎは除外）
    - 普通→普通の乗換は除外する

    戻り値: (香里園発時刻, 乗車列車(Train), 到着列車(Train), 目的駅着時刻, 到着列車への乗車駅)
    の組。乗換が無い経路の場合、乗車列車と到着列車は同一のTrainになり、乗車駅は香里園になる。
    見つからなければ None。
    """
    if target not in DESTINATIONS:
        raise ValueError(f"未対応の目的駅です: {target}（対応: {list(DESTINATIONS)}）")
    dest_label = DESTINATIONS[target]
    dl = _tmin(deadline)
    ws, we = _tmin(window_start), _tmin(window_end)

    station_trains: dict[str, list[tuple[int, Train]]] = defaultdict(list)
    for tr in trains:
        for st, t in tr.stops.items():
            station_trains[st].append((t, tr))
    for st in station_trains:
        station_trains[st].sort(key=lambda x: x[0])

    def next_departure(station: str, after_time: int, exclude: Train, from_type: str):
        lst = station_trains.get(station, [])
        times = [x[0] for x in lst]
        idx = bisect.bisect_left(times, after_time)  # 同一時刻の乗換も可
        while idx < len(lst):
            t, tr = lst[idx]
            if t - after_time > MAX_TRANSFER_WAIT_MIN:
                return None  # 以降はさらに待ち時間が伸びるだけなので打ち切る
            if tr is not exclude and _is_viable(tr, station, dest_label, target):
                if not (from_type == "普通" and tr.type == "普通"):
                    return t, tr
            idx += 1
        return None

    # (root_depart, root_train, t_final, arrive, board_station_of_t_final)
    results: list[tuple[int, Train, Train, int, str]] = []

    def dfs(
        train: Train,
        board_station: str,
        transfers_used: int,
        seen: set[str],
        root_depart: int,
        root_train: Train,
    ):
        if train.dest == dest_label and target in train.stops:
            results.append((root_depart, root_train, train, train.stops[target], board_station))
        if transfers_used >= MAX_TRANSFERS:
            return
        for st in TRANSFER_STATIONS:
            if st not in train.stops or STATION_ORDER[st] <= STATION_ORDER[board_station] or st in seen:
                continue
            t_here = train.stops[st]
            nd = next_departure(st, t_here, train, train.type)
            if nd is None:
                continue
            _, conn = nd
            dfs(conn, st, transfers_used + 1, seen | {st}, root_depart, root_train)

    for tr in trains:
        if ORIGIN_STATION not in tr.stops:
            continue
        kdep = tr.stops[ORIGIN_STATION]
        if not (ws <= kdep <= we):
            continue
        dfs(tr, ORIGIN_STATION, 0, {ORIGIN_STATION}, kdep, tr)

    valid = [(d, root, tr, a, bs) for (d, root, tr, a, bs) in results if a <= dl]
    if not valid:
        return None
    # 到着が最も遅い(=期限にもっとも近い)ものを採用。同着なら出発が遅い方を優先。
    valid.sort(key=lambda x: (x[3], x[0]))
    depart, root_train, t_final, arrive, board_station = valid[-1]
    return _fmt(depart), root_train, t_final, _fmt(arrive), board_station


OTHER_DESTINATION = {"渡辺橋": "淀屋橋", "淀屋橋": "渡辺橋"}


def _filtered_stops(stops: dict[str, int], target: str, from_station: str | None = None) -> dict[str, str]:
    """表示用に停車駅の時刻を整形する。

    - 今回の目的駅と異なる方の終着駅（例: 目的駅が渡辺橋のときの淀屋橋）は、
      その列車がたまたま停車していても路線が異なり無関係なため取り除く。
    - from_station を指定した場合、その駅より手前（乗客がまだ乗っていない区間）
      の停車駅も取り除く（到着列車の、乗換前の区間を表示しないようにするため）。
    """
    other = OTHER_DESTINATION.get(target)
    min_order = STATION_ORDER.get(from_station, -1) if from_station else -1
    return {
        st: _fmt(t)
        for st, t in stops.items()
        if st != other and STATION_ORDER.get(st, -1) >= min_order
    }


def find_feeders(trains: list[Train], t_final: Train, target: str) -> list[dict]:
    """
    到着列車(t_final)に乗換可能な、香里園発の全列車を探す。

    - 乗換の待ち時間は0〜MAX_TRANSFER_WAIT_MIN分（同一時刻の乗換も可、待ちすぎは除外）
    - 普通→普通の乗換は除外する
    - t_final 自身は候補から除く

    戻り値: 香里園発時刻の降順（＝最も遅く出発できるものが先頭）にソートした
    [{"type":..., "stops": {駅名: "HH:MM", ...}, "transfer_points": [{"station":..., "feeder_time":..., "final_time":...}, ...]}, ...]
    """
    feeders = []
    for tr in trains:
        if tr is t_final or ORIGIN_STATION not in tr.stops:
            continue
        points = []
        for st in TRANSFER_STATIONS:
            if st == ORIGIN_STATION:
                continue  # 香里園は乗車駅であって乗換駅ではないため、比較対象から除く
            if st in tr.stops and st in t_final.stops:
                if tr.type == "普通" and t_final.type == "普通":
                    continue
                wait = t_final.stops[st] - tr.stops[st]
                if 0 <= wait <= MAX_TRANSFER_WAIT_MIN:
                    points.append({"station": st, "feeder_time": _fmt(tr.stops[st]), "final_time": _fmt(t_final.stops[st])})
        if points:
            feeders.append({
                "type": tr.type,
                "stops": _filtered_stops(tr.stops, target),
                "transfer_points": points,
                "_depart": tr.stops[ORIGIN_STATION],
            })
    feeders.sort(key=lambda f: f["_depart"], reverse=True)
    for f in feeders:
        del f["_depart"]
    return feeders


def train_to_dict(train: Train, target: str, from_station: str | None = None) -> dict:
    return {"type": train.type, "dest": train.dest, "stops": _filtered_stops(train.stops, target, from_station)}
