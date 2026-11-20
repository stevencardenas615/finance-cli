import typer
from datetime import datetime
from decimal import Decimal, InvalidOperation
import sqlite3
from enum import Enum
import csv
from pathlib import Path

app = typer.Typer()

user_database = "transactions.db"

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999.99")

class AllowedCategory(str, Enum):
    RENT = "rent"
    FOOD_DINING = "food_dining"
    SUBSCRIPTIONS = "subscriptions"
    TRANSPORTATION = "transportation"
    UTILITIES = "utilities"
    HEALTHCARE = "healthcare"
    MISC = "misc"

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

def db_connect(database_file: str | Path,) -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    conn = sqlite3.connect(database_file)
    conn.execute("CREATE TABLE IF NOT EXISTS history (amount REAL, category TEXT, date TEXT, note TEXT)")
    return conn, conn.cursor()

def db_disconnect(connector: sqlite3.Connection) -> None:
    connector.commit()
    connector.close()

def search_builder(choice: AllowedCategory | None = None, start: datetime | None = None, end: datetime | None = None) -> tuple[list[str], list[str]]:
    
    filters = [
        ("category = ?", choice.value if choice else None, choice is not None),
        ("date >= ?", start.strftime("%Y-%m-%d") if start else None, start is not None),
        ("date <= ?", end.strftime("%Y-%m-%d") if end else None, end is not None)
        ]

    where_prompts = [sql for sql, val, active in filters if active]
    parameters = [val for sql, val, active in filters if active]

    return where_prompts, parameters
   
@app.command()
def add(
    amount: Decimal = typer.Argument(..., parser=parse_amount),
    category: AllowedCategory = typer.Argument(...),
    date: datetime | None = typer.Option(None, parser=parse_date),
    note: str = "",
) -> None:
    conn, cursor = db_connect(user_database)

    if date is None:
        date = datetime.now()

    date_str = date.strftime("%Y-%m-%d")

    cursor.execute("INSERT INTO history (amount, category, date, note) VALUES (?, ?, ?, ?)", (float(amount), category, date_str, note))
    db_disconnect(conn)

@app.command()
def pull(
    choice: AllowedCategory | None = None,
    start: datetime | None = typer.Option(None, parser=parse_date),
    end: datetime | None = typer.Option(None, parser=parse_date),
) -> None:
    conn, cursor = db_connect(user_database)

    where_prompts, parameters = search_builder(choice, start, end)

    query = "SELECT * FROM history"
    if where_prompts:
        query += " WHERE " + " AND ".join(where_prompts)
    query += " ORDER BY date DESC"

    history = cursor.execute(query, parameters).fetchall()
                    
    if not history:
        print("No Transaction")
    else:
        for row in history:
            print(f"{row[2]} | {row[1].upper()} | ${row[0]:.2f}", end="")
            if row[3]:
                print(f" | Note: {row[3]}")
            else:
                print("")

    db_disconnect(conn)

@app.command()
def summary(
    choice: AllowedCategory | None = None,
    start: datetime | None = typer.Option(None, parser=parse_date),
    end: datetime | None = typer.Option(None, parser=parse_date),
) -> None:
    conn, cursor = db_connect(user_database)

    where_prompts, parameters = search_builder(choice, start, end)

    query = "SELECT category, amount FROM history"

    if where_prompts:
        query += " WHERE " + " AND ".join(where_prompts)

    history = cursor.execute(query, parameters).fetchall()

    if not history:
        print("No Transactions")
    else:
        totals = add_up([(row[0], stored_amount(row[1])) for row in history])
        # Largest category first.
        for category, total in sorted(totals.items(), key=lambda item: item[1], reverse=True):
            print(f"{category.upper()}: ${total:.2f}")
        print(f"TOTAL: ${sum(totals.values()):.2f}")

    db_disconnect(conn)

@app.command()
def export_history(
    filename: str,
    choice: AllowedCategory | None = None,
    start: datetime | None = typer.Option(None, parser=parse_date),
    end: datetime | None = typer.Option(None, parser=parse_date),
) -> None:

    conn, cursor = db_connect(user_database)

    where_prompts, parameters = search_builder(choice, start, end)

    query = "SELECT * FROM history"
    if where_prompts:
        query += " WHERE " + " AND ".join(where_prompts)
    query += " ORDER BY date DESC"

    history = cursor.execute(query, parameters).fetchall()

        
    with open(filename, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames = ["amount", "category", "date", "note"])
        writer.writeheader()
        for row in history:
            writer.writerow({"amount": row[0], "category": row[1], "date": row[2], "note": row[3]})

    db_disconnect(conn)

@app.command()
def import_history(filename: Path) -> None:
    if not  filename.exists():
        print(f"Error: File '{filename}' does not exist.")
        raise typer.Exit(code=1)

    conn, cursor = db_connect(user_database)

    success_count = 0
    failed_rows = []

    # utf-8-sig reads plain UTF-8 and also drops a byte-order mark if there is one.
    with open(filename, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            current_line = reader.line_num
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

                cursor.execute("INSERT INTO history (amount, category, date, note) VALUES (?, ?, ?, ?)", (float(amount), raw_category, date_str, note))
                success_count += 1
            except (KeyError, ValueError, TypeError) as error:
                failed_rows.append(f"Line {current_line}: Error {error} | Data: {dict(row)}")
                continue

    db_disconnect(conn)
    print(f"Successfully Imported: {success_count} rows")
    print(f"Skipped (BAD INPUT): {len(failed_rows)} rows")
    if failed_rows:
        print("Failed Rows:")
        for failure in failed_rows:
            print(failure)

if __name__ == "__main__":
    app()