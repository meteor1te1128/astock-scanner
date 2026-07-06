"""策略回测引擎（v2：加过滤规则与线上筛选器完全一致）。

时序（A股 T+1）：t日收盘出信号 → t+1开盘买入 → t+2起可卖：
  盘中触及止损线(-5%)按止损价卖；收盘MACD柱转绿则次日开盘卖。
双边手续费 0.1%。

"加过滤"规则包含：价>MA20、放量、成交额>3亿、强于沪深300、
换手率>=2%（历史换手率精确回测）、市值>=50亿（用当前市值近似，README已注明）、
基本面无恶化信号且分>=40（按交易日当时已披露的报告期匹配，不用未来数据）。
"""
import pandas as pd

FEE = 0.001


def simulate_stock(df: pd.DataFrame, use_filters: bool, index_pct: pd.Series | None,
                   extra_ok: pd.Series | None = None, stop_loss: float = 0.05) -> list[dict]:
    """extra_ok: 与 df 等长的布尔序列（换手率/市值/基本面逐日判定），仅加过滤时用。"""
    trades = []
    i, n = 1, len(df)
    while i < n - 1:
        t, p = df.iloc[i], df.iloc[i - 1]
        signal = (not pd.isna(t["hist"])) and (not pd.isna(p["hist"])) \
            and t["hist"] > 0 and p["hist"] <= 0
        if signal and use_filters:
            ok = (not pd.isna(t["ma20"])) and (not pd.isna(t["vol_ma5"])) \
                and t["close"] > t["ma20"] and t["volume"] > t["vol_ma5"] \
                and t["amount"] > 3e8 and i >= 60
            if ok and index_pct is not None:
                pct = (t["close"] / p["close"] - 1) * 100
                ok = pct > float(index_pct.iloc[i])
            if ok and extra_ok is not None:
                ok = bool(extra_ok.iloc[i])
            signal = bool(ok)
        if not signal:
            i += 1
            continue
        entry = float(df.iloc[i + 1]["open"])
        stop = entry * (1 - stop_loss)
        j, exit_price, reason = i + 2, None, None
        while j < n:
            d = df.iloc[j]
            if float(d["low"]) <= stop:
                exit_price, reason = stop, "止损"
                break
            if (not pd.isna(d["hist"])) and d["hist"] < 0:
                if j + 1 < n:
                    exit_price, reason, j = float(df.iloc[j + 1]["open"]), "转绿", j + 1
                break
            j += 1
        if exit_price is None:
            i = j
            continue
        ret = exit_price / entry * (1 - FEE) ** 2 - 1
        trades.append({"entry_i": i + 1, "exit_i": j, "ret": ret,
                       "hold_days": j - (i + 1), "reason": reason})
        i = j + 1
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "win_rate": None, "avg_ret": None, "total_ret": None,
                "max_dd": None, "avg_hold": None}
    rets = [t["ret"] for t in trades]
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in rets:
        equity *= (1 + r)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    return {"n": len(rets),
            "win_rate": round(sum(r > 0 for r in rets) / len(rets) * 100, 1),
            "avg_ret": round(sum(rets) / len(rets) * 100, 2),
            "total_ret": round((equity - 1) * 100, 1),
            "max_dd": round(max_dd * 100, 1),
            "avg_hold": round(sum(t["hold_days"] for t in trades) / len(trades), 1)}


def build_extra_ok(g: pd.DataFrame, mcap_yi: float | None,
                   fund_by_period: dict | None) -> pd.Series:
    """逐日判定换手率/市值/基本面三项。数据缺失按通过处理（宽容），
    市值不足50亿则整只股票全程 False。"""
    from .screener import TURNOVER_MIN, MCAP_MIN_YI, FUND_SCORE_MIN
    from .fundamental import report_period_for
    if mcap_yi is not None and not pd.isna(mcap_yi) and mcap_yi < MCAP_MIN_YI:
        return pd.Series(False, index=g.index)
    ok = pd.Series(True, index=g.index)
    if "turnover" in g.columns:
        t = pd.to_numeric(g["turnover"], errors="coerce")
        ok &= ~(t < TURNOVER_MIN)          # NaN 视为通过
    if fund_by_period:
        code = g["code"].iloc[0]
        period_of = {d: report_period_for(d) for d in g["date"].unique()}
        def fund_ok(d):
            m = fund_by_period.get(period_of[d])
            if not m or code not in m:
                return True
            f = m[code]
            return not f["flags"] and f["score"] >= FUND_SCORE_MIN
        ok &= g["date"].map(fund_ok)
    return ok


def run_backtest(all_data: pd.DataFrame, index_df: pd.DataFrame,
                 mcap_map: dict | None = None,
                 fund_by_period: dict | None = None) -> dict:
    from .screener import prepare
    idx = index_df.sort_values("date").reset_index(drop=True)
    idx["pct"] = idx["close"].pct_change() * 100
    idx_pct_by_date = idx.set_index("date")["pct"]
    mcap_map = mcap_map or {}
    results = {"pure": [], "filtered": []}
    for code, g in all_data.groupby("code"):
        name = str(g["name"].iloc[-1])
        g = prepare(g)
        aligned = g["date"].map(idx_pct_by_date)
        results["pure"] += simulate_stock(g, False, None)
        if "ST" not in name.upper():
            extra = build_extra_ok(g.assign(code=code), mcap_map.get(code),
                                   fund_by_period)
            results["filtered"] += simulate_stock(g, True, aligned, extra_ok=extra)
    bench = None
    if len(idx) > 1:
        bench = round((float(idx["close"].iloc[-1]) / float(idx["close"].iloc[0]) - 1)
                      * 100, 1)
    return {"pure": summarize(results["pure"]),
            "filtered": summarize(results["filtered"]),
            "benchmark_total_ret": bench}
