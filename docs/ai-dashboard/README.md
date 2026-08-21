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

## 12指標と警戒ライン

| # | 指標 | 取得 | 判定基準 |
|---|------|------|----------|
| 1 | Hyperscaler CapEx/Cloud | 手動(四半期) | CapEx↑+Cloud↑=🟢 / Cloud横ばい=🟡 / Cloud↓+FCF悪化=🔴 |
| 2 | CRWV Revenue Backlog | 手動(四半期) | QoQ +2%超=🟢 / 横ばい=🟡 / 減少=🟠 / -15%超or解約=🔴 |
| 3 | NBIS Customer Commitments | 手動(四半期) | 同上 |
| 4 | GPUレンタル価格(最新世代) | 自動(日次) | 3か月変化 -10%まで🟢 / -20%🟡 / -30%🟠 / それ以上🔴 |
| 5 | Spot/On-demand比率(CW B200) | 自動(日次) | 0.40以上🟢 / 0.25🟡 / 0.15🟠 / 未満🔴 |
| 6 | APLD Contracted/Live MW | 手動(四半期) | QoQ増加=🟢 / 横ばい=🟡 / 減少=🟠 / キャンセル=🔴 |
| 7 | DLR Bookings/更新賃料 | 手動(四半期) | 賃料+3%超🟢 / 0〜3%🟡 / マイナス🟠 / -5%超🔴 |
| 8 | AEP Contracted Load GW | 手動(四半期) | QoQ増加=🟢 / 横ばい=🟡 / 減少=🟠 / キャンセル=🔴 |
| 9 | PJM需要予測(Dominion) | 手動(年次) | 上方修正🟢 / 横ばい🟡 / 下方修正🟠 / 大幅下方🔴 |
| 10 | HY OAS | 自動(日次) | 3か月で+50bpまで🟢 / +100bp🟡 / +200bp🟠 / それ以上🔴 |
| 11 | CRWV債券利回り(2032) | 手動 | 11%未満🟢 / 13%未満🟡 / 16%未満🟠 / 16%以上🔴 |
| 12 | AI企業の新規借入条件 | 手動(随時) | クーポン安定🟢 / +1pt🟡 / +3pt🟠 / 発行失敗🔴 |

## 複合判定

5グループ (需要 / Compute価格 / DC需給 / 電力 / 信用) のうち🟠以上が:

- **1グループ** → 🟡 企業固有要因・ノイズの可能性
- **2グループ** → 🟠 警戒
- **3グループ以上** → 🔴 AIサイクル変調の可能性
- **需要側3グループ以上 + 信用悪化** → 🚨 **EXIT検討シグナル**

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
