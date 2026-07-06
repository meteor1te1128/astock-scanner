"""每日短线筛选器（v2：划掉+标原因模式）。

显示门槛：MACD 柱由绿转红【首日】（历史≥35个交易日才计算，保证EMA可信）。
触发信号的股票全部返回，分为：
  pass   —— 通过全部过滤，正常显示
  reject —— 被以下任一规则划掉（原因全部列出）：
    价格在20日均线下方 / 缩量 / 成交额<3亿 / 弱于沪深300 /
    换手率<2% / 市值<50亿 / ST / 上市不足60日 /
    基本面：净利润大幅下滑、现金流质量差、应收异常、毛利率恶化、综合分<40
  警告（不划掉）：换手率>25% 过热提示
全部阈值已于 2026-07 与用户确认。
"""
import pandas as pd

MIN_MACD_DAYS = 35        # EMA(26)+DEA(9) 预热
MIN_LISTED_DAYS = 60      # 次新股线
MIN_AMOUNT_YUAN = 3e8
TURNOVER_MIN = 2.0        # %
TURNOVER_HOT = 25.0       # %
MCAP_MIN_YI = 50.0        # 亿
FUND_SCORE_MIN = 40


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """给单只股票日线补上 hist/ma20/vol_ma5 三列。"""
    from .indicators import macd, ma
    df = df.sort_values("date").reset_index(drop=True)
    _, _, df["hist"] = macd(df["close"])
    df["ma20"] = ma(df["close"], 20)
    df["vol_ma5"] = ma(df["volume"], 5)
    return df


def evaluate_stock(df: pd.DataFrame, name: str, index_pct_today: float,
                   mcap_yi: float | None = None, industry: str = "",
                   fund: dict | None = None) -> dict | None:
    """返回 None = 未触发信号（不显示）；否则返回含 status/reasons/warnings 的记录。
    df 需已 prepare()。fund: {"score": float, "flags": [str]} 或 None。"""
    if len(df) < MIN_MACD_DAYS:
        return None
    today, prev = df.iloc[-1], df.iloc[-2]
    if pd.isna(today["hist"]) or pd.isna(prev["hist"]):
        return None
    if not (today["hist"] > 0 and prev["hist"] <= 0):
        return None  # 不是红柱首日 → 不进列表

    reasons, warnings = [], []
    if "ST" in str(name).upper():
        reasons.append("ST风险警示股")
    if len(df) < MIN_LISTED_DAYS:
        reasons.append("上市不足60个交易日")
    if pd.isna(today["ma20"]) or today["close"] <= today["ma20"]:
        reasons.append("价格在20日均线下方")
    if pd.isna(today["vol_ma5"]) or today["volume"] <= today["vol_ma5"]:
        reasons.append("缩量（不足5日均量）")
    if today["amount"] <= MIN_AMOUNT_YUAN:
        reasons.append(f"成交额{today['amount'] / 1e8:.1f}亿不足3亿")
    pct_today = (today["close"] / prev["close"] - 1) * 100
    if pct_today <= index_pct_today:
        reasons.append("弱于沪深300")
    t = today.get("turnover")
    if t is not None and not pd.isna(t):
        if t < TURNOVER_MIN:
            reasons.append(f"换手率{t:.1f}%过低")
        elif t > TURNOVER_HOT:
            warnings.append(f"换手率{t:.1f}%过热")
    if mcap_yi is not None and not pd.isna(mcap_yi) and mcap_yi < MCAP_MIN_YI:
        reasons.append(f"市值{mcap_yi:.0f}亿过小")
    if fund:
        reasons += list(fund.get("flags") or [])
        s = fund.get("score")
        if s is not None and s < FUND_SCORE_MIN:
            reasons.append(f"基本面分过低（{s:.0f}）")

    return {
        "name": name, "industry": industry or "未知",
        "close": round(float(today["close"]), 2),
        "pct": round(float(pct_today), 2),
        "amount_yi": round(float(today["amount"]) / 1e8, 2),
        "turnover": None if t is None or pd.isna(t) else round(float(t), 2),
        "mcap_yi": None if mcap_yi is None or pd.isna(mcap_yi) else round(float(mcap_yi), 0),
        "hist": round(float(today["hist"]), 4),
        "above_ma20_pct": round((float(today["close"]) / float(today["ma20"]) - 1) * 100, 2)
        if not pd.isna(today["ma20"]) else None,
        "status": "pass" if not reasons else "reject",
        "reasons": reasons, "warnings": warnings,
        "fund_score": None if not fund else fund.get("score"),
    }


def run_screen(all_data: pd.DataFrame, index_pct_today: float,
               mcap_map: dict | None = None, industry_map: dict | None = None,
               fund_map: dict | None = None) -> pd.DataFrame:
    """全市场扫描。返回所有触发信号的股票（pass 在前按成交额降序，reject 在后）。"""
    mcap_map, industry_map, fund_map = mcap_map or {}, industry_map or {}, fund_map or {}
    rows = []
    for code, g in all_data.groupby("code"):
        name = g["name"].iloc[-1]
        r = evaluate_stock(prepare(g), name, index_pct_today,
                           mcap_yi=mcap_map.get(code),
                           industry=industry_map.get(code, ""),
                           fund=fund_map.get(code))
        if r:
            r["code"] = code
            rows.append(r)
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["status", "amount_yi"],
                              ascending=[True, False]).reset_index(drop=True)
    return out
