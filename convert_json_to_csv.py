#!/usr/bin/env python3
"""
Convert the JSON results to CSV format for easier analysis
"""

import json
import csv
import os


def format_bytes(bytes_value):
    """Format bytes as human-readable string"""
    if bytes_value == 0:
        return "0 B"

    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


def convert_to_csv(json_file, summary_csv, detailed_csv):
    """Convert JSON results to CSV format"""

    print(f"Converting {json_file} to CSV format...")

    # Load JSON data
    with open(json_file, 'r') as f:
        data = json.load(f)

    # Create summary CSV
    with open(summary_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'jurisdiction',
            'volume',
            'file_count',
            'total_size_bytes',
            'total_size_formatted',
            'avg_file_size_bytes',
            'avg_file_size_formatted'
        ])

        # Data rows
        total_files = 0
        total_size = 0
        total_entries = 0

        for jurisdiction, volumes in data.items():
            for volume, volume_data in volumes.items():
                files = volume_data.get('files', 0)
                size = volume_data.get('size', 0)

                avg_size = size // files if files > 0 else 0

                writer.writerow([
                    jurisdiction,
                    volume,
                    files,
                    size,
                    format_bytes(size),
                    avg_size,
                    format_bytes(avg_size)
                ])

                total_files += files
                total_size += size
                total_entries += 1

        print(f"✓ Summary CSV created: {summary_csv}")
        print(f"  - {total_entries:,} jurisdiction/volume combinations")
        print(f"  - {total_files:,} total files")
        print(f"  - {format_bytes(total_size)} total size")

    # Create detailed CSV (individual files)
    with open(detailed_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'jurisdiction',
            'volume',
            'filename',
            'size_bytes',
            'size_formatted',
            'last_modified'
        ])

        # Data rows
        file_count = 0
        for jurisdiction, volumes in data.items():
            for volume, volume_data in volumes.items():
                details = volume_data.get('details', [])

                for file_detail in details:
                    writer.writerow([
                        jurisdiction,
                        volume,
                        file_detail.get('filename', ''),
                        file_detail.get('size_bytes', 0),
                        file_detail.get('size_str', ''),
                        file_detail.get('last_modified', '')
                    ])
                    file_count += 1

        print(f"✓ Detailed CSV created: {detailed_csv}")
        print(f"  - {file_count:,} individual file records")


def main():
    json_file = "caselaw_file_counts_parallel.json"
    summary_csv = "caselaw_summary.csv"
    detailed_csv = "caselaw_detailed.csv"

    if not os.path.exists(json_file):
        print(f"❌ JSON file not found: {json_file}")
        return

    print("🔄 Converting JSON to CSV format...")
    print("=" * 50)

    convert_to_csv(json_file, summary_csv, detailed_csv)

    print("\n✅ Conversion complete!")
    print(f"📊 Summary data: {summary_csv}")
    print(f"📄 Detailed data: {detailed_csv}")


if __name__ == "__main__":
    main()
