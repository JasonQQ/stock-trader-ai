#!/usr/bin/env python3
"""
StockTrader-AI Web Dashboard
按钮触发脚本，展示选股表格与资金曲线。
用法: streamlit run app.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
PYTHON = sys.executable or "python3"


def _load_account_manager():
    """按文件路径加载，避免 Streamlit 热重载缓存旧 account_manager。"""
    path = SCRIPTS / "account_manager.py"
    spec = importlib.util.spec_from_file_location("stock_trader_account_manager", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载账户模块: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_am = _load_account_manager()
ensure_migrated = _am.ensure_migrated
list_accounts = _am.list_accounts
all_account_ids = _am.all_account_ids
load_account_data = _am.load_account_data
create_account = _am.create_account
delete_account = _am.delete_account

st.set_page_config(
    page_title="StockTrader-AI",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 样式（浅色高对比，避免暗色与 Streamlit 控件打架）────────────────────────
st.markdown(
    """
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap');

  :root {
    --bg: #f4f7fb;
    --panel: #ffffff;
    --line: #d7e0ea;
    --text: #1b2430;
    --muted: #5b6b7c;
    --accent: #0b6e4f;
    --accent-soft: #e6f4ef;
    --up: #0a7a43;
    --down: #c62828;
    --shadow: 0 1px 2px rgba(27, 36, 48, 0.04), 0 4px 16px rgba(27, 36, 48, 0.06);
  }

  html, body, [class*="css"] {
    font-family: "IBM Plex Sans", "PingFang SC", "Hiragino Sans GB", sans-serif;
    color: var(--text);
  }

  .stApp {
    background:
      linear-gradient(180deg, #eef3f8 0%, var(--bg) 220px);
    color: var(--text);
  }

  .stApp, .stApp p, .stApp span, .stApp label, .stApp li,
  [data-testid="stMarkdownContainer"], [data-testid="stCaption"] {
    color: var(--text);
  }

  [data-testid="stSidebar"] {
    background: #ffffff;
    border-right: 1px solid var(--line);
  }
  [data-testid="stSidebar"] * {
    color: var(--text) !important;
  }
  [data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small {
    color: var(--muted) !important;
  }

  h1, h2, h3, h4 {
    color: var(--text) !important;
    letter-spacing: -0.02em;
  }

  .brand {
    font-size: 1.7rem;
    font-weight: 700;
    color: var(--text);
    margin: 0 0 0.2rem 0;
  }
  .brand span { color: var(--accent); }
  .sub {
    color: var(--muted);
    font-size: 0.95rem;
    margin-bottom: 1.25rem;
  }

  .metric-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 1rem 1.1rem;
    box-shadow: var(--shadow);
    min-height: 88px;
  }
  .metric-card .label {
    color: var(--muted);
    font-size: 0.8rem;
    font-weight: 500;
    letter-spacing: 0.02em;
  }
  .metric-card .value {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 1.3rem;
    font-weight: 600;
    color: var(--text);
    margin-top: 0.35rem;
    word-break: break-word;
  }
  .up { color: var(--up) !important; }
  .down { color: var(--down) !important; }

  /* 主按钮：深绿，白字，对比清晰 */
  .stButton > button[kind="primary"],
  .stButton > button[data-testid="baseButton-primary"] {
    background: var(--accent) !important;
    color: #ffffff !important;
    border: 1px solid #095c42 !important;
    font-weight: 600;
  }
  .stButton > button[kind="primary"]:hover,
  .stButton > button[data-testid="baseButton-primary"]:hover {
    background: #095c42 !important;
    border-color: #084c37 !important;
    color: #ffffff !important;
  }

  /* 次要按钮 */
  .stButton > button {
    background: #ffffff !important;
    color: var(--text) !important;
    border: 1px solid var(--line) !important;
    font-weight: 500;
  }
  .stButton > button:hover {
    background: var(--accent-soft) !important;
    border-color: #9ec9b8 !important;
    color: var(--text) !important;
  }

  div[data-testid="stDataFrame"] {
    border: 1px solid var(--line);
    border-radius: 10px;
    background: #ffffff;
    box-shadow: var(--shadow);
  }

  [data-testid="stExpander"] {
    background: #ffffff;
    border: 1px solid var(--line);
    border-radius: 10px;
  }

  /* 输入控件文字可读 */
  .stSlider label, .stNumberInput label, .stDateInput label {
    color: var(--text) !important;
    font-weight: 500;
  }
</style>
""",
    unsafe_allow_html=True,
)


def _child_env(account_id: str | None = None) -> dict[str, str]:
    """给子脚本干净环境。Streamlit 若从 Cursor 启动会带 HTTP_PROXY，行情 curl 会被代理吃掉。"""
    import os

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    for key in list(env):
        if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "ftp_proxy"):
            env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    if account_id:
        env["STOCK_ACCOUNT_ID"] = account_id
    return env


# ── 脚本执行 ──────────────────────────────────────────────────────────────────
def run_script(
    args: list[str],
    *,
    timeout: int = 300,
    stdin_text: str | None = None,
    account_id: str | None = None,
) -> tuple[Any | None, str, str, int]:
    """运行 scripts/ 下命令，解析 stdout JSON。返回 (data, stdout, stderr, code)。"""
    script = args[0]
    rest = list(args[1:])
    # simulate_trend / daily_report 支持 --account；其余脚本用环境变量
    if account_id and script in ("simulate_trend.py", "daily_report.py", "health_check.py"):
        cmd = [PYTHON, str(SCRIPTS / script), "--account", account_id, *rest]
    else:
        cmd = [PYTHON, str(SCRIPTS / script), *rest]
    env = _child_env(account_id)
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return None, "", f"超时（>{timeout}s）", 124
    except Exception as e:
        return None, "", str(e), 1

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    data = None
    text = stdout.strip()
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 日报等 Markdown 输出
            data = {"_raw": text}
    return data, stdout, stderr, proc.returncode


def load_account(account_id: str | None = None) -> dict | None:
    """加载指定账户；默认使用页面当前查看的账户。"""
    ensure_migrated()
    ids = all_account_ids()
    aid = account_id or st.session_state.get("view_account_id")
    if not aid:
        aid = ids[0] if ids else None
    if not aid:
        return None
    try:
        return load_account_data(aid)
    except Exception:
        return None


def to_quote_code(code: str) -> str:
    """纯数字代码 → 腾讯行情代码（sh/sz）。"""
    c = (code or "").strip().lower()
    if c.startswith(("sh", "sz")):
        return c
    c = c.lstrip("shsz")
    if c.startswith("6") or c.startswith("5"):
        return f"sh{c}"
    return f"sz{c}"


def fetch_quote(code: str) -> dict | None:
    data, _, err, rc = run_script(["get_price.py", to_quote_code(code)], timeout=15)
    if rc != 0 or not isinstance(data, dict) or data.get("error"):
        return None
    return data


def fetch_live_status(account_id: str) -> dict | None:
    """拉取账户实时市值状态（simulate_trend status，按现价计价）。"""
    data, _, _, rc = run_script(
        ["simulate_trend.py", "status"],
        timeout=45,
        account_id=account_id,
    )
    if not isinstance(data, dict) or data.get("error"):
        return None
    return data


def parse_pct(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def render_account_switcher():
    st.markdown("### 账户")
    st.caption("Cron 会同时监控全部账户；此处切换仅影响页面展示的状态与风控。")
    ensure_migrated()
    accounts = list_accounts()

    if "view_account_id" not in st.session_state:
        st.session_state.view_account_id = accounts[0]["id"] if accounts else "default"

    if not accounts:
        st.warning("还没有账户，请先创建。")
        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            new_id = st.text_input("账户 ID", value="default", key="new_acct_id")
        with c2:
            new_name = st.text_input("显示名称", value="默认账户", key="new_acct_name")
        with c3:
            new_cash = st.number_input("初始资金", value=100000.0, step=10000.0, key="new_acct_cash")
        if st.button("创建账户", type="primary"):
            try:
                create_account(new_id, name=new_name, cash=float(new_cash))
                st.session_state.view_account_id = new_id
                st.success(f"已创建 {new_name}")
                st.rerun()
            except Exception as e:
                st.error(str(e))
        return

    # 用实时行情刷新各账户收益/回撤摘要
    live_map = st.session_state.get("accounts_live_map") or {}
    if st.session_state.get("refresh_accounts_live") or not live_map:
        with st.spinner("按实时行情更新各账户市值…"):
            live_map = {}
            for a in accounts:
                st_live = fetch_live_status(a["id"])
                if st_live:
                    live_map[a["id"]] = st_live
            st.session_state["accounts_live_map"] = live_map
            st.session_state["refresh_accounts_live"] = False

    labels = {}
    for a in accounts:
        live = live_map.get(a["id"]) or {}
        ret = parse_pct(live.get("total_return_pct", a.get("return_pct", 0)))
        labels[a["id"]] = f"{a.get('name') or a['id']} · 收益 {ret:+.1f}%"
    ids = list(labels.keys())
    view_id = st.session_state.view_account_id
    if view_id not in ids:
        view_id = ids[0]
        st.session_state.view_account_id = view_id

    r1, r2 = st.columns([4, 1])
    with r1:
        choice = st.selectbox(
            "查看账户",
            ids,
            index=ids.index(view_id),
            format_func=lambda i: labels[i],
            key="acct_select",
        )
        if choice != st.session_state.view_account_id:
            st.session_state.view_account_id = choice
            st.rerun()
    with r2:
        st.write("")
        st.write("")
        if st.button("刷新行情", use_container_width=True):
            st.session_state["refresh_accounts_live"] = True
            st.session_state.pop("accounts_live_map", None)
            st.rerun()

    summary_rows = []
    for a in accounts:
        live = live_map.get(a["id"]) or {}
        summary_rows.append({
            "ID": a["id"],
            "名称": a.get("name"),
            "查看中": "●" if a["id"] == st.session_state.view_account_id else "",
            "总资产": live.get("total_assets", a.get("total_assets")),
            "市值": live.get("market_value"),
            "收益%": parse_pct(live.get("total_return_pct", a.get("return_pct"))),
            "回撤%": parse_pct(live.get("drawdown_from_peak", a.get("drawdown_pct"))),
            "实时": "是" if live.get("priced_live") else ("—" if not a.get("positions_count") else "否"),
            "持仓数": live.get("positions_count", a.get("positions_count")),
            "环境": a.get("regime"),
            "龙头风格": a.get("leader_style") or "-",
        })
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    with st.expander("新建 / 删除账户"):
        n1, n2, n3, n4 = st.columns([2, 2, 2, 1])
        with n1:
            nid = st.text_input("新账户 ID", value="strategy_b", key="create_id")
        with n2:
            nname = st.text_input("名称", value="策略B", key="create_name")
        with n3:
            ncash = st.number_input("资金", value=100000.0, step=10000.0, key="create_cash")
        with n4:
            st.write("")
            st.write("")
            do_create = st.button("新建", use_container_width=True)
        if do_create:
            try:
                create_account(nid, name=nname, cash=float(ncash))
                st.session_state.view_account_id = nid
                st.success(f"已创建 {nname}（Cron 将自动纳入监控）")
                st.rerun()
            except Exception as e:
                st.error(str(e))

        if len(ids) > 1:
            d1, d2 = st.columns([3, 1])
            with d1:
                did = st.selectbox("删除账户", ids, key="del_id")
            with d2:
                st.write("")
                st.write("")
                if st.button("删除", use_container_width=True):
                    try:
                        delete_account(did)
                        remain = [i for i in ids if i != did]
                        st.session_state.view_account_id = remain[0] if remain else "default"
                        st.success(f"已删除 {did}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
        else:
            st.caption("至少保留一个账户。")


# ── 表格辅助 ──────────────────────────────────────────────────────────────────
CANDIDATE_COLS = {
    "code": "代码",
    "name": "名称",
    "price": "现价",
    "change_pct": "涨幅%",
    "turnover": "换手%",
    "amount": "成交额(亿)",
    "pe": "PE",
    "sector": "板块",
    "pre_score": "预评分",
    "tech_score": "技术分",
    "total_score": "总分",
    "rank": "排名",
    "enhanced_bonus": "增强加分",
}

SECTOR_COLS = {
    "rank": "排名",
    "name": "行业",
    "avg_change": "涨幅%",
    "style": "风格",
    "cap": "市值",
    "up_ratio": "上涨比",
}


def flatten_candidates(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    flat = []
    for r in rows:
        item = {k: r.get(k) for k in (
            "rank", "code", "name", "price",
            "turnover", "amount", "pe", "sector", "pre_score",
            "tech_score", "total_score", "enhanced_bonus",
        ) if k in r or k in ("rank", "code", "name")}
        # screener 用 change，scorer 用 change_pct；只保留一列避免重名
        if "change_pct" in r and r.get("change_pct") is not None:
            item["change_pct"] = r["change_pct"]
        elif "change" in r and r.get("change") is not None:
            item["change_pct"] = r["change"]
        tech = r.get("tech_detail") or {}
        if tech:
            item["均线多头"] = tech.get("ma_aligned")
            item["MACD+"] = tech.get("macd_positive")
            item["量比"] = tech.get("vol_ratio")
            item["相对强度"] = tech.get("rs_ratio")
        enh = r.get("enhanced_signals") or {}
        if enh:
            item["增强信号"] = ", ".join(f"{k}:{v}" for k, v in enh.items())
        top_f = r.get("top_factors") or []
        if top_f:
            item["优势因子"] = ", ".join(
                f"{list(f.keys())[0]}={list(f.values())[0]:.0f}" for f in top_f if f
            )
        flat.append(item)
    df = pd.DataFrame(flat)
    rename = {k: v for k, v in CANDIDATE_COLS.items() if k in df.columns}
    return df.rename(columns=rename)


def df_sectors(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).copy()
    if "avg_change" not in df.columns and "change" in df.columns:
        df = df.rename(columns={"change": "avg_change"})
    elif "avg_change" in df.columns and "change" in df.columns:
        df = df.drop(columns=["change"])
    rename = {k: v for k, v in SECTOR_COLS.items() if k in df.columns}
    cols = [c for c in rename if c in df.columns]
    return df[cols].rename(columns=rename)


def metric_html(label: str, value: str, tone: str = "") -> str:
    cls = f" value {tone}".rstrip()
    return (
        f'<div class="metric-card"><div class="label">{label}</div>'
        f'<div class="{cls}">{value}</div></div>'
    )


def fetch_cron_status() -> dict:
    data, _, err, code = run_script(["cron_runner.py", "--status"], timeout=30)
    if isinstance(data, dict) and "jobs" in data:
        return data
    return {
        "error": err or "无法读取调度状态",
        "crontab_installed": False,
        "jobs": [],
        "trading_day": False,
        "trading_reason": "-",
        "time": "",
        "weekday": "",
        "monitor_planned_today": 0,
        "monitor_runs_today": 0,
    }


STATUS_LABEL = {
    "ok": "✅ 成功",
    "skip": "⏭️ 跳过",
    "error": "❌ 失败",
    None: "—",
}
DUE_LABEL = {
    "pending": "待执行",
    "in_progress": "进行中",
    "done_today": "今日已排完",
    "missed": "有漏跑",
    "missed_in_progress": "进行中(有漏跑)",
    "holiday_skip": "非交易日",
    "idle": "—",
}


RISK_LAYER_DEFS = [
    {
        "key": "drawdown",
        "层级": "P0",
        "机制": "组合回撤熔断",
        "触发条件": "峰值总资产回撤 ≥ 10%",
        "执行动作": "全部清仓 + 冻结交易 3 个交易日",
    },
    {
        "key": "trailing",
        "层级": "P0",
        "机制": "移动止盈",
        "触发条件": "浮盈曾达 ≥ 10% 后回落至最高浮盈的 50%",
        "执行动作": "全部卖出锁定利润",
    },
    {
        "key": "atr",
        "层级": "P1",
        "机制": "ATR 动态止损",
        "触发条件": "止损 = -1.5 × 20日ATR / 买入价",
        "执行动作": "高波动自动放宽止损空间",
    },
    {
        "key": "take_profit",
        "层级": "P1",
        "机制": "分级止盈",
        "触发条件": "TP1 / TP2 / TP3（环境自适应）",
        "执行动作": "分三档各减仓 1/3",
    },
    {
        "key": "stampede",
        "层级": "P1",
        "机制": "防踩踏 + 指数熔断",
        "触发条件": "≥50% 持仓跌 > 3% 或上证跌 ≥ 3%",
        "执行动作": "暂停买入 + 收紧止损至 -3%",
    },
    {
        "key": "max_hold",
        "层级": "P2",
        "机制": "持仓天数管理",
        "触发条件": "持有 ≥ 20 天且浮盈未达 TP1",
        "执行动作": "强制平仓释放资金",
    },
    {
        "key": "limit_down",
        "层级": "P2",
        "机制": "跌停封板检测",
        "触发条件": "开盘一字跌停",
        "执行动作": "告警邮件 + 次日优先处理",
    },
    {
        "key": "correlation",
        "层级": "P2",
        "机制": "持仓相关性监控",
        "触发条件": "任意两只持仓相关系数 ≥ 0.7",
        "执行动作": "分散化警告 + 换股建议",
    },
]


def _count_trade_reasons(trades: list[dict], prefixes: tuple[str, ...]) -> int:
    n = 0
    for t in trades:
        reason = str(t.get("reason") or "")
        if any(reason == p or reason.startswith(p) for p in prefixes):
            n += 1
    return n


def _collect_monitor_alert_counts() -> dict[str, int]:
    """从 cron 运行历史里统计 monitor 告警类型。"""
    path = DATA / "cron_status.json"
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return counts
    for job in (st.get("jobs") or {}).values():
        for entry in job.get("history") or []:
            detail = entry.get("detail") or {}
            # monitor 结果可能嵌在 monitor 字段（建仓任务）或顶层
            payloads = [detail]
            if isinstance(detail.get("monitor"), dict):
                payloads.append(detail["monitor"])
            for payload in payloads:
                for alert in payload.get("alerts") or []:
                    if not isinstance(alert, dict):
                        continue
                    at = alert.get("type") or ""
                    if at:
                        counts[at] = counts.get(at, 0) + 1
                for act in payload.get("actions_taken") or []:
                    if not isinstance(act, dict):
                        continue
                    reason = str(act.get("reason") or "")
                    if reason:
                        counts[f"action:{reason}"] = counts.get(f"action:{reason}", 0) + 1
    return counts


def _hit_status(hit: bool, active: bool, count: int, detail: str = "") -> str:
    parts = []
    if active:
        parts.append("生效中")
    if hit or count > 0:
        if count > 0:
            parts.append(f"曾命中 {count} 次")
        elif hit:
            parts.append("曾命中")
    if not parts:
        parts.append("未命中")
    if detail:
        parts.append(detail)
    return " · ".join(parts)


def build_risk_layers_table(acc: dict | None) -> pd.DataFrame:
    stats = (acc or {}).get("stats") or {}
    trades = (acc or {}).get("trade_history") or []
    positions = (acc or {}).get("positions") or {}
    alert_counts = _collect_monitor_alert_counts()

    peak = float((acc or {}).get("peak_total_assets") or (acc or {}).get("initial_cash") or 0)
    total = float((acc or {}).get("total_assets") or 0)
    dd_pct = ((peak - total) / peak * 100) if peak > 0 else 0.0
    frozen = (acc or {}).get("frozen_until")
    today = date.today().isoformat()
    frozen_active = bool(frozen and str(frozen) >= today)

    atr_armed = sum(1 for p in positions.values() if p.get("atr_stop_loss") is not None)
    trailing_armed = sum(
        1 for p in positions.values()
        if float(p.get("max_profit_pct") or 0) >= 0.10
    )

    dd_count = max(
        int(stats.get("drawdown_circuit_breaks") or 0),
        _count_trade_reasons(trades, ("drawdown_breaker",)),
    )
    trail_count = max(
        int(stats.get("trailing_stop_count") or 0),
        _count_trade_reasons(trades, ("trailing_stop",)),
    )
    atr_count = max(
        int(stats.get("stop_loss_count") or 0),
        _count_trade_reasons(trades, ("stop_loss",)),
    )
    tp_count = max(
        int(stats.get("take_profit_count") or 0),
        _count_trade_reasons(
            trades, ("take_profit", "take_profit_1", "take_profit_2", "take_profit_3")
        ),
    )
    stampede_count = (
        alert_counts.get("stampede", 0) + alert_counts.get("index_circuit_breaker", 0)
    )
    hold_count = (
        _count_trade_reasons(trades, ("max_holding_period",))
        + alert_counts.get("action:max_holding_period", 0)
    )
    ld_count = alert_counts.get("limit_down", 0)
    corr_count = alert_counts.get("correlation", 0)

    status_by_key = {
        "drawdown": _hit_status(
            dd_count > 0 or frozen_active,
            frozen_active,
            dd_count,
            f"当前回撤 {dd_pct:.2f}%"
            + (f"，冻结至 {frozen}" if frozen_active else "（阈值 10%）"),
        ),
        "trailing": (
            f"曾命中 {trail_count} 次"
            if trail_count > 0
            else (
                f"监控中 · {trailing_armed} 只已达10%浮盈 · 未回撤触发"
                if trailing_armed
                else "未命中"
            )
        ),
        "atr": (
            _hit_status(
                True, False, atr_count,
                f"{atr_armed} 只持仓已设 ATR" if atr_armed else "",
            )
            if atr_count > 0
            else (
                f"已部署 · {atr_armed} 只持仓挂有 ATR 止损 · 未触发"
                if atr_armed
                else ("未命中 · 空仓无部署" if not positions else "未命中")
            )
        ),
        "take_profit": _hit_status(tp_count > 0, False, tp_count),
        "stampede": _hit_status(
            stampede_count > 0, False, stampede_count,
            "来自盯盘告警" if stampede_count else "",
        ),
        "max_hold": _hit_status(hold_count > 0, False, hold_count),
        "limit_down": _hit_status(
            ld_count > 0, False, ld_count,
            "来自盯盘告警" if ld_count else "",
        ),
        "correlation": (
            "未命中 · 持仓不足2只，无需监控"
            if len(positions) < 2
            else _hit_status(
                corr_count > 0, False, corr_count, f"当前持仓 {len(positions)} 只"
            )
        ),
    }

    rows = []
    for layer in RISK_LAYER_DEFS:
        rows.append({
            "层级": layer["层级"],
            "机制": layer["机制"],
            "触发条件": layer["触发条件"],
            "执行动作": layer["执行动作"],
            "命中状态": status_by_key.get(layer["key"], "未命中"),
        })
    return pd.DataFrame(rows)


def render_account_overview():
    ids = all_account_ids()
    view_id = st.session_state.get("view_account_id") or (ids[0] if ids else "default")
    acc = load_account(view_id)
    name = (acc or {}).get("account_name") or view_id
    st.markdown(f"### 账户概览 · {name} `{view_id}`")

    if not acc:
        st.info(
            "该账户尚无数据。可在「账户切换」中新建，或执行："
            "`python3 scripts/simulate_trend.py init 100000`"
        )
        render_risk_layers(None)
        return

    # 优先用缓存的实时 status（账户列表 / 刷新行情已拉取）；缺失时再请求
    amap = st.session_state.get("accounts_live_map") or {}
    live = amap.get(view_id)
    if live is None:
        with st.spinner("按实时行情计算收益/回撤…"):
            live = fetch_live_status(view_id)
        if live:
            amap[view_id] = live
            st.session_state["accounts_live_map"] = amap
    if live:
        total = float(live.get("total_assets") or 0)
        cash = float(live.get("current_cash") or 0)
        ret = float(live.get("total_return") or 0)
        ret_pct = parse_pct(live.get("total_return_pct"))
        dd_pct = parse_pct(live.get("drawdown_from_peak"))
        mv = float(live.get("market_value") or 0)
        priced = bool(live.get("priced_live"))
        positions = live.get("positions") or {}
    else:
        total = float(acc.get("total_assets") or 0)
        cash = float(acc.get("current_cash") or 0)
        ret = total - float(acc.get("initial_cash") or 0)
        ret_pct = ret / acc["initial_cash"] * 100 if acc.get("initial_cash") else 0
        peak = float(acc.get("peak_total_assets") or acc.get("initial_cash") or 0)
        dd_pct = (peak - total) / peak * 100 if peak else 0
        mv = sum(
            float(p.get("shares") or 0) * float(p.get("avg_price") or 0)
            for p in (acc.get("positions") or {}).values()
        )
        priced = False
        positions = acc.get("positions") or {}

    m1, m2, m3, m4 = st.columns(4)
    tone = "up" if ret >= 0 else "down"
    m1.markdown(metric_html("总资产", f"¥{total:,.2f}"), unsafe_allow_html=True)
    m2.markdown(metric_html("可用现金", f"¥{cash:,.2f}"), unsafe_allow_html=True)
    m3.markdown(metric_html("累计收益", f"{ret:+,.2f} ({ret_pct:+.2f}%)", tone), unsafe_allow_html=True)
    regime = (acc.get("market_regime") or {}).get("current", "-")
    style_cn = (acc.get("market_regime") or {}).get("leader_style_cn")
    env_label = str(regime).upper()
    if style_cn:
        env_label = f"{env_label} · {style_cn}"
    m4.markdown(metric_html("市场环境", env_label), unsafe_allow_html=True)

    m5, m6, m7, m8 = st.columns(4)
    m5.markdown(metric_html("持仓市值", f"¥{mv:,.2f}"), unsafe_allow_html=True)
    m6.markdown(
        metric_html("峰值回撤", f"{dd_pct:.2f}%", "down" if dd_pct > 0 else ""),
        unsafe_allow_html=True,
    )
    m7.markdown(
        metric_html("计价方式", "实时行情" if priced else "成本价（行情未取到）"),
        unsafe_allow_html=True,
    )
    m8.markdown(metric_html("持仓数", str(len(positions))), unsafe_allow_html=True)

    snaps = acc.get("daily_snapshots") or []
    if snaps:
        st.markdown("#### 资金曲线")
        curve = pd.DataFrame(snaps).set_index("date")[["total_assets"]]
        curve.columns = ["总资产"]
        st.line_chart(curve, height=280)
    else:
        st.caption("暂无每日快照，交易日后运行 monitor / snapshot 会生成曲线数据。")

    if positions:
        st.markdown("#### 当前持仓")
        rows = []
        for code, p in positions.items():
            row = {
                "代码": code,
                "名称": p.get("name", ""),
                "数量": p.get("shares"),
                "成本": p.get("avg_price"),
                "现价": p.get("last_price"),
                "市值": p.get("market_value"),
                "浮盈": p.get("pnl"),
                "浮盈%": p.get("pnl_pct"),
                "买入日": p.get("buy_date") or p.get("last_buy_date"),
            }
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("当前空仓")

    # 风控回撤用实时总资产覆盖展示
    acc_for_risk = dict(acc)
    if live:
        acc_for_risk["total_assets"] = total
        acc_for_risk["peak_total_assets"] = live.get("peak_total_assets") or acc.get("peak_total_assets")
        acc_for_risk["positions"] = {
            c: {k: v for k, v in p.items() if k not in ("last_price", "market_value", "pnl", "pnl_pct", "priced_live")}
            for c, p in positions.items()
        }

    render_manual_trade(view_id, acc)
    render_risk_layers(acc_for_risk)


def render_manual_trade(account_id: str, acc: dict):
    """当前查看账户的手动买入 / 卖出。"""
    st.markdown("#### 手动交易")
    if st.session_state.pop("trade_flash", None):
        flash = st.session_state.pop("trade_flash_msg", "交易成功")
        st.success(flash)

    # 行情回填须在对应 widget 实例化之前写入 session_state
    pending_buy = st.session_state.pop("pending_buy_quote", None)
    if isinstance(pending_buy, dict):
        st.session_state["buy_quote"] = pending_buy
        st.session_state["buy_name"] = pending_buy.get("name") or ""
        st.session_state["buy_price"] = float(pending_buy.get("price") or 0.01) or 0.01
    pending_sell = st.session_state.pop("pending_sell_quote", None)
    if isinstance(pending_sell, dict):
        st.session_state["sell_quote"] = pending_sell
        st.session_state["sell_price"] = float(pending_sell.get("price") or 0.01) or 0.01

    frozen = acc.get("frozen_until")
    today = date.today().isoformat()
    if frozen and str(frozen) >= today:
        st.warning(f"账户冻结至 {frozen}，不可交易（回撤熔断）。")
        return

    cash = float(acc.get("current_cash") or 0)
    positions = acc.get("positions") or {}
    tab_buy, tab_sell = st.tabs(["买入", "卖出"])

    with tab_buy:
        if "buy_price" not in st.session_state:
            st.session_state["buy_price"] = 10.0
        if "buy_name" not in st.session_state:
            st.session_state["buy_name"] = ""

        b1, b2, b3 = st.columns([2, 2, 1])
        with b1:
            buy_code = st.text_input("股票代码", placeholder="如 002475", key="buy_code")
        with b2:
            buy_name = st.text_input("名称（可留空，点行情自动填）", key="buy_name")
        with b3:
            st.write("")
            st.write("")
            do_quote_buy = st.button("获取行情", key="btn_quote_buy", use_container_width=True)

        if do_quote_buy and buy_code.strip():
            q = fetch_quote(buy_code.strip())
            if not q:
                st.error("获取行情失败，请检查代码")
            else:
                st.session_state["pending_buy_quote"] = q
                st.rerun()

        quote = st.session_state.get("buy_quote") or {}
        if quote and quote.get("code"):
            st.caption(
                f"行情 {quote.get('name')}({quote.get('code')}) "
                f"现价 {quote.get('price')}  涨跌 {quote.get('change_pct', 0):+.2f}%"
            )

        b4, b5, b6 = st.columns(3)
        with b4:
            buy_shares = st.number_input("数量（股）", min_value=100, value=100, step=100, key="buy_shares")
        with b5:
            buy_price = st.number_input(
                "委托价格", min_value=0.01, step=0.01, format="%.2f", key="buy_price",
            )
        with b6:
            buy_reason = st.text_input("理由", value="manual", key="buy_reason")

        est = buy_shares * buy_price
        st.caption(f"预估金额约 ¥{est:,.2f}（未含佣金滑点）· 可用现金 ¥{cash:,.2f}")

        if st.button("确认买入", type="primary", key="btn_buy"):
            code = buy_code.strip()
            name = (st.session_state.get("buy_name") or buy_name or "").strip() or code
            if not code:
                st.error("请填写股票代码")
            else:
                if not name or name == code:
                    q = fetch_quote(code)
                    if q:
                        name = q.get("name") or name
                        if buy_price <= 0.01 and q.get("price"):
                            buy_price = float(q["price"])
                with st.spinner("提交买入…"):
                    data, _, err, rc = run_script(
                        [
                            "simulate_trend.py", "buy",
                            code, name, str(int(buy_shares)), f"{float(buy_price):.2f}",
                            "--reason", buy_reason or "manual",
                        ],
                        timeout=60,
                        account_id=account_id,
                    )
                if rc == 0 and isinstance(data, dict) and not data.get("error"):
                    st.session_state["trade_flash"] = True
                    st.session_state["trade_flash_msg"] = (
                        f"买入成功：{data.get('name', name)} {data.get('shares')}股 "
                        f"@ {data.get('price')}，费用 {data.get('fees')}"
                    )
                    st.session_state.pop("accounts_live_map", None)
                    st.session_state["refresh_accounts_live"] = True
                    st.rerun()
                else:
                    st.error(
                        (data or {}).get("error")
                        if isinstance(data, dict)
                        else err or f"买入失败 exit={rc}"
                    )

    with tab_sell:
        if not positions:
            st.info("当前无持仓，无法卖出。")
        else:
            pos_labels = {
                code: f"{p.get('name', '')}({code}) · {p.get('shares')}股 · 成本{p.get('avg_price')}"
                for code, p in positions.items()
            }
            sell_code = st.selectbox(
                "持仓",
                list(pos_labels.keys()),
                format_func=lambda c: pos_labels[c],
                key="sell_code",
            )
            pos = positions[sell_code]
            max_shares = int(pos.get("shares") or 0)
            s1, s2, s3 = st.columns([2, 2, 1])
            with s1:
                sell_shares = st.number_input(
                    "卖出数量", min_value=100, max_value=max(max_shares, 100),
                    value=max_shares, step=100, key="sell_shares",
                )
            with s2:
                if "sell_price" not in st.session_state:
                    st.session_state["sell_price"] = float(pos.get("avg_price") or 10)
                sell_price = st.number_input(
                    "委托价格", min_value=0.01, step=0.01, format="%.2f", key="sell_price",
                )
            with s3:
                st.write("")
                st.write("")
                do_quote_sell = st.button("获取行情", key="btn_quote_sell", use_container_width=True)

            if do_quote_sell:
                q = fetch_quote(sell_code)
                if not q:
                    st.error("获取行情失败")
                else:
                    st.session_state["pending_sell_quote"] = q
                    st.rerun()

            if st.session_state.get("sell_quote"):
                q = st.session_state["sell_quote"]
                st.caption(
                    f"行情 {q.get('name')} 现价 {q.get('price')}  "
                    f"涨跌 {q.get('change_pct', 0):+.2f}%"
                )

            sell_reason = st.text_input("理由", value="manual", key="sell_reason")
            buy_date = pos.get("buy_date") or pos.get("last_buy_date") or ""
            if buy_date == today:
                st.warning("T+1：今日买入的持仓不可当日卖出。")

            if st.button("确认卖出", type="primary", key="btn_sell"):
                if sell_shares > max_shares:
                    st.error(f"超过持仓数量 {max_shares}")
                else:
                    with st.spinner("提交卖出…"):
                        data, _, err, rc = run_script(
                            [
                                "simulate_trend.py", "sell",
                                sell_code, str(int(sell_shares)), f"{float(sell_price):.2f}",
                                "--reason", sell_reason or "manual",
                            ],
                            timeout=60,
                            account_id=account_id,
                        )
                    if rc == 0 and isinstance(data, dict) and not data.get("error"):
                        st.session_state["trade_flash"] = True
                        st.session_state["trade_flash_msg"] = (
                            f"卖出成功：{sell_code} {data.get('shares', sell_shares)}股 "
                            f"@ {data.get('price')}，盈亏 {data.get('profit', data.get('profit_pct', ''))}"
                        )
                        st.session_state.pop("accounts_live_map", None)
                        st.session_state["refresh_accounts_live"] = True
                        st.rerun()
                    else:
                        st.error(
                            (data or {}).get("error")
                            if isinstance(data, dict)
                            else err or f"卖出失败 exit={rc}"
                        )


def render_risk_layers(acc: dict | None):
    st.markdown("### 八层风控")
    st.caption("命中状态综合账户统计、成交原因与盯盘告警记录；空仓时多数层显示未命中属正常。")
    df = build_risk_layers_table(acc)
    st.dataframe(df, use_container_width=True, hide_index=True)


def load_cron_jobs() -> dict:
    path = DATA / "cron_status.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get("jobs") or {}


def list_daily_report_files() -> list[dict]:
    """扫描 data/trend_report_YYYY-MM-DD[_account].md。"""
    rows = []
    for p in sorted(DATA.glob("trend_report_*.md"), reverse=True):
        stem = p.stem  # trend_report_2026-08-12_paper_b
        parts = stem.split("_")
        # ["trend", "report", "2026-08-12", maybe account]
        if len(parts) < 3:
            continue
        day = parts[2]
        if len(day) != 10:
            continue
        if len(parts) < 4:
            continue  # 跳过无账户后缀的旧文件 trend_report_YYYY-MM-DD.md
        account_id = "_".join(parts[3:])
        rows.append({"date": day, "account_id": account_id, "path": p})
    return rows


def _monitor_payload(entry: dict) -> dict:
    d = entry.get("detail") if isinstance(entry, dict) else {}
    if isinstance(d, dict) and (d.get("actions_taken") is not None or d.get("alerts") is not None):
        return d
    if isinstance(entry, dict) and (entry.get("actions_taken") is not None or entry.get("monitor")):
        return entry.get("monitor") or entry
    return d if isinstance(d, dict) else {}


def render_monitor_job_report(job: dict, title: str):
    if not job:
        st.caption(f"尚无{title}记录。")
        return
    last = job.get("last_run") or "—"
    st.caption(
        f"上次 {last[:19].replace('T', ' ') if isinstance(last, str) else last} · "
        f"{STATUS_LABEL.get(job.get('last_status'), job.get('last_status') or '—')} · "
        f"{job.get('last_message') or ''}"
    )
    accounts = (job.get("last_detail") or {}).get("accounts") or {}
    if not accounts:
        st.caption("无分账户详情。")
        return
    view_id = st.session_state.get("view_account_id")
    ordered = sorted(accounts.keys(), key=lambda i: (0 if i == view_id else 1, i))
    for aid in ordered:
        entry = accounts[aid] or {}
        label = "当前查看" if aid == view_id else aid
        with st.expander(f"{aid} · {entry.get('message') or entry.get('status') or ''}（{label}）", expanded=(aid == view_id)):
            payload = _monitor_payload(entry)
            actions = payload.get("actions_taken") or []
            alerts = payload.get("alerts") or []
            if not actions and not alerts:
                st.write(entry.get("message") or "无动作、无告警")
                continue
            if alerts:
                st.markdown("**告警**")
                rows = []
                for a in alerts:
                    if isinstance(a, dict):
                        rows.append({
                            "类型": a.get("type"),
                            "说明": a.get("detail") or a.get("action") or a.get("from") or "",
                            "明细": " → ".join(
                                str(x) for x in (a.get("from"), a.get("to")) if x
                            ) or a.get("index_change") or a.get("code") or "",
                        })
                    else:
                        rows.append({"类型": "", "说明": str(a), "明细": ""})
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            if actions:
                st.markdown("**执行动作**")
                rows = []
                for a in actions:
                    if not isinstance(a, dict):
                        continue
                    rows.append({
                        "动作": a.get("action") or a.get("reason"),
                        "代码": a.get("code"),
                        "名称": a.get("name"),
                        "数量": a.get("shares"),
                        "价格": a.get("price"),
                        "原因": a.get("reason") or a.get("detail"),
                    })
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            summary = payload.get("market_summary") or {}
            pos = summary.get("positions") or {}
            if pos:
                st.markdown("**当时持仓**")
                st.dataframe(
                    pd.DataFrame([
                        {
                            "代码": c,
                            "名称": p.get("name"),
                            "现价": p.get("price"),
                            "涨幅%": p.get("change_pct"),
                            "浮盈%": p.get("profit_pct"),
                            "持仓天": p.get("holding_days"),
                        }
                        for c, p in pos.items()
                    ]),
                    use_container_width=True,
                    hide_index=True,
                )


def render_health_report(job: dict):
    if not job:
        st.caption("尚无健康检查记录。")
        return
    last = job.get("last_run") or "—"
    st.caption(
        f"上次 {str(last)[:19].replace('T', ' ')} · "
        f"{STATUS_LABEL.get(job.get('last_status'), job.get('last_status') or '—')} · "
        f"{job.get('last_message') or ''}"
    )
    accounts = (job.get("last_detail") or {}).get("accounts") or {}
    if not accounts:
        st.caption("无分账户详情。")
        return
    view_id = st.session_state.get("view_account_id")
    for aid, entry in accounts.items():
        detail = entry.get("detail") or {}
        checks = detail.get("checks") or []
        with st.expander(
            f"{aid} · {entry.get('message') or detail.get('status') or ''}",
            expanded=(aid == view_id),
        ):
            if checks:
                st.dataframe(
                    pd.DataFrame([
                        {
                            "检查项": c.get("name"),
                            "结果": "✅" if c.get("ok") else "❌",
                            "说明": c.get("detail"),
                        }
                        for c in checks
                    ]),
                    use_container_width=True,
                    hide_index=True,
                )
            alerts = detail.get("alerts") or []
            if alerts:
                for a in alerts:
                    st.warning(a if isinstance(a, str) else json.dumps(a, ensure_ascii=False))


def render_open_report(job: dict):
    if not job:
        st.caption("尚无开盘建仓记录。")
        return
    last = job.get("last_run") or "—"
    st.caption(
        f"上次 {str(last)[:19].replace('T', ' ')} · "
        f"{STATUS_LABEL.get(job.get('last_status'), job.get('last_status') or '—')} · "
        f"{job.get('last_message') or ''}"
    )
    accounts = (job.get("last_detail") or {}).get("accounts") or {}
    view_id = st.session_state.get("view_account_id")
    for aid, entry in accounts.items():
        trade = entry.get("auto_trade") or {}
        buys = trade.get("buys") or []
        skipped = trade.get("skipped") or []
        with st.expander(
            f"{aid} · {entry.get('message') or ''}",
            expanded=(aid == view_id),
        ):
            if buys:
                st.markdown("**成交**")
                st.dataframe(
                    pd.DataFrame([
                        {
                            "代码": b.get("code"),
                            "名称": b.get("name"),
                            "数量": b.get("shares"),
                            "价格": b.get("price"),
                            "评分": b.get("score"),
                        }
                        for b in buys
                    ]),
                    use_container_width=True,
                    hide_index=True,
                )
            if skipped:
                st.markdown("**未成交 / 过滤**")
                st.dataframe(
                    pd.DataFrame([
                        {
                            "代码": s.get("code"),
                            "名称": s.get("name"),
                            "原因": s.get("reason"),
                        }
                        for s in skipped
                    ]),
                    use_container_width=True,
                    hide_index=True,
                )
            if not buys and not skipped:
                st.write(entry.get("message") or "无建仓动作")


def render_daily_reports():
    files = list_daily_report_files()
    if not files:
        st.caption("还没有收盘日报文件。交易日 15:30 生成后会出现在这里。")
        return
    dates = sorted({r["date"] for r in files}, reverse=True)
    accounts = sorted({r["account_id"] for r in files})
    view_id = st.session_state.get("view_account_id") or (accounts[0] if accounts else "default")
    c1, c2 = st.columns(2)
    with c1:
        day = st.selectbox("报告日期", dates, key="report_day")
    with c2:
        acct_opts = [a for a in accounts if any(r["date"] == day and r["account_id"] == a for r in files)]
        default_idx = acct_opts.index(view_id) if view_id in acct_opts else 0
        aid = st.selectbox("账户", acct_opts, index=default_idx, key="report_acct")
    match = next((r for r in files if r["date"] == day and r["account_id"] == aid), None)
    if not match:
        st.caption("该日该账户无日报。")
        return
    st.caption(f"`{match['path'].name}`")
    text = match["path"].read_text(encoding="utf-8")
    st.markdown(text)


def render_job_reports():
    st.markdown("#### 任务报告")
    jobs = load_cron_jobs()
    t_report, t_close, t_health, t_open = st.tabs(
        ["收盘日报", "尾盘风控", "健康检查", "开盘建仓"]
    )
    with t_report:
        render_daily_reports()
        job = jobs.get("report") or {}
        if job.get("last_message"):
            st.caption(f"调度：{job.get('last_message')}")
    with t_close:
        render_monitor_job_report(jobs.get("closing") or {}, "尾盘风控")
    with t_health:
        render_health_report(jobs.get("health") or {})
    with t_open:
        render_open_report(jobs.get("open_0945") or {})


def render_schedule_panel():
    st.markdown("### 调度中心")
    st.caption("对齐 README 自动调度：盯盘/建仓/日报/健康检查对全部账户执行；周五调权全局一次")

    b1, b2, b3, b4 = st.columns(4)
    with b1:
        do_refresh = st.button("刷新状态", use_container_width=True)
    with b2:
        do_install = st.button("安装 Cron", use_container_width=True, type="primary")
    with b3:
        do_uninstall = st.button("卸载 Cron", use_container_width=True)
    with b4:
        force_run = st.checkbox("强制执行（忽略交易日）", value=False)

    if do_install:
        with st.spinner("写入用户 crontab…"):
            data, _, err, code = run_script(
                ["cron_runner.py", "--install-crontab"], timeout=30
            )
        if isinstance(data, dict) and data.get("ok"):
            st.success(f"已安装 {data.get('entries', 0)} 条定时任务")
        else:
            st.error((data or {}).get("error") if isinstance(data, dict) else err or "安装失败")

    if do_uninstall:
        with st.spinner("卸载 crontab…"):
            data, _, err, code = run_script(
                ["cron_runner.py", "--uninstall-crontab"], timeout=30
            )
        if isinstance(data, dict) and data.get("ok"):
            st.success(data.get("message") or "已卸载")
        else:
            st.error((data or {}).get("error") if isinstance(data, dict) else err or "卸载失败")

    status = fetch_cron_status()
    if status.get("error") and not status.get("jobs"):
        st.warning(status["error"])
        return

    installed = status.get("crontab_installed", False)
    trading = status.get("trading_day", False)
    m1, m2, m3, m4 = st.columns(4)
    m1.markdown(
        metric_html("Cron 状态", "已安装" if installed else "未安装",
                    "" if installed else "down"),
        unsafe_allow_html=True,
    )
    m2.markdown(
        metric_html("今日", f"{status.get('weekday', '')} · "
                    f"{'交易日' if trading else status.get('trading_reason', '休市')}"),
        unsafe_allow_html=True,
    )
    m3.markdown(
        metric_html(
            "盯盘进度",
            f"{status.get('monitor_runs_today', 0)} / {status.get('monitor_planned_today', 0)}",
        ),
        unsafe_allow_html=True,
    )
    m4.markdown(
        metric_html("服务器时间", status.get("time", "-") or "-"),
        unsafe_allow_html=True,
    )

    jobs = status.get("jobs") or []
    if jobs:
        rows = []
        for j in jobs:
            last = j.get("last_run")
            last_hm = last[11:16] if isinstance(last, str) and len(last) >= 16 else "—"
            rows.append({
                "任务": j.get("name"),
                "分组": j.get("group"),
                "计划": " ".join(j.get("schedule_times") or j.get("times") or []),
                "周期": j.get("weekdays"),
                "今日进度": DUE_LABEL.get(j.get("due_state"), j.get("due_state")),
                "下次": j.get("next_expected_today") or "—",
                "上次时刻": last_hm,
                "上次结果": STATUS_LABEL.get(j.get("last_status"), j.get("last_status") or "—"),
                "说明": j.get("last_message") or j.get("desc") or "",
                "今日次数": j.get("runs_today", 0),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    render_job_reports()

    # 手动触发
    job_ids = {f"{j.get('name')} ({j.get('id')})": j.get("id") for j in jobs}
    if job_ids:
        c_sel, c_run = st.columns([3, 1])
        with c_sel:
            pick = st.selectbox("手动触发任务", list(job_ids.keys()))
        with c_run:
            st.write("")
            st.write("")
            do_manual = st.button("立即执行", use_container_width=True)
        if do_manual:
            jid = job_ids[pick]
            args = ["cron_runner.py", jid]
            if force_run:
                args.append("--force")
            with st.spinner(f"执行 {jid}…"):
                data, _, err, code = run_script(args, timeout=360)
            if isinstance(data, dict) and data.get("status") in ("ok", "skip"):
                st.success(f"{data.get('name', jid)}: {data.get('message')}")
            else:
                st.error(
                    (data or {}).get("message")
                    if isinstance(data, dict)
                    else err or f"失败 exit={code}"
                )
            if isinstance(data, dict):
                with st.expander("执行详情"):
                    st.json(data)

    with st.expander("Cron 表达式预览 / 日志路径"):
        st.caption(f"状态文件: `{status.get('status_file', '')}`")
        st.caption(f"日志目录: `{status.get('log_dir', '')}`")
        prev, out, _, _ = run_script(["cron_runner.py", "--print-crontab"], timeout=15)
        text = (prev or {}).get("_raw") if isinstance(prev, dict) else None
        st.code(text or out or "（无法读取）", language="bash")

    if do_refresh:
        st.rerun()


# ── 侧边栏参数 ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 运行参数")
    top_n = st.slider("选股数量 Top N", 5, 30, 10, 1)
    score_top = st.slider("评分输出 Top N", 3, 20, 5, 1)
    max_price = st.number_input("最高股价过滤", min_value=5.0, max_value=100.0, value=20.0, step=1.0)

    st.markdown("---")
    st.markdown("### 回测参数")
    today = date.today()
    default_start = today - timedelta(days=90)  # 近 3 个月
    bt_start = st.date_input("起始日期", value=default_start)
    bt_end = st.date_input("结束日期", value=today)
    bt_cash = st.number_input("初始资金", min_value=10000.0, value=100000.0, step=10000.0)

    st.markdown("---")
    st.caption(f"工作目录\n`{ROOT}`")
    st.caption("脚本以 subprocess 调用，与 CLI 行为一致。")


# ── 页头 ──────────────────────────────────────────────────────────────────────
st.markdown(
    '<p class="brand">StockTrader<span>-AI</span></p>'
    '<p class="sub">多账户切换 · 选股表格与资金曲线 · 调度与风控</p>',
    unsafe_allow_html=True,
)

# 会话结果缓存（需在账户切换前初始化 view 相关以外的状态）
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "last_action" not in st.session_state:
    st.session_state.last_action = None
if "last_stderr" not in st.session_state:
    st.session_state.last_stderr = ""

render_account_switcher()
st.markdown("---")
render_account_overview()
st.markdown("---")
render_schedule_panel()
st.markdown("---")
st.markdown("### 操作台")

# ── 操作按钮 ──────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1:
    run_screen = st.button("① 量化选股", use_container_width=True, type="primary")
with c2:
    run_score = st.button("② 选股 + 因子评分", use_container_width=True, type="primary")
with c3:
    run_regime = st.button("③ 市场环境", use_container_width=True)
with c4:
    run_sector = st.button("④ 板块轮动", use_container_width=True)

c5, c6, c7, c8 = st.columns(4)
with c5:
    run_backtest = st.button("⑤ 策略回测", use_container_width=True)
with c6:
    run_account = st.button("⑥ 账户状态", use_container_width=True)
with c7:
    run_report = st.button("⑦ 生成日报", use_container_width=True)
with c8:
    run_health = st.button("⑧ 健康检查", use_container_width=True)


def set_result(action: str, data: Any, stderr: str = "", code: int = 0):
    st.session_state.last_action = action
    st.session_state.last_result = data
    st.session_state.last_stderr = stderr
    st.session_state.last_code = code


# ── 触发执行 ──────────────────────────────────────────────────────────────────
if run_screen:
    with st.spinner("正在全市场选股（可能需要 1–3 分钟）…"):
        data, out, err, code = run_script(
            ["stock_screener.py", "--top", str(top_n), "--max-price", str(max_price), "--json"],
            timeout=300,
        )
    set_result("screen", data, err, code)

if run_score:
    with st.spinner("选股中…"):
        screen, _, err1, code1 = run_script(
            ["stock_screener.py", "--top", str(top_n), "--max-price", str(max_price), "--json"],
            timeout=300,
        )
    if code1 != 0 or not screen or "candidates" not in (screen or {}):
        set_result("score", screen or {"error": "选股失败"}, err1, code1)
    else:
        with st.spinner("因子评分中…"):
            scored, _, err2, code2 = run_script(
                ["factor_scorer.py", "--top", str(score_top), "--json"],
                timeout=300,
                stdin_text=json.dumps(screen, ensure_ascii=False),
            )
        set_result(
            "score",
            {"screen": screen, "scored": scored},
            err1 + "\n" + err2,
            code2,
        )

if run_regime:
    with st.spinner("判断市场环境…"):
        data, _, err, code = run_script(["market_regime.py"], timeout=90)
    set_result("regime", data, err, code)

if run_sector:
    with st.spinner("分析板块轮动…"):
        data, _, err, code = run_script(
            ["sector_rotation.py", "--json", "--suggest"],
            timeout=60,
        )
    set_result("sector", data, err, code)

if run_backtest:
    with st.spinner("回测运行中（可能需要数分钟）…"):
        data, _, err, code = run_script(
            [
                "backtest.py",
                "--start", bt_start.isoformat(),
                "--end", bt_end.isoformat(),
                "--cash", str(bt_cash),
                "--json",
            ],
            timeout=600,
        )
    set_result("backtest", data, err, code)

if run_account:
    with st.spinner("读取账户…"):
        data, _, err, code = run_script(
            ["simulate_trend.py", "status"],
            timeout=45,
            account_id=st.session_state.get("view_account_id"),
        )
    # 附带本地快照用于画曲线
    acc = load_account()
    if isinstance(data, dict) and acc:
        data["daily_snapshots"] = acc.get("daily_snapshots", [])
        data["trade_history"] = acc.get("trade_history", [])
    set_result("account", data, err, code)

if run_report:
    with st.spinner("生成日报…"):
        data, out, err, code = run_script(
            ["daily_report.py"],
            timeout=180,
            account_id=st.session_state.get("view_account_id"),
        )
    if data is None:
        data = {"error": err.strip() or "日报无输出", "_raw": out or ""}
        if code == 0:
            code = 1
    set_result("report", data, err, code)

if run_health:
    with st.spinner("健康检查…"):
        data, _, err, code = run_script(
            ["health_check.py"],
            timeout=60,
            account_id=st.session_state.get("view_account_id"),
        )
    set_result("health", data, err, code)


# ── 结果展示 ──────────────────────────────────────────────────────────────────
action = st.session_state.last_action
result = st.session_state.last_result
stderr = st.session_state.last_stderr
code = st.session_state.get("last_code", 0)

if result is None:
    if not action:
        st.caption("在上方查看账户与调度状态；点击操作台按钮执行脚本，结果将显示在此处。")
    else:
        st.error(f"「{action}」无输出（exit={code}）")
        if stderr:
            st.code(stderr[-4000:], language="text")
    st.stop()

# 有执行结果
if isinstance(result, dict) and result.get("error"):
    st.error(f"执行失败（exit={code}）：{result.get('error')}")
    if stderr:
        st.code(stderr[-2000:], language="text")
    st.stop()
if code not in (0, None) and result is None:
    st.error(f"执行失败（exit={code}）\n\n{stderr[:2000]}")
    st.stop()

st.markdown(f"#### 结果 · {action}")

# ── 各 action 渲染 ─────────────────────────────────────────────────────────────
if action == "screen" and isinstance(result, dict):
    cands = result.get("candidates") or []
    m1, m2, m3, m4 = st.columns(4)
    m1.markdown(metric_html("候选数", str(len(cands))), unsafe_allow_html=True)
    m2.markdown(metric_html("扫描总数", str(result.get("total_screened", "-"))), unsafe_allow_html=True)
    m3.markdown(metric_html("过技术关", str(result.get("passed_tech", "-"))), unsafe_allow_html=True)
    top3 = ", ".join(result.get("top3_sectors") or []) or "-"
    m4.markdown(metric_html("强势板块", top3), unsafe_allow_html=True)

    st.markdown("##### 选股结果")
    st.dataframe(flatten_candidates(cands), use_container_width=True, hide_index=True)

    sectors = result.get("sector_ranking") or []
    if sectors:
        st.markdown("##### 板块排名")
        st.dataframe(df_sectors(sectors), use_container_width=True, hide_index=True)

    limit_up = result.get("limit_up_today") or []
    if limit_up:
        with st.expander(f"今日涨停板（{len(limit_up)}）"):
            st.dataframe(pd.DataFrame(limit_up), use_container_width=True, hide_index=True)

elif action == "score" and isinstance(result, dict):
    screen = result.get("screen") or {}
    scored = result.get("scored")
    if isinstance(scored, list):
        st.markdown("##### 因子评分 Top")
        st.dataframe(flatten_candidates(scored), use_container_width=True, hide_index=True)
        # 分数条形图
        chart_df = pd.DataFrame([
            {"名称": f"{r.get('name', '')}({r.get('code')})", "总分": r.get("total_score", 0)}
            for r in scored
        ]).set_index("名称")
        if not chart_df.empty:
            st.markdown("##### 评分对比")
            st.bar_chart(chart_df, height=280)
    elif isinstance(scored, dict) and scored.get("error"):
        st.error(scored["error"])

    cands = (screen or {}).get("candidates") or []
    if cands:
        with st.expander("原始选股候选"):
            st.dataframe(flatten_candidates(cands), use_container_width=True, hide_index=True)

elif action == "regime" and isinstance(result, dict):
    regime = result.get("regime", "-")
    conf = result.get("confidence", "-")
    style_cn = result.get("leader_style_cn") or "-"
    m1, m2, m3, m4 = st.columns(4)
    m1.markdown(metric_html("市场环境", str(regime).upper()), unsafe_allow_html=True)
    m2.markdown(metric_html("置信度", f"{conf}%"), unsafe_allow_html=True)
    m3.markdown(metric_html("多头分", str(result.get("bull_score", "-"))), unsafe_allow_html=True)
    m4.markdown(metric_html("空头分", str(result.get("bear_score", "-"))), unsafe_allow_html=True)

    s1, s2, s3, s4 = st.columns(4)
    s1.markdown(metric_html("龙头风格", str(style_cn)), unsafe_allow_html=True)
    s2.markdown(metric_html("风格置信度", f"{result.get('leader_style_confidence', '-')}%"), unsafe_allow_html=True)
    s3.markdown(metric_html("连板分", str(result.get("board_score", "-"))), unsafe_allow_html=True)
    s4.markdown(metric_html("趋势分", str(result.get("trend_score", "-"))), unsafe_allow_html=True)
    if result.get("style_reason"):
        st.caption(result["style_reason"])

    leaders = result.get("leaders") or []
    if leaders:
        st.markdown("##### 领涨龙头")
        st.dataframe(
            pd.DataFrame([
                {
                    "代码": x.get("code"),
                    "名称": x.get("name"),
                    "涨幅%": x.get("change_pct"),
                    "连板": x.get("lianban"),
                    "流通市值(亿)": x.get("float_cap_yi"),
                    "换手%": x.get("turnover"),
                    "成交额(亿)": x.get("amount_yi"),
                }
                for x in leaders
            ]),
            use_container_width=True,
            hide_index=True,
        )
    metrics = result.get("style_metrics") or {}
    if metrics:
        with st.expander("风格明细"):
            st.dataframe(
                pd.DataFrame([{"指标": k, "值": v} for k, v in metrics.items()]),
                use_container_width=True,
                hide_index=True,
            )

    tech = result.get("technicals") or {}
    if tech:
        st.markdown("##### 技术指标")
        st.dataframe(
            pd.DataFrame([{"指标": k, "值": v} for k, v in tech.items()]),
            use_container_width=True,
            hide_index=True,
        )
    params = result.get("params_active") or {}
    if params:
        st.markdown("##### 当前生效参数")
        st.json(params)

elif action == "sector" and isinstance(result, dict):
    m1, m2, m3 = st.columns(3)
    m1.markdown(metric_html("市场环境", str(result.get("regime", "-")).upper()), unsafe_allow_html=True)
    m2.markdown(metric_html("环境分", str(result.get("regime_score", "-"))), unsafe_allow_html=True)
    m3.markdown(metric_html("置信度", f"{result.get('confidence', '-')}%"), unsafe_allow_html=True)

    ranking = result.get("sector_ranking") or []
    if ranking:
        st.markdown("##### 行业涨幅排名")
        sdf = df_sectors(ranking)
        st.dataframe(sdf, use_container_width=True, hide_index=True)
        # 曲线/柱：板块涨幅
        if "涨幅%" in sdf.columns and "行业" in sdf.columns:
            bar = sdf.set_index("行业")[["涨幅%"]]
            st.bar_chart(bar, height=320)

    suggestions = result.get("suggestions") or []
    if suggestions:
        st.markdown("##### 选股方向建议")
        for i, sug in enumerate(suggestions, 1):
            st.markdown(f"**{i}. {sug.get('direction', '')}** — {sug.get('focus', '')}")
            st.caption(sug.get("reason", ""))

elif action == "backtest" and isinstance(result, dict):
    if result.get("error"):
        st.error(result["error"])
    else:
        m1, m2, m3, m4 = st.columns(4)
        ret = result.get("total_return_pct", 0)
        tone = "up" if (ret or 0) >= 0 else "down"
        m1.markdown(metric_html("累计收益", f"{ret}%", tone), unsafe_allow_html=True)
        m2.markdown(metric_html("最大回撤", f"{result.get('max_drawdown_pct', '-')}%"), unsafe_allow_html=True)
        m3.markdown(metric_html("夏普比率", str(result.get("sharpe_ratio", "-"))), unsafe_allow_html=True)
        m4.markdown(metric_html("胜率", f"{result.get('win_rate_pct', '-')}%"), unsafe_allow_html=True)

        m5, m6, m7, m8 = st.columns(4)
        m5.markdown(metric_html("最终资产", f"¥{result.get('final_assets', 0):,.2f}"), unsafe_allow_html=True)
        m6.markdown(metric_html("交易次数", str(result.get("total_trades", "-"))), unsafe_allow_html=True)
        m7.markdown(metric_html("盈亏比", str(result.get("profit_loss_ratio", "-"))), unsafe_allow_html=True)
        m8.markdown(metric_html("平均持仓", f"{result.get('avg_holding_days', '-')} 天"), unsafe_allow_html=True)

        snaps = result.get("daily_snapshots") or []
        if snaps:
            st.markdown("##### 回测资金曲线")
            curve = pd.DataFrame(snaps)
            plot = curve.set_index("date")[["total_assets"]].copy()
            plot.columns = ["总资产"]
            st.line_chart(plot, height=360)

        trades = result.get("trades") or []
        if trades:
            st.markdown("##### 交易明细")
            tdf = pd.DataFrame(trades)
            prefer = [c for c in (
                "date", "type", "code", "shares", "price", "profit", "profit_pct", "reason"
            ) if c in tdf.columns]
            st.dataframe(tdf[prefer] if prefer else tdf, use_container_width=True, hide_index=True)

elif action == "account" and isinstance(result, dict):
    if result.get("error"):
        st.error(result["error"])
    else:
        ret_s = str(result.get("total_return_pct", "0%"))
        tone = "up" if not ret_s.startswith("-") else "down"
        m1, m2, m3, m4 = st.columns(4)
        m1.markdown(metric_html("总资产", f"¥{result.get('total_assets', 0):,.2f}"), unsafe_allow_html=True)
        m2.markdown(metric_html("可用现金", f"¥{result.get('current_cash', 0):,.2f}"), unsafe_allow_html=True)
        m3.markdown(metric_html("累计收益", ret_s, tone), unsafe_allow_html=True)
        m4.markdown(metric_html("峰值回撤", str(result.get("drawdown_from_peak", "-"))), unsafe_allow_html=True)

        snaps = result.get("daily_snapshots") or []
        if snaps:
            st.markdown("##### 资金曲线")
            curve = pd.DataFrame(snaps).set_index("date")[["total_assets"]]
            curve.columns = ["总资产"]
            st.line_chart(curve, height=320)

        positions = result.get("positions") or {}
        if positions:
            st.markdown("##### 持仓")
            rows = []
            for code, p in positions.items():
                rows.append({
                    "代码": code,
                    "名称": p.get("name", ""),
                    "数量": p.get("shares"),
                    "成本": p.get("avg_price"),
                    "买入日": p.get("buy_date"),
                    "逻辑止损": p.get("logic_stop"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("当前空仓")

        trades = result.get("trade_history") or []
        if trades:
            with st.expander(f"成交历史（{len(trades)}）"):
                st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)

        with st.expander("统计 / 风控参数"):
            st.json({"stats": result.get("stats"), "market_regime": result.get("market_regime")})

elif action == "report":
    if isinstance(result, dict) and result.get("error"):
        st.error(result["error"])
    else:
        md = result.get("_raw") if isinstance(result, dict) else str(result)
        st.markdown(md or "_空报告_")

elif action == "health" and isinstance(result, dict):
    st.json(result)

else:
    st.json(result)

with st.expander("原始输出 / 日志"):
    if isinstance(result, dict):
        preview = {
            k: v for k, v in result.items()
            if k not in ("daily_snapshots", "trades", "trade_history", "screen", "_raw")
        }
        st.json(preview if preview else result)
    elif isinstance(result, list):
        st.json(result[:50])
    if stderr:
        st.code(stderr[-4000:], language="text")
