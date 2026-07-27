A tool to watch my time usage and alert me when I am not making good use of my time.

# Alerter — User Manual

`alerter.py` reads your Chrome, Firefox, Safari, Edge, and Internet Explorer
browsing history and alerts you to four patterns:

- **Doom-scrolling (short-form content)** — repeated visits to one site
  spaced 0 to 10 minutes apart, sustained for more than 7 minutes.
- **Long-form watching** — visits to one site spaced between 10 and 90
  minutes apart, sustained for more than 1.5 hours (e.g. a long video with
  periodic checks/comments).
- **Off-topic browsing** — any URL that isn't related to your configured
  learning goals (default: AI & programming).
- **YouTube limit** — once you've watched more than a daily limit (default
  10) of YouTube Shorts (URLs under `/shorts/`) on a calendar day, every
  Short watched after that is flagged.
- **Calendar reminders** — speaks "You have a scheduled event at &lt;time&gt;"
  15, 10, and 5 minutes before each timed event on your Google Calendar
  (all-day events are ignored, since they have no specific time to count
  down to). Requires `googlecal.py` to be set up; see below.

It works even while the browser is open, by copying the history database
files before reading them (browsers lock these files while running).

## Requirements

- Python 3.9+ (standard library only — no dependencies to install, except
  optionally for Internet Explorer, see below)
- Spoken alerts use macOS's `say` command; on other platforms `speak()` just
  prints the message instead
- At least one of Chrome, Firefox, Safari, or Edge, previously used on this
  machine (Safari is macOS-only; Edge/Chrome/Firefox work on macOS and
  Windows; IE is Windows-only)

Browser paths are detected automatically based on OS:

| Browser | macOS | Windows |
|---|---|---|
| Chrome | `~/Library/Application Support/Google/Chrome/*/History` | `%LOCALAPPDATA%\Google\Chrome\User Data\*\History` |
| Edge | `~/Library/Application Support/Microsoft Edge/*/History` | `%LOCALAPPDATA%\Microsoft\Edge\User Data\*\History` |
| Firefox | `~/Library/Application Support/Firefox/Profiles/*/places.sqlite` | `%APPDATA%\Mozilla\Firefox\Profiles\*\places.sqlite` |
| Safari | `~/Library/Safari/History.db` | not available |
| Internet Explorer | not available | `%LOCALAPPDATA%\Microsoft\Windows\WebCache\WebCacheV01.dat` |

**Safari note:** modern macOS protects `~/Library/Safari/History.db` behind
Full Disk Access (TCC). If you see a permission warning, grant your terminal
app (or `python3`) Full Disk Access under System Settings > Privacy &
Security > Full Disk Access, then re-run.

**Internet Explorer note:** IE has been retired by Microsoft (replaced by
Edge) and stores history in a proprietary ESE database (`WebCacheV01.dat`),
not SQLite. Reading it requires the optional `libesedb-python` package
(`pip install libesedb-python`); without it, `alerter.py` still finds and
copies the file but skips parsing it, with a note explaining why. This path
is best-effort and unverified — the ESE container/column layout can vary by
Windows version, and there's no supported IE install left to test against.

## Quick start

```bash
python3 alerter.py             # just the live alert check, no report
python3 alerter.py day         # also print a report over the last 24 hours
python3 alerter.py week        # ...over the last 7 days
python3 alerter.py month       # ...over the last 30 days
```

Every invocation will:

1. Copy every history database it can find (Chrome, Firefox, Safari, Edge,
   IE) into `./history/`.
2. If a period (`day`/`week`/`month`) is given, report on all four
   conditions over that window: doom-scrolling and long-form watching as
   time periods, off-topic browsing as per-day-per-domain visit counts, and
   YouTube Shorts over the daily limit as a per-day count. With no period,
   this step is skipped entirely.
3. Check whether you are *currently* doom-scrolling, watching long-form
   content, browsing off-topic, or over your YouTube Shorts limit — and if
   exactly one of those is true, speak an alert out loud (e.g. "I have
   detected that you are doom scrolling."). This always runs, regardless of
   whether a report was requested.

Example output for `python3 alerter.py day`:

```
Doom-scrolling in the past 1 day: 6.58 hours
Periods:
  youtube.com: 2026-07-26 01:37 - 02:08
  youtube.com: 2026-07-26 18:08 - 20:46
Long-form content watching in the past 1 day: 0.00 hours
No long-form content watching periods detected.
Off-topic visits in the past 1 day: 730
  2026-07-26 youtube.com: 496 visits
  2026-07-26 linkedin.com: 38 visits
YouTube Shorts over the daily limit of 10 in the past 1 day: 484
  2026-07-26: 484 shorts over the limit
No alert condition currently active.
```

## How it works

### `Alerter`

Finds and copies your browser history, then parses it into visits.

```python
from alerter import Alerter

alerter = Alerter()                 # copies land in ./history/ next to alerter.py
alerter.copy_history_files()        # explicitly (re)copy every browser's history DB
visits = alerter.read_urls(24)      # copy + read visits from the last 24 hours
```

`read_urls(n)` always makes a fresh copy first, then returns a list of
`Visit` objects sorted by time:

```python
Visit(url, domain, time, title, browser)
```

- `url` — the full URL visited
- `domain` — the site's domain (`www.` stripped)
- `time` — a `datetime` of when it was visited
- `title` — the page title, if the browser recorded one (IE visits don't carry a title)
- `browser` — `"chrome"`, `"firefox"`, `"safari"`, `"edge"`, or `"ie"`

The `./history/` folder contains raw copies of your history databases
(`chrome_0_History`, `firefox_0_places.sqlite`, `safari_0_History.db`,
`edge_0_History`, `ie_0_WebCacheV01.dat`, etc. — indexed per browser profile
found). These are refreshed every time `read_urls`/`copy_history_files` runs
and are not cleaned up automatically — treat them like your browser history,
since that's exactly what they are.

### `Alert` and `AlertManager`

`AlertManager` holds a set of configured `Alert` objects and runs them
against history pulled from an `Alerter`.

```python
from alerter import Alerter, AlertManager, DoomScrollAlert, LongFormAlert, OffTopicAlert, YouTubeLimitAlert

alerter = Alerter()
manager = AlertManager(alerter)
manager.add_alert(DoomScrollAlert())
manager.add_alert(LongFormAlert())
manager.add_alert(OffTopicAlert())
manager.add_alert(YouTubeLimitAlert())

# All matches per alert, over a given lookback window (hours):
results = manager.evaluate_all(hours=24)
for name, periods in results.items():
    for p in periods:
        print(name, p.domain, p.start, p.end, p.duration)

# Which alerts are firing right now? -> {name: matches}, matches being
# whatever that alert's active_matches()/evaluate() returns.
active_now = manager.detect_current(hours=3, recent_window=timedelta(minutes=5))
```

Each match is a `Period(domain, start, end, visit_count)`, except for
`CalendarAlert`, whose matches are `CalendarReminder`s (see below).

### Excluding domains from every alert

Pass `excluded_domains` to `AlertManager` to filter out visits to certain
domains (and their subdomains) before *any* alert sees them -- e.g. sites
you use for work, job-hunting, or learning that would otherwise look like
doom-scrolling or off-topic browsing:

```python
manager = AlertManager(alerter, excluded_domains={"linkedin.com", "google.com"})
```

`alerter.py`'s own `main()` sets this to `EXCLUDED_DOMAINS` (currently
`linkedin.com`, `mercor.com`, `alignerr.com`, `turing.com`, `gmail.com`,
`google.com`, `arxiv.org`, `github.com`, and `kaggle.com` -- `google.com`
also covers Google subdomains like `mail.google.com` and `docs.google.com`,
since matching is by domain suffix). This applies to the live check,
`evaluate_all`, and the period reports alike, since all three read visits
through `manager.read_visits(hours)`.

### Printed reports

`AlertManager` also has four methods that evaluate an alert and print a
human-readable report, returning the matched `Period`s as well:

```python
visits = alerter.read_urls(24)
manager.report_doom_scrolling(visits)  # total hours + list of dated periods
manager.report_long_form(visits)       # same, for long-form watching
manager.report_off_topic(visits)       # visit count per off-topic domain, per day
manager.report_youtube_limit(visits)   # count of over-the-limit Shorts, per day
```

All four take an optional `hours` argument (default `24`) purely to label
the printed header (e.g. "in the past 7 days") — pass the same value you
used for `read_urls(n)`.

### `main(period=None)` and the CLI

`main()` always runs the live alert check. Pass `period="day"`, `"week"`, or
`"month"` (or run `python3 alerter.py <period>` from the shell) to also print
the four reports above first, over that window — `PERIOD_HOURS` maps each
name to an hours count (`day`→24, `week`→168, `month`→720; a month is a
fixed 30-day window, not a calendar month). Leave it unset for just the
alert check, with no report.

### Built-in alerts

| Class | Trigger | Default threshold |
|---|---|---|
| `DoomScrollAlert` | consecutive same-site visits with gaps under `max_gap` | 0 min < gap < 10 min, sustained > 7 min |
| `LongFormAlert` | consecutive same-site visits with gaps in a mid-range window | 10 min < gap < 90 min, sustained > 1.5 hr |
| `OffTopicAlert` | any visited domain/page not matching your allow-list or keywords | see below |
| `YouTubeLimitAlert` | any YouTube Shorts visit (`/shorts/...`) once today's count exceeds `daily_limit` | `daily_limit=10` |
| `CalendarAlert` | a timed Google Calendar event starting in one of `lead_times` | `lead_times=(15, 10, 5)` minutes |

All thresholds are constructor arguments:

```python
DoomScrollAlert(max_gap=timedelta(minutes=10), min_duration=timedelta(minutes=7))
LongFormAlert(min_gap=timedelta(minutes=10), max_gap=timedelta(minutes=90), min_duration=timedelta(hours=1.5))
YouTubeLimitAlert(daily_limit=10)
CalendarAlert(google_cal, lead_times=(timedelta(minutes=15), timedelta(minutes=10), timedelta(minutes=5)))
```

### `CalendarAlert`

Speaks "You have a scheduled event at &lt;time&gt;" 15, 10, and 5 minutes
before each *timed* event on your Google Calendar. All-day events are
skipped entirely, since they have no specific time to count down to.

```python
from googlecal import GoogleCal
from alerter import Alerter, AlertManager, CalendarAlert

manager = AlertManager(Alerter())
manager.add_alert(CalendarAlert(GoogleCal()))
```

It takes any object exposing `get_events(time_min, time_max)` (i.e. a
`GoogleCal`), so `alerter.py` itself never imports `googlecal.py` — that
import only happens where you wire the two together (`main()` does this
lazily too, so the browsing alerts still work if `googlecal.py`'s
dependencies or credentials aren't set up).

Each match is a `CalendarReminder(summary, location, start, lead)`. Because
a reminder needs to speak details specific to *which* event and *which*
lead time fired — not a fixed string like the other alerts — `CalendarAlert`
overrides `speak_message(matches)` instead of using the shared
`ALERT_MESSAGES` lookup. `catch_window` (default 5 minutes) controls how
long after a trigger instant it still counts as "now"; it should be at
least as long as how often you run the live check (e.g. the `*/5 * * * *`
cron cadence below), so a trigger can't fall between two runs unnoticed.

`YouTubeLimitAlert` identifies a Short by domain (`youtube.com` or
`m.youtube.com`) plus a URL path starting with `/shorts/`; it counts visits
per calendar day (`visit.time.date()`), and every Short after the
`daily_limit`-th one on a given day is flagged.

**`LongFormAlert` and YouTube Shorts:** the same URL-based Short check is
used to exclude Shorts from `LongFormAlert`'s session-building. A Short is
simply dropped from consideration before grouping by domain and gap — it
neither counts as long-form viewing itself, nor does it interrupt a
long-form session in progress (the gap is measured across it, between the
two surrounding non-Short visits). Every other domain is unaffected, since
there's no URL-based way to tell short from long content outside YouTube.
`DoomScrollAlert` is intentionally left untouched — a long-form YouTube
video visit still counts towards a doom-scrolling session exactly as any
other visit would.

### Customizing "on topic" for `OffTopicAlert`

By default, `OffTopicAlert` treats a page as on-topic (AI & programming,
cloud/deployment, job-hunting, or notes/productivity) if its domain is on
an allow-list, or its URL/title contains a keyword like `python`,
`machine learning`, `algorithm`, `devops`, `job search`, etc. — the
keyword list is meant to generalize past the allow-listed domains
themselves, so a similar site not explicitly listed (e.g. another cloud
host or job board) still gets treated as on-topic. Anything else is
flagged. Override either list to match your own learning goals:

```python
OffTopicAlert(
    allowed_domains={"github.com", "arxiv.org", "en.wikipedia.org"},
    keywords={"python", "statistics", "history"},
    min_recent_visits=3,
    lookback_window=timedelta(minutes=15),
)
```

The alert is active once `min_recent_visits` (default 3) off-topic visits
land within `lookback_window` (default 15 minutes) — a single stray
off-topic page load won't trigger speech — provided at least one of those
visits also falls within `active_matches()`'s `recent_window` (default 5
minutes, matching the cron cadence). That second check means a burst of
off-topic visits stops re-triggering once it's trailed off for more than
`recent_window`, even if it's still within `lookback_window`.

### Writing your own alert

Subclass `Alert` and implement `evaluate(visits)`, returning a list of
`Period` matches:

```python
from alerter import Alert, Period

class LateNightAlert(Alert):
    name = "late_night"

    def evaluate(self, visits):
        return [Period(v.domain, v.time, v.time, 1) for v in visits if v.time.hour >= 23 or v.time.hour < 5]

manager.add_alert(LateNightAlert())
```

By default, the message spoken for an active alert comes from
`ALERT_MESSAGES[name]`. If your alert needs to speak something specific to
which match(es) fired (like `CalendarAlert` does), override
`speak_message(matches)` instead:

```python
class LateNightAlert(Alert):
    name = "late_night"

    def evaluate(self, visits):
        return [Period(v.domain, v.time, v.time, 1) for v in visits if v.time.hour >= 23 or v.time.hour < 5]

    def speak_message(self, matches):
        return f"You're up late browsing {matches[0].domain}."
```

## Spoken alerts

`main()` calls `speak(message)`, which shells out to macOS's `say` command.
It only speaks when **exactly one** alert is currently active, to avoid an
ambiguous announcement when multiple conditions overlap — in that case it
prints the list of active alerts instead of speaking.

For live detection, `main()` calls `detect_current` with a lookback of
"hours since midnight" (minimum 3), rather than a fixed 3 hours. A fixed
short window would work fine for doom-scrolling/long-form, but
`YouTubeLimitAlert` needs to see the *whole* day's Shorts count to know
whether the limit has actually been crossed — a 3-hour window late in the
day could miss the earlier Shorts that pushed you over the limit.

## Running it periodically

`alerter.py` runs once and exits. To get ongoing alerts, schedule it, e.g.
with `cron` or `launchd`, every few minutes:

```
*/5 * * * * /usr/bin/python3 /path/to/alerter.py >> /path/to/alerter.log 2>&1
```

## `googlecal.py`: Google Calendar reports

`googlecal.py` reads your Google Calendar and reports on appointments for
the current day, week, or month.

### Requirements

```bash
pip install -r requirements.txt
```

Then in Google Cloud Console:

1. Enable the "Google Calendar API" for a project.
2. Create an OAuth 2.0 Client ID of type "Desktop app" (APIs & Services >
   Credentials).
3. Download the client secret JSON and save it as `credentials.json` next
   to `googlecal.py`.

On first use a browser window opens to grant access; the resulting token is
cached in `token.json` next to `googlecal.py` so future runs don't prompt
again (refreshed automatically once expired). Both `credentials.json` and
`token.json` hold sensitive access to your calendar — they're already
covered by `.gitignore`, don't commit or share them.

### Quick start

```bash
python3 googlecal.py             # appointments for today (default)
python3 googlecal.py day         # same as above, explicit
python3 googlecal.py week        # appointments for the current Mon-Sun week
python3 googlecal.py month       # appointments for the current calendar month
```

### `GoogleCal`

```python
from googlecal import GoogleCal

cal = GoogleCal()                        # looks for credentials.json/token.json next to googlecal.py
events = cal.get_events_for_day()        # today; pass a date to target a different day
events = cal.get_events_for_week()       # current Mon-Sun calendar week
events = cal.get_events_for_month()      # current calendar month

cal.report_day()                         # prints the day's appointments, returns the Events
cal.report_week()
cal.report_month()
```

Each result is a list of `Event(summary, start, end, location, all_day)`,
sorted by start time. Use `get_events(time_min, time_max)` directly for a
custom window.

## Limitations & privacy notes

- IE support is best-effort and requires the optional `libesedb-python`
  dependency; see the Internet Explorer note above.
- Safari requires Full Disk Access on modern macOS; see the Safari note above.
- History timestamps and matching are approximate — the site-grouping logic
  treats consecutive visits to the *same domain* (with no visit to another
  domain in between) as one browsing session; navigating away and back
  quickly to the same site can under- or over-count sessions.
- The `./history/` folder holds a real copy of your browsing history in
  plain SQLite files. Don't commit it or share it — treat it as sensitive.
- Detection only looks backward from "now," so a session in progress is
  only flagged once its most recent visit falls inside the alert's timing
  window.
