#!/usr/bin/env python3
"""
Cron 统一入口：按 README/SKILL 调度表执行任务，并写入运行状态。

用法:
  python3 scripts/cron_runner.py <job_id>
  python3 scripts/cron_runner.py --status
  python3 scripts/cron_runner.py --list
  python3 scripts/cron_runner.py --install-crontab
  python3 scripts/cron_runner.py --uninstall-crontab
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
LOG_DIR = ROOT / "logs" / "cron"
STATUS_FILE = DATA / "cron_status.json"
MARKER_BEGIN = "# BEGIN stock-trader-ai"
MARKER_END = "# END stock-trader-ai"

# 与 README / SKILL 对齐的调度定义
SCHEDULE = [
    {
        "id": "morning_0930",
        "name": "早盘盯盘",
        "cron": "30 9 * * 1-5",
        "times": ["09:30"],
        "weekdays": "周一至周五",
        "group": "盯盘",
        "desc": "回撤熔断 / 移动止盈 / ATR止损",
    },
    {
        "id": "open_0945",
        "name": "开盘建仓",
        "cron": "45 9 * * 1-5",
        "times": ["09:45"],
        "weekdays": "周一至周五",
        "group": "建仓",
        "desc": "选股评分后按 regime 最大仓位/持仓数自动买入；先跑盯盘风控",
    },
    {
        "id": "morning_0950",
        "name": "早盘盯盘",
        "cron": "50 9 * * 1-5",
        "times": ["09:50"],
        "weekdays": "周一至周五",
        "group": "盯盘",
        "desc": "早盘第二轮风控检查",
    },
    {
        "id": "midday",
        "name": "智能盯盘",
        "cron": "0,20,40 10,11 * * 1-5",
        "times": ["10:00", "10:20", "10:40", "11:00", "11:20", "11:40"],
        "weekdays": "周一至周五",
        "group": "盯盘",
        "desc": "上午每20分钟 ×6",
    },
    {
        "id": "afternoon",
        "name": "午后盯盘",
        "cron": "0,20,40 13,14 * * 1-5",
        "times": ["13:00", "13:20", "13:40", "14:00", "14:20", "14:40"],
        "weekdays": "周一至周五",
        "group": "盯盘",
        "desc": "下午每20分钟 ×6",
    },
    {
        "id": "closing",
        "name": "尾盘风控",
        "cron": "10 15 * * 1-5",
        "times": ["15:10"],
        "weekdays": "周一至周五",
        "group": "风控",
        "desc": "只卖不买，完整风控+持仓天数",
    },
    {
        "id": "report",
        "name": "收盘日报",
        "cron": "30 15 * * 1-5",
        "times": ["15:30"],
        "weekdays": "周一至周五",
        "group": "报告",
        "desc": "夏普/回撤/相关性矩阵",
    },
    {
        "id": "health",
        "name": "健康检查",
        "cron": "0 16 * * 1-5",
        "times": ["16:00"],
        "weekdays": "周一至周五",
        "group": "运维",
        "desc": "验证日报/账户/快照，异常可告警",
    },
    {
        "id": "weekly",
        "name": "周度优化",
        "cron": "5 16 * * 5",
        "times": ["16:05"],
        "weekdays": "周五",
        "group": "优化",
        "desc": "因子权重自进化（向初始值回归）",
    },
]

JOB_BY_ID = {j["id"]: j for j in SCHEDULE}


def _python() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable or "python3"


def _now() -> datetime:
    return datetime.now(CST)


def load_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"jobs": {}, "installed": False, "updated_at": None}


def save_status(status: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    status["updated_at"] = _now().isoformat()
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_trading_day(now: datetime | None = None) -> tuple[bool, str]:
    """复用 time_guard 日历逻辑。"""
    sys.path.insert(0, str(SCRIPTS))
    import time_guard  # noqa: E402

    now = now or _now()
    holiday, name = time_guard.is_holiday(now)
    if holiday:
        return False, f"节假日（{name}）"
    if now.weekday() >= 5 and not time_guard.is_extra_trading_day(now):
        return False, "周末休市"
    if not time_guard.is_trading_day(now):
        return False, "非交易日"
    return True, "交易日"


def run_cmd(
    args: list[str],
    timeout: int = 300,
    *,
    account_id: str | None = None,
) -> tuple[int, str, str]:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if account_id:
        env["STOCK_ACCOUNT_ID"] = account_id
    proc = subprocess.run(
        [_python(), str(SCRIPTS / args[0]), *args[1:]],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT),
        env=env,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _all_accounts() -> list[str]:
    sys.path.insert(0, str(SCRIPTS))
    from account_manager import all_account_ids, ensure_migrated
    ensure_migrated()
    return all_account_ids()


def _run_for_all_accounts(
    label: str,
    runner,
) -> tuple[str, str, dict]:
    """对全部账户执行 runner(account_id) -> (status, msg, detail)。"""
    ids = _all_accounts()
    if not ids:
        return "error", "无可用账户", {}
    results = {}
    ok_n = skip_n = err_n = 0
    for aid in ids:
        try:
            st, msg, detail = runner(aid)
        except Exception as e:
            st, msg, detail = "error", str(e), {}
        results[aid] = {"status": st, "message": msg, "detail": detail}
        if st == "ok":
            ok_n += 1
        elif st == "skip":
            skip_n += 1
        else:
            err_n += 1
    if err_n:
        status = "error"
    elif ok_n:
        status = "ok"
    else:
        status = "skip"
    msg = f"{label}: {len(ids)} 账户 · 成功{ok_n}/跳过{skip_n}/失败{err_n}"
    return status, msg, {"accounts": results}


def _append_history(job_stat: dict, entry: dict, keep: int = 30) -> None:
    hist = job_stat.setdefault("history", [])
    hist.append(entry)
    job_stat["history"] = hist[-keep:]


def record_run(job_id: str, status: str, message: str, detail: dict | None = None) -> dict:
    st = load_status()
    jobs = st.setdefault("jobs", {})
    job = jobs.setdefault(job_id, {})
    now = _now()
    today = now.strftime("%Y-%m-%d")
    entry = {
        "time": now.isoformat(),
        "status": status,
        "message": message,
        "detail": detail or {},
    }
    job["last_run"] = entry["time"]
    job["last_status"] = status
    job["last_message"] = message
    job["last_detail"] = detail or {}
    if job.get("last_date") != today:
        job["runs_today"] = 0
        job["last_date"] = today
    job["runs_today"] = int(job.get("runs_today", 0)) + 1
    _append_history(job, entry)
    save_status(st)
    return entry


def execute_job(job_id: str, *, force: bool = False) -> dict:
    if job_id not in JOB_BY_ID:
        raise SystemExit(f"未知任务: {job_id}，可用: {', '.join(JOB_BY_ID)}")

    meta = JOB_BY_ID[job_id]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = _now()
    log_file = LOG_DIR / f"{now.strftime('%Y%m%d')}_{job_id}.log"

    trading, reason = is_trading_day(now)
    # 健康检查在交易日收盘后也要跑；非交易日全部跳过（force 除外）
    if not trading and not force:
        entry = record_run(job_id, "skip", reason)
        _write_log(log_file, f"SKIP {reason}\n")
        return {"job_id": job_id, "name": meta["name"], **entry}

    try:
        if job_id in ("morning_0930", "morning_0950"):
            def _m(aid):
                code, out, err = run_cmd(
                    ["monitor.py", "--mode", "morning"], timeout=180, account_id=aid
                )
                return _interpret_monitor(code, out, err)
            status, msg, detail = _run_for_all_accounts("早盘盯盘", _m)
        elif job_id == "open_0945":
            status, msg, detail = _run_open_all()
        elif job_id == "midday":
            def _m(aid):
                code, out, err = run_cmd(
                    ["monitor.py", "--mode", "afternoon"], timeout=180, account_id=aid
                )
                return _interpret_monitor(code, out, err)
            status, msg, detail = _run_for_all_accounts("上午盯盘", _m)
        elif job_id == "afternoon":
            def _m(aid):
                code, out, err = run_cmd(
                    ["monitor.py", "--mode", "afternoon"], timeout=180, account_id=aid
                )
                return _interpret_monitor(code, out, err)
            status, msg, detail = _run_for_all_accounts("午后盯盘", _m)
        elif job_id == "closing":
            def _m(aid):
                code, out, err = run_cmd(
                    ["monitor.py", "--mode", "closing"], timeout=240, account_id=aid
                )
                return _interpret_monitor(code, out, err)
            status, msg, detail = _run_for_all_accounts("尾盘风控", _m)
        elif job_id == "report":
            def _r(aid):
                code, out, err = run_cmd(["daily_report.py"], timeout=180, account_id=aid)
                meta = {}
                for line in reversed((err or "").strip().splitlines() or []):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            meta = json.loads(line)
                            break
                        except json.JSONDecodeError:
                            continue
                if code == 0:
                    return "ok", "日报已生成", {
                        "report_file": meta.get("report_file"),
                        "preview": (out or "")[:800],
                    }
                return "error", (out or err)[:300], {"stderr": (err or "")[-400:]}
            status, msg, detail = _run_for_all_accounts("收盘日报", _r)
        elif job_id == "health":
            def _h(aid):
                code, out, err = run_cmd(
                    ["health_check.py", "--send"], timeout=120, account_id=aid
                )
                try:
                    data = json.loads(out.strip()) if out.strip() else {}
                except json.JSONDecodeError:
                    data = {"raw": out[:500]}
                if code == 0 and data.get("status") == "ok":
                    return "ok", "健康检查通过", data
                return "error", "健康检查异常", data
            status, msg, detail = _run_for_all_accounts("健康检查", _h)
        elif job_id == "weekly":
            # 周五专属；权重全局共享，只跑一次
            if now.weekday() != 4 and not force:
                status, msg, detail = "skip", "非周五，跳过周度优化", {}
            else:
                code, out, err = run_cmd(["weekly_optimize.py"], timeout=60)
                try:
                    data = json.loads(out.strip()) if out.strip() else {}
                except json.JSONDecodeError:
                    data = {"raw": out[:500]}
                if code == 0 and data.get("status") == "ok":
                    status, msg, detail = "ok", "权重已回归优化", data
                else:
                    status, msg, detail = "error", data.get("error", err[:200]), data
        else:
            status, msg, detail = "error", f"未实现: {job_id}", {}
    except subprocess.TimeoutExpired:
        status, msg, detail = "error", "执行超时", {}
    except Exception as e:
        status, msg, detail = "error", str(e), {}

    entry = record_run(job_id, status, msg, detail)
    _write_log(log_file, f"{status.upper()} {msg}\n{json.dumps(detail, ensure_ascii=False)[:2000]}\n")
    return {"job_id": job_id, "name": meta["name"], **entry}


def _write_log(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{_now().strftime('%H:%M:%S')}] {text}")


def _interpret_monitor(code: int, out: str, err: str) -> tuple[str, str, dict]:
    text = (out or "").strip()
    if text == "NO_REPLY":
        return "skip", "非交易时段或无需操作 (NO_REPLY)", {"stderr": err[-300:]}
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        data = {"raw": text[:500]}
    if code != 0 and "error" in data:
        return "error", str(data.get("error")), data
    actions = data.get("actions_taken") or []
    alerts = data.get("alerts") or []
    msg = f"动作{len(actions)} / 告警{len(alerts)}"
    return "ok", msg, data if isinstance(data, dict) else {"raw": data}


def _join_stock_names(names: list, limit: int = 4) -> str:
    seen: list[str] = []
    for n in names:
        n = str(n or "").strip()
        if n and n not in seen:
            seen.append(n)
        if len(seen) >= max(1, limit):
            break
    return "、".join(seen)


def format_open_summary(account_ids: list, per_account: dict) -> str:
    """说明列：待补仓N个（股票名）· 买入M笔。"""
    slots = 0
    bought_names: list[str] = []
    pending_names: list[str] = []
    for entry in (per_account or {}).values():
        trade = entry.get("auto_trade") or {}
        buys = trade.get("buys") or []
        n_slots = int(trade.get("slots_needed") or entry.get("slots_needed") or 0)
        if n_slots <= 0 and not buys:
            continue
        slots += n_slots
        for b in buys:
            bought_names.append(b.get("name") or b.get("code") or "")
        remain = max(0, n_slots - len(buys))
        if remain:
            for s in trade.get("skipped") or []:
                if remain <= 0:
                    break
                pending_names.append(s.get("name") or s.get("code") or "")
                remain -= 1
    show_names = bought_names + pending_names
    slot_part = f"待补仓{slots}个"
    name_s = _join_stock_names(show_names, limit=max(slots, 1))
    if slots and name_s:
        slot_part += f"（{name_s}）"
    return (
        f"开盘建仓: {len(account_ids)} 账户 · {slot_part} · 买入{len(bought_names)}笔"
    )


def _run_open_all() -> tuple[str, str, dict]:
    """开盘建仓：全账户先盯盘风控；对未满仓账户选股评分并按 regime 自动买入。"""
    sys.path.insert(0, str(SCRIPTS))
    from account_manager import load_account_data
    from auto_open import pick_and_buy, slots_needed, buy_block_reason

    ids = _all_accounts()
    if not ids:
        return "error", "无可用账户", {}

    per_account = {}
    need_buy_ids = []
    for aid in ids:
        code, out, err = run_cmd(
            ["monitor.py", "--mode", "morning"], timeout=180, account_id=aid
        )
        mon_status, mon_msg, mon_detail = _interpret_monitor(code, out, err)
        acc = load_account_data(aid)
        positions = (acc or {}).get("positions") or {}
        need = slots_needed(acc) if acc else 0
        block = buy_block_reason(
            mon_detail if isinstance(mon_detail, dict) else None,
            acc or {},
        )
        entry = {
            "monitor_status": mon_status,
            "monitor_message": mon_msg,
            "monitor": mon_detail,
            "positions_count": len(positions),
            "slots_needed": need,
            "buy_blocked": block,
        }
        if need > 0 and not block and mon_status != "error":
            need_buy_ids.append(aid)
            entry["need_screen"] = True
        else:
            entry["need_screen"] = False
            if block:
                entry["message"] = f"跳过建仓（{block}）"
            elif need <= 0:
                entry["message"] = f"已满仓，跳过选股（{mon_msg}）"
            else:
                entry["message"] = f"跳过建仓（盯盘 {mon_status}: {mon_msg}）"
        per_account[aid] = entry

    screen_payload = None
    scored: list = []
    if need_buy_ids:
        sc_code, sc_out, sc_err = run_cmd(
            ["stock_screener.py", "--top", "10", "--json"], timeout=300
        )
        try:
            screen = json.loads(sc_out.strip()) if sc_out.strip() else {}
        except json.JSONDecodeError:
            return "error", "选股输出非 JSON", {
                "accounts": per_account, "stderr": sc_err[-300:]
            }
        if sc_code != 0 or "candidates" not in screen:
            return "error", "选股失败", {"accounts": per_account, "stderr": sc_err[-300:]}

        score_proc = subprocess.run(
            [_python(), str(SCRIPTS / "factor_scorer.py"), "--top", "8", "--json"],
            input=json.dumps(screen, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(ROOT),
        )
        try:
            scored = json.loads(score_proc.stdout.strip()) if score_proc.stdout.strip() else []
        except json.JSONDecodeError:
            scored = []
        if not isinstance(scored, list):
            scored = []

        today = _now().strftime("%Y-%m-%d")
        buy_ok = buy_skip = buy_err = 0
        for aid in need_buy_ids:
            out_file = DATA / f"open_candidates_{today}_{aid}.json"
            payload = {
                "date": today,
                "account_id": aid,
                "time": _now().isoformat(),
                "candidates": scored,
                "screen_count": len(screen.get("candidates") or []),
            }
            out_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            mon = per_account[aid].get("monitor")
            trade = pick_and_buy(
                aid,
                scored,
                monitor_detail=mon if isinstance(mon, dict) else None,
            )
            per_account[aid]["candidates_file"] = str(out_file)
            per_account[aid]["auto_trade"] = trade
            per_account[aid]["status"] = trade.get("status", "error")
            n_buys = len(trade.get("buys") or [])
            per_account[aid]["message"] = (
                f"{trade.get('message')} · 候选{len(scored)} · 成交{n_buys}"
            )
            st = trade.get("status")
            if st == "ok":
                buy_ok += 1
            elif st == "skip":
                buy_skip += 1
            else:
                buy_err += 1

        screen_payload = {
            "need_buy_accounts": need_buy_ids,
            "top": scored[:5],
            "auto_trade_summary": {
                "ok": buy_ok, "skip": buy_skip, "error": buy_err,
            },
        }

    err_n = sum(
        1 for v in per_account.values()
        if v.get("monitor_status") == "error" or v.get("status") == "error"
    )
    ok_n = sum(
        1 for v in per_account.values()
        if v.get("status") == "ok"
        or (v.get("monitor_status") == "ok" and not v.get("need_screen"))
    )
    status = "error" if err_n else ("ok" if ok_n or need_buy_ids else "skip")
    msg = format_open_summary(ids, per_account)
    return status, msg, {"accounts": per_account, "screen": screen_payload}


def crontab_block() -> str:
    py = _python()
    runner = str(SCRIPTS / "cron_runner.py")
    lines = [MARKER_BEGIN, f"# StockTrader-AI auto schedule @ {ROOT}"]
    for job in SCHEDULE:
        # 重定向日志
        log = str(LOG_DIR / f"cron_{job['id']}.log")
        cmd = f'cd "{ROOT}" && "{py}" "{runner}" {job["id"]} >> "{log}" 2>&1'
        lines.append(f'{job["cron"]} {cmd}  # {job["name"]}')
    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def _run_crontab(args: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
    """调用 crontab；macOS / Streamlit 沙箱可能拒绝执行。"""
    try:
        return subprocess.run(
            ["crontab", *args],
            input=input_text,
            capture_output=True,
            text=True,
        )
    except (PermissionError, OSError, FileNotFoundError) as e:
        fake = subprocess.CompletedProcess(
            args=["crontab", *args], returncode=1, stdout="", stderr=str(e),
        )
        fake._denied = True  # type: ignore[attr-defined]
        return fake


def get_crontab() -> str:
    r = _run_crontab(["-l"])
    # crontab -l 无内容或无权访问时可能 exit 1
    return r.stdout or ""


def crontab_readable() -> bool:
    """当前进程是否能执行 crontab。"""
    try:
        subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        return True
    except (PermissionError, OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_crontab_installed() -> bool:
    text = get_crontab()
    if MARKER_BEGIN in text and MARKER_END in text:
        return True
    if not crontab_readable():
        # 无法探测时沿用上次成功安装记录，避免把已安装显示成未安装
        return bool(load_status().get("installed"))
    return False


def install_crontab() -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not crontab_readable():
        err = "当前环境无权执行 crontab（macOS 隐私限制）。请在系统终端运行: python3 scripts/cron_runner.py --install-crontab"
        return {"ok": False, "error": err, "entries": 0}
    current = get_crontab()
    # 去掉旧块
    cleaned = re.sub(
        rf"{re.escape(MARKER_BEGIN)}.*?{re.escape(MARKER_END)}\n?",
        "",
        current,
        flags=re.S,
    ).rstrip()
    new_text = (cleaned + "\n\n" if cleaned else "") + crontab_block()
    proc = _run_crontab(["-"], input_text=new_text)
    ok = proc.returncode == 0
    st = load_status()
    st["installed"] = ok
    st["installed_at"] = _now().isoformat() if ok else st.get("installed_at")
    st["install_error"] = None if ok else (proc.stderr or proc.stdout or "crontab 失败")
    save_status(st)
    return {"ok": ok, "error": st.get("install_error"), "entries": len(SCHEDULE)}


def uninstall_crontab() -> dict:
    if not crontab_readable():
        return {
            "ok": False,
            "error": "当前环境无权执行 crontab。请在系统终端运行: python3 scripts/cron_runner.py --uninstall-crontab",
        }
    current = get_crontab()
    if MARKER_BEGIN not in current:
        return {"ok": True, "message": "未安装，无需卸载"}
    cleaned = re.sub(
        rf"{re.escape(MARKER_BEGIN)}.*?{re.escape(MARKER_END)}\n?",
        "",
        current,
        flags=re.S,
    ).strip()
    if cleaned:
        proc = _run_crontab(["-"], input_text=cleaned + "\n")
    else:
        proc = _run_crontab(["-r"])
        # 有的系统无 crontab 时 -r 会失败，忽略
        if proc.returncode != 0:
            proc = _run_crontab(["-"], input_text="\n")
    ok = proc.returncode == 0
    st = load_status()
    st["installed"] = False if ok else is_crontab_installed()
    st["uninstalled_at"] = _now().isoformat() if ok else None
    save_status(st)
    return {"ok": ok, "error": None if ok else (proc.stderr or "卸载失败")}


def _parse_cron_times_today(job: dict, now: datetime) -> list[datetime]:
    """根据简化 cron（分 时 * * 星期）生成今日触发时刻。"""
    parts = job["cron"].split()
    if len(parts) < 5:
        return []
    mins_s, hours_s, _, _, dow_s = parts[:5]

    def expand(field: str, lo: int, hi: int) -> list[int]:
        vals = set()
        for piece in field.split(","):
            if piece == "*":
                vals.update(range(lo, hi + 1))
            elif "-" in piece:
                a, b = piece.split("-", 1)
                vals.update(range(int(a), int(b) + 1))
            else:
                vals.add(int(piece))
        return sorted(v for v in vals if lo <= v <= hi)

    # cron DOW: 0=Sun, 1=Mon ... 5=Fri；Python: Mon=0
    if dow_s != "*":
        allowed = set(expand(dow_s, 0, 7))
        if 7 in allowed:  # 部分实现 7=周日
            allowed.add(0)
        cron_dow = (now.weekday() + 1) % 7
        if cron_dow not in allowed:
            return []

    hours = expand(hours_s, 0, 23)
    mins = expand(mins_s, 0, 59)
    return [
        now.replace(hour=h, minute=m, second=0, microsecond=0)
        for h in hours for m in mins
    ]


def build_status_payload() -> dict:
    now = _now()
    trading, trading_reason = is_trading_day(now)
    installed = is_crontab_installed()
    st = load_status()
    # 读 crontab 失败时不要把已安装状态写成未安装
    if crontab_readable() or installed:
        st["installed"] = installed
        save_status(st)

    today = now.strftime("%Y-%m-%d")
    rows = []
    for job in SCHEDULE:
        times = _parse_cron_times_today(job, now)
        past = [t for t in times if t <= now]
        future = [t for t in times if t > now]
        last_expected = past[-1].strftime("%H:%M") if past else None
        next_expected = future[0].strftime("%H:%M") if future else None
        jstat = (st.get("jobs") or {}).get(job["id"], {})
        # 跨日未跑的任务仍可能残留旧 runs_today，展示时按日期清零
        runs_today = int(jstat.get("runs_today") or 0) if jstat.get("last_date") == today else 0

        due_state = "idle"
        if not trading:
            due_state = "holiday_skip"
        elif past and runs_today < len(past):
            # 已过点但实际执行次数不足 → 漏跑（常见于 Mac 睡眠错过 cron）
            due_state = "missed" if not future else "missed_in_progress"
        elif next_expected is None and last_expected:
            due_state = "done_today"
        elif next_expected and not past:
            due_state = "pending"
        elif next_expected and past:
            due_state = "in_progress"

        last_message = jstat.get("last_message")
        if job["id"] == "open_0945":
            detail = jstat.get("last_detail") or {}
            accounts = detail.get("accounts") or {}
            if accounts:
                last_message = format_open_summary(list(accounts.keys()), accounts)

        rows.append({
            **job,
            "installed": installed,
            "last_run": jstat.get("last_run"),
            "last_status": jstat.get("last_status"),
            "last_message": last_message,
            "runs_today": runs_today,
            "last_expected_today": last_expected,
            "next_expected_today": next_expected,
            "schedule_times": job["times"],
            "due_state": due_state,
        })

    # 今日盯盘次数统计（按计划；与展示一致，按 last_date 过滤）
    monitor_planned = sum(
        len(j["times"]) for j in SCHEDULE if j["group"] == "盯盘"
    )
    monitor_runs = sum(
        int(r.get("runs_today") or 0)
        for r in rows if r.get("group") == "盯盘"
    )

    return {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
        "trading_day": trading,
        "trading_reason": trading_reason,
        "crontab_installed": installed,
        "monitor_planned_today": monitor_planned if trading and now.weekday() < 5 else 0,
        "monitor_runs_today": monitor_runs,
        "jobs": rows,
        "status_file": str(STATUS_FILE),
        "log_dir": str(LOG_DIR),
    }


def main():
    parser = argparse.ArgumentParser(description="StockTrader-AI Cron Runner")
    parser.add_argument("job_id", nargs="?", help="任务 ID")
    parser.add_argument("--status", action="store_true", help="输出调度状态 JSON")
    parser.add_argument("--list", action="store_true", help="列出任务")
    parser.add_argument("--install-crontab", action="store_true", help="安装到用户 crontab")
    parser.add_argument("--uninstall-crontab", action="store_true", help="从 crontab 卸载")
    parser.add_argument("--force", action="store_true", help="忽略交易日/周五限制强制执行")
    parser.add_argument("--print-crontab", action="store_true", help="打印将安装的 crontab 块")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(build_status_payload(), ensure_ascii=False, indent=2))
        return
    if args.list:
        print(json.dumps(SCHEDULE, ensure_ascii=False, indent=2))
        return
    if args.print_crontab:
        print(crontab_block())
        return
    if args.install_crontab:
        print(json.dumps(install_crontab(), ensure_ascii=False, indent=2))
        return
    if args.uninstall_crontab:
        print(json.dumps(uninstall_crontab(), ensure_ascii=False, indent=2))
        return
    if not args.job_id:
        parser.print_help()
        sys.exit(1)

    result = execute_job(args.job_id, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") in ("ok", "skip") else 1)


if __name__ == "__main__":
    main()
