# Project instructions

## Purpose

Build a desktop and command-line tool that records and searches machine setting history.

## Requirements

- Use only the Python standard library.
- Store records in CSV format.
- Treat one input session as one record.
- Use the same record_id for all setting rows entered together.
- Count records separately for each machine.
- Keep the newest 90 records for each machine.
- Show the 91st and older records as deletion candidates.
- Do not delete records without user confirmation.
- GUI: show settings directly in the history list.
- GUI: select existing setting names per machine; only new items require name/unit input.
- Persist definitions separately from history. Deletion must preserve definitions.
- Never silently prefill a previous record's numeric values.

## File responsibilities

- main.py handles menus and user interaction.
- storage.py handles CSV reading and writing.
- history.py handles validation, grouping, searching, and deletion-candidate selection.
- Tests belong in test_storage.py and test_history.py.
- gui.py handles the Tkinter desktop interface; main.py preserves the CLI.
- app_service.py coordinates registration and deletion; catalog.py stores definitions.
- comparison.py compares two records with Decimal, without unit conversion.
- test_app_service.py covers new persistence, recovery, and comparison cases.
- test_gui.py runs GUI flows when Tk and a display are available, otherwise skips.

## Safety

- Do not add real company data.
- Do not commit data/records.csv.
- Preserve existing user changes.
- Do not commit or push unless explicitly requested.

## Verification

Run all tests with:

python -m unittest -v
