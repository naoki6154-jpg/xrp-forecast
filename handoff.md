# 引き継ぎメモ（handoff）

各チャットは作業の始めにこのファイルと `state/latest.json` を読み、終わりに変更点を1行ずつ追記する。
形式：`- YYYY-MM-DD HH:MM JST［チャット名］変更内容`

## 記録
- 2026-11-07 18:10 JST［🗄️ データ基盤］リポジトリ作成。段階0の構築を開始（フォルダー構成と引き継ぎメモを作成）。
- 2026-11-07 18:20 JST［🗄️ データ基盤］取得プログラム（fetchers/）と定時実行（.github/workflows/fetch-daily.yml、毎朝07:15・毎夜19:15 JST）を追加。初回実行で7取得先すべて成功：bitbank XRP/円 日足 2018-01-01〜（3,233行、欠けなし）、Binance公式一括データ XRP・BTC・ETH・XLM/USDT 日足 2018年〜、Kraken ドル建て日足 直近721日、恐怖強欲指数 2018年〜、OKX 資金調達率（直近3ヶ月、以後毎日積み上げ）・建玉・ロングショート比、FRED 15系列＋初回発表値6系列＋発表日（CPI・雇用統計・PCE）、Polymarket。FRED_API_KEY は Secrets に登録済み。
- 未着手：Binance・Bybitの最新値（日本のクラウド、方法C）、過去事実データベース（facts/：FOMC日程、事件、CPI・雇用統計の市場予想、出来事ごとのその後の値動き）。
- 2026-11-07 19:30 JST［🗄️ データ基盤］日本からの取得（設計書3-4 方法C）が稼働。構成は設計書から少し変更：Cloud Run（asia-northeast1、サービス名 jp-relay、プロジェクト xrp-forecast）は「中継係」で、Binance・Bybitの決められた公開エンドポイント12種を取って生のまま返すだけ。保存はGitHub Actions（fetch-hourly.yml、毎時5分）が行う（GCPにGitHubの書き込み権限を渡さない、Cloud Scheduler不要）。合言葉は Secrets の JP_RELAY_URL / JP_RELAY_TOKEN。保存先 data/raw/jp_relay/。初回で全12項目×XRP・BTC取得成功。再配置は Cloud Shell で `cd ~/xrp-forecast && git pull -q && bash cloud/jp_relay/setup.sh`（合言葉が作り直されるので Secrets の更新が必要）。GCPの予算アラート：月100円の予算、1%と100%（実績）でメール、プロモーションのクレジットは除外。
- 次：過去事実データベース（facts/）。翌日にGCPの請求レポートで無料枠内か確認する。
- 2026-11-07 20:05 JST［🧭 司令塔］段階1の予想エンジン（engine/：モデルA イベント型モンテカルロ＋モデルC 変動率、乱数の種固定、100万本）とダッシュボード（https://claude.ai/artifact/Gx6ZDpofZMFHSSC1zMLzi2 、内蔵データベース付き）を作成。初回の予想を forecasts/2026-11-07_1955_manual.json とダッシュボードに記録。12月FOMCの利上げ確率は Polymarket の年末の政策金利の上限から計算（74%）、他のイベント確率は既存試算を引き継いだ「判断」。オプションの予想変動率は未取得のため実現変動率で代用。
- 2026-11-07 20:05 JST［🧭 司令塔］ダッシュボードへの書き方（runs／updates／config/layout／layout_history／changelog／data/users/<id>/position）は engine/README.md、定時更新の手順は engine/RUNBOOK.md。予想の記録は forecasts/ に追加し、state/latest.json は書き換えない（データ基盤の担当のまま）。データ基盤へのお願い：bitbank の円価格を毎時の取得にも入れてもらえると、夜の更新で円の「代わりの取得先」を使わずに済む。
