#!/usr/bin/env python3
"""Reads events from a *public* Google Calendar and reports on appointments
for the current day, week, or month. Uses the calendar's published iCal
feed over plain HTTP -- no Google API client, no OAuth, nothing for anyone
running this to authenticate.
"""

import argparse
import base64
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import recurring_ical_events
import requests
from icalendar import Calendar

# The calendar's public share link, e.g. from Google Calendar's "Settings and
# sharing" page for a calendar with "Make available to public" turned on.
# The 'cid' query param is just the calendar ID (its owner's email, for a
# primary calendar), base64-encoded.
DEFAULT_SHARE_URL = (
    "https://calendar.google.com/calendar/u/1"
    "?cid=OTQ0ZDgwODBjNTcxMjQ2MmNmZDRlMzJkMjBjYjI4ZTdjZWQ5ZjNhZjU0ZjljODg3OGNiYjkwNWRkZGY5NTgyYUBncm91cC5jYWxlbmRhci5nb29nbGUuY29t"
)

ICS_URL_TEMPLATE = "https://calendar.google.com/calendar/ical/{calendar_id}/public/basic.ics"


def _calendar_id_from_share_url(share_url: str) -> str:
    """Pull the calendar ID out of a 'calendar.google.com/calendar/u/N?cid=...'
    share link -- the cid is the calendar ID, base64-encoded."""
    cid = parse_qs(urlparse(share_url).query).get("cid")
    if not cid:
        raise ValueError(f"Share URL has no 'cid' parameter: {share_url!r}")
    padded = cid[0] + "=" * (-len(cid[0]) % 4)
    return base64.urlsafe_b64decode(padded).decode()


@dataclass
class Event:
    summary: str
    start: dt.datetime
    end: dt.datetime
    location: str = ""
    all_day: bool = False
    id: str = ""


class GoogleCal:
    """Reads events from a Google Calendar via its published iCal feed.

    This only works for a calendar shared publicly -- there's no login, so
    there's nothing private to protect. To make a calendar readable this
    way:
      1. In Google Calendar, open Settings for the calendar to share.
      2. Under "Access permissions", check "Make available to public"
         (either "See only free/busy" or "See all event details").
      3. Grab that calendar's share link (Settings > "Integrate calendar" >
         "Public URL to this calendar", or the link from the calendar's
         three-dot menu > "Settings and sharing") and pass it as
         share_url=, or just pass its calendar ID (usually its owner's
         email address) as calendar_id=.

    Anyone can then read that calendar's events with no credentials of
    their own.
    """

    def __init__(self, calendar_id: str = None, share_url: str = None,
                 base_dir: str = None, timeout: float = 10.0):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        if calendar_id is None:
            calendar_id = _calendar_id_from_share_url(share_url or DEFAULT_SHARE_URL)
        self.calendar_id = calendar_id
        self.ics_url = ICS_URL_TEMPLATE.format(calendar_id=quote(calendar_id, safe=""))
        self.timeout = timeout

    # -- fetching ------------------------------------------------------------

    def _fetch_calendar(self) -> Calendar:
        response = requests.get(self.ics_url, timeout=self.timeout)
        if response.status_code == 404:
            raise RuntimeError(
                f"No public calendar feed at {self.ics_url}. The calendar must have "
                "'Make available to public' turned on (Settings and sharing > "
                "Access permissions) for this to work without authentication."
            )
        response.raise_for_status()
        return Calendar.from_ical(response.content)

    @staticmethod
    def _parse_occurrence(raw) -> Event:
        start = raw["DTSTART"].dt
        end = raw["DTEND"].dt if "DTEND" in raw else start
        all_day = not isinstance(start, dt.datetime)
        if all_day:
            start = dt.datetime.combine(start, dt.time.min)
            end = dt.datetime.combine(end, dt.time.min)
        else:
            if start.tzinfo is None:
                start = start.astimezone()
            if end.tzinfo is None:
                end = end.astimezone()
        uid = str(raw.get("UID", ""))
        return Event(
            summary=str(raw.get("SUMMARY", "(no title)")),
            start=start,
            end=end,
            location=str(raw.get("LOCATION", "")),
            all_day=all_day,
            # Recurring events share one UID across every occurrence, so a
            # single occurrence is identified by UID + its own start time
            # (mirroring the per-occurrence IDs the old Calendar API gave
            # expanded recurring events).
            id=f"{uid}|{start.isoformat()}" if uid else "",
        )

    def get_events(self, time_min: dt.datetime, time_max: dt.datetime) -> list[Event]:
        """Fetch every event occurrence overlapping [time_min, time_max),
        sorted by start time, with recurring events expanded. Naive
        datetimes are assumed to be local time."""
        if time_min.tzinfo is None:
            time_min = time_min.astimezone()
        if time_max.tzinfo is None:
            time_max = time_max.astimezone()

        calendar = self._fetch_calendar()
        occurrences = recurring_ical_events.of(calendar).between(time_min, time_max)
        events = [self._parse_occurrence(occ) for occ in occurrences]
        events.sort(key=lambda e: e.start)
        return events

    # -- convenience windows -------------------------------------------------

    def get_events_for_day(self, day: dt.date = None) -> list[Event]:
        day = day or dt.date.today()
        start = dt.datetime.combine(day, dt.time.min).astimezone()
        end = start + dt.timedelta(days=1)
        return self.get_events(start, end)

    def get_events_for_week(self, day: dt.date = None) -> list[Event]:
        """Events in the Monday-to-Sunday calendar week containing 'day'."""
        day = day or dt.date.today()
        monday = day - dt.timedelta(days=day.weekday())
        start = dt.datetime.combine(monday, dt.time.min).astimezone()
        end = start + dt.timedelta(days=7)
        return self.get_events(start, end)

    def get_events_for_month(self, day: dt.date = None) -> list[Event]:
        """Events in the calendar month containing 'day'."""
        day = day or dt.date.today()
        first = day.replace(day=1)
        next_month = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        start = dt.datetime.combine(first, dt.time.min).astimezone()
        end = dt.datetime.combine(next_month, dt.time.min).astimezone()
        return self.get_events(start, end)

    # -- reporting -------------------------------------------------------------

    @staticmethod
    def _format_event(event: Event) -> str:
        if event.all_day:
            when = f"{event.start.strftime('%Y-%m-%d')} (all day)"
        elif event.start.date() == event.end.date():
            when = f"{event.start.strftime('%Y-%m-%d %H:%M')}-{event.end.strftime('%H:%M')}"
        else:
            when = f"{event.start.strftime('%Y-%m-%d %H:%M')} - {event.end.strftime('%Y-%m-%d %H:%M')}"
        location = f" @ {event.location}" if event.location else ""
        return f"  {when}  {event.summary}{location}"

    def _report(self, events: list[Event], label: str) -> list[Event]:
        print(f"{label}: {len(events)} event{'s' if len(events) != 1 else ''}")
        for event in events:
            print(self._format_event(event))
        return events

    def report_day(self, day: dt.date = None) -> list[Event]:
        day = day or dt.date.today()
        return self._report(self.get_events_for_day(day), f"Appointments for {day}")

    def report_week(self, day: dt.date = None) -> list[Event]:
        day = day or dt.date.today()
        monday = day - dt.timedelta(days=day.weekday())
        sunday = monday + dt.timedelta(days=6)
        return self._report(self.get_events_for_week(day), f"Appointments for week {monday} - {sunday}")

    def report_month(self, day: dt.date = None) -> list[Event]:
        day = day or dt.date.today()
        return self._report(self.get_events_for_month(day), f"Appointments for {day.strftime('%B %Y')}")


PERIOD_REPORTS = {
    "day": GoogleCal.report_day,
    "week": GoogleCal.report_week,
    "month": GoogleCal.report_month,
}


def main(period: str = None):
    """Report on Google Calendar appointments. Does nothing unless a period
    ('day'/'week'/'month') is given -- there's no report-by-default here,
    since this script is also imported just for its GoogleCal class."""
    if period is None:
        return
    if period not in PERIOD_REPORTS:
        raise ValueError(f"period must be one of {sorted(PERIOD_REPORTS)}, got {period!r}")
    cal = GoogleCal()
    PERIOD_REPORTS[period](cal)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Report on Google Calendar appointments for a day, week, or month.")
    parser.add_argument("period", nargs="?", default=None, choices=sorted(PERIOD_REPORTS),
                         help="Window to report on (default: no report unless given)")
    args = parser.parse_args()
    if args.period is None:
        parser.print_help()
    else:
        main(args.period)
