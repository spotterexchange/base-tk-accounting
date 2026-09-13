"""CrowdVold Sales Import.

Stages rows from the monthly CrowdVold sales workbook, maps CrowdVold events
to ReachPro events, previews per-row dispositions, and creates the approved
rows in ReachPro as Offline sales (marked Fulfilled). Creation is idempotent:
the CrowdVold order number is stored as the ReachPro marketplaceSaleId and
checked before every create, so re-runs and retries can never double-create.

Deliberately does NOT trigger the offline-sales sweep afterwards - the new
sales sit in ReachPro until someone runs Full Sync by hand.
"""
import io
import re
import time
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

import openpyxl
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import engine, get_db
from app.routers.sync import _api_call, _sweep_api_get

router = APIRouter(prefix="/cv-import", tags=["cv-import"])

_create_status: dict = {"running": False, "progress": None, "result": None, "error": None}

REQUIRED_HEADERS = {"Transaction date", "Order Number", "Event Name", "Event Date",
                    "Transaction Type", "Quantity", "Earnings"}


def _hdr_index(row) -> Optional[dict]:
    hdr = {str(c).strip(): i for i, c in enumerate(row) if c is not None and str(c).strip()}
    return hdr if REQUIRED_HEADERS.issubset(hdr.keys()) else None


@router.post("/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Parse every workbook tab that carries CrowdVold's sale columns
    (headers are matched by name, so the column drift between tabs is fine).
    'Sale' rows stage into cv_sales - already-seen order numbers are skipped,
    so overlapping or re-uploaded files are harmless. 'Cancelled order' rows
    mark their matching sale cancelled."""
    raw = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read workbook: {e}")

    inserted = existing = 0
    cancels: list[str] = []
    warnings: list[str] = []
    tabs = []
    for name in wb.sheetnames:
        ws = wb[name]
        rows = ws.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            continue
        ix = _hdr_index(header)
        if not ix:
            continue
        tab_sales = 0
        for r in rows:
            if r is None or ix["Order Number"] >= len(r):
                continue
            order = r[ix["Order Number"]]
            if order in (None, ""):
                continue
            order = str(order).strip()
            ttype = str(r[ix["Transaction Type"]] or "").strip()
            if ttype == "Cancelled order":
                cancels.append(order)
                continue
            if ttype != "Sale":
                continue
            qty = r[ix["Quantity"]]
            earnings = r[ix["Earnings"]]
            if not qty or qty < 1 or earnings is None:
                warnings.append(f"{name}: order {order} skipped (quantity={qty}, earnings={earnings})")
                continue
            def g(col):
                i = ix.get(col)
                return r[i] if i is not None and i < len(r) else None
            res = db.execute(text("""
                INSERT INTO cv_sales (order_number, source_tab, transaction_date, event_name,
                                      event_date, quantity, price, total_amount, seller_fees, earnings)
                VALUES (:o, :tab, :td, :en, :ed, :q, :p, :tot, :fees, :earn)
                ON CONFLICT (order_number) DO NOTHING
            """), {
                "o": order, "tab": name, "td": r[ix["Transaction date"]],
                "en": str(r[ix["Event Name"]] or "").strip(), "ed": r[ix["Event Date"]],
                "q": int(qty), "p": g("Price"), "tot": g("Total Transaction Amount"),
                "fees": g("Seller Fees"), "earn": float(earnings),
            })
            if res.rowcount:
                inserted += 1
            else:
                existing += 1
            tab_sales += 1
        tabs.append({"tab": name, "sale_rows": tab_sales})
    wb.close()
    if not tabs:
        raise HTTPException(status_code=400, detail="No tabs with CrowdVold sale columns found in this file.")

    cancelled_applied = 0
    for order in cancels:
        row = db.execute(text("SELECT status FROM cv_sales WHERE order_number = :o"), {"o": order}).fetchone()
        if not row:
            warnings.append(f"Cancelled order {order} has no matching sale row (in this file or previously staged)")
            continue
        if row.status == "created":
            warnings.append(f"Cancelled order {order} was ALREADY created in ReachPro - needs manual attention")
            continue
        if row.status != "cancelled":
            db.execute(text("""
                UPDATE cv_sales SET status = 'cancelled',
                    status_note = 'Cancelled order row in upload'
                WHERE order_number = :o
            """), {"o": order})
            cancelled_applied += 1
    db.commit()
    return {"tabs": tabs, "inserted": inserted, "already_staged": existing,
            "cancellations_applied": cancelled_applied, "warnings": warnings}


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, re.sub(r"\W+", " ", a.lower()), re.sub(r"\W+", " ", b.lower())).ratio()


@router.get("/events")
def list_events(db: Session = Depends(get_db)):
    """Distinct CrowdVold events with row counts, saved mapping (if any), and
    auto-suggestions from our events table (same-day, best name similarity)."""
    events = db.execute(text("""
        SELECT cv.event_name, cv.event_date::date AS event_date,
               COUNT(*) AS rows, SUM(cv.quantity) AS tickets, SUM(cv.earnings) AS earnings,
               COUNT(*) FILTER (WHERE cv.status = 'created') AS created_rows,
               m.reachpro_event_id, m.reachpro_event_name, m.reachpro_event_date, m.reachpro_venue, m.skip
        FROM cv_sales cv
        LEFT JOIN cv_event_mappings m
          ON m.cv_event_name = cv.event_name AND m.cv_event_date = cv.event_date::date
        GROUP BY cv.event_name, cv.event_date::date, m.reachpro_event_id, m.reachpro_event_name,
                 m.reachpro_event_date, m.reachpro_venue, m.skip
        ORDER BY cv.event_date::date
    """)).fetchall()
    known = db.execute(text("""
        SELECT reachpro_event_id, performer, venue, event_date FROM events
        WHERE reachpro_event_id IS NOT NULL
    """)).fetchall()
    out = []
    for e in events:
        item = dict(e._mapping)
        if item["reachpro_event_id"] is None and not item["skip"]:
            cands = []
            for k in known:
                if abs((k.event_date - e.event_date).days) <= 1:
                    score = _name_similarity(e.event_name, k.performer)
                    if score > 0.35:
                        cands.append({"reachpro_event_id": int(k.reachpro_event_id),
                                      "name": k.performer, "venue": k.venue,
                                      "event_date": str(k.event_date), "score": round(score, 2)})
            cands.sort(key=lambda c: -c["score"])
            item["suggestions"] = cands[:3]
        else:
            item["suggestions"] = []
        out.append(item)
    return out


@router.get("/event-search")
def event_search(q: str):
    """Find ReachPro events by name via inventory search (covers events our
    recon DB has never seen a sale for). An all-digits query is treated as a
    ReachPro event id and resolved directly - CrowdVold's own event dates are
    sometimes wrong, which defeats name/date matching entirely."""
    q = q.strip()
    if q.isdigit():
        status, data = _sweep_api_get(f"/events?eventId={q}", attempts=2)
        ev = (data or {}).get("event") if status == 200 else None
        if not ev:
            return []
        avail = None
        inv_status, inv = _sweep_api_get(
            f"/inventory/search?eventIds={q}&includePastEvents=true&maxPageSize=200", attempts=2)
        if inv_status == 200:
            avail = sum(l.get("availableQuantity") or 0 for l in inv.get("inventory", []))
        return [{
            "reachpro_event_id": ev.get("id"), "name": ev.get("name"),
            "event_date": (ev.get("date") or "")[:10],
            "venue": ev.get("venue") if isinstance(ev.get("venue"), str)
                     else (ev.get("venue") or {}).get("name"),
            "listings": len(inv.get("inventory", [])) if inv_status == 200 else None,
            "available_tickets": avail,
        }]
    status, data = _sweep_api_get(
        f"/inventory/search?eventSearchText={q}&includePastEvents=true&maxPageSize=100", attempts=2)
    if status != 200:
        raise HTTPException(status_code=502, detail=f"ReachPro inventory search failed (HTTP {status})")
    seen = {}
    for listing in data.get("inventory", []):
        ev = listing.get("event") or {}
        eid = ev.get("id")
        if not eid:
            continue
        entry = seen.setdefault(eid, {
            "reachpro_event_id": eid, "name": ev.get("name"),
            "event_date": (ev.get("date") or "")[:10],
            "venue": (ev.get("venue") or {}).get("name") if isinstance(ev.get("venue"), dict) else ev.get("venue"),
            "listings": 0, "available_tickets": 0,
        })
        entry["listings"] += 1
        entry["available_tickets"] += listing.get("availableQuantity") or 0
    return sorted(seen.values(), key=lambda x: x["event_date"])


class MappingUpdate(BaseModel):
    cv_event_name: str
    cv_event_date: str  # YYYY-MM-DD
    reachpro_event_id: Optional[int] = None
    reachpro_event_name: Optional[str] = None
    reachpro_event_date: Optional[str] = None
    reachpro_venue: Optional[str] = None
    skip: bool = False


@router.put("/events/mapping")
def save_mapping(body: MappingUpdate, db: Session = Depends(get_db)):
    if not body.skip and body.reachpro_event_id is None:
        raise HTTPException(status_code=400, detail="Provide a ReachPro event id, or set skip.")
    db.execute(text("""
        INSERT INTO cv_event_mappings (cv_event_name, cv_event_date, reachpro_event_id,
                                       reachpro_event_name, reachpro_event_date, reachpro_venue, skip)
        VALUES (:n, :d, :rid, :rname, :rdate, :rvenue, :skip)
        ON CONFLICT (cv_event_name, cv_event_date)
        DO UPDATE SET reachpro_event_id = :rid, reachpro_event_name = :rname,
                      reachpro_event_date = :rdate, reachpro_venue = :rvenue, skip = :skip
    """), {"n": body.cv_event_name, "d": body.cv_event_date,
           "rid": body.reachpro_event_id, "rname": body.reachpro_event_name,
           "rdate": body.reachpro_event_date or None, "rvenue": body.reachpro_venue,
           "skip": body.skip})
    db.commit()
    return {"status": "ok"}


class ExcludeUpdate(BaseModel):
    excluded: bool
    note: Optional[str] = None


@router.put("/sales/{order_number}/exclude")
def exclude_sale(order_number: str, body: ExcludeUpdate, db: Session = Depends(get_db)):
    """Toggle a staged row out of / back into the import. Only rows not yet
    created can be toggled."""
    row = db.execute(text("SELECT status FROM cv_sales WHERE order_number = :o"), {"o": order_number}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    if row.status not in ("staged", "excluded"):
        raise HTTPException(status_code=400, detail=f"Row is '{row.status}' and cannot be toggled.")
    db.execute(text("""
        UPDATE cv_sales SET status = :st, status_note = :note WHERE order_number = :o
    """), {"st": "excluded" if body.excluded else "staged",
           "note": (body.note or "Excluded by user") if body.excluded else None, "o": order_number})
    db.commit()
    return {"status": "ok"}


def _fetch_event_listings(event_id: int) -> list[dict]:
    status, data = _sweep_api_get(
        f"/inventory/search?eventIds={event_id}&includePastEvents=true&maxPageSize=200", attempts=3)
    if status != 200:
        raise RuntimeError(f"inventory search failed for event {event_id} (HTTP {status})")
    out = []
    for l in data.get("inventory", []):
        out.append({"inventory_id": l.get("id"), "available": l.get("availableQuantity") or 0,
                    "unit_cost": l.get("unitCost"), "section": (l.get("seating") or {}).get("section"),
                    "row": (l.get("seating") or {}).get("row")})
    return out


def _compute_dispositions(db: Session):
    """The heart of the preview AND the create step: for every staged row,
    decide ready / unmapped / skipped_event / no_inventory / possible_duplicate,
    choosing the inventory listing each ready row would allocate from.
    Returns (rows, per-event summaries, listing errors)."""
    sales = db.execute(text("""
        SELECT cv.order_number, cv.source_tab, cv.transaction_date, cv.event_name,
               cv.event_date, cv.quantity, cv.earnings, cv.status, cv.status_note,
               cv.reachpro_invoice_id, cv.fulfilled,
               m.reachpro_event_id, m.reachpro_event_name, m.skip
        FROM cv_sales cv
        LEFT JOIN cv_event_mappings m
          ON m.cv_event_name = cv.event_name AND m.cv_event_date = cv.event_date::date
        ORDER BY cv.event_date, cv.transaction_date
    """)).fetchall()

    # Existing Offline sales on the mapped events (by our recon copy), for
    # duplicate detection: same event, same quantity, proceeds within $1.
    mapped_ids = sorted({int(s.reachpro_event_id) for s in sales if s.reachpro_event_id})
    offline_pool: dict[int, list] = {}
    if mapped_ids:
        for r in db.execute(text("""
            SELECT e.reachpro_event_id::bigint AS rid, s.quantity, s.proceeds, s.reachpro_sale_id
            FROM sales s
            JOIN events e ON e.event_id = s.event_id
            JOIN marketplaces mk ON mk.marketplace_id = s.marketplace_id
            WHERE mk.name = 'Offline' AND e.reachpro_event_id IS NOT NULL
              AND e.reachpro_event_id ~ '^[0-9]+$'
        """)).fetchall():
            if r.rid in mapped_ids:
                offline_pool.setdefault(r.rid, []).append(
                    {"qty": r.quantity, "proceeds": float(r.proceeds or 0), "sale_id": r.reachpro_sale_id, "used": False})

    listings_by_event: dict[int, list] = {}
    listing_errors: dict[int, str] = {}
    for eid in mapped_ids:
        try:
            listings_by_event[eid] = [l for l in _fetch_event_listings(eid) if l["available"] > 0]
        except RuntimeError as e:
            listing_errors[eid] = str(e)

    remaining = {eid: {l["inventory_id"]: l["available"] for l in ls}
                 for eid, ls in listings_by_event.items()}

    out_rows = []
    for s in sales:
        row = {"order_number": s.order_number, "source_tab": s.source_tab,
               "transaction_date": str(s.transaction_date or ""), "event_name": s.event_name,
               "event_date": str(s.event_date or "")[:10], "quantity": s.quantity,
               "earnings": float(s.earnings), "status": s.status,
               "status_note": s.status_note, "reachpro_invoice_id": s.reachpro_invoice_id,
               "fulfilled": s.fulfilled,
               "reachpro_event_id": int(s.reachpro_event_id) if s.reachpro_event_id else None,
               "reachpro_event_name": s.reachpro_event_name,
               "disposition": s.status, "inventory_id": None}
        if s.status == "staged":
            if s.skip:
                row["disposition"] = "skipped_event"
            elif not s.reachpro_event_id:
                row["disposition"] = "unmapped"
            elif int(s.reachpro_event_id) in listing_errors:
                row["disposition"] = "listing_error"
                row["status_note"] = listing_errors[int(s.reachpro_event_id)]
            else:
                eid = int(s.reachpro_event_id)
                dupe = next((d for d in offline_pool.get(eid, [])
                             if not d["used"] and d["qty"] == s.quantity
                             and abs(d["proceeds"] - float(s.earnings)) <= 1.00), None)
                if dupe:
                    dupe["used"] = True
                    row["disposition"] = "possible_duplicate"
                    row["status_note"] = f"Matches existing Offline sale {dupe['sale_id']} (qty {dupe['qty']}, ${dupe['proceeds']:.2f})"
                else:
                    inv = next((iid for iid, avail in remaining.get(eid, {}).items()
                                if avail >= s.quantity), None)
                    if inv is None:
                        row["disposition"] = "no_inventory"
                    else:
                        remaining[eid][inv] -= s.quantity
                        row["disposition"] = "ready"
                        row["inventory_id"] = inv
        out_rows.append(row)

    events_summary = []
    by_event: dict = {}
    for r in out_rows:
        key = (r["event_name"], r["event_date"])
        ev = by_event.setdefault(key, {"event_name": key[0], "event_date": key[1],
                                       "reachpro_event_id": r["reachpro_event_id"],
                                       "rows": 0, "tickets": 0, "earnings": 0.0,
                                       "dispositions": {}, "available_tickets": None})
        ev["rows"] += 1
        ev["tickets"] += r["quantity"]
        ev["earnings"] += r["earnings"]
        ev["dispositions"][r["disposition"]] = ev["dispositions"].get(r["disposition"], 0) + 1
        if r["reachpro_event_id"] and r["reachpro_event_id"] in listings_by_event:
            ev["available_tickets"] = sum(l["available"] for l in listings_by_event[r["reachpro_event_id"]])
    events_summary = list(by_event.values())
    return out_rows, events_summary, listing_errors


@router.get("/preview")
def preview(db: Session = Depends(get_db)):
    rows, events_summary, listing_errors = _compute_dispositions(db)
    totals: dict = {}
    for r in rows:
        t = totals.setdefault(r["disposition"], {"rows": 0, "tickets": 0, "earnings": 0.0})
        t["rows"] += 1
        t["tickets"] += r["quantity"]
        t["earnings"] += round(r["earnings"], 2)
    return {"rows": rows, "events": events_summary, "totals": totals,
            "listing_errors": listing_errors}


def _reachpro_find_by_order(order_number: str):
    """Idempotency check: does ReachPro already have a sale with this
    marketplaceSaleId? Returns the invoice id or None."""
    status, data = _api_call("GET", f"/invoices/marketplace/{order_number}")
    if status == 200 and isinstance(data, list) and data:
        return data[0].get("id")
    return None


def _run_create(order_numbers: Optional[list[str]]):
    global _create_status
    from app.database import SessionLocal
    db = SessionLocal()
    created = fulfilled = failed = skipped = 0
    try:
        rows, _, _ = _compute_dispositions(db)
        todo = [r for r in rows if r["disposition"] == "ready"
                and (order_numbers is None or r["order_number"] in order_numbers)]
        # created-but-not-fulfilled rows from an interrupted earlier run: retry the PATCH only
        refulfill = [r for r in rows if r["status"] == "created" and not r["fulfilled"]
                     and (order_numbers is None or r["order_number"] in order_numbers)]
        total = len(todo) + len(refulfill)

        for i, r in enumerate(todo):
            _create_status["progress"] = {"phase": "creating", "done": i, "total": total,
                                          "created": created, "failed": failed, "current": r["order_number"]}
            existing_id = _reachpro_find_by_order(r["order_number"])
            if existing_id:
                inv_id, note = str(existing_id), "Already existed in ReachPro"
                skipped += 1
            else:
                status, resp = _api_call("POST", "/invoices", {
                    "inventoryId": r["inventory_id"],
                    "marketplace": "Offline",
                    "saleDate": r["transaction_date"] or None,
                    "marketplaceSaleId": r["order_number"],
                    "externalId": r["order_number"],
                    "totalNetProceeds": round(r["earnings"], 2),
                    "quantitySold": r["quantity"],
                    "internalNotes": f"CrowdVold import ({r['source_tab']})",
                })
                if status in (429,) or status >= 500 or status == 0:
                    time.sleep(15)
                    # ambiguous failure: check whether it actually landed before retrying
                    existing_id = _reachpro_find_by_order(r["order_number"])
                    if not existing_id:
                        status, resp = _api_call("POST", "/invoices", {
                            "inventoryId": r["inventory_id"], "marketplace": "Offline",
                            "saleDate": r["transaction_date"] or None,
                            "marketplaceSaleId": r["order_number"], "externalId": r["order_number"],
                            "totalNetProceeds": round(r["earnings"], 2), "quantitySold": r["quantity"],
                            "internalNotes": f"CrowdVold import ({r['source_tab']})",
                        })
                if existing_id:
                    inv_id, note = str(existing_id), "Already existed in ReachPro"
                    skipped += 1
                elif status == 200 and isinstance(resp, dict) and resp.get("id"):
                    inv_id, note = str(resp["id"]), None
                    created += 1
                else:
                    failed += 1
                    db.execute(text("""
                        UPDATE cv_sales SET status = 'create_failed', status_note = :n
                        WHERE order_number = :o
                    """), {"n": f"HTTP {status}: {str(resp)[:300]}", "o": r["order_number"]})
                    db.commit()
                    time.sleep(0.25)
                    continue

            f_status, f_resp = _api_call("PATCH", f"/invoices/{inv_id}", {"fullfilmentState": "Fulfilled"})
            is_fulfilled = f_status == 200
            if is_fulfilled:
                fulfilled += 1
            db.execute(text("""
                UPDATE cv_sales SET status = 'created', reachpro_invoice_id = :id,
                    fulfilled = :f, inventory_id = :inv, pushed_at = NOW(),
                    status_note = :n
                WHERE order_number = :o
            """), {"id": inv_id, "f": is_fulfilled, "inv": r["inventory_id"],
                   "n": note if is_fulfilled else (note or "") + f" [fulfill failed HTTP {f_status}: {str(f_resp)[:200]}]".strip(),
                   "o": r["order_number"]})
            db.commit()
            time.sleep(0.25)

        for j, r in enumerate(refulfill):
            _create_status["progress"] = {"phase": "fulfilling retries", "done": len(todo) + j,
                                          "total": total, "created": created, "failed": failed,
                                          "current": r["order_number"]}
            f_status, _ = _api_call("PATCH", f"/invoices/{r['reachpro_invoice_id']}", {"fullfilmentState": "Fulfilled"})
            if f_status == 200:
                fulfilled += 1
                db.execute(text("""
                    UPDATE cv_sales SET fulfilled = TRUE, status_note = NULL WHERE order_number = :o
                """), {"o": r["order_number"]})
                db.commit()
            time.sleep(0.25)

        result = {"created": created, "fulfilled": fulfilled, "failed": failed,
                  "already_existed": skipped, "attempted": total,
                  "finished_at": datetime.now(timezone.utc).isoformat()}
        _create_status.update({"running": False, "result": result, "error": None, "progress": None})
    except Exception as e:
        _create_status.update({"running": False, "error": str(e), "progress": None})
    finally:
        db.close()


class CreateRequest(BaseModel):
    order_numbers: Optional[list[str]] = None  # None = every 'ready' row


@router.post("/create", dependencies=[Depends(require_admin)])
def create_sales(body: CreateRequest):
    """Create the ready rows in ReachPro as Offline+Fulfilled sales. Runs in
    the background - poll /cv-import/status. Admin-only: ReachPro sales have
    no delete. Does NOT run any sync afterwards."""
    if _create_status["running"]:
        return {"started": False, "message": "A create run is already in progress"}
    _create_status.update({"running": True, "result": None, "error": None,
                           "progress": {"phase": "starting"}})
    threading.Thread(target=_run_create, args=(body.order_numbers,), daemon=True).start()
    return {"started": True}


@router.get("/status")
def status():
    return _create_status
