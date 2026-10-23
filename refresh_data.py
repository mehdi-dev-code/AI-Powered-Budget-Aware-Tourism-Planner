"""Apply edits made in data/*.csv to the database.

Usage:
    python refresh_data.py            add new rows and update changed rows
    python refresh_data.py --prune    also remove database rows that were deleted from the CSV files
"""
import sys
from backend.database import refresh_data, format_refresh_report

if __name__ == "__main__":
    print(format_refresh_report(refresh_data(prune="--prune" in sys.argv)))
