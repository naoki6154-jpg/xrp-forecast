# xrp-forecast

XRP（リップル）の価格予想システムのデータ置き場と予想エンジン。設計書はClaudeのプロジェクト「📈XRP予想」にある。
投資助言ではない。売買の判断は利用者が行い、このシステムは注文を出さない。

## 構成
```
.github/workflows/  取得の定時実行（fetch-daily：毎朝07:15・毎夜19:15 JST、fetch-hourly：毎時5分）
cloud/jp_relay/     東京（Cloud Run）に置く中継係。設定は Cloud Shell で setup.sh を実行
fetchers/           取得先ごとの取得プログラム（標準ライブラリだけで動く）
data/raw/           取得したままのデータ（全行に取得元・取得日時つき）
data/clean/         確認済みのデータ
facts/              過去事実データベース（出来事とその後の値動き）
engine/             予想エンジン
forecasts/          予想の記録（日付ごと、採点用）
state/latest.json   最新の状態（各取得先の成否・行数・最新日付）
handoff.md          チャット間の引き継ぎメモ
```

## 取得先（段階0）
| 名前 | 中身 | ファイル |
| --- | --- | --- |
| bitbank | XRP/円 日足（2018年〜） | data/raw/prices/bitbank_xrp_jpy_1d.csv |
| kraken | XRP・BTC・ETH・XLM/ドル 日足（直近約720日） | data/raw/prices/kraken_*_usd_1d.csv |
| binance_vision | XRP・BTC・ETH・XLM/USDT 日足（2018年〜、公式一括データ） | data/raw/prices/binance_*_usdt_1d.csv |
| fear_greed | 恐怖強欲指数（2018年2月〜） | data/raw/sentiment/fear_greed.csv |
| okx | 資金調達率・建玉・ロングショート比（XRP・BTC） | data/raw/derivatives/okx_*.csv |
| fred | CPI・PCE・雇用・金利・利回り・ドル・ドル円・原油・ナスダック、初回発表値、発表日 | data/raw/fred/*.csv |
| jp_relay | Binance・Bybit 先物の建玉・ロングショート比・テイカー比・資金調達率・現在値（XRP・BTC、毎時。東京のCloud Run経由） | data/raw/jp_relay/*.csv |
| polymarket | 予測市場の確率（毎日の記録） | data/raw/prediction_markets/polymarket_daily.csv |

`partial=1` の行は、取得時点でその日の足がまだ確定していないことを示す（次の取得で上書きされる）。
