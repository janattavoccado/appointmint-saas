"""
Date/Time Utility Module for AppointMint AI Assistant

This module provides timezone-aware date/time handling and natural language
parsing for the AI reservation assistant.

Uses pytz for timezone handling (compatible with all Python versions).

Supports:
- 12-hour and 24-hour time formats
- Relative expressions (today, tomorrow, day after tomorrow)
- Weekday names (Monday, Tuesday, next Friday, this Saturday)
- Common phrases (tonight, this evening, lunch time)
"""

from datetime import datetime, timedelta, date, time
from typing import Optional, Tuple, Dict, Any
import re
import pytz


# Common timezone mappings
TIMEZONE_ALIASES = {
    'EST': 'America/New_York',
    'EDT': 'America/New_York',
    'CST': 'America/Chicago',
    'CDT': 'America/Chicago',
    'MST': 'America/Denver',
    'MDT': 'America/Denver',
    'PST': 'America/Los_Angeles',
    'PDT': 'America/Los_Angeles',
    'GMT': 'Europe/London',
    'BST': 'Europe/London',
    'CET': 'Europe/Paris',
    'CEST': 'Europe/Paris',
    'UTC': 'UTC',
}

# Weekday mappings
WEEKDAYS = {
    'monday': 0, 'mon': 0,
    'tuesday': 1, 'tue': 1, 'tues': 1,
    'wednesday': 2, 'wed': 2,
    'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
    'friday': 4, 'fri': 4,
    'saturday': 5, 'sat': 5,
    'sunday': 6, 'sun': 6,
}

# Common time expressions
TIME_EXPRESSIONS = {
    'noon': (12, 0),
    'midday': (12, 0),
    'midnight': (0, 0),
    'morning': (9, 0),
    'afternoon': (14, 0),
    'evening': (18, 0),
    'night': (20, 0),
    'lunch': (12, 0),
    'lunch time': (12, 0),
    'lunchtime': (12, 0),
    'dinner': (19, 0),
    'dinner time': (19, 0),
    'dinnertime': (19, 0),
    'breakfast': (8, 0),
    'brunch': (11, 0),
}


def get_timezone(tz_string: str):
    """
    Get a pytz timezone object from a timezone string.
    Handles common aliases like EST, PST, etc.
    """
    # Check if it's an alias
    if tz_string.upper() in TIMEZONE_ALIASES:
        tz_string = TIMEZONE_ALIASES[tz_string.upper()]
    
    try:
        return pytz.timezone(tz_string)
    except pytz.exceptions.UnknownTimeZoneError:
        # Default to UTC if invalid
        return pytz.UTC


def get_current_datetime(timezone: str = 'UTC') -> Dict[str, Any]:
    """
    Get the current date and time in the specified timezone.
    
    Returns a dictionary with formatted date/time information that the AI
    assistant can use to understand the current context.
    """
    tz = get_timezone(timezone)
    now = datetime.now(tz)
    
    return {
        'timezone': timezone,
        'current_date': now.strftime('%Y-%m-%d'),
        'current_time': now.strftime('%H:%M'),
        'current_time_12h': now.strftime('%I:%M %p'),
        'day_of_week': now.strftime('%A'),
        'formatted_date': now.strftime('%A, %B %d, %Y'),
        'formatted_datetime': now.strftime('%A, %B %d, %Y at %I:%M %p'),
        'iso_datetime': now.isoformat(),
        'unix_timestamp': int(now.timestamp()),
        'hour': now.hour,
        'minute': now.minute,
        'is_morning': 5 <= now.hour < 12,
        'is_afternoon': 12 <= now.hour < 17,
        'is_evening': 17 <= now.hour < 21,
        'is_night': now.hour >= 21 or now.hour < 5,
    }


def parse_relative_date(expression: str, reference_date: date = None, timezone: str = 'UTC') -> Optional[date]:
    """
    Parse relative date expressions like 'today', 'tomorrow', 'next Monday'.
    
    Args:
        expression: The date expression to parse
        reference_date: The reference date (defaults to today in the given timezone)
        timezone: The timezone to use for 'today'
    
    Returns:
        A date object or None if parsing fails
    """
    if reference_date is None:
        tz = get_timezone(timezone)
        reference_date = datetime.now(tz).date()
    
    expression = expression.lower().strip()
    
    # Direct date expressions
    if expression in ('today', 'tonight', 'this evening'):
        return reference_date
    
    if expression in ('tomorrow', 'tmrw', 'tmr'):
        return reference_date + timedelta(days=1)
    
    if expression in ('day after tomorrow', 'overmorrow'):
        return reference_date + timedelta(days=2)
    
    if expression == 'yesterday':
        return reference_date - timedelta(days=1)
    
    # "In X days" pattern
    in_days_match = re.match(r'in\s+(\d+)\s+days?', expression)
    if in_days_match:
        days = int(in_days_match.group(1))
        return reference_date + timedelta(days=days)
    
    # Weekday patterns
    current_weekday = reference_date.weekday()
    
    # "this [weekday]" - the upcoming occurrence this week
    this_weekday_match = re.match(r'this\s+(\w+)', expression)
    if this_weekday_match:
        weekday_name = this_weekday_match.group(1).lower()
        if weekday_name in WEEKDAYS:
            target_weekday = WEEKDAYS[weekday_name]
            days_ahead = target_weekday - current_weekday
            if days_ahead <= 0:
                days_ahead += 7
            return reference_date + timedelta(days=days_ahead)
    
    # "next [weekday]" - the occurrence in the next week
    next_weekday_match = re.match(r'next\s+(\w+)', expression)
    if next_weekday_match:
        weekday_name = next_weekday_match.group(1).lower()
        if weekday_name in WEEKDAYS:
            target_weekday = WEEKDAYS[weekday_name]
            days_ahead = target_weekday - current_weekday
            if days_ahead <= 0:
                days_ahead += 7
            # Add another week for "next"
            days_ahead += 7
            return reference_date + timedelta(days=days_ahead)
    
    # Just a weekday name (e.g., "Monday") - next occurrence
    if expression in WEEKDAYS:
        target_weekday = WEEKDAYS[expression]
        days_ahead = target_weekday - current_weekday
        if days_ahead <= 0:
            days_ahead += 7
        return reference_date + timedelta(days=days_ahead)
    
    # Try to parse as a date string
    date_formats = [
        '%Y-%m-%d',      # 2026-01-20
        '%m/%d/%Y',      # 01/20/2026
        '%m/%d/%y',      # 01/20/26
        '%d/%m/%Y',      # 20/01/2026
        '%B %d, %Y',     # January 20, 2026
        '%B %d %Y',      # January 20 2026
        '%b %d, %Y',     # Jan 20, 2026
        '%b %d %Y',      # Jan 20 2026
        '%d %B %Y',      # 20 January 2026
        '%d %b %Y',      # 20 Jan 2026
        '%B %d',         # January 20 (assumes current year)
        '%b %d',         # Jan 20 (assumes current year)
    ]
    
    for fmt in date_formats:
        try:
            parsed = datetime.strptime(expression, fmt)
            # If year is 1900 (default), use current year
            if parsed.year == 1900:
                parsed = parsed.replace(year=reference_date.year)
                # If the date has passed, assume next year
                if parsed.date() < reference_date:
                    parsed = parsed.replace(year=reference_date.year + 1)
            return parsed.date()
        except ValueError:
            continue
    
    return None


def parse_time(expression: str) -> Optional[Tuple[int, int]]:
    """
    Parse time expressions in various formats.
    
    Supports:
    - 24-hour format: "14:30", "19:00"
    - 12-hour format: "2:30 PM", "7 PM", "7pm"
    - Common expressions: "noon", "evening", "dinner time"
    
    Returns:
        A tuple of (hour, minute) or None if parsing fails
    """
    expression = expression.lower().strip()
    
    # Check common expressions first
    if expression in TIME_EXPRESSIONS:
        return TIME_EXPRESSIONS[expression]
    
    # 24-hour format: "14:30", "19:00", "9:00"
    match_24h = re.match(r'^(\d{1,2}):(\d{2})$', expression)
    if match_24h:
        hour = int(match_24h.group(1))
        minute = int(match_24h.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour, minute)
    
    # 12-hour format with minutes: "2:30 PM", "2:30pm", "2:30 pm"
    match_12h_min = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)$', expression)
    if match_12h_min:
        hour = int(match_12h_min.group(1))
        minute = int(match_12h_min.group(2))
        period = match_12h_min.group(3).replace('.', '')
        
        if 1 <= hour <= 12 and 0 <= minute <= 59:
            if period == 'pm' and hour != 12:
                hour += 12
            elif period == 'am' and hour == 12:
                hour = 0
            return (hour, minute)
    
    # 12-hour format without minutes: "7 PM", "7pm", "7 pm"
    match_12h = re.match(r'^(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)$', expression)
    if match_12h:
        hour = int(match_12h.group(1))
        period = match_12h.group(2).replace('.', '')
        
        if 1 <= hour <= 12:
            if period == 'pm' and hour != 12:
                hour += 12
            elif period == 'am' and hour == 12:
                hour = 0
            return (hour, 0)
    
    # Just hour in 24h format: "19", "7" (ambiguous, assume PM for restaurant context)
    match_hour = re.match(r'^(\d{1,2})$', expression)
    if match_hour:
        hour = int(match_hour.group(1))
        if 0 <= hour <= 23:
            # For restaurant context, assume evening hours for single digits
            if hour < 12 and hour >= 1 and hour <= 9:
                hour += 12  # Assume PM for 1-9
            return (hour, 0)
    
    # "X o'clock" format
    match_oclock = re.match(r'^(\d{1,2})\s*o\'?clock\s*(am|pm)?$', expression)
    if match_oclock:
        hour = int(match_oclock.group(1))
        period = match_oclock.group(2)
        
        if 1 <= hour <= 12:
            if period == 'pm' and hour != 12:
                hour += 12
            elif period == 'am' and hour == 12:
                hour = 0
            elif period is None and hour < 12:
                # Assume PM for restaurant context
                hour += 12
            return (hour, 0)
    
    # "half past X" format
    match_half = re.match(r'^half\s+past\s+(\d{1,2})\s*(am|pm)?$', expression)
    if match_half:
        hour = int(match_half.group(1))
        period = match_half.group(2)
        
        if 1 <= hour <= 12:
            if period == 'pm' and hour != 12:
                hour += 12
            elif period == 'am' and hour == 12:
                hour = 0
            elif period is None and hour < 12:
                hour += 12
            return (hour, 30)
    
    # "quarter past X" format
    match_quarter_past = re.match(r'^quarter\s+past\s+(\d{1,2})\s*(am|pm)?$', expression)
    if match_quarter_past:
        hour = int(match_quarter_past.group(1))
        period = match_quarter_past.group(2)
        
        if 1 <= hour <= 12:
            if period == 'pm' and hour != 12:
                hour += 12
            elif period == 'am' and hour == 12:
                hour = 0
            elif period is None and hour < 12:
                hour += 12
            return (hour, 15)
    
    # "quarter to X" format
    match_quarter_to = re.match(r'^quarter\s+to\s+(\d{1,2})\s*(am|pm)?$', expression)
    if match_quarter_to:
        hour = int(match_quarter_to.group(1))
        period = match_quarter_to.group(2)
        
        if 1 <= hour <= 12:
            # Quarter to X means X-1:45
            if period == 'pm' and hour != 12:
                hour += 12
            elif period == 'am' and hour == 12:
                hour = 0
            elif period is None and hour < 12:
                hour += 12
            hour -= 1
            if hour < 0:
                hour = 23
            return (hour, 45)
    
    return None


def parse_datetime(expression: str, timezone: str = 'UTC') -> Optional[Dict[str, Any]]:
    """
    Parse a combined date/time expression.
    
    Examples:
    - "tomorrow at 7pm"
    - "next Friday at 19:00"
    - "January 25 at noon"
    - "this Saturday evening"
    
    Returns:
        A dictionary with parsed date and time information
    """
    expression = expression.lower().strip()
    tz = get_timezone(timezone)
    now = datetime.now(tz)
    
    result = {
        'original': expression,
        'parsed_date': None,
        'parsed_time': None,
        'datetime': None,
        'formatted': None,
        'success': False,
    }
    
    # Try to split by "at" or "@"
    parts = re.split(r'\s+at\s+|\s*@\s*', expression, maxsplit=1)
    
    if len(parts) == 2:
        date_part, time_part = parts
        parsed_date = parse_relative_date(date_part, now.date(), timezone)
        parsed_time = parse_time(time_part)
        
        if parsed_date and parsed_time:
            result['parsed_date'] = parsed_date.isoformat()
            result['parsed_time'] = f"{parsed_time[0]:02d}:{parsed_time[1]:02d}"
            dt = datetime.combine(parsed_date, time(parsed_time[0], parsed_time[1]))
            dt = tz.localize(dt)
            result['datetime'] = dt.isoformat()
            result['formatted'] = dt.strftime('%A, %B %d, %Y at %I:%M %p')
            result['success'] = True
            return result
    
    # Try parsing as just a date
    parsed_date = parse_relative_date(expression, now.date(), timezone)
    if parsed_date:
        result['parsed_date'] = parsed_date.isoformat()
        result['success'] = True
        return result
    
    # Try parsing as just a time
    parsed_time = parse_time(expression)
    if parsed_time:
        result['parsed_time'] = f"{parsed_time[0]:02d}:{parsed_time[1]:02d}"
        result['success'] = True
        return result
    
    return result


def format_time_12h(hour: int, minute: int) -> str:
    """Format time in 12-hour format."""
    period = 'AM' if hour < 12 else 'PM'
    display_hour = hour % 12
    if display_hour == 0:
        display_hour = 12
    if minute == 0:
        return f"{display_hour} {period}"
    return f"{display_hour}:{minute:02d} {period}"


def format_time_24h(hour: int, minute: int) -> str:
    """Format time in 24-hour format."""
    return f"{hour:02d}:{minute:02d}"


def get_common_timezones() -> list:
    """Get a list of common timezones for restaurant configuration."""
    return [
        ('UTC', 'UTC (Coordinated Universal Time)'),
        ('America/New_York', 'Eastern Time (US & Canada)'),
        ('America/Chicago', 'Central Time (US & Canada)'),
        ('America/Denver', 'Mountain Time (US & Canada)'),
        ('America/Los_Angeles', 'Pacific Time (US & Canada)'),
        ('America/Anchorage', 'Alaska'),
        ('Pacific/Honolulu', 'Hawaii'),
        ('Europe/London', 'London'),
        ('Europe/Paris', 'Paris, Berlin, Rome'),
        ('Europe/Moscow', 'Moscow'),
        ('Europe/Zagreb', 'Zagreb, Belgrade'),
        ('Asia/Dubai', 'Dubai'),
        ('Asia/Kolkata', 'Mumbai, New Delhi'),
        ('Asia/Singapore', 'Singapore'),
        ('Asia/Tokyo', 'Tokyo'),
        ('Asia/Shanghai', 'Beijing, Shanghai'),
        ('Australia/Sydney', 'Sydney'),
        ('Pacific/Auckland', 'Auckland'),
    ]
