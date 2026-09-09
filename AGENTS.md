# Project instructions

## Purpose

Build a command-line tool that records and searches machine setting history.

## Requirements

- Use only the Python standard library.
- Store records in CSV format.
- Treat one input session as one record.
- Use the same record_id for all setting rows entered together.
- Count records separately for each machine.
- Keep the newest 90 records for each machine.
- Show the 91st and older records as deletion candidates.
- Do not delete records without user confirmation.

## File responsibilities

- main.py handles menus and user interaction.
- storage.py handles CSV reading and writing.
- history.py handles validation, grouping, searching, and deletion-candidate selection.
- Tests belong in test_storage.py and test_history.py.

## Safety

- Do not add real company data.
- Do not commit data/records.csv.
- Preserve existing user changes.
- Do not commit or push unless explicitly requested.

## Verification

Run all tests with:

python -m unittest -v