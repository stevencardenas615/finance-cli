import os
import sys
import typer
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation
import sqlite3
from enum import Enum
import csv
from pathlib import Path
from typing import NoReturn

app = typer.Typer()

DB_ENV_VAR = "FINANCE_CLI_DB"
DB_NAME = "transactions.db"

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999.99")

# Only runs when the table does not exist yet, so a database made by an older
# version keeps its old table (four plain columns, no id, no constraints).
# The checks repeat the rules in parse_amount and parse_date, so the database
# refuses bad rows even if a future bug lets one through.
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    amount REAL NOT NULL CHECK (amount > 0 AND amount < 1000000000),
    category TEXT NOT NULL,
    date TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    note TEXT NOT NULL DEFAULT ''
)
"""

class AllowedCategory(str, Enum):
    RENT = "rent"
    FOOD_DINING = "food_dining"
    SUBSCRIPTIONS = "subscriptions"
    TRANSPORTATION = "transportation"
    UTILITIES = "utilities"
    HEALTHCARE = "healthcare"
    MISC = "misc"

def fail(message: str) -> NoReturn:
    """Stop the command for a problem the user can fix: one line on stderr and exit code 1."""
    print(f"Error: {message}", file=sys.stderr)
    raise typer.Exit(code=1)

def parse_amount(text: str) -> Decimal:
    """Turn text into an amount of money, or raise ValueError. Used by add and by import."""
    try:
        amount = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError(f"'{text}' is not a number") from None
    # Decimal accepts "nan" and "inf", which are not amounts.
    if not amount.is_finite():
        raise ValueError(f"'{text}' is not a number")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero")
    if amount > MAX_AMOUNT:
        raise ValueError(f"Amount must not be more than {MAX_AMOUNT}")
    if amount != amount.quantize(CENT):
        raise ValueError("Amount can have at most two decimal places")
    return amount.quantize(CENT)

def stored_amount(value: float | None) -> Decimal:
    """Turn the number SQLite stored back into the exact amount that was entered."""
    # A database from before amounts were validated can hold NULL where "nan" was added. Count it as zero.
    if value is None:
        return Decimal("0.00")
    # str() gives the shortest text that identifies the float, which is the amount as it was typed.
    return Decimal(str(value))

def add_up(amounts: list[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """Total the (category, amount) pairs per category. Decimal keeps every cent exact."""
    totals: dict[str, Decimal] = {}
    for category, amount in amounts:
        totals[category] = totals.get(category, Decimal("0.00")) + amount
    return totals

def parse_date(text: str) -> datetime:
    """Turn YYYY-MM-DD text into a date, or raise ValueError. Used by the date options and by import."""
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"'{text}' is not a date written as YYYY-MM-DD") from None

def database_path() -> Path:
    """Decide which database file to use."""
    # 1. The environment variable wins when it is set.
    override = os.environ.get(DB_ENV_VAR)
    if override:
        return Path(override)
    # 2. Older versions created the file in the folder the command was run from. Keep using it if it is there.
    if Path(DB_NAME).exists():
        return Path(DB_NAME)
    # 3. Otherwise one fixed place per user, so the folder you run from does not matter.
    return Path.home() / ".finance-cli" / DB_NAME

def db_connect(database_file: Path) -> sqlite3.Connection:
    """Open the database, creating the folder, the file and the table if they are missing.

    The commands use it as "with closing(db_connect(...)) as conn", so the connection
    is closed even when something goes wrong halfway.
    """
    try:
        database_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(database_file)
    except (OSError, sqlite3.Error) as error:
        fail(f"Cannot open database '{database_file}': {error}")
    try:
        conn.execute(CREATE_TABLE)
    except sqlite3.Error as error:
        conn.close()
        fail(f"Cannot use database '{database_file}': {error}")
    return conn

def search_builder(choice: AllowedCategory | None = None, start: datetime | None = None, end: datetime | None = None) -> tuple[list[str], list[str]]:
    
    filters = [
        ("category = ?", choice.value if choice else None),
        ("date >= ?", start.strftime("%Y-%m-%d") if start else None),
        ("date <= ?", end.strftime("%Y-%m-%d") if end else None)
        ]

    where_prompts = [sql for sql, val in filters if val is not None]
    parameters = [val for sql, val in filters if val is not None]

    return where_prompts, parameters

def fetch_history(
    conn: sqlite3.Connection,
    choice: AllowedCategory | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[tuple[Decimal, str, str, str]]:
    """Run the one SELECT that pull, summary and export share. Returns (amount, category, date, note) rows."""
    where_prompts, parameters = search_builder(choice, start, end)

    query = "SELECT amount, category, date, note FROM history"
    if where_prompts:
        query += " WHERE " + " AND ".join(where_prompts)
    # Newest date first. Rows on the same date stay in the order they were added.
    query += " ORDER BY date DESC, rowid"

    rows = conn.execute(query, parameters).fetchall()
    return [(stored_amount(amount), category, date, note) for amount, category, date, note in rows]

@app.command()
def add(
    amount: Decimal = typer.Argument(..., parser=parse_amount),
    category: AllowedCategory = typer.Argument(...),
    date: datetime | None = typer.Option(None, parser=parse_date),
    note: str = "",
) -> None:
    if date is None:
        date = datetime.now()

    date_str = date.strftime("%Y-%m-%d")

    with closing(db_connect(database_path())) as conn:
        conn.execute("INSERT INTO history (amount, category, date, note) VALUES (?, ?, ?, ?)", (float(amount), category.value, date_str, note))
        conn.commit()

@app.command()
def pull(
    choice: AllowedCategory | None = None,
    start: datetime | None = typer.Option(None, parser=parse_date),
    end: datetime | None = typer.Option(None, parser=parse_date),
) -> None:
    with closing(db_connect(database_path())) as conn:
        history = fetch_history(conn, choice, start, end)

    if not history:
        print("No Transaction")
    else:
        for amount, category, date, note in history:
            print(f"{date} | {category.upper()} | ${amount:.2f}", end="")
            if note:
                print(f" | Note: {note}")
            else:
                print("")

@app.command()
def summary(
    choice: AllowedCategory | None = None,
    start: datetime | None = typer.Option(None, parser=parse_date),
    end: datetime | None = typer.Option(None, parser=parse_date),
) -> None:
    with closing(db_connect(database_path())) as conn:
        history = fetch_history(conn, choice, start, end)

    if not history:
        print("No Transactions")
    else:
        totals = add_up([(category, amount) for amount, category, date, note in history])
        # Largest category first.
        for category, total in sorted(totals.items(), key=lambda item: item[1], reverse=True):
            print(f"{category.upper()}: ${total:.2f}")
        print(f"TOTAL: ${sum(totals.values()):.2f}")

@app.command()
def export_history(
    filename: Path,
    choice: AllowedCategory | None = None,
    start: datetime | None = typer.Option(None, parser=parse_date),
    end: datetime | None = typer.Option(None, parser=parse_date),
) -> None:
    with closing(db_connect(database_path())) as conn:
        history = fetch_history(conn, choice, start, end)

    try:
        with open(filename, "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames = ["amount", "category", "date", "note"])
            writer.writeheader()
            for amount, category, date, note in history:
                writer.writerow({"amount": f"{amount:.2f}", "category": category, "date": date, "note": note})
    except OSError as error:
        fail(f"Cannot write '{filename}': {error.strerror}")

@app.command()
def import_history(filename: Path) -> None:
    if not filename.exists():
        fail(f"File '{filename}' does not exist.")

    # Read the whole file before touching the database, so a file that cannot be read changes nothing.
    try:
        # utf-8-sig reads plain UTF-8 and also drops a byte-order mark if there is one.
        with open(filename, "r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            rows = [(reader.line_num, row) for row in reader]
            columns = reader.fieldnames or []
    except OSError as error:
        fail(f"Cannot read '{filename}': {error.strerror}")
    except (UnicodeDecodeError, csv.Error) as error:
        fail(f"'{filename}' is not a UTF-8 CSV file: {error}")

    missing = [name for name in ["amount", "category", "date"] if name not in columns]
    if missing:
        fail(f"'{filename}' has no {', '.join(missing)} column. The first line must be: amount,category,date,note")

    success_count = 0
    duplicate_count = 0
    failed_rows = []

    with closing(db_connect(database_path())) as conn:
        # What is stored before this import starts. A row from the file that is already
        # in here is skipped, so importing the same file twice does not double anything.
        already_stored = set(conn.execute("SELECT amount, category, date, note FROM history").fetchall())

        for current_line, row in rows:
            try:
                raw_amount = row["amount"]
                raw_category = row["category"]
                raw_date = row["date"]
                # A row that is too short gets None for the fields it lacks.
                if raw_amount is None or raw_category is None or raw_date is None:
                    raise ValueError("Missing fields")
                # Extra fields are collected under the key None. Usually a note with a comma and no quotes.
                if None in row:
                    raise ValueError("Too many fields (a note with a comma needs quotes)")
                # The note is optional, so a row that stops after the date is fine.
                note = (row.get("note") or "").strip()

                amount = parse_amount(raw_amount)
                date_str = parse_date(raw_date).strftime("%Y-%m-%d")

                if raw_category not in [item.value for item in AllowedCategory]:
                    raise ValueError("Incorrect Category")

                new_row = (float(amount), raw_category, date_str, note)
                if new_row in already_stored:
                    duplicate_count += 1
                    continue

                conn.execute("INSERT INTO history (amount, category, date, note) VALUES (?, ?, ?, ?)", new_row)
                success_count += 1
            # Every reason to reject a row above is a ValueError. Anything else is a bug and should be loud.
            except ValueError as error:
                failed_rows.append(f"Line {current_line}: Error {error} | Data: {dict(row)}")

        conn.commit()

    print(f"Successfully Imported: {success_count} rows")
    print(f"Skipped (ALREADY STORED): {duplicate_count} rows")
    print(f"Skipped (BAD INPUT): {len(failed_rows)} rows")
    if failed_rows:
        print("Failed Rows:")
        for failure in failed_rows:
            print(failure)

if __name__ == "__main__":
    app()