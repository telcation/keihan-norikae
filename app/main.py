from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# `python app/main.py` のように直接実行された場合、Pythonはこのファイルのある
# app/ ディレクトリだけをモジュール探索パスに入れるため、`from app.config import ...`
# のような絶対importが解決できずエラーになる。プロジェクトルート（app/の親）を
# 明示的にパスへ追加しておくことで、`python app/main.py` /
# `python -m app.main` / `uvicorn app.main:app` のどの起動方法でも動くようにする。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import UPLOAD_MAX_BYTES
from app.pdf_extract import extract_pdf, extract_revision_date
from app.route_search import (
    DESTINATIONS,
    Train,
    find_baseline,
    find_feeders,
    build_trains,
    train_to_dict,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("norikae")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TRAINS_FILE = DATA_DIR / "trains.json"
STATIC_DIR = BASE_DIR / "static"

DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="京阪 乗換案内API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _save_trains(revision_date: str | None, trains: list[Train]) -> None:
    payload = {
        "revision_date": revision_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trains": [{"type": tr.type, "dest": tr.dest, "stops": tr.stops} for tr in trains],
    }
    TRAINS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_trains() -> tuple[str | None, list[Train]] | None:
    if not TRAINS_FILE.exists():
        return None
    payload = json.loads(TRAINS_FILE.read_text(encoding="utf-8"))
    trains = [Train(type=t["type"], dest=t["dest"], stops=t["stops"]) for t in payload["trains"]]
    return payload.get("revision_date"), trains


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="PDFファイルをアップロードしてください")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="空のファイルです")
    if len(pdf_bytes) > UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"ファイルサイズが大きすぎます（上限 {UPLOAD_MAX_BYTES // (1024*1024)}MB）",
        )

    try:
        pages = extract_pdf(pdf_bytes)
        if not pages:
            raise ValueError("PDFからページを読み取れませんでした（フォーマットが想定と異なる可能性があります）")
        revision_date = extract_revision_date(pdf_bytes)
        trains = build_trains(pages)
    except Exception as e:  # noqa: BLE001
        logger.exception("PDF解析に失敗しました")
        raise HTTPException(status_code=422, detail=f"PDFの解析に失敗しました: {e}") from e

    _save_trains(revision_date, trains)
    logger.info("データを更新しました: %d本の列車", len(trains))

    return {"ok": True, "revision_date": revision_date, "train_count": len(trains)}


@app.get("/api/route")
def get_route(
    target: str = Query(..., description="降車駅（渡辺橋 または 淀屋橋）"),
    deadline: str = Query(..., description="到着期限 HH:MM"),
):
    if target not in DESTINATIONS:
        raise HTTPException(status_code=400, detail=f"未対応の降車駅です（対応: {list(DESTINATIONS)}）")

    loaded = _load_trains()
    if loaded is None:
        raise HTTPException(status_code=404, detail="時刻表データがまだアップロードされていません")
    revision_date, trains = loaded

    baseline = find_baseline(trains, target=target, deadline=deadline)
    if baseline is None:
        return {
            "revision_date": revision_date,
            "target_options": list(DESTINATIONS.keys()),
            "error": f"{deadline} までに {target} へ到着できる列車が見つかりません",
        }

    depart, root_train, t_final, arrive, board_station = baseline
    feeders = find_feeders(trains, t_final, target)

    return {
        "revision_date": revision_date,
        "target_options": list(DESTINATIONS.keys()),
        "baseline": {
            "depart": depart,
            "arrive": arrive,
            "board_type": root_train.type,
            "final_train": train_to_dict(t_final, target, from_station=board_station),
        },
        "feeders": feeders,
    }


# 静的ファイル（フロントエンド）の配信
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    # PyCharmの「実行」ボタンや `python app/main.py` で直接起動できるようにするための
    # エントリーポイント。通常は `uvicorn app.main:app --reload` を使う方がホットリロードが
    # 効いて開発しやすいが、IDEから素直に実行したい場合はこちらが使える。
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=5011, reload=True)
