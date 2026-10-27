import math
import threading
import time
from collections import deque
from collections.abc import Callable

from app.services.exceptions import BudgetExceededError

DAY_SECONDS = 24 * 60 * 60.0


class TokenBudget:
    """LLM tokens the server may use in any 24 hours, across every key: the ceiling on the bill.

    Checked before a run starts and charged when it ends, so runs already going when the budget
    runs out finish (it can be overshot by what they use). In process memory, like the rate limits.
    """

    def __init__(self, limit: Callable[[], int]):
        self._limit = limit  # 0 = no ceiling
        self._spent: deque[tuple[float, int]] = deque()  # (when, tokens) per finished run
        self._total = 0
        self._lock = threading.Lock()

    def check(self) -> None:
        """BudgetExceededError if the last 24 hours used up the budget."""
        limit = self._limit()
        if not limit:
            return
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            if self._total >= limit:
                retry_after = max(1, math.ceil(self._spent[0][0] + DAY_SECONDS - now))
                raise BudgetExceededError(
                    "The server has used its daily LLM token budget; try again later", retry_after=retry_after
                )

    def charge(self, tokens: int) -> None:
        if tokens <= 0:
            return
        with self._lock:
            self._spent.append((time.monotonic(), tokens))
            self._total += tokens

    def used(self) -> int:
        with self._lock:
            self._expire(time.monotonic())
            return self._total

    def _expire(self, now: float) -> None:
        while self._spent and self._spent[0][0] <= now - DAY_SECONDS:
            self._total -= self._spent.popleft()[1]
