# 京阪 乗換案内（香里園 → 渡辺橋 / 淀屋橋）

京阪電気鉄道の公式PDF時刻表をアップロードすると、香里園発の全経路（乗換0〜2回まで）を
自動的に解析・計算して表示するWebアプリです。

- バックエンド: FastAPI（PDF解析・経路探索）
- フロントエンド: 素のHTML/JS（`static/index.html`、ビルド不要）
- データの持ち方: サーバー側で `data/datasets.json` に生成結果を保存（PDFをアップロードするたびに更新）

## できること

1. `/` を開くと、現在サーバーに保存されている時刻表データをもとに、香里園から
   指定した降車駅（渡辺橋 or 淀屋橋）まで、到着期限内に着けるすべての経路を一覧表示する
2. 設定画面からPDFをアップロードすると、サーバー側で自動的に再解析され、
   渡辺橋・淀屋橋の両方のデータが同時に更新される（手作業でのデータ編集機能はない）

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
  main.py          FastAPIアプリ本体（/api/upload-pdf, /api/datasets, /api/health）
  pdf_extract.py    PDFの座標解析（pdfplumberでヘッダー行の座標を基準に列を復元する）
  route_search.py   香里園からの多段乗換探索（乗換0〜2回、天満橋は対象外 等のルールを実装）
  config.py         設定値
static/
  index.html        フロントエンド（/api/datasets を fetch してレンダリング）
data/
  datasets.json     生成結果の永続化先（gitには含めない。VPS上のファイルシステムに直接保存する）
```

### 経路探索のルール（route_search.py に実装済み）

- 対象乗換駅：香里園〜京橋間の14駅（天満橋は対象外）
- 乗換に必要な時間：1分以上。ただし萱島・守口市は同一時刻（0分）での乗換も可
- 各乗換駅では「その先へ進める最初の列車」を接続先とする
  （香里園に停まらない種別＝行き止まりの列車に探索を止められないよう除外する）
- 普通→普通の乗換は時間短縮にならないため除外
- 「座れる可能性」の判断はしない。あくまで期限内に到着できる経路を機械的に列挙するのみ
  （どれを選ぶかは利用者の経験則による判断に委ねる）

新しい降車駅を追加したい場合は `route_search.py` の `DESTINATIONS` / `DEST_LABEL` に
1行追加するだけでよい（探索アルゴリズム自体は駅非依存）。

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
| `VPS_APP_DIR` | VPS上でこのリポジトリを配置するディレクトリの絶対パス（例: `/home/USER/norikae-app`。ユーザーのホーム配下を推奨、理由は後述） |

### 2. VPS側の準備（初回のみ）

```bash
# Python 3.12 と rsync を用意（Ubuntu想定。ディストリのバージョンによりパッケージ名は読み替え）
sudo apt update
sudo apt install -y python3.12 python3.12-venv rsync

# デプロイ専用のSSH鍵を作成し、公開鍵をVPSに登録
ssh-keygen -t ed25519 -f deploy_key -N ""
cat deploy_key.pub >> ~/.ssh/authorized_keys
# deploy_key の中身（秘密鍵）を GitHub Secrets の VPS_SSH_KEY に登録する

# アプリ用ディレクトリを作成し、リポジトリの内容を一度手動で配置する
# （以降はCIのrsyncが差分更新してくれるので、初回だけでよい）
mkdir -p ~/norikae-app
cd ~/norikae-app
# ここにこのリポジトリの内容一式をコピー（git clone でも scp でもよい）

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# systemdユーザーサービスとして登録
mkdir -p ~/.config/systemd/user
cp deploy/norikae-app.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now norikae-app

# ログアウト後もサービスを動かし続けるために必要（sudoが要るのはここだけ）
sudo loginctl enable-linger $USER

# 動作確認
curl http://127.0.0.1:5011/api/health
```

nginxでHTTPS終端する場合は `deploy/nginx.conf.example` を参考に設定し、
`certbot --nginx -d your-domain` などでSSL証明書を取得する。

**サブパス（例: `telcation.com/norikae`）で公開する場合の注意**：
フロントエンド（`static/index.html`）はAPIを相対パス（`api/datasets` 等）で呼び出す
実装にしてあるため、ルート直下ではなく `/norikae/` のようなパス配下に置いても動く。
ただし末尾スラッシュ（`/norikae/`）でアクセスされることが前提なので、
`deploy/nginx.conf.example` では `/norikae`（スラッシュなし）へのアクセスを
`/norikae/` へ301リダイレクトするようにしている。

### 3. 以降のデプロイ

`main` ブランチにpushすると、GitHub Actionsが自動的に

1. `pytest` / `ruff` を実行
2. `rsync` でプロジェクト一式をVPSへ転送（`.venv` や `data/datasets.json` は除外し、既存の仮想環境とアップロード済みデータには触れない）
3. SSHでVPSに接続し、`pip install -r requirements.txt` で依存関係を更新してから
   `systemctl --user restart norikae-app` でサービスを再起動

を行う（`.github/workflows/deploy.yml`）。**sudoは初回のlingering設定以外では一切使わない**
（ユーザーレベルのsystemdサービスとして動かしているため）。

### データの永続化について

`data/datasets.json` はrsyncの除外リストに入れているため、デプロイのたびに転送されて
上書きされることはない。VPS上のファイルシステムに直接置かれ続けるので、
コンテナのような揮発性の心配はそもそもない。

### トラブルシューティング

```bash
# サービスの状態・ログを見る
systemctl --user status norikae-app
journalctl --user -u norikae-app -f

# 手動で再起動
systemctl --user restart norikae-app
```
