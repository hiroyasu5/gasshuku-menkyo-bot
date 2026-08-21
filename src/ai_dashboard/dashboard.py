"""AI Bubble Dashboard - 静的HTMLダッシュボード生成 (docs/ai-dashboard/index.html)。

self-containedなHTML1枚を生成する。GitHub Pages (main / /docs) を有効化すれば
https://<user>.github.io/<repo>/ai-dashboard/ で閲覧できる。
チャートは埋め込みJSONをvanilla JSでSVG描画し、ホバーで値を表示する。
"""
from __future__ import annotations

import json
import logging

from . import storage
from .config import DASHBOARD_DIR, DASHBOARD_FILE
from .models import (
    CONF_NONE,
    CONF_PROVISIONAL,
    GROUP_LABEL_JA,
    GROUP_ORDER,
    CompositeResult,
    IndicatorResult,
    Level,
    LEVEL_EMOJI,
    LEVEL_LABEL_JA,
)

logger = logging.getLogger(__name__)

CHART_DAYS = 180
TABLE_DAYS = 30

# ダッシュボードに載せる日次メトリクスの表示定義
DAILY_METRIC_LABELS = {
    "hy_oas_bps": "HY OAS (bp)",
    "single_b_oas_bps": "Single-B OAS (bp)",
    "ig_oas_bps": "IG OAS (bp)",
    "sd_b300_rental": "B300 ($/GPU-h)",
    "sd_b200_rental": "B200 ($/GPU-h)",
    "sd_h200_rental": "H200 ($/GPU-h)",
    "sd_h100_rental": "H100 ($/GPU-h)",
    "cw_b200_od_node": "CW B200 OD ($/node-h)",
    "cw_b200_spot_node": "CW B200 Spot ($/node-h)",
}


def _series_for_chart(history: dict, metric: str, name: str, slot: int) -> dict | None:
    series = storage.get_series(history, metric)[-CHART_DAYS:]
    if len(series) < 2:
        return None
    return {"name": name, "slot": slot, "points": series}


def generate_dashboard(
    history: dict,
    results: list[IndicatorResult],
    composite: CompositeResult,
) -> None:
    credit_series = [
        s for s in [
            _series_for_chart(history, "hy_oas_bps", "HY OAS", 1),
            _series_for_chart(history, "single_b_oas_bps", "Single-B OAS", 2),
            _series_for_chart(history, "ig_oas_bps", "IG OAS", 3),
        ] if s
    ]
    gpu_series = [
        s for s in [
            _series_for_chart(history, "sd_b200_rental", "B200", 1),
            _series_for_chart(history, "sd_h100_rental", "H100", 2),
            _series_for_chart(history, "sd_h200_rental", "H200", 3),
        ] if s
    ]

    html = _render(history, results, composite, credit_series, gpu_series)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("ダッシュボード生成完了: %s", DASHBOARD_FILE)


# ---------------------------------------------------------------
# HTML描画
# ---------------------------------------------------------------

def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def _card(r: IndicatorResult) -> str:
    lv = r.level.value
    badges = ""
    if r.stale:
        badges += '<span class="badge badge-stale">🕐 stale</span>'
    if r.confidence == CONF_PROVISIONAL:
        badges += '<span class="badge badge-prov">暫定</span>'
    elif r.confidence == CONF_NONE and r.level is not Level.UNKNOWN:
        badges += '<span class="badge badge-prov">未確認</span>'
    mini_table = ""
    if r.detail_rows:
        head = "".join(f"<th>{_esc(c)}</th>" for c in r.detail_rows[0])
        body = "".join(
            "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>"
            for row in r.detail_rows[1:]
        )
        mini_table = (
            f'<div class="minitablewrap"><table class="minitable">'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
        )
    return f"""
      <div class="card" data-level="{lv}">
        <div class="card-head">
          <span class="chip chip-{lv}"><span class="chip-icon">{LEVEL_EMOJI[r.level]}</span>{LEVEL_LABEL_JA[r.level]}</span>
          <span class="badges">{badges}</span>
          <span class="asof">{_esc(r.as_of)}</span>
        </div>
        <h3>{_esc(r.name)}</h3>
        <div class="value">{_esc(r.value_text)}</div>
        <p class="detail">{_esc(r.detail)}</p>
        {mini_table}
        <p class="source">{_esc(r.source)}</p>
      </div>"""


def _table_rows(history: dict) -> tuple[list[str], list[list[str]]]:
    dates = sorted(history["daily"].keys())[-TABLE_DAYS:]
    metrics = [k for k in DAILY_METRIC_LABELS if any(
        k in history["daily"][d] for d in dates
    )]
    header = ["日付"] + [DAILY_METRIC_LABELS[k] for k in metrics]
    rows = []
    for d in reversed(dates):
        vals = history["daily"][d]
        row = [d]
        for k in metrics:
            v = vals.get(k)
            row.append(f"{v:,.2f}" if isinstance(v, (int, float)) else "-")
        rows.append(row)
    return header, rows


def _render(
    history: dict,
    results: list[IndicatorResult],
    composite: CompositeResult,
    credit_series: list[dict],
    gpu_series: list[dict],
) -> str:
    clv = composite.level.value
    group_pills = "".join(
        f'<span class="chip chip-{composite.group_levels.get(g, Level.UNKNOWN).value}">'
        f'<span class="chip-icon">{LEVEL_EMOJI[composite.group_levels.get(g, Level.UNKNOWN)]}</span>'
        f"{_esc(GROUP_LABEL_JA[g])}</span>"
        for g in GROUP_ORDER
    )

    sections = []
    for g in GROUP_ORDER:
        cards = "".join(_card(r) for r in results if r.group == g)
        sections.append(
            f'<section><h2>{_esc(GROUP_LABEL_JA[g])}</h2>'
            f'<div class="grid">{cards}</div></section>'
        )

    header, rows = _table_rows(history)
    thead = "".join(f"<th>{_esc(h)}</th>" for h in header)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>" for row in rows
    )

    charts_html = ""
    chart_data = {}
    if credit_series:
        charts_html += (
            '<div class="chart-card"><h3>クレジットスプレッド (直近180日)</h3>'
            '<div class="chart" id="chart-credit"></div></div>'
        )
        chart_data["chart-credit"] = {"series": credit_series, "unit": "bp"}
    if gpu_series:
        charts_html += (
            '<div class="chart-card"><h3>GPUレンタル価格 $/GPU-h (直近180日)</h3>'
            '<div class="chart" id="chart-gpu"></div></div>'
        )
        chart_data["chart-gpu"] = {"series": gpu_series, "unit": "$"}
    if not charts_html:
        charts_html = '<p class="muted">時系列データ蓄積中。チャートは2日分以上の観測が貯まると表示されます。</p>'

    exit_banner = ""
    if composite.exit_signal:
        exit_banner = (
            '<div class="exit-banner">🚨 EXIT検討シグナル: '
            "需要側3グループ以上と信用市場が同時に悪化しています</div>"
        )

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Bubble Escape Dashboard</title>
<style>
:root {{
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
  --good-bg: rgba(12,163,12,0.10); --warning-bg: rgba(250,178,25,0.14);
  --serious-bg: rgba(236,131,90,0.14); --critical-bg: rgba(208,59,59,0.12);
  --unknown-bg: rgba(137,135,129,0.12);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19;
  --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--page); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Noto Sans JP", sans-serif;
  line-height: 1.55;
}}
main {{ max-width: 1080px; margin: 0 auto; padding: 24px 20px 64px; }}
h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
h2 {{ font-size: 1.05rem; margin: 32px 0 12px; color: var(--ink-2); }}
h3 {{ font-size: 0.95rem; margin: 8px 0 4px; }}
.updated {{ color: var(--muted); font-size: 0.8rem; margin: 0 0 16px; }}
.banner {{
  border: 1px solid var(--border); border-radius: 12px; background: var(--surface);
  padding: 16px 18px; margin: 16px 0;
}}
.banner .summary {{ font-size: 1.05rem; font-weight: 600; margin: 0 0 10px; }}
.pills {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.exit-banner {{
  background: var(--critical-bg); border: 1px solid var(--critical);
  color: var(--ink); font-weight: 700; border-radius: 12px;
  padding: 14px 18px; margin: 16px 0;
}}
.chip {{
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 0.78rem; font-weight: 600; border-radius: 999px;
  padding: 2px 10px 2px 7px; border: 1px solid var(--border); color: var(--ink-2);
}}
.chip-icon {{ font-size: 0.7rem; }}
.chip-green {{ background: var(--good-bg); }}
.chip-yellow {{ background: var(--warning-bg); }}
.chip-orange {{ background: var(--serious-bg); }}
.chip-red {{ background: var(--critical-bg); }}
.chip-unknown {{ background: var(--unknown-bg); }}
.grid {{
  display: grid; gap: 14px;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
}}
.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 14px 16px;
}}
.card-head {{ display: flex; align-items: center; gap: 6px; }}
.asof {{ color: var(--muted); font-size: 0.75rem; margin-left: auto; }}
.badges {{ display: inline-flex; gap: 4px; }}
.badge {{
  font-size: 0.68rem; font-weight: 600; border-radius: 6px; padding: 1px 6px;
  border: 1px solid var(--border); color: var(--ink-2); background: var(--unknown-bg);
}}
.confidence {{ color: var(--ink-2); font-size: 0.82rem; margin: 10px 0 0; }}
.minitablewrap {{ overflow-x: auto; margin: 8px 0 2px; }}
.minitable {{ border-collapse: collapse; width: 100%; font-size: 0.74rem; }}
.minitable th, .minitable td {{
  padding: 3px 8px; text-align: right; border-bottom: 1px solid var(--grid);
  font-variant-numeric: tabular-nums;
}}
.minitable th:first-child, .minitable td:first-child {{ text-align: left; }}
.minitable th {{ color: var(--muted); font-weight: 600; }}
.value {{ font-size: 1.25rem; font-weight: 700; margin: 2px 0; }}
.detail {{ color: var(--ink-2); font-size: 0.85rem; margin: 4px 0 2px; }}
.source {{ color: var(--muted); font-size: 0.72rem; margin: 4px 0 0; }}
.charts {{ display: grid; gap: 14px; grid-template-columns: 1fr; }}
@media (min-width: 900px) {{ .charts {{ grid-template-columns: 1fr 1fr; }} }}
.chart-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 14px 16px;
}}
.chart {{ position: relative; }}
.chart svg {{ display: block; width: 100%; height: auto; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 6px 0 2px; }}
.legend span {{ display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem; color: var(--ink-2); }}
.legend i {{ width: 14px; height: 3px; border-radius: 2px; display: inline-block; }}
.tooltip {{
  position: absolute; pointer-events: none; display: none;
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 6px 10px; font-size: 0.75rem; color: var(--ink);
  box-shadow: 0 2px 8px rgba(0,0,0,0.15); white-space: nowrap; z-index: 5;
}}
.muted {{ color: var(--muted); }}
details {{ margin-top: 28px; }}
summary {{ cursor: pointer; color: var(--ink-2); font-weight: 600; }}
.tablewrap {{ overflow-x: auto; margin-top: 10px; border: 1px solid var(--border); border-radius: 10px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.78rem; background: var(--surface); }}
th, td {{ padding: 6px 10px; text-align: right; border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ color: var(--muted); font-weight: 600; position: sticky; top: 0; background: var(--surface); }}
footer {{ margin-top: 40px; color: var(--muted); font-size: 0.78rem; }}
footer code {{ background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; }}
</style>
</head>
<body>
<main>
  <h1>AI Bubble Escape Dashboard</h1>
  <p class="updated">最終更新: {_esc(history.get("last_updated") or "-")} (JST) ・ 日次自動更新 + 四半期手動入力</p>
  {exit_banner}
  <div class="banner">
    <p class="summary"><span class="chip chip-{clv}"><span class="chip-icon">{LEVEL_EMOJI[composite.level]}</span>{LEVEL_LABEL_JA[composite.level]}</span>
    {_esc(composite.summary)}</p>
    <div class="pills">{group_pills}</div>
    <p class="confidence">Data confidence: <strong>{composite.confidence_pct}%</strong>
    (実データで確認済み {composite.confirmed_count}/{composite.total_count}指標 ・
    残りはデータ不足⚪または暫定判定)</p>
  </div>

  {"".join(sections)}

  <h2>時系列チャート</h2>
  <div class="charts">{charts_html}</div>

  <details>
    <summary>日次データ表 (直近{TABLE_DAYS}日)</summary>
    <div class="tablewrap"><table>
      <thead><tr>{thead}</tr></thead>
      <tbody>{tbody}</tbody>
    </table></div>
  </details>

  <footer>
    <p>判定基準: 🟠警戒以上のグループが 1=ノイズの可能性 / 2=警戒 / 3以上=AIサイクル変調。
    需要側(需要/Compute/稼働率/DC/電力)3グループ以上+信用市場の同時悪化でEXIT検討シグナル。</p>
    <p>🟢はデータで正常を確認した時のみ。⚪=データ不足、「暫定」=level_hint・フォールバック値・
    stale(🕐 古いデータ)による判定で、Data confidenceには数えない。</p>
    <p>四半期指標の更新: <code>data/ai_dashboard/manual_inputs.yaml</code> を編集してmainへpush。</p>
  </footer>
</main>
<script>
const CHART_DATA = {json.dumps(chart_data, ensure_ascii=False)};

function drawChart(id, cfg) {{
  const el = document.getElementById(id);
  if (!el) return;
  const W = 460, H = 240, PAD = {{l: 46, r: 12, t: 12, b: 26}};
  const series = cfg.series;
  const allDates = [...new Set(series.flatMap(s => s.points.map(p => p[0])))].sort();
  const dateIdx = new Map(allDates.map((d, i) => [d, i]));
  const allVals = series.flatMap(s => s.points.map(p => p[1]));
  let vmin = Math.min(...allVals), vmax = Math.max(...allVals);
  if (vmin === vmax) {{ vmin -= 1; vmax += 1; }}
  const span = vmax - vmin;
  vmin -= span * 0.08; vmax += span * 0.08;
  const x = d => PAD.l + (dateIdx.get(d) / Math.max(1, allDates.length - 1)) * (W - PAD.l - PAD.r);
  const y = v => PAD.t + (1 - (v - vmin) / (vmax - vmin)) * (H - PAD.t - PAD.b);

  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${{W}} ${{H}}`);
  svg.setAttribute("role", "img");

  // 横グリッド + y目盛り (4本)
  for (let i = 0; i <= 4; i++) {{
    const v = vmin + (i / 4) * (vmax - vmin);
    const gy = y(v);
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", PAD.l); line.setAttribute("x2", W - PAD.r);
    line.setAttribute("y1", gy); line.setAttribute("y2", gy);
    line.setAttribute("style", "stroke:var(--grid);stroke-width:1");
    svg.appendChild(line);
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", PAD.l - 6); t.setAttribute("y", gy + 3.5);
    t.setAttribute("text-anchor", "end");
    t.setAttribute("style", "fill:var(--muted);font-size:9px;font-variant-numeric:tabular-nums");
    t.textContent = v >= 100 ? Math.round(v) : v.toFixed(2);
    svg.appendChild(t);
  }}
  // x目盛り (最初/中央/最後)
  [0, Math.floor((allDates.length - 1) / 2), allDates.length - 1].forEach(i => {{
    if (i < 0 || allDates.length === 0) return;
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", x(allDates[i])); t.setAttribute("y", H - 8);
    t.setAttribute("text-anchor", i === 0 ? "start" : (i === allDates.length - 1 ? "end" : "middle"));
    t.setAttribute("style", "fill:var(--muted);font-size:9px");
    t.textContent = allDates[i].slice(5);
    svg.appendChild(t);
  }});

  series.forEach(s => {{
    const pts = s.points.map(p => `${{x(p[0]).toFixed(1)}},${{y(p[1]).toFixed(1)}}`).join(" ");
    const pl = document.createElementNS(ns, "polyline");
    pl.setAttribute("points", pts);
    pl.setAttribute("style", `fill:none;stroke:var(--series-${{s.slot}});stroke-width:2;stroke-linejoin:round;stroke-linecap:round`);
    svg.appendChild(pl);
  }});

  // クロスヘア + ツールチップ
  const cross = document.createElementNS(ns, "line");
  cross.setAttribute("y1", PAD.t); cross.setAttribute("y2", H - PAD.b);
  cross.setAttribute("style", "stroke:var(--axis);stroke-width:1;display:none");
  svg.appendChild(cross);
  const dots = series.map(s => {{
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("r", 3.5);
    c.setAttribute("style", `fill:var(--series-${{s.slot}});stroke:var(--surface);stroke-width:2;display:none`);
    svg.appendChild(c);
    return c;
  }});

  const tip = document.createElement("div");
  tip.className = "tooltip";
  el.appendChild(tip);
  el.appendChild(svg);

  svg.addEventListener("pointermove", ev => {{
    const rect = svg.getBoundingClientRect();
    const px = (ev.clientX - rect.left) / rect.width * W;
    let best = 0, bestD = Infinity;
    allDates.forEach((d, i) => {{
      const dd = Math.abs(x(d) - px);
      if (dd < bestD) {{ bestD = dd; best = i; }}
    }});
    const d = allDates[best];
    cross.setAttribute("x1", x(d)); cross.setAttribute("x2", x(d));
    cross.style.display = "";
    let html = `<strong>${{d}}</strong>`;
    series.forEach((s, si) => {{
      const p = s.points.find(pp => pp[0] === d);
      if (p) {{
        dots[si].setAttribute("cx", x(d)); dots[si].setAttribute("cy", y(p[1]));
        dots[si].style.display = "";
        const val = cfg.unit === "$" ? "$" + p[1].toFixed(2) : Math.round(p[1]) + cfg.unit;
        html += `<br><span style="color:var(--series-${{s.slot}})">●</span> ${{s.name}}: ${{val}}`;
      }} else {{ dots[si].style.display = "none"; }}
    }});
    tip.innerHTML = html;
    tip.style.display = "block";
    const tipX = (x(d) / W) * rect.width;
    tip.style.left = Math.min(rect.width - 150, Math.max(0, tipX + 12)) + "px";
    tip.style.top = "10px";
  }});
  svg.addEventListener("pointerleave", () => {{
    cross.style.display = "none"; tip.style.display = "none";
    dots.forEach(c => c.style.display = "none");
  }});

  // 凡例
  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML = series.map(s =>
    `<span><i style="background:var(--series-${{s.slot}})"></i>${{s.name}}</span>`
  ).join("");
  el.parentElement.insertBefore(legend, el);
}}

Object.entries(CHART_DATA).forEach(([id, cfg]) => drawChart(id, cfg));
</script>
</body>
</html>
"""
