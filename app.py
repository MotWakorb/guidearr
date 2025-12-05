#!/usr/bin/env python3
"""
Dispatcharr Channel Guide - Flask Application
Generates a dynamic HTML channel guide from Dispatcharr API data with caching.
"""

import os
import re
import requests
import threading
import time
from datetime import datetime
from flask import Flask, render_template_string, jsonify
from typing import Dict, List, Optional, Tuple
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

app = Flask(__name__)

# Configuration from environment variables
DISPATCHARR_BASE_URL = os.getenv('DISPATCHARR_BASE_URL', 'http://localhost:9191')
DISPATCHARR_USERNAME = os.getenv('DISPATCHARR_USERNAME', '')
DISPATCHARR_PASSWORD = os.getenv('DISPATCHARR_PASSWORD', '')
CHANNEL_PROFILE_NAME = os.getenv('CHANNEL_PROFILE_NAME', '')
EXCLUDE_CHANNEL_GROUPS = os.getenv('EXCLUDE_CHANNEL_GROUPS', '')
PAGE_TITLE = os.getenv('PAGE_TITLE', 'TV Channel Guide')
CACHE_REFRESH_CRON = os.getenv('CACHE_REFRESH_CRON', '0 */6 * * *')  # Default: every 6 hours

# Global cache
cache = {
    'html': None,
    'channels': None,
    'groups_map': None,
    'logos_map': None,
    'epg_programs': None,
    'last_updated': None,
    'error': None,
    'lock': threading.Lock()
}


def get_access_token() -> str:
    """Authenticate to Dispatcharr and get JWT access token."""
    base_url = DISPATCHARR_BASE_URL.rstrip('/')
    token_url = f"{base_url}/api/accounts/token/"

    payload = {
        'username': DISPATCHARR_USERNAME,
        'password': DISPATCHARR_PASSWORD
    }

    response = requests.post(token_url, json=payload)
    response.raise_for_status()

    data = response.json()
    if 'access' not in data:
        raise Exception("Token endpoint response did not contain 'access'")

    return data['access']


def get_channel_groups(access_token: str) -> Dict[int, str]:
    """Fetch all channel groups and return a mapping of ID -> name."""
    base_url = DISPATCHARR_BASE_URL.rstrip('/')
    groups_url = f"{base_url}/api/channels/groups/"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }

    response = requests.get(groups_url, headers=headers)
    response.raise_for_status()

    groups = response.json()

    # Build mapping of group ID to group name
    groups_map = {}
    for group in groups:
        if 'id' in group and 'name' in group:
            groups_map[group['id']] = group['name']

    return groups_map


def get_logos(access_token: str) -> Dict[int, dict]:
    """Fetch all logos and return a mapping of ID -> logo object."""
    base_url = DISPATCHARR_BASE_URL.rstrip('/')
    logos_url = f"{base_url}/api/channels/logos/"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }

    all_logos = []

    response = requests.get(logos_url, headers=headers)
    response.raise_for_status()

    data = response.json()

    # Handle both array and paginated responses
    if isinstance(data, list):
        all_logos = data
    elif isinstance(data, dict) and 'results' in data:
        # Paginated response
        all_logos.extend(data.get('results', []))

        next_url = data.get('next')
        while next_url:
            if next_url.startswith('http://') or next_url.startswith('https://'):
                full_url = next_url
            else:
                full_url = f"{base_url}/{next_url.lstrip('/')}"

            response = requests.get(full_url, headers=headers)
            response.raise_for_status()

            page_data = response.json()
            if not page_data:
                break

            if 'results' in page_data:
                all_logos.extend(page_data.get('results', []))

            next_url = page_data.get('next')

    # Build mapping of logo ID to logo object
    logos_map = {}
    for logo in all_logos:
        if 'id' in logo:
            logos_map[logo['id']] = logo

    return logos_map


def get_channels(access_token: str, page_size: int = 100) -> List[dict]:
    """Fetch all channels from Dispatcharr API."""
    base_url = DISPATCHARR_BASE_URL.rstrip('/')
    channels_url = f"{base_url}/api/channels/channels/?page_size={page_size}"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }

    response = requests.get(channels_url, headers=headers)
    response.raise_for_status()

    data = response.json()

    # Handle both array and paginated responses
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and 'results' in data:
        all_channels = []
        all_channels.extend(data.get('results', []))

        next_url = data.get('next')
        while next_url:
            if next_url.startswith('http://') or next_url.startswith('https://'):
                full_url = next_url
            else:
                full_url = f"{base_url}/{next_url.lstrip('/')}"

            response = requests.get(full_url, headers=headers)
            response.raise_for_status()

            page_data = response.json()
            if not page_data:
                break

            if 'results' in page_data:
                all_channels.extend(page_data.get('results', []))

            next_url = page_data.get('next')

        return all_channels

    raise Exception("Unexpected response structure from channels endpoint")


def get_epg_programs_by_date_range(access_token: str, start_time: datetime, end_time: datetime) -> List[dict]:
    """
    Try to fetch EPG programs for a specific date range.
    Falls back to get_epg_grid if date range filtering is not supported.
    """
    base_url = DISPATCHARR_BASE_URL.rstrip('/')

    # Try /api/epg/programs/ with date filters
    programs_url = f"{base_url}/api/epg/programs/"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }

    # Try common date filter parameter patterns
    params_attempts = [
        {
            'start_time__gte': start_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_time__lte': end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
        },
        {
            'start_time_min': start_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_time_max': end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
        },
        {
            'start': start_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end': end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
        }
    ]

    for params in params_attempts:
        try:
            response = requests.get(programs_url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                programs = response.json()

                # Handle different response structures
                if isinstance(programs, list):
                    program_list = programs
                elif isinstance(programs, dict):
                    program_list = programs.get('data', programs.get('results', []))
                else:
                    continue

                # If we got programs, filter them by date range on client side
                if program_list:
                    filtered = []
                    for prog in program_list:
                        try:
                            prog_start = datetime.fromisoformat(prog['start_time'].replace('Z', '+00:00'))
                            prog_end = datetime.fromisoformat(prog['end_time'].replace('Z', '+00:00'))
                            if prog_start.tzinfo:
                                prog_start = prog_start.replace(tzinfo=None)
                            if prog_end.tzinfo:
                                prog_end = prog_end.replace(tzinfo=None)

                            # Check if program overlaps with our time range
                            if prog_start < end_time and prog_end > start_time:
                                filtered.append(prog)
                        except (ValueError, KeyError):
                            continue

                    if filtered:
                        print(f"Successfully fetched {len(filtered)} programs for date range {start_time} to {end_time}")
                        return filtered
        except Exception as e:
            print(f"Attempt with params {params} failed: {e}")
            continue

    # If all attempts failed, fall back to grid endpoint
    print(f"Date range filtering not supported, falling back to /api/epg/grid/")
    return get_epg_grid(access_token)


def get_epg_grid(access_token: str) -> List[dict]:
    """
    Fetch EPG grid data (programs from previous hour, currently running, and upcoming for next 24 hours).
    Returns a list of program dictionaries.
    """
    base_url = DISPATCHARR_BASE_URL.rstrip('/')
    epg_grid_url = f"{base_url}/api/epg/grid/"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }

    response = requests.get(epg_grid_url, headers=headers)
    response.raise_for_status()

    programs = response.json()

    # Handle different response structures
    if isinstance(programs, list):
        return programs
    elif isinstance(programs, dict):
        # Check for 'data' key (Dispatcharr format)
        if 'data' in programs:
            return programs.get('data', [])
        # Check for 'results' key (paginated format)
        elif 'results' in programs:
            return programs.get('results', [])

    return []


def get_channel_profile_by_name(access_token: str, profile_name: str) -> Optional[dict]:
    """Get a specific channel profile by name."""
    base_url = DISPATCHARR_BASE_URL.rstrip('/')
    profiles_url = f"{base_url}/api/channels/profiles/"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }

    response = requests.get(profiles_url, headers=headers)
    response.raise_for_status()

    profiles = response.json()

    # Find profile by name (case-insensitive)
    for profile in profiles:
        if profile.get('name', '').lower() == profile_name.lower():
            return profile

    return None


def get_channel_ids_from_profile(profile: dict) -> List[int]:
    """Extract channel IDs from a channel profile."""
    channels_prop = profile.get('channels', [])

    if not channels_prop:
        return []

    channel_ids = []

    if isinstance(channels_prop, list):
        for item in channels_prop:
            if isinstance(item, int):
                channel_ids.append(item)
            elif isinstance(item, str) and item.isdigit():
                channel_ids.append(int(item))

    return channel_ids


def get_current_program_for_channel(channel: dict, programs: List[dict]) -> Optional[dict]:
    """
    Find the currently airing program for a given channel.
    Matches by tvg_id and checks if current time is between start_time and end_time.
    """
    tvg_id = channel.get('tvg_id')
    if not tvg_id:
        return None

    now = datetime.utcnow()

    for program in programs:
        # Match by tvg_id
        if program.get('tvg_id') != tvg_id:
            continue

        # Parse times
        try:
            start_time = datetime.fromisoformat(program['start_time'].replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(program['end_time'].replace('Z', '+00:00'))

            # Remove timezone info for comparison if present
            if start_time.tzinfo:
                start_time = start_time.replace(tzinfo=None)
            if end_time.tzinfo:
                end_time = end_time.replace(tzinfo=None)

            # Check if now is between start and end
            if start_time <= now <= end_time:
                return program
        except (ValueError, KeyError, AttributeError):
            continue

    return None


def get_next_program_for_channel(channel: dict, programs: List[dict]) -> Optional[dict]:
    """
    Find the next upcoming program for a given channel.
    Returns the program that starts soonest after the current program ends.
    """
    tvg_id = channel.get('tvg_id')
    if not tvg_id:
        return None

    now = datetime.utcnow()
    upcoming_programs = []

    for program in programs:
        # Match by tvg_id
        if program.get('tvg_id') != tvg_id:
            continue

        # Parse times
        try:
            start_time = datetime.fromisoformat(program['start_time'].replace('Z', '+00:00'))

            # Remove timezone info for comparison if present
            if start_time.tzinfo:
                start_time = start_time.replace(tzinfo=None)

            # Only consider programs that haven't started yet
            if start_time > now:
                upcoming_programs.append({
                    'program': program,
                    'start_time': start_time
                })
        except (ValueError, KeyError, AttributeError):
            continue

    # Sort by start time and return the earliest
    if upcoming_programs:
        upcoming_programs.sort(key=lambda x: x['start_time'])
        return upcoming_programs[0]['program']

    return None


def clean_channel_name(name: str) -> str:
    """Remove channel number prefix from channel name (e.g., '2.1 | ABC News' -> 'ABC News')."""
    if not name:
        return "Unknown Channel"

    # Remove pattern like "2.1 | " or "102 | "
    cleaned = re.sub(r'^\d+(\.\d+)?\s*\|\s*', '', name)
    return cleaned if cleaned else name


def generate_grid_html(timeline_html: str, rows_html: str, hours: int, num_slots: int, slot_width: int, selected_date: str = '', start_hour: int = 0, channel_count: int = 0) -> str:
    """Generate the complete HTML for the grid view."""
    total_width = num_slots * slot_width

    last_updated = cache.get('last_updated')
    updated_str = last_updated.strftime('%Y-%m-%d %H:%M:%S') if last_updated else 'Unknown'

    # Generate date options (today + next 7 days)
    from datetime import timedelta
    today = datetime.utcnow().date()
    date_options = []
    for i in range(8):
        date = today + timedelta(days=i)
        date_str = date.strftime('%Y-%m-%d')
        display_label = 'Today' if i == 0 else ('Tomorrow' if i == 1 else date.strftime('%A, %b %d'))
        selected = 'selected' if date_str == selected_date else ''
        date_options.append(f'<option value="{date_str}" {selected}>{display_label}</option>')

    date_options_html = '\n'.join(date_options)

    # Generate hour options
    hour_options = []
    for h in range(0, 24, 6):
        selected = 'selected' if h == start_hour else ''
        hour_label = f"{h:02d}:00" if h > 0 else "Midnight"
        if h == 6:
            hour_label = "6:00 AM"
        elif h == 12:
            hour_label = "Noon"
        elif h == 18:
            hour_label = "6:00 PM"
        hour_options.append(f'<option value="{h}" {selected}>{hour_label}</option>')

    hour_options_html = '\n'.join(hour_options)

    html = f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{PAGE_TITLE} - Grid View</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}

            :root {{
                --bg-primary: #0a0a0f;
                --bg-secondary: #16213e;
                --bg-tertiary: #1a1a2e;
                --text-primary: #e8e8e8;
                --text-secondary: #9aa5ce;
                --border-color: #2a3f5f;
                --channel-bg: #1a1a2e;
                --program-bg: #2d3e5f;
                --program-hover: #3d4e6f;
                --timeline-bg: #16213e;
            }}

            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: var(--bg-primary);
                color: var(--text-primary);
                overflow: hidden;
                height: 100vh;
            }}

            .header {{
                background: var(--bg-secondary);
                padding: 15px 20px;
                border-bottom: 2px solid var(--border-color);
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}

            .header h1 {{
                font-size: 1.5em;
                color: var(--text-primary);
            }}

            .view-toggle {{
                background: #667eea;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-weight: 600;
                text-decoration: none;
                display: inline-block;
            }}

            .view-toggle:hover {{
                background: #5568d3;
            }}

            .date-selector {{
                display: flex;
                gap: 10px;
                align-items: center;
                flex: 1;
                justify-content: center;
            }}

            .date-selector label {{
                color: var(--text-secondary);
                font-size: 0.9em;
                font-weight: 500;
            }}

            .date-selector select {{
                background: var(--bg-tertiary);
                color: var(--text-primary);
                border: 1px solid var(--border-color);
                padding: 8px 12px;
                border-radius: 6px;
                font-size: 0.95em;
                cursor: pointer;
                min-width: 150px;
            }}

            .date-selector select:hover {{
                border-color: #667eea;
            }}

            .date-selector select:focus {{
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 2px rgba(102, 126, 234, 0.2);
            }}

            .date-selector button {{
                background: #50C878;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-weight: 600;
                font-size: 0.95em;
                transition: background 0.2s;
            }}

            .date-selector button:hover {{
                background: #45b369;
            }}

            .grid-container {{
                height: calc(100vh - 100px);
                display: flex;
                flex-direction: column;
                overflow: hidden;
            }}

            .timeline-header {{
                display: flex;
                position: sticky;
                top: 0;
                z-index: 100;
                background: var(--timeline-bg);
                border-bottom: 2px solid var(--border-color);
                overflow-x: hidden;
                overflow-y: hidden;
            }}

            .timeline-header-spacer {{
                width: 200px;
                flex-shrink: 0;
                border-right: 2px solid var(--border-color);
                background: var(--timeline-bg);
            }}

            .time-slot-header {{
                width: {slot_width}px;
                flex-shrink: 0;
                text-align: center;
                padding: 12px 8px;
                font-weight: 600;
                font-size: 0.9em;
                border-right: 1px solid var(--border-color);
                color: var(--text-primary);
            }}

            .grid-content {{
                flex: 1;
                overflow-y: auto;
                overflow-x: auto;
                position: relative;
            }}

            .grid-scroll-area {{
                display: inline-block;
                min-width: 100%;
            }}

            .channels-column {{
                display: none;
            }}

            .grid-row {{
                display: flex;
                border-bottom: 1px solid var(--border-color);
                min-height: 80px;
            }}

            .channel-info {{
                width: 200px;
                flex-shrink: 0;
                padding: 10px;
                display: flex;
                align-items: center;
                gap: 8px;
                background: var(--channel-bg);
                position: sticky;
                left: 0;
                z-index: 10;
            }}

            .channel-num {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 4px 10px;
                border-radius: 12px;
                font-weight: 700;
                font-size: 0.85em;
                min-width: 35px;
                text-align: center;
                flex-shrink: 0;
            }}

            .grid-channel-logo {{
                max-width: 50px;
                max-height: 35px;
                object-fit: contain;
                flex-shrink: 0;
            }}

            .channel-name-grid {{
                font-size: 0.9em;
                font-weight: 500;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}

            .program-timeline {{
                position: relative;
                min-width: {total_width}px;
                height: 80px;
            }}

            .program-block {{
                position: absolute;
                top: 4px;
                height: 72px;
                background: var(--program-bg);
                border: 1px solid var(--border-color);
                border-radius: 6px;
                padding: 8px;
                overflow: hidden;
                cursor: pointer;
                transition: all 0.2s ease;
            }}

            .program-block:hover {{
                background: var(--program-hover);
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(0,0,0,0.4);
                z-index: 10;
            }}

            .program-block.no-data {{
                background: transparent;
                border: 1px dashed var(--border-color);
                color: var(--text-secondary);
                display: flex;
                align-items: center;
                justify-content: center;
                font-style: italic;
                font-size: 0.85em;
                cursor: default;
            }}

            .program-block.no-data:hover {{
                transform: none;
                box-shadow: none;
            }}

            .program-block-title {{
                font-weight: 600;
                font-size: 0.9em;
                color: var(--text-primary);
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
                margin-bottom: 4px;
            }}

            .program-block-subtitle {{
                font-size: 0.8em;
                color: var(--text-secondary);
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}

            .footer {{
                background: var(--bg-secondary);
                padding: 10px 20px;
                text-align: center;
                font-size: 0.85em;
                color: var(--text-secondary);
                border-top: 2px solid var(--border-color);
            }}

            .grid-content {{
                scrollbar-width: thin;
                scrollbar-color: #667eea var(--bg-tertiary);
            }}

            .grid-content::-webkit-scrollbar {{
                height: 12px;
                width: 12px;
            }}

            .grid-content::-webkit-scrollbar-track {{
                background: var(--bg-tertiary);
            }}

            .grid-content::-webkit-scrollbar-thumb {{
                background: #667eea;
                border-radius: 6px;
            }}

            .grid-content::-webkit-scrollbar-thumb:hover {{
                background: #5568d3;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>{PAGE_TITLE} - Grid View ({hours} hours)</h1>
            <div class="date-selector">
                <label for="date-select">Date:</label>
                <select id="date-select">
                    {date_options_html}
                </select>
                <label for="hour-select">Start:</label>
                <select id="hour-select">
                    {hour_options_html}
                </select>
                <button onclick="updateGrid()">Update</button>
            </div>
            <a href="/" class="view-toggle">📋 List View</a>
        </div>

        <div class="grid-container">
            <div class="timeline-header">
                {timeline_html}
            </div>

            <div class="grid-content">
                <div class="grid-scroll-area">
                    <div class="channels-column">
                        <!-- Channel info column is part of each row -->
                    </div>
                    <div class="programs-column">
                        {rows_html}
                    </div>
                </div>
            </div>
        </div>

        <div class="footer">
            Last updated: {updated_str} | Showing {channel_count} channels
        </div>

        <script>
            // Sync scrolling between timeline and content
            const gridContent = document.querySelector('.grid-content');
            const timelineHeader = document.querySelector('.timeline-header');

            gridContent.addEventListener('scroll', (e) => {{
                timelineHeader.scrollLeft = e.target.scrollLeft;
            }});

            // Update grid with selected date and time
            function updateGrid() {{
                const dateSelect = document.getElementById('date-select');
                const hourSelect = document.getElementById('hour-select');
                const selectedDate = dateSelect.value;
                const selectedHour = hourSelect.value;

                // Get current hours parameter from URL or use default
                const urlParams = new URLSearchParams(window.location.search);
                const hours = urlParams.get('hours') || '24';

                // Get timezone offset
                const tzOffset = new Date().getTimezoneOffset();

                // Build new URL with timezone
                const newUrl = `/grid?date=${{selectedDate}}&start_hour=${{selectedHour}}&hours=${{hours}}&tz_offset=${{tzOffset}}`;
                window.location.href = newUrl;
            }}

            // Add timezone offset to URL on page load if not present
            window.addEventListener('DOMContentLoaded', function() {{
                const urlParams = new URLSearchParams(window.location.search);
                const tzOffset = new Date().getTimezoneOffset();

                if (!urlParams.has('tz_offset')) {{
                    urlParams.set('tz_offset', tzOffset);
                    const newUrl = window.location.pathname + '?' + urlParams.toString();
                    window.history.replaceState({{}}, '', newUrl);
                    // Reload to apply timezone
                    window.location.href = newUrl;
                }}
            }});

            // Auto-scroll to current time on page load
            window.addEventListener('load', function() {{
                // Get current time
                const now = new Date();
                const currentHour = now.getHours();
                const currentMinute = now.getMinutes();

                // Calculate time slots
                // Each slot is 30 minutes wide (200px per slot)
                const slotWidth = {slot_width};
                const intervalMinutes = 30;

                // Get the start time from URL or assume midnight
                const urlParams = new URLSearchParams(window.location.search);
                const dateParam = urlParams.get('date');
                const startHourParam = parseInt(urlParams.get('start_hour')) || 0;

                // Calculate hours since start of display
                let hoursSinceStart = currentHour - startHourParam;
                let minutesSinceStart = currentMinute;

                // If we have a specific date selected that's not today, don't auto-scroll
                if (dateParam && dateParam !== new Date().toISOString().split('T')[0]) {{
                    return;
                }}

                // If current time is before the start hour, don't scroll (we're showing past day)
                if (hoursSinceStart < 0) {{
                    return;
                }}

                // Calculate total minutes since start
                const totalMinutesSinceStart = (hoursSinceStart * 60) + minutesSinceStart;

                // Calculate scroll position (number of slots * slot width)
                const scrollPosition = (totalMinutesSinceStart / intervalMinutes) * slotWidth;

                // Center the current time in view (subtract half viewport width)
                const gridContent = document.querySelector('.grid-content');
                if (gridContent) {{
                    const centerOffset = gridContent.clientWidth / 2;
                    gridContent.scrollLeft = Math.max(0, scrollPosition - centerOffset);
                }}
            }});
        </script>
    </body>
    </html>
    '''

    return html


def generate_time_slots(hours: int = 6, interval_minutes: int = 30, start_date: datetime = None, start_hour: int = None) -> List[datetime]:
    """
    Generate time slots for the specified number of hours.

    Args:
        hours: Number of hours to generate slots for
        interval_minutes: Minutes per slot (default 30)
        start_date: Specific date to start from (default: current UTC time)
        start_hour: Specific hour to start from (0-23, default: current hour rounded down)
    """
    from datetime import timedelta

    if start_date is None:
        now = datetime.utcnow()
    else:
        now = start_date

    # If start_hour specified, use it; otherwise round down to nearest interval
    if start_hour is not None:
        start_time = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    else:
        minutes = (now.minute // interval_minutes) * interval_minutes
        start_time = now.replace(minute=minutes, second=0, microsecond=0)

    slots = []
    num_slots = (hours * 60) // interval_minutes

    for i in range(num_slots + 1):
        slot_time = start_time + timedelta(minutes=i * interval_minutes)
        slots.append(slot_time)

    return slots


def get_programs_in_timerange(channel: dict, programs: List[dict], start_time: datetime, end_time: datetime) -> List[dict]:
    """Get all programs for a channel within a time range."""
    tvg_id = channel.get('tvg_id')
    if not tvg_id:
        return []

    matching_programs = []

    for program in programs:
        if program.get('tvg_id') != tvg_id:
            continue

        try:
            prog_start = datetime.fromisoformat(program['start_time'].replace('Z', '+00:00'))
            prog_end = datetime.fromisoformat(program['end_time'].replace('Z', '+00:00'))

            if prog_start.tzinfo:
                prog_start = prog_start.replace(tzinfo=None)
            if prog_end.tzinfo:
                prog_end = prog_end.replace(tzinfo=None)

            # Check if program overlaps with time range
            if prog_start < end_time and prog_end > start_time:
                matching_programs.append({
                    **program,
                    'parsed_start': prog_start,
                    'parsed_end': prog_end
                })
        except (ValueError, KeyError, AttributeError):
            continue

    # Sort by start time
    matching_programs.sort(key=lambda p: p['parsed_start'])

    return matching_programs


def generate_html(channels: List[dict], groups_map: Dict[int, str], logos_map: Dict[int, dict], epg_programs: List[dict] = None) -> str:
    """Generate the HTML channel guide with optional EPG data."""

    # Default to empty list if no EPG data provided
    if epg_programs is None:
        epg_programs = []

    # Sort channels by channel_number
    sorted_channels = sorted(channels, key=lambda ch: float(ch.get('channel_number', 999999)))

    # Group channels by their group
    grouped_channels = {}
    for channel in sorted_channels:
        group_id = channel.get('channel_group_id')
        group_name = groups_map.get(group_id, 'Other Channels') if group_id else 'Other Channels'

        if group_name not in grouped_channels:
            grouped_channels[group_name] = []

        grouped_channels[group_name].append(channel)

    # Sort groups by the first channel's number in each group
    sorted_group_names = sorted(
        grouped_channels.keys(),
        key=lambda gn: float(grouped_channels[gn][0].get('channel_number', 999999)) if grouped_channels[gn] else 999999
    )

    # Generate HTML for each group
    groups_html = ""
    for group_name in sorted_group_names:
        group_channels = grouped_channels[group_name]

        rows_html = ""
        for channel in group_channels:
            # Format channel number to remove .0 for whole numbers
            raw_number = channel.get('channel_number', 'N/A')
            if raw_number != 'N/A':
                try:
                    float_num = float(raw_number)
                    if float_num == int(float_num):
                        channel_number = str(int(float_num))
                    else:
                        channel_number = str(float_num)
                except (ValueError, TypeError):
                    channel_number = str(raw_number)
            else:
                channel_number = 'N/A'

            channel_name = clean_channel_name(channel.get('name', 'Unknown Channel'))

            # Get logo if available
            logo_html = ""
            logo_id = channel.get('logo_id')
            if logo_id and logo_id in logos_map:
                logo = logos_map[logo_id]
                logo_url = logo.get('cache_url') or logo.get('url', '')
                if logo_url:
                    logo_html = f'<img src="{logo_url}" alt="{channel_name}" class="channel-logo" onerror="this.style.display=\'none\'">'

            # Get current and next EPG programs
            current_program = get_current_program_for_channel(channel, epg_programs)
            next_program = get_next_program_for_channel(channel, epg_programs)

            # Format EPG info
            epg_html = ""
            if current_program:
                program_title = current_program.get('title', 'Unknown Program')
                program_subtitle = current_program.get('sub_title', '')

                # Format time - store UTC timestamps for JavaScript conversion
                try:
                    start_time = datetime.fromisoformat(current_program['start_time'].replace('Z', '+00:00'))
                    end_time = datetime.fromisoformat(current_program['end_time'].replace('Z', '+00:00'))
                    if start_time.tzinfo:
                        start_time = start_time.replace(tzinfo=None)
                    if end_time.tzinfo:
                        end_time = end_time.replace(tzinfo=None)

                    # Store both UTC display (fallback) and ISO timestamps for JS conversion
                    time_str = f"{start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}"
                    start_iso = start_time.isoformat() + 'Z'
                    end_iso = end_time.isoformat() + 'Z'
                    time_data_attr = f'data-start="{start_iso}" data-end="{end_iso}"'
                except:
                    time_str = ""
                    time_data_attr = ""

                epg_html = f"""
                    <div class="current-program">
                        <div class="program-title">{program_title}</div>
                        {f'<div class="program-subtitle">{program_subtitle}</div>' if program_subtitle else ''}
                        {f'<div class="program-time" {time_data_attr}>{time_str}</div>' if time_str else ''}
                    </div>
                """

                # Add next program if available
                if next_program:
                    next_title = next_program.get('title', 'Unknown Program')
                    try:
                        next_start = datetime.fromisoformat(next_program['start_time'].replace('Z', '+00:00'))
                        if next_start.tzinfo:
                            next_start = next_start.replace(tzinfo=None)
                        next_time_str = next_start.strftime('%I:%M %p')
                        next_start_iso = next_start.isoformat() + 'Z'
                        epg_html += f'<div class="next-program" data-start="{next_start_iso}">Up Next: {next_title} (<span class="next-time">{next_time_str}</span>)</div>'
                    except:
                        epg_html += f'<div class="next-program">Up Next: {next_title}</div>'
            else:
                epg_html = '<div class="no-epg">No program information available</div>'

            rows_html += f"""
                <tr>
                    <td class="channel-number-cell">
                        <span class="channel-number">{channel_number}</span>
                    </td>
                    <td class="channel-logo-cell">
                        {logo_html}
                    </td>
                    <td class="channel-name">{channel_name}</td>
                    <td class="epg-info">{epg_html}</td>
                </tr>
            """

        groups_html += f"""
            <div class="channel-group">
                <h2 class="group-title">{group_name}</h2>
                <table class="channels-table">
                    <thead>
                        <tr>
                            <th>Channel</th>
                            <th></th>
                            <th>Name</th>
                            <th>Now Playing</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        """

    # Get cache info for footer
    last_updated = cache.get('last_updated')
    updated_str = last_updated.strftime('%Y-%m-%d %H:%M:%S') if last_updated else 'Unknown'

    # Build list of group names for the printable dialog
    groups_json = ','.join([f'"{name}"' for name in sorted_group_names])

    # Full HTML template
    html_template = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{PAGE_TITLE}</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}

            :root {{
                --bg-primary: #1a1a2e;
                --bg-secondary: #16213e;
                --bg-tertiary: #0f3460;
                --text-primary: #e8e8e8;
                --text-secondary: #9aa5ce;
                --text-tertiary: #7a8aa5;
                --accent-gradient-1: linear-gradient(135deg, #0f3460 0%, #16213e 100%);
                --accent-gradient-2: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                --table-bg: #0f3460;
                --table-header-bg: linear-gradient(135deg, #1a237e 0%, #0d47a1 100%);
                --border-color: #0f3460;
                --hover-bg: #1565c0;
                --logo-bg: #1a1a2e;
            }}

            body.light-mode {{
                --bg-primary: #f5f5f5;
                --bg-secondary: #ffffff;
                --bg-tertiary: #e0e0e0;
                --text-primary: #212121;
                --text-secondary: #424242;
                --text-tertiary: #616161;
                --accent-gradient-1: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
                --accent-gradient-2: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                --table-bg: #fafafa;
                --table-header-bg: linear-gradient(135deg, #2196f3 0%, #1976d2 100%);
                --border-color: #e0e0e0;
                --hover-bg: #e3f2fd;
                --logo-bg: #e0e0e0;
            }}

            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                background: var(--bg-primary);
                color: var(--text-primary);
                min-height: 100vh;
                padding: 20px;
                transition: background-color 0.3s ease, color 0.3s ease;
            }}

            .header {{
                text-align: center;
                margin-bottom: 40px;
                padding: 30px;
                background: var(--accent-gradient-1);
                border-radius: 12px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                transition: background 0.3s ease;
            }}

            .header h1 {{
                font-size: 2.5em;
                font-weight: 700;
                color: var(--text-primary);
                margin-bottom: 10px;
                text-shadow: none;
            }}

            .header .channel-count {{
                font-size: 1.1em;
                color: var(--text-secondary);
                font-weight: 300;
            }}

            .channel-group {{
                background: var(--bg-secondary);
                border-radius: 12px;
                padding: 0;
                margin-bottom: 30px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                transition: background 0.3s ease;
                overflow: visible;
            }}

            .group-title {{
                font-size: 1.8em;
                font-weight: 600;
                color: var(--text-primary);
                margin: 0;
                padding: 15px 30px;
                border-bottom: 3px solid var(--border-color);
                border-radius: 12px 12px 0 0;
                position: sticky;
                top: 0;
                background: var(--bg-secondary);
                z-index: 100;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}

            .channels-table {{
                width: 100%;
                border-collapse: collapse;
                background: var(--table-bg);
                border-radius: 0 0 12px 12px;
                overflow: hidden;
                table-layout: fixed;
                transition: background 0.3s ease;
            }}

            .channels-table thead {{
                background: var(--table-header-bg);
            }}

            .channels-table thead th {{
                padding: 12px 15px;
                text-align: left;
                font-weight: 600;
                color: #ffffff;
                font-size: 0.9em;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                position: sticky;
                top: 0;
                z-index: 50;
                background: linear-gradient(135deg, #1a237e 0%, #0d47a1 100%);
                box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            }}

            .channels-table thead th:first-child {{
                width: 120px;
            }}

            .channels-table thead th:nth-child(2) {{
                width: 120px;
            }}

            body.light-mode .channels-table thead th {{
                background: linear-gradient(135deg, #2196f3 0%, #1976d2 100%);
            }}

            .channels-table tbody tr {{
                border-bottom: 1px solid #1a237e;
                transition: background-color 0.2s ease;
            }}

            .channels-table tbody tr:last-child {{
                border-bottom: none;
            }}

            .channels-table tbody tr:hover {{
                background: #1565c0;
            }}

            .channels-table td {{
                padding: 15px;
                vertical-align: middle;
            }}

            .channel-number-cell {{
                text-align: center;
                vertical-align: middle;
            }}

            .channel-number {{
                display: inline-block;
                padding: 6px 14px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 20px;
                font-weight: 700;
                font-size: 0.95em;
                color: #ffffff;
                box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
            }}

            .channel-logo-cell {{
                text-align: center;
                background: var(--logo-bg);
                border-radius: 6px;
                height: 70px;
                vertical-align: middle;
                padding: 10px !important;
                transition: background 0.3s ease;
            }}

            .channel-logo {{
                max-width: 80px;
                max-height: 50px;
                object-fit: contain;
                display: block;
                margin: 0 auto;
            }}

            .channel-name {{
                font-size: 1.05em;
                color: var(--text-primary);
                font-weight: 500;
            }}

            .epg-info {{
                color: var(--text-secondary);
                font-size: 0.95em;
                line-height: 1.5;
            }}

            .current-program {{
                margin-bottom: 8px;
            }}

            .program-title {{
                font-weight: 600;
                color: var(--text-primary);
                font-size: 1em;
                margin-bottom: 4px;
            }}

            .program-subtitle {{
                font-style: italic;
                color: var(--text-secondary);
                font-size: 0.9em;
                margin-bottom: 4px;
            }}

            .program-time {{
                font-size: 0.85em;
                color: #667eea;
                font-weight: 500;
                margin-top: 4px;
            }}

            .next-program {{
                font-size: 0.85em;
                color: var(--text-tertiary);
                padding-top: 6px;
                border-top: 1px solid var(--border-color);
                margin-top: 6px;
            }}

            .no-epg {{
                font-size: 0.85em;
                color: var(--text-tertiary);
                font-style: italic;
            }}

            .footer {{
                text-align: center;
                margin-top: 40px;
                padding: 20px;
                color: var(--text-secondary);
                font-size: 0.9em;
            }}

            .footer .cache-info {{
                margin-top: 10px;
                font-size: 0.85em;
                color: var(--text-tertiary);
            }}

            .theme-toggle {{
                position: fixed;
                top: 20px;
                right: 20px;
                background: var(--bg-secondary);
                border: 2px solid var(--border-color);
                color: var(--text-primary);
                padding: 10px 20px;
                border-radius: 50px;
                font-size: 1em;
                font-weight: 600;
                cursor: pointer;
                box-shadow: 0 4px 15px rgba(0,0,0,0.2);
                transition: all 0.3s ease;
                z-index: 1001;
            }}

            .theme-toggle:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(0,0,0,0.3);
            }}

            .print-button {{
                position: fixed;
                bottom: 30px;
                right: 30px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 15px 30px;
                border-radius: 50px;
                font-size: 1em;
                font-weight: 600;
                cursor: pointer;
                box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
                transition: transform 0.2s ease, box-shadow 0.2s ease;
                z-index: 1000;
            }}

            .print-button:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
            }}

            .modal {{
                display: none;
                position: fixed;
                z-index: 2000;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                background-color: rgba(0,0,0,0.7);
                animation: fadeIn 0.3s;
            }}

            .modal-content {{
                background: var(--bg-secondary);
                margin: 5% auto;
                padding: 30px;
                border-radius: 12px;
                width: 90%;
                max-width: 600px;
                max-height: 80vh;
                overflow-y: auto;
                box-shadow: 0 10px 50px rgba(0,0,0,0.5);
                animation: slideIn 0.3s;
                transition: background 0.3s ease;
            }}

            @keyframes fadeIn {{
                from {{ opacity: 0; }}
                to {{ opacity: 1; }}
            }}

            @keyframes slideIn {{
                from {{ transform: translateY(-50px); opacity: 0; }}
                to {{ transform: translateY(0); opacity: 1; }}
            }}

            .modal-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                padding-bottom: 15px;
                border-bottom: 2px solid #0f3460;
            }}

            .modal-header h2 {{
                color: var(--text-primary);
                margin: 0;
            }}

            .close {{
                color: var(--text-secondary);
                font-size: 28px;
                font-weight: bold;
                cursor: pointer;
                transition: color 0.2s;
            }}

            .close:hover {{
                color: var(--text-primary);
            }}

            .group-checkbox {{
                display: block;
                padding: 12px;
                margin: 8px 0;
                background: var(--table-bg);
                border-radius: 8px;
                cursor: pointer;
                transition: background 0.2s;
            }}

            .group-checkbox:hover {{
                background: var(--hover-bg);
            }}

            .group-checkbox input {{
                margin-right: 10px;
                cursor: pointer;
            }}

            .group-checkbox label {{
                cursor: pointer;
                color: var(--text-primary);
            }}

            .modal-buttons {{
                display: flex;
                gap: 15px;
                margin-top: 25px;
                padding-top: 20px;
                border-top: 1px solid #0f3460;
            }}

            .modal-button {{
                flex: 1;
                padding: 12px 20px;
                border: none;
                border-radius: 8px;
                font-size: 1em;
                font-weight: 600;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
            }}

            .select-all-btn {{
                background: #0f3460;
                color: #ffffff;
            }}

            .select-all-btn:hover {{
                background: #1565c0;
                transform: translateY(-2px);
            }}

            .print-btn {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
            }}

            .print-btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
            }}

            @media print {{
                @page {{
                    size: landscape;
                    margin: 0.4in 0.5in;
                }}

                * {{
                    -webkit-print-color-adjust: exact !important;
                    print-color-adjust: exact !important;
                }}

                body {{
                    background: white;
                    color: black;
                    padding: 0;
                    margin: 0;
                    font-family: Arial, sans-serif;
                    font-size: 8pt;
                    line-height: 1.3;
                    column-count: 4;
                    column-gap: 10px;
                }}

                .print-button, .modal, .footer {{
                    display: none !important;
                }}

                .header {{
                    column-span: all;
                    text-align: center;
                    background: white !important;
                    border-bottom: 2px solid #000;
                    padding: 3px 0 4px 0;
                    margin: 0 0 8px 0;
                }}

                .header h1 {{
                    color: black !important;
                    font-size: 16pt;
                    font-weight: bold;
                    margin: 0;
                    line-height: 1.1;
                }}

                .header .channel-count {{
                    color: black !important;
                    font-size: 8pt;
                    margin: 2px 0 0 0;
                    line-height: 1;
                }}

                .channel-group {{
                    break-inside: avoid;
                    page-break-inside: avoid;
                    -webkit-column-break-inside: avoid;
                    margin: 0 0 8px 0;
                    padding: 6px 8px;
                    display: block;
                    width: 100%;
                    box-sizing: border-box;
                    border: 1.5px solid #333;
                    border-radius: 8px;
                    background: white !important;
                }}

                .group-title {{
                    font-size: 9pt;
                    font-weight: bold;
                    margin: 0 0 4px 0;
                    padding: 0 0 3px 0;
                    line-height: 1.1;
                    color: black !important;
                    background: white !important;
                    border: none;
                    border-bottom: 2px solid #000;
                }}

                .channels-table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin: 0;
                    padding: 0;
                    background: white !important;
                    display: block;
                }}

                .channels-table thead {{
                    display: none;
                }}

                .channels-table tbody {{
                    background: white !important;
                    display: block;
                }}

                .channels-table tbody tr {{
                    border: none !important;
                    background: white !important;
                    display: block;
                    margin: 0;
                    padding: 1px 0;
                    line-height: 1.3;
                }}

                .channels-table tbody tr:hover {{
                    background: white !important;
                }}

                .channels-table td {{
                    color: black !important;
                    padding: 0 !important;
                    vertical-align: baseline;
                    border: none !important;
                    display: inline;
                }}

                .channel-number-cell {{
                    display: inline;
                    width: auto;
                }}

                .channel-number {{
                    background: transparent !important;
                    color: black !important;
                    font-weight: bold;
                    font-size: 8pt;
                    border: none !important;
                    box-shadow: none !important;
                    padding: 0 !important;
                    margin: 0 !important;
                    display: inline;
                    border-radius: 0;
                    min-width: 0;
                }}

                .channel-logo-cell {{
                    display: none !important;
                }}

                .channel-name {{
                    font-size: 8pt !important;
                    color: black !important;
                    line-height: 1.3;
                    font-weight: normal;
                    display: inline;
                    margin-left: 4px;
                }}

                .channels-table tbody tr::after {{
                    content: "";
                    display: block;
                }}
            }}

            @media (max-width: 768px) {{
                .header h1 {{
                    font-size: 1.8em;
                }}

                .group-title {{
                    font-size: 1.4em;
                }}

                .channel-logo {{
                    max-width: 60px;
                    max-height: 40px;
                }}
            }}
        </style>
    </head>
    <body>
        <!-- Theme Toggle Button -->
        <button class="theme-toggle" onclick="toggleTheme()">🌙 Dark / ☀️ Light</button>

        <div class="header">
            <h1>{PAGE_TITLE}</h1>
            <p class="channel-count">{len(sorted_channels)} channels available</p>
        </div>

        {groups_html}

        <div class="footer">
            <p>Generated from Dispatcharr API</p>
            <p class="cache-info">Last updated: {updated_str}</p>
        </div>

        <!-- View Buttons -->
        <a href="/grid" class="grid-button" style="position: fixed; bottom: 90px; right: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px 25px; border-radius: 50px; font-size: 1.1em; font-weight: 600; cursor: pointer; box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3); transition: all 0.3s ease; z-index: 1000; text-decoration: none; display: inline-block;">📺 Grid View</a>
        <button class="print-button" onclick="openPrintDialog()">📄 Printable Guide</button>

        <!-- Print Dialog Modal -->
        <div id="printModal" class="modal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2>Select Channel Groups to Print</h2>
                    <span class="close" onclick="closePrintDialog()">&times;</span>
                </div>
                <div id="groupCheckboxes">
                    <!-- Group checkboxes with format selectors will be generated by JavaScript -->
                </div>
                <div class="modal-buttons">
                    <button class="modal-button select-all-btn" onclick="toggleAllGroups()">Select All / None</button>
                    <button class="modal-button print-btn" onclick="printSelected()">Print Selected</button>
                </div>
            </div>
        </div>

        <script>
            const allGroups = [{groups_json}];
            let selectedGroups = new Set(allGroups);
            let groupModes = {{}};  // Track detailed/summary per group

            // Initialize all groups to 'detailed' mode by default
            allGroups.forEach(group => {{
                groupModes[group] = 'detailed';
            }});

            // Initialize checkboxes with format selectors
            function initializeCheckboxes() {{
                const container = document.getElementById('groupCheckboxes');
                container.innerHTML = '';

                allGroups.forEach(group => {{
                    const groupId = 'group_' + group.replace(/[^a-zA-Z0-9]/g, '_');

                    const div = document.createElement('div');
                    div.className = 'group-checkbox';
                    div.style.cssText = 'display: flex; align-items: center; padding: 8px; border-bottom: 1px solid #ddd;';

                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.id = groupId;
                    checkbox.value = group;
                    checkbox.checked = true;
                    checkbox.style.cssText = 'margin-right: 10px; cursor: pointer;';
                    checkbox.onchange = function() {{
                        if (this.checked) {{
                            selectedGroups.add(group);
                        }} else {{
                            selectedGroups.delete(group);
                        }}
                    }};

                    const label = document.createElement('label');
                    label.htmlFor = groupId;
                    label.textContent = group;
                    label.style.cssText = 'flex: 1; cursor: pointer; font-weight: 500;';
                    label.onclick = function() {{
                        checkbox.checked = !checkbox.checked;
                        checkbox.onchange();
                    }};

                    // Format selector buttons
                    const formatDiv = document.createElement('div');
                    formatDiv.style.cssText = 'display: flex; gap: 5px;';

                    const detailedBtn = document.createElement('button');
                    detailedBtn.textContent = 'Detailed';
                    detailedBtn.className = 'format-btn';
                    detailedBtn.style.cssText = 'padding: 4px 12px; border: 2px solid #4A90E2; border-radius: 4px; cursor: pointer; font-size: 11px; font-weight: bold; background: #4A90E2; color: white;';
                    detailedBtn.onclick = function(e) {{
                        e.stopPropagation();
                        groupModes[group] = 'detailed';
                        detailedBtn.style.background = '#4A90E2';
                        detailedBtn.style.color = 'white';
                        summaryBtn.style.background = 'white';
                        summaryBtn.style.color = '#E67E22';
                    }};

                    const summaryBtn = document.createElement('button');
                    summaryBtn.textContent = 'Summary';
                    summaryBtn.className = 'format-btn';
                    summaryBtn.style.cssText = 'padding: 4px 12px; border: 2px solid #E67E22; border-radius: 4px; cursor: pointer; font-size: 11px; font-weight: bold; background: white; color: #E67E22;';
                    summaryBtn.onclick = function(e) {{
                        e.stopPropagation();
                        groupModes[group] = 'summary';
                        summaryBtn.style.background = '#E67E22';
                        summaryBtn.style.color = 'white';
                        detailedBtn.style.background = 'white';
                        detailedBtn.style.color = '#4A90E2';
                    }};

                    formatDiv.appendChild(detailedBtn);
                    formatDiv.appendChild(summaryBtn);

                    div.appendChild(checkbox);
                    div.appendChild(label);
                    div.appendChild(formatDiv);

                    container.appendChild(div);
                }});
            }}

            function openPrintDialog() {{
                initializeCheckboxes();
                document.getElementById('printModal').style.display = 'block';
            }}

            function closePrintDialog() {{
                document.getElementById('printModal').style.display = 'none';
            }}

            function toggleAllGroups() {{
                const checkboxes = document.querySelectorAll('#groupCheckboxes input[type="checkbox"]');
                const allChecked = Array.from(checkboxes).every(cb => cb.checked);

                checkboxes.forEach(cb => {{
                    cb.checked = !allChecked;
                    if (cb.checked) {{
                        selectedGroups.add(cb.value);
                    }} else {{
                        selectedGroups.delete(cb.value);
                    }}
                }});
            }}

            function printSelected() {{
                // Build list of selected groups (groups to include)
                const includedGroups = Array.from(selectedGroups);

                if (includedGroups.length === 0) {{
                    alert('Please select at least one channel group to print.');
                    return;
                }}

                // Build modes parameter (group:mode,group:mode,...)
                const modesParam = includedGroups.map(group => {{
                    const mode = groupModes[group] || 'detailed';
                    return `${{encodeURIComponent(group)}}:${{mode}}`;
                }}).join(',');

                // Open print page in new window
                const printUrl = `/print?modes=${{modesParam}}`;
                window.open(printUrl, '_blank');

                // Close modal
                closePrintDialog();
            }}

            // Close modal when clicking outside
            window.onclick = function(event) {{
                const modal = document.getElementById('printModal');
                if (event.target === modal) {{
                    closePrintDialog();
                }}
            }}

            // Theme toggle function
            function toggleTheme() {{
                document.body.classList.toggle('light-mode');
                // Save theme preference to localStorage
                if (document.body.classList.contains('light-mode')) {{
                    localStorage.setItem('theme', 'light');
                }} else {{
                    localStorage.setItem('theme', 'dark');
                }}
            }}

            // Load saved theme preference on page load
            window.addEventListener('DOMContentLoaded', function() {{
                const savedTheme = localStorage.getItem('theme');
                if (savedTheme === 'light') {{
                    document.body.classList.add('light-mode');
                }}

                // Convert UTC times to local timezone
                convertTimesToLocal();
            }});

            function convertTimesToLocal() {{
                // Convert program times
                document.querySelectorAll('.program-time[data-start]').forEach(function(elem) {{
                    const startUTC = elem.getAttribute('data-start');
                    const endUTC = elem.getAttribute('data-end');

                    if (startUTC && endUTC) {{
                        const startDate = new Date(startUTC);
                        const endDate = new Date(endUTC);

                        const startTime = startDate.toLocaleTimeString('en-US', {{
                            hour: 'numeric',
                            minute: '2-digit',
                            hour12: true
                        }});
                        const endTime = endDate.toLocaleTimeString('en-US', {{
                            hour: 'numeric',
                            minute: '2-digit',
                            hour12: true
                        }});

                        elem.textContent = startTime + ' - ' + endTime;
                    }}
                }});

                // Convert next program times
                document.querySelectorAll('.next-program[data-start] .next-time').forEach(function(elem) {{
                    const parentElem = elem.closest('.next-program');
                    const startUTC = parentElem.getAttribute('data-start');

                    if (startUTC) {{
                        const startDate = new Date(startUTC);
                        const startTime = startDate.toLocaleTimeString('en-US', {{
                            hour: 'numeric',
                            minute: '2-digit',
                            hour12: true
                        }});
                        elem.textContent = startTime;
                    }}
                }});
            }}
        </script>
    </body>
    </html>
    """

    return html_template


def refresh_cache():
    """Fetch data from Dispatcharr API and update the cache."""
    print(f"[{datetime.now()}] Starting cache refresh...")

    with cache['lock']:
        try:
            # Authenticate
            access_token = get_access_token()

            # Fetch channel groups
            groups_map = get_channel_groups(access_token)
            print(f"[{datetime.now()}] Fetched {len(groups_map)} channel groups")

            # Fetch logos
            logos_map = get_logos(access_token)
            print(f"[{datetime.now()}] Fetched {len(logos_map)} logos")

            # Fetch EPG data - try to get from midnight today to end of tomorrow
            epg_programs = []
            try:
                # Calculate time range: midnight today to midnight tomorrow (24+ hours)
                now_utc = datetime.utcnow()
                midnight_today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                end_tomorrow = midnight_today + timedelta(hours=48)  # 2 full days

                # Try date range fetch first, fall back to grid endpoint
                epg_programs = get_epg_programs_by_date_range(access_token, midnight_today, end_tomorrow)
                print(f"[{datetime.now()}] Fetched {len(epg_programs)} EPG programs")
            except Exception as e:
                print(f"[{datetime.now()}] Warning: Failed to fetch EPG data: {str(e)}")
                # Continue without EPG data

            # Fetch channels
            channels = get_channels(access_token)
            print(f"[{datetime.now()}] Fetched {len(channels)} channels")

            # Filter by channel profile if specified
            if CHANNEL_PROFILE_NAME:
                profile = get_channel_profile_by_name(access_token, CHANNEL_PROFILE_NAME)
                if profile:
                    profile_channel_ids = get_channel_ids_from_profile(profile)
                    if profile_channel_ids:
                        channels = [ch for ch in channels if ch.get('id') in profile_channel_ids]
                        print(f"[{datetime.now()}] Filtered to {len(channels)} channels for profile '{CHANNEL_PROFILE_NAME}'")

            # Exclude channel groups if specified
            if EXCLUDE_CHANNEL_GROUPS:
                exclude_names = [name.strip() for name in EXCLUDE_CHANNEL_GROUPS.split(',') if name.strip()]
                if exclude_names:
                    # Find group IDs to exclude
                    exclude_group_ids = []
                    for group_id, group_name in groups_map.items():
                        if any(group_name.lower() == exclude.lower() for exclude in exclude_names):
                            exclude_group_ids.append(group_id)

                    # Filter out channels in excluded groups
                    if exclude_group_ids:
                        before_count = len(channels)
                        channels = [ch for ch in channels if ch.get('channel_group_id') not in exclude_group_ids]
                        print(f"[{datetime.now()}] Excluded {before_count - len(channels)} channels, {len(channels)} remaining")

            # Generate and cache HTML
            html = generate_html(channels, groups_map, logos_map, epg_programs)
            cache['html'] = html
            cache['channels'] = channels  # Store raw channel data
            cache['groups_map'] = groups_map  # Store groups map
            cache['logos_map'] = logos_map  # Store logos map
            cache['epg_programs'] = epg_programs  # Store EPG data
            cache['last_updated'] = datetime.now()
            cache['error'] = None

            print(f"[{datetime.now()}] Cache refresh complete!")

        except Exception as e:
            error_msg = f"Cache refresh failed: {str(e)}"
            print(f"[{datetime.now()}] ERROR: {error_msg}")
            cache['error'] = error_msg

            # If we don't have cached HTML yet, create an error page
            if cache['html'] is None:
                cache['html'] = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Error</title>
                    <style>
                        body {{
                            font-family: Arial, sans-serif;
                            background: #1a1a2e;
                            color: #e8e8e8;
                            padding: 40px;
                            text-align: center;
                        }}
                        .error {{
                            background: #16213e;
                            padding: 30px;
                            border-radius: 12px;
                            max-width: 600px;
                            margin: 0 auto;
                        }}
                        h1 {{ color: #ff6b6b; }}
                        pre {{
                            background: #0f3460;
                            padding: 20px;
                            border-radius: 8px;
                            text-align: left;
                            overflow-x: auto;
                        }}
                    </style>
                </head>
                <body>
                    <div class="error">
                        <h1>Error Loading Channel Guide</h1>
                        <pre>{error_msg}</pre>
                        <p style="margin-top: 20px;">The cache will retry on the next scheduled refresh.</p>
                    </div>
                </body>
                </html>
                """


@app.route('/')
def index():
    """Main route that serves the cached channel guide."""
    return cache['html'] or "Cache is loading, please refresh in a moment..."


@app.route('/health')
def health():
    """Health check endpoint."""
    status = {
        'status': 'healthy',
        'cache_populated': cache['html'] is not None,
        'last_updated': cache['last_updated'].isoformat() if cache['last_updated'] else None,
        'error': cache['error']
    }
    return jsonify(status), 200


@app.route('/refresh')
def manual_refresh():
    """Manual cache refresh endpoint."""
    refresh_cache()
    return jsonify({
        'status': 'refreshed',
        'last_updated': cache['last_updated'].isoformat() if cache['last_updated'] else None
    }), 200


@app.route('/debug/timezone')
def debug_timezone():
    """Debug endpoint to check timezone offset."""
    from flask import request
    from datetime import timedelta

    tz_offset_minutes = int(request.args.get('tz_offset', '0'))
    now_utc = datetime.utcnow()
    now_local = now_utc - timedelta(minutes=tz_offset_minutes)
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_utc = midnight_local + timedelta(minutes=tz_offset_minutes)

    return jsonify({
        'tz_offset_minutes': tz_offset_minutes,
        'now_utc': now_utc.isoformat(),
        'now_local': now_local.isoformat(),
        'midnight_local': midnight_local.isoformat(),
        'midnight_utc': midnight_utc.isoformat(),
        'first_display_time': (midnight_utc - timedelta(minutes=tz_offset_minutes)).strftime('%I:%M %p')
    }), 200


@app.route('/grid')
def grid_view():
    """Generate a scrollable grid view with timeline."""
    from datetime import timedelta
    from flask import request

    # Check if cache is populated
    if cache['channels'] is None or cache['groups_map'] is None:
        return "Cache is loading, please try again in a moment...", 503

    channels = cache['channels']
    groups_map = cache['groups_map']
    logos_map = cache.get('logos_map', {})
    epg_programs = cache.get('epg_programs', [])

    # Get parameters
    hours = int(request.args.get('hours', 24))
    hours = min(max(hours, 2), 24)  # Limit between 2-24 hours

    # Get timezone offset from browser (in minutes, e.g., -300 for UTC-5)
    tz_offset_param = request.args.get('tz_offset', '0')
    try:
        tz_offset_minutes = int(tz_offset_param)
    except ValueError:
        tz_offset_minutes = 0

    # Get date parameter (format: YYYY-MM-DD)
    date_param = request.args.get('date', '')
    start_hour_param = request.args.get('start_hour', '0')

    selected_date = None
    start_hour = 0

    if date_param:
        try:
            # Parse the date
            selected_date = datetime.strptime(date_param, '%Y-%m-%d')
            start_hour = int(start_hour_param)
            start_hour = min(max(start_hour, 0), 23)
        except ValueError:
            # Invalid date format, use current time
            selected_date = None
            start_hour = 0

    # Generate time slots (in UTC, will convert for display)
    # Note: getTimezoneOffset() returns positive values for zones west of UTC
    # e.g., CST (UTC-6) returns 360, so we ADD to local time to get UTC
    if selected_date:
        # If specific date selected, apply the start_hour and convert to UTC
        # selected_date is parsed as naive datetime (e.g., 2025-12-05 00:00:00)
        # We need to apply start_hour in local time, then convert to UTC

        # Apply the selected start hour to the selected date (in local time)
        local_start = selected_date.replace(hour=start_hour, minute=0, second=0, microsecond=0)

        # Convert to UTC by adding the offset
        # For CST: local 00:00 + 360min = 06:00 UTC
        selected_datetime_utc = local_start + timedelta(minutes=tz_offset_minutes)

        # Don't pass start_hour to generate_time_slots since we already set the correct hour
        time_slots = generate_time_slots(hours=hours, interval_minutes=30, start_date=selected_datetime_utc, start_hour=None)
    else:
        # For current day, start from local midnight
        # Step 1: Get current UTC time
        now_utc = datetime.utcnow()

        # Step 2: Convert to user's local time by subtracting offset
        # For CST (offset=360), UTC - 360min = Local time
        now_local = now_utc - timedelta(minutes=tz_offset_minutes)

        # Step 3: Get midnight in user's local time
        midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        # Step 4: Convert back to UTC by adding offset
        # For CST, midnight local + 360min = 6AM UTC (which represents midnight CST in UTC)
        midnight_utc = midnight_local + timedelta(minutes=tz_offset_minutes)

        # Don't pass start_hour since midnight_utc already has the correct hour set
        time_slots = generate_time_slots(hours=hours, interval_minutes=30, start_date=midnight_utc, start_hour=None)

    start_time = time_slots[0]
    end_time = time_slots[-1]

    # Use cached EPG data (refreshed every 30 minutes with wider time range)
    epg_programs = cache.get('epg_programs', [])

    # Sort and filter channels
    sorted_channels = sorted(channels, key=lambda ch: float(ch.get('channel_number', 999999)))

    # Filter by channel profile if specified
    if CHANNEL_PROFILE_NAME:
        # This filtering is already done in cache, but let's be safe
        pass

    # Exclude channel groups if specified
    if EXCLUDE_CHANNEL_GROUPS:
        exclude_names = [name.strip().lower() for name in EXCLUDE_CHANNEL_GROUPS.split(',') if name.strip()]
        if exclude_names:
            exclude_group_ids = [gid for gid, gname in groups_map.items() if gname.lower() in exclude_names]
            sorted_channels = [ch for ch in sorted_channels if ch.get('channel_group_id') not in exclude_group_ids]

    # Build timeline header HTML
    timeline_html = '<div class="timeline-header-spacer"></div>'  # Spacer for channel column

    for slot in time_slots:
        # Convert UTC time slot to local time for display
        local_slot = slot - timedelta(minutes=tz_offset_minutes)
        time_label = local_slot.strftime('%I:%M %p').lstrip('0')
        timeline_html += f'<div class="time-slot-header">{time_label}</div>'

    # Build channel rows
    rows_html = ""
    slot_width = 200  # pixels per 30-minute slot

    for channel in sorted_channels:
        channel_number = channel.get('channel_number', 'N/A')
        if channel_number != 'N/A':
            try:
                float_num = float(channel_number)
                channel_number = str(int(float_num)) if float_num == int(float_num) else str(float_num)
            except:
                pass

        channel_name = clean_channel_name(channel.get('name', 'Unknown'))

        # Get logo
        logo_html = ""
        logo_id = channel.get('logo_id')
        if logo_id and logo_id in logos_map:
            logo = logos_map[logo_id]
            logo_url = logo.get('cache_url') or logo.get('url', '')
            if logo_url:
                logo_html = f'<img src="{logo_url}" alt="{channel_name}" class="grid-channel-logo">'

        # Get programs for this channel in the time range
        channel_programs = get_programs_in_timerange(channel, epg_programs, start_time, end_time)

        # Build program blocks
        program_blocks = ""

        if channel_programs:
            for program in channel_programs:
                # Calculate position and width
                prog_start = max(program['parsed_start'], start_time)
                prog_end = min(program['parsed_end'], end_time)

                # Calculate offset from start time in minutes
                offset_minutes = (prog_start - start_time).total_seconds() / 60
                duration_minutes = (prog_end - prog_start).total_seconds() / 60

                # Convert to pixels
                left_px = (offset_minutes / 30) * slot_width
                width_px = (duration_minutes / 30) * slot_width

                title = program.get('title', 'Unknown')
                subtitle = program.get('sub_title', '')

                program_blocks += f'''
                    <div class="program-block" style="left: {left_px}px; width: {width_px}px;" title="{title}{' - ' + subtitle if subtitle else ''}">
                        <div class="program-block-title">{title}</div>
                        {f'<div class="program-block-subtitle">{subtitle}</div>' if subtitle else ''}
                    </div>
                '''
        else:
            # No EPG data
            total_width = len(time_slots) * slot_width
            program_blocks = f'<div class="program-block no-data" style="left: 0; width: {total_width}px;">No program data</div>'

        rows_html += f'''
            <div class="grid-row">
                <div class="channel-info">
                    <div class="channel-num">{channel_number}</div>
                    {logo_html}
                    <div class="channel-name-grid">{channel_name}</div>
                </div>
                <div class="program-timeline">
                    {program_blocks}
                </div>
            </div>
        '''

    # Generate full HTML
    selected_date_str = selected_date.strftime('%Y-%m-%d') if selected_date else ''
    grid_html = generate_grid_html(timeline_html, rows_html, hours, len(time_slots), slot_width, selected_date_str, start_hour, len(sorted_channels))

    return grid_html


@app.route('/print')
def print_guide():
    """Generate a print-optimized HTML page."""
    from flask import request

    # Get per-group modes from query parameter (format: "group1:detailed,group2:summary,...")
    modes_param = request.args.get('modes', '')
    group_modes = {}  # Map of group_name -> 'detailed' or 'summary'

    if modes_param:
        for item in modes_param.split(','):
            if ':' in item:
                group_name, mode = item.rsplit(':', 1)
                from urllib.parse import unquote
                group_modes[unquote(group_name)] = mode

    try:
        # Get data from cache
        if cache['channels'] is None or cache['groups_map'] is None:
            return "Cache is loading, please try again in a moment...", 503

        channels = cache['channels'].copy()  # Make a copy so we don't modify the cache
        groups_map = cache['groups_map']

        # Filter to only include groups that were selected (those in group_modes)
        selected_group_names = set(group_modes.keys())
        if selected_group_names:
            included_group_ids = [gid for gid, gname in groups_map.items() if gname in selected_group_names]
            channels = [ch for ch in channels if ch.get('channel_group_id') in included_group_ids]

        # Sort ALL channels by channel_number globally (not by group)
        sorted_channels = sorted(channels, key=lambda x: float(x.get('channel_number', 999999)))

        # Define color palette for group headers
        group_colors = [
            ('#4A90E2', '#E8F2FC'),  # Blue
            ('#50C878', '#E8F8F0'),  # Emerald
            ('#9B59B6', '#F4ECF7'),  # Purple
            ('#E67E22', '#FDF2E9'),  # Orange
            ('#16A085', '#E8F6F3'),  # Teal
            ('#C0392B', '#FADBD8'),  # Red
            ('#F39C12', '#FEF5E7'),  # Yellow
            ('#2C3E50', '#EAF2F8'),  # Navy
            ('#D35400', '#FBEEE6'),  # Pumpkin
            ('#8E44AD', '#F5EEF8'),  # Violet
        ]

        # Group channels by group name first
        grouped_channels = {}
        for channel in sorted_channels:
            group_id = channel.get('channel_group_id')
            group_name = groups_map.get(group_id, 'Other') if group_id else 'Other'
            if group_name not in grouped_channels:
                grouped_channels[group_name] = []
            grouped_channels[group_name].append(channel)

        # Sort groups by first channel number
        sorted_group_names = sorted(
            grouped_channels.keys(),
            key=lambda gn: float(grouped_channels[gn][0].get('channel_number', 999999)) if grouped_channels[gn] else 999999
        )

        # Build HTML with per-group modes
        groups_html = ""
        group_index = 0

        for group_name in sorted_group_names:
            group_channels = grouped_channels[group_name]
            if not group_channels:
                continue

            # Get mode for this specific group
            mode = group_modes.get(group_name, 'detailed')

            # Assign color
            header_color, bg_color = group_colors[group_index % len(group_colors)]
            group_index += 1

            if mode == 'summary':
                # Summary mode for this group: show header with channel range
                first_ch = group_channels[0].get('channel_number', 'N/A')
                last_ch = group_channels[-1].get('channel_number', 'N/A')

                # Format numbers
                def format_number(raw):
                    if raw != 'N/A':
                        try:
                            float_num = float(raw)
                            return str(int(float_num)) if float_num == int(float_num) else str(float_num)
                        except:
                            return str(raw)
                    return 'N/A'

                first_num = format_number(first_ch)
                last_num = format_number(last_ch)
                range_text = f"{first_num} - {last_num}" if first_num != last_num else first_num
                channel_count = len(group_channels)

                groups_html += f"""        <div class="channel-group summary-mode" data-group="{group_name}" style="background: {bg_color};">
            <div class="group-title" style="background: {header_color}; color: #fff;">{group_name}</div>
            <div class="channel-list">
                <div class='channel-line'><span class='ch-num'>{range_text}</span> ({channel_count} channels)</div>
            </div>
        </div>
"""
            else:
                # Detailed mode for this group: show all channels
                groups_html += f"""        <div class="channel-group" data-group="{group_name}" style="background: {bg_color};">
            <div class="group-title" style="background: {header_color}; color: #fff;">{group_name}</div>
            <div class="channel-list">
"""
                for channel in group_channels:
                    # Format channel number to remove .0 for whole numbers
                    raw_number = channel.get('channel_number', 'N/A')
                    if raw_number != 'N/A':
                        try:
                            float_num = float(raw_number)
                            if float_num == int(float_num):
                                number = str(int(float_num))
                            else:
                                number = str(float_num)
                        except:
                            number = str(raw_number)
                    else:
                        number = 'N/A'

                    name = clean_channel_name(channel.get('name', 'Unknown'))
                    groups_html += f"                <div class='channel-line' data-group='{group_name}'><span class='ch-num'>{number}</span> {name}</div>\n"

                groups_html += "            </div>\n        </div>\n"

        # Get current date
        updated_str = datetime.now().strftime('%m/%d/%Y')

        # Generate print HTML
        print_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{PAGE_TITLE}</title>
    <style>
        @page {{
            size: 11in 8.5in; /* Standard letter size, landscape */
            margin: 0.3in 0.4in;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
        }}

        body {{
            font-family: Arial, sans-serif;
            font-size: 6pt;
            line-height: 1.15;
            color: #000;
            background: #fff;
            column-count: 5;
            column-gap: 10px;
            column-fill: auto;
            max-height: 7.9in; /* 8.5in - 0.6in margin */
            overflow: visible;
        }}

        .header {{
            column-span: all;
            text-align: center;
            border-bottom: 1.5px solid #000;
            padding-bottom: 3px;
            margin-bottom: 6px;
        }}

        .header h1 {{
            font-size: 14pt;
            font-weight: bold;
            margin: 0 0 2px 0;
            letter-spacing: 0.5px;
        }}

        .header .date {{
            font-size: 7pt;
            margin: 0;
            color: #333;
        }}

        .channel-group {{
            /* Allow groups to break across columns for better flow */
            break-inside: auto;
            page-break-inside: auto;
            border: 1px solid #999;
            border-radius: 2px;
            padding: 3px 4px;
            margin-bottom: 4px;
            /* Background color set via inline styles */
        }}

        .group-title {{
            font-size: 7pt;
            font-weight: bold;
            border-bottom: none;
            padding: 2px 4px;
            margin: -3px -4px 2px -4px;
            break-after: avoid;
            /* Background and text colors set via inline styles */
        }}

        .group-title.continuation {{
            opacity: 0.85;
            font-style: italic;
        }}

        .channel-list {{
            /* Simple list container */
        }}

        .channel-line {{
            margin: 0;
            padding: 0.5px 0;
            line-height: 1.2;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            break-inside: avoid;
        }}

        /* Add continuation header when channels appear at the top of a new column */
        .channel-list > .channel-line:first-child::before {{
            content: attr(data-group) " (cont.)";
            display: block;
            font-size: 6.5pt;
            font-weight: bold;
            font-style: italic;
            color: #fff;
            background: rgba(0, 0, 0, 0.6);
            padding: 2px 4px;
            margin: -4px -4px 2px -4px;
            white-space: normal;
        }}

        /* But don't show it if it's right after the group title (not a continuation) */
        .group-title + .channel-list > .channel-line:first-child::before {{
            display: none;
        }}

        .ch-num {{
            font-weight: bold;
            display: inline-block;
            min-width: 28px;
            color: #000;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{PAGE_TITLE}</h1>
        <div class="date">{len(sorted_channels)} channels available</div>
    </div>

{groups_html}

<script>
    // Automatically trigger print dialog when page loads
    window.addEventListener('load', function() {{
        setTimeout(function() {{
            window.print();
        }}, 500); // Small delay to ensure page is fully rendered
    }});
</script>
</body>
</html>"""

        return print_html

    except Exception as e:
        return f"Error generating print guide: {str(e)}", 500


# Initialize scheduler
scheduler = BackgroundScheduler()


def start_scheduler():
    """Start the background scheduler for cache refresh."""
    try:
        # Parse cron expression
        cron_parts = CACHE_REFRESH_CRON.split()
        if len(cron_parts) == 5:
            minute, hour, day, month, day_of_week = cron_parts

            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week
            )

            scheduler.add_job(refresh_cache, trigger)
            scheduler.start()

            print(f"[{datetime.now()}] Scheduler started with cron: {CACHE_REFRESH_CRON}")
        else:
            print(f"[{datetime.now()}] WARNING: Invalid cron expression '{CACHE_REFRESH_CRON}'. Using default: every 6 hours")
            scheduler.add_job(refresh_cache, 'interval', hours=6)
            scheduler.start()

    except Exception as e:
        print(f"[{datetime.now()}] ERROR starting scheduler: {e}")
        print(f"[{datetime.now()}] Falling back to 6-hour interval")
        scheduler.add_job(refresh_cache, 'interval', hours=6)
        scheduler.start()


# Initialize cache and scheduler on module load (works with both direct run and gunicorn)
print(f"[{datetime.now()}] Performing initial cache load...")
refresh_cache()

# Start the scheduler
start_scheduler()

if __name__ == '__main__':
    # Run the Flask app directly (for development)
    app.run(host='0.0.0.0', port=5000)
