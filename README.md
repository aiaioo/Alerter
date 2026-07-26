A tool to watch my time usage and alert me when I am not making good use of my time.

# Alerter — User Manual

`alerter.py` reads your Chrome, Firefox, Safari, Edge, and Internet Explorer
browsing history and alerts you to four patterns:

- **Doom-scrolling (short-form content)** — repeated visits to one site
  spaced 0 to 10 minutes apart, sustained for more than 15 minutes.
- **Long-form watching** — visits to one site spaced between 10 and 90
  minutes apart, sustained for more than 1.5 hours (e.g. a long video with
  periodic checks/comments).
- **Off-topic browsing** — any URL that isn't related to your configured
  learning goals (default: AI & programming).
- **YouTube limit** — once you've watched more than a daily limit (default
  10) of YouTube Shorts (URLs under `/shorts/`) on a calendar day, every
  Short watched after that is flagged.

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
python3 alerter.py
```

This will:

1. Copy every history database it can find (Chrome, Firefox, Safari, Edge,
   IE) into `./history/`.
2. Report on all four conditions over the last 24 hours: doom-scrolling and
   long-form watching as time periods, off-topic browsing as per-domain
   visit counts, and YouTube Shorts over the daily limit as a per-day count.
3. Check whether you are *currently* doom-scrolling, watching long-form
   content, browsing off-topic, or over your YouTube Shorts limit — and if
   exactly one of those is true, speak an alert out loud (e.g. "I have
   detected that you are doom scrolling.").

Example output:

```
Doom-scrolling in the past 24 hours: 6.58 hours
Periods:
  youtube.com: 01:37 - 02:08
  youtube.com: 18:08 - 20:46
Long-form content watching in the past 24 hours: 0.00 hours
No long-form content watching periods detected.
Off-topic visits in the past 24 hours: 730
  youtube.com: 496 visits
  my.shaadi.com: 72 visits
  linkedin.com: 38 visits
YouTube Shorts over the daily limit of 10 in the past 24 hours: 484
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

# Which alerts are firing right now (last match within the recent window)?
active_now = manager.detect_current(hours=3, recent_window=timedelta(minutes=10))
```

Each match is a `Period(domain, start, end, visit_count)`.

### Printed reports

`AlertManager` also has four methods that evaluate an alert and print a
human-readable report, returning the matched `Period`s as well:

```python
visits = alerter.read_urls(24)
manager.report_doom_scrolling(visits)  # total hours + list of HH:MM-HH:MM periods
manager.report_long_form(visits)       # same, for long-form watching
manager.report_off_topic(visits)       # visit count per off-topic domain
manager.report_youtube_limit(visits)   # count of over-the-limit Shorts, per day
```

All four take an optional `hours` argument (default `24`) purely to label
the printed header — pass the same value you used for `read_urls(n)`. This is
what `main()` calls to produce the report shown in Quick start.

### Built-in alerts

| Class | Trigger | Default threshold |
|---|---|---|
| `DoomScrollAlert` | consecutive same-site visits with gaps under `max_gap` | 0 min < gap < 10 min, sustained > 15 min |
| `LongFormAlert` | consecutive same-site visits with gaps in a mid-range window | 10 min < gap < 90 min, sustained > 1.5 hr |
| `OffTopicAlert` | any visited domain/page not matching your allow-list or keywords | see below |
| `YouTubeLimitAlert` | any YouTube Shorts visit (`/shorts/...`) once today's count exceeds `daily_limit` | `daily_limit=10` |

All thresholds are constructor arguments:

```python
DoomScrollAlert(max_gap=timedelta(minutes=10), min_duration=timedelta(minutes=15))
LongFormAlert(min_gap=timedelta(minutes=10), max_gap=timedelta(minutes=90), min_duration=timedelta(hours=1.5))
YouTubeLimitAlert(daily_limit=10)
```

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

By default, `OffTopicAlert` treats a page as on-topic (AI & programming) if
its domain is on an allow-list, or its URL/title contains a keyword like
`python`, `machine learning`, `algorithm`, etc. Anything else is flagged.
Override either list to match your own learning goals:

```python
OffTopicAlert(
    allowed_domains={"github.com", "arxiv.org", "en.wikipedia.org"},
    keywords={"python", "statistics", "history"},
)
```

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
