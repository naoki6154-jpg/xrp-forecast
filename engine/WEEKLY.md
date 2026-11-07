# 週1回の振り返りの手順（設計書16-2・16-3）

毎週日曜 20:10（日本時間）の定時実行「XRP予想 週次の振り返り」が、この手順を上から順に実行する。担当は③検証。
**毎日の定時更新（RUNBOOK.md・run.py）とは別の仕組み。** 毎日の予想の計算・手順・forecasts/ の記録には触らない。

- リポジトリ：`naoki6154-jpg/xrp-forecast`（ブランチ main）
- ダッシュボード：https://claude.ai/artifact/Gx6ZDpofZMFHSSC1zMLzi2 （slug `812a6935-60db-4520-a941-033b834823a7`）
- 数字は全て `engine/weekly.py` が出す。AIは数字を作らず、表示された JSON・文面をそのまま使う。
- 変更してよいのは `reports/weekly/` と、handoff.md の末尾への1行の追記だけ。
- 投資助言ではない。注文は出さない。

## 手順

1. **リポジトリを用意する**（RUNBOOK.md の手順1と同じ）。`git pull -q --rebase`。handoff.md の末尾を読む。
2. **実行の名前**：`RID=$(TZ=Asia/Tokyo date +%Y%m%d-%H%M)-weekly`
3. **③検証の開始を記録**：`python3 engine/dash_ops.py runlog --agent verify --phase start --run-id $RID --mode weekly` の出力を db_write。
4. **振り返りを作る**：`python3 engine/weekly.py`
   - reports/weekly/<年>-W<週>.md と .json ができる。
   - 費用：GitHub Actions の使用分数は gh で自動で数える。Google Cloud の金額は、読む仕組みができるまで「未取得」のまま（推測で埋めない）。
   - 鍵の期限：docs/keys.md の「次に取り替える日」から、30日以内のものを拾う。
5. **ダッシュボードに書く**：`engine/out/weekly_ops.json` の中身（JSONの配列）を、1文字も変えずに db_write の ops に渡す。
6. **GitHubに保存**：
   ```
   echo "- $(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M') JST［🔬 ③検証（週次の振り返り）］reports/weekly/ に今週の振り返りを保存。要対応 <件数>件" >> handoff.md
   git add reports/weekly/ handoff.md && git commit -q -m "weekly: $RID" && git pull -q --rebase && git push -q
   ```
   <件数> は weekly_summary.txt の1行目の「要対応 N件」（なければ0）。pull でぶつかったら rebase を中止し、相手の変更を消さずに報告する。
7. **③検証の終了を記録**：`python3 engine/dash_ops.py runlog --agent verify --phase end --run-id $RID --mode weekly --status 成功 --duration <秒> --ok-items <要対応の件数> --note "週次の振り返り <年>-W<週>"` の出力を db_write。失敗したら状態を「失敗」または「一部失敗」にする。
8. **通知**：`engine/out/weekly_summary.txt` の内容をそのまま SendUserMessage で送る。
   - 問題がなければ5行以内（weekly.py が5行に収めている）。
   - 問題があれば、1行目に「どのチャットに何と頼めばよいか」（weekly.py が RUNBOOK.md の「止まったときの対応」に沿って書いている）。

## 振り返りの中身（16-2）

| 項目 | どこから |
| --- | --- |
| 予想の採点 | forecasts/ の記録のうち期限が来たものと、Binance の1時間足の終値。方向の当たり率、50%・80%範囲に入った割合、ブライアスコア（基準0.25）。20件未満は参考値 |
| 稼働 | 朝07:40・夜21:20の枠ごとに、forecasts/ に記録があるか（11/8 朝から数える） |
| データの欠け | data/raw/_fetch_log.csv の取得先ごとの成功率、代わりの取得先を使った回数、空欄の項目 |
| 費用 | GitHub Actions の分数（gh）。Google Cloud は未取得 |
| 予想の変化 | 1週間前の予想との差と、価格・変動率・利上げ確率・重み・発表済みのイベントの変化 |
| 来週の予定 | イベント登録表と FRED の発表日のうち、7日以内のもの |
| 改善の候補 | 決まった規則で拾う（範囲の的中率、モデル間の差、判断の確率の件数、取得の成功率など）。実施は🔬検証・改良チャットで確かめてから |
| 鍵の期限 | docs/keys.md のうち、30日以内に来るもの |
