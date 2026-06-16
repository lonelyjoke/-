"""Local web UI for the Chan strategy MVP.

Run:
    python scripts/chan_strategy_system/ui.py

Then open:
    http://127.0.0.1:8765

The UI itself uses only the Python standard library. It launches
``backtest.py`` as a subprocess, so dependency or Tushare errors are shown in
the result panel instead of preventing the page from opening.
"""

from __future__ import annotations

import html
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
BACKTEST_SCRIPT = SCRIPT_DIR / "backtest.py"
CORE_SCRIPT = SCRIPT_DIR / "chan_core.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "reports" / "chan_strategy"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache" / "chan_strategy"
SYMBOL_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$", re.IGNORECASE)
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _layout(content: str, *, title: str = "Chan Strategy Console") -> bytes:
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)}</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #20242a;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #0f766e;
      --accent-dark: #0b5f59;
      --accent-soft: #e6f4f1;
      --danger: #b42318;
      --danger-soft: #fff0ed;
      --ok: #067647;
      --warn: #b54708;
      --code: #101828;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      line-height: 1.5;
    }}
    header {{
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    .wrap {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 22px 24px;
    }}
    h1 {{
      margin: 0;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .sub {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
    }}
    main .wrap {{
      display: grid;
      grid-template-columns: minmax(320px, 400px) 1fr;
      gap: 18px;
      align-items: start;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 16px;
      letter-spacing: 0;
    }}
    h3 {{
      margin: 18px 0 10px;
      font-size: 14px;
      letter-spacing: 0;
    }}
    label {{
      display: block;
      margin: 12px 0 6px;
      color: #344054;
      font-size: 13px;
      font-weight: 600;
    }}
    input, select {{
      width: 100%;
      height: 40px;
      border: 1px solid #cfd6e1;
      border-radius: 6px;
      padding: 0 11px;
      color: var(--text);
      background: #fff;
      font-size: 14px;
    }}
    input:focus, select:focus {{
      outline: 2px solid rgba(15, 118, 110, .18);
      border-color: var(--accent);
    }}
    .row {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    button {{
      width: 100%;
      height: 42px;
      margin-top: 16px;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }}
    button:hover {{ background: var(--accent-dark); }}
    details {{
      margin-top: 14px;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }}
    summary {{
      cursor: pointer;
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
    }}
    .hint {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .status {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfe;
    }}
    .metric b {{
      display: block;
      font-size: 13px;
      color: var(--muted);
      font-weight: 600;
    }}
    .metric span {{
      display: block;
      margin-top: 4px;
      font-size: 15px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .metric.good {{
      background: var(--accent-soft);
      border-color: #a7d7cf;
    }}
    .metric.bad {{
      background: var(--danger-soft);
      border-color: #f3b4aa;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      height: 26px;
      padding: 0 9px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--ok);
      font-size: 13px;
      font-weight: 700;
    }}
    .badge.bad {{
      background: var(--danger-soft);
      color: var(--danger);
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 12px 0 4px;
    }}
    .action-link {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 7px 10px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      color: var(--accent-dark);
      background: #fff;
      text-decoration: none;
      font-size: 13px;
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 8px;
      font-size: 13px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 8px 7px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: #475467;
      background: #f8fafc;
      font-weight: 700;
    }}
    pre {{
      min-height: 220px;
      max-height: 560px;
      overflow: auto;
      margin: 0;
      padding: 14px;
      border-radius: 8px;
      background: var(--code);
      color: #eef4ff;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .error {{
      color: var(--danger);
      font-weight: 700;
    }}
    .empty {{
      padding: 42px 16px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      color: var(--muted);
      text-align: center;
      background: #fbfcfe;
    }}
    .note {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 8px;
    }}
    .progress-wrap {{
      display: none;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin: 12px 0 0;
      background: #fbfcfe;
    }}
    .progress-meta {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
      color: #344054;
      font-size: 13px;
      font-weight: 700;
    }}
    .bar {{
      height: 10px;
      border-radius: 999px;
      background: #e4e7ec;
      overflow: hidden;
    }}
    .bar-fill {{
      width: 0%;
      height: 100%;
      border-radius: 999px;
      background: var(--accent);
      transition: width .25s ease;
    }}
    .progress-log {{
      max-height: 150px;
      min-height: 80px;
      margin-top: 10px;
      font-size: 12px;
    }}
    .chart-box {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      padding: 12px;
      margin: 8px 0 14px;
    }}
    .chart-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
      color: #344054;
      font-size: 13px;
      font-weight: 700;
    }}
    .legend {{
      display: flex;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }}
    .legend span::before {{
      content: "";
      display: inline-block;
      width: 18px;
      height: 3px;
      margin-right: 6px;
      vertical-align: middle;
      border-radius: 99px;
      background: var(--accent);
    }}
    .legend .bench::before {{ background: #667085; }}
    .chart-svg {{
      display: block;
      width: 100%;
      height: auto;
    }}
    .conclusion {{
      border: 1px solid #a7d7cf;
      border-radius: 8px;
      background: var(--accent-soft);
      padding: 14px;
      margin-bottom: 14px;
    }}
    .conclusion-title {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
      font-weight: 800;
    }}
    .conclusion p {{
      margin: 0;
      color: #344054;
      font-size: 14px;
    }}
    .groups {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 8px;
    }}
    .group {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
    }}
    .group h4 {{
      margin: 0 0 8px;
      font-size: 13px;
      color: #344054;
      letter-spacing: 0;
    }}
    .kv {{
      display: grid;
      grid-template-columns: minmax(80px, 1fr) minmax(80px, auto);
      gap: 6px 10px;
      font-size: 13px;
      border-top: 1px solid #eef2f6;
      padding-top: 8px;
    }}
    .kv div:nth-child(odd) {{ color: var(--muted); }}
    .kv div:nth-child(even) {{ font-weight: 700; text-align: right; }}
    .brief-list {{
      margin: 0;
      padding-left: 18px;
      color: #344054;
      font-size: 13px;
    }}
    .brief-list li {{
      margin: 5px 0;
    }}
    a {{ color: var(--accent-dark); }}
    @media (max-width: 840px) {{
      main .wrap {{ grid-template-columns: 1fr; }}
      .status {{ grid-template-columns: 1fr; }}
      .groups {{ grid-template-columns: 1fr; }}
    }}
  </style>
  <script>
    async function submitRun(event) {{
      event.preventDefault();
      const form = event.target;
      const button = form.querySelector("button[type=submit]");
      const progress = document.getElementById("progress-wrap");
      const fill = document.getElementById("progress-fill");
      const label = document.getElementById("progress-label");
      const pct = document.getElementById("progress-pct");
      const log = document.getElementById("progress-log");
      const result = document.getElementById("result-panel");
      button.disabled = true;
      button.textContent = "分析中...";
      progress.style.display = "block";
      fill.style.width = "5%";
      label.textContent = "提交任务";
      pct.textContent = "5%";
      log.textContent = "";
      result.innerHTML = `<h2>结果</h2><div class="empty">策略正在运行，完成后结果会自动显示在这里。</div>`;
      try {{
        const resp = await fetch("/run_async", {{ method: "POST", body: new URLSearchParams(new FormData(form)) }});
        const data = await resp.json();
        if (!data.job_id) throw new Error(data.error || "任务创建失败");
        pollJob(data.job_id, button);
      }} catch (err) {{
        label.textContent = "启动失败";
        log.textContent = String(err);
        button.disabled = false;
        button.textContent = "开始分析";
      }}
    }}

    async function pollJob(jobId, button) {{
      const fill = document.getElementById("progress-fill");
      const label = document.getElementById("progress-label");
      const pct = document.getElementById("progress-pct");
      const log = document.getElementById("progress-log");
      try {{
        const resp = await fetch(`/status?id=${{encodeURIComponent(jobId)}}`);
        const data = await resp.json();
        fill.style.width = `${{data.percent || 0}}%`;
        label.textContent = data.stage || "运行中";
        pct.textContent = `${{data.percent || 0}}%`;
        log.textContent = data.log_tail || "";
        if (data.done) {{
          button.disabled = false;
          button.textContent = "开始分析";
          const panel = document.getElementById("result-panel");
          const section = panel ? panel.closest("section") : null;
          if (section && data.html) {{
            section.outerHTML = data.html;
          }}
          return;
        }}
        setTimeout(() => pollJob(jobId, button), 1000);
      }} catch (err) {{
        label.textContent = "轮询失败";
        log.textContent = String(err);
        button.disabled = false;
        button.textContent = "开始分析";
      }}
    }}

    function syncVersionFields() {{
      const version = document.getElementById("analysis_version")?.value || "v2";
      const v1Field = document.getElementById("v1-base-field");
      const v2Field = document.getElementById("v2-freqs-field");
      const v2SpeedField = document.getElementById("v2-speed-field");
      if (v1Field) v1Field.style.display = version === "v1" ? "block" : "none";
      if (v2Field) {{
        v2Field.style.display = version === "v2" ? "block" : "none";
        v2Field.style.gridColumn = version === "v2" ? "1 / -1" : "auto";
      }}
      if (v2SpeedField) v2SpeedField.style.display = version === "v2" ? "block" : "none";
    }}

    document.addEventListener("DOMContentLoaded", syncVersionFields);
  </script>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>缠论策略分析控制台</h1>
      <div class="sub">输入股票代码即可运行第一版三买策略分析。示例：002202.SZ（金风科技）</div>
    </div>
  </header>
  <main>
    <div class="wrap">
      {content}
    </div>
  </main>
</body>
</html>"""
    return page.encode("utf-8")


def _form(values: dict[str, str] | None = None) -> str:
    values = values or {}
    symbol = values.get("symbol", "002202.SZ")
    mode = values.get("mode", "tushare")
    analysis_version = values.get("analysis_version", "v2")
    speed_mode = values.get("speed_mode", "quick")
    start_date = values.get("start_date", "20200101")
    end_date = values.get("end_date", _recent_weekday())
    backtest_start = values.get("backtest_start", "20200701")
    base_freq = values.get("base_freq", "30分钟")
    fq = values.get("fq", "后复权")
    return f"""
<section>
  <h2>输入</h2>
  <form method="post" action="/run" onsubmit="submitRun(event)">
    <label for="symbol">股票代码</label>
    <input id="symbol" name="symbol" value="{_escape(symbol)}" placeholder="例如 002202.SZ" autocomplete="off" required>

    <div class="row">
      <div>
        <label for="analysis_version">分析版本</label>
        <select id="analysis_version" name="analysis_version" onchange="syncVersionFields()">
          <option value="v2" {"selected" if analysis_version == "v2" else ""}>V2 主版本底座</option>
          <option value="v1" {"selected" if analysis_version == "v1" else ""}>V1 三买回测</option>
        </select>
      </div>
      <div>
        <label for="mode">数据模式</label>
        <select id="mode" name="mode">
          <option value="tushare" {"selected" if mode == "tushare" else ""}>Tushare真实数据</option>
          <option value="mock" {"selected" if mode == "mock" else ""}>Mock链路测试</option>
        </select>
      </div>
    </div>
    <div class="row">
      <div id="v1-base-field">
        <label for="base_freq">V1回测周期</label>
        <select id="base_freq" name="base_freq">
          <option value="30分钟" {"selected" if base_freq == "30分钟" else ""}>30分钟</option>
          <option value="60分钟" {"selected" if base_freq == "60分钟" else ""}>60分钟</option>
        </select>
      </div>
      <div id="v2-freqs-field">
        <label for="analysis_freqs">V2多级别分析周期</label>
        <input id="analysis_freqs" name="analysis_freqs" value="{_escape(values.get("analysis_freqs", "日线,60分钟,30分钟"))}" required>
      </div>
      <div id="v2-speed-field">
        <label for="speed_mode">V2运行模式</label>
        <select id="speed_mode" name="speed_mode">
          <option value="quick" {"selected" if speed_mode == "quick" else ""}>快速巡检：日常看持仓</option>
          <option value="standard" {"selected" if speed_mode == "standard" else ""}>完整研究：统计检验</option>
        </select>
      </div>
    </div>

    <details>
      <summary>高级参数</summary>
      <div class="row">
        <div>
          <label for="start_date">数据开始</label>
          <input id="start_date" name="start_date" value="{_escape(start_date)}" required>
        </div>
        <div>
          <label for="end_date">数据结束</label>
          <input id="end_date" name="end_date" value="{_escape(end_date)}" required>
        </div>
      </div>
      <div class="row">
        <div>
          <label for="backtest_start">回测开始</label>
          <input id="backtest_start" name="backtest_start" value="{_escape(backtest_start)}" required>
        </div>
        <div>
          <label for="fq">复权方式</label>
          <select id="fq" name="fq">
            <option value="后复权" {"selected" if fq == "后复权" else ""}>后复权</option>
            <option value="前复权" {"selected" if fq == "前复权" else ""}>前复权</option>
          </select>
        </div>
      </div>
    </details>

    <button type="submit">开始分析</button>
  </form>
  <div id="progress-wrap" class="progress-wrap">
    <div class="progress-meta">
      <span id="progress-label">等待开始</span>
      <span id="progress-pct">0%</span>
    </div>
    <div class="bar"><div id="progress-fill" class="bar-fill"></div></div>
    <pre id="progress-log" class="progress-log"></pre>
  </div>
  <div class="hint">真实数据模式需要你本机已设置 <code>TUSHARE_TOKEN</code>。页面不会接收或保存 token。V2 首次运行会缓存K线和信号；快速巡检适合日常看持仓，完整研究适合做统计检验。</div>
</section>
"""


def _recent_weekday() -> str:
    day = datetime.now()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.strftime("%Y%m%d")


def _result_panel(result: dict | None = None) -> str:
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not result:
        return f"""
<section>
  <div id="result-panel">
  <h2>结果</h2>
  <div class="status">
    <div class="metric"><b>Token</b><span>{"已配置，长度 " + str(len(token)) if token else "未配置"}</span></div>
    <div class="metric"><b>默认代码</b><span>002202.SZ</span></div>
    <div class="metric"><b>输出目录</b><span>{_escape(DEFAULT_OUTPUT_DIR)}</span></div>
  </div>
  <div class="empty">输入股票代码后，策略摘要、回测指标和报告链接会显示在这里。</div>
  </div>
</section>
"""

    ok = result.get("returncode") == 0 and result.get("run_dir")
    status_badge = '<span class="badge">运行成功</span>' if ok else '<span class="badge bad">运行失败</span>'
    log_html = _escape(result.get("log", ""))
    summary = result.get("summary", {})
    stats = result.get("stats", {})
    per_symbol = result.get("per_symbol", [])
    chart = result.get("chart", "")
    conclusion = result.get("conclusion", "")
    groups = result.get("metric_groups", {})
    quality = result.get("quality", [])
    pairs = result.get("pairs", [])
    files = result.get("files", {})
    raw_report = result.get("report_text", "")
    is_v2 = result.get("analysis_version") == "v2"
    v2_summary = result.get("v2_summary", {})
    v2_readable = result.get("v2_readable", {})
    v2_states = result.get("v2_states", [])
    v2_candidates = result.get("v2_candidates", [])
    v2_signal_stats = result.get("v2_signal_stats", [])

    cards = [
        ("Token", "已配置，长度 " + str(len(token)) if token else "未配置", ""),
        ("真实数据", "True" if result.get("real_data") else "False", "good" if result.get("real_data") else ""),
        ("样例数据", "True" if result.get("sample_data") else "False", "bad" if result.get("sample_data") else ""),
    ]
    if is_v2:
        cards.extend(
            [
                ("分析版本", "V2 Core", "good"),
                ("运行模式", v2_summary.get("speed_mode", "-"), ""),
                ("综合评分", v2_summary.get("score", "-"), ""),
                ("状态评级", v2_summary.get("rating", "-"), ""),
            ]
        )
    if per_symbol:
        row = per_symbol[0]
        cards.extend(
            [
                ("K线数量", row.get("bars", "-"), ""),
                ("交易次数", row.get("pairs", "-"), ""),
                ("持仓覆盖", row.get("nonzero_weight_rows", "-"), ""),
            ]
        )
    for key in ["交易胜率", "单笔收益", "持仓K线数", "多头占比", "品种数量"]:
        if key in stats:
            cards.append((key, stats[key], ""))

    cards_html = "".join(
        f'<div class="metric {klass}"><b>{_escape(label)}</b><span>{_escape(value)}</span></div>'
        for label, value, klass in cards
    )
    links = []
    if files.get("html"):
        links.append(f'<a class="action-link" href="/artifact?path={quote(files["html"])}" target="_blank">打开HTML回测报告</a>')
    if files.get("report"):
        links.append(f'<a class="action-link" href="/artifact?path={quote(files["report"])}" target="_blank">打开Markdown报告</a>')
    if files.get("weights"):
        links.append(f'<a class="action-link" href="/artifact?path={quote(files["weights"])}" target="_blank">下载持仓权重CSV</a>')
    if files.get("pairs"):
        links.append(f'<a class="action-link" href="/artifact?path={quote(files["pairs"])}" target="_blank">下载交易明细CSV</a>')
    if files.get("states"):
        links.append(f'<a class="action-link" href="/artifact?path={quote(files["states"])}" target="_blank">下载多级别状态CSV</a>')
    if files.get("candidates"):
        links.append(f'<a class="action-link" href="/artifact?path={quote(files["candidates"])}" target="_blank">下载候选信号CSV</a>')
    if files.get("signal_stats"):
        links.append(f'<a class="action-link" href="/artifact?path={quote(files["signal_stats"])}" target="_blank">下载信号统计CSV</a>')

    per_symbol_html = _table_from_rows(per_symbol) if per_symbol else '<div class="note">没有单票摘要。</div>'
    groups_html = _metric_groups_html(groups) if groups else '<div class="note">本次没有生成组合绩效，可能是没有非零持仓。</div>'
    quality_html = _quality_html(quality)
    pairs_html = _table_from_rows(pairs[:12]) if pairs else '<div class="note">暂无交易明细，可能本次没有完整开平仓对。</div>'
    v2_html = ""
    if is_v2:
        v2_html = f"""
  <h3>当前怎么看</h3>
  {_readable_html(v2_readable)}
  <h3>多级别结构状态</h3>
  {_table_from_rows(v2_states) if v2_states else '<div class="note">暂无多级别状态。</div>'}
  <h3>最近候选买卖点</h3>
  {_table_from_rows(v2_candidates) if v2_candidates else '<div class="note">最近没有识别到一买/二买/三买或卖点候选。</div>'}
  <h3>历史统计明细</h3>
  <div class="note">先读上方“历史统计”和“风险”两块白话解读，再看下面表格。表格里的收益是信号出现后的未来收益统计，用于找规律，不等同于已确认交易规则。</div>
  {_table_from_rows(v2_signal_stats) if v2_signal_stats else '<div class="note">暂无足够候选信号生成统计结果。</div>'}
"""
    default_conclusion = (
        "运行完成。请结合策略曲线、交易次数和数据质量进一步判断。"
        if ok
        else "运行失败。请展开运行日志查看具体错误；本次结果不应作为策略结论。"
    )
    conclusion_html = f"""
  <div class="conclusion">
    <div class="conclusion-title"><span>分析师结论</span>{status_badge}</div>
    <p>{_escape(conclusion or default_conclusion)}</p>
  </div>
"""

    return f"""
<section>
  <div id="result-panel">
  <h2>结果</h2>
  {conclusion_html}
  <div class="status">
    {cards_html}
  </div>
  <div class="actions">{"".join(links)}</div>
  {v2_html if is_v2 else f'''
  <h3>回测曲线</h3>
  {chart or '<div class="note">本次没有足够的持仓权重数据绘制曲线。</div>'}
  <h3>单票摘要</h3>
  {per_symbol_html}
  <h3>核心指标</h3>
  {groups_html}
  '''}
  <h3>数据质量</h3>
  {quality_html}
  {'' if is_v2 else f'<h3>最近交易明细</h3>{pairs_html}'}
  <details>
    <summary>查看原始报告文本</summary>
    <pre>{_escape(raw_report or "暂无报告文本。")}</pre>
  </details>
  <details>
    <summary>查看运行日志</summary>
    <pre>{log_html}</pre>
  </details>
  </div>
</section>
"""


def _read_post(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[0].strip() for key, values in parsed.items()}


def _validate_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise ValueError("股票代码格式应为 6位数字 + .SZ/.SH/.BJ，例如 002202.SZ")
    return symbol


def _build_backtest_args(form: dict[str, str]) -> list[str]:
    symbol = _validate_symbol(form.get("symbol", ""))
    mode = form.get("mode", "tushare")
    if mode not in {"tushare", "mock"}:
        raise ValueError("数据模式只能是 tushare 或 mock")
    analysis_version = form.get("analysis_version", "v2")
    if analysis_version not in {"v1", "v2"}:
        raise ValueError("分析版本只能是 v1 或 v2")

    if analysis_version == "v2":
        return [
            sys.executable,
            "-u",
            str(CORE_SCRIPT),
            "--mode",
            mode,
            "--symbols",
            symbol,
            "--start-date",
            form.get("start_date", "20200101"),
            "--end-date",
            form.get("end_date", "20240601"),
            "--backtest-start",
            form.get("backtest_start", "20200701"),
            "--analysis-freqs",
            form.get("analysis_freqs", "日线,60分钟,30分钟"),
            "--speed-mode",
            form.get("speed_mode", "quick"),
            "--fq",
            form.get("fq", "后复权"),
            "--cache-dir",
            str(DEFAULT_CACHE_DIR),
            "--output-dir",
            str(DEFAULT_OUTPUT_DIR),
        ]

    return [
        sys.executable,
        "-u",
        str(BACKTEST_SCRIPT),
        "--mode",
        mode,
        "--pool",
        "custom",
        "--symbols",
        symbol,
        "--start-date",
        form.get("start_date", "20200101"),
        "--end-date",
        form.get("end_date", "20240601"),
        "--backtest-start",
        form.get("backtest_start", "20200701"),
        "--base-freq",
        form.get("base_freq", "30分钟"),
        "--fq",
        form.get("fq", "后复权"),
        "--limit",
        "1",
        "--cache-dir",
        str(DEFAULT_CACHE_DIR),
        "--output-dir",
        str(DEFAULT_OUTPUT_DIR),
    ]


def _run_backtest(form: dict[str, str]) -> dict:
    args = _build_backtest_args(form)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=60 * 60,
        check=False,
    )
    cmd = " ".join(args)
    log_parts = [
        f"Command: {cmd}",
        "",
        "STDOUT:",
        proc.stdout.strip() or "(empty)",
    ]
    if proc.stderr.strip():
        log_parts.extend(["", "STDERR:", proc.stderr.strip()])
    log_parts.extend(["", f"Exit code: {proc.returncode}"])
    if proc.returncode != 0:
        log_parts.append("运行未成功。请先确认依赖已安装、TUSHARE_TOKEN 有效、网络/代理可用。")
    log = "\n".join(log_parts)
    run_dir = _extract_run_dir(proc.stdout)
    return {
        "analysis_version": form.get("analysis_version", "v2"),
        "returncode": proc.returncode,
        "log": log,
        "run_dir": str(run_dir) if run_dir else "",
        "real_data": "Real Tushare data used: True" in proc.stdout,
        "sample_data": "Sample data used/generated: True" in proc.stdout,
        **(_load_run_artifacts(run_dir) if run_dir else {}),
    }


def _start_job(form: dict[str, str]) -> str:
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "done": False,
            "percent": 5,
            "stage": "提交任务",
            "log": "",
            "html": "",
            "created": time.time(),
        }
    thread = threading.Thread(target=_job_worker, args=(job_id, form), daemon=True)
    thread.start()
    return job_id


def _job_worker(job_id: str, form: dict[str, str]) -> None:
    try:
        args = _build_backtest_args(form)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        _update_job(job_id, percent=8, stage="启动回测进程", append=f"Command: {' '.join(args)}\n")
        proc = subprocess.Popen(
            args,
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert proc.stdout is not None
        output_lines: list[str] = []
        for line in proc.stdout:
            output_lines.append(line)
            _update_progress_from_line(job_id, line)
        returncode = proc.wait()
        stdout = "".join(output_lines)
        log = "\n".join(
            [
                f"Command: {' '.join(args)}",
                "",
                "OUTPUT:",
                stdout.strip() or "(empty)",
                "",
                f"Exit code: {returncode}",
            ]
        )
        run_dir = _extract_run_dir(stdout)
        result = {
            "analysis_version": form.get("analysis_version", "v2"),
            "returncode": returncode,
            "log": log,
            "run_dir": str(run_dir) if run_dir else "",
            "real_data": "Real Tushare data used: True" in stdout,
            "sample_data": "Sample data used/generated: True" in stdout,
            **(_load_run_artifacts(run_dir) if run_dir else {}),
        }
        html_result = _result_panel(result)
        _update_job(job_id, done=True, percent=100, stage="完成" if returncode == 0 else "失败", html=html_result, log=log)
    except Exception as exc:  # noqa: BLE001
        html_result = _result_panel(
            {
                "analysis_version": form.get("analysis_version", "v2"),
                "returncode": 1,
                "log": f"{type(exc).__name__}: {exc}",
                "run_dir": "",
            }
        )
        _update_job(job_id, done=True, percent=100, stage="失败", html=html_result, append=f"\n{type(exc).__name__}: {exc}\n")


def _update_job(job_id: str, **updates) -> None:
    append = updates.pop("append", "")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        if append:
            job["log"] = (job.get("log", "") + append)[-12000:]
        for key, value in updates.items():
            job[key] = value


def _update_progress_from_line(job_id: str, line: str) -> None:
    text = line.strip()
    percent = None
    stage = None
    if "TUSHARE_TOKEN configured" in text:
        percent, stage = 12, "检查数据权限"
    if "[symbol] running" in text:
        percent, stage = 22, "准备标的与缓存"
    if "[v2-plan]" in text:
        percent, stage = 15, "V2任务规划完成"
    if "[v2] analyzing" in text:
        percent = _v2_task_percent(text, default=28)
        stage = _v2_stage_from_line(text, "V2多级别结构分析")
    if "[v2-fetch]" in text:
        percent, stage = 30, _v2_stage_from_line(text, "拉取Tushare K线")
    if "[v2-cache-hit]" in text:
        percent, stage = 32, _v2_stage_from_line(text, "读取本地缓存")
    if "[v2-format]" in text:
        percent, stage = 42, _v2_stage_from_line(text, "转换缠论K线")
    if "[v2-format-done]" in text:
        percent, stage = 50, _v2_stage_from_line(text, "缠论K线转换完成")
    if "[v2-quick-trim]" in text:
        percent, stage = 53, _v2_stage_from_line(text, "快速巡检窗口已生成")
    if "[v2-signals-cache-hit]" in text:
        percent, stage = 66, _v2_stage_from_line(text, "读取信号缓存")
    if "[v2-signals]" in text:
        percent, stage = 58, _v2_stage_from_line(text, "生成缠论信号")
    if "[v2-signals-cache-write]" in text:
        percent, stage = 68, _v2_stage_from_line(text, "写入信号缓存")
    if "[v2-signals-done]" in text:
        percent, stage = 70, _v2_stage_from_line(text, "缠论信号生成完成")
    if "[v2-done]" in text:
        percent = _v2_task_percent(text, default=75, done=True)
        stage = _v2_stage_from_line(text, "当前周期分析完成")
    if "[v2-error]" in text:
        percent, stage = 80, "V2分析遇到错误"
    if "RawBar(" in text:
        percent, stage = 52, "缠论K线样例已生成"
    if "[v2-all-done]" in text:
        percent, stage = 88, "V2结果汇总"
    if "[warn] no nonzero" in text:
        percent, stage = 82, "生成报告"
    if "Real Tushare data used" in text:
        percent, stage = 90, "整理回测结果"
    if "[done] report:" in text:
        percent, stage = 98, "报告已生成"
    kwargs = {"append": line}
    if percent is not None:
        kwargs["percent"] = percent
    if stage is not None:
        kwargs["stage"] = stage
    _update_job(job_id, **kwargs)


def _v2_task_percent(text: str, *, default: int, done: bool = False) -> int:
    match = re.search(r"task=(\d+)/(\d+)", text)
    if not match:
        return default
    current = int(match.group(1))
    total = max(int(match.group(2)), 1)
    base, span = 18, 64
    progress = current / total if done else (current - 0.5) / total
    return max(default, min(86, int(base + span * progress)))


def _v2_stage_from_line(text: str, fallback: str) -> str:
    match = re.search(
        r"(?:analyzing|hit\]|fetch\]|format\]|format-done\]|quick-trim\]|signals\]|signals-cache-hit\]|signals-cache-write\]|signals-done\]|done\])\s+([^ ]+)\s+([^ ]+)",
        text,
    )
    if not match:
        return fallback
    return f"{fallback}：{match.group(1)} {match.group(2)}"


def _extract_run_dir(stdout: str) -> Path | None:
    match = re.search(r"\[done\] report:\s*(.+?report\.md)", stdout)
    if not match:
        return None
    path = Path(match.group(1).strip())
    return path.parent if path.exists() else None


def _load_run_artifacts(run_dir: Path) -> dict:
    v2_summary_path = run_dir / "v2_summary.json"
    if v2_summary_path.exists():
        return _load_v2_artifacts(run_dir)

    report_path = run_dir / "report.md"
    stats_path = run_dir / "portfolio_stats.csv"
    per_symbol_path = run_dir / "per_symbol_summary.csv"
    html_path = run_dir / "portfolio_report.html"
    weights_path = run_dir / "portfolio_weights.csv"
    pairs_path = run_dir / "pairs.csv"
    report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    stats = _read_stats_csv(stats_path)
    per_symbol = _read_table_csv(per_symbol_path)
    weights = _read_table_csv(weights_path)
    pairs = _read_table_csv(pairs_path)
    quality = _build_quality_notes(stats, per_symbol, weights)
    return {
        "report_text": report_text,
        "summary": _parse_report_summary(report_text),
        "stats": stats,
        "per_symbol": per_symbol,
        "chart": _render_equity_chart(weights),
        "conclusion": _build_conclusion(stats, per_symbol, quality),
        "metric_groups": _build_metric_groups(stats, per_symbol),
        "quality": quality,
        "pairs": _compact_pairs(pairs),
        "files": {
            "report": str(report_path) if report_path.exists() else "",
            "html": str(html_path) if html_path.exists() else "",
            "weights": str(weights_path) if weights_path.exists() else "",
            "pairs": str(pairs_path) if pairs_path.exists() else "",
        },
    }


def _load_v2_artifacts(run_dir: Path) -> dict:
    report_path = run_dir / "v2_report.md"
    states_path = run_dir / "v2_latest_states.csv"
    candidates_path = run_dir / "v2_candidates.csv"
    stats_path = run_dir / "v2_signal_stats.csv"
    summary_path = run_dir / "v2_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    states = _read_table_csv(states_path)
    candidates = _read_table_csv(candidates_path)
    sig_stats = _read_table_csv(stats_path)
    readable = summary.get("readable", {})
    if sig_stats:
        readable = _ensure_v2_reading_guide(readable, sig_stats)
    report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    return {
        "analysis_version": "v2",
        "report_text": report_text,
        "v2_summary": summary,
        "v2_readable": readable,
        "v2_states": states,
        "v2_candidates": _compact_v2_candidates(candidates),
        "v2_signal_stats": _compact_v2_stats(sig_stats),
        "conclusion": _build_v2_conclusion(summary),
        "quality": [
            {"level": "说明", "message": "V2 Core 是结构与信号研究底座；它用于发现假设，不等同于最终实盘交易策略。"},
            {"level": "口径", "message": summary.get("scope_note", "请结合运行模式判断统计范围。")},
        ],
        "files": {
            "report": str(report_path) if report_path.exists() else "",
            "states": str(states_path) if states_path.exists() else "",
            "candidates": str(candidates_path) if candidates_path.exists() else "",
            "signal_stats": str(stats_path) if stats_path.exists() else "",
        },
    }


def _read_stats_csv(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    stats = {}
    for row in rows[1:]:
        if len(row) >= 2:
            stats[row[0]] = row[1]
    return stats


def _read_table_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _compact_v2_candidates(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    preferred = ["symbol", "freq", "dt", "close", "signal_type", "signal_value"]
    output = []
    for row in rows[-30:][::-1]:
        output.append({key: row.get(key, "") for key in preferred if key in row})
    return output


def _compact_v2_stats(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    preferred = [
        "symbol",
        "freq",
        "signal_type",
        "count",
        "ret_5b_mean",
        "ret_5b_win_rate",
        "ret_10b_mean",
        "ret_10b_win_rate",
        "ret_20b_mean",
        "ret_20b_win_rate",
    ]
    output = []
    sorted_rows = sorted(rows, key=lambda x: int(float(x.get("count") or 0)), reverse=True)
    for row in sorted_rows[:30]:
        output.append({key: row.get(key, "") for key in preferred if key in row})
    return output


def _build_v2_conclusion(summary: dict) -> str:
    if not summary:
        return "V2 Core 已运行，但未生成综合摘要；请查看多级别状态和日志定位原因。"
    return (
        f"V2 Core 综合评分 {summary.get('score', '-')}，状态为{summary.get('rating', '-')}。"
        f"{summary.get('action', '')} 主要依据：{summary.get('reason', '暂无')}"
    )


def _parse_report_summary(text: str) -> dict:
    summary = {}
    for line in text.splitlines():
        if line.startswith("- ") and ": " in line:
            key, _, value = line[2:].partition(": ")
            summary[key.strip()] = value.strip("` ")
    return summary


def _table_from_rows(rows: list[dict]) -> str:
    if not rows:
        return ""
    columns = list(rows[0].keys())
    head = "".join(f"<th>{_escape(col)}</th>" for col in columns)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{_escape(row.get(col, ''))}</td>" for col in columns) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _metric_groups_html(groups: dict[str, dict]) -> str:
    chunks = []
    for title, items in groups.items():
        kv = "".join(f"<div>{_escape(k)}</div><div>{_escape(v)}</div>" for k, v in items.items())
        chunks.append(f'<div class="group"><h4>{_escape(title)}</h4><div class="kv">{kv}</div></div>')
    return f'<div class="groups">{"".join(chunks)}</div>'


def _quality_html(notes: list[dict]) -> str:
    if not notes:
        return '<div class="note">暂无数据质量提示。</div>'
    rows = [{"级别": n.get("level", ""), "提示": n.get("message", "")} for n in notes]
    return _table_from_rows(rows)


def _ensure_v2_reading_guide(readable: dict, stats_rows: list[dict]) -> dict:
    readable = dict(readable or {})
    existing = [str(x) for x in readable.get("signal_stats", []) if str(x).strip()]
    guide = [
        "阅读历史统计时，先看样本数 count，再看未来10根平均收益，最后看胜率；样本少于10次的信号只当线索，不当结论。",
        _v2_signal_coverage_from_rows(stats_rows),
    ]
    if not any("覆盖情况" in item for item in existing):
        readable["signal_stats"] = guide + existing
    return readable


def _v2_signal_coverage_from_rows(rows: list[dict]) -> str:
    counts = {key: 0 for key in ["一买", "二买", "三买", "一卖", "二卖", "三卖"]}
    for row in rows:
        sig_type = str(row.get("signal_type", ""))
        if sig_type not in counts:
            continue
        try:
            counts[sig_type] += int(float(row.get("count") or 0))
        except (TypeError, ValueError):
            pass
    buy = "，".join(f"{k} {counts[k]} 次" for k in ["一买", "二买", "三买"])
    sell = "，".join(f"{k} {counts[k]} 次" for k in ["一卖", "二卖", "三卖"])
    return f"信号覆盖情况：买点包括 {buy}；卖点包括 {sell}。如果一买没有出现在摘要前列，通常是样本少或收益排序不靠前，并不代表系统没有统计。"


def _readable_html(readable: dict) -> str:
    if not readable:
        return '<div class="note">暂无白话解读。请查看原始报告文本。</div>'
    sections = [
        ("一句话", [readable.get("one_liner", "")]),
        ("结构", readable.get("structure", [])),
        ("最近信号", readable.get("recent_signals", [])),
        ("历史统计", readable.get("signal_stats", [])),
        ("风险", readable.get("risk", [])),
        ("下一步", readable.get("next_steps", [])),
    ]
    chunks = []
    for title, items in sections:
        clean = [str(x) for x in items if str(x).strip()]
        if not clean:
            continue
        body = "".join(f"<li>{_escape(item)}</li>" for item in clean)
        chunks.append(f'<div class="group"><h4>{_escape(title)}</h4><ul class="brief-list">{body}</ul></div>')
    return f'<div class="groups">{"".join(chunks)}</div>' if chunks else '<div class="note">暂无白话解读。</div>'


def _build_metric_groups(stats: dict, per_symbol: list[dict]) -> dict[str, dict]:
    row = per_symbol[0] if per_symbol else {}
    return {
        "收益能力": {
            "年化": stats.get("年化", "-"),
            "绝对收益": stats.get("绝对收益", "-"),
            "单笔收益": stats.get("单笔收益", "-"),
            "夏普": stats.get("夏普", "-"),
        },
        "风险控制": {
            "最大回撤": stats.get("最大回撤", "-"),
            "年化波动率": stats.get("年化波动率", "-"),
            "回撤风险": stats.get("回撤风险", "-"),
            "持仓K线数": stats.get("持仓K线数", "-"),
        },
        "交易质量": {
            "交易胜率": stats.get("交易胜率", "-"),
            "交易次数": row.get("pairs", "-"),
            "多头占比": stats.get("多头占比", "-"),
            "非零覆盖": stats.get("非零覆盖", "-"),
        },
        "样本规模": {
            "K线数量": row.get("bars", "-"),
            "持仓记录": row.get("holds", "-"),
            "持仓覆盖行": row.get("nonzero_weight_rows", "-"),
            "品种数量": stats.get("品种数量", "-"),
        },
    }


def _build_quality_notes(stats: dict, per_symbol: list[dict], weights: list[dict]) -> list[dict]:
    notes = []
    row = per_symbol[0] if per_symbol else {}
    pairs = _to_float(row.get("pairs"))
    bars = _to_float(row.get("bars"))
    nonzero = _to_float(row.get("nonzero_weight_rows"))
    if bars is not None and bars < 1000:
        notes.append({"level": "注意", "message": "K线数量偏少，策略结论可能不稳定。"})
    if pairs is not None and pairs < 20:
        notes.append({"level": "注意", "message": "完整交易次数少于20笔，胜率和收益指标参考价值有限。"})
    if nonzero is not None and nonzero == 0:
        notes.append({"level": "警告", "message": "本次没有非零持仓，说明策略没有触发有效交易。"})
    if weights:
        first_dt = weights[0].get("dt", "")
        if str(first_dt).startswith("1970"):
            notes.append({"level": "注意", "message": "持仓权重时间戳显示为1970，可能来自旧版CZSC结果格式；建议重新运行生成修正后的结果。"})
    if not notes:
        notes.append({"level": "正常", "message": "真实数据已使用，样本规模和交易记录可用于初步阅读。"})
    return notes


def _build_conclusion(stats: dict, per_symbol: list[dict], quality: list[dict]) -> str:
    row = per_symbol[0] if per_symbol else {}
    symbol = row.get("symbol", "当前标的")
    win_rate = _to_float(stats.get("交易胜率"))
    avg_bp = _to_float(stats.get("单笔收益"))
    pairs = _to_float(row.get("pairs"))
    long_ratio = _to_float(stats.get("多头占比"))
    if pairs is not None and pairs < 20:
        return f"{symbol} 本次交易样本偏少，建议先作为链路验证和观察信号，不宜直接据此实盘。"
    if avg_bp is None:
        return f"{symbol} 已完成回测，但缺少单笔收益指标；请优先查看曲线和交易明细。"
    quality_text = "较好" if avg_bp > 0 and (win_rate or 0) >= 0.35 else "一般"
    action = "可作为候选观察信号继续跟踪" if avg_bp > 0 else "暂不建议作为独立买卖依据"
    details = []
    if win_rate is not None:
        details.append(f"交易胜率 {win_rate:.2%}")
    details.append(f"单笔收益 {avg_bp}")
    if long_ratio is not None:
        details.append(f"多头占比 {long_ratio:.2%}")
    return f"{symbol} 的三买策略信号质量{quality_text}，{action}；" + "，".join(details) + "。"


def _compact_pairs(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    preferred = [
        "symbol",
        "标的代码",
        "交易方向",
        "开仓时间",
        "平仓时间",
        "开仓价格",
        "平仓价格",
        "盈亏比例",
        "持仓K线数",
        "事件序列",
        "pos_name",
    ]
    output = []
    for row in rows[-20:][::-1]:
        compact = {k: row[k] for k in preferred if k in row and row[k] != ""}
        if not compact:
            keys = list(row.keys())[:8]
            compact = {k: row.get(k, "") for k in keys}
        output.append(compact)
    return output


def _to_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _render_equity_chart(rows: list[dict]) -> str:
    points = _build_equity_points(rows)
    if len(points) < 2:
        return ""
    strategy = [(i, row["equity"]) for i, row in enumerate(points)]
    benchmark = [(i, row["benchmark"]) for i, row in enumerate(points)]
    width, height = 760, 260
    pad_left, pad_right, pad_top, pad_bottom = 52, 18, 18, 34
    all_values = [v for _, v in strategy + benchmark if v == v]
    ymin, ymax = min(all_values), max(all_values)
    if ymin == ymax:
        ymin -= 0.01
        ymax += 0.01
    span = ymax - ymin
    ymin -= span * 0.08
    ymax += span * 0.08

    def scale_x(x: int) -> float:
        usable = width - pad_left - pad_right
        return pad_left + (usable * x / max(len(points) - 1, 1))

    def scale_y(y: float) -> float:
        usable = height - pad_top - pad_bottom
        return pad_top + usable * (1 - (y - ymin) / (ymax - ymin))

    strat_path = _polyline(strategy, scale_x, scale_y)
    bench_path = _polyline(benchmark, scale_x, scale_y)
    y_ticks = _ticks(ymin, ymax, 5)
    grid = []
    labels = []
    for tick in y_ticks:
        y = scale_y(tick)
        grid.append(f'<line x1="{pad_left}" y1="{y:.2f}" x2="{width-pad_right}" y2="{y:.2f}" stroke="#e4e7ec" stroke-width="1"/>')
        labels.append(f'<text x="{pad_left-8}" y="{y+4:.2f}" text-anchor="end" font-size="11" fill="#667085">{tick:.2f}</text>')
    x_labels = [
        (pad_left, "开始"),
        ((width - pad_left - pad_right) / 2 + pad_left, "中段"),
        (width - pad_right, "结束"),
    ]
    x_label_svg = "".join(
        f'<text x="{x:.2f}" y="{height-10}" text-anchor="middle" font-size="11" fill="#667085">{label}</text>'
        for x, label in x_labels
    )
    last_equity = points[-1]["equity"]
    last_benchmark = points[-1]["benchmark"]
    svg = f"""
<div class="chart-box">
  <div class="chart-head">
    <span>策略净值曲线</span>
    <span class="legend"><span>策略</span><span class="bench">标的价格</span></span>
  </div>
  <svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" aria-label="策略净值曲线">
    <rect x="0" y="0" width="{width}" height="{height}" fill="#fbfcfe"/>
    {''.join(grid)}
    {''.join(labels)}
    <line x1="{pad_left}" y1="{pad_top}" x2="{pad_left}" y2="{height-pad_bottom}" stroke="#d0d5dd"/>
    <line x1="{pad_left}" y1="{height-pad_bottom}" x2="{width-pad_right}" y2="{height-pad_bottom}" stroke="#d0d5dd"/>
    <polyline points="{bench_path}" fill="none" stroke="#667085" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" opacity=".85"/>
    <polyline points="{strat_path}" fill="none" stroke="#0f766e" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
    {x_label_svg}
  </svg>
  <div class="note">期末策略净值：{last_equity:.4f}；标的价格归一化：{last_benchmark:.4f}。曲线基于持仓权重和价格序列近似计算，用于快速观察。</div>
</div>
"""
    return svg


def _build_equity_points(rows: list[dict]) -> list[dict]:
    cleaned = []
    for row in rows:
        try:
            cleaned.append(
                {
                    "dt": row.get("dt", ""),
                    "price": float(row.get("price", 0) or 0),
                    "weight": float(row.get("weight", 0) or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    cleaned = [row for row in cleaned if row["price"] > 0]
    if len(cleaned) < 2:
        return []
    first_price = cleaned[0]["price"]
    equity = 1.0
    points = [{"equity": equity, "benchmark": 1.0}]
    prev_price = cleaned[0]["price"]
    prev_weight = cleaned[0]["weight"]
    for row in cleaned[1:]:
        price = row["price"]
        ret = price / prev_price - 1
        equity *= 1 + prev_weight * ret
        points.append({"equity": equity, "benchmark": price / first_price})
        prev_price = price
        prev_weight = row["weight"]
    return _downsample(points, 360)


def _downsample(points: list[dict], limit: int) -> list[dict]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def _polyline(points: list[tuple[int, float]], scale_x, scale_y) -> str:
    return " ".join(f"{scale_x(x):.2f},{scale_y(y):.2f}" for x, y in points)


def _ticks(ymin: float, ymax: float, count: int) -> list[float]:
    if count <= 1:
        return [ymin]
    step = (ymax - ymin) / (count - 1)
    return [ymin + i * step for i in range(count)]


def _serve_artifact(handler: BaseHTTPRequestHandler) -> None:
    parsed = urlparse(handler.path)
    target = Path(unquote(parse_qs(parsed.query).get("path", [""])[0]))
    allowed = DEFAULT_OUTPUT_DIR.resolve()
    try:
        resolved = target.resolve()
    except OSError:
        handler.send_error(404)
        return
    if not str(resolved).startswith(str(allowed)) or not resolved.exists() or not resolved.is_file():
        handler.send_error(404)
        return
    suffix = resolved.suffix.lower()
    content_type = {
        ".html": "text/html; charset=utf-8",
        ".md": "text/plain; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }.get(suffix, "application/octet-stream")
    data = resolved.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/artifact?"):
            _serve_artifact(self)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/status":
            job_id = parse_qs(parsed.query).get("id", [""])[0]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id, {}))
            if not job:
                self._send_json({"done": True, "percent": 100, "stage": "任务不存在", "log_tail": "", "html": ""}, status=404)
                return
            self._send_json(
                {
                    "done": bool(job.get("done")),
                    "percent": int(job.get("percent", 0)),
                    "stage": job.get("stage", "运行中"),
                    "log_tail": str(job.get("log", ""))[-5000:],
                    "html": job.get("html", "") if job.get("done") else "",
                }
            )
            return
        self._send(_layout(_form() + _result_panel()))

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/run_async":
            form = _read_post(self)
            try:
                job_id = _start_job(form)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"任务创建失败：{type(exc).__name__}: {exc}"}, status=400)
                return
            self._send_json({"job_id": job_id})
            return
        if self.path != "/run":
            self.send_error(404)
            return
        form = _read_post(self)
        try:
            result = _run_backtest(form)
        except Exception as exc:  # noqa: BLE001
            result = {
                "analysis_version": form.get("analysis_version", "v2"),
                "returncode": 1,
                "log": f"输入或运行失败：{type(exc).__name__}: {exc}",
                "run_dir": "",
            }
        self._send(_layout(_form(form) + _result_panel(result)))

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[ui] {self.address_string()} - {fmt % args}")

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = "127.0.0.1"
    port = int(os.environ.get("CHAN_STRATEGY_UI_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Chan strategy UI: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
