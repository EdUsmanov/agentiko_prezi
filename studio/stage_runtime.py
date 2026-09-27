"""The shared generation deadline and stage clock, independent of orchestration."""

from dataclasses import dataclass, field
import time


@dataclass(frozen=True)
class GenerationDeadline:
    deadline_at: float | None

    def remaining(self, reserve: float = 0) -> float:
        value = self.deadline_at - time.time() if self.deadline_at is not None else float("inf")
        if value <= 0:
            raise TimeoutError("Общий лимит генерации исчерпан")
        if value <= reserve:
            raise ValueError(
                "Недостаточно оставшегося времени для стадии и обязательного экспорта. Неполный результат не выдаётся за готовый."
            )
        return value - reserve


@dataclass
class StageClock:
    mark: float = field(default_factory=time.monotonic)
    timings: dict[str, float] = field(default_factory=dict)

    def checkpoint(self, name: str) -> None:
        now = time.monotonic()
        self.timings[name] = round(now - self.mark, 3)
        self.mark = now
