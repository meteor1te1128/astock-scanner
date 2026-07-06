"""技术指标计算。全部为纯函数，方便单元测试。"""
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """国内软件口径：DIF=EMA12-EMA26, DEA=EMA9(DIF), 柱=(DIF-DEA)*2。
    返回 (dif, dea, hist)。柱>0 显示红色（多头），柱<0 显示绿色。"""
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    hist = (dif - dea) * 2
    return dif, dea, hist


def ma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()
