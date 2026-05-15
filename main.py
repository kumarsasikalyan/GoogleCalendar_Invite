import os
import json
import re
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build

import logging
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable is not set")

    creds_info = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(
        creds_info, scopes=SCOPES
    )
    service = build("calendar", "v3", credentials=credentials)
    return service


MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?"
    r"|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)


def parse_date(text, now):
    """Return a date parsed from text, defaulting to tomorrow."""

    # today / tomorrow / day after tomorrow
    if re.search(r"\btoday\b", text, re.IGNORECASE):
        return now.date()
    if re.search(r"\bday after tomorrow\b", text, re.IGNORECASE):
        return (now + timedelta(days=2)).date()
    if re.search(r"\btomorrow\b", text, re.IGNORECASE):
        return (now + timedelta(days=1)).date()

    # "next Monday" / "on Monday" / plain day name
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, day in enumerate(day_names):
        if re.search(r"\b" + day + r"\b", text, re.IGNORECASE):
            days_ahead = i - now.weekday()
            # "next X" always pushes to next week even if today is that day
            if re.search(r"\bnext\b", text, re.IGNORECASE):
                days_ahead = days_ahead if days_ahead > 0 else days_ahead + 7
            else:
                if days_ahead <= 0:
                    days_ahead += 7
            return (now + timedelta(days=days_ahead)).date()

    # YYYY-MM-DD
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()

    # "May 15" / "May 15th"
    m = re.search(
        r"\b(" + MONTH_PATTERN + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", text, re.IGNORECASE
    )
    if m:
        month = MONTH_MAP[m.group(1).lower()[:3]]
        day = int(m.group(2))
        year = now.year
        if datetime(year, month, day).date() < now.date():
            year += 1
        return datetime(year, month, day).date()

    # "15th May" / "15 May"
    m = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + MONTH_PATTERN + r")\b", text, re.IGNORECASE
    )
    if m:
        day = int(m.group(1))
        month = MONTH_MAP[m.group(2).lower()[:3]]
        year = now.year
        if datetime(year, month, day).date() < now.date():
            year += 1
        return datetime(year, month, day).date()

    # DD/MM/YYYY or DD/MM
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", text)
    if m:
        try:
            day, month = int(m.group(1)), int(m.group(2))
            year = int(m.group(3)) if m.group(3) else now.year
            return datetime(year, month, day).date()
        except ValueError:
            pass

    # Default: tomorrow
    return (now + timedelta(days=1)).date()


TZ_OFFSETS = {
    "ist": 330,    # India Standard Time UTC+5:30
    "gmt": 0,
    "utc": 0,
    "est": -300,   # Eastern Standard Time UTC-5
    "edt": -240,   # Eastern Daylight Time UTC-4
    "cst": -360,
    "cdt": -300,
    "mst": -420,
    "mdt": -360,
    "pst": -480,
    "pdt": -420,
}

def parse_tz_offset(text):
    """Return UTC offset in minutes from timezone abbreviation in text."""
    m = re.search(r"\b(ist|gmt|utc|e[sd]t|c[sd]t|m[sd]t|p[sd]t)\b", text, re.IGNORECASE)
    if m:
        return TZ_OFFSETS.get(m.group(1).lower(), 0)
    return 0


def parse_time(text):
    """Return (hour, minute) parsed from text, defaulting to 10:00."""

    # HH:MM am/pm  e.g. "3:30 pm", "10:00am"
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)\b", text, re.IGNORECASE)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if m.group(3).lower() == "pm" and hour != 12:
            hour += 12
        elif m.group(3).lower() == "am" and hour == 12:
            hour = 0
        return hour, minute

    # HH:MM 24-hour  e.g. "14:30", "09:00"
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # H am/pm  e.g. "3pm", "10 am"
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        if m.group(2).lower() == "pm" and hour != 12:
            hour += 12
        elif m.group(2).lower() == "am" and hour == 12:
            hour = 0
        return hour, 0

    # "at 14" bare 24-hour with "at"
    m = re.search(r"\bat\s+(\d{1,2})\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), 0

    # Default: 10:00
    return 10, 0


def parse_meeting_details(text):
    # Emails
    emails = re.findall(r"[\w.\-+]+@[\w.\-]+\.\w+", text)

    # Subject
    subject = "Team Meeting"
    for pattern in [
        r"(?:subject|about|topic|regarding)[:\s]+([^\n,]+)",
        r'"([^"]+)"',
        r"'([^']+)'",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            subject = m.group(1).strip()
            break

    # Date and time
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    date = parse_date(text, now)
    hour, minute = parse_time(text)
    tz_offset_minutes = parse_tz_offset(text)

    start_local = datetime(date.year, date.month, date.day, hour, minute, 0)
    start = start_local - timedelta(minutes=tz_offset_minutes)
    end = start + timedelta(hours=1)

    return emails, subject, start, end


def extract_message_text(params):
    try:
        msg = params.get("message", {})
        if isinstance(msg, dict):
            text = ""
            for part in msg.get("parts", []):
                if isinstance(part, dict) and part.get("kind", part.get("type")) == "text":
                    text += part.get("text", "")
            return text
        if isinstance(msg, str):
            return msg
    except Exception:
        pass
    return str(params)


def make_result(req_id, state, text, task_id=None, method="tasks/send"):
    if method == "message/send":
        return jsonify({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": str(req_id) + "-reply",
                "parts": [{"kind": "text", "text": text}],
            },
        })
    return jsonify({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "id": task_id or req_id,
            "status": {"state": state},
            "artifacts": [
                {"parts": [{"type": "text", "text": text}]}
            ],
        },
    })


@app.route("/.well-known/agent.json")
def agent_card():
    base_url = request.host_url.rstrip("/")
    return jsonify({
        "name": "Google Calendar Scheduler",
        "description": (
            "Schedules Google Calendar meetings and sends email invites. "
            "Tell me the attendee emails and a subject to create a meeting."
        ),
        "url": f"{base_url}/",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "schedule_meeting",
                "name": "Schedule Meeting",
                "description": (
                    "Schedule a Google Calendar meeting between two or more people "
                    "and send them email invites."
                ),
                "tags": ["calendar", "meeting", "scheduling", "google"],
                "examples": [
                    "Schedule a meeting between alice@gmail.com and bob@gmail.com about Project Kickoff",
                    "Schedule a meeting between alice@company.com and bob@company.com subject Weekly Sync",
                ],
            }
        ],
    })


@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Google Calendar A2A Agent is running"})


@app.route("/", methods=["POST"])
def handle_task():
    data = request.get_json(silent=True, force=True)
    if not data:
        app.logger.warning("RAW BODY: %s", request.get_data(as_text=True))
        return jsonify({"error": "Invalid JSON body"}), 400

    app.logger.warning("REQUEST: %s", json.dumps(data))
    req_id = data.get("id", "1")
    method = data.get("method", "")
    params = data.get("params", {})

    # Accept these A2A methods
    if method not in ("tasks/send", "tasks/get", "message/send"):
        return jsonify({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not supported: {method}"},
        })

    task_id = params.get("id", req_id)
    message_text = extract_message_text(params)

    if not message_text.strip():
        return make_result(req_id, "failed",
                           "Please tell me the attendee emails and meeting subject.", task_id, method)

    try:
        emails, subject, start_time, end_time = parse_meeting_details(message_text)

        if not emails:
            return make_result(req_id, "failed",
                               "I could not find any email addresses. "
                               "Please include the attendee emails in your message.", task_id, method)

        calendar_email = os.environ.get("CALENDAR_EMAIL")
        if not calendar_email:
            return make_result(req_id, "failed",
                               "Server configuration error: CALENDAR_EMAIL is not set.", task_id, method)

        service = get_calendar_service()

        event_body = {
            "summary": subject,
            "description": "Attendees: " + ", ".join(emails),
            "start": {"dateTime": start_time.isoformat() + "Z", "timeZone": "UTC"},
            "end":   {"dateTime": end_time.isoformat() + "Z",   "timeZone": "UTC"},
        }

        event = service.events().insert(
            calendarId=calendar_email,
            body=event_body,
        ).execute()

        reply = (
            f"Meeting scheduled successfully!\n"
            f"Subject:   {subject}\n"
            f"Attendees: {', '.join(emails)}\n"
            f"Date:      {start_time.strftime('%A, %B %d %Y')}\n"
            f"Time:      {start_time.strftime('%I:%M %p')} UTC\n"
            f"Event link: {event.get('htmlLink', 'N/A')}"
        )
        return make_result(req_id, "completed", reply, task_id, method)

    except Exception as exc:
        return make_result(req_id, "failed", f"Failed to schedule meeting: {exc}", task_id, method)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
