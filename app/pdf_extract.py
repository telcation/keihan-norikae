"""
京阪電気鉄道「全列車時刻表PDF」（京阪本線・中之島線・鴨東線 下り）を解析するモジュール。

PDFはExcel等で作られたと思われる、座標が揃った表形式のテキストで構成されている。
各ページには複数の列車（列）と、駅ごとの行が並ぶ。テキスト抽出だけでは列がずれるため、
pdfplumberで単語ごとの座標(x, top)を取得し、ヘッダー行の座標を基準に列を復元する。

この設計についての注記:
- 列車種別ヘッダー行の x 座標をその日の「列の基準グリッド」として使う
- 駅ラベル（香里園、京橋 等）は行の左端(x≈52.9)に単独で現れるため、それをそのまま
  行の識別に使える（回転テキストではなく通常のテキストとして抽出される）
- 「着」「発」は同じ駅で2行に分かれることがある（停車時間があるケース）
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import pdfplumber

LABEL_X = 52.9
SUBTYPE_X = 103.8
X_TOLERANCE = 3
TOP_CLUSTER_TOL = 1.0
COLUMN_MATCH_TOLERANCE = 20

META_LABELS = {"列車種別", "列車愛称", "行先駅", "両数・扉数・備考"}


@dataclass
class PageData:
    n_cols: int
    meta: dict[str, list[str | None]]
    rows: list[tuple[str, str, list[str | None]]] = field(default_factory=list)


def _cluster_tops(tops: list[float], tol: float = TOP_CLUSTER_TOL) -> dict[float, float]:
    """近い 'top' 座標（同じ行とみなせるもの）をひとつの代表値にまとめる"""
    tops = sorted(tops)
    clusters: list[list[float]] = []
    cur = [tops[0]]
    for t in tops[1:]:
        if t - cur[-1] <= tol:
            cur.append(t)
        else:
            clusters.append(cur)
            cur = [t]
    clusters.append(cur)
    mapping: dict[float, float] = {}
    for c in clusters:
        rep = sum(c) / len(c)
        for t in c:
            mapping[t] = rep
    return mapping


def extract_page(page) -> PageData | None:
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return None
    raw_tops = [round(w["top"], 1) for w in words]
    top_to_rep = _cluster_tops(raw_tops)

    lines: dict[float, list[dict]] = {}
    for w in words:
        rt = top_to_rep[round(w["top"], 1)]
        lines.setdefault(rt, []).append(w)
    sorted_tops = sorted(lines.keys())

    header_top = next(
        (t for t in sorted_tops if any(w["text"] == "列車種別" for w in lines[t])), None
    )
    if header_top is None:
        return None

    header_words = sorted((w for w in lines[header_top] if w["x0"] > 110), key=lambda w: w["x0"])
    col_centers = [(w["x0"] + w["x1"]) / 2 for w in header_words]
    n_cols = len(col_centers)
    if n_cols == 0:
        return None

    def assign_col(x0: float, x1: float) -> int | None:
        cx = (x0 + x1) / 2
        best_i, best_d = None, float("inf")
        for i, c in enumerate(col_centers):
            d = abs(cx - c)
            if d < best_d:
                best_d, best_i = d, i
        return best_i if best_d < COLUMN_MATCH_TOLERANCE else None

    def get_meta_row(label: str) -> list[str | None]:
        for t in sorted_tops:
            if any(w["text"] == label for w in lines[t]):
                vals: list[str | None] = [None] * n_cols
                for w in lines[t]:
                    if w["x0"] <= 110:
                        continue
                    ci = assign_col(w["x0"], w["x1"])
                    if ci is not None:
                        vals[ci] = w["text"]
                return vals
        return [None] * n_cols

    meta = {
        "type": get_meta_row("列車種別"),
        "nickname": get_meta_row("列車愛称"),
        "dest": get_meta_row("行先駅"),
        "formation": get_meta_row("両数・扉数・備考"),
    }

    rows: list[tuple[str, str, list[str | None]]] = []
    current_station: str | None = None
    for t in sorted_tops:
        lw = sorted(lines[t], key=lambda w: w["x0"])
        label_word = None
        subtype = None
        data_words = []
        for w in lw:
            if abs(w["x0"] - LABEL_X) < X_TOLERANCE and w["text"] not in ("着", "発"):
                label_word = w["text"]
            elif abs(w["x0"] - SUBTYPE_X) < X_TOLERANCE and w["text"] in ("着", "発"):
                subtype = w["text"]
            elif w["x0"] > 110:
                data_words.append(w)
        if label_word and label_word in META_LABELS:
            continue
        if label_word:
            current_station = label_word
        if subtype is None or current_station is None:
            continue
        row_vals: list[str | None] = [None] * n_cols
        for w in data_words:
            ci = assign_col(w["x0"], w["x1"])
            if ci is not None:
                row_vals[ci] = w["text"]
        rows.append((current_station, subtype, row_vals))

    return PageData(n_cols=n_cols, meta=meta, rows=rows)


def extract_pdf(pdf_bytes: bytes) -> list[PageData]:
    """PDFのバイト列を受け取り、各ページの解析結果を返す"""
    pages: list[PageData] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            result = extract_page(page)
            if result is not None:
                pages.append(result)
    return pages


def extract_revision_date(pdf_bytes: bytes) -> str | None:
    """PDF先頭ページのタイトル行から改正日（例:2026年8月24日）を抜き出す。
    見つからない場合はNoneを返す。"""
    import re

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            return None
        text = pdf.pages[0].extract_text() or ""
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日変更", text)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
