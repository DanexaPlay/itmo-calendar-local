"""Single-event iCalendar writer using UTC, stable UID and RFC 5545 folding."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import Settings


def escape_text(value: str) -> str:
    return (value.replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n')
            .replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,'))


def fold_line(line: str) -> bytes:
    parts: list[bytes] = []
    current = bytearray()
    for char in line:
        encoded = char.encode('utf-8')
        if len(current) + len(encoded) > 75:
            parts.append(bytes(current))
            current = bytearray(b' ')
        current.extend(encoded)
    parts.append(bytes(current))
    return b'\r\n'.join(parts) + b'\r\n'


def stamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def instant(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def render_event(row: dict[str, Any], settings: Settings) -> str:
    """Only active local records may be published as events."""
    if not row['active']:
        raise ValueError('inactive_event_cannot_be_rendered')
    event = row['payload']
    summary = event['subject'] + (' - ' + event['work_type'] if event['work_type'] else '')
    description = ['Personal timetable from my.itmo', 'Original timezone: ' + settings.source_timezone]
    for key, label in (('teacher', 'Teacher'), ('group', 'Group'), ('format', 'Format'), ('note', 'Note')):
        if event[key]:
            description.append(label + ': ' + event[key])
    location = ', '.join(x for x in (event['room'], event['building']) if x)
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//ITMO Local Calendar//0.2//EN',
             'CALSCALE:GREGORIAN', 'BEGIN:VEVENT', 'UID:' + row['uid'],
             'DTSTAMP:' + stamp(row['modified']), 'CREATED:' + stamp(row['created']),
             'LAST-MODIFIED:' + stamp(row['modified']), 'SEQUENCE:' + str(row['sequence']),
             'DTSTART:' + instant(event['start']), 'DTEND:' + instant(event['end']),
             'SUMMARY:' + escape_text(summary), 'DESCRIPTION:' + escape_text('\n'.join(description)),
             'STATUS:CONFIRMED', 'TRANSP:OPAQUE', 'X-ITMO-LOCAL-SYNC:V1']
    if location:
        lines.append('LOCATION:' + escape_text(location))
    if settings.alarm_minutes:
        lines += ['BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:' + escape_text(summary),
                  'TRIGGER:-PT' + str(settings.alarm_minutes) + 'M', 'END:VALARM']
    lines += ['END:VEVENT', 'END:VCALENDAR']
    return b''.join(fold_line(line) for line in lines).decode('utf-8')
