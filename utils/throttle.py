"""统一防风控：令牌桶限速 + 指数退避重试 + 连续失败熔断 + 自适应降速。

用法（见 BaseCrawler._throttle / _with_retry）：
    self._throttle()                      # 请求前限速
    data = self._with_retry(lambda: fetch())  # 带退避重试的请求
"""
import random
import time
from dataclasses import dataclass, field


class CircuitOpenError(Exception):
    """连续失败超过阈值，本轮熔断停止。"""


class TokenBucket:
    """令牌桶：按 rate_per_min 匀速补充令牌，请求前 acquire 一个。"""

    def __init__(self, rate_per_min=30, burst=None):
        self.rate_per_min = float(rate_per_min)
        self.capacity = float(burst or max(1, int(rate_per_min) // 2))
        self.tokens = self.capacity
        self.updated = time.monotonic()

    def _refill(self):
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * (self.rate_per_min / 60.0))
        self.updated = now

    def acquire(self):
        while True:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return
            wait = (1 - self.tokens) / max(self.rate_per_min / 60.0, 1e-6)
            time.sleep(min(wait, 5))


@dataclass
class Throttle:
    rate_per_min: int = 30
    min_rate_per_min: int = 6
    max_fail_streak: int = 5
    retries: int = 3
    recover_after: int = 20
    bucket: TokenBucket = field(default=None)
    fail_streak: int = 0
    success_streak: int = 0

    def __post_init__(self):
        if self.bucket is None:
            self.bucket = TokenBucket(self.rate_per_min)

    def wait(self):
        self.bucket.acquire()

    def on_success(self):
        self.fail_streak = 0
        self.success_streak += 1
        if self.success_streak >= self.recover_after and self.bucket.rate_per_min < self.rate_per_min:
            self.bucket.rate_per_min = min(float(self.rate_per_min), self.bucket.rate_per_min * 2)
            self.success_streak = 0

    def on_blocked(self):
        """命中 429/403 等风控：降速并计一次失败。"""
        self.success_streak = 0
        self.bucket.rate_per_min = max(float(self.min_rate_per_min), self.bucket.rate_per_min * 0.5)

    def on_fail(self):
        self.fail_streak += 1
        self.success_streak = 0
        if self.fail_streak >= self.max_fail_streak:
            raise CircuitOpenError(f'连续失败 {self.fail_streak} 次，熔断停止本轮')

    @staticmethod
    def backoff(attempt):
        time.sleep(min(60, (2 ** attempt) + random.uniform(0, 1)))
