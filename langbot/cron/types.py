"""Cron types for scheduled task management."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CronSchedule:
    """Schedule definition for a cron job.

    Supports three schedule types:
    - "at": One-time execution at specific timestamp
    - "every": Recurring execution at fixed interval
    - "cron": Cron expression (e.g., "0 9 * * *" for daily at 9am)
    """
    kind: Literal["at", "every", "cron"]
    # For "at": timestamp in milliseconds
    at_ms: int | None = None
    # For "every": interval in milliseconds
    every_ms: int | None = None
    # For "cron": cron expression (e.g. "0 9 * * *")
    expr: str | None = None
    # Timezone for cron expressions (IANA format, e.g., "America/New_York")
    tz: str | None = None


@dataclass
class CronPayload:
    """What to do when the job runs."""
    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    message: str = ""
    # Deliver response to channel
    deliver: bool = False
    channel: str | None = None  # e.g. "cli", "qq"
    to: str | None = None  # e.g. chat ID or user ID


@dataclass
class CronJobState:
    """Runtime state of a job."""
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: Literal["ok", "error", "skipped"] | None = None
    last_error: str | None = None


@dataclass
class CronJob:
    """A scheduled job."""
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False


@dataclass
class CronStore:
    """Persistent store for cron jobs."""
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)
