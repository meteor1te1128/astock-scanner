"""统一的对外请求重试+指数退避。

设计原则（与用户确认于 2026-07）：
  - 只对【网络类】错误重试：连接中断、超时、远端断连等。
  - 【代码类】错误（字段缺失 KeyError、类型错误等）立即抛出，绝不掩盖真问题。
  - 重试 3 次，指数退避 2→4→8 秒。
  - 3 次全失败则抛出最后一次的原始异常，保留完整堆栈。

所有 akshare / requests 调用都应经过 retry() 包裹，不再各自写重试。
"""
import time

import requests.exceptions as rex

RETRIES = 3
BACKOFF_BASE = 2  # 秒；第 k 次失败后 sleep BACKOFF_BASE**k → 2,4,8

# 判定为"可重试网络错误"的异常类型。
NETWORK_ERRORS = (
    rex.ConnectionError,
    rex.Timeout,
    rex.ChunkedEncodingError,
    rex.ContentDecodingError,
    ConnectionError,          # 内置
    TimeoutError,             # 内置
)


def is_network_error(exc: BaseException) -> bool:
    """是否为应当重试的网络类错误。也识别底层 http.client 的远端断连。"""
    if isinstance(exc, NETWORK_ERRORS):
        return True
    # akshare 常把底层 RemoteDisconnected 包在 requests.ConnectionError 里，
    # 上面已覆盖；但个别情况下会以字符串形态透出，做一次兜底文本判定。
    text = f"{type(exc).__name__}: {exc}"
    for kw in ("RemoteDisconnected", "Connection aborted",
               "Connection reset", "Read timed out",
               "Max retries exceeded"):
        if kw in text:
            return True
    return False


def retry(fn, *args, retries: int = RETRIES, base: int = BACKOFF_BASE,
          _sleep=time.sleep, **kwargs):
    """调用 fn(*args, **kwargs)，网络错误自动重试+指数退避。

    - 网络错误：重试，退避 base**1, base**2, …；最后一次仍失败则抛出。
    - 非网络错误：立即抛出，不重试（避免掩盖字段缺失等代码问题）。
    """
    last = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not is_network_error(exc):
                raise  # 代码类错误立即抛出
            last = exc
            if attempt < retries:
                _sleep(base ** attempt)
    raise last
