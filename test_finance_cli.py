import csv
from datetime import datetime
from decimal import Decimal

import pytest
from typer.testing import CliRunner

import finance_cli
from finance_cli import AllowedCategory, app, parse_amount, search_builder

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
    monkeypatch.setattr(finance_cli, "user_database", tmp_path / "test.db")


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
    assert run("add", "14.50", "food_dining", "--date", "2026-09-06", "--note", "Lunch burrito").exit_code == 0
    assert run("add", "5", "food_dining", "--date", "2026-09-05").exit_code == 0

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
    assert "No Transaction" in run("pull").output


def test_pull_on_empty_database():
    result = run("pull")
    assert result.exit_code == 0
    assert "No Transaction" in result.output


def test_pull_filters_by_category_and_date_range():
    run("add", "1200", "rent", "--date", "2026-09-01")
    run("add", "10", "food_dining", "--date", "2026-09-02")
    run("add", "20", "food_dining", "--date", "2026-09-10")
    run("add", "30", "food_dining", "--date", "2026-10-01")

    by_category = run("pull", "--choice", "rent").output.splitlines()
    assert by_category == ["2026-09-01 | RENT | $1200.00"]

    by_range = run("pull", "--start", "2026-09-02", "--end", "2026-09-10").output.splitlines()
    assert by_range == [
        "2026-09-10 | FOOD_DINING | $20.00",
        "2026-09-02 | FOOD_DINING | $10.00",
    ]

    both = run("pull", "--choice", "food_dining", "--start", "2026-09-05").output.splitlines()
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

    assert run("export-history", target, "--choice", "food_dining").exit_code == 0

    with open(target, newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 1
    assert rows[0]["category"] == "food_dining"
    assert rows[0]["date"] == "2026-09-04"
    assert rows[0]["note"] == "coffee, large"
    assert float(rows[0]["amount"]) == 7.25


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
    assert "does not exist" in result.output


def test_export_then_import_round_trip(tmp_path, monkeypatch):
    run("add", "45.20", "food_dining", "--date", "2026-09-01", "--note", "groceries")
    run("add", "1200", "rent", "--date", "2026-09-01")
    before = run("pull").output
    exported = tmp_path / "backup.csv"
    run("export-history", exported)

    # Import the file into a second, empty database.
    monkeypatch.setattr(finance_cli, "user_database", tmp_path / "second.db")
    assert "No Transaction" in run("pull").output
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
    assert "No Transaction" in run("pull").output


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
    assert "No Transaction" in run("pull").output


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
