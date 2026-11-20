import csv
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

import finance_cli
from finance_cli import AllowedCategory, add_up, app, database_path, parse_amount, search_builder, stored_amount

runner = CliRunner()

SEPTEMBER_CSV = """amount,category,date,note
45.20,food_dining,2026-09-01,groceries
1200.00,rent,2026-09-01,September rent
abc,utilities,2026-09-02,bad amount
60.00,foood,2026-09-03,typo in category
35.99,subscriptions,2026-09-04,streaming
"""


@pytest.fixture(autouse=True)
def temp_database(tmp_path, monkeypatch):
    """Point every test at its own empty database inside pytest's temp folder."""
    monkeypatch.setenv("FINANCE_CLI_DB", str(tmp_path / "test.db"))


def run(*args):
    """Run one command the way a user would and return the result."""
    result = runner.invoke(app, [str(arg) for arg in args])
    # A crash shows up as an ordinary exception. A deliberate exit is SystemExit.
    assert not isinstance(result.exception, Exception), repr(result.exception)
    return result


def test_search_builder_no_filters():
    where_prompts, parameters = search_builder()
    assert where_prompts == []
    assert parameters == []

def test_search_builder_all_filters():
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 31)
    choice = AllowedCategory.RENT

    where_prompts, parameters = search_builder(choice=choice, start=start, end=end)
    assert where_prompts == ["category = ?", "date >= ?", "date <= ?"]
    assert parameters == ["rent", "2026-01-01", "2026-01-31"]

def test_search_builder_only_category():
    where_prompts, parameters = search_builder(choice=AllowedCategory.FOOD_DINING)
    assert where_prompts == ["category = ?"]
    assert parameters == ["food_dining"]

def test_search_builder_date_range_only():
    start = datetime(2026, 1, 1)
    end = datetime(2026, 2, 28)
    where_prompts, parameters = search_builder(start=start, end=end)
    assert where_prompts == ["date >= ?", "date <= ?"]
    assert parameters == ["2026-01-01", "2026-02-28"]

def test_search_builder_category_and_start_date():
    start = datetime(2026, 3, 15)
    where_prompts, parameters = search_builder(choice=AllowedCategory.TRANSPORTATION, start=start)
    assert where_prompts == ["category = ?", "date >= ?"]
    assert parameters == ["transportation", "2026-03-15"]

def test_search_builder_category_and_end_date():
    end = datetime(2026, 1, 15)
    where_prompts, parameters = search_builder(choice=AllowedCategory.TRANSPORTATION, end=end)
    assert where_prompts == ["category = ?", "date <= ?"]
    assert parameters == ["transportation", "2026-01-15"]



def test_add_then_pull_shows_the_transaction():
    added = run("add", "14.50", "food_dining", "--date", "2026-09-06", "--note", "Lunch burrito")
    assert added.exit_code == 0
    assert added.output == "Added: 2026-09-06 | FOOD_DINING | $14.50 | Note: Lunch burrito\n"
    added = run("add", "5", "food_dining", "--date", "2026-09-05")
    assert added.exit_code == 0
    assert added.output == "Added: 2026-09-05 | FOOD_DINING | $5.00\n"

    result = run("pull")
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "2026-09-06 | FOOD_DINING | $14.50 | Note: Lunch burrito",
        "2026-09-05 | FOOD_DINING | $5.00",
    ]


def test_add_without_date_uses_today():
    run("add", "3", "misc")
    today = datetime.now().strftime("%Y-%m-%d")
    assert run("pull").output.splitlines() == [f"{today} | MISC | $3.00"]


def test_add_rejects_unknown_category():
    result = run("add", "3", "groceries")
    assert result.exit_code != 0
    assert "No Transactions" in run("pull").output


def test_pull_on_empty_database():
    result = run("pull")
    assert result.exit_code == 0
    assert "No Transactions" in result.output


def test_pull_filters_by_category_and_date_range():
    run("add", "1200", "rent", "--date", "2026-09-01")
    run("add", "10", "food_dining", "--date", "2026-09-02")
    run("add", "20", "food_dining", "--date", "2026-09-10")
    run("add", "30", "food_dining", "--date", "2026-10-01")

    by_category = run("pull", "--category", "rent").output.splitlines()
    assert by_category == ["2026-09-01 | RENT | $1200.00"]

    by_range = run("pull", "--start", "2026-09-02", "--end", "2026-09-10").output.splitlines()
    assert by_range == [
        "2026-09-10 | FOOD_DINING | $20.00",
        "2026-09-02 | FOOD_DINING | $10.00",
    ]

    both = run("pull", "--category", "food_dining", "--start", "2026-09-05").output.splitlines()
    assert both == [
        "2026-10-01 | FOOD_DINING | $30.00",
        "2026-09-10 | FOOD_DINING | $20.00",
    ]


def test_summary_totals_per_category_largest_first():
    run("add", "10.25", "food_dining", "--date", "2026-09-02")
    run("add", "1200", "rent", "--date", "2026-09-01")
    run("add", "4.50", "food_dining", "--date", "2026-09-03")

    result = run("summary")
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "RENT: $1200.00",
        "FOOD_DINING: $14.75",
        "TOTAL: $1214.75",
    ]


def test_summary_respects_date_range():
    run("add", "10", "food_dining", "--date", "2026-09-02")
    run("add", "99", "food_dining", "--date", "2026-10-02")

    result = run("summary", "--start", "2026-09-01", "--end", "2026-09-30")
    assert result.output.splitlines() == ["FOOD_DINING: $10.00", "TOTAL: $10.00"]


def test_summary_on_empty_database():
    result = run("summary")
    assert result.exit_code == 0
    assert "No Transactions" in result.output


def test_export_writes_filtered_rows_to_csv(tmp_path):
    run("add", "1200", "rent", "--date", "2026-09-01", "--note", "September rent")
    run("add", "7.25", "food_dining", "--date", "2026-09-04", "--note", "coffee, large")
    target = tmp_path / "out.csv"

    result = run("export-history", target, "--category", "food_dining")
    assert result.exit_code == 0
    assert result.output == f"Exported 1 rows to {target}\n"

    with open(target, newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 1
    assert rows[0]["category"] == "food_dining"
    assert rows[0]["date"] == "2026-09-04"
    assert rows[0]["note"] == "coffee, large"
    assert rows[0]["amount"] == "7.25"


def test_export_writes_amounts_with_two_decimals(tmp_path):
    run("add", "14.5", "food_dining", "--date", "2026-09-06")
    run("add", "250", "rent", "--date", "2026-09-05")
    target = tmp_path / "out.csv"
    run("export-history", target)

    assert target.read_text().splitlines() == [
        "amount,category,date,note",
        "14.50,food_dining,2026-09-06,",
        "250.00,rent,2026-09-05,",
    ]


def test_import_skips_bad_rows_and_reports_them(tmp_path):
    source = tmp_path / "september.csv"
    source.write_text(SEPTEMBER_CSV)

    result = run("import-history", source)
    assert result.exit_code == 0
    assert "Successfully Imported: 3 rows" in result.output
    assert "Skipped (BAD INPUT): 2 rows" in result.output
    assert "Line 4:" in result.output
    assert "Line 5: Error Incorrect Category" in result.output

    assert run("pull").output.splitlines() == [
        "2026-09-04 | SUBSCRIPTIONS | $35.99 | Note: streaming",
        "2026-09-01 | FOOD_DINING | $45.20 | Note: groceries",
        "2026-09-01 | RENT | $1200.00 | Note: September rent",
    ]


def test_import_missing_file_exits_with_an_error(tmp_path):
    result = run("import-history", tmp_path / "nope.csv")
    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert result.stdout == ""


def test_export_then_import_round_trip(tmp_path, monkeypatch):
    run("add", "45.20", "food_dining", "--date", "2026-09-01", "--note", "groceries")
    run("add", "1200", "rent", "--date", "2026-09-01")
    before = run("pull").output
    exported = tmp_path / "backup.csv"
    run("export-history", exported)

    # Import the file into a second, empty database.
    monkeypatch.setenv("FINANCE_CLI_DB", str(tmp_path / "second.db"))
    assert "No Transactions" in run("pull").output
    assert "Successfully Imported: 2 rows" in run("import-history", exported).output
    assert run("pull").output == before


def test_import_row_without_a_note_field_is_imported(tmp_path):
    source = tmp_path / "short.csv"
    source.write_text(
        "amount,category,date,note\n"
        "45.20,food_dining,2026-09-01,groceries\n"
        "12.00,misc,2026-09-02\n"
        "35.99,subscriptions,2026-09-04,streaming\n"
    )

    result = run("import-history", source)
    assert result.exit_code == 0
    assert "Successfully Imported: 3 rows" in result.output
    assert "Skipped (BAD INPUT): 0 rows" in result.output
    assert "2026-09-02 | MISC | $12.00" in run("pull").output.splitlines()


def test_import_row_missing_a_required_field_is_skipped_and_the_rest_kept(tmp_path):
    source = tmp_path / "short.csv"
    source.write_text(
        "amount,category,date,note\n"
        "45.20,food_dining,2026-09-01,groceries\n"
        "12.00,misc\n"
        "35.99,subscriptions,2026-09-04,streaming\n"
    )

    result = run("import-history", source)
    assert result.exit_code == 0
    assert "Successfully Imported: 2 rows" in result.output
    assert "Skipped (BAD INPUT): 1 rows" in result.output
    assert "Line 3: Error Missing fields" in result.output
    assert run("pull").output.splitlines() == [
        "2026-09-04 | SUBSCRIPTIONS | $35.99 | Note: streaming",
        "2026-09-01 | FOOD_DINING | $45.20 | Note: groceries",
    ]


def test_import_accepts_a_file_with_a_byte_order_mark(tmp_path):
    # Excel's "CSV UTF-8" option puts a byte-order mark at the start of the file.
    source = tmp_path / "from_excel.csv"
    source.write_bytes(b"\xef\xbb\xbf" + SEPTEMBER_CSV.encode("utf-8"))

    result = run("import-history", source)
    assert "Successfully Imported: 3 rows" in result.output
    assert "Skipped (BAD INPUT): 2 rows" in result.output


def test_import_reports_rows_with_too_many_fields(tmp_path):
    source = tmp_path / "commas.csv"
    source.write_text(
        "amount,category,date,note\n"
        "7.25,food_dining,2026-09-04,coffee, large\n"
        '9.50,food_dining,2026-09-05,"tea, small"\n'
    )

    result = run("import-history", source)
    assert "Successfully Imported: 1 rows" in result.output
    assert "Skipped (BAD INPUT): 1 rows" in result.output
    assert "Line 2: Error Too many fields" in result.output
    # The quoted note keeps its comma. The unquoted row is not half imported.
    assert run("pull").output.splitlines() == ["2026-09-05 | FOOD_DINING | $9.50 | Note: tea, small"]


@pytest.mark.parametrize("text, expected", [
    ("12.50", Decimal("12.50")),
    ("12.5", Decimal("12.50")),
    ("7", Decimal("7.00")),
    (" 0.01 ", Decimal("0.01")),
    ("999999999.99", Decimal("999999999.99")),
])
def test_parse_amount_accepts_valid_amounts(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", [
    "nan", "NaN", "inf", "-inf", "Infinity", "1e400",
    "abc", "", " ", "$5", "1,200.00", "12.5.0",
    "0", "0.00", "-5", "-0.01",
    "1.999", "0.001",
    "1000000000",
])
def test_parse_amount_rejects_everything_else(text):
    with pytest.raises(ValueError):
        parse_amount(text)


@pytest.mark.parametrize("text", ["nan", "inf", "1e400", "abc", "0", "1.999", "-5"])
def test_add_rejects_invalid_amounts(text):
    # The "--" stops "-5" from being read as an option.
    result = run("add", "--", text, "rent")
    assert result.exit_code != 0
    assert "Invalid value for 'amount'" in result.output
    assert "No Transactions" in run("pull").output


def test_import_rejects_invalid_amounts_with_the_same_rules_as_add(tmp_path):
    source = tmp_path / "amounts.csv"
    source.write_text(
        "amount,category,date,note\n"
        "nan,rent,2026-09-01,\n"
        "inf,rent,2026-09-01,\n"
        "-5,rent,2026-09-01,\n"
        "0,rent,2026-09-01,\n"
        "1.999,rent,2026-09-01,\n"
        ",rent,2026-09-01,\n"
        "12.5,rent,2026-09-01,the only good row\n"
    )

    result = run("import-history", source)
    assert "Successfully Imported: 1 rows" in result.output
    assert "Skipped (BAD INPUT): 6 rows" in result.output
    assert "Line 2: Error 'nan' is not a number" in result.output
    assert "Line 4: Error Amount must be greater than zero" in result.output
    assert "Line 6: Error Amount can have at most two decimal places" in result.output
    assert run("pull").output.splitlines() == ["2026-09-01 | RENT | $12.50 | Note: the only good row"]


def test_add_normalises_a_date_without_leading_zeros():
    run("add", "5", "rent", "--date", "2026-9-1")
    assert run("pull").output.splitlines() == ["2026-09-01 | RENT | $5.00"]


@pytest.mark.parametrize("text", ["5", "20260901", "2026-13-01", "09/01/2026", "2026-09-01T10:00:00", "tomorrow"])
def test_add_rejects_dates_that_are_not_year_month_day(text):
    result = run("add", "5", "rent", "--date", text)
    assert result.exit_code != 0
    assert "Invalid value for '--date'" in result.output
    assert "No Transactions" in run("pull").output


@pytest.mark.parametrize("command", ["pull", "summary"])
@pytest.mark.parametrize("option", ["--start", "--end"])
def test_filters_reject_bad_dates(command, option):
    result = run(command, option, "20260901")
    assert result.exit_code != 0
    assert f"Invalid value for '{option}'" in result.output


def test_import_rejects_bad_dates(tmp_path):
    source = tmp_path / "dates.csv"
    source.write_text(
        "amount,category,date,note\n"
        "5,rent,09/01/2026,\n"
        "5,rent,2026-02-30,\n"
        "5,rent,2026-9-1,\n"
    )

    result = run("import-history", source)
    assert "Successfully Imported: 1 rows" in result.output
    assert "Line 2: Error '09/01/2026' is not a date written as YYYY-MM-DD" in result.output
    assert "Line 3: Error '2026-02-30' is not a date written as YYYY-MM-DD" in result.output
    assert run("pull").output.splitlines() == ["2026-09-01 | RENT | $5.00"]


def test_stored_amount_gives_back_what_was_entered():
    assert stored_amount(float(Decimal("0.10"))) == Decimal("0.10")
    assert stored_amount(float(Decimal("999999999.99"))) == Decimal("999999999.99")
    assert stored_amount(None) == Decimal("0.00")


def test_add_up_is_exact_where_floats_are_not():
    assert 0.10 + 0.20 != 0.30  # the reason the sums are not done with floats

    totals = add_up([
        ("misc", stored_amount(0.10)),
        ("misc", stored_amount(0.20)),
        ("rent", stored_amount(1200.0)),
    ])
    assert totals == {"misc": Decimal("0.30"), "rent": Decimal("1200.00")}
    assert sum(totals.values()) == Decimal("1200.30")


def test_summary_adds_many_small_amounts_exactly(tmp_path):
    source = tmp_path / "dimes.csv"
    source.write_text("amount,category,date,note\n" + "0.10,misc,2026-09-01,\n" * 1000 + "0.01,misc,2026-09-01,\n")
    run("import-history", source)

    assert run("summary").output.splitlines() == ["MISC: $100.01", "TOTAL: $100.01"]


def make_old_database(path, rows):
    """Build a database the way the first version of the tool created it."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE history (amount REAL, category TEXT, date TEXT, note TEXT)")
    conn.executemany("INSERT INTO history (amount, category, date, note) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_summary_survives_a_null_amount_left_by_an_old_version(tmp_path):
    # Before amounts were validated, "add nan" stored NULL.
    make_old_database(tmp_path / "test.db", [
        (None, "rent", "2026-09-01", "was nan"),
        (10.0, "rent", "2026-09-02", ""),
    ])

    assert run("summary").output.splitlines() == ["RENT: $10.00", "TOTAL: $10.00"]


def test_pull_survives_a_null_amount_left_by_an_old_version(tmp_path):
    make_old_database(tmp_path / "test.db", [(None, "rent", "2026-09-01", "was nan")])

    assert run("pull").output.splitlines() == ["2026-09-01 | RENT | $0.00 | Note: was nan"]


def test_rows_on_the_same_date_come_out_in_the_order_they_were_added():
    for note in ["first", "second", "third"]:
        run("add", "1", "misc", "--date", "2026-09-04", "--note", note)

    assert run("pull").output.splitlines() == [
        "2026-09-04 | MISC | $1.00 | Note: first",
        "2026-09-04 | MISC | $1.00 | Note: second",
        "2026-09-04 | MISC | $1.00 | Note: third",
    ]


@pytest.fixture
def no_override(tmp_path, monkeypatch):
    """No environment variable, an empty current folder and a pretend home folder."""
    monkeypatch.delenv("FINANCE_CLI_DB")
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(work)
    return home, work


def test_environment_variable_chooses_the_database_file(tmp_path, monkeypatch):
    chosen = tmp_path / "somewhere" / "else" / "money.db"
    monkeypatch.setenv("FINANCE_CLI_DB", str(chosen))

    assert database_path() == chosen
    assert run("add", "5", "rent", "--date", "2026-09-01").exit_code == 0
    assert chosen.exists()


def test_default_database_is_in_the_home_folder_not_the_current_one(no_override):
    home, work = no_override

    assert database_path() == home / ".finance-cli" / "transactions.db"
    run("add", "5", "rent", "--date", "2026-09-01")
    assert (home / ".finance-cli" / "transactions.db").exists()
    assert list(work.iterdir()) == []


def test_default_database_is_the_same_from_any_folder(no_override, tmp_path, monkeypatch):
    run("add", "5", "rent", "--date", "2026-09-01")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert run("pull").output.splitlines() == ["2026-09-01 | RENT | $5.00"]


def test_existing_database_in_the_current_folder_keeps_working(no_override):
    home, work = no_override
    make_old_database(work / "transactions.db", [(1200.0, "rent", "2026-09-01", "September rent")])

    assert database_path() == Path("transactions.db")
    assert run("pull").output.splitlines() == ["2026-09-01 | RENT | $1200.00 | Note: September rent"]
    run("add", "5", "misc", "--date", "2026-09-02")
    assert len(run("pull").output.splitlines()) == 2
    assert list(home.iterdir()) == []


def test_environment_variable_wins_over_a_database_in_the_current_folder(no_override, tmp_path, monkeypatch):
    home, work = no_override
    make_old_database(work / "transactions.db", [(1200.0, "rent", "2026-09-01", "")])
    monkeypatch.setenv("FINANCE_CLI_DB", str(tmp_path / "chosen.db"))

    assert "No Transactions" in run("pull").output


def test_new_database_has_a_primary_key(tmp_path):
    run("add", "5", "rent", "--date", "2026-09-01")
    run("add", "6", "rent", "--date", "2026-09-02")

    conn = sqlite3.connect(tmp_path / "test.db")
    columns = conn.execute("PRAGMA table_info(history)").fetchall()
    ids = conn.execute("SELECT id FROM history ORDER BY id").fetchall()
    conn.close()

    # Each PRAGMA row is (position, name, type, not null, default, primary key).
    assert [column[1] for column in columns] == ["id", "amount", "category", "date", "note"]
    assert [column[1] for column in columns if column[5]] == ["id"]
    assert ids == [(1,), (2,)]


@pytest.mark.parametrize("bad_row", [
    (None, "rent", "2026-09-01", ""),
    (0.0, "rent", "2026-09-01", ""),
    (-5.0, "rent", "2026-09-01", ""),
    (float("inf"), "rent", "2026-09-01", ""),
    (float("nan"), "rent", "2026-09-01", ""),
    (5.0, None, "2026-09-01", ""),
    (5.0, "rent", None, ""),
    (5.0, "rent", "yesterday", ""),
    (5.0, "rent", "2026-09-01", None),
])
def test_new_database_refuses_bad_rows_by_itself(tmp_path, bad_row):
    run("pull")  # creates the database

    conn = sqlite3.connect(tmp_path / "test.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO history (amount, category, date, note) VALUES (?, ?, ?, ?)", bad_row)
    conn.close()


def test_database_from_an_old_version_keeps_working_unchanged(tmp_path):
    make_old_database(tmp_path / "test.db", [
        (1200.0, "rent", "2026-09-01", "September rent"),
        (14.5, "food_dining", "2026-09-06", ""),
    ])

    assert run("add", "7.25", "food_dining", "--date", "2026-09-04", "--note", "coffee").exit_code == 0
    assert run("pull").output.splitlines() == [
        "2026-09-06 | FOOD_DINING | $14.50",
        "2026-09-04 | FOOD_DINING | $7.25 | Note: coffee",
        "2026-09-01 | RENT | $1200.00 | Note: September rent",
    ]
    assert run("summary").output.splitlines() == [
        "RENT: $1200.00",
        "FOOD_DINING: $21.75",
        "TOTAL: $1221.75",
    ]
    exported = tmp_path / "out.csv"
    run("export-history", exported)
    assert len(exported.read_text().splitlines()) == 4

    source = tmp_path / "more.csv"
    source.write_text("amount,category,date,note\n9.99,subscriptions,2026-09-04,spotify\n")
    assert "Successfully Imported: 1 rows" in run("import-history", source).output

    # The old table is left exactly as it was: no id column was added.
    conn = sqlite3.connect(tmp_path / "test.db")
    columns = conn.execute("PRAGMA table_info(history)").fetchall()
    conn.close()
    assert [column[1] for column in columns] == ["amount", "category", "date", "note"]


def test_importing_the_same_file_twice_does_not_double_anything(tmp_path):
    source = tmp_path / "september.csv"
    source.write_text(SEPTEMBER_CSV)

    first = run("import-history", source)
    assert "Successfully Imported: 3 rows" in first.output
    assert "Skipped (ALREADY STORED): 0 rows" in first.output
    pulled = run("pull").output

    second = run("import-history", source)
    assert "Successfully Imported: 0 rows" in second.output
    assert "Skipped (ALREADY STORED): 3 rows" in second.output
    assert "Skipped (BAD INPUT): 2 rows" in second.output
    assert run("pull").output == pulled


def test_export_then_import_into_the_same_database_does_not_double_anything(tmp_path):
    run("add", "45.20", "food_dining", "--date", "2026-09-01", "--note", "groceries")
    run("add", "1200", "rent", "--date", "2026-09-01")
    summary = run("summary").output
    exported = tmp_path / "backup.csv"
    run("export-history", exported)

    assert "Skipped (ALREADY STORED): 2 rows" in run("import-history", exported).output
    assert run("summary").output == summary


def test_import_keeps_identical_rows_that_are_inside_one_file(tmp_path):
    # Two coffees at the same price on the same day are two real purchases.
    source = tmp_path / "coffee.csv"
    source.write_text(
        "amount,category,date,note\n"
        "7.25,food_dining,2026-09-04,coffee\n"
        "7.25,food_dining,2026-09-04,coffee\n"
        "7.25,food_dining,2026-09-04,second coffee\n"
    )

    result = run("import-history", source)
    assert "Successfully Imported: 3 rows" in result.output
    assert "Skipped (ALREADY STORED): 0 rows" in result.output
    assert run("summary").output.splitlines() == ["FOOD_DINING: $21.75", "TOTAL: $21.75"]


# Expected failures: one "Error: ..." line on stderr and exit code 1. The run()
# helper already fails the test if a command ends with a traceback instead.

def test_import_of_a_folder_exits_with_an_error(tmp_path):
    result = run("import-history", tmp_path)
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: Cannot read")


def test_import_of_a_file_that_is_not_text_exits_with_an_error(tmp_path):
    source = tmp_path / "photo.csv"
    source.write_bytes(b"\xff\xd8\xff\xe0 not text at all \x00\x01")

    result = run("import-history", source)
    assert result.exit_code == 1
    assert "is not a UTF-8 CSV file" in result.stderr
    assert "No Transactions" in run("pull").output


def test_import_stops_when_text_breaks_halfway_and_stores_nothing(tmp_path):
    # Two good rows, then bytes that are not UTF-8 (a file saved in an older Windows encoding).
    source = tmp_path / "latin1.csv"
    source.write_bytes(
        b"amount,category,date,note\n"
        b"5,rent,2026-09-01,ok\n"
        b"6,rent,2026-09-02,ok\n"
        b"7,food_dining,2026-09-03,caf\xe9\n"
    )

    result = run("import-history", source)
    assert result.exit_code == 1
    assert "is not a UTF-8 CSV file" in result.stderr
    assert "No Transactions" in run("pull").output


@pytest.mark.parametrize("content", [
    "",
    "45.20,food_dining,2026-09-01,groceries\n",
    "amount,category,when,note\n45.20,food_dining,2026-09-01,groceries\n",
])
def test_import_without_the_header_line_exits_with_an_error(tmp_path, content):
    source = tmp_path / "no_header.csv"
    source.write_text(content)

    result = run("import-history", source)
    assert result.exit_code == 1
    assert "The first line must be: amount,category,date,note" in result.stderr
    assert "No Transactions" in run("pull").output


def test_import_works_without_a_note_column(tmp_path):
    source = tmp_path / "three_columns.csv"
    source.write_text("amount,category,date\n5,rent,2026-09-01\n")

    assert "Successfully Imported: 1 rows" in run("import-history", source).output
    assert run("pull").output.splitlines() == ["2026-09-01 | RENT | $5.00"]


def test_export_to_a_folder_that_does_not_exist_exits_with_an_error(tmp_path):
    run("add", "5", "rent", "--date", "2026-09-01")

    result = run("export-history", tmp_path / "missing" / "out.csv")
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: Cannot write")


@pytest.mark.parametrize("command", [
    ["add", "5", "rent"],
    ["pull"],
    ["summary"],
    ["export-history", "out.csv"],
])
def test_file_that_is_not_a_database_exits_with_an_error(tmp_path, monkeypatch, command):
    not_a_database = tmp_path / "notes.txt"
    not_a_database.write_text("this is a text file, not a SQLite database\n" * 10)
    monkeypatch.setenv("FINANCE_CLI_DB", str(not_a_database))
    monkeypatch.chdir(tmp_path)

    result = run(*command)
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: Cannot use database")
    assert not (tmp_path / "out.csv").exists()


def test_database_path_that_cannot_be_created_exits_with_an_error(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a folder is needed")
    monkeypatch.setenv("FINANCE_CLI_DB", str(blocker / "transactions.db"))

    result = run("pull")
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: Cannot open database")


@pytest.fixture
def connections(monkeypatch):
    """Remember every connection the commands open, so a test can check they were closed."""
    opened = []
    real_connect = sqlite3.connect

    def remembering_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", remembering_connect)
    return opened


def is_closed(conn):
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:  # "Cannot operate on a closed database."
        return True
    return False


def test_every_command_closes_its_connection(tmp_path, connections):
    source = tmp_path / "september.csv"
    source.write_text(SEPTEMBER_CSV)

    run("add", "5", "rent")
    run("pull")
    run("summary")
    run("export-history", tmp_path / "out.csv")
    run("import-history", source)

    assert len(connections) == 5
    assert all(is_closed(conn) for conn in connections)


def test_connection_is_closed_when_the_file_is_not_a_database(tmp_path, monkeypatch, connections):
    not_a_database = tmp_path / "notes.txt"
    not_a_database.write_text("this is a text file, not a SQLite database\n" * 10)
    monkeypatch.setenv("FINANCE_CLI_DB", str(not_a_database))

    assert run("pull").exit_code == 1
    assert len(connections) == 1
    assert is_closed(connections[0])


@pytest.mark.parametrize("command", [["pull"], ["summary"], ["export-history", "out.csv"]])
def test_connection_is_closed_when_a_command_crashes_halfway(tmp_path, monkeypatch, connections, command):
    def broken_fetch(*args, **kwargs):
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(finance_cli, "fetch_history", broken_fetch)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, command)
    assert isinstance(result.exception, RuntimeError)
    assert len(connections) == 1
    assert is_closed(connections[0])


def test_failed_import_row_is_not_left_half_written(tmp_path, monkeypatch, connections):
    # A crash in the middle of an import closes the connection without a commit,
    # so none of the rows from that run are kept.
    source = tmp_path / "september.csv"
    source.write_text(SEPTEMBER_CSV)
    real_parse_date = finance_cli.parse_date

    def parse_date_with_a_bug(text):
        if text == "2026-09-04":
            raise RuntimeError("simulated bug")
        return real_parse_date(text)

    monkeypatch.setattr(finance_cli, "parse_date", parse_date_with_a_bug)
    result = runner.invoke(app, ["import-history", str(source)])
    assert isinstance(result.exception, RuntimeError)
    assert is_closed(connections[0])

    monkeypatch.setattr(finance_cli, "parse_date", real_parse_date)
    assert "No Transactions" in run("pull").output


def test_export_refuses_to_replace_an_existing_file(tmp_path):
    run("add", "5", "rent", "--date", "2026-09-01")
    target = tmp_path / "out.csv"
    target.write_text("something I did not want to lose\n")

    result = run("export-history", target)
    assert result.exit_code == 1
    assert "already exists. Add --overwrite to replace it." in result.stderr
    assert target.read_text() == "something I did not want to lose\n"


def test_export_replaces_an_existing_file_with_overwrite(tmp_path):
    run("add", "5", "rent", "--date", "2026-09-01")
    target = tmp_path / "out.csv"
    target.write_text("old contents\n")

    assert run("export-history", target, "--overwrite").exit_code == 0
    assert target.read_text().splitlines() == ["amount,category,date,note", "5.00,rent,2026-09-01,"]


def test_old_choice_option_is_gone():
    result = run("pull", "--choice", "rent")
    assert result.exit_code == 2
    assert "No such option: --choice" in result.output
