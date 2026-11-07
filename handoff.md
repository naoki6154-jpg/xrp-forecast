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
- 2026-11-07 20:11 JST［🗄️ データ基盤］過去事実データベースの最小版が完成。読む場所は https://raw.githubusercontent.com/naoki6154-jpg/xrp-forecast/main/ + 次のパス。毎朝07:45 JSTに自動更新（facts-build）。
- 2026-11-07 20:11 JST［🗄️ データ基盤］価格：data/raw/prices/binance_xrp_usdt_1h.csv（2018-05-04〜2026-11-06、74,521行）・binance_btc_usdt_1h.csv（2018-01-01〜、77,448行）・binance_{xrp,btc}_usdt_1d.csv（日足、3,109行／3,232行）。列 time(UTC)/date, open, high, low, close, volume, source, fetched_at。円建ては bitbank_xrp_jpy_1d.csv。1時間足・日足は前日分まで（当日分は毎時の jp_relay と kraken を参照）。
- 2026-11-07 20:11 JST［🗄️ データ基盤］出来事：facts/events.csv（475件。1件1行：event_id, category, subcategory, name, datetime_utc, datetime_jst, time_basis, source, source_url）。数値：facts/event_values.csv（event_id×metric：value=発表当日の値〈ALFRED〉, previous, consensus, surprise=結果−予想, unit, method, source）。
- 2026-11-07 20:11 JST［🗄️ データ基盤］直近2年（2024-11-07以降）の件数：FOMC 17件・CPI 23件・雇用統計 23件・PCE 23件、すべて日時と結果あり。市場予想あり：CPI前年比 12/23（空欄11）、コアCPI前年比 21/23（空欄2）、雇用者数 12/23（空欄11）、FOMC 12/17（空欄5）→ 予想の空欄は合計29件。CPI前月比・コアCPI前月比・失業率・PCEの予想は未収集（全件空欄）。結果の空欄は2件（2025-12-18発表のCPI前月比・コア前月比：2025年10月分が公表されなかったため前月がない）。
- 2026-11-07 20:11 JST［🗄️ データ基盤］市場予想は二次情報（Forex Fundamentals の経済指標カレンダー、2026-11-07取得）。出典URLは facts/consensus.csv、一次データとの照合は facts/consensus_check.csv（59件中58件一致、1件は指数の丸めによる0.1ポイント差）。発表時刻はCPI・雇用統計・PCEが8:30 ET、FOMCが14:00 ETの慣例を使用（time_basis に明記）。
- 2026-11-07 20:11 JST［🗄️ データ基盤］事件：facts/events_manual.csv（手で登録する台帳、8件：SEC提訴2020-12-22、FTX破綻2022-11-11、トーレス判決2023-07-13、BTC現物ETF承認2024-01-10、控訴取り下げ2025-08-07、訴訟終了2025-08-22、XRP現物ETF上場2025-11-13、Binanceハッキング2026-10-29）。うち2件は時刻未確認で日付のみ。政治・地政学は未登録（次回）。
- 2026-11-07 20:11 JST［🗄️ データ基盤］その後の値動き：facts/reactions.csv（出来事×期間〈1時間・4時間・1日・1週間・1ヶ月〉、2,371行。XRP・BTCの騰落率、最大の上昇・下落、XRP−BTC）。空欄512行のうち505行は2018年5月より前の出来事（BinanceのXRPデータがない）、7行は期間がまだ経過していないもの。種類ごとの集計：facts/reaction_summary.csv（件数 n を必ず併記）。基準は「発表直前の正時の価格」→「h経過後の最初の正時の価格」。
- 2026-11-07 20:11 JST［🗄️ データ基盤］東京の中継係（cloud/jp_relay/main.py）を確認：合言葉（RELAY_TOKEN）が一致しない要求と、合言葉が未設定のときは403で断る。問い合わせ先はコード内の固定リスト12種（Binance fapi/api・Bybit api の公開エンドポイントのみ）と銘柄 XRP・BTC・ETH に限定し、任意のURLは取らない。取引所のAPIキーは使わない。最大インスタンス数は1。
- 2026-11-07 20:11 JST［🗄️ データ基盤］お知らせ：担当外のフォルダーに、以前の作業で入れたものがある（cloud/jp_relay/__pycache__/ のキャッシュ1件、README.md と .github/workflows/ の3つの定時実行）。今後は触らない。cloud/ の不要なキャッシュの削除と README の更新は、必要なら担当のチャットで行ってください。
- 2026-11-07 20:15 JST［🧭 司令塔］ダッシュボードの見た目を改善（画面設定 第2版）：先頭に「サマリー」（価格・全体の点検・4期間の上がる確率）と「エージェントの動き」（6段の流れ・次の定時更新・試運転14回の成否）を追加し、詳細の欄は閉じておく形に。データの書き方と定時更新の流れは変更なし（試運転に影響なし）。
