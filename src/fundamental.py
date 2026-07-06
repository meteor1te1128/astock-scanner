"""基本面评分（0-100）+ 风险信号 + 下跌归因标签。

诚实说明：东财批量业绩报表只有【净利润同比】，没有扣非口径，
故"净利润大幅下滑"信号基于净利润同比（差异=非经常性损益），已在 README 注明。

归因规则（近60个交易日跌幅>15%才打标签）：
  情绪杀：基本面分>=60 且无恶化信号；基本面杀：任一恶化信号；其余：存疑。
"""
import numpy as np
import pandas as pd


def _score_linear(x, lo, hi):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 50.0
    return float(np.clip((x - lo) / (hi - lo) * 100, 0, 100))


def score_row(r: dict) -> dict:
    margin_trend = r.get("margin", np.nan) - r.get("margin_prev", np.nan)
    recv_gap = r.get("recv_g", np.nan) - r.get("rev_g", np.nan)
    parts = {
        "ROE": (_score_linear(r.get("roe"), 0, 20), 0.20),
        "营收增速": (_score_linear(r.get("rev_g"), -10, 30), 0.15),
        "净利增速": (_score_linear(r.get("profit_g"), -20, 40), 0.20),
        "毛利率趋势": (_score_linear(margin_trend, -5, 5), 0.10),
        "负债率": (_score_linear(r.get("debt_ratio"), 90, 30), 0.10),
        "现金流质量": (_score_linear(r.get("ocf_ratio"), 0, 1.2), 0.15),
        "应收健康度": (_score_linear(recv_gap, 30, -10), 0.05),
        "商誉风险": (_score_linear(r.get("goodwill_ratio"), 40, 0), 0.05),
    }
    total = sum(s * w for s, w in parts.values())
    detail = {k: round(s, 0) for k, (s, w) in parts.items()}
    flags = []
    if not np.isnan(r.get("profit_g", np.nan)) and r["profit_g"] < -20:
        flags.append("净利润大幅下滑")
    if not np.isnan(r.get("ocf_ratio", np.nan)) and r["ocf_ratio"] < 0.3:
        flags.append("现金流质量差")
    if not np.isnan(recv_gap) and recv_gap > 30:
        flags.append("应收增速远超营收")
    if not np.isnan(margin_trend) and margin_trend < -3:
        flags.append("毛利率恶化")
    return {"score": round(total, 1), "detail": detail, "flags": flags}


def attribute_drop(drop_pct_60d: float, score: float, flags: list[str]) -> str:
    if drop_pct_60d > -15:
        return ""
    if flags:
        return "基本面杀"
    if score >= 60:
        return "情绪杀"
    return "存疑"


# ---------- 报告期日历 ----------

_DISCLOSED = [("1101", "0930"), ("0901", "0630"), ("0501", "0331")]


def report_period_for(date) -> str:
    """给定任意日期，返回当时【已披露完毕】的最新报告期（考虑披露截止：
    一季报4/30、半年报8/31、三季报10/31、年报次年4/30）。"""
    d = pd.Timestamp(date)
    md = f"{d.month:02d}{d.day:02d}"
    for start, per in _DISCLOSED:
        if md >= start:
            return f"{d.year}{per}"
    return f"{d.year - 1}0930"


def _prev_period(period: str) -> str:
    y, md = int(period[:4]), period[4:]
    return {"0331": f"{y - 1}1231", "0630": f"{y}0331",
            "0930": f"{y}0630", "1231": f"{y}0930"}[md]


def periods_back(n: int, from_date=None) -> list[str]:
    p = report_period_for(from_date or pd.Timestamp.now())
    out = [p]
    for _ in range(n - 1):
        p = _prev_period(p)
        out.append(p)
    return out


# ---------- AKShare 批量拉取 ----------

def _fetch_period(period: str) -> pd.DataFrame:
    """拉取指定报告期全市场并打分。返回 code,name,score,flags,detail。"""
    import akshare as ak
    from .net import retry
    prev = _prev_period(period)

    def pull(fn, date, cols):
        df = retry(fn, date=date)  # 网络错误重试；字段缺失属代码错误，下面立即抛出
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise RuntimeError(f"AKShare 接口 {fn.__name__} 缺少字段 {missing}，"
                               f"实际字段: {list(df.columns)[:20]}… 需要更新字段映射")
        return df

    yjbb = pull(ak.stock_yjbb_em, period,
                ["股票代码", "股票简称", "营业总收入-同比增长", "净利润-同比增长",
                 "净资产收益率", "销售毛利率"])
    yjbb_prev = pull(ak.stock_yjbb_em, prev, ["股票代码", "销售毛利率"])
    zcfz = pull(ak.stock_zcfz_em, period, ["股票代码", "资产负债率"])
    xjll = pull(ak.stock_xjll_em, period, ["股票代码", "净现金流-净现金流"])
    lrb = pull(ak.stock_lrb_em, period, ["股票代码", "净利润"])

    base = yjbb.rename(columns={
        "股票代码": "code", "股票简称": "name", "营业总收入-同比增长": "rev_g",
        "净利润-同比增长": "profit_g", "净资产收益率": "roe", "销售毛利率": "margin"})
    base = base.merge(yjbb_prev.rename(columns={"股票代码": "code",
                      "销售毛利率": "margin_prev"})[["code", "margin_prev"]],
                      on="code", how="left")
    base = base.merge(zcfz.rename(columns={"股票代码": "code",
                      "资产负债率": "debt_ratio"})[["code", "debt_ratio"]],
                      on="code", how="left")
    base = base.merge(xjll.rename(columns={"股票代码": "code",
                      "净现金流-净现金流": "ocf"})[["code", "ocf"]], on="code", how="left")
    base = base.merge(lrb.rename(columns={"股票代码": "code",
                      "净利润": "np"})[["code", "np"]], on="code", how="left")
    for c in ["rev_g", "profit_g", "roe", "margin", "margin_prev",
              "debt_ratio", "ocf", "np"]:
        base[c] = pd.to_numeric(base[c], errors="coerce")
    base["ocf_ratio"] = np.where(base["np"].abs() > 0, base["ocf"] / base["np"], np.nan)
    base["recv_g"] = np.nan
    base["goodwill_ratio"] = np.nan
    out = []
    for _, r in base.iterrows():
        s = score_row(r.to_dict())
        out.append({"code": r["code"], "name": r["name"], **s})
    return pd.DataFrame(out)


def fetch_fundamentals() -> pd.DataFrame:
    """最新报告期全市场评分。"""
    return _fetch_period(report_period_for(pd.Timestamp.now()))


def to_fund_map(scores: pd.DataFrame) -> dict:
    """DataFrame → {code: {"score":…, "flags":[…]}}，供筛选器/回测使用。"""
    return {r["code"]: {"score": r["score"], "flags": list(r["flags"])}
            for _, r in scores.iterrows()}


def fund_history(n_periods: int = 8) -> dict:
    """回测用：{报告期: {code: {"score","flags"}}}。

    用户确认：绝不减少条件。任何报告期拉取失败（网络重试耗尽或字段变化）
    都会抛出，中断整个回测——由用户重跑，而非静默跳过导致回测结果失真。
    """
    out = {}
    for p in periods_back(n_periods):
        out[p] = to_fund_map(_fetch_period(p))  # 失败即抛出，不再吞
    return out
