#!/usr/bin/env python3
"""
Market Regime 分类器 v2
双维度判定：技术面（120根日K线）+ 情绪面
输出：bull / range / bear + 置信度 + 参数库
新增：ATR 动态止损参数、涨幅窗口 1-9.5%
"""
import json, os, sys, subprocess, re, math, time
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
_CURL_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _direct_env():
    """行情接口直连。Cursor/Streamlit 会注入 HTTP_PROXY，curl 走代理后常返回空。"""
    env = os.environ.copy()
    for key in list(env):
        if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "ftp_proxy"):
            env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


# === 数据获取 ===
def _curl(url, timeout=15):
    r = subprocess.run(
        [
            "curl", "-s", "--noproxy", "*", "--connect-timeout", "10",
            "-A", _CURL_UA,
            "-H", "Referer: https://finance.qq.com/",
            url,
        ],
        capture_output=True, timeout=timeout, env=_direct_env(),
    )
    return (r.stdout or b"").decode("utf-8", errors="ignore").strip()


def _parse_tencent_kline(raw, code):
    if not raw:
        return []
    data = json.loads(raw)
    klines = (data.get("data") or {}).get(code) or {}
    return klines.get("qfqday") or klines.get("day") or klines.get("qfq") or []


def _parse_sina_kline(raw):
    """新浪: {day,open,high,low,close,volume} → 腾讯行 [date,open,close,high,low,volume]"""
    if not raw:
        return []
    items = json.loads(raw)
    if not isinstance(items, list):
        return []
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rows.append([
            it.get("day") or it.get("date") or "",
            str(it.get("open") or 0),
            str(it.get("close") or 0),
            str(it.get("high") or 0),
            str(it.get("low") or 0),
            str(it.get("volume") or 0),
        ])
    return rows


def get_kline(code="sh000001", days=120, max_retries=3):
    urls = [
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq",
        f"https://web.ifzq.gtimg.cn/appstock/app/kline/get?param={code},day,,,{days},qfq",
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},",
    ]
    sina = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={days}"
    )
    last_err = None
    for attempt in range(max_retries):
        try:
            for url in urls:
                rows = _parse_tencent_kline(_curl(url), code)
                if rows:
                    return rows
            rows = _parse_sina_kline(_curl(sina, timeout=15))
            if rows:
                return rows
        except Exception as e:
            last_err = e
        if attempt < max_retries - 1:
            time.sleep(1 * (attempt + 1))
    raise RuntimeError(
        f"No kline data for {code} after {max_retries} retries"
        + (f" ({last_err})" if last_err else "")
    )

def curl_with_retry(args, max_retries=3, timeout=15):
    """带重试的 curl 封装"""
    import time
    for attempt in range(max_retries):
        try:
            r = subprocess.run(
                ["curl", "-s", "--noproxy", "*", "-A", _CURL_UA] + args,
                capture_output=True, timeout=timeout, env=_direct_env(),
            )
            return r.stdout.decode("utf-8", errors="ignore").strip()
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(1 * (attempt + 1))
    return ""

def get_emotion_data():
    try:
        total_text = curl_with_retry(["--connect-timeout", "5",
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount?node=hs_a"], timeout=10)
        if not total_text:
            return None
        total = int(json.loads(total_text))

        top_text = curl_with_retry(["--connect-timeout", "10",
            "-H", "Referer: https://finance.sina.com.cn/",
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=80&sort=changepercent&asc=0&node=hs_a"], timeout=15)
        if not top_text:
            return None
        top_items = json.loads(top_text)

        bottom_text = curl_with_retry(["--connect-timeout", "10",
            "-H", "Referer: https://finance.sina.com.cn/",
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=80&sort=changepercent&asc=1&node=hs_a"], timeout=15)
        if not bottom_text:
            return None
        bottom_items = json.loads(bottom_text)

        up_in_top = sum(1 for s in top_items if float(s.get("changepercent", 0)) > 0)
        down_in_bottom = sum(1 for s in bottom_items if float(s.get("changepercent", 0)) < 0)
        limit_up = sum(1 for s in top_items if float(s.get("changepercent", 0)) >= 9.9 and float(s.get("buy", 0)) > 0)
        limit_down = sum(1 for s in bottom_items if float(s.get("changepercent", 0)) <= -9.9 and float(s.get("sell", 0)) == 0)

        if up_in_top == 80:
            up = int(total * 0.8)
        elif up_in_top > 60:
            up = int(total * up_in_top / 80 * 0.7)
        else:
            up = int(total * up_in_top / 80 * 0.5)

        if down_in_bottom == 80:
            down = int(total * 0.8)
        elif down_in_bottom > 60:
            down = int(total * down_in_bottom / 80 * 0.7)
        else:
            down = int(total * down_in_bottom / 80 * 0.5)

        idx_text = curl_with_retry(["--connect-timeout", "5", "https://qt.gtimg.cn/q=sh000001"], timeout=10)
        idx_fields = idx_text.split("~")
        total_amount = float(idx_fields[37]) / 10000 if len(idx_fields) > 37 else 0

        return {
            "up": up, "down": down,
            "limit_up": limit_up, "limit_down": limit_down,
            "total_amount": total_amount, "total": total,
            "top_items": top_items[:40],
        }
    except Exception:
        return None


def get_kline_once(code, days=16):
    """单次拉取日K，供风格判定（不做三重重试）。"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
    try:
        rows = _parse_tencent_kline(_curl(url), code)
        if rows:
            return rows
    except Exception:
        pass
    return []


def _to_market_code(code):
    c = str(code).lstrip("shszSHSZ")
    return f"sh{c}" if c.startswith(("6", "9")) else f"sz{c}"


def _limit_threshold(code, name):
    if "ST" in (name or "").upper():
        return 0.047
    c = str(code).lstrip("shszSHSZ")
    if c.startswith(("300", "301", "688")):
        return 0.195
    return 0.095


def count_lianban(rows, code, name):
    """从最近一根K往回数连续涨停天数。"""
    if not rows or len(rows) < 2:
        return 0
    limit = _limit_threshold(code, name)
    n = 0
    for i in range(len(rows) - 1, 0, -1):
        prev = float(rows[i - 1][2])
        close = float(rows[i][2])
        if prev <= 0:
            break
        if (close - prev) / prev >= limit - 0.003:
            n += 1
        else:
            break
    return n


def _index_change(code, days=21):
    rows = get_kline_once(code, days + 5)
    closes = [float(r[2]) for r in rows if len(r) > 2]
    if len(closes) < days:
        return None
    return round((closes[-1] - closes[-days]) / closes[-days] * 100, 2)


def _skip_leader_candidate(code, name, change_pct):
    name = name or ""
    code = str(code).lstrip("shszSHSZ")
    if name.startswith(("N", "C")):
        return True
    if "ST" in name.upper():
        return True
    if code.startswith(("8", "4", "92")):
        return True
    if change_pct > 44:
        return True
    return False


def classify_leader_style(top_items=None, adx=0):
    """领涨龙头风格：趋势 / 小盘连板 / 分化。"""
    items = list(top_items or [])
    analyzed = []
    for it in items:
        if len(analyzed) >= 12:
            break
        code = str(it.get("code") or "")
        name = it.get("name") or code
        try:
            chg = float(it.get("changepercent") or 0)
        except (TypeError, ValueError):
            chg = 0.0
        if _skip_leader_candidate(code, name, chg):
            continue
        rows = get_kline_once(_to_market_code(code), 16)
        lianban = count_lianban(rows, code, name)
        try:
            nmc = float(it.get("nmc") or 0) / 10000.0  # 万元 → 亿
        except (TypeError, ValueError):
            nmc = 0.0
        try:
            turnover = float(it.get("turnoverratio") or 0)
            amount = float(it.get("amount") or 0) / 1e8
        except (TypeError, ValueError):
            turnover, amount = 0.0, 0.0
        analyzed.append({
            "code": code,
            "name": name,
            "change_pct": round(chg, 2),
            "lianban": lianban,
            "float_cap_yi": round(nmc, 1),
            "turnover": round(turnover, 2),
            "amount_yi": round(amount, 2),
        })

    hs300 = _index_change("sh000300")
    csi1000 = _index_change("sh000852")
    gem = _index_change("sz399006")
    small_rs = None
    if hs300 is not None and csi1000 is not None:
        small_rs = round(csi1000 - hs300, 2)

    board_pts = 0
    trend_pts = 0
    max_lb = max((x["lianban"] for x in analyzed), default=0)
    n_lb2 = sum(1 for x in analyzed if x["lianban"] >= 2)
    n_lb3 = sum(1 for x in analyzed if x["lianban"] >= 3)
    zt = [x for x in analyzed if x["change_pct"] >= 9.5 or x["lianban"] >= 1]
    small_zt = [x for x in zt if 0 < x["float_cap_yi"] < 80]
    small_share = (len(small_zt) / len(zt)) if zt else 0
    caps = [x["float_cap_yi"] for x in analyzed if x["float_cap_yi"] > 0]
    caps_sorted = sorted(caps)
    median_cap = caps_sorted[len(caps_sorted) // 2] if caps_sorted else None
    mid_trend_leaders = [
        x for x in analyzed
        if x["float_cap_yi"] >= 200 and 2 <= x["change_pct"] < 9.5 and x["lianban"] <= 1
    ]

    if max_lb >= 5:
        board_pts += 3
    elif max_lb >= 3:
        board_pts += 2
    elif max_lb >= 2:
        board_pts += 1
    if n_lb3 >= 2:
        board_pts += 2
    elif n_lb2 >= 3:
        board_pts += 1
    if zt and small_share >= 0.6:
        board_pts += 2
    if median_cap is not None and median_cap < 50:
        board_pts += 1
    if small_rs is not None and small_rs >= 3:
        board_pts += 1

    if max_lb <= 1:
        trend_pts += 2
    if mid_trend_leaders:
        trend_pts += 2
    if median_cap is not None and median_cap >= 150:
        trend_pts += 1
    if small_rs is not None and small_rs <= -1:
        trend_pts += 1
    if adx >= 25:
        trend_pts += 1

    if board_pts >= trend_pts + 2:
        style, style_cn = "consecutive_board", "小盘连板"
        conf = min(100, 55 + (board_pts - trend_pts) * 12)
    elif trend_pts >= board_pts + 2:
        style, style_cn = "trend", "趋势"
        conf = min(100, 55 + (trend_pts - board_pts) * 12)
    else:
        style, style_cn = "mixed", "分化"
        conf = max(40, 70 - abs(board_pts - trend_pts) * 8)

    leaders = sorted(
        analyzed,
        key=lambda x: (x["lianban"], x["change_pct"], x["amount_yi"]),
        reverse=True,
    )[:5]
    reason_bits = [
        f"最高连板{max_lb}天",
        f"2板及以上{n_lb2}只",
        f"涨停中小盘占比{small_share:.0%}" if zt else "近端无有效涨停样本",
    ]
    if small_rs is not None:
        reason_bits.append(f"中证1000相对沪深300 {small_rs:+.1f}%")
    if median_cap is not None:
        reason_bits.append(f"领涨流通市值中位数{median_cap:.0f}亿")

    return {
        "leader_style": style,
        "leader_style_cn": style_cn,
        "leader_style_confidence": round(conf, 1),
        "board_score": board_pts,
        "trend_score": trend_pts,
        "leaders": leaders,
        "style_reason": "；".join(reason_bits),
        "style_metrics": {
            "max_lianban": max_lb,
            "lianban_ge2": n_lb2,
            "lianban_ge3": n_lb3,
            "zt_small_share": round(small_share, 2),
            "median_float_cap_yi": round(median_cap, 1) if median_cap is not None else None,
            "hs300_20d": hs300,
            "csi1000_20d": csi1000,
            "gem_20d": gem,
            "small_vs_large_20d": small_rs,
        },
    }

# === 技术指标 ===
def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(-period, 0):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calc_volatility(closes, period=20):
    if len(closes) < period:
        return 0
    subset = closes[-period:]
    mean = sum(subset) / period
    variance = sum((x - mean) ** 2 for x in subset) / period
    return math.sqrt(variance) / mean * 100

def calc_adx(klines, period=14):
    """计算 ADX（Average Directional Index）趋势强度指标"""
    if len(klines) < period + 1:
        return 0, 0, 0  # adx, +di, -di
    true_ranges = []
    plus_dm = []
    minus_dm = []
    for i in range(1, len(klines)):
        high = float(klines[i][3])
        low = float(klines[i][4])
        prev_high = float(klines[i-1][3])
        prev_low = float(klines[i-1][4])
        prev_close = float(klines[i-1][2])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
    # Wilder 平滑
    def wilder_smooth(data, p):
        result = [sum(data[:p])]
        for i in range(p, len(data)):
            result.append(result[-1] - result[-1] / p + data[i])
        return result
    tr_smoothed = wilder_smooth(true_ranges, period)
    plus_smoothed = wilder_smooth(plus_dm, period)
    minus_smoothed = wilder_smooth(minus_dm, period)
    if not tr_smoothed or tr_smoothed[-1] == 0:
        return 0, 0, 0
    plus_di = 100 * plus_smoothed[-1] / tr_smoothed[-1]
    minus_di = 100 * minus_smoothed[-1] / tr_smoothed[-1]
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0, round(plus_di, 1), round(minus_di, 1)
    dx = abs(plus_di - minus_di) / di_sum * 100
    # ADX 是 DX 的 14 日均值（简化：取最近 DX 序列的均值）
    adx = dx  # 简化为当前 DX，足够判断趋势强度
    return round(adx, 1), round(plus_di, 1), round(minus_di, 1)

def calc_index_atr(klines, period=20):
    """计算指数 ATR（用于动态止损基准）"""
    if len(klines) < 2:
        return 0
    true_ranges = []
    for i in range(1, len(klines)):
        high = float(klines[i][3])
        low = float(klines[i][4])
        prev_close = float(klines[i-1][2])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    return sum(true_ranges[-period:]) / min(len(true_ranges), period)

def classify_regime():
    rows = get_kline("sh000001", 120)
    closes = [float(r[2]) for r in rows]

    if len(closes) < 60:
        return {"regime": "range", "confidence": 0, "error": "数据不足"}

    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    change_20d = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0
    rsi = calc_rsi(closes, 14)
    volatility = calc_volatility(closes, 20)
    index_atr = calc_index_atr(rows, 20)

    ma20_series = []
    for i in range(25, len(closes) + 1):
        ma20_series.append(sum(closes[i-20:i]) / 20)
    ma20_slope = 0
    if len(ma20_series) >= 25:
        recent = sum(ma20_series[-5:]) / 5
        old = sum(ma20_series[-25:-20]) / 5
        ma20_slope = (recent - old) / old * 100

    # ADX 趋势强度
    adx, plus_di, minus_di = calc_adx(rows, 14)

    # 成交额趋势（3日均量 vs 20日均量）
    amounts = [float(r[5]) * float(r[2]) / 1e8 for r in rows if len(r) > 5]  # 亿元
    vol_trend = None
    if len(amounts) >= 20:
        avg_3 = sum(amounts[-3:]) / 3
        avg_20 = sum(amounts[-20:]) / 20
        vol_trend = round(avg_3 / avg_20, 2) if avg_20 > 0 else None

    # Bull 打分
    bull_score = 0
    if ma20 and ma60 and ma20 > ma60 * 1.02:
        bull_score += 2
    if change_20d > 5:
        bull_score += 2
    elif change_20d > 3:
        bull_score += 1
    if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
        bull_score += 1
    if rsi > 55:
        bull_score += 1
    if ma20_slope > 0.5:
        bull_score += 1
    # ADX 强趋势加分
    if adx >= 25 and plus_di > minus_di:
        bull_score += 1

    # Bear 打分
    bear_score = 0
    if ma20 and ma60 and ma20 < ma60 * 0.98:
        bear_score += 2
    if change_20d < -5:
        bear_score += 2
    elif change_20d < -3:
        bear_score += 1
    if ma5 and ma10 and ma20 and ma5 < ma10 < ma20:
        bear_score += 1
    if rsi < 40:
        bear_score += 1
    if ma20_slope < -0.5:
        bear_score += 1
    # ADX 强下跌趋势加分
    if adx >= 25 and minus_di > plus_di:
        bear_score += 1

    # 情绪面调整
    emotion = get_emotion_data()
    emotion_adj = 0
    emotion_detail = {}
    if emotion:
        ratio = emotion["up"] / max(emotion["down"], 1)
        emotion_detail["涨跌比"] = round(ratio, 2)
        emotion_detail["涨停"] = emotion["limit_up"]
        emotion_detail["跌停"] = emotion["limit_down"]
        emotion_detail["成交额(亿)"] = round(emotion["total_amount"], 0)

        if ratio >= 2.0:
            emotion_adj += 2
        elif ratio >= 1.5:
            emotion_adj += 1
        elif ratio <= 0.5:
            emotion_adj -= 2
        elif ratio <= 0.7:
            emotion_adj -= 1

        if emotion["limit_up"] >= 80:
            emotion_adj += 1
        elif emotion["limit_up"] <= 15:
            emotion_adj -= 1
        if emotion["limit_down"] >= 30:
            emotion_adj -= 2
        if emotion["total_amount"] < 7000:
            emotion_adj -= 1
        elif emotion["total_amount"] > 15000:
            emotion_adj += 1

    bull_score += max(0, emotion_adj)
    bear_score += max(0, -emotion_adj)

    # 判定
    if bull_score >= 4 and bull_score > bear_score:
        regime = "bull"
        confidence = min(bull_score / 8 * 100, 100)
    elif bear_score >= 4 and bear_score > bull_score:
        regime = "bear"
        confidence = min(bear_score / 8 * 100, 100)
    else:
        regime = "range"
        confidence = max(0, 100 - abs(bull_score - bear_score) * 15)

    # 强趋势标记（ADX >= 25 且置信度 >= 60）
    strong_trend = adx >= 25 and confidence >= 60

    # 参数库（ATR 动态止损 + 涨幅窗口 1-9.5%）
    params = {
        "bull": {
            "stop_loss": -0.08, "tp1": 0.15, "tp2": 0.25, "tp3": 0.35,
            "max_position": 0.90, "max_holdings": 4,
            "min_change": 1, "max_change": 9.5,
            "atr_multiplier": 1.5,
        },
        "range": {
            "stop_loss": -0.07, "tp1": 0.15, "tp2": 0.25, "tp3": 0.35,
            "max_position": 0.85, "max_holdings": 3,
            "min_change": 1, "max_change": 9.5,
            "atr_multiplier": 1.5,
        },
        "bear": {
            "stop_loss": -0.04, "tp1": 0.10, "tp2": 0.18, "tp3": 0.25,
            "max_position": 0.60, "max_holdings": 2,
            "min_change": 1, "max_change": 7,
            "atr_multiplier": 1.2,
        },
    }

    # 强趋势时 ATR 倍数自适应（ADX >= 25：放宽止损防扫损）
    if strong_trend and adx >= 30:
        params[regime]["atr_multiplier"] = 1.8
    elif strong_trend:
        params[regime]["atr_multiplier"] = 1.6

    style_info = classify_leader_style(
        (emotion or {}).get("top_items") if emotion else None,
        adx=adx,
    )
    # 小盘连板：不追高度板，涨幅上限收紧
    if style_info.get("leader_style") == "consecutive_board":
        params[regime]["max_change"] = min(float(params[regime].get("max_change") or 9.5), 7.0)
        params[regime]["min_change"] = max(float(params[regime].get("min_change") or 1), 1.5)

    return {
        "regime": regime,
        "strong_trend": strong_trend,
        "confidence": round(confidence, 1),
        "bull_score": bull_score,
        "bear_score": bear_score,
        "emotion_adj": emotion_adj,
        "leader_style": style_info.get("leader_style"),
        "leader_style_cn": style_info.get("leader_style_cn"),
        "leader_style_confidence": style_info.get("leader_style_confidence"),
        "board_score": style_info.get("board_score"),
        "trend_score": style_info.get("trend_score"),
        "leaders": style_info.get("leaders") or [],
        "style_reason": style_info.get("style_reason"),
        "style_metrics": style_info.get("style_metrics") or {},
        "technicals": {
            "ma5": round(ma5, 2) if ma5 else None,
            "ma10": round(ma10, 2) if ma10 else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "ma60": round(ma60, 2) if ma60 else None,
            "change_20d": round(change_20d, 2),
            "rsi_14": round(rsi, 1),
            "volatility": round(volatility, 2),
            "ma20_slope": round(ma20_slope, 2),
            "index_atr": round(index_atr, 2),
            "adx": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "vol_trend_3d": vol_trend,
        },
        "emotion": emotion_detail,
        "params_active": params[regime],
        "timestamp": datetime.now(CST).isoformat(),
    }

if __name__ == "__main__":
    try:
        result = classify_regime()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)
