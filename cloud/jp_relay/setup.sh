#!/bin/bash
# Google Cloud Shell で1回だけ実行する設定スクリプト（東京の中継係を作る）
# 使い方： bash xrp-forecast/cloud/jp_relay/setup.sh
set -euo pipefail
PROJECT=xrp-forecast
REGION=asia-northeast1   # 東京
SERVICE=jp-relay
cd "$(dirname "$0")"

echo "=== 1/4 プロジェクトを選択 ==="
gcloud config set project "$PROJECT" --quiet

echo "=== 2/4 必要な機能を有効化（1〜2分） ==="
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --quiet

echo "=== 3/4 中継係を東京に配置（3〜5分） ==="
TOKEN=$(openssl rand -hex 24)
gcloud run deploy "$SERVICE" --source . --region "$REGION" \
  --allow-unauthenticated --set-env-vars "RELAY_TOKEN=$TOKEN" \
  --memory 256Mi --cpu 1 --min-instances 0 --max-instances 1 --timeout 60 --quiet
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')

echo "=== 4/4 動作確認 ==="
curl -s "$URL/bundle?symbols=XRP" -H "X-Relay-Token: $TOKEN" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for it in d["items"]:
    print(("OK  " if it.get("status")==200 else "NG  "), it["name"], it.get("status"), it.get("error","")[:80])
'
echo
echo "================ GitHubに登録する2つの値 ================"
echo "JP_RELAY_URL   = $URL"
echo "JP_RELAY_TOKEN = $TOKEN"
echo "========================================================="
echo "（この2つはチャットに貼らず、GitHubのSecretsに直接登録してください）"
