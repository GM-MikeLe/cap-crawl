#!/usr/bin/env python3
"""
Quick test script to validate the case.law file counting approach on a small sample
"""

import json
import requests
import time
import re

BASE_URL = "https://static.case.law/"


def test_sample():
    """Test on a small sample of jurisdictions and volumes"""

    # Test a few known jurisdictions and volumes
    test_cases = [
        ("us", "1"),       # US Supreme Court, volume 1
        ("us", "100"),     # US Supreme Court, volume 100
        ("cal", "1"),      # California, volume 1
        ("ny", "1"),       # New York, volume 1
        ("tex", "1"),      # Texas, volume 1
    ]

    print("Testing sample cases:")
    print("====================")

    total_files = 0

    for jurisdiction, volume in test_cases:
        cases_url = f"{BASE_URL}{jurisdiction}/{volume}/cases/"
        print(f"Testing: {cases_url}")

        try:
            response = requests.get(cases_url, timeout=10)
            if response.status_code == 200:
                # Parse HTML table structure for files and sizes
                table_rows = re.findall(
                    r'<tr><td><a href=\'([^\']+)\'>([^<]+)</a></td><td>([^<]*)</td><td>([^<]*)</td></tr>', response.text)

                file_count = 0
                total_size = 0
                examples = []

                for link, filename, size_str, last_modified in table_rows:
                    # Skip parent directory, metadata files, and directories
                    if (link not in ['../', '../'] and
                        not link.endswith('/') and
                        not link.startswith('?') and
                        link not in ['', '#'] and
                            filename not in ['', '#']):

                        file_count += 1
                        if len(examples) < 3:
                            examples.append(f"{filename} ({size_str})")

                print(f"  ✓ Found {file_count} files")
                total_files += file_count

                # Show first few files as example
                if examples:
                    print(f"    Examples: {', '.join(examples)}")

            elif response.status_code == 404:
                print(f"  ✗ Directory not found (404)")
            else:
                print(f"  ✗ HTTP {response.status_code}")

        except Exception as e:
            print(f"  ✗ Error: {e}")

        time.sleep(0.2)  # Brief delay

    print(f"\nTotal sample files found: {total_files}")
    return total_files


if __name__ == "__main__":
    test_sample()
