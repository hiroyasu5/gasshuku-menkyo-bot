# AI Bubble Escape Dashboard - 運用ガイド

AI設備投資サイクルの変調を12指標で監視するシステム。
毎日 JST 8:30 に GitHub Actions が自動実行し、Discord通知と
このディレクトリの `index.html` (ダッシュボード) を更新する。

## 仕組み

```
GitHub Actions (毎日 JST 8:30 / manual_inputs.yaml のpush時)
  └ python -m src.ai_dashboard
      ├ FRED: HY OAS / Single-B OAS / IG OAS を自動取得 (無料・キー不要)
      ├ Silicon Data: GPUレンタル指数をスクレイピング (実験的)
      ├ CoreWeave: B200 on-demand/spot掲載価格をスクレイピング (実験的)
      ├ data/ai_dashboard/manual_inputs.yaml: 四半期指標・債券利回りを読込
      ├ 12指標を🟢🟡🟠🔴で判定 + 5グループの複合判定
      ├ Discord通知 (変化時・エラー時・月曜サマリー・決算リマインダー)
      └ history.json と index.html を更新してコミット
```

## 15指標と警戒ライン

| # | 指標 | 取得 | 判定基準 |
|---|------|------|----------|
| 1 | Hyperscaler 5社 (breadth) | 手動(四半期) | CapEx下方修正2社+or Cloud縮小=🔴 / 1社or減速3社=🟠 / 減速2社=🟡 |
| 2 | CRWV Backlog/Commitments | 手動(四半期) | 両方QoQで判定: +2%超=🟢 / 横ばい=🟡 / 減少=🟠 / -15%超or解約=🔴 |
| 3 | NBIS Customer Commitments | 手動(四半期) | 同上 |
| 4 | ORCL RPO | 手動(四半期) | 同上 |
| 5 | GPUレンタル価格(最新世代) | 自動(日次) | 3か月変化 -10%まで🟢 / -20%🟡 / -30%🟠 / それ以上🔴 |
| 6 | Spot/On-demand比率(CW B200) | 自動(日次) | 30日トレンド重視: -0.05🟡 / -0.10🟠 / -0.20🔴 (絶対値は参考) |
| 7 | CRWV Utilization Proxy | 手動(四半期) | 売上QoQ−電力QoQ: 0pt以上🟢 / -10pt🟡 / -25pt🟠 / 未満🔴 |
| 8 | APLD Contracted/Live MW | 手動(四半期) | QoQ増加=🟢 / 横ばい=🟡 / 減少=🟠 / キャンセル=🔴 |
| 9 | DLR Bookings/更新賃料 | 手動(四半期) | 賃料+3%超🟢 / 0〜3%🟡 / マイナス🟠 / -5%超🔴 |
| 10 | AEP Contracted Load GW | 手動(四半期) | QoQ増加=🟢 / 横ばい=🟡 / 減少=🟠 / キャンセル=🔴 |
| 11 | PJM需要予測(Dominion) | 手動(年次) | 上方修正🟢 / 横ばい🟡 / 下方修正🟠 / 大幅下方🔴 |
| 12 | HY OAS | 自動(日次) | 3か月で+50bpまで🟢 / +100bp🟡 / +200bp🟠 / それ以上🔴 |
| 13 | CRWVスプレッド(2032) | 手動+自動 | yield−UST7y: 700bp未満🟢 / 900🟡 / 1200🟠 / 以上🔴。vs HY OASも表示 |
| 14 | AI企業の新規借入条件 | 手動(随時) | 同issuer同categoryのspread比較のみ: +25bp🟢 / +100🟡 / +300🟠 / 発行失敗🔴 |
| 15 | Liquidity Coverage (24m) | 手動(四半期) | (Cash+未使用枠)÷24m満期debt: 2x🟢 / 1x🟡 / 0.7x🟠 / 未満🔴 |

## Market Early Warning (v3・EXIT判定には使わない先行警報)

| # | 指標 | 取得 | 判定基準 |
|---|------|------|----------|
| 16 | AI Market Breadth | 自動(日次・Stooq) | 固定AI_BASKET_V1(24銘柄)の200DMA上回り率: >65%🟢 / 50-65🟡 / 30-50🟠 / <30🔴。20日で-20pt以上なら1段階悪化 |
| 17 | EPS Revision Breadth | 自動(日次・Alpha Vantage) | Tier1 12社のFY1 EPS consensus 30日前比の上方修正率: ≥55%🟢 / 35🟡 / 20🟠 / 未満🔴 |
| 18 | Multiple Expansion | 自動(日次) | 90日株価リターン−90日EPS修正(中央値): ≤10pt🟢 / 25🟡 / 45🟠 / 超🔴 |

Market警報ライン: 3指標中🟠以上が 0=🟢bull確認 / 1=🟡Watch / 2=🟠Early warning /
3=🔴Market regime deterioration。**Market🔴だけではEXITしない**(クロスシグナル用)。

- バスケットは `src/ai_dashboard/basket.py` の `AI_BASKET_V1` に固定。
  構成を変える時は入れ替えではなく **V2を新設** する
- ⑰⑱には無料の `ALPHAVANTAGE_API_KEY` secretが必要(無料枠25req/日、12社で収まる)。
  未設定でも⑯Breadthは動く(Stooqはキー不要)
- EPS consensusは日次スナップショットを`history.json`に蓄積し、30日/90日前比を
  自前計算する。蓄積されるまで⑰⑱は⚪

## AI Bubble State と Stage

上部パネルに Market / Fundamentals(需要+稼働率) / Infrastructure(Compute+DC+電力) /
Credit の4行と、崩壊チェーンのどこまで悪化が伝播したかを示すStageを表示:

1. **Expansion** — 需要・利益・CapExすべて拡大
2. **Exuberance** — Multiple Expansion🟠+(Valuation先行)、実需はまだ強い
3. **Divergence** — Market警報🟠+(Breadth/Revisions悪化)、実需はまだ強い
4. **Fundamental rollover** — 需要/稼働率/インフラ側が🟠+
5. **Credit stress** — 信用グループが🟠+
6. **Bust** — Credit🔴+実体悪化

## イベントログ (色変化の履歴)

全指標・複合判定・Market警報・Stageのレベルが変わるたびに
`history.json` の `events` に「日付・指標・変化前→変化後・当時の値」を永続記録し、
ダッシュボードに時系列表で表示する。将来AIサイクルが崩れた時に
**「最初に鳴った警報は何だったか」** を検証するための履歴。
稼働初期 (2026-08-21〜) の変化はgitコミット履歴から遡及復元済み (「遡及復元」バッジ)。

## Data confidence (v2)

🟢は**データで正常を確認した時のみ**。データ不足は⚪、level_hint・フォールバック値・
stale(🕐)による判定は「暫定」バッジ付きで、複合バナーの **Data confidence %**
(= confirmed指標の割合) には数えない。

## 複合判定

6グループ (需要 / Compute価格 / 稼働率 / DC需給 / 電力 / 信用) のうち🟠以上が:

- **1グループ** → 🟡 企業固有要因・ノイズの可能性
- **2グループ** → 🟠 警戒
- **3グループ以上** → 🔴 AIサイクル変調の可能性
- **需要側(信用以外の5グループ)3つ以上 + 信用悪化** → 🚨 **EXIT検討シグナル**

## 運用: やること

### 四半期決算後 (リマインダーが届く)

`data/ai_dashboard/manual_inputs.yaml` の該当セクションに新しい四半期エントリを
**追記**(上書きしない。QoQ比較に前期分が必要)してmainへpush。pushすると
Actionsが即座に再評価する。

### 随時

- AI企業の新規資金調達が出たら `financing:` に追記
- CRWV債券利回りを確認したら `crwv_bond.yield_pct` を更新
- 決算日が確定したら `earnings_calendar:` の日付を修正

### Discord設定

- 既存の `DISCORD_WEBHOOK_URL` secret をそのまま使う
- 専用チャンネルに分けたい場合はリポジトリsecretに
  `AI_DASHBOARD_DISCORD_WEBHOOK_URL` を追加する

### ダッシュボードをWebで見る

リポジトリ Settings → Pages → Source を `main` / `/docs` に設定すると
`https://<user>.github.io/gasshuku-menkyo-bot/ai-dashboard/` で見られる。
設定しなくても `docs/ai-dashboard/index.html` をローカルで開けば見られる。

## 注意事項

- Silicon Data / CoreWeave のスクレイパーはサイト構造変更で壊れる可能性がある。
  壊れるとDiscordにエラー通知が届き、`gpu_manual_fallback` の手動値が使われる。
  その間は `manual_inputs.yaml` のフォールバック値を手で更新すれば運用継続できる。
- GPU価格の「3か月変化」判定は時系列が90日貯まるまで参考値。
- FREDは初回実行時に2年分をバックフィルするので、HY OASの3か月判定は初日から有効。
