# 鍵の一覧表（設計書16-3）

**鍵そのもの（値）はここに書かない。** 名前・置き場所・作った日・次に取り替える日だけを記録する。
週次の振り返り（毎週日曜 20:10、engine/weekly.py）が「次に取り替える日」を読み、30日以内のものを知らせる。
取り替えたら、この表の「作った日」と「次に取り替える日」を直し、handoff.md に1行記録する（取り替えの作業は🗄️データ基盤チャット）。

| 鍵の名前 | 置き場所 | 作った日 | 次に取り替える日 | メモ |
| --- | --- | --- | --- | --- |
| JP_RELAY_TOKEN（中継係の合言葉） | GitHubのSecrets（JP_RELAY_TOKEN）と Google Cloud Run（jp-relay、asia-northeast1） | 2026-11-07 | 2027-02-05 | 90日ごとに取り替える。Cloud Shell で cloud/jp_relay/setup.sh を実行すると作り直され、GitHubのSecretsの更新も必要（handoff.md 2026-11-07 19:30） |
| FRED_API_KEY（FREDのAPIキー） | GitHubのSecrets（FRED_API_KEY） | 2026-11-07 | 必要なとき | 定期の取り替えはしない。漏れた疑いがあれば FRED で再発行して Secrets を更新 |
| GitHubとの連携の許可（Claude → リポジトリ） | claude.ai の GitHub 連携（リポジトリ naoki6154-jpg/xrp-forecast への書き込み） | 2026-11-07 | 期限なし（毎週確認） | 期限は表示されないため、定時更新・週次の振り返りで GitHub への保存が成功しているかで確かめる。失敗したら🗄️データ基盤チャットでつなぎ直す |
| Google Cloud の費用を読む係（gcp-cost-reader）と GitHub との連携（Workload Identity：プール github／プロバイダ github-oidc） | Google Cloud（プロジェクト xrp-forecast）。GitHub側は .github/workflows/gcp-cost.yml に名前だけ（秘密ではない） | 2026-11-08（設定は cloud/gcp_cost/setup.sh） | 取り替え不要（鍵ファイルを使わない方式） | 権限は「請求データのデータセット billing_export を読む」と「読み取りの実行」だけ。連携できるのは naoki6154-jpg/xrp-forecast の GitHub Actions だけ。不要になったら Cloud Shell で係を削除すれば止まる |

参考（鍵ではないが Secrets にある設定値）：JP_RELAY_URL（中継係のURL）。


## 鍵を取り替えたときの手順（🗄️ データ基盤チャットが行う）

1. 取り替える（例：中継係の合言葉は、Cloud Shell で `cd ~/xrp-forecast && git pull -q && bash cloud/jp_relay/setup.sh` を実行し、表示された新しい値を GitHub の Secrets に登録し直す）。
2. GitHub Actions を手動で1回動かし、新しい鍵で取れることを確かめる。
3. **この表の「作った日」を取り替えた日に、「次に取り替える日」を90日後（またはその鍵の決まり）に直す。**
4. handoff.md に「何を・いつ取り替えたか」を1行記録する（鍵そのものは書かない）。

利用者が自分で鍵を取り替えた場合は、データ基盤チャットに「〇〇の鍵を取り替えた」と伝えれば、2〜4を行う。
