# 再現の手順（2026-11-25 🔬検証・改良）
すべて python3 -I で実行。パス（/home/claude/work と /home/claude/xrp-forecast）は作業環境のもの。
1. build.py … forecasts/ の記録と Binance 1時間足から、採点用の表（df.pkl）を作る（実際の価格は weekly.py と同じ規則：予想の目標時刻の直前に終わった1時間足の終値。無ければ東京経由の現在値）
2. score.py … 司令塔と同じ採点（ドル建て・定時のみ）
3. wf.py … engine/ のモデルB・C・D・T の関数を使い、2020-01-01〜の毎日の起点で前進型の予想を作る（その日までのデータだけ）。レジームは engine/inputs.regime と同じ規則
4. a1.py（採点の再計算）、a2.py・a2b.py（モデル別）、a3.py・a3b.py（範囲の下外れ）、a4.py（局面別の確率の偏り）、a5.py（実質件数と必要期間）
出力は ../output/*.txt
