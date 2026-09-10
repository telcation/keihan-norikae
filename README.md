# 京阪 乗換案内（香里園 → 渡辺橋 / 淀屋橋）

京阪電気鉄道の公式PDF時刻表をアップロードすると、香里園を起点に、指定した期限内に
目的駅へ到着できる「基準ルート」と、そこに乗り換え可能な全列車を自動的に洗い出して
表示するWebアプリです。

- バックエンド: FastAPI（PDF解析・経路探索）
- フロントエンド: 素のHTML/JS（`static/index.html`、ビルド不要）
- データの持ち方: サーバー側で `data/trains.json` に全列車の時刻データを保存
  （PDFをアップロードするたびに更新。渡辺橋・淀屋橋どちらの計算にも同じデータを使う）

## この仕様に至る経緯

もともとは「期限内に到着できる経路を全パターン列挙する」実装だったが、以下の方針に変更した。

- 基準ルート（期限内で最も遅く出発できる経路）を検索し、実際に目的駅まで運んでくれる
  「到着列車」を1本特定する
- その到着列車の、香里園以降の停車駅・時刻を全て表示する
- その到着列車に乗り換え可能な列車（香里園発の全列車が対象）も、香里園以降の
  停車駅・時刻を全て表示する
  - 普通→普通の乗換は時間短縮にならないため除外する
  - 乗換に必要な時間の制限は設けない（同一時刻での乗換も可とする）
  - 乗換駅（表示駅）は、発着駅（香里園・目的駅）以外は寝屋川市・萱島・守口市・京橋の
    4駅に限定する
- 「座れる可能性」の判断はしない（人間が経験則で判断する）。あくまで機械的に
  接続可能な全列車を洗い出すことが目的

## できること

1. `/` を開くと、現在サーバーに保存されている時刻表データをもとに、香里園から
   指定した降車駅（渡辺橋 or 淀屋橋）まで、到着期限内に着ける基準ルートと、
   それに乗換可能な全列車の時刻表を一覧表示する
2. 設定画面からPDFをアップロードすると、サーバー側で自動的に再解析され、
   渡辺橋・淀屋橋どちらの計算にも使えるデータが更新される（手作業でのデータ編集機能はない）

## ローカルでの動かし方

**Python 3.12 を推奨**。特にWindowsで最新のプレビュー版Python（3.14など）を使うと、`pydantic-core` 等の
コンパイル済みパッケージのwheelがまだ提供されておらず、ソースビルド（要Rust環境）で
失敗することがある。`py -3.12 -m venv .venv` のように明示的にバージョンを指定すること。

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windowsは .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 5011
```

起動方法は以下のどれでもよい（すべて同じ挙動になるようにしてある）。
- `uvicorn app.main:app --reload --port 5011`（推奨。ファイル変更時に自動リロードされる）
- `python -m app.main`
- `python app/main.py`（PyCharmで `main.py` を右クリック→実行、でもこの方法で起動する）

`http://127.0.0.1:5011/` を開く。初回はデータが空の状態なので、設定画面からPDFを
アップロードしてください。

### テスト

```bash
pytest
ruff check app tests
```

## PDFの入手元

京阪電気鉄道の公式サイトから、「京阪本線・中之島線・鴨東線 出町柳→淀屋橋・中之島方面行き
（下り）」の全列車時刻表PDFをダウンロードする。
https://www.keihan.co.jp/traffic/time-fare/time.html

※ 対応しているのはこのPDFのフォーマット（列車ごとに列、駅ごとに行の座標が揃った表）のみ。
　他社・他形式のPDFは非対応。

## アーキテクチャ

```
app/
  main.py          FastAPIアプリ本体（/api/upload-pdf, /api/route, /api/health）
  pdf_extract.py    PDFの座標解析（pdfplumberでヘッダー行の座標を基準に列を復元する）
  route_search.py   香里園からの経路探索（基準ルート特定＋接続可能列車の全列挙）
  config.py         設定値
static/
  index.html        フロントエンド（/api/route を fetch してレンダリング）
data/
  trains.json       PDF解析結果（全列車の時刻データ）の永続化先。gitには含めない
```

### API

- `POST /api/upload-pdf` — PDFをアップロードして解析し、`data/trains.json` を更新する
- `GET /api/route?target=渡辺橋&deadline=09:30` — 基準ルートと接続可能な全列車を計算して返す
- `GET /api/health` — ヘルスチェック

新しい降車駅を追加したい場合は `route_search.py` の `DESTINATIONS` に1行追加するだけでよい
（探索アルゴリズム自体は駅非依存）。

## デプロイ（VPS + systemd + GitHub Actions）

Dockerは使わず、VPS上に直接Python仮想環境を作り、systemdのユーザーサービスとして常駐させる構成。
CIからは `rsync` でファイルを転送し、SSH経由で依存関係の再インストールとサービス再起動を行う。

### 1. GitHub側の準備

リポジトリに以下のSecretsを設定する（Settings → Secrets and variables → Actions）。

| Secret名 | 内容 |
|---|---|
| `VPS_HOST` | VPSのIPアドレスまたはホスト名 |
| `VPS_USER` | SSHログインユーザー名 |
| `VPS_SSH_KEY` | デプロイ用のSSH秘密鍵（後述の手順で作成したもの） |
| `VPS_APP_DIR` | VPS上でこのリポジトリを配置するディレクトリの絶対パス（例: `/home/USER/norikae-app`） |

### 2. VPS側の準備（初回のみ）

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv rsync

# デプロイ専用のSSH鍵を作成し、公開鍵をVPSに登録
ssh-keygen -t ed25519 -f deploy_key -N ""
cat deploy_key.pub >> ~/.ssh/authorized_keys
# deploy_key の中身（秘密鍵）を GitHub Secrets の VPS_SSH_KEY に登録する

mkdir -p ~/norikae-app
cd ~/norikae-app
# ここにこのリポジトリの内容一式をコピー（git clone でも scp でもよい）

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

mkdir -p ~/.config/systemd/user
cp deploy/norikae-app.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now norikae-app

sudo loginctl enable-linger $USER   # ログアウト後もサービスを動かし続けるために必要（sudoが要るのはここだけ）

curl http://127.0.0.1:5011/api/health
```

nginxで `/norikae` サブパス配下に公開する場合は `deploy/nginx.conf.example` を参考に設定し、
`certbot --nginx -d your-domain` などでSSL証明書を取得する。

**サブパス公開時の注意**：フロントエンド（`static/index.html`）はAPIを相対パス（`api/route` 等）で
呼び出す実装にしてあるため、ルート直下ではなく `/norikae/` のようなパス配下に置いても動く。
ただし末尾スラッシュ（`/norikae/`）でアクセスされることが前提。

### 3. 以降のデプロイ

`main` ブランチにpushすると、GitHub Actionsが自動的に

1. `pytest` / `ruff` を実行
2. `rsync` でプロジェクト一式をVPSへ転送（`.venv` や `data/trains.json` は除外）
3. SSHでVPSに接続し、`pip install -r requirements.txt` で依存関係を更新してから
   `systemctl --user restart norikae-app` でサービスを再起動

を行う（`.github/workflows/deploy.yml`）。sudoは初回のlingering設定以外では一切使わない。

### トラブルシューティング

```bash
systemctl --user status norikae-app
journalctl --user -u norikae-app -f
systemctl --user restart norikae-app
```
