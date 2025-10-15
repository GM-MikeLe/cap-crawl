#!/usr/bin/env python3
"""
Unit script to count unique jurisdictions in caselaw_summary.csv

This script reads the caselaw_summary.csv file and counts how many unique
jurisdictions are present in the dataset.
"""

import csv
import sys
from pathlib import Path


def count_jurisdictions(csv_file_path):
    """
    Count unique jurisdictions in the CSV file.

    Args:
        csv_file_path (str): Path to the CSV file

    Returns:
        int: Number of unique jurisdictions
    """
    jurisdictions = set()

    try:
        with open(csv_file_path, 'r', encoding='utf-8') as file:
            csv_reader = csv.DictReader(file)

            for row in csv_reader:
                jurisdiction = row['jurisdiction'].strip()
                if jurisdiction:  # Skip empty jurisdictions
                    jurisdictions.add(jurisdiction)

    except FileNotFoundError:
        print(f"Error: File '{csv_file_path}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)

    return len(jurisdictions)


def main():
    """Main function to run the jurisdiction counter."""
    # Default CSV file path
    csv_file = "caselaw_summary.csv"

    # Check if custom file path provided as command line argument
    if len(sys.argv) > 1:
        csv_file = sys.argv[1]

    # Check if file exists
    if not Path(csv_file).exists():
        print(f"Error: File '{csv_file}' does not exist.")
        sys.exit(1)

    # Count jurisdictions
    unique_count = count_jurisdictions(csv_file)

    # Print results
    print(f"Total unique jurisdictions: {unique_count}")
    print(f"File analyzed: {csv_file}")


if __name__ == "__main__":
    main()
