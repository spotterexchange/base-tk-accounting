import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


@dataclass
class ParsedPayout:
    marketplace_order_id: str
    amount: float
    payment_date: Optional[str]
    raw_data: dict
    notes: Optional[str] = None
    payment_type: Optional[str] = None
    is_parking: bool = False


@dataclass
class ParseResult:
    marketplace_name: str
    filename: str
    file_hash: str
    rows: list[ParsedPayout]
    skipped: list[dict]
    errors: list[str]


def _read_csv(content: bytes) -> list[dict]:
    # Marketplace exports arrive in whatever encoding the exporter felt like:
    # UTF-16 (null bytes), UTF-8 with/without BOM, or Windows-1252 (Viagogo
    # files with accented names / non-breaking spaces). cp1252 maps every
    # byte, so the fallback always decodes rather than 500ing the import.
    if b"\x00" in content[:50]:
        text = content.decode("utf-16")
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1252")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if r and any(c.strip() for c in r)]
    if not rows:
        return []
    headers = [h.strip() for h in rows[0]]
    return [dict(zip(headers, [c.strip() for c in r])) for r in rows[1:]]


def _money(x: str) -> float:
    return float(str(x).replace("$", "").replace(",", "").strip() or "0")


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parking(raw: dict) -> bool:
    return any("parking" in str(v).lower() for v in raw.values())


def _parse_date_yyyymmdd(s: str) -> Optional[str]:
    try:
        return datetime.strptime(s.strip(), "%Y%m%d").strftime("%Y-%m-%d")
    except Exception:
        return None


def _infer_year_mmdd(mmdd: str) -> Optional[str]:
    """Parse MMDD and infer year: current year unless that date is in the future, then prior year."""
    try:
        today = date.today()
        candidate = date(today.year, int(mmdd[:2]), int(mmdd[2:]))
        if candidate > today:
            candidate = date(today.year - 1, int(mmdd[:2]), int(mmdd[2:]))
        return candidate.strftime("%Y-%m-%d")
    except Exception:
        return None


def _parse_date_mmddyy(s: str) -> Optional[str]:
    try:
        s = s.strip()
        mm, dd, yy = s[:2], s[2:4], s[4:6]
        year = 2000 + int(yy)
        return date(year, int(mm), int(dd)).strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_viagogo(content: bytes, filename: str) -> ParseResult:
    rows = _read_csv(content)
    parsed, skipped = [], []
    for r in rows:
        tid = r.get("TransactionID", "").strip()
        if not tid.isdigit():
            skipped.append({"row": r, "reason": f"TransactionID='{tid}' is not numeric"})
            continue
        desc = r.get("Description", "").strip()
        proceeds = _money(r.get("Proceeds", "0"))
        charges  = _money(r.get("Charges", "0"))
        credit   = _money(r.get("Credit", "0"))
        if charges:
            amount = -abs(charges)
            payment_type = "Adjustment"
        elif credit:
            amount = credit
            payment_type = "Adjustment"
        else:
            amount = proceeds
            payment_type = "Payment"
        parsed.append(ParsedPayout(
            marketplace_order_id=tid,
            amount=amount,
            payment_date=None,
            raw_data=r,
            notes=desc or None,
            payment_type=payment_type,
            is_parking=_parking(r),
        ))
    return ParseResult("Viagogo", filename, _hash(content), parsed, skipped, [])


def parse_seatgeek(content: bytes, filename: str) -> ParseResult:
    rows = _read_csv(content)
    parsed, skipped = [], []
    for r in rows:
        oid = r.get("Order ID", "").strip()
        if not oid:
            skipped.append({"row": r, "reason": "blank Order ID"})
            continue
        row_type = r.get("Type", "").strip()
        reason = r.get("Reason", "").strip() or None
        payment_type = "Adjustment" if row_type and row_type != "Order Payment" else "Payment"
        parsed.append(ParsedPayout(
            marketplace_order_id=oid,
            amount=_money(r.get("Amount", "0")),
            payment_date=(r.get("Effective Date", "").strip() or "")[:10] or None,
            raw_data=r,
            notes=reason,
            payment_type=payment_type,
            is_parking=_parking(r),
        ))
    return ParseResult("SeatGeek", filename, _hash(content), parsed, skipped, [])


def parse_lysted(content: bytes, filename: str) -> ParseResult:
    rows = _read_csv(content)
    parsed, skipped = [], []
    for r in rows:
        ref = r.get("Reference", "").strip()
        if not ref:
            skipped.append({"row": r, "reason": "blank Reference"})
            continue
        sold_to = r.get("Sold To", "").strip() or None
        # Adjustment/credit/cancellation rows (Sale ID prefixed "adj") only
        # populate "Payout", not "Total Payout" - fall back to it so these
        # don't silently parse as $0.
        is_adjustment = r.get("Sale ID", "").strip().lower().startswith("adj")
        amount = _money(r.get("Total Payout", "0")) or _money(r.get("Payout", "0"))
        parsed.append(ParsedPayout(
            marketplace_order_id=ref,
            amount=amount,
            payment_date=None,
            raw_data=r,
            notes=sold_to,
            payment_type="Adjustment" if is_adjustment else "Payment",
            is_parking=_parking(r),
        ))
    return ParseResult("Lysted", filename, _hash(content), parsed, skipped, [])


def parse_gametime(content: bytes, filename: str) -> ParseResult:
    rows = _read_csv(content)
    parsed, skipped = [], []
    # Parse "to" date from filename: gametime-payout-{YYYYMMDD}-{YYYYMMDD}.csv
    payment_date = None
    m = re.search(r"gametime-payout-\d{8}-(\d{8})", filename, re.IGNORECASE)
    if m:
        payment_date = _parse_date_yyyymmdd(m.group(1))
    for r in rows:
        oid = r.get("#", "").strip()
        if not oid:
            skipped.append({"row": r, "reason": "blank order ID (summary row)"})
            continue
        reason = r.get("Reason", "").strip() or None
        parsed.append(ParsedPayout(
            marketplace_order_id=oid,
            amount=_money(r.get("Payout", "0")),
            payment_date=payment_date,
            raw_data=r,
            notes=reason,
            payment_type="Adjustment" if reason else "Payment",
            is_parking=_parking(r),
        ))
    return ParseResult("Gametime", filename, _hash(content), parsed, skipped, [])


def parse_tickpick(content: bytes, filename: str) -> ParseResult:
    rows = _read_csv(content)
    parsed, skipped = [], []
    # Parse date from filename suffix: remittance_tickpick {MMDDYY}.csv
    payment_date = None
    m = re.search(r"remittance_tickpick[\s_](\d{6})\.csv", filename, re.IGNORECASE)
    if m:
        payment_date = _parse_date_mmddyy(m.group(1))
    for r in rows:
        oid = r.get("Order Number", "").strip()
        if not oid.isdigit():
            skipped.append({"row": r, "reason": f"Order Number='{oid}' is not numeric (footer row)"})
            continue
        parsed.append(ParsedPayout(
            marketplace_order_id=oid,
            amount=_money(r.get("Payout", "0")),
            payment_date=payment_date,
            raw_data=r,
            notes=None,
            payment_type="Payment",
            is_parking=_parking(r),
        ))
    return ParseResult("TickPick", filename, _hash(content), parsed, skipped, [])


def parse_gotickets(content: bytes, filename: str) -> ParseResult:
    """Each raw CSV line becomes its own payout row (no netting across lines).

    A line with real Qty/Section/Row is an actual per-seat sale ('Payment').
    A line with those fields blank is a correction/adjustment tied to a prior
    payout (positive or negative) and is classified 'Adjustment' instead.
    """
    rows = _read_csv(content)
    payment_date = None
    m = re.search(r"gotickets_(\d{4})\.csv", filename, re.IGNORECASE)
    if m:
        payment_date = _infer_year_mmdd(m.group(1))
    parsed, skipped = [], []
    for r in rows:
        oid = r.get("Order ID", "").strip()
        if not oid or not oid.isdigit():
            skipped.append({"row": r, "reason": f"Order ID='{oid}' is not numeric"})
            continue
        is_sale_line = bool(r.get("Qty", "").strip() or r.get("Section", "").strip() or r.get("Row", "").strip())
        parsed.append(ParsedPayout(
            marketplace_order_id=oid,
            amount=_money(r.get("Amount", "0")),
            payment_date=payment_date,
            raw_data=r,
            notes=None,
            payment_type="Payment" if is_sale_line else "Adjustment",
            is_parking=_parking(r),
        ))
    return ParseResult("GoTickets", filename, _hash(content), parsed, skipped, [])


def parse_ticketnetwork(content: bytes, filename: str) -> ParseResult:
    rows = _read_csv(content)
    parsed, skipped = [], []
    for r in rows:
        oid = r.get("ORDER ID", "").strip()
        if not oid.isdigit():
            skipped.append({"row": r, "reason": f"ORDER ID='{oid}' is not numeric"})
            continue
        parsed.append(ParsedPayout(
            marketplace_order_id=oid,
            amount=_money(r.get("TOTAL", "0")),
            payment_date=None,
            raw_data=r,
            notes=None,
            payment_type="Payment",
            is_parking=_parking(r),
        ))
    return ParseResult("TicketNetwork", filename, _hash(content), parsed, skipped, [])


def parse_tevo(content: bytes, filename: str) -> ParseResult:
    """Tevo has shipped (at least) two export formats:
    - the older one has State and Credit columns; State=='Completed' marks a
      normal payment, anything else is an adjustment
    - the newer remittance export has Credit but NO State column at all - those
      rows are normal completed payments, not adjustments (negative amounts
      still get reclassified to Adjustment by the global rule in parse_file)
    A third Tevo report type has no Credit column at all - importing it would
    silently produce all-$0.00 rows, so those rows are skipped loudly instead."""
    rows = _read_csv(content)
    parsed, skipped = [], []
    for r in rows:
        oid = r.get("ID", "").strip()
        if not oid:
            skipped.append({"row": r, "reason": "blank ID"})
            continue
        if "Credit" not in r:
            skipped.append({"row": r, "reason": "no Credit column - wrong Tevo report type (would import as $0.00)"})
            continue
        credit = _money(r.get("Credit", "0"))
        amount = round(credit * 0.97, 2)
        if "State" in r:
            state = r.get("State", "").strip()
            payment_type = "Payment" if state == "Completed" else "Adjustment"
            notes = state if state and state != "Completed" else None
        else:
            payment_type = "Payment"
            notes = None
        parsed.append(ParsedPayout(
            marketplace_order_id=oid,
            amount=amount,
            payment_date=None,
            raw_data=r,
            notes=notes,
            payment_type=payment_type,
            is_parking=_parking(r),
        ))
    return ParseResult("Tevo", filename, _hash(content), parsed, skipped, [])


def parse_b2b(content: bytes, filename: str) -> ParseResult:
    rows = _read_csv(content)
    parsed, skipped = [], []
    for r in rows:
        sale_id = r.get("Sale Id", "").strip()
        if not sale_id:
            skipped.append({"row": r, "reason": "blank Sale Id"})
            continue
        direction = r.get("Direction", "").strip().lower()
        parsed.append(ParsedPayout(
            marketplace_order_id=sale_id,
            amount=_money(r.get("Net Payout", "0")),
            payment_date=None,
            raw_data=r,
            notes=direction if direction and direction != "credit" else None,
            payment_type="Payment" if direction == "credit" else "Adjustment",
            is_parking=_parking(r),
        ))
    return ParseResult("B2B", filename, _hash(content), parsed, skipped, [])


def detect_marketplace(filename: str) -> Optional[str]:
    name = filename.lower()
    if name.startswith("viagogo ") or name.startswith("viagogo_"):
        return "Viagogo"
    if name.startswith("sp_") and name.endswith("_charges.csv"):
        return "SeatGeek"
    if name.startswith("lysted-remit-"):
        return "Lysted"
    if name.startswith("gametime-payout-"):
        return "Gametime"
    if name.startswith("remittance_tickpick"):
        return "TickPick"
    if name.startswith("gotickets_"):
        return "GoTickets"
    if name.startswith("tn_fulfillment_remittance_"):
        return "TicketNetwork"
    if name.startswith("tevo_orders_"):
        return "Tevo"
    if name.startswith("b2b_settlement_transactions-"):
        return "B2B"
    return None


PARSERS = {
    "Viagogo": parse_viagogo,
    "SeatGeek": parse_seatgeek,
    "Lysted": parse_lysted,
    "Gametime": parse_gametime,
    "TickPick": parse_tickpick,
    "GoTickets": parse_gotickets,
    "TicketNetwork": parse_ticketnetwork,
    "Tevo": parse_tevo,
    "B2B": parse_b2b,
}


def parse_file(content: bytes, filename: str) -> ParseResult:
    marketplace = detect_marketplace(filename)
    if not marketplace:
        return ParseResult(
            marketplace_name="Unknown",
            filename=filename,
            file_hash=_hash(content),
            rows=[],
            skipped=[],
            errors=[f"Could not detect marketplace from filename '{filename}'"],
        )
    result = PARSERS[marketplace](content, filename)
    # A genuine sale payout ('Payment') can never be negative — any marketplace's
    # parser that produces a negative amount is really describing a correction/
    # chargeback, so reclassify it as an Adjustment regardless of what the
    # per-marketplace logic above decided.
    for payout in result.rows:
        if payout.amount < 0:
            payout.payment_type = "Adjustment"
    return result
