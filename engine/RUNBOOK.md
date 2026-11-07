# 定時更新の手順書（段階1・試運転中は変えない）

朝（07:40）と夜（21:20）の定時実行が、この手順を上から順に実行する。チャットで「定時更新」と言われたときも同じ。
**試運転（2026-11-08〜11-14の7日間）の間は、この手順と engine/ の計算方法を変えない。** 変えたら7日を数え直す（設計書13章）。

- リポジトリ：`naoki6154-jpg/xrp-forecast`（ブランチ main）
- ダッシュボード：https://claude.ai/artifact/Gx6ZDpofZMFHSSC1zMLzi2 （slug `812a6935-60db-4520-a941-033b834823a7`）
- 予想の数字は全て `engine/run.py` が出す。AIは数字を作らず、表示された JSON をそのまま書き写すだけ。
- 書き込みの決まり：定時実行が変更してよいのは forecasts/ と、handoff.md の末尾への追記（朝の手順7）だけ。engine/ を含め、他は変更しない。push の前に必ず pull。ぶつかったら相手の変更を消さずに止めて報告する。
- 投資助言ではない。注文は出さない。

## 手順

1. **リポジトリを用意する**
   ```
   cd ~ && (test -d xrp-forecast/.git || git clone --depth 1 https://github.com/naoki6154-jpg/xrp-forecast xrp-forecast)
   cd xrp-forecast && git pull -q --rebase
   cat handoff.md | tail -20 ; head -c 600 state/latest.json
   ```
   リポジトリがこのセッションにない場合は add_repo（access: push）で追加してから clone する。
2. **実行の名前を決める**：`MODE` は朝なら `morning`、夜なら `night`、それ以外は `manual`。
   ```
   RID=$(TZ=Asia/Tokyo date +%Y%m%d-%H%M)-$MODE ; echo $RID
   ```
3. **メインの開始を記録**：`python3 engine/dash_ops.py runlog --agent main --phase start --run-id $RID --mode $MODE`
   の出力（JSONの配列）を、そのまま db_write の ops に渡す（slug は上記）。
4. **①数値データ担当の点検**：`python3 engine/dash_ops.py numeric-check --run-id $RID --mode $MODE` の出力を db_write。
   （取得そのものは GitHub Actions と東京の Cloud Run が行う。ここでは保存済みデータの成否と新しさを数える）
5. **予想エンジンを実行**：
   ```
   python3 engine/run.py --mode $MODE --run-id $RID
   ```
   失敗したら、エラーの最後の数行をメモして手順9へ（状態は「失敗」）。
6. **ダッシュボードに予想を書く**：`python3 engine/dash_ops.py update` の出力を、そのまま db_write の ops に渡す。
   1文字も変えない（数字を丸めたり要約したりしない）。書けなかったら1回だけやり直し、だめなら「一部失敗」。
7. **（朝だけ）画面の変更履歴を handoff.md に書き写す**：利用者がダッシュボードの「編集」で変えた内容は、ダッシュボードの履歴にしか残らないため。
   - db_query で collection `changelog` を `orderBy: -at`、`limit: 50` で読む。
   - `by` が「利用者」で始まるものだけを選ぶ（チャットが書いた分は、そのチャットが handoff.md に書いている）。
   - そのうち、handoff.md にまだ `changelog/<文書のid>` の文字列が無いものを、古い順に1件1行で、handoff.md の**末尾に追記**する（書き換えない）。
     形式：`- YYYY-MM-DD HH:MM JST［利用者（ダッシュボードの編集）］<what の内容>（changelog/<文書のid>。朝の定時更新が書き写し）`
   - 該当が無ければ何もしない。夜の更新では行わない。
8. **記録をGitHubに保存**（forecasts/ と、朝に書き写した場合の handoff.md だけ）：
   ```
   git add forecasts/ handoff.md && git commit -q -m "forecast: $RID" && git pull -q --rebase && git push -q
   ```
   pull でぶつかったら、`git rebase --abort` で止め、相手の変更を消さずに報告する（push しない）。
9. **メインの終了を記録**：
   ```
   python3 engine/dash_ops.py runlog --agent main --phase end --run-id $RID --mode $MODE --status 成功 --duration <秒> --ok-items <書けた期間の数> [--failed 項目,項目] [--note "..."]
   ```
   状態は 成功／一部失敗／失敗 のどれか。出力を db_write。
10. **報告**：`engine/out/summary.txt` の内容を、そのまま報告する。異常（失敗、代わりの取得先、モデル間の差が大きい、データが古い）があれば先頭に書く。
   朝の実行で異常があったとき、または前回より「1週間後に上がる確率」が10ポイント以上動いたときは、プッシュ通知を送る。

## 夜の軽い更新

手順は同じ。②情報・イベント担当と③検証担当は段階1では動かさない（ダッシュボードでは「待機」）。
夜は①の点検と予想の再計算だけを行い、手順7（画面の変更履歴の書き写し）は行わない。

## よくある失敗

| 症状 | 対応 |
| --- | --- |
| db_write が拒否された | 1回だけやり直す。だめなら状態「一部失敗」で終了し、報告に書く |
| state/latest.json が古い（取得が止まっている） | 予想は出す（古いことはダッシュボードに出る）。報告の先頭に書き、データ基盤チャットに直してもらう |
| git push がぶつかった | rebase を中止して報告。forecasts/ の記録はダッシュボードには書けているので、次回まとめて保存してよい |
