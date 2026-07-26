#!/usr/bin/env python3
"""Monitors Chrome, Firefox, Safari, Edge, and Internet Explorer browsing
history and raises alerts for doom-scrolling, long-form content watching,
and off-topic browsing.
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Alerter: locates, copies, and reads browser history
# ---------------------------------------------------------------------------

@dataclass
class Visit:
    url: str
    domain: str
    time: datetime
    title: str = ""
    browser: str = ""


class Alerter:
    """Finds Chrome, Firefox, Safari, Edge, and Internet Explorer history
    databases and copies them into a local 'history' folder so they can be
    parsed even while the browser holds a lock on the originals, then
    extracts recent visits from the copies."""

    # microseconds-since-1601-01-01 (Chrome/WebKit epoch) -> unix epoch offset.
    # Edge is Chromium-based and shares this exact schema and epoch.
    CHROME_EPOCH_OFFSET_SECS = 11644473600
    # seconds-since-2001-01-01 (Safari/Core Data epoch) -> unix epoch offset
    SAFARI_EPOCH_OFFSET_SECS = 978307200

    BROWSER_FILENAMES = {
        "chrome": "History",
        "edge": "History",
        "firefox": "places.sqlite",
        "safari": "History.db",
        "ie": "WebCacheV01.dat",
    }

    def __init__(self, base_dir: str = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.history_dir = self.base_dir / "history"
        self.history_dir.mkdir(exist_ok=True)
        self.copied_files = []  # list of (browser, Path) after copy_history_files()

    # -- locating source files -------------------------------------------

    @staticmethod
    def _profiles_with_file(bases, filename):
        paths = []
        for base in bases:
            if base.exists():
                paths.extend(p / filename for p in base.glob("*") if (p / filename).is_file())
        return paths

    def _chrome_history_paths(self):
        bases = []
        if IS_MAC:
            bases.append(Path.home() / "Library" / "Application Support" / "Google" / "Chrome")
        if IS_WINDOWS and os.environ.get("LOCALAPPDATA"):
            bases.append(Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data")
        return self._profiles_with_file(bases, "History")

    def _edge_history_paths(self):
        bases = []
        if IS_MAC:
            bases.append(Path.home() / "Library" / "Application Support" / "Microsoft Edge")
        if IS_WINDOWS and os.environ.get("LOCALAPPDATA"):
            bases.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data")
        return self._profiles_with_file(bases, "History")

    def _firefox_history_paths(self):
        bases = []
        if IS_MAC:
            bases.append(Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles")
        if IS_WINDOWS and os.environ.get("APPDATA"):
            bases.append(Path(os.environ["APPDATA"]) / "Mozilla" / "Firefox" / "Profiles")
        return self._profiles_with_file(bases, "places.sqlite")

    def _safari_history_paths(self):
        if not IS_MAC:
            return []
        path = Path.home() / "Library" / "Safari" / "History.db"
        return [path] if path.is_file() else []

    def _ie_history_paths(self):
        if not IS_WINDOWS or not os.environ.get("LOCALAPPDATA"):
            return []
        path = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "WebCache" / "WebCacheV01.dat"
        return [path] if path.is_file() else []

    # -- copying -----------------------------------------------------------

    def _copy_history_file(self, src: Path, browser: str, index: int) -> Path:
        dest = self.history_dir / f"{browser}_{index}_{self.BROWSER_FILENAMES[browser]}"
        shutil.copy2(src, dest)
        # copy WAL/SHM sidecars too, if present, for a more consistent snapshot
        for sidecar_suffix in ("-wal", "-shm"):
            sidecar = Path(str(src) + sidecar_suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, Path(str(dest) + sidecar_suffix))
        return dest

    def copy_history_files(self):
        """Copies every discovered history DB into ./history/.
        Returns the list of (browser, copied_path) tuples."""
        self.copied_files = []
        discoverers = {
            "chrome": self._chrome_history_paths,
            "edge": self._edge_history_paths,
            "firefox": self._firefox_history_paths,
            "safari": self._safari_history_paths,
            "ie": self._ie_history_paths,
        }
        for browser, discover in discoverers.items():
            for i, src in enumerate(discover()):
                try:
                    dest = self._copy_history_file(src, browser, i)
                    self.copied_files.append((browser, dest))
                except PermissionError as e:
                    print(
                        f"Warning: no permission to read {browser} history ({src}). "
                        "On macOS this usually means Terminal/Python needs Full Disk "
                        "Access under System Settings > Privacy & Security. "
                        f"({e})"
                    )
                except (OSError, shutil.Error) as e:
                    print(f"Warning: could not copy {browser} history {src}: {e}")
        return self.copied_files

    # -- reading -------------------------------------------------------------

    @staticmethod
    def _domain(url: str) -> str:
        try:
            netloc = urlparse(url).netloc.lower()
        except ValueError:
            return ""
        return netloc[4:] if netloc.startswith("www.") else netloc

    def _read_chromium_visits(self, db_path: Path, since: datetime, browser: str):
        """Shared reader for Chrome and Edge, which use an identical schema."""
        since_chrome = int((since.timestamp() + self.CHROME_EPOCH_OFFSET_SECS) * 1_000_000)
        visits = []
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
            cur = con.cursor()
            cur.execute(
                """
                SELECT urls.url, urls.title, visits.visit_time
                FROM visits JOIN urls ON urls.id = visits.url
                WHERE visits.visit_time >= ?
                ORDER BY visits.visit_time ASC
                """,
                (since_chrome,),
            )
            for url, title, visit_time in cur.fetchall():
                ts = visit_time / 1_000_000 - self.CHROME_EPOCH_OFFSET_SECS
                visits.append(Visit(url, self._domain(url), datetime.fromtimestamp(ts), title or "", browser))
            con.close()
        except sqlite3.Error as e:
            print(f"Warning: could not read {browser} history {db_path}: {e}")
        return visits

    def _read_firefox_visits(self, db_path: Path, since: datetime, browser: str = "firefox"):
        since_ff = int(since.timestamp() * 1_000_000)
        visits = []
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
            cur = con.cursor()
            cur.execute(
                """
                SELECT moz_places.url, moz_places.title, moz_historyvisits.visit_date
                FROM moz_historyvisits JOIN moz_places ON moz_places.id = moz_historyvisits.place_id
                WHERE moz_historyvisits.visit_date >= ?
                ORDER BY moz_historyvisits.visit_date ASC
                """,
                (since_ff,),
            )
            for url, title, visit_date in cur.fetchall():
                ts = visit_date / 1_000_000
                visits.append(Visit(url, self._domain(url), datetime.fromtimestamp(ts), title or "", browser))
            con.close()
        except sqlite3.Error as e:
            print(f"Warning: could not read Firefox history {db_path}: {e}")
        return visits

    def _read_safari_visits(self, db_path: Path, since: datetime, browser: str = "safari"):
        since_safari = since.timestamp() - self.SAFARI_EPOCH_OFFSET_SECS
        visits = []
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
            cur = con.cursor()
            cur.execute(
                """
                SELECT history_items.url, history_visits.title, history_visits.visit_time
                FROM history_visits
                JOIN history_items ON history_items.id = history_visits.history_item
                WHERE history_visits.visit_time >= ?
                ORDER BY history_visits.visit_time ASC
                """,
                (since_safari,),
            )
            for url, title, visit_time in cur.fetchall():
                ts = visit_time + self.SAFARI_EPOCH_OFFSET_SECS
                visits.append(Visit(url, self._domain(url), datetime.fromtimestamp(ts), title or "", browser))
            con.close()
        except sqlite3.Error as e:
            print(f"Warning: could not read Safari history {db_path}: {e}")
        return visits

    def _read_ie_visits(self, db_path: Path, since: datetime, browser: str = "ie"):
        """Best-effort reader for IE's WebCache (an ESE database, not SQLite).
        Requires the optional 'libesedb-python' package (pyesedb); IE support
        is unverified since IE is retired and no longer available to test
        against, so failures here are silently degraded, not fatal."""
        try:
            import pyesedb
        except ImportError:
            print(
                "Note: found Internet Explorer history but the optional "
                "'libesedb-python' package (pyesedb) isn't installed, so it "
                "can't be parsed. Run 'pip install libesedb-python' to enable it."
            )
            return []

        since_filetime = int((since.timestamp() + self.CHROME_EPOCH_OFFSET_SECS) * 10_000_000)
        visits = []
        try:
            esedb = pyesedb.file()
            esedb.open(str(db_path))
            for i in range(esedb.get_number_of_tables()):
                table = esedb.get_table(i)
                if not table.get_name().startswith("Container_"):
                    continue
                columns = {table.get_column(c).get_name(): c for c in range(table.get_number_of_columns())}
                if "Url" not in columns or "AccessedTime" not in columns:
                    continue
                url_col, time_col = columns["Url"], columns["AccessedTime"]
                for r in range(table.get_number_of_records()):
                    record = table.get_record(r)
                    try:
                        raw_url = record.get_value_data(url_col)
                        raw_time = record.get_value_data_as_integer(time_col)
                    except (OSError, ValueError):
                        continue
                    if not raw_url or raw_time is None or raw_time < since_filetime:
                        continue
                    url = raw_url.decode("utf-8", errors="ignore").split("@", 1)[-1].rstrip("\x00")
                    if not url.startswith(("http://", "https://")):
                        continue
                    ts = raw_time / 10_000_000 - self.CHROME_EPOCH_OFFSET_SECS
                    visits.append(Visit(url, self._domain(url), datetime.fromtimestamp(ts), "", browser))
            esedb.close()
        except Exception as e:
            print(f"Warning: could not read Internet Explorer history {db_path}: {e}")
        return visits

    def read_urls(self, n: int):
        """Refreshes the history copies and returns every Visit (url, time,
        domain, title, browser) from the last n hours, sorted by time."""
        self.copy_history_files()
        since = datetime.now() - timedelta(hours=n)
        readers = {
            "chrome": self._read_chromium_visits,
            "edge": self._read_chromium_visits,
            "firefox": self._read_firefox_visits,
            "safari": self._read_safari_visits,
            "ie": self._read_ie_visits,
        }
        all_visits = []
        for browser, path in self.copied_files:
            all_visits.extend(readers[browser](path, since, browser))
        all_visits.sort(key=lambda v: v.time)
        return all_visits


# ---------------------------------------------------------------------------
# Session detection shared by the "same site, repeatedly" style alerts
# ---------------------------------------------------------------------------

@dataclass
class Period:
    domain: str
    start: datetime
    end: datetime
    visit_count: int

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


def _domain_runs(visits):
    """Split chronologically sorted visits into maximal runs where every
    visit in a run shares a domain with no intervening visit to any other
    domain."""
    runs, current, current_domain = [], [], None
    for v in visits:
        if not v.domain:
            if current:
                runs.append(current)
            current, current_domain = [], None
            continue
        if v.domain == current_domain:
            current.append(v)
        else:
            if current:
                runs.append(current)
            current, current_domain = [v], v.domain
    if current:
        runs.append(current)
    return runs


def _sessions_for_condition(visits, gap_ok, min_duration):
    """Within each same-domain run, chain consecutive visits whose gap
    satisfies gap_ok(gap) into sessions, and keep sessions lasting at
    least min_duration."""
    periods = []
    for run in _domain_runs(visits):
        start_idx = 0
        for i in range(1, len(run) + 1):
            broke = i == len(run) or not gap_ok(run[i].time - run[i - 1].time)
            if broke:
                session = run[start_idx:i]
                if len(session) > 1:
                    duration = session[-1].time - session[0].time
                    if duration >= min_duration:
                        periods.append(Period(session[0].domain, session[0].time, session[-1].time, len(session)))
                start_idx = i
    return periods


YOUTUBE_DOMAINS = {"youtube.com", "m.youtube.com"}


def _is_youtube_short(visit: Visit) -> bool:
    """True if visit is a YouTube Short (short-form content), identified by URL path."""
    return visit.domain in YOUTUBE_DOMAINS and urlparse(visit.url).path.startswith("/shorts/")


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class Alert:
    """Base class for a configured alert condition."""

    name = "alert"

    def evaluate(self, visits):
        """Return the list of Period matches for this condition."""
        raise NotImplementedError

    def is_active_now(self, visits, now=None, recent_window=timedelta(minutes=10)):
        now = now or datetime.now()
        return any(p.end >= now - recent_window for p in self.evaluate(visits))


class DoomScrollAlert(Alert):
    """a) Repeated, frequent visits to one site (short-form content): gap
    between 0 and 10 min, sustained > 15 min."""

    name = "doom_scroll"

    def __init__(self, max_gap=timedelta(minutes=10), min_duration=timedelta(minutes=15)):
        self.max_gap = max_gap
        self.min_duration = min_duration

    def evaluate(self, visits):
        return _sessions_for_condition(visits, lambda gap: gap < self.max_gap, self.min_duration)


class LongFormAlert(Alert):
    """b) Long-form YouTube watching: visits to non-Short YouTube URLs,
    chained into a session as long as consecutive views are no more than
    2 hours apart. youtube.com and m.youtube.com are treated as the same
    site for this grouping."""

    name = "long_form"

    def __init__(self, max_gap=timedelta(hours=2)):
        self.max_gap = max_gap

    def evaluate(self, visits):
        candidates = [
            Visit(v.url, "youtube", v.time, v.title, v.browser)
            for v in visits
            if v.domain in YOUTUBE_DOMAINS and not _is_youtube_short(v)
        ]
        return _sessions_for_condition(candidates, lambda gap: gap <= self.max_gap, timedelta())


class OffTopicAlert(Alert):
    """c) Any URL unrelated to configured learning goals (default: AI & programming)."""

    name = "off_topic"

    DEFAULT_ALLOWED_DOMAINS = {
        "github.com", "stackoverflow.com", "docs.python.org", "arxiv.org",
        "openai.com", "anthropic.com", "claude.ai", "chat.openai.com",
        "huggingface.co", "pytorch.org", "tensorflow.org", "kaggle.com",
        "leetcode.com", "realpython.com", "developer.mozilla.org", "readthedocs.io",
    }
    DEFAULT_KEYWORDS = {
        "ai", "machine learning", "deep learning", "neural network", "llm",
        "python", "programming", "software", "coding", "algorithm",
        "data science", "pytorch", "tensorflow", "gpt", "transformer",
    }

    def __init__(self, allowed_domains=None, keywords=None):
        self.allowed_domains = set(allowed_domains) if allowed_domains else set(self.DEFAULT_ALLOWED_DOMAINS)
        self.keywords = {k.lower() for k in (keywords or self.DEFAULT_KEYWORDS)}

    def _is_on_topic(self, visit: Visit) -> bool:
        if any(visit.domain == d or visit.domain.endswith("." + d) for d in self.allowed_domains):
            return True
        haystack = f"{visit.url} {visit.title}".lower()
        return any(kw in haystack for kw in self.keywords)

    def evaluate(self, visits):
        return [Period(v.domain, v.time, v.time, 1) for v in visits if v.domain and not self._is_on_topic(v)]


class YouTubeLimitAlert(Alert):
    """d) YouTube limit: once more than daily_limit YouTube Shorts have been
    watched on a calendar day, flag every Short watched after that."""

    name = "youtube_limit"

    def __init__(self, daily_limit: int = 10):
        self.daily_limit = daily_limit

    def evaluate(self, visits):
        periods = []
        counts_by_day = Counter()
        for v in sorted(visits, key=lambda v: v.time):
            if not _is_youtube_short(v):
                continue
            day = v.time.date()
            counts_by_day[day] += 1
            if counts_by_day[day] > self.daily_limit:
                periods.append(Period(v.domain, v.time, v.time, 1))
        return periods


# ---------------------------------------------------------------------------
# Reporting periods: day / week / month, expressed in hours for read_urls()
# ---------------------------------------------------------------------------

PERIOD_HOURS = {
    "day": 24,
    "week": 24 * 7,
    "month": 24 * 30,  # calendar months vary; treated as a fixed 30-day window
}


def _format_period(hours: float) -> str:
    """Render an hours count as whole days when it divides evenly, since
    week/month reports read better as '7 days' than '168 hours'."""
    if hours >= 24 and hours % 24 == 0:
        days = int(hours // 24)
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{hours:g} hours"


# ---------------------------------------------------------------------------
# AlertManager: lets a user configure alerts and check them against history
# ---------------------------------------------------------------------------

class AlertManager:
    def __init__(self, alerter: Alerter):
        self.alerter = alerter
        self.alerts = {}  # name -> Alert

    def add_alert(self, alert: Alert):
        self.alerts[alert.name] = alert

    def remove_alert(self, name: str):
        self.alerts.pop(name, None)

    def evaluate_all(self, hours: int):
        visits = self.alerter.read_urls(hours)
        return {name: alert.evaluate(visits) for name, alert in self.alerts.items()}

    def detect_current(self, hours: int = 3, recent_window=timedelta(minutes=10)):
        """Return the names of alerts whose condition is active right now
        (i.e. their most recent matching visit fell within recent_window)."""
        visits = self.alerter.read_urls(hours)
        now = datetime.now()
        return [name for name, alert in self.alerts.items() if alert.is_active_now(visits, now, recent_window)]

    @staticmethod
    def _format_span(start: datetime, end: datetime) -> str:
        if start.date() == end.date():
            return f"{start.strftime('%Y-%m-%d %H:%M')} - {end.strftime('%H:%M')}"
        return f"{start.strftime('%Y-%m-%d %H:%M')} - {end.strftime('%Y-%m-%d %H:%M')}"

    def _report_duration_alert(self, alert_name: str, label: str, visits, hours: float):
        periods = self.alerts[alert_name].evaluate(visits)
        total_hours = sum((p.duration for p in periods), timedelta()).total_seconds() / 3600
        print(f"{label} in the past {_format_period(hours)}: {total_hours:.2f} hours")
        if periods:
            print("Periods:")
            for p in periods:
                print(f"  {p.domain}: {self._format_span(p.start, p.end)}")
        else:
            print(f"No {label.lower()} periods detected.")
        return periods

    def report_doom_scrolling(self, visits, hours: float = 24):
        """a) Report doom-scrolling periods (repeated, frequent same-site visits)."""
        return self._report_duration_alert("doom_scroll", "Doom-scrolling", visits, hours)

    def report_long_form(self, visits, hours: float = 24):
        """b) Report long-form YouTube watching periods (non-Short views chained by gaps <= 2 hours)."""
        return self._report_duration_alert("long_form", "Long-form content watching", visits, hours)

    def report_off_topic(self, visits, hours: float = 24):
        """c) Report visits unrelated to configured learning goals, grouped by domain and day."""
        periods = self.alerts["off_topic"].evaluate(visits)
        print(f"Off-topic visits in the past {_format_period(hours)}: {len(periods)}")
        counts = Counter((p.start.date(), p.domain) for p in periods)
        counts = Counter({key: count for key, count in counts.items() if count >= 10})
        for (day, domain), count in sorted(counts.items(), key=lambda item: (item[0][0], -item[1], item[0][1])):
            print(f"  {day} {domain}: {count} visit{'s' if count != 1 else ''}")
        return periods

    def report_youtube_limit(self, visits, hours: float = 24):
        """d) Report YouTube Shorts watched beyond the daily limit, grouped by day."""
        periods = self.alerts["youtube_limit"].evaluate(visits)
        limit = self.alerts["youtube_limit"].daily_limit
        print(f"YouTube Shorts over the daily limit of {limit} in the past {_format_period(hours)}: {len(periods)}")
        counts = Counter(p.start.date() for p in periods)
        for day, count in sorted(counts.items()):
            print(f"  {day}: {count} short{'s' if count != 1 else ''} over the limit")
        return periods


def speak(message: str):
    try:
        subprocess.run(["say", message], check=False)
    except FileNotFoundError:
        print(f"[say unavailable] {message}")


ALERT_MESSAGES = {
    "doom_scroll": "I have detected that you are doom scrolling.",
    "long_form": "I have detected that you are watching long form content.",
    "off_topic": "I have detected that you are browsing content unrelated to your learning goals.",
    "youtube_limit": "I have detected that you have gone over your YouTube Shorts limit for today.",
}


def main(period: str = None):
    """Run the live alert check. If period ('day'/'week'/'month') is given,
    also print a report over that window before checking."""
    alerter = Alerter()
    manager = AlertManager(alerter)
    manager.add_alert(DoomScrollAlert())
    manager.add_alert(LongFormAlert())
    manager.add_alert(OffTopicAlert())
    manager.add_alert(YouTubeLimitAlert())

    if period is not None:
        if period not in PERIOD_HOURS:
            raise ValueError(f"period must be one of {sorted(PERIOD_HOURS)}, got {period!r}")
        hours = PERIOD_HOURS[period]
        visits = alerter.read_urls(hours)
        manager.report_doom_scrolling(visits, hours=hours)
        manager.report_long_form(visits, hours=hours)
        manager.report_off_topic(visits, hours=hours)
        manager.report_youtube_limit(visits, hours=hours)

    # Detect what's happening right now, and speak it if exactly one alert fires.
    # The lookback covers the whole calendar day so far, since the YouTube
    # limit alert needs today's full Shorts count, not just a few hours of it.
    now = datetime.now()
    hours_since_midnight = (now - datetime.combine(now.date(), datetime.min.time())).total_seconds() / 3600
    active = manager.detect_current(hours=max(3, hours_since_midnight))
    if len(active) == 1:
        message = ALERT_MESSAGES.get(active[0], f"I have detected the alert: {active[0]}.")
        print(message)
        speak(message)
    elif len(active) > 1:
        print(f"Multiple alerts active at once ({active}); not speaking to avoid ambiguity.")
    else:
        print("No alert condition currently active.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check for active alerts. Optionally also print a report over a day, week, or month."
    )
    parser.add_argument("period", nargs="?", default=None, choices=sorted(PERIOD_HOURS),
                         help="Also print a report over this period (default: no report, just the alert check)")
    args = parser.parse_args()
    main(args.period)
