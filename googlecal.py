#!/usr/bin/env python3
"""Reads events from a Google Calendar and reports on appointments for the
current day, week, or month.
"""

import argparse
import datetime as dt
import os.path
from dataclasses import dataclass
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Read-only is enough for reporting; never request write access here.
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


@dataclass
class Event:
    summary: str
    start: dt.datetime
    end: dt.datetime
    location: str = ""
    all_day: bool = False
    id: str = ""


class GoogleCal:
    """Authenticates against the Google Calendar API and fetches events for
    a given time range, or for the current day/week/month.

    Setup:
      1. In Google Cloud Console, enable the "Google Calendar API" and create
         an OAuth 2.0 Client ID of type "Desktop app".
      2. Download the client secret JSON and save it as 'credentials.json'
         next to this file (or pass credentials_file=... explicitly).
      3. On first use, a browser window opens for you to grant access; the
         resulting token is cached in 'token.json' so future runs don't
         prompt again (refreshed automatically once expired).
    """

    def __init__(self, base_dir: str = None, credentials_file: str = "credentials.json",
                 token_file: str = "token.json", calendar_id: str = "primary"):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.credentials_file = self.base_dir / credentials_file
        self.token_file = self.base_dir / token_file
        self.calendar_id = calendar_id
        self._service = None

    # -- authentication ----------------------------------------------------

    def _authenticate(self) -> Credentials:
        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_file.exists():
                    raise FileNotFoundError(
                        f"Missing OAuth client secret at {self.credentials_file}. "
                        "Download it from Google Cloud Console (APIs & Services > "
                        "Credentials) and save it there. See GoogleCal's docstring "
                        "for full setup steps."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_file), SCOPES)
                creds = flow.run_local_server(port=0)
            self.token_file.write_text(creds.to_json())
        return creds

    @property
    def service(self):
        if self._service is None:
            self._service = build("calendar", "v3", credentials=self._authenticate())
        return self._service

    # -- fetching ------------------------------------------------------------

    @staticmethod
    def _parse_event(raw: dict) -> Event:
        start_raw = raw["start"].get("dateTime", raw["start"].get("date"))
        end_raw = raw["end"].get("dateTime", raw["end"].get("date"))
        all_day = "date" in raw["start"] and "dateTime" not in raw["start"]
        if all_day:
            start = dt.datetime.fromisoformat(start_raw)
            end = dt.datetime.fromisoformat(end_raw)
        else:
            start = dt.datetime.fromisoformat(start_raw)
            end = dt.datetime.fromisoformat(end_raw)
        return Event(
            summary=raw.get("summary", "(no title)"),
            start=start,
            end=end,
            location=raw.get("location", ""),
            all_day=all_day,
            id=raw.get("id", ""),
        )

    def get_events(self, time_min: dt.datetime, time_max: dt.datetime) -> list[Event]:
        """Fetch every event on the calendar starting in [time_min, time_max),
        sorted by start time. Naive datetimes are assumed to be local time."""
        if time_min.tzinfo is None:
            time_min = time_min.astimezone()
        if time_max.tzinfo is None:
            time_max = time_max.astimezone()

        events, page_token = [], None
        while True:
            response = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
            ).execute()
            events.extend(self._parse_event(item) for item in response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
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
