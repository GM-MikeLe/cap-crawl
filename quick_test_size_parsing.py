#!/usr/bin/env python3
"""
Quick test to verify size parsing and reporting works correctly
"""

import json
import requests
import time
import re
from collections import defaultdict

BASE_URL = "https://static.case.law/"


def parse_size_string(size_str):
    """Convert size string like '20.18 KB' to bytes"""
    if not size_str or size_str == '-':
        return 0

    # Remove any whitespace and convert to uppercase
    size_str = size_str.strip().upper()

    # Extract number and unit
    parts = size_str.split()
    if len(parts) != 2:
        return 0

    try:
        value = float(parts[0])
        unit = parts[1]

        multipliers = {
            'B': 1,
            'KB': 1024,
            'MB': 1024 * 1024,
            'GB': 1024 * 1024 * 1024,
            'TB': 1024 * 1024 * 1024 * 1024
        }

        return int(value * multipliers.get(unit, 1))
    except (ValueError, KeyError):
        return 0


def format_bytes(bytes_value):
    """Format bytes as human-readable string"""
    if bytes_value == 0:
        return "0 B"

    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


def count_files_and_sizes_in_html(html_content):
    """Count files and calculate total size from HTML directory listing"""
    if not html_content:
        return 0, 0, []

    file_count = 0
    total_size = 0
    file_details = []

    # Parse HTML table structure
    # Look for table rows with file, size, and last modified columns
    table_rows = re.findall(
        r'<tr><td><a href=\'([^\']+)\'>([^<]+)</a></td><td>([^<]*)</td><td>([^<]*)</td></tr>', html_content)

    for link, filename, size_str, last_modified in table_rows:
        # Skip parent directory, metadata files, and directories
        if (link not in ['../', '../'] and
            not link.endswith('/') and
            not link.startswith('?') and
            link not in ['', '#'] and
                filename not in ['', '#']):

            file_size = parse_size_string(size_str)
            file_count += 1
            total_size += file_size

            file_details.append({
                'filename': filename,
                'size_bytes': file_size,
                'size_str': size_str,
                'last_modified': last_modified
            })

    return file_count, total_size, file_details


def test_specific_directories():
    """Test parsing on specific directories"""

    test_cases = [
        ("us", "372"),  # We know this has files
        ("cal", "50"),  # Test another jurisdiction
        ("ny", "25"),   # Another test case
    ]

    print("Testing Size Parsing on Specific Directories")
    print("=" * 50)

    total_files = 0
    total_size = 0

    for jurisdiction, volume in test_cases:
        cases_url = f"{BASE_URL}{jurisdiction}/{volume}/cases/"
        print(f"\nTesting: {jurisdiction}/{volume}/cases/")

        try:
            response = requests.get(cases_url, timeout=10)
            if response.status_code == 200:
                file_count, directory_size, file_details = count_files_and_sizes_in_html(
                    response.text)

                print(f"  ✓ Found {file_count} files")
                print(f"  ✓ Total size: {format_bytes(directory_size)}")

                if file_count > 0:
                    avg_size = directory_size // file_count
                    print(f"  ✓ Average file size: {format_bytes(avg_size)}")

                    # Show size distribution
                    sizes = [f['size_bytes'] for f in file_details]
                    if sizes:
                        print(f"  ✓ Largest file: {format_bytes(max(sizes))}")
                        print(f"  ✓ Smallest file: {format_bytes(min(sizes))}")

                    # Show a few examples
                    examples = file_details[:3]
                    print("  ✓ Examples:")
                    for detail in examples:
                        print(
                            f"    - {detail['filename']}: {detail['size_str']} ({detail['size_bytes']} bytes)")

                total_files += file_count
                total_size += directory_size

            else:
                print(f"  ✗ HTTP {response.status_code}")

        except Exception as e:
            print(f"  ✗ Error: {e}")

        time.sleep(0.2)  # Brief delay

    print(f"\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Total files tested: {total_files}")
    print(f"Total size: {format_bytes(total_size)}")
    if total_files > 0:
        print(f"Average file size: {format_bytes(total_size // total_files)}")


if __name__ == "__main__":
    test_specific_directories()
