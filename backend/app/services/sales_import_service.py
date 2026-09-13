import csv
import io
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import text


def _parse_date(v: str) -> str | None:
    """Parse dates like '5/9/2026 2:00:00 PM +00:00' or '2026-05-09T...' into YYYY-MM-DD."""
    v = v.strip()
    if not v:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p %z", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(v.split("+")[0].strip(), fmt.split("%z")[0].strip()).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # fallback: take just the date part before first space
    return v.split(" ")[0]


def _money(v: str) -> float:
    return float(str(v).replace("$", "").replace(",", "").strip() or "0")


def _parse_sales_csv(content: bytes) -> list[dict]:
    text_data = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text_data))
    return [dict(r) for r in reader]


def _get_or_create_purchaser(db: Session, name: str, cache: dict) -> int:
    name = name.strip()
    if name in cache:
        return cache[name]
    row = db.execute(
        text("SELECT purchaser_id FROM purchasers WHERE LOWER(name) = LOWER(:name)"),
        {"name": name}
    ).fetchone()
    if row:
        cache[name] = row[0]
        return row[0]
    new_id = db.execute(
        text("""
            INSERT INTO purchasers (name, type, default_commission_pct)
            VALUES (:name, 'broker', 0)
            RETURNING purchaser_id
        """),
        {"name": name}
    ).fetchone()[0]
    cache[name] = new_id
    return new_id


def _get_or_create_event(db: Session, performer: str, venue: str, event_date: str, cache: dict) -> int:
    key = (performer.strip().lower(), venue.strip().lower(), _parse_date(event_date) if event_date else "")
    if key in cache:
        return cache[key]
    row = db.execute(
        text("""
            SELECT event_id FROM events
            WHERE LOWER(performer) = LOWER(:performer)
              AND LOWER(venue) = LOWER(:venue)
              AND event_date = CAST(:event_date AS date)
        """),
        {"performer": performer, "venue": venue, "event_date": _parse_date(event_date)}
    ).fetchone()
    if row:
        cache[key] = row[0]
        return row[0]
    new_id = db.execute(
        text("""
            INSERT INTO events (performer, venue, event_date)
            VALUES (:performer, :venue, CAST(:event_date AS date))
            RETURNING event_id
        """),
        {"performer": performer.strip(), "venue": venue.strip(), "event_date": _parse_date(event_date)}
    ).fetchone()[0]
    cache[key] = new_id
    return new_id


def import_sales(db: Session, content: bytes) -> dict:
    rows = _parse_sales_csv(content)

    purchaser_cache: dict = {}
    event_cache: dict = {}

    created = 0
    skipped = 0
    matched_payouts = 0
    new_purchasers = []

    for r in rows:
        reachpro_sale_id = r.get("Sale Id", "").strip()
        if not reachpro_sale_id:
            skipped += 1
            continue

        # Skip if already imported
        existing = db.execute(
            text("SELECT sale_id FROM sales WHERE reachpro_sale_id = :id"),
            {"id": reachpro_sale_id}
        ).fetchone()
        if existing:
            skipped += 1
            continue

        # Event
        event_name = r.get("Event Name", "").strip()
        venue = r.get("Venue", "").strip()
        event_date = r.get("Event Date", "").strip()
        event_id = _get_or_create_event(db, event_name, venue, event_date, event_cache) if event_date else None

        # Fulfillment status
        if r.get("Cancellation Date", "").strip():
            fulfillment_status = "cancelled"
        elif r.get("Fulfillment Date", "").strip():
            fulfillment_status = "fulfilled"
        else:
            fulfillment_status = "pending"

        # Sale
        sale_date = _parse_date(r.get("Sale Date", ""))
        marketplace_order_id = r.get("External Sale Id", "").strip() or None
        quantity_str = r.get("Quantity Sold", "").strip()
        quantity = int(quantity_str) if quantity_str.isdigit() else None

        sale_id = db.execute(
            text("""
                INSERT INTO sales (
                    reachpro_sale_id, event_id, fulfillment_status,
                    cost, proceeds, pnl,
                    marketplace_order_id, sale_date, quantity,
                    reachpro_payment_id
                ) VALUES (
                    :rsid, :eid, :fs,
                    :cost, :proceeds, :pnl,
                    :moid, CAST(:sd AS timestamptz), :qty,
                    :rpid
                )
                RETURNING sale_id
            """),
            {
                "rsid": reachpro_sale_id,
                "eid": event_id,
                "fs": fulfillment_status,
                "cost": _money(r.get("Total Cost", "0")),
                "proceeds": _money(r.get("Total Net Proceeds", "0")),
                "pnl": _money(r.get("P & L", "0")),
                "moid": marketplace_order_id,
                "sd": sale_date,
                "qty": quantity,
                "rpid": r.get("Payment Reference Id", "").strip() or None,
            }
        ).fetchone()[0]

        # Purchasers — split on comma
        purchased_by = r.get("Purchased By", "").strip()
        purchaser_names = [p.strip() for p in purchased_by.split(",") if p.strip()]
        split_pct = round(1.0 / len(purchaser_names), 4) if purchaser_names else 1.0

        for name in purchaser_names:
            before_count = len(purchaser_cache)
            purchaser_id = _get_or_create_purchaser(db, name, purchaser_cache)
            if len(purchaser_cache) > before_count:
                new_purchasers.append(name)
            db.execute(
                text("""
                    INSERT INTO sale_purchasers (sale_id, purchaser_id, split_pct)
                    VALUES (:sid, :pid, :pct)
                    ON CONFLICT DO NOTHING
                """),
                {"sid": sale_id, "pid": purchaser_id, "pct": split_pct}
            )

        # Match to marketplace_payouts by marketplace_order_id
        if marketplace_order_id:
            result = db.execute(
                text("""
                    UPDATE marketplace_payouts
                    SET sale_id = :sale_id
                    WHERE marketplace_order_id = :moid
                      AND sale_id IS NULL
                """),
                {"sale_id": sale_id, "moid": marketplace_order_id}
            )
            matched_payouts += result.rowcount

        created += 1

    db.commit()

    return {
        "sales_created": created,
        "sales_skipped_duplicate": skipped,
        "payout_rows_matched": matched_payouts,
        "new_purchasers_created": list(set(new_purchasers)),
    }
