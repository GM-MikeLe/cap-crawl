#!/usr/bin/env python3
"""
FIXED VERSION: Parallel script to count files in static.case.law using correct structure:
{reporter_slug}/{volume_number}/cases/{file_name}

Previous version incorrectly used {jurisdiction}/{volume_number}/cases/
This version extracts valid (reporter_slug, volume_number) pairs from VolumesMetadata.json
"""

import json
import requests
import time
import re
from collections import defaultdict
from urllib.parse import urljoin
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import signal

# Configuration
BASE_URL = "https://static.case.law/"
MAX_WORKERS = 20  # Number of concurrent threads
MAX_RETRIES = 3
REQUEST_TIMEOUT = 10
RATE_LIMIT_DELAY = 0.05  # Small delay since we're doing many in parallel

# Global variables for progress tracking
progress_lock = threading.Lock()
total_processed = 0
total_files_found = 0
total_size_found = 0
results = defaultdict(lambda: defaultdict(lambda: {'files': 0, 'size': 0}))


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    print('\n\nReceived interrupt signal. Saving partial results...')
    save_results()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


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


def extract_valid_combinations(volumes):
    """
    Extract valid (reporter_slug, volume_number) combinations from VolumesMetadata.
    This is the CORRECT approach - use the actual metadata instead of creating
    a Cartesian product that results in mostly invalid URLs.
    """
    combinations = []
    combinations_set = set()  # To avoid duplicates
    
    for volume in volumes:
        reporter_slug = volume.get('reporter_slug')
        volume_number = volume.get('volume_number')
        
        # Only add if both fields exist and are non-empty
        if reporter_slug and volume_number:
            combo = (reporter_slug, volume_number)
            if combo not in combinations_set:
                combinations_set.add(combo)
                combinations.append(combo)
    
    return combinations


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


def fetch_directory_data(reporter_volume):
    """Fetch and process data for a single reporter/volume combination"""
    reporter_slug, volume = reporter_volume
    global total_processed, total_files_found, total_size_found

    cases_url = f"{BASE_URL}{reporter_slug}/{volume}/cases/"

    for attempt in range(MAX_RETRIES):
        try:
            # Add small delay to avoid overwhelming the server
            time.sleep(RATE_LIMIT_DELAY)

            response = requests.get(cases_url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                file_count, directory_size, file_details = count_files_and_sizes_in_html(
                    response.text)

                # Update global counters and results
                with progress_lock:
                    total_processed += 1

                    if file_count > 0:
                        results[reporter_slug][volume] = {
                            'files': file_count,
                            'size': directory_size,
                            'details': file_details
                        }

                        total_files_found += file_count
                        total_size_found += directory_size

                        print(
                            f"✓ {reporter_slug}/{volume}/cases/: {file_count} files, {format_bytes(directory_size)}")

                    # Progress reporting
                    if total_processed % 100 == 0:
                        print(
                            f"🔄 Progress: {total_processed} processed, {total_files_found:,} files found so far, {format_bytes(total_size_found)}")

                return {
                    'reporter_slug': reporter_slug,
                    'volume': volume,
                    'success': True,
                    'files': file_count,
                    'size': directory_size
                }

            elif response.status_code == 404:
                # Directory doesn't exist, that's okay
                with progress_lock:
                    total_processed += 1
                return {
                    'reporter_slug': reporter_slug,
                    'volume': volume,
                    'success': False,
                    'error': '404'
                }
            else:
                # Other HTTP error, retry
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1 * (attempt + 1))  # Exponential backoff
                    continue

                with progress_lock:
                    total_processed += 1
                return {
                    'reporter_slug': reporter_slug,
                    'volume': volume,
                    'success': False,
                    'error': f'HTTP {response.status_code}'
                }

        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1 * (attempt + 1))  # Exponential backoff
                continue

            with progress_lock:
                total_processed += 1
            return {
                'reporter_slug': reporter_slug,
                'volume': volume,
                'success': False,
                'error': str(e)
            }

    # If we get here, all retries failed
    with progress_lock:
        total_processed += 1
    return {
        'reporter_slug': reporter_slug,
        'volume': volume,
        'success': False,
        'error': 'Max retries exceeded'
    }


def save_results():
    """Save results to JSON file"""
    output_file = "caselaw_file_counts_parallel_fixed.json"

    # Convert defaultdict to regular dict for JSON serialization
    results_dict = {}
    for reporter_slug, volumes in results.items():
        results_dict[reporter_slug] = dict(volumes)

    with open(output_file, 'w') as f:
        json.dump(results_dict, f, indent=2)

    print(f"\n💾 Results saved to: {output_file}")


def generate_final_report(total_combinations):
    """Generate and print the final report"""
    successful_directories = sum(1 for r in results.values()
                                 for v in r.values() if v['files'] > 0)
    failed_requests = total_processed - successful_directories

    print("\n" + "="*60)
    print("FINAL REPORT")
    print("="*60)
    print(f"Total files found: {total_files_found:,}")
    print(f"Total size: {format_bytes(total_size_found)}")
    print(
        f"Average file size: {format_bytes(total_size_found // total_files_found) if total_files_found > 0 else '0 B'}")
    print(f"Successful directories: {successful_directories:,}")
    print(f"Failed/empty requests: {failed_requests:,}")
    print(f"Total combinations processed: {total_processed:,}")
    print(f"Total combinations possible: {total_combinations:,}")

    # Top reporters by file count and size
    print("\nTop 10 Reporters by File Count:")
    reporter_file_totals = {}
    reporter_size_totals = {}

    for reporter_slug, volumes in results.items():
        total_files_for_reporter = 0
        total_size_for_reporter = 0
        for volume_data in volumes.values():
            total_files_for_reporter += volume_data.get('files', 0)
            total_size_for_reporter += volume_data.get('size', 0)

        if total_files_for_reporter > 0:
            reporter_file_totals[reporter_slug] = total_files_for_reporter
            reporter_size_totals[reporter_slug] = total_size_for_reporter

    top_reporters_by_files = sorted(reporter_file_totals.items(),
                                    key=lambda x: x[1], reverse=True)[:10]

    for i, (reporter_slug, count) in enumerate(top_reporters_by_files, 1):
        size = reporter_size_totals.get(reporter_slug, 0)
        print(f"{i:2d}. {reporter_slug}: {count:,} files, {format_bytes(size)}")

    # Top reporters by total size
    print("\nTop 10 Reporters by Total Size:")
    top_reporters_by_size = sorted(reporter_size_totals.items(),
                                   key=lambda x: x[1], reverse=True)[:10]

    for i, (reporter_slug, size) in enumerate(top_reporters_by_size, 1):
        count = reporter_file_totals.get(reporter_slug, 0)
        print(f"{i:2d}. {reporter_slug}: {format_bytes(size)} ({count:,} files)")


def main():
    """Main function to count all files using parallel processing"""
    print("🚀 FIXED Parallel Case.law File Counter")
    print("=" * 50)
    print("📌 Using correct structure: {reporter_slug}/{volume_number}/cases/")
    print("=" * 50)

    # Load metadata
    jurisdictions, volumes, reporters = load_metadata()

    # Extract valid combinations from volumes metadata
    combinations = extract_valid_combinations(volumes)

    total_combinations = len(combinations)
    print(f"📊 Found {len(reporters)} reporters in ReportersMetadata")
    print(f"📊 Found {len(volumes):,} volumes in VolumesMetadata")
    print(f"📊 Valid (reporter_slug, volume) combinations: {total_combinations:,}")
    print(f"🔧 Using {MAX_WORKERS} parallel workers")
    print(f"⚡ Estimated time: ~{total_combinations / MAX_WORKERS / 60:.1f} minutes")
    print()

    start_time = time.time()

    # Process combinations in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
        future_to_combination = {
            executor.submit(fetch_directory_data, combination): combination
            for combination in combinations
        }

        # Process completed tasks
        completed = 0
        for future in as_completed(future_to_combination):
            completed += 1
            result = future.result()

            # Print errors for debugging (except 404s which are expected)
            if not result['success'] and result.get('error') not in ['404']:
                print(
                    f"❌ Error for {result['reporter_slug']}/{result['volume']}: {result.get('error')}")

    elapsed_time = time.time() - start_time

    print(f"\n⏱️  Total processing time: {elapsed_time:.2f} seconds ({elapsed_time/60:.2f} minutes)")
    print(
        f"⚡ Average rate: {total_processed/elapsed_time:.2f} requests/second")

    # Generate final report
    generate_final_report(total_combinations)

    # Save results
    save_results()

    return total_files_found


if __name__ == "__main__":
    try:
        total = main()
        print(f"\n🎉 Completed successfully! Total files: {total:,}")
        print(f"📊 Expected ~7 million records according to Case.law")
        if total > 0:
            coverage = (total / 7_000_000) * 100
            print(f"📈 Coverage: ~{coverage:.1f}% of expected total")
    except KeyboardInterrupt:
        print("\n\n⚠️  Operation cancelled by user.")
        save_results()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        save_results()
        sys.exit(1)
