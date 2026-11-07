# 予想エンジン（段階1）

設計書 第5〜7章・第11章の最小版。🧭 司令塔チャットの担当。

| ファイル | 中身 |
| --- | --- |
| `run.py` | 実行の入口。入力→モデルA・C→混合→記録とダッシュボード用データ |
| `inputs.py` | `data/raw/` と `state/latest.json` を読むだけ（書かない） |
| `models.py` | モデルA（イベント型モンテカルロ）、モデルC（変動率。オプションの代用） |
| `events.json` | イベント登録表（第7章）。モデルAはこれをそのまま読む |
| `config.json` | 乱数の種、道筋の数、期間、重み、変動率の求め方 |
| `dash_ops.py` | ダッシュボードに書く内容（稼働記録・予想）を JSON で出す |
| `RUNBOOK.md` | 定時更新の手順（定時実行が毎回読む） |
| `weekly.py`・`WEEKLY.md` | 週1回の振り返り（設計書16-2、毎週日曜 20:10、③検証）。毎日の定時更新とは別の仕組み |

## 計算の中身

- **同じ入力なら毎回同じ結果**：乱数の種は `config.json` の `seed`（20261107）で固定。記録には入力の指紋（`input_hash`）を残す。
- **現在値**：ドルは Binance 現物（東京の Cloud Run 経由・毎時）→ Bybit → Kraken の順。円は bitbank（1日2回）で、90分より古いときは「ドル価格×ドル円」で代わりに出し、ダッシュボード上部に「代わりの取得先」として出す。
- **変動率**：オプションの予想変動率（Deribit）が未取得のため、日足の実現変動率（EWMA と90日の平均）で代用。
- **モデルA**：`events.json` の各イベントの結果を確率で振り、影響（跳ね・じわじわ・荒れ・ドル円）を道筋に足す。12月FOMCの利上げ確率だけは Polymarket の「年末の政策金利の上限」と FRED の現在の上限から計算（市場）。他は既存の100万回試算（mc1m.py）の設定を引き継いだ「判断」で、理由は各行に記録。毎日の基本の変動は、イベントの分を差し引いて合計が実現変動率に合うように決める。裾の厚い t 分布（自由度4）、週末は変動を小さく。
- **モデルC**：方向の偏りなし（期待値ゼロ）、実現変動率、t 分布。
- **混合**：A 50%・C 50%で道筋を合わせる（合計100万本）。モデルAのイベント確率は12月FOMC以外が「判断」なので、検証（段階3）で成績が出るまでは判断の影響を抑える。設計書第6章の初期の重み（A 35%・C 20%）に戻すかは段階3で決める。
- **出力**：1日・1週間・1ヶ月・年末の、上がる確率（円・ドル）、中央値、50%・80%の範囲、信頼度。確率の幅は、モデルの答えの最小〜最大（最低±3ポイント）。
- **信頼度**：段階1は全て「低」（採点0件、判断の確率が多い）。モデルCの範囲の過去730日の的中率を参考に表示。
- 段階1で使っていないもの：モデルB（類似局面）・D（季節性）・E（機械学習）、過去事実データベース（facts/）。段階2で、別の仕組みとして足す（試運転中は混ぜない）。

## ダッシュボードのデータの書き方（どの表に何を書くか）

ダッシュボードは内蔵のデータベースを読むだけ。書き込みは `db_write`（slug `812a6935-60db-4520-a941-033b834823a7`）。

| 場所 | 書く人 | 中身 |
| --- | --- | --- |
| `runs/<run_id>_<agent>_<phase>` | 各エージェント | 稼働記録。動き始め（phase=start）と終わり（end）に1件ずつ。`agent`=main／numeric／info／verify、`status`=実行中／成功／一部失敗／失敗、`at`・`at_ms`、`duration_sec`、`ok_items`、`failed_items`、`next_at`、`note`。`dash_ops.py runlog` で作る |
| `updates/<run_id>` | メイン | 1回の更新の全データ（今の状況・予想・デリバティブ・経済・イベント・検証・取得の状況・データの新しさ・代わりの取得先）。`dash_ops.py update` で作る。上書きせず毎回追加 |
| `config/layout` | 利用者（編集ボタン）・チャット | 画面設定。欄の種類・順番・表示/非表示・開閉・中の項目（params）・単位・警告の値 |
| `layout_history/<YYYYMMDDHHMMSS>` | 画面設定を変える人 | 変更前の画面設定（`layout`）。「前の形に戻す」はいちばん新しい1件を戻して消す |
| `changelog/<YYYYMMDDHHMMSS>` | 画面設定を変える人 | 変更の履歴（`at`・`by`・`what`）。画面の一番下に出る |
| `weekly/<YYYY>-W<ww>` | ③検証（週次の振り返り） | 週ごとの振り返り（問題と頼み先・採点・稼働・データの欠け・費用・予想の変化・来週の予定・改善の候補・鍵の期限）。`engine/out/weekly_ops.json` をそのまま書く |
| `data/users/<利用者のid>/position` | 💼 保有ポジション チャット | 保有枚数 `qty`・取得額 `cost_jpy`・戦略の比較 `strategies`。本人だけが読める |

**画面設定の欄の種類**（`sections[].type`）：summary（サマリー：価格・全体の点検・4期間の上がる確率）・pipeline（エージェントの動き：取得→①→②→予想エンジン→③→メイン の6段の流れ、次の定時更新、試運転14回の成否。params に schedule・since・grace_min・trial_start・trial_days）・weekly（週次の振り返り：最新の週。params に stale_days）・status・forecast・agents・fetchers・freshness・derivatives・macro・events・news・verification・position と、汎用の number（数字）・table（表）・line（折れ線）・bar（棒グラフ）・list（一覧）。
汎用の欄は `params.path` で最新の更新（`updates`）の中の場所を指す（例 `derivatives.funding`、`history.forecast.vol_annual` で更新ごとの推移）。`fields`（列）、`fmt`（pct・yen・usd・money）、`warn`（`{key, op, value}` で赤くする）、`label`。

**チャットで画面設定を変えるとき**：`config/layout` を db_get → その内容を `layout_history/<日時>` に set → `config/layout` を `version`+1 で書く（ifVersion 付き）→ `changelog/<日時>` を追加 → handoff.md に1行追記。

鍵の一覧表は `docs/keys.md`（鍵そのものは書かない）。止まったときの対応は `engine/RUNBOOK.md` の最後の節。
