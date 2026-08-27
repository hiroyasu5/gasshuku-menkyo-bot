# BTC Market Data Feed 設計書

URLを開くだけで、その時点までのBTC市況(Price / OI / Funding / CVD / 板マイクロ構造 / Hyperliquid whales / 清算 / オプション)を機械的に読めるJSONサービスの設計。

- ステータス: 設計フェーズ(実装前)
- 対象ブランチ: `claude/btc-market-data-api-12potq`
- 最終更新: 2026-08-27

---

## 0. 要約(TL;DR)

- **常時稼働のコレクタが1プロセス必要**。CVD・キャンセル率・補充率・吸収スコアは「取引所WebSocketを24時間つなぎっぱなしにして自分で計算する」以外に入手手段がない。GitHub Actions(cron最短5分・起動遅延最大数十分・常駐不可)では原理的に作れないため、**このリポジトリの既存パターン(Actions + Pages)は流用できない**。
- 推奨構成は **「Python asyncio コレクタ1プロセスが収集・1分集計・JSON生成・HTTPS配信まで全部やる」案A**。Fly.io の最小インスタンス or 月$5クラスのVPS 1台で動く。
- MVP のエンドポイントは2種類だけ:
  - `/btc/latest.json` — 最新1分の全指標スナップショット
  - `/btc/history/YYYY-MM-DD.json` — その日の1分足時系列(UTC日付)。`/btc/history.json` は直近48時間のローリング版エイリアス。
- Phase 1 は Price / OI / Funding / CVD / 板深度 / キャンセル・補充・吸収 / HL whale 集計の6系統。清算・オプション・orderflow詳細・predictionはPhase 2以降に追加(スキーマは最初から拡張前提で切っておく)。

---

## 1. なぜ静的ホスティングだけでは作れないか

| 指標 | 必要なデータ | 取得手段 |
|---|---|---|
| Price / OI / Funding | スナップショット | REST(数秒〜毎分ポーリングで足りる) |
| CVD 1m/5m/15m/1h | **全約定のアグレッサー方向** | WebSocket trade stream を連続受信して自分で累積 |
| bid/ask depth, cancel率, 補充率, 吸収 | **L2板の差分イベント** | WebSocket depth diff stream を連続受信して板を再構築 |
| 清算 | 清算イベント | WebSocket liquidation stream(過去分のREST APIはほぼ無い) |

右列のWS系は「その瞬間に受信していなかったデータは永久に手に入らない」。つまり収集は必ず常駐プロセスになる。

一方で**配信**は完全に静的でよい(毎分JSONを書き出すだけ)。この「収集=常駐、配信=静的」の分離が設計の背骨。

### ホスティング案の比較

| 案 | 構成 | 長所 | 短所 | 判定 |
|---|---|---|---|---|
| **A. コレクタ自身が配信(推奨)** | Fly.io / Railway / VPS 上の1プロセスが収集し、同プロセスの aiohttp が `/btc/*.json` を返す | 最小構成・デプロイ1個・遅延ゼロ | ホスト再起動中は配信も止まる(数十秒) | **Phase 1 はこれ** |
| B. コレクタ + Cloudflare R2/Pages 配信 | コレクタが毎分 R2 に PUT、URL は Cloudflare が配信 | 配信が落ちない・URL安定・CDN | 構成2個・R2の結果整合で数秒ズレ | Phase 2 で必要なら移行 |
| C. GitHub Actions 15分ジョブ + commit + Pages | ジョブ内で15分WS受信→集計→commit | 追加インフラゼロ | cron遅延で常時欠測、ジョブ境界でCVD断絶、毎分commitは不可能、リポジトリ肥大 | **不採用** |

案Aで始めて、URLの前段に Cloudflare(オレンジ雲)を挟んでおくと、後から案Bへ移行してもURLが変わらない。独自ドメイン(例: `btc.example.com/btc/latest.json`)を最初から使うことを推奨。

リソース見積り: RAM 200〜300MB、CPUほぼアイドル、受信帯域は Binance depth diff が支配的で数GB/日 → Fly.io shared-cpu-1x(256〜512MB)か最安VPSで足りる。

---

## 2. 全体アーキテクチャ

```
Binance fapi WS ──┐
Bybit v5 WS ──────┤   ┌────────────┐    ┌───────────────┐   ┌──────────────┐
Hyperliquid WS ───┼──▶│ Connectors │───▶│  Aggregator    │──▶│ JSON Writer  │
                  │   │ (再接続・  │    │ 1s tick →      │   │ tmp+rename   │
Binance REST ─────┤   │  板再構築) │    │ 1m bar         │   │ latest/      │
Bybit REST ───────┤   └────────────┘    │ rolling window │   │ history/     │
HL info REST ─────┘         │           └───────┬────────┘   └──────┬───────┘
(OI/funding/whales)         │                   │                   │
                            ▼                   ▼                   ▼
                      ┌──────────┐        ┌──────────┐      ┌──────────────┐
                      │ health    │        │ SQLite   │      │ aiohttp      │
                      │ monitor   │        │ (1m bar  │      │ static serve │
                      │ →Discord  │        │  永続化) │      │ /btc/*.json  │
                      └──────────┘        └──────────┘      └──────────────┘
```

- **Connectors**: 取引所ごとに1コルーチン。指数バックオフ再接続、Binance板は「REST snapshot + diff適用 + シーケンス検証、欠落したら再snapshot」の公式手順。
- **Aggregator**: 1秒ごとに板・trade蓄積から秒次メトリクスを計測し、毎分00秒で1分バーに確定。CVDの5m/15m/1hは1分バーのローリング和。
- **SQLite (WALモード)**: 確定した1分バーを保存。プロセス再起動時に直近72時間分を読み戻し、CVDローリング窓と履歴JSONを復元する(再起動しても履歴が消えない)。
- **JSON Writer**: 毎分バー確定直後に `latest.json` と当日 `history/YYYY-MM-DD.json` を一時ファイル→`rename` で原子的に更新(読み手が壊れたJSONを見ない)。
- **health monitor**: 各フィードの最終受信時刻を監視し、閾値超過で Discord webhook 通知(既存 `notifier` の資産を流用)。JSONの `health` ブロックにも常時反映。

---

## 3. データソース一覧(Phase 1)

| 指標 | ソース | 方式 | 頻度 |
|---|---|---|---|
| BTC price(last/mark) | Binance fapi `btcusdt@aggTrade` / `markPrice` | WS | リアルタイム |
| OI Binance | `GET /fapi/v1/openInterest` ×mark price | REST | 30s |
| OI Bybit | `GET /v5/market/tickers` (openInterestValue) | REST | 30s |
| OI Hyperliquid | info `metaAndAssetCtxs` (openInterest×mark) | REST | 30s |
| Funding Binance | `GET /fapi/v1/premiumIndex` (lastFundingRate) | REST | 60s |
| Funding Bybit | `GET /v5/market/tickers` (fundingRate) | REST | 60s |
| Funding Hyperliquid | info `metaAndAssetCtxs` (funding) | REST | 60s |
| trades(CVD・吸収用) | Binance `btcusdt@aggTrade`(`m`フラグでアグレッサー判定) | WS | 全約定 |
| L2板(深度・キャンセル・補充用) | Binance `btcusdt@depth@100ms` + RESTスナップショット | WS | 100ms diff |
| HL whale ポジション | info `clearinghouseState`(ウォレット巡回) | REST | 60s/一巡 |
| whale ウォレット母集団 | HL leaderboard(`stats-data.hyperliquid.xyz/Mainnet/leaderboard`)+ 手動追加リスト | REST | 1h |

方針: **CVDと板マイクロ構造はまずBinance単独**で出す(BTCUSDT perpが最も流動的で、指標として代表性が高い)。Bybit/HLのtrade・板も合算するのはPhase 2の拡張(スキーマ上は `venue` 別に持てる形にしておく)。

Phase 2以降のソース:

| 指標 | ソース | 備考 |
|---|---|---|
| 清算 | Binance `btcusdt@forceOrder`、Bybit `allLiquidation.BTCUSDT` | WS。HLの清算は約定フィードから推定(精度低め)で補助扱い |
| オプション | Deribit public REST(`ticker`, `get_book_summary_by_currency`) | RESTだけで完結するので実装は軽い。優先度次第でPhase 2前倒し可 |
| prediction | Kalshi public API、Polymarket CLOB API | 対象マーケットのslugを設定ファイルで指定 |
| tradfi | CMEリアルタイムは有償。遅延データ(Yahoo Finance `BTC=F`)+ ETF flow(Farside等のスクレイプ) | 精度に限界があるため最後 |

---

## 4. 指標定義(マイクロ構造系の計算式)

キャンセル率・補充率・吸収は標準指標ではないので、ここで定義を固定する。すべて **Binance BTCUSDT perp の板とtrade** から、1秒tickで計測→1分集約。

用語: `mid` = (best bid + best ask)/2。深度バンドは mid±0.1% / ±0.5% / ±1% のUSD建て名目合計。

### 4.1 CVD
```
cvd_1m_usd = Σ(buyアグレッサー約定のUSD名目) − Σ(sellアグレッサー約定のUSD名目)   # 直近1分
cvd_5m / 15m / 1h = 1分バーのローリング和
```
併せて `aggressive_buy_Xm_usd` / `aggressive_sell_Xm_usd` の生値も出す(CVDだけだと打ち消しが見えないため)。

### 4.2 板イベントの分解(キャンセル vs 約定)
depth diff で bid レベルの数量が減ったとき、その減少分のうち「同じ100ms窓に同価格帯で発生したsellアグレッサー約定量」で説明できる分を**約定による減少**、残りを**キャンセル**とみなす。増加はすべて**追加(addition)**。

```
bid_cancel_rate = bidキャンセル量 / (bidキャンセル量 + bid約定消化量)   # 1分間、±1%バンド内
ask_cancel_rate = 同様にask側
```
1に近い = 板が見せ板的(約定せずに引く)。0に近い = 板が実際に約定を受けている。

### 4.3 補充率(replenishment)
```
bid_replenishment_ratio = bid追加量 / bid減少量(キャンセル+約定)   # 1分間、mid±0.1%バンド内
```
1超 = 削られるより速く積み直されている。0.5未満 = 板が薄くなっていく局面。

### 4.4 吸収スコア(absorption)
「大量のアグレッサーを受けても価格が動かない」度合い。1分間で:
```
buy_absorption_score  = norm( sellアグレッサーUSD / max(ε, 価格下落幅bps) )
sell_absorption_score = norm( buyアグレッサーUSD / max(ε, 価格上昇幅bps) )
```
`norm()` は直近24時間の同値のパーセンタイル順位(0〜1)。つまり「今の吸収の強さは過去24hの中で上位何%か」。絶対値でなく順位にするのは、ボラ水準が変わっても読み方が変わらないようにするため。

### 4.5 HL whale 集計
- leaderboard上位(口座価値ベース)+ 手動指定ウォレットを母集団(50〜100件)として保持。
- 60秒で一巡するよう `clearinghouseState` をずらして巡回し、BTCポジションのみ抽出。
- `whale_long_usd` = 母集団のBTCロング名目合計、`whale_short_usd` = 同ショート。`long_pct = long/(long+short)`。
- 前回スナップショットとの差分から `net_change_1h_usd` を計算。ウォレット別の詳細(entry/liq price/増減イベント)は `whales.json`(Phase 2)。

---

## 5. エンドポイント仕様

配信はすべて: `Access-Control-Allow-Origin: *`、gzip、`latest.json` は `Cache-Control: no-store`、確定済み過去日の `history/*.json` は `max-age=3600`。

### 5.1 `GET /btc/latest.json`(Phase 1)

```jsonc
{
  "schema_version": 1,
  "timestamp": "2026-08-27T12:00:00Z",
  "timestamp_jst": "2026-08-27T21:00:00+09:00",
  "btc": { "price": 78542.3, "mark_price": 78545.1 },
  "oi": {
    "binance_usd": 12345678900,
    "bybit_usd": 5432100000,
    "hyperliquid_usd": 2100000000,
    "total_usd": 19877778900
  },
  "funding": {                     // 直近funding rate(1回分, 8h建てはそのまま)
    "binance": 0.0001,
    "bybit": -0.000004,
    "hyperliquid": 0.0000125       // HLは1h建て → annualizedも併記予定
  },
  "orderflow": {                   // Binance BTCUSDT perp基準
    "cvd_1m_usd": -4200000,
    "cvd_5m_usd": -18100000,
    "cvd_15m_usd": -31000000,
    "cvd_1h_usd": -74500000,
    "aggressive_buy_5m_usd": 94000000,
    "aggressive_sell_5m_usd": 125000000,
    "bid_depth_01pct_usd": 18200000,
    "bid_depth_05pct_usd": 61000000,
    "bid_depth_1pct_usd": 112000000,
    "ask_depth_01pct_usd": 15100000,
    "ask_depth_05pct_usd": 55200000,
    "ask_depth_1pct_usd": 98000000,
    "depth_imbalance_01pct": 0.093,   // (bid−ask)/(bid+ask), ±0.1%
    "bid_cancel_rate": 0.31,
    "ask_cancel_rate": 0.42,
    "bid_replenishment_ratio": 0.82,
    "ask_replenishment_ratio": 0.51,
    "buy_absorption_score": 0.74,
    "sell_absorption_score": 0.29
  },
  "hyperliquid_whales": {
    "wallets_tracked": 62,
    "long_usd": 144200000,
    "short_usd": 328700000,
    "long_pct": 30.5,
    "short_pct": 69.5,
    "net_change_1h_usd": -18000000
  },
  "liquidations": null,            // Phase 2で {long_5m_usd, ...}
  "options": null,                 // Phase 2で {atm_iv, rr_25d, ...}
  "prediction": null,              // Phase 3
  "tradfi": null,                  // Phase 3
  "health": {
    "binance_ws_age_sec": 0.4,     // 最終受信からの経過秒
    "bybit_rest_age_sec": 12,
    "hyperliquid_rest_age_sec": 8,
    "whale_scan_age_sec": 45,
    "stale": []                    // 閾値超過したフィード名の配列。空=全部正常
  }
}
```

読み手(=AI)向けの設計判断:
- **`health.stale` を必ず見れば良い**構造にする。フィード断があっても他のフィールドは最後の値+staleフラグで返し、JSON全体は常に返る。
- 取れなかった値は `null`(0と欠測を絶対に混同させない)。
- Phase 2以降のキーも最初から `null` で置いておき、スキーマ変更ではなく「nullが埋まる」形で拡張する。

### 5.2 `GET /btc/history/YYYY-MM-DD.json`(Phase 1)

UTC日付ごとの1分足。行指向で、各行は `latest.json` の主要指標をフラット化したもの。

```jsonc
{
  "schema_version": 1,
  "date": "2026-08-27",
  "interval": "1m",
  "columns_note": "rowsの各要素のキーはlatest.jsonの主要指標のフラット版",
  "rows": [
    {
      "ts": "2026-08-27T00:00:00Z",
      "price": 78210.5,
      "oi_binance_usd": 12300000000,
      "oi_bybit_usd": 5400000000,
      "oi_hl_usd": 2080000000,
      "funding_binance": 0.0001,
      "funding_bybit": -0.000004,
      "funding_hl": 0.0000125,
      "cvd_1m_usd": -4200000,
      "aggr_buy_1m_usd": 21000000,
      "aggr_sell_1m_usd": 25200000,
      "bid_depth_01pct_usd": 18200000,
      "ask_depth_01pct_usd": 15100000,
      "bid_depth_1pct_usd": 112000000,
      "ask_depth_1pct_usd": 98000000,
      "bid_cancel_rate": 0.31,
      "ask_cancel_rate": 0.42,
      "bid_replenishment_ratio": 0.82,
      "ask_replenishment_ratio": 0.51,
      "buy_absorption_score": 0.74,
      "sell_absorption_score": 0.29,
      "whale_long_usd": 144200000,
      "whale_short_usd": 328700000,
      "whale_long_pct": 30.5,
      "gap": false                 // このバーで主要フィード断があればtrue
    }
    // ... 1440行/日
  ]
}
```

- サイズ見積り: 1行 ≈ 500B × 1440行 ≈ 700KB/日(gzip後 ~150KB)。問題なし。
- 1分足のCVDは `cvd_1m_usd` のみ保存。5m/15m/1h/任意区間(Asiaセッション等)は**読み手が1分足を積分して再計算できる**ので保存しない(履歴の冗長化を避ける)。
- 保持期間: ディスク上は30日(それ以前は削除 or 月次アーカイブ)。SQLiteは全期間保持(1分バーは軽い)。

### 5.3 `GET /btc/history.json`

直近48時間のローリング版(5.2と同じ行形式)。「今日+昨日の2ファイルを読む」手間を省くための便利エイリアス。

### 5.4 Phase 2以降のエンドポイント(スキーマ骨子のみ)

- `/btc/liquidations.json` — 1m/5m/15m/1hのlong/short清算USD、`by_exchange` 内訳。1分足履歴にも `liq_long_1m_usd`/`liq_short_1m_usd` 列を追加。
- `/btc/whales.json` — ウォレット別詳細(side, size, notional, entry, mark, uPnL, leverage, liq price, 前回比 `change`: opened/increased/reduced/closed/flipped)。上位50件。
- `/btc/options.json` — Deribit: index, ATM IV, 7d/30d IV, 25Δ call/put IV, RR, butterfly, put/call OI, expiry別OI, major strike OI。RESTのみで完結するため優先度を上げやすい。
- `/btc/orderflow.json` — 直近1時間の1秒足(aggr buy/sell, CVD, 3バンド深度, add/cancel/replenish, absorption)。サイズ ~1MB gzip。
- `/btc/prediction.json` — platform, market, threshold, probability, 1h/4h/24h前, change。
- `/btc/tradfi.json` — CME(遅延)price/basis/volume/OI、ETF net flow(issuer別)。

---

## 6. 実装構成

言語はPython 3.12(既存リポジトリと揃える)。新規パッケージとして分離:

```
src/btc_feed/
  __main__.py        # asyncio エントリポイント(全タスク起動)
  config.py          # バンド幅、巡回間隔、whaleリスト、閾値
  connectors/
    binance.py       # aggTrade WS, depth WS+snapshot同期, OI/funding REST
    bybit.py         # OI/funding REST(Phase2: WS)
    hyperliquid.py   # metaAndAssetCtxs, clearinghouseState巡回, leaderboard
  orderbook.py       # L2板の再構築とバンド深度・イベント分解(cancel/add/fill)
  aggregate.py       # 1s tick → 1m bar、ローリング窓、absorptionパーセンタイル
  store.py           # SQLite (WAL) 読み書き、再起動時の窓復元
  writer.py          # latest.json / history/*.json の原子的書き出し
  server.py          # aiohttp: /btc/*.json 配信 + /healthz
  health.py          # フィード監視 → health block + Discord通知
Dockerfile
fly.toml             # (Fly.ioを選ぶ場合)
```

- 依存追加: `aiohttp`, `websockets`(または aiohttp のWSクライアントに統一)。既存の `httpx` も流用可。
- 既存の門限bot・AIダッシュボードとはコード・ワークフローとも完全分離(requirementsは `requirements-btc.txt` に分ける)。
- デプロイはDockerコンテナ1個。systemd(VPSの場合)or Fly.ioのauto-restartで常駐。

### テスト方針
- `orderbook.py` のイベント分解(diff→cancel/fill/add の振り分け)と `aggregate.py` の窓計算は録画したWSメッセージのフィクスチャで単体テスト(ここが唯一ロジックが濃い場所)。
- コネクタは再接続とシーケンス欠落→再snapshotの状態遷移だけテスト。

---

## 7. 運用・信頼性

- **再起動耐性**: 1分バーはSQLiteに確定保存 → 再起動時に読み戻して履歴とローリング窓を復元。落ちていた区間は `gap: true` の行として残る(補間しない)。
- **フィード監視**: WS最終受信 > 10s、REST最終成功 > 3×間隔 で `health.stale` に載せ、5分継続でDiscord通知。
- **レート制限**: Binance REST は軽ポーリングのみ(weight余裕大)。HLの `clearinghouseState` 巡回は母集団を60秒に分散させ、429時は巡回周期を自動延長。
- **時刻**: すべてUTCで処理・保存。JSTは `timestamp_jst` として表示用に併記(履歴ファイルの日付境界はUTC)。
- **セキュリティ**: 公開読み取り専用・APIキー不要(取得はすべてpublicエンドポイント)。書き込み系は存在しない。DoSが気になればCloudflare前段で吸収。

---

## 8. フェーズ計画

| フェーズ | 内容 | 成果物 |
|---|---|---|
| **Phase 1 (MVP)** | コレクタ本体 + Binance板/trade処理 + OI/Funding 3所 + HL whale集計 + latest/history配信 + health + デプロイ | `latest.json`, `history/*.json`, `history.json` が本番URLで毎分更新 |
| Phase 2a | 清算(Binance/Bybit WS)→ latestと1分足履歴に追加 | `liquidations.json` |
| Phase 2b | Deribitオプション(REST) | `options.json` |
| Phase 2c | whaleウォレット別詳細 + 変化イベント | `whales.json` |
| Phase 2d | 1秒足orderflow | `orderflow.json` |
| Phase 3 | prediction(Kalshi/Polymarket)、tradfi(CME遅延+ETF flow)、CVDの取引所合算 | `prediction.json`, `tradfi.json` |

Phase 1完了の受け入れ条件:
1. 本番URLで `latest.json` が毎分更新され、Phase 1の全フィールドが埋まる
2. `history/YYYY-MM-DD.json` に1分足が欠測マーク付きで蓄積される
3. プロセスを殺して再起動しても履歴が残り、gap行だけが増える
4. フィード断がDiscordに通知され、JSONの `health.stale` に反映される

---

## 9. 決めておきたいこと(実装前の確認事項)

1. **デプロイ先**: Fly.io(クレカ登録・月$0〜3)/ 手持ちVPS / Railway。→ 指定がなければ Fly.io で進める。
2. **ドメイン**: Cloudflare管理の独自ドメインを前段に置くか(推奨)、ホスト付与のURL(`*.fly.dev`)で始めるか。
3. **whale手動リスト**: leaderboard上位に加えて追跡したい既知ウォレットがあれば `config.py` に足す。
