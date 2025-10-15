#!/usr/bin/env python3
"""
Script to count files in static.case.law following the pattern:
{jurisdiction}/{volume_number}/cases/{file_name}
"""

import json
import requests
import time
import re
from collections import defaultdict
from urllib.parse import urljoin
import sys

# Configuration
BASE_URL = "https://static.case.law/"
DELAY_BETWEEN_REQUESTS = 0.5  # seconds
MAX_RETRIES = 3


def load_metadata():
    """Load and parse metadata files"""
    print("Loading metadata files...")

    # Load jurisdictions
    with open('JurisdictionsMetadata.json', 'r') as f:
        jurisdictions = json.load(f)

    # Load volumes
    with open('VolumesMetadata.json', 'r') as f:
        volumes = json.load(f)

    # Load reporters
    with open('ReportersMetadata.json', 'r') as f:
        reporters = json.load(f)

    return jurisdictions, volumes, reporters


def get_jurisdiction_slugs(jurisdictions):
    """Extract jurisdiction slugs from metadata"""
    slugs = set()
    for jurisdiction in jurisdictions:
        if 'slug' in jurisdiction:
            slugs.add(jurisdiction['slug'])
    return sorted(slugs)


def get_volume_numbers(volumes):
    """Extract unique volume numbers from metadata"""
    volume_nums = set()
    for volume in volumes:
        if 'volume_number' in volume and volume['volume_number']:
            volume_nums.add(volume['volume_number'])
    return sorted(volume_nums, key=lambda x: int(x) if x.isdigit() else float('inf'))


def fetch_directory_listing(url):
    """Fetch directory listing from a URL"""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return response.text
            elif response.status_code == 404:
                return None  # Directory doesn't exist
            else:
                print(f"Warning: HTTP {response.status_code} for {url}")
                return None
        except requests.RequestException as e:
            print(f"Error fetching {url} (attempt {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
    return None


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


def format_bytes(bytes_value):
    """Format bytes as human-readable string"""
    if bytes_value == 0:
        return "0 B"

    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


def main():
    """Main function to count all files"""
    print("Case.law File Counter")
    print("====================")

    # Load metadata
    jurisdictions, volumes, reporters = load_metadata()

    # Get unique jurisdiction slugs and volume numbers
    jurisdiction_slugs = get_jurisdiction_slugs(jurisdictions)
    volume_numbers = get_volume_numbers(volumes)

    print(f"Found {len(jurisdiction_slugs)} jurisdictions")
    print(f"Found {len(volume_numbers)} unique volume numbers")
    print(
        f"Total combinations to check: {len(jurisdiction_slugs) * len(volume_numbers)}")
    print()

    # Statistics
    total_files = 0
    total_size = 0
    successful_directories = 0
    failed_requests = 0
    results = defaultdict(lambda: defaultdict(lambda: {'files': 0, 'size': 0}))

    # Process each jurisdiction/volume combination
    total_combinations = len(jurisdiction_slugs) * len(volume_numbers)
    current_combination = 0

    for jurisdiction in jurisdiction_slugs:
        print(f"Processing jurisdiction: {jurisdiction}")

        for volume in volume_numbers:
            current_combination += 1

            # Construct URL for cases directory
            cases_url = f"{BASE_URL}{jurisdiction}/{volume}/cases/"

            # Show progress
            if current_combination % 100 == 0:
                print(f"Progress: {current_combination}/{total_combinations} "
                      f"({100*current_combination/total_combinations:.1f}%)")

            # Fetch directory listing
            html_content = fetch_directory_listing(cases_url)

            if html_content is not None:
                file_count, directory_size, file_details = count_files_and_sizes_in_html(
                    html_content)
                if file_count > 0:
                    results[jurisdiction][volume] = {
                        'files': file_count,
                        'size': directory_size,
                        'details': file_details
                    }
                    total_files += file_count
                    total_size += directory_size
                    successful_directories += 1
                    print(
                        f"  {jurisdiction}/{volume}/cases/: {file_count} files, {format_bytes(directory_size)}")
            else:
                failed_requests += 1

            # Rate limiting
            time.sleep(DELAY_BETWEEN_REQUESTS)

    # Generate report
    print("\n" + "="*50)
    print("FINAL REPORT")
    print("="*50)
    print(f"Total files found: {total_files:,}")
    print(f"Total size: {format_bytes(total_size)}")
    print(
        f"Average file size: {format_bytes(total_size // total_files) if total_files > 0 else '0 B'}")
    print(f"Successful directories: {successful_directories:,}")
    print(f"Failed requests: {failed_requests:,}")
    print(f"Total combinations checked: {total_combinations:,}")

    # Top jurisdictions by file count and size
    print("\nTop 10 Jurisdictions by File Count:")
    jurisdiction_file_totals = {}
    jurisdiction_size_totals = {}

    for jurisdiction, volumes in results.items():
        total_files_for_jurisdiction = 0
        total_size_for_jurisdiction = 0
        for volume_data in volumes.values():
            if isinstance(volume_data, dict):
                total_files_for_jurisdiction += volume_data.get('files', 0)
                total_size_for_jurisdiction += volume_data.get('size', 0)
            else:
                # Handle legacy format (just in case)
                total_files_for_jurisdiction += volume_data

        jurisdiction_file_totals[jurisdiction] = total_files_for_jurisdiction
        jurisdiction_size_totals[jurisdiction] = total_size_for_jurisdiction

    top_jurisdictions_by_files = sorted(jurisdiction_file_totals.items(),
                                        key=lambda x: x[1], reverse=True)[:10]

    for i, (jurisdiction, count) in enumerate(top_jurisdictions_by_files, 1):
        size = jurisdiction_size_totals.get(jurisdiction, 0)
        print(f"{i:2d}. {jurisdiction}: {count:,} files, {format_bytes(size)}")

    # Top jurisdictions by total size
    print("\nTop 10 Jurisdictions by Total Size:")
    top_jurisdictions_by_size = sorted(jurisdiction_size_totals.items(),
                                       key=lambda x: x[1], reverse=True)[:10]

    for i, (jurisdiction, size) in enumerate(top_jurisdictions_by_size, 1):
        count = jurisdiction_file_totals.get(jurisdiction, 0)
        print(f"{i:2d}. {jurisdiction}: {format_bytes(size)} ({count:,} files)")

    # Save detailed results to file
    output_file = "caselaw_file_counts.json"
    with open(output_file, 'w') as f:
        json.dump(dict(results), f, indent=2)

    print(f"\nDetailed results saved to: {output_file}")

    return total_files


if __name__ == "__main__":
    try:
        total = main()
        print(f"\nCompleted successfully. Total files: {total:,}")
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
