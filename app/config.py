"""アプリ全体の設定値。環境変数で上書き可能にしている。"""
import os

APP_TITLE = "京阪 乗換案内"
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", 20 * 1024 * 1024))  # 20MB
