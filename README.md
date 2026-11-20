# Finance CLI
![CI](https://github.com/stevencardenas615/finance-cli/actions/workflows/ci.yml/badge.svg)

A command-line tool for saving and searching your personal finances.

This was my first finished project. I built it to learn how the parts of a small program fit together: command-line arguments, a database, CSV files and tests. I wrote it before [inventory-api](https://github.com/stevencardenas615/inventory-api), a FastAPI and PostgreSQL API, and later came back to it with what I learned there: exact money amounts, tests for every command, pinned dependencies and CI.

## What it does

Stores transactions in a local SQLite database with a category, amount, date and optional note. Transactions can be filtered by category or date(s) including ranges. Results or transaction history can be exported into a csv file. Transaction history could also be imported into the database, streamlining usability. Transaction total amounts can also be calculated using date ranges, results are separated by category and a total line displayed at end.

## Setup

Requires Python 3.11+

```bash
git clone https://github.com/stevencardenas615/finance-cli.git
cd finance-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python finance_cli.py --help
```

The dependencies are pinned in `pyproject.toml`. The install also adds a `finance-cli` command that works from any folder while the virtual environment is active.

The database is created automatically on first use. The file is picked in this order:

1. The path in the `FINANCE_CLI_DB` environment variable, if it is set.
2. A `transactions.db` in the current folder, if one exists. Earlier versions created the file there, so an existing one keeps working.
3. Otherwise `~/.finance-cli/transactions.db`, so the commands see the same data from any folder.

## Usage

### Add a transaction

```bash
python finance_cli.py add 5 food_dining --date 2026-09-05
python finance_cli.py add 14.50 food_dining --date 2026-09-06 --note "Lunch burrito"
```

```
Added: 2026-09-05 | FOOD_DINING | $5.00
Added: 2026-09-06 | FOOD_DINING | $14.50 | Note: Lunch burrito
```

`--date` and `--note` are optional. Without `--date` the transaction is saved with today's date. Dates are always written as YYYY-MM-DD.

The categories are `rent`, `food_dining`, `subscriptions`, `transportation`, `utilities`, `healthcare` and `misc`.

An amount has to be a number greater than zero with at most two decimal places. Anything else, such as `nan`, `-5` or `12.345`, is rejected before it reaches the database.

### Pull transactions

```bash
python finance_cli.py pull --category food_dining
```

```
2026-09-06 | FOOD_DINING | $14.50 | Note: Lunch burrito
2026-09-05 | FOOD_DINING | $5.00
2026-09-04 | FOOD_DINING | $1.99 | Note: water bottle from grocery store
2026-09-04 | FOOD_DINING | $0.99 | Note: chips from grocery store
2026-09-04 | FOOD_DINING | $7.25 | Note: coffee
2026-09-04 | FOOD_DINING | $3.99 | Note: jollyranchers from grocery store
2026-09-03 | FOOD_DINING | $7.99 | Note: Ritas
```

`--start` and `--end` limit the dates. Both days are included, and either one can be left out.

```bash
python finance_cli.py pull --start 2026-09-05 --end 2026-09-06
```

```
2026-09-06 | FOOD_DINING | $14.50 | Note: Lunch burrito
2026-09-05 | RENT | $250.00
2026-09-05 | FOOD_DINING | $5.00
```

### Transaction summary

```bash
python finance_cli.py summary
```

```
RENT: $500.00
TRANSPORTATION: $82.39
FOOD_DINING: $41.71
SUBSCRIPTIONS: $9.99
TOTAL: $634.09
```

The same `--category`, `--start` and `--end` options work here:

```bash
python finance_cli.py summary --start 2026-09-04 --end 2026-09-05
```

```
RENT: $500.00
TRANSPORTATION: $82.39
FOOD_DINING: $19.22
SUBSCRIPTIONS: $9.99
TOTAL: $611.60
```

### Export history to CSV

```bash
python finance_cli.py export-history transaction_history.csv
```

```
Exported 11 rows to transaction_history.csv
```

CSV file created `transaction_history.csv`:

```
amount,category,date,note
14.50,food_dining,2026-09-06,Lunch burrito
250.00,rent,2026-09-05,
5.00,food_dining,2026-09-05,
82.39,transportation,2026-09-04,
250.00,rent,2026-09-04,
1.99,food_dining,2026-09-04,water bottle from grocery store
9.99,subscriptions,2026-09-04,spotify
0.99,food_dining,2026-09-04,chips from grocery store
7.25,food_dining,2026-09-04,coffee
3.99,food_dining,2026-09-04,jollyranchers from grocery store
7.99,food_dining,2026-09-03,Ritas
```

`--category`, `--start` and `--end` work here too.

Export does not replace a file that already exists unless you add `--overwrite`:

```
Error: File 'transaction_history.csv' already exists. Add --overwrite to replace it.
```

### Import history from CSV

Given `september.csv`:

```
amount,category,date,note
45.20,food_dining,2026-09-01,groceries
1200.00,rent,2026-09-01,September rent
abc,utilities,2026-09-02,bad amount
60.00,foood,2026-09-03,typo in category
35.99,subscriptions,2026-09-04,streaming
```

```bash
python finance_cli.py import-history september.csv
```

```
Successfully Imported: 3 rows
Skipped (ALREADY STORED): 0 rows
Skipped (BAD INPUT): 2 rows
Failed Rows:
Line 4: Error 'abc' is not a number | Data: {'amount': 'abc', 'category': 'utilities', 'date': '2026-09-02', 'note': 'bad amount'}
Line 5: Error Incorrect Category | Data: {'amount': '60.00', 'category': 'foood', 'date': '2026-09-03', 'note': 'typo in category'}
```

Invalid rows are skipped and reported with their line number and reason;
valid rows are still imported.

A row with missing fields or with too many fields (usually a note that has a comma and no quotes around it) counts as invalid. A row that only leaves out the note is fine.

A row that is already stored (same amount, category, date and note) is skipped, so importing the same file twice does not double anything.

The file has to be UTF-8 text and start with the `amount,category,date,note` line. If the file cannot be read at all, the command stops with an error and nothing is imported.


## Design Decisions

**SQLite** The reason why I ended up using SQLite is because memory was being deleted on every instance when using the CLI. SQLite allows us to store this in a local database.

**Dynamic Query Builder** I used a dynamic query builder to prevent the confusing and constant if statements in the project. Adding another filter would've added another 4 lines of code of an if statement. The dynamic query builder helps significantly in streamlining the creation of future commands because of reusability.

**Enum for categories** By using Enum for categories we can control, filter and check user input to ensure that it matches with what we are allowing. It's an all in one check that starts right from the CLI.

**Import skips bad rows and reports them** For ease of use I decided in skipping the bad rows and printing the results at the end. The results show what row and for what reason data was rejected and not stored. This aids in the search of your csv file to view what's wrong. It also ensures that at least all data is checked and given a chance to be imported.

## Limits

- **Amounts are stored as REAL.** The amount column in SQLite is a floating point number. I kept it that way so that database files made by earlier versions keep working. To stay exact anyway, every amount is checked as a Decimal on the way in (greater than zero, at most two decimal places, at most 999999999.99) and totals are added up as Decimal, so sums are exact to the cent for every amount the tool accepts. In inventory-api I used NUMERIC from the start.
- **Old database files keep their old table.** New databases get an `id` primary key and NOT NULL and CHECK constraints. A file made by an earlier version is not migrated, so there the rules are only enforced by the code. Earlier versions accepted `add nan`, which stored an empty amount. Such a row is shown as $0.00.
- **Expenses only.** Amounts must be positive, so there is no income and there are no refunds.
- **No edit or delete command.** A wrong entry can only be fixed by opening the database by hand.
- **Categories are fixed in the code.**
- **The duplicate check on import compares all four fields.** If a transaction is already stored, an identical row in a CSV file is skipped even when it was really a second purchase.
- **One user, one machine.** It is a local file with no accounts and no sync.

## Testing

```bash
pytest
```

The tests run every command through Typer's test runner against a temporary database, so they never touch real data. They cover add, pull, summary, export and import, the query builder, amount and date validation, the import cases above (short rows, extra fields, byte-order mark, duplicates, unreadable files), where the database file goes, and that a database made by the earlier version still works. GitHub Actions runs them on every push and pull request.

