"""Hard spend ceiling for every L3 demo run.

The demo lane is the only place a reviewer can trigger live model calls, so the
ceiling lives in code, not in a docstring. A run that would cross the ceiling
aborts *before* the request is sent: BudgetGuard.reserve() is called first and
raises, so no chargeable call can leak out of an unbudgeted loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """Raised before a call that would cross the ceiling."""


@dataclass
class BudgetGuard:
    max_calls: int = 8
    max_tokens: int = 6000
    calls: int = 0
    tokens: int = 0
    ledger: list[dict] = field(default_factory=list)

    def reserve(self, stage: str, est_tokens: int = 0) -> None:
        """Claim one call slot. Raises before the request leaves the process."""
        if self.calls + 1 > self.max_calls:
            raise BudgetExceeded(
                f"call ceiling reached ({self.calls}/{self.max_calls}) at stage={stage}"
            )
        if self.tokens + est_tokens > self.max_tokens:
            raise BudgetExceeded(
                f"token ceiling would be crossed ({self.tokens}+{est_tokens}"
                f"/{self.max_tokens}) at stage={stage}"
            )
        self.calls += 1
        self.ledger.append({"stage": stage, "est_tokens": est_tokens, "state": "reserved"})

    def settle(self, tokens: int, ok: bool = True) -> None:
        self.tokens += max(0, int(tokens or 0))
        if self.ledger:
            self.ledger[-1].update({"actual_tokens": int(tokens or 0), "ok": ok,
                                    "state": "settled"})

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.tokens)

    def to_dict(self) -> dict:
        return {
            "max_calls": self.max_calls,
            "max_tokens": self.max_tokens,
            "calls": self.calls,
            "tokens": self.tokens,
            "remaining_calls": self.remaining_calls,
            "remaining_tokens": self.remaining_tokens,
            "ledger": self.ledger,
        }
