"""Errors of a job, with what the central service should do about them."""
from __future__ import annotations


class WorkError(Exception):
    """A user-facing reason why a job failed. retry: later (try again later), never, or
    next_step (this mode cannot work for the source; the configured chain may continue)."""

    def __init__(self, message: str, retry: str = "never") -> None:
        super().__init__(message)
        self.retry = retry


class Cancelled(Exception):
    pass


YOUTUBE_REFUSED = ("HTTP Error 429", "Too Many Requests", "Sign in to confirm", "confirm you're not a bot")
FORBIDDEN = ("HTTP Error 403", "403: Forbidden")
LIVE = ("Live streams are not supported", "live event will begin", "Premieres in", "This live event",
        "live stream recording is not available", "is_live")


def from_ytdlp(exc: Exception, what: str) -> WorkError:
    text = str(exc)
    if any(x in text for x in YOUTUBE_REFUSED):
        return WorkError(f"A YouTube most elutasította a kérést ({what}); később újrapróbálom: {text[:300]}", "later")
    if any(x in text for x in FORBIDDEN):
        # YouTube refused the media itself: most often a yt-dlp that YouTube has outpaced, sometimes a
        # passing block. The runner checks for a newer yt-dlp before the next try.
        from . import ytdlp_update
        ytdlp_update.want_soon()
        return WorkError(f"A YouTube megtagadta a letöltést (HTTP 403, {what}). Ezt többnyire egy újabb yt-dlp "
                         "megoldja; a futtató megnézi, van-e, és később újrapróbálja.", "later")
    if any(x in text for x in LIVE):
        return WorkError("Élő adás vagy premier; a vége után dolgozható fel.", "later")
    if "unable to download" in text.lower() or "timed out" in text.lower() or "Connection" in text:
        return WorkError(f"Hálózati hiba ({what}); később újrapróbálom: {text[:300]}", "later")
    return WorkError(f"A yt-dlp nem tudta {what}: {text[:500]}")
