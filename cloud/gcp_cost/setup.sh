#!/bin/bash
# Google Cloud の費用を GitHub Actions から「読むだけ」で取れるようにする設定（Cloud Shell で1回だけ実行）
# 使い方： cd ~/xrp-forecast && git pull -q && bash cloud/gcp_cost/setup.sh
#
# 作るもの：
#   1) BigQuery のデータセット billing_export（請求データの書き出し先。東京）
#   2) 読むだけの係 gcp-cost-reader（このデータセットを読む権限と、読み取りの実行権限だけ）
#   3) GitHub との連携（Workload Identity）。naoki6154-jpg/xrp-forecast の GitHub Actions だけが係になれる
# 鍵ファイルは作らない（取り替えが要らず、漏れる心配がない）。
set -euo pipefail
PROJECT=xrp-forecast
REPO=naoki6154-jpg/xrp-forecast
SA=gcp-cost-reader
SA_EMAIL="${SA}@${PROJECT}.iam.gserviceaccount.com"
POOL=github
PROVIDER=github-oidc
DATASET=billing_export

echo "=== 1/5 プロジェクトを選択し、必要な機能を有効化（1〜2分） ==="
gcloud config set project "$PROJECT" --quiet
gcloud services enable bigquery.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com --quiet
PN=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')

echo "=== 2/5 BigQuery のデータセット（請求データの書き出し先、東京） ==="
bq --location=asia-northeast1 mk --dataset --description "Cloud Billing export (standard usage cost)" "${PROJECT}:${DATASET}" 2>/dev/null \
  || echo "（すでにあります）"

echo "=== 3/5 読むだけの係 ==="
gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA" --display-name "GCP cost reader (read-only, for GitHub Actions)" --quiet
# 読み取りを実行する権限（プロジェクト）と、データセットを読む権限（このデータセットだけ）
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.jobUser" --condition=None --quiet > /dev/null
bq add-iam-policy-binding --member="serviceAccount:${SA_EMAIL}" --role="roles/bigquery.dataViewer" \
  "${PROJECT}:${DATASET}" > /dev/null

echo "=== 4/5 GitHub との連携（このリポジトリの GitHub Actions だけ） ==="
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions" --quiet
gcloud iam workload-identity-pools providers describe "$PROVIDER" --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global \
       --workload-identity-pool="$POOL" --display-name="GitHub OIDC" \
       --issuer-uri="https://token.actions.githubusercontent.com" \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
       --attribute-condition="assertion.repository=='${REPO}'" --quiet
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PN}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" \
  --quiet > /dev/null

echo "=== 5/5 確認 ==="
echo "プロジェクト番号：${PN}（GitHub側の設定は 777720829424 を前提にしています）"
[ "$PN" = "777720829424" ] && echo "OK：一致しました" || echo "注意：番号が違います。この画面の内容をチャットに貼ってください"
echo "読むだけの係：${SA_EMAIL}"
echo
echo "完了しました。次は請求データの書き出し先を、Google Cloud の画面で ${PROJECT}.${DATASET} に設定してください（チャットの案内どおり）。"
