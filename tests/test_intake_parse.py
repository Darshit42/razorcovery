import io

import pytest

from intake import parse


def test_normalize_indian_phone_forms():
    n = parse.normalize_indian_phone
    assert n("9876543210") == "+919876543210"
    assert n("+91 98765-43210") == "+919876543210"
    assert n("09876543210") == "+919876543210"
    assert n("919876543210") == "+919876543210"
    assert n("98765 4321 0") == "+919876543210"
    assert n("12345") is None            # too short
    assert n("1234567890") is None       # doesn't start 6-9
    assert n("+1 555 010 0100") is None  # not Indian
    assert n("") is None


CSV = (
    "customer,mobile,outstanding,failure_type,order_id\n"
    "Ravi Kumar,9811100011,4200,payment_retry,ord_1\n"
    "Asha Rao,08887776665,180.50,checkout,ord_2\n"
    "Bad Phone,123,999,payment,ord_3\n"
    "Ravi Kumar,9811100011,500,payment,ord_4\n"        # duplicate phone
    "No Amount,9844400044,,mandate,ord_5\n"
).encode()


def test_parse_csv_detects_columns_and_normalizes():
    r = parse.parse_sheet(CSV, "contacts.csv")
    assert r.total_rows == 5
    assert r.valid_count == 2
    names = [pr.customer_name for pr in r.valid_rows]
    assert names == ["Ravi Kumar", "Asha Rao"]
    assert r.valid_rows[0].phone == "+919811100011"
    assert r.valid_rows[1].phone == "+918887776665"
    assert r.valid_rows[1].amount_inr == 180              # rounded from 180.50
    assert r.valid_rows[0].failure_type == "payment_retry"
    assert r.valid_rows[1].failure_type == "checkout_abandonment"
    assert "+919811100011" in r.duplicate_phones


def test_parse_reports_row_errors_not_silent_drop():
    r = parse.parse_sheet(CSV, "contacts.csv")
    rows_with_err = {e.row for e in r.errors}
    assert 3 in rows_with_err   # bad phone
    assert 4 in rows_with_err   # duplicate
    assert 5 in rows_with_err   # missing amount
    assert any("Duplicate" in e.message for e in r.errors)
    assert any("amount" in (e.field or "") for e in r.errors)


def test_no_phone_column_is_a_hard_error():
    r = parse.parse_sheet(b"name,amount\nX,100\n", "x.csv")
    assert r.valid_count == 0
    assert any("phone" in (e.field or "") for e in r.errors)


def test_default_failure_type_override():
    r = parse.parse_sheet(
        b"name,phone,amount\nX,9811100011,2000\n", "x.csv",
        default_failure_type="mandate_failure",
    )
    assert r.valid_rows[0].failure_type == "mandate_failure"


def test_explicit_phone_column_mapping():
    csv = b"name,contact_no,amount\nX,9811100011,2000\n"
    auto = parse.parse_sheet(csv, "x.csv")
    assert auto.valid_count == 1  # 'contact_no' matches 'contact' substring
    mapped = parse.parse_sheet(csv, "x.csv", mapping={"phone": "contact_no"})
    assert mapped.valid_count == 1


def test_xlsx_round_trip():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["name", "phone", "amount", "type"])
    ws.append(["Neha", "9812345678", 3200, "payment"])
    ws.append(["Rohan", "9887654321", 500, "checkout"])
    buf = io.BytesIO()
    wb.save(buf)
    r = parse.parse_sheet(buf.getvalue(), "book.xlsx")
    assert r.valid_count == 2
    assert r.valid_rows[0].phone == "+919812345678"
    assert r.valid_rows[0].amount_inr == 3200
