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


def clean_channel_name(name: str) -> str:
    """Remove channel number prefix from channel name (e.g., '2.1 | ABC News' -> 'ABC News')."""
    if not name:
        return "Unknown Channel"

    # Remove pattern like "2.1 | " or "102 | "
    cleaned = re.sub(r'^\d+(\.\d+)?\s*\|\s*', '', name)
    return cleaned if cleaned else name


def generate_html(channels: List[dict], groups_map: Dict[int, str], logos_map: Dict[int, dict]) -> str:
    """Generate the HTML channel guide."""

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

            rows_html += f"""
                <tr>
                    <td class="channel-number-cell">
                        <span class="channel-number">{channel_number}</span>
                    </td>
                    <td class="channel-logo-cell">
                        {logo_html}
                    </td>
                    <td class="channel-name">{channel_name}</td>
                </tr>
            """

        groups_html += f"""
            <div class="channel-group">
                <h2 class="group-title">{group_name}</h2>
                <table class="channels-table">
                    <thead>
                        <tr>
                            <th>Channel</th>
                            <th>Logo</th>
                            <th>Name</th>
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

            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                background: #1a1a2e;
                color: #e8e8e8;
                min-height: 100vh;
                padding: 20px;
            }}

            .header {{
                text-align: center;
                margin-bottom: 40px;
                padding: 30px;
                background: linear-gradient(135deg, #0f3460 0%, #16213e 100%);
                border-radius: 12px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.4);
            }}

            .header h1 {{
                font-size: 2.5em;
                font-weight: 700;
                color: #ffffff;
                margin-bottom: 10px;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
            }}

            .header .channel-count {{
                font-size: 1.1em;
                color: #9aa5ce;
                font-weight: 300;
            }}

            .channel-group {{
                background: #16213e;
                border-radius: 12px;
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.4);
            }}

            .group-title {{
                font-size: 1.8em;
                font-weight: 600;
                color: #ffffff;
                margin-bottom: 20px;
                padding-bottom: 10px;
                border-bottom: 3px solid #0f3460;
            }}

            .channels-table {{
                width: 100%;
                border-collapse: collapse;
                background: #0f3460;
                border-radius: 8px;
                overflow: hidden;
                table-layout: fixed;
            }}

            .channels-table thead {{
                background: linear-gradient(135deg, #1a237e 0%, #0d47a1 100%);
            }}

            .channels-table thead th {{
                padding: 15px;
                text-align: left;
                font-weight: 600;
                color: #ffffff;
                font-size: 0.95em;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}

            .channels-table thead th:first-child {{
                width: 120px;
            }}

            .channels-table thead th:nth-child(2) {{
                width: 120px;
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
                background: #1a1a2e;
                border-radius: 6px;
                height: 70px;
                vertical-align: middle;
                padding: 10px !important;
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
                color: #e8e8e8;
                font-weight: 500;
            }}

            .footer {{
                text-align: center;
                margin-top: 40px;
                padding: 20px;
                color: #9aa5ce;
                font-size: 0.9em;
            }}

            .footer .cache-info {{
                margin-top: 10px;
                font-size: 0.85em;
                color: #7a8aa5;
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
                background: #16213e;
                margin: 5% auto;
                padding: 30px;
                border-radius: 12px;
                width: 90%;
                max-width: 600px;
                max-height: 80vh;
                overflow-y: auto;
                box-shadow: 0 10px 50px rgba(0,0,0,0.5);
                animation: slideIn 0.3s;
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
                color: #ffffff;
                margin: 0;
            }}

            .close {{
                color: #9aa5ce;
                font-size: 28px;
                font-weight: bold;
                cursor: pointer;
                transition: color 0.2s;
            }}

            .close:hover {{
                color: #ffffff;
            }}

            .group-checkbox {{
                display: block;
                padding: 12px;
                margin: 8px 0;
                background: #0f3460;
                border-radius: 8px;
                cursor: pointer;
                transition: background 0.2s;
            }}

            .group-checkbox:hover {{
                background: #1565c0;
            }}

            .group-checkbox input {{
                margin-right: 10px;
                cursor: pointer;
            }}

            .group-checkbox label {{
                cursor: pointer;
                color: #e8e8e8;
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
        <div class="header">
            <h1>{PAGE_TITLE}</h1>
            <p class="channel-count">{len(sorted_channels)} channels available</p>
        </div>

        {groups_html}

        <div class="footer">
            <p>Generated from Dispatcharr API</p>
            <p class="cache-info">Last updated: {updated_str}</p>
        </div>

        <!-- Print Button -->
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
            html = generate_html(channels, groups_map, logos_map)
            cache['html'] = html
            cache['channels'] = channels  # Store raw channel data
            cache['groups_map'] = groups_map  # Store groups map
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

                    name = channel.get('name', 'Unknown')
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
