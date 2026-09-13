import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta, date
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from app.database import engine
from app.config import settings

router = APIRouter(prefix="/sync", tags=["sync"])

# In-memory sync status
_sync_status: dict = {"running": False, "result": None, "error": None, "progress": None}

BASE    = "https://pointofsaleapi.stubhub.net"
ACCOUNT = "691ed4c2-dfe4-4435-82ee-d9fb49d1f2cc"


def _get_headers():
    return {"Authorization": f"Bearer {settings.stubhub_bearer_token}", "Account-Id": ACCOUNT}


def _api_get(path):
    req = urllib.request.Request(BASE + path, headers=_get_headers())
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return 0, {}


def _sweep_api_get(path, attempts=5, timeout=120):
    """_api_get for the bulk sweeps: generous timeout (PO pages routinely
    take ~25s, which trips _api_get's 30s) and retries on timeouts, 429s,
    and transient 5xx so one flaky page doesn't kill a long crawl."""
    status, data = 0, {}
    for a in range(attempts):
        req = urllib.request.Request(BASE + path, headers=_get_headers())
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            status, data = e.code, {}
            if e.code == 429 or e.code >= 500:
                time.sleep(10 * (a + 1))
                continue
            return status, data
        except Exception:
            status, data = 0, {}
            time.sleep(10 * (a + 1))
    return status, data


def _fetch_invoices_for_buyer(buyer_id: str, start: str, end: str, errors: dict = None, name: str = "", sale_status: str = None) -> list:
    """Fetch all invoices for a buyer over the given date range, paginating automatically."""
    invoices = []
    pagination_token = None
    page_num = 0
    while True:
        path = f"/invoices/search?buyerUserId={buyer_id}&saleStartDate={start}&saleEndDate={end}&pageSize=500"
        if sale_status:
            path += f"&saleStatus={sale_status}"
        if pagination_token:
            path += f"&paginationToken={pagination_token}"
        status, data = _api_get(path)
        if status != 200:
            if errors is not None:
                errors[name] = f"HTTP {status} on page {page_num + 1}"
            break
        page = data.get("invoices", [])
        invoices.extend(page)
        page_num += 1
        pagination_token = data.get("paginationToken")
        if not page or len(page) < 250 or not pagination_token:
            break
    return invoices


def _upsert_event(db, inv: dict) -> int | None:
    event = inv.get("event") or {}
    mapping = inv.get("eventMapping") or {}
    performer_obj = inv.get("performer") or {}
    venue_obj = inv.get("venue") or {}

    performer  = performer_obj.get("name") or event.get("name") or mapping.get("eventName", "")
    event_name = event.get("name") or mapping.get("eventName", "")
    venue      = venue_obj.get("name") or event.get("venue") or mapping.get("venueName", "")
    raw_date   = event.get("date") or mapping.get("eventDate") or ""
    event_date = raw_date[:10] or None
    event_time = raw_date[11:19] or None
    reachpro_event_id = str(event.get("id", "")) or None

    if not performer and not event_name:
        return None

    if reachpro_event_id:
        row = db.execute(text("""
            INSERT INTO events (performer, venue, event_date, event_time, reachpro_event_id)
            VALUES (:performer, :venue, :event_date, :event_time, :rpid)
            ON CONFLICT (reachpro_event_id) DO UPDATE
                SET performer  = EXCLUDED.performer,
                    venue      = EXCLUDED.venue,
                    event_date = EXCLUDED.event_date,
                    event_time = EXCLUDED.event_time
            RETURNING event_id
        """), {
            "performer": performer or event_name,
            "venue": venue,
            "event_date": event_date,
            "event_time": event_time,
            "rpid": reachpro_event_id,
        }).fetchone()
    else:
        row = db.execute(text("""
            INSERT INTO events (performer, venue, event_date, event_time)
            VALUES (:performer, :venue, :event_date, :event_time)
            ON CONFLICT DO NOTHING
            RETURNING event_id
        """), {
            "performer": performer or event_name,
            "venue": venue,
            "event_date": event_date,
            "event_time": event_time,
        }).fetchone()
        if not row:
            row = db.execute(text("""
                SELECT event_id FROM events
                WHERE performer = :performer AND event_date = :event_date
            """), {"performer": performer or event_name, "event_date": event_date}).fetchone()

    return row.event_id if row else None


def _is_skippable(order_id: str) -> bool:
    """Return True for order IDs we know will never resolve via direct lookup."""
    if not order_id or not order_id.strip():
        return True
    oid = order_id.strip().lower()
    if oid in ("dne", "carryover", "-", "dne; carryover"):
        return True
    return False


def _fetch_invoice_direct(order_id: str):
    """Fetch invoice via /invoices/marketplace/{order_id}. Returns invoice dict or None."""
    status, data = _api_get(f"/invoices/marketplace/{order_id}")
    if status != 200:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict) and "id" in data:
        return data
    invs = data.get("invoices", [])
    return invs[0] if invs else None


def _calc_splits(tickets: list, guid_to_purchaser: dict) -> dict:
    """Return {purchaser_id: split_pct} based on ticket-level buyerUserId counts."""
    counts = {}
    for t in tickets:
        bid = str(t.get("buyerUserId") or "")
        p = guid_to_purchaser.get(bid)
        if p:
            counts[p.purchaser_id] = counts.get(p.purchaser_id, 0) + 1
    total = sum(counts.values())
    if not total:
        return {}
    return {pid: round(cnt / total, 6) for pid, cnt in counts.items()}


def _cancellation_date(inv: dict):
    raw = inv.get("cancellationDate") or ""
    return raw[:10] or None


def _tag_names(inv: dict):
    """ReachPro invoice tag names joined with '; ' - stored on the sale and
    substring-matched by commission overrides."""
    names = [t.get("name", "").strip() for t in (inv.get("tags") or []) if t.get("name")]
    return "; ".join(names) or None


def _get_po_dates(db, po_ids) -> dict:
    """PO id -> purchase date via the po_purchase_dates cache, fetching cache
    misses from the API (PO purchase dates are immutable in practice, so the
    cache never needs invalidating)."""
    from datetime import date as date_cls
    po_ids = {int(p) for p in po_ids if p}
    if not po_ids:
        return {}
    rows = db.execute(text(
        "SELECT po_id, purchase_date FROM po_purchase_dates WHERE po_id = ANY(:ids)"
    ), {"ids": list(po_ids)}).fetchall()
    dates = {r.po_id: r.purchase_date for r in rows}
    for pid in po_ids - set(dates):
        status, data = _api_get(f"/purchases/{pid}")
        raw = (data.get("purchaseDate") or "") if status == 200 and isinstance(data, dict) else ""
        pdate = date_cls.fromisoformat(raw[:10]) if raw else None
        db.execute(text("""
            INSERT INTO po_purchase_dates (po_id, purchase_date) VALUES (:pid, :d)
            ON CONFLICT (po_id) DO NOTHING
        """), {"pid": pid, "d": pdate})
        dates[pid] = pdate
    return dates


def _latest_purchase_date(db, tickets: list):
    """Most recent PO purchaseDate across the sale's tickets (business rule:
    for split-sourced sales the latest purchase wins)."""
    dates = _get_po_dates(db, {t.get("purchaseOrderId") for t in tickets})
    real = [d for d in dates.values() if d]
    return max(real) if real else None


def _fetch_originating_listing(inv: dict):
    """For Rejected/pending-sourcing sales ReachPro strips the tickets (and
    with them the buyer) off the invoice. The originating listing usually
    still carries per-ticket buyerUserIds and a unitCost, so it's the fallback
    source for purchaser attribution and cost. Returns the listing dict or None."""
    listing_id = inv.get("originatedFromListingId")
    if not listing_id:
        return None
    status, data = _api_get(f"/inventory/{listing_id}")
    if status != 200 or not data:
        return None
    return data[0] if isinstance(data, list) else data


def _write_invoice_to_db(db, inv: dict, payout_row, guid_to_purchaser: dict, stats: dict):
    """Upsert a sale from an invoice and link it to the payout row. Mutates stats."""
    tickets = inv.get("tickets") or []
    listing = None

    if not any(t.get("buyerUserId") for t in tickets):
        stats["no_buyer_id"] += 1
        listing = _fetch_originating_listing(inv)
        listing_tickets = (listing.get("tickets") or []) if listing else []
        if any(t.get("buyerUserId") for t in listing_tickets):
            tickets = listing_tickets
            stats["listing_fallback_used"] = stats.get("listing_fallback_used", 0) + 1

    rp_id = str(inv.get("id", ""))
    order_id = payout_row.marketplace_order_id

    existing_sale = db.execute(text(
        "SELECT sale_id FROM sales WHERE reachpro_sale_id = :rp_id"
    ), {"rp_id": rp_id}).fetchone()

    if existing_sale:
        sale_id = existing_sale.sale_id
        stats["already_existed"] += 1
        needs_event = db.execute(text(
            "SELECT event_id FROM sales WHERE sale_id = :sid"
        ), {"sid": sale_id}).fetchone()
        if needs_event and needs_event.event_id is None:
            event_id = _upsert_event(db, inv)
            if event_id:
                db.execute(text(
                    "UPDATE sales SET event_id = :eid WHERE sale_id = :sid"
                ), {"eid": event_id, "sid": sale_id})
    else:
        event_id = _upsert_event(db, inv)
        proceeds = inv.get("totalNetProceeds")
        cost     = inv.get("totalCost")
        # Rejected/pending-sourcing invoices report totalCost as 0/null along
        # with their stripped tickets - recover the real cost from the
        # originating listing's unitCost so commission math stays correct.
        if (cost is None or float(cost) == 0) and listing and listing.get("unitCost"):
            qty = inv.get("quantitySold") or 0
            recovered = round(float(listing["unitCost"]) * int(qty), 2)
            if recovered > 0:
                cost = recovered
        pnl      = round(float(proceeds or 0) - float(cost or 0), 2) if proceeds is not None and cost is not None else None

        sale_row = db.execute(text("""
            INSERT INTO sales (
                reachpro_sale_id, marketplace_id, event_id,
                proceeds, cost, pnl,
                sale_date, quantity, marketplace_order_id,
                fulfillment_status, tags, purchased_date, cancellation_date
            ) VALUES (
                :rp_id, :mkt_id, :event_id,
                :proceeds, :cost, :pnl,
                :sale_date, :qty, :order_id,
                :fstatus, :tags, :pdate, :cancel_date
            ) RETURNING sale_id
        """), {
            "rp_id":    rp_id,
            "mkt_id":   payout_row.marketplace_id,
            "event_id": event_id,
            "proceeds": proceeds,
            "cost":     cost,
            "pnl":      pnl,
            "sale_date": inv.get("saleDate"),
            "qty":      inv.get("quantitySold"),
            "order_id": order_id,
            "fstatus":  (inv.get("posState") or {}).get("saleStatus"),
            "tags":     _tag_names(inv),
            "pdate":    _latest_purchase_date(db, tickets),
            "cancel_date": _cancellation_date(inv),
        }).fetchone()
        sale_id = sale_row.sale_id
        stats["sales_created"] += 1

    # Write split purchasers based on per-ticket buyerUserId breakdown
    splits = _calc_splits(tickets, guid_to_purchaser)
    for purchaser_id, split_pct in splits.items():
        db.execute(text("""
            INSERT INTO sale_purchasers (sale_id, purchaser_id, split_pct)
            VALUES (:sid, :pid, :pct)
            ON CONFLICT (sale_id, purchaser_id) DO UPDATE SET split_pct = EXCLUDED.split_pct
        """), {"sid": sale_id, "pid": purchaser_id, "pct": split_pct})

    for purchaser_id in splits:
        p = next((p for p in guid_to_purchaser.values() if p.purchaser_id == purchaser_id), None)
        if p:
            stats["purchasers_hit"][p.name] = stats["purchasers_hit"].get(p.name, 0) + 1

    db.execute(text("""
        UPDATE marketplace_payouts SET sale_id = :sid WHERE payout_id = :pid
    """), {"sid": sale_id, "pid": payout_row.payout_id})

    stats["matched"] += 1


def _sync_wasted(db, purchasers: list, guid_to_purchaser: dict, start: str, end: str, stats: dict):
    """Fetch wasted invoices per buyer and write sale + negative payout rows."""
    # Get or create Wastage marketplace
    wastage_row = db.execute(text(
        "SELECT marketplace_id FROM marketplaces WHERE name = 'Wastage'"
    )).fetchone()
    if wastage_row:
        wastage_id = wastage_row.marketplace_id
    else:
        wastage_id = db.execute(text(
            "INSERT INTO marketplaces (name) VALUES ('Wastage') RETURNING marketplace_id"
        )).fetchone().marketplace_id
        db.commit()

    wasted_created = 0
    wasted_skipped = 0
    wasted_total_cost = 0.0

    for purchaser in purchasers:
        buyer_id = str(purchaser.buyer_user_id)
        _sync_status["progress"] = {**stats, "phase": "wasted", "fetching": purchaser.name}
        invoices = _fetch_invoices_for_buyer(buyer_id, start, end, sale_status="Wasted")

        for inv in invoices:
            rp_id = str(inv.get("id", ""))
            payout_key = f"wasted-{rp_id}"

            # Skip if already synced
            existing = db.execute(text(
                "SELECT sale_id FROM sales WHERE reachpro_sale_id = :rp_id"
            ), {"rp_id": rp_id}).fetchone()
            if existing:
                wasted_skipped += 1
                continue

            # Cost from ticket-level unitCost (totalCost is null on wasted invoices)
            tickets = inv.get("tickets") or []
            cost = round(sum(float(t.get("unitCost") or 0) for t in tickets), 2)
            quantity = len(tickets)

            event_id = _upsert_event(db, inv)

            sale_row = db.execute(text("""
                INSERT INTO sales (
                    reachpro_sale_id, marketplace_id, event_id,
                    proceeds, cost, pnl,
                    sale_date, quantity, marketplace_order_id,
                    fulfillment_status, tags, purchased_date, cancellation_date
                ) VALUES (
                    :rp_id, :mkt_id, :event_id,
                    0, :cost, :pnl,
                    :sale_date, :qty, :order_id,
                    'Wasted', :tags, :pdate, :cancel_date
                ) RETURNING sale_id
            """), {
                "rp_id":     rp_id,
                "mkt_id":    wastage_id,
                "event_id":  event_id,
                "cost":      cost,
                "pnl":       -cost,
                "sale_date": inv.get("saleDate"),
                "qty":       quantity,
                "order_id":  rp_id,
                "tags":      _tag_names(inv),
                "pdate":     _latest_purchase_date(db, tickets),
                "cancel_date": _cancellation_date(inv),
            }).fetchone()
            sale_id = sale_row.sale_id

            # Write splits
            splits = _calc_splits(tickets, guid_to_purchaser)
            for purchaser_id, split_pct in splits.items():
                db.execute(text("""
                    INSERT INTO sale_purchasers (sale_id, purchaser_id, split_pct)
                    VALUES (:sid, :pid, :pct)
                    ON CONFLICT (sale_id, purchaser_id) DO UPDATE SET split_pct = EXCLUDED.split_pct
                """), {"sid": sale_id, "pid": purchaser_id, "pct": split_pct})

            # Create negative payout row representing the loss
            db.execute(text("""
                INSERT INTO marketplace_payouts (
                    marketplace_id, sale_id, marketplace_order_id,
                    amount, payment_type, payout_key, is_parking
                ) VALUES (
                    :mkt_id, :sid, :order_id,
                    :amount, 'Wastage', :pkey, FALSE
                ) ON CONFLICT (payout_key) DO NOTHING
            """), {
                "mkt_id":   wastage_id,
                "sid":      sale_id,
                "order_id": rp_id,
                "amount":   -cost,
                "pkey":     payout_key,
            })

            wasted_created += 1
            wasted_total_cost += cost
            if wasted_created % 100 == 0:
                db.commit()

    if wasted_created % 100 != 0:
        db.commit()

    stats["wasted_created"] = wasted_created
    stats["wasted_skipped"] = wasted_skipped
    stats["wasted_total_cost"] = round(wasted_total_cost, 2)


def _sync_offline(db, guid_to_purchaser: dict, start: str, end: str, stats: dict):
    """Pull private ("Offline"-marketplace) sales from ReachPro and write
    sale + synthetic payout rows so they earn commission like any other sale.

    There is no payout file for a private sale, so ReachPro's proceeds field
    substitutes for the payout amount. The synthetic row is payment_type
    'Payment' so the commission engine applies the standard treatment
    (proceeds minus cost), and its NULL import_file_id keeps it permanently
    out of the ReachPro push pipeline.

    Fetched account-wide and filtered client-side: the API ignores every
    marketplace filter param (verified empirically), and per-buyer streams
    die to the buyerUserId 10-item page cap. House-account offline sales
    (tickets owned by no purchaser) are skipped - nobody to pay.

    Proceeds are often entered as $0/$1 placeholders at sale time and fixed
    in ReachPro later, so every sweep re-checks existing offline sales and
    updates any whose proceeds changed - unless the row already sits inside
    a non-cancelled purchaser payout batch, which freezes it (flagged in
    stats instead). Rows still at <= $1 proceeds with real cost are counted
    as unpriced; the Generate Payouts preview surfaces them for fixing."""
    offline_row = db.execute(text(
        "SELECT marketplace_id FROM marketplaces WHERE name = 'Offline'"
    )).fetchone()
    if offline_row:
        offline_id = offline_row.marketplace_id
    else:
        offline_id = db.execute(text(
            "INSERT INTO marketplaces (name) VALUES ('Offline') RETURNING marketplace_id"
        )).fetchone().marketplace_id
        db.commit()

    created = updated = skipped_house = unpriced = frozen = 0

    pagination_token = None
    page_num = 0
    while True:
        _sync_status["progress"] = {**stats, "phase": "offline", "fetching": f"page {page_num + 1}"}
        path = f"/invoices/search?saleStartDate={start}&saleEndDate={end}&pageSize=250"
        if pagination_token:
            path += f"&paginationToken={pagination_token}"
        status, data = _api_get(path)
        if status != 200:
            stats["offline_fetch_error"] = f"HTTP {status} on page {page_num + 1}"
            break
        page = data.get("invoices", [])
        page_num += 1
        pagination_token = data.get("paginationToken")

        for inv in page:
            if inv.get("marketplace") != "Offline":
                continue
            rp_id = str(inv.get("id", ""))
            tickets = inv.get("tickets") or []
            splits = _calc_splits(tickets, guid_to_purchaser)
            if not splits:
                skipped_house += 1
                continue

            proceeds = round(float(inv.get("totalNetProceeds") or 0), 2)
            cost = round(sum(float(t.get("unitCost") or 0) for t in tickets), 2)
            quantity = inv.get("quantitySold") or len(tickets)
            if proceeds <= 1 and cost > 0:
                unpriced += 1

            existing = db.execute(text(
                "SELECT sale_id, proceeds FROM sales WHERE reachpro_sale_id = :rp_id"
            ), {"rp_id": rp_id}).fetchone()

            if existing:
                if float(existing.proceeds or 0) == proceeds:
                    continue
                # Re-priced in ReachPro since our last sweep. Frozen once the
                # row is inside a live commission batch - the batch snapshot
                # must keep matching what was actually paid.
                locked = db.execute(text("""
                    SELECT 1 FROM purchaser_payout_lines l
                    JOIN purchaser_payouts pp ON pp.purchaser_payout_id = l.purchaser_payout_id
                    JOIN marketplace_payouts mp ON mp.payout_id = l.marketplace_payout_id
                    WHERE mp.payout_key = :pkey AND pp.status != 'cancelled'
                    LIMIT 1
                """), {"pkey": f"offline-{rp_id}"}).fetchone()
                if locked:
                    frozen += 1
                    continue
                db.execute(text("""
                    UPDATE sales SET proceeds = :proceeds, cost = :cost, pnl = :pnl
                    WHERE sale_id = :sid
                """), {"proceeds": proceeds, "cost": cost, "pnl": round(proceeds - cost, 2),
                       "sid": existing.sale_id})
                db.execute(text("""
                    UPDATE marketplace_payouts SET amount = :amount WHERE payout_key = :pkey
                """), {"amount": proceeds, "pkey": f"offline-{rp_id}"})
                updated += 1
                continue

            event_id = _upsert_event(db, inv)
            sale_row = db.execute(text("""
                INSERT INTO sales (
                    reachpro_sale_id, marketplace_id, event_id,
                    proceeds, cost, pnl,
                    sale_date, quantity, marketplace_order_id,
                    fulfillment_status, tags, purchased_date, cancellation_date
                ) VALUES (
                    :rp_id, :mkt_id, :event_id,
                    :proceeds, :cost, :pnl,
                    :sale_date, :qty, :order_id,
                    :status, :tags, :pdate, :cancel_date
                ) RETURNING sale_id
            """), {
                "rp_id":     rp_id,
                "mkt_id":    offline_id,
                "event_id":  event_id,
                "proceeds":  proceeds,
                "cost":      cost,
                "pnl":       round(proceeds - cost, 2),
                "sale_date": inv.get("saleDate"),
                "qty":       quantity,
                "order_id":  inv.get("marketplaceSaleId") or rp_id,
                "status":    (inv.get("posState") or {}).get("saleStatus") or "Offline",
                "tags":      _tag_names(inv),
                "pdate":     _latest_purchase_date(db, tickets),
                "cancel_date": _cancellation_date(inv),
            }).fetchone()
            sale_id = sale_row.sale_id

            for purchaser_id, split_pct in splits.items():
                db.execute(text("""
                    INSERT INTO sale_purchasers (sale_id, purchaser_id, split_pct)
                    VALUES (:sid, :pid, :pct)
                    ON CONFLICT (sale_id, purchaser_id) DO UPDATE SET split_pct = EXCLUDED.split_pct
                """), {"sid": sale_id, "pid": purchaser_id, "pct": split_pct})

            db.execute(text("""
                INSERT INTO marketplace_payouts (
                    marketplace_id, sale_id, marketplace_order_id,
                    amount, payment_type, payout_key, is_parking
                ) VALUES (
                    :mkt_id, :sid, :order_id,
                    :amount, 'Payment', :pkey, FALSE
                ) ON CONFLICT (payout_key) DO NOTHING
            """), {
                "mkt_id":   offline_id,
                "sid":      sale_id,
                "order_id": inv.get("marketplaceSaleId") or rp_id,
                "amount":   proceeds,
                "pkey":     f"offline-{rp_id}",
            })
            created += 1

        db.commit()
        if not page or len(page) < 250 or not pagination_token:
            break

    stats["offline_created"] = created
    stats["offline_repriced"] = updated
    stats["offline_unpriced"] = unpriced
    stats["offline_frozen_price_change"] = frozen
    stats["offline_house_skipped"] = skipped_house


def _sync_tags_and_dates(db, start: str, end: str, stats: dict,
                         po_start: str | None = None):
    """Refresh sales.tags and sales.purchased_date from ReachPro.

    Two crawls:
      1. /invoices/search over the sale window - tag names per sale, so tags
         edited in ReachPro after a sale first synced self-correct here.
      2. /purchases/search - every PO carries its tickets' saleIds plus
         purchaseDate, giving saleId -> most recent purchase date directly
         (and warming the po_purchase_dates cache). Incremental by default:
         only POs purchased since the newest cached date (minus a margin),
         since PO purchase dates are immutable. Pass an explicit po_start
         for a full-history backfill.
    Only rows whose values actually changed are written."""
    current = _current_sale_tag_state(db)
    _sweep_sale_tags(db, start, end, stats, current)
    _sweep_purchase_dates(db, start, end, stats, current, po_start)


def _current_sale_tag_state(db) -> dict:
    return {
        r.reachpro_sale_id: (r.tags, r.purchased_date, r.fulfillment_status,
                             str(r.cancellation_date) if r.cancellation_date else None)
        for r in db.execute(text(
            "SELECT reachpro_sale_id, tags, purchased_date, fulfillment_status, cancellation_date FROM sales WHERE reachpro_sale_id IS NOT NULL"
        )).fetchall()
    }


def _sweep_sale_tags(db, start: str, end: str, stats: dict, current: dict):
    tags_updated = 0
    statuses_updated = 0
    token = None
    page = 0
    while True:
        _sync_status["progress"] = {**stats, "phase": "tags", "fetching": f"page {page + 1}"}
        path = f"/invoices/search?saleStartDate={start}&saleEndDate={end}&pageSize=250"
        if token:
            path += f"&paginationToken={token}"
        status, data = _sweep_api_get(path)
        if status != 200:
            stats["tags_fetch_error"] = f"HTTP {status} on page {page + 1}"
            break
        invs = data.get("invoices", [])
        page += 1
        token = data.get("paginationToken")
        for inv in invs:
            rp_id = str(inv.get("id", ""))
            if rp_id not in current:
                continue
            cur = current[rp_id]
            new_tags = _tag_names(inv)
            # Status refresh rides along: cancellations usually land in
            # ReachPro after a sale first synced, and the cancelled-sale
            # offset hunt reads our stored copy - stale status = missed sale
            new_status = (inv.get("posState") or {}).get("saleStatus") or None
            new_cancel = _cancellation_date(inv)
            # The /invoices/search list payload omits cancellationDate - only
            # the per-invoice detail carries it. For dead-looking statuses
            # (Rejected, FulfillmentFailed, Cancelled*) with no stored
            # cancellation date yet, spend one detail fetch to get the truth.
            if new_cancel is None and (cur[3] if len(cur) > 3 else None) is None:
                status_l = (new_status or (cur[2] if len(cur) > 2 else "") or "").lower()
                if any(k in status_l for k in ("cancel", "reject", "fail")):
                    d_status, detail = _sweep_api_get(f"/invoices/{rp_id}")
                    if d_status == 200:
                        new_cancel = _cancellation_date(detail)
            tags_changed = new_tags != cur[0]
            status_changed = bool(new_status) and new_status != (cur[2] if len(cur) > 2 else None)
            cancel_changed = bool(new_cancel) and new_cancel != (cur[3] if len(cur) > 3 else None)
            if tags_changed or status_changed or cancel_changed:
                db.execute(text("""
                    UPDATE sales SET tags = :t,
                        fulfillment_status = COALESCE(:st, fulfillment_status),
                        cancellation_date = COALESCE(:cd, cancellation_date)
                    WHERE reachpro_sale_id = :rp
                """), {"t": new_tags, "st": new_status, "cd": new_cancel, "rp": rp_id})
                current[rp_id] = (new_tags, cur[1],
                                  new_status or (cur[2] if len(cur) > 2 else None),
                                  new_cancel or (cur[3] if len(cur) > 3 else None))
                if tags_changed:
                    tags_updated += 1
                if status_changed or cancel_changed:
                    statuses_updated += 1
        db.commit()
        if not invs or not token:
            break
    stats["tags_updated"] = tags_updated
    stats["statuses_updated"] = statuses_updated


def _sweep_purchase_dates(db, start: str, end: str, stats: dict, current: dict,
                          po_start: str | None = None):
    # pageSize 250: PO payloads carry full ticketGroups; big pages both
    # time out (5000) and spike memory enough to OOM the 512MB Render
    # instance (1000). Small pages keep the peak flat.
    from datetime import date as date_cls

    # Incremental by default: PO purchase dates are immutable, so anything
    # already cached never needs re-reading. Resume from the newest cached
    # date minus a week of margin (late-entered POs) - typically one or two
    # pages instead of a ~150k-PO re-crawl. Explicit po_start = backfill,
    # bounded by the event window like the original full crawl.
    incremental = po_start is None
    if incremental:
        newest = db.execute(text("SELECT MAX(purchase_date) FROM po_purchase_dates")).scalar()
        if newest:
            po_start = (datetime.combine(newest, datetime.min.time(), tzinfo=timezone.utc)
                        - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
        else:
            po_start = "2020-01-01T00:00:00"

    sale_pdate: dict = {}
    pos_seen = 0
    token = None
    page = 0
    base = (f"/purchases/search?purchaseStartDate={po_start}"
            f"&purchaseEndDate={end}&pageSize=250")
    if not incremental:
        base += f"&eventStartDate={start}"
    while True:
        _sync_status["progress"] = {**stats, "phase": "purchase dates", "fetching": f"page {page + 1}"}
        path = base + (f"&paginationToken={token}" if token else "")
        status, data = _sweep_api_get(path)
        if status != 200:
            stats["po_fetch_error"] = f"HTTP {status} on page {page + 1}"
            break
        pos = data.get("purchases", [])
        page += 1
        token = data.get("paginationToken")
        for po in pos:
            raw = po.get("purchaseDate") or ""
            pdate = date_cls.fromisoformat(raw[:10]) if raw else None
            pos_seen += 1
            db.execute(text("""
                INSERT INTO po_purchase_dates (po_id, purchase_date) VALUES (:pid, :d)
                ON CONFLICT (po_id) DO NOTHING
            """), {"pid": po.get("id"), "d": pdate})
            if not pdate:
                continue
            for tg in po.get("ticketGroups", []):
                for t in tg.get("tickets", []):
                    sid = t.get("saleId")
                    if sid:
                        sid = str(sid)
                        if sid not in sale_pdate or pdate > sale_pdate[sid]:
                            sale_pdate[sid] = pdate
        db.commit()
        if not pos or not token:
            break

    # Only ever move a sale's purchased_date FORWARD (the business rule is
    # "most recent purchase wins"). Essential for incremental crawls, which
    # see only recent POs and would otherwise downgrade a sale whose newest
    # PO lies outside the crawl window.
    pdates_updated = 0
    for rp_id, pdate in sale_pdate.items():
        cur = current.get(rp_id)
        if cur is None or (cur[1] is not None and pdate <= cur[1]):
            continue
        db.execute(text(
            "UPDATE sales SET purchased_date = :d WHERE reachpro_sale_id = :rp"
        ), {"d": pdate, "rp": rp_id})
        pdates_updated += 1
        if pdates_updated % 500 == 0:
            db.commit()
    db.commit()

    stats["purchase_dates_updated"] = pdates_updated
    stats["pos_scanned"] = pos_seen


def _run_sync(db, import_file_ids: list[int] | None = None) -> dict:
    # Load purchasers
    purchasers = db.execute(text("""
        SELECT purchaser_id, name, buyer_user_id
        FROM purchasers
        WHERE buyer_user_id IS NOT NULL
    """)).fetchall()
    guid_to_purchaser = {str(p.buyer_user_id): p for p in purchasers}

    # Load UNMATCHED payouts with an order ID - parking passes go through the
    # same matching flow as any other sale, they're real sold/paid-out
    # inventory. Rows a human has explicitly dismissed as "won't match" are
    # excluded, same treatment as the known-unresolvable skippable order IDs
    # below. Already-matched rows are excluded outright: re-processing them
    # meant one ReachPro API call plus several DB writes apiece for nothing
    # (32k of them once made the full sync take two hours).
    # Optionally scoped to a specific set of just-imported files.
    query = """
        SELECT mp.payout_id, mp.marketplace_order_id, mp.marketplace_id
        FROM marketplace_payouts mp
        WHERE mp.marketplace_order_id IS NOT NULL
          AND mp.sale_id IS NULL
          AND mp.match_dismissed_at IS NULL
    """
    params = {}
    if import_file_ids is not None:
        query += " AND mp.import_file_id = ANY(:fids)"
        params["fids"] = import_file_ids
    all_rows = db.execute(text(query), params).fetchall()

    stats = {
        "total_payouts": len(all_rows),
        "matched": 0,
        "sales_created": 0,
        "already_existed": 0,
        "no_buyer_id": 0,
        "skipped": 0,
        "direct_attempted": 0,
        "direct_matched": 0,
        "unmatched_after_sync": 0,
        "purchasers_hit": {},
    }

    # Direct per-row lookup for all payouts
    to_lookup = []
    for payout_row in all_rows:
        order_id = payout_row.marketplace_order_id
        if _is_skippable(order_id):
            stats["skipped"] += 1
            stats["unmatched_after_sync"] += 1
        else:
            to_lookup.append(payout_row)

    stats["direct_attempted"] = len(to_lookup)
    _sync_status["progress"] = {**stats, "remaining": len(to_lookup)}

    def _lookup(payout_row):
        return payout_row, _fetch_invoice_direct(payout_row.marketplace_order_id)

    batch = 0
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_lookup, row): row for row in to_lookup}
        for future in as_completed(futures):
            payout_row, inv = future.result()
            if not inv:
                stats["unmatched_after_sync"] += 1
                continue
            _write_invoice_to_db(db, inv, payout_row, guid_to_purchaser, stats)
            stats["direct_matched"] += 1
            batch += 1
            if batch >= 100:
                db.commit()
                batch = 0
                _sync_status["progress"] = dict(stats)

    if batch > 0:
        db.commit()

    # Wasted-tickets and offline-sales phases aren't tied to any particular
    # import file (they're broad sweeps over the same rolling window), so
    # skip them for a batch-scoped sync - only the untargeted "Start Sync"
    # runs them.
    if import_file_ids is None:
        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(days=270)).strftime("%Y-%m-%dT00:00:00")
        end_date   = now.strftime("%Y-%m-%dT23:59:59")
        _sync_wasted(db, purchasers, guid_to_purchaser, start_date, end_date, stats)
        _sync_offline(db, guid_to_purchaser, start_date, end_date, stats)
        # Tag drift is re-checked over a tighter 120-day window (edits happen
        # near sale time, and committed batches are frozen anyway); the
        # purchase-date sweep is incremental via the permanent PO cache.
        tags_start = (now - timedelta(days=120)).strftime("%Y-%m-%dT00:00:00")
        _sync_tags_and_dates(db, tags_start, end_date, stats)

    return stats


def _log_sync_run_start(started_at: datetime, scope: str):
    """Insert the run row immediately (finished_at NULL) so a run whose
    process is killed mid-crawl (OOM etc.) is visible as a failure instead of
    silently vanishing - the exact blind spot that made three OOM-killed
    syncs in one afternoon look like nothing ever ran."""
    try:
        with engine.begin() as conn:
            return conn.execute(text("""
                INSERT INTO sync_runs (started_at, scope) VALUES (:s, :scope)
                RETURNING run_id
            """), {"s": started_at, "scope": scope}).fetchone().run_id
    except Exception:
        return None


def _log_sync_run(started_at: datetime, stats: dict | None, error: str | None,
                  run_id: int | None = None, scope: str | None = None):
    try:
        with engine.connect() as conn:
            if run_id is not None:
                conn.execute(text("""
                    UPDATE sync_runs SET
                        finished_at = :finished_at,
                        total_payouts = :total_payouts, processed = :processed,
                        sales_created = :sales_created, already_existed = :already_existed,
                        no_invoice = :no_invoice, no_buyer_id = :no_buyer_id,
                        unmapped_purchaser = :unmapped_purchaser,
                        purchasers_hit = :purchasers_hit, error = :error
                    WHERE run_id = :run_id
                """), {
                    "run_id":             run_id,
                    "finished_at":        datetime.now(timezone.utc),
                    "total_payouts":      (stats or {}).get("total_payouts"),
                    "processed":          (stats or {}).get("matched"),
                    "sales_created":      (stats or {}).get("sales_created"),
                    "already_existed":    (stats or {}).get("already_existed"),
                    "no_invoice":         (stats or {}).get("no_invoice"),
                    "no_buyer_id":        (stats or {}).get("no_buyer_id"),
                    "unmapped_purchaser": (stats or {}).get("unmapped_purchaser"),
                    "purchasers_hit":     json.dumps((stats or {}).get("purchasers_hit") or {}),
                    "error":              error,
                })
                conn.commit()
                return
            conn.execute(text("""
                INSERT INTO sync_runs (
                    started_at, finished_at,
                    total_payouts, processed, sales_created, already_existed,
                    no_invoice, no_buyer_id, unmapped_purchaser,
                    purchasers_hit, error, scope
                ) VALUES (
                    :started_at, :finished_at,
                    :total_payouts, :processed, :sales_created, :already_existed,
                    :no_invoice, :no_buyer_id, :unmapped_purchaser,
                    :purchasers_hit, :error, :scope
                )
            """), {
                "scope":              scope,
                "started_at":         started_at,
                "finished_at":        datetime.now(timezone.utc),
                "total_payouts":      (stats or {}).get("total_payouts"),
                "processed":          (stats or {}).get("matched"),
                "sales_created":      (stats or {}).get("sales_created"),
                "already_existed":    (stats or {}).get("already_existed"),
                "no_invoice":         (stats or {}).get("unmatched_after_sync"),
                "no_buyer_id":        (stats or {}).get("no_buyer_id"),
                "unmapped_purchaser": None,
                "purchasers_hit":     json.dumps((stats or {}).get("purchasers_hit", {})),
                "error":              error,
            })
            conn.commit()
    except Exception:
        pass


def _background_sync(import_file_ids: list[int] | None = None):
    global _sync_status
    started_at = datetime.now(timezone.utc)
    scope = "batch" if import_file_ids else "full"
    _sync_status = {"running": True, "result": None, "error": None, "progress": None}
    run_id = _log_sync_run_start(started_at, scope)
    try:
        with engine.connect() as conn:
            stats = _run_sync(conn, import_file_ids)
        _log_sync_run(started_at, stats, None, run_id=run_id, scope=scope)
        _sync_status = {"running": False, "result": stats, "error": None, "progress": None}
    except Exception as e:
        _log_sync_run(started_at, None, str(e), run_id=run_id, scope=scope)
        _sync_status = {"running": False, "result": None, "error": str(e), "progress": _sync_status.get("progress")}


def trigger_background_sync(background_tasks: BackgroundTasks, import_file_ids: list[int] | None = None) -> bool:
    """Callable from other routers (e.g. imports.py right after a commit) to
    kick off the same background sync/status tracker this module already
    exposes via /sync/status. Returns False if a sync is already running."""
    if _sync_status.get("running"):
        return False
    background_tasks.add_task(_background_sync, import_file_ids)
    return True


class SyncRequest(BaseModel):
    import_file_ids: Optional[list[int]] = None


@router.post("/sales-from-api")
def sync_sales_from_api(background_tasks: BackgroundTasks, body: SyncRequest = SyncRequest()):
    """Kick off background sync: bulk fetches invoices per purchaser then matches to unmatched payouts.
    Pass import_file_ids to scope the sync to specific just-imported files instead of the whole backlog."""
    started = trigger_background_sync(background_tasks, body.import_file_ids)
    if not started:
        return {"started": False, "message": "Sync already running"}
    return {"started": True, "message": "Sync started in background — poll /sync/status"}


@router.get("/status")
def sync_status():
    return _sync_status


@router.get("/last-run")
def last_sync_run():
    """The most recent FULL sync run and its outcome, for the Sync & Push
    card. A row with no finished_at is either running right now or was killed
    mid-run (OOM, restart) - the in-process running flag disambiguates."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT run_id, started_at, finished_at, processed, sales_created, error
            FROM sync_runs WHERE scope = 'full'
            ORDER BY started_at DESC LIMIT 1
        """)).fetchone()
    if not row:
        return {"status": "never", "started_at": None, "finished_at": None,
                "processed": None, "sales_created": None, "error": None}
    if _sync_status.get("running"):
        status, error = "in_progress", None
    elif row.finished_at is None:
        status = "failed"
        error = "Run never finished - the server process was likely killed mid-sync"
    elif row.error:
        status, error = "failed", row.error
    else:
        status, error = "success", None
    return {
        "status": status,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "processed": row.processed,
        "sales_created": row.sales_created,
        "error": error,
    }


class ManualMatchRequest(BaseModel):
    payout_id: int
    order_id: str


@router.post("/manual-match")
def manual_match(body: ManualMatchRequest):
    """Retry matching a single unmatched payout with a human-supplied order ID,
    for cases where the stored marketplace_order_id doesn't correspond to the
    real downstream marketplace order (e.g. a reseller's own internal reference
    instead of the actual StubHub/SeatGeek order number). Reuses the exact same
    lookup and write path the automatic sync uses - no separate matching logic."""
    order_id = body.order_id.strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id is required")

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT payout_id, marketplace_order_id, marketplace_id, sale_id
            FROM marketplace_payouts WHERE payout_id = :pid
        """), {"pid": body.payout_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Payout not found")
        if row.sale_id is not None:
            raise HTTPException(status_code=400, detail="This payout is already matched to a sale")

        inv = _fetch_invoice_direct(order_id)
        if not inv:
            return {"matched": False, "message": f"No ReachPro invoice found for order ID '{order_id}'"}

        purchasers = conn.execute(text("""
            SELECT purchaser_id, name, buyer_user_id FROM purchasers WHERE buyer_user_id IS NOT NULL
        """)).fetchall()
        guid_to_purchaser = {str(p.buyer_user_id): p for p in purchasers}

        stats = {"matched": 0, "sales_created": 0, "already_existed": 0, "no_buyer_id": 0, "purchasers_hit": {}}
        _write_invoice_to_db(conn, inv, row, guid_to_purchaser, stats)
        conn.commit()
        return {"matched": True, "sales_created": stats["sales_created"] > 0}


@router.get("/unsynced-count")
def unsynced_count():
    """Count marketplace payouts not yet matched to a sale, using the same
    eligibility rule as _run_sync (excludes known-unresolvable order IDs like
    'DNE'/'carryover'; parking passes are included like any other sale)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT marketplace_order_id
            FROM marketplace_payouts
            WHERE sale_id IS NULL
              AND marketplace_order_id IS NOT NULL
        """)).fetchall()
    count = sum(1 for r in rows if not _is_skippable(r.marketplace_order_id))
    return {"unsynced_count": count}


# In-memory inventory refresh status
_inventory_sync_status: dict = {"running": False, "result": None, "error": None, "progress": None}


def _api_get_with_retry_after(path):
    """Like _api_get but surfaces the Retry-After header on 429s (so backoff
    can follow the server's own hint) and the underlying error detail on
    non-HTTP failures (so a genuine failure is diagnosable, not just 'HTTP 0').
    Long timeout: some inventory pages with huge ticket arrays are slow."""
    req = urllib.request.Request(BASE + path, headers=_get_headers())
    try:
        r = urllib.request.urlopen(req, timeout=120)
        return r.status, json.loads(r.read()), None, None
    except urllib.error.HTTPError as e:
        retry_after = e.headers.get("Retry-After") if e.headers else None
        return e.code, {}, retry_after, f"HTTP {e.code}"
    except Exception as e:
        return 0, {}, None, f"{type(e).__name__}: {e}"


def _fetch_inventory_page(path: str) -> dict:
    """One inventory page with retries: Retry-After-aware backoff on 429s,
    brief pauses on transient errors (timeouts, blips, 5xx), and a raise with
    the underlying detail after 10 failed attempts."""
    status, last_error = None, None
    for attempt in range(10):
        status, data, retry_after, last_error = _api_get_with_retry_after(path)
        if status == 200:
            return data
        if status == 429:
            try:
                wait = min(int(retry_after), 120) if retry_after else 65
            except ValueError:
                wait = 65
            time.sleep(wait)
        else:
            time.sleep(10)
    raise RuntimeError(f"page fetch failed: {last_error}")


def _fetch_buyer_inventory(buyer_user_id: str) -> list:
    """Fetch one purchaser's inventory listings. NOTE: not used by the main
    refresh - the server caps buyerUserId-filtered pages at 10 items (vs ~100
    unfiltered), which makes per-buyer streams so long they die to
    pagination-token expiry deep in the crawl. Kept for targeted single-buyer
    needs only. Raises rather than returning a partial list."""
    inventory = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    path = f"/inventory/search?pageSize=100&buyerUserId={buyer_user_id}&eventStartDate={today}"
    token = None
    while True:
        p = path + (f"&paginationToken={token}" if token else "")
        try:
            data = _fetch_inventory_page(p)
        except RuntimeError as e:
            raise RuntimeError(
                f"inventory fetch failed for buyer {buyer_user_id} after {len(inventory)} listings: {e}"
            )
        page = data.get("inventory") or []
        if not page:
            break
        inventory.extend(page)
        token = data.get("paginationToken")
        if not token:
            break
        time.sleep(0.5)
    return inventory


def _iter_available_inventory(counter: dict):
    """Yield every future-event inventory listing account-wide in a single
    token-paginated stream. Two hard-won findings shape this: the buyerUserId
    filter silently caps pages at 10 items (vs ~100 unfiltered), making
    per-buyer streams so long they die to pagination-token expiry (persistent
    500s deep in the crawl) - so no buyer filter, ever; and eventStartDate is
    honored server-side, cutting the crawl from the full listing history
    (~100k+) to just future events. Buyer scoping happens locally in the
    grouping step. Each listing's availableQuantity counts its still-unsold
    tickets - that's what "remaining inventory" means. Raises on any page
    failure rather than yielding a partial stream silently, because the
    refresh rewrites the cache table wholesale and silent truncation would
    persist as truth (this bit us once).

    A generator (with counter["listings"] tracking total yielded) rather than
    a returned list: the full ~22k-listing list with ticket arrays OOM-killed
    the 512MB Render instance mid-crawl, which the status endpoint then
    reported as a clean not-running state."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    path = f"/inventory/search?pageSize=100&eventStartDate={today}"
    token = None
    while True:
        p = path + (f"&paginationToken={token}" if token else "")
        try:
            data = _fetch_inventory_page(p)
        except RuntimeError as e:
            raise RuntimeError(f"inventory fetch failed after {counter['listings']} listings: {e}")
        page = data.get("inventory") or []
        if not page:
            break
        counter["listings"] += len(page)
        yield from page
        _inventory_sync_status["progress"] = {"phase": "fetching", "listings_fetched": counter["listings"]}
        token = data.get("paginationToken")
        if not token:
            break
        time.sleep(0.5)


def _group_future_inventory(inventory) -> list:
    """Group listings' still-available tickets by (buyer, event name) for
    events still in the future, summing qty/cost across however many listings
    and however many distinct dates/venues share that event name (e.g.
    multiple World Cup matches all roll up into one "World Cup" line).
    Accepts any iterable, including the paginated fetch generator."""
    today = datetime.now(timezone.utc).date()
    grouped: dict = {}
    for r in inventory:
        avail = r.get("availableQuantity") or 0
        if avail <= 0:
            continue
        tickets = r.get("tickets") or []
        buyer_user_id = tickets[0].get("buyerUserId") if tickets else None
        if not buyer_user_id:
            continue
        event = r.get("event") or r.get("eventMapping") or {}
        event_name = event.get("name") or "(Unknown)"
        raw_date = (event.get("date") or "")[:10]
        try:
            event_date = date.fromisoformat(raw_date) if raw_date else None
        except ValueError:
            event_date = None
        if not event_date or event_date < today:
            continue
        unit_cost = float(r.get("unitCost") or 0)
        key = (buyer_user_id, event_name)
        bucket = grouped.setdefault(key, {
            "buyer_user_id": buyer_user_id, "event_name": event_name,
            "qty": 0, "cost": 0.0, "po_ids": set(),
        })
        bucket["qty"] += avail
        bucket["cost"] += unit_cost * avail
        for t in tickets:
            if t.get("purchaseOrderId"):
                bucket["po_ids"].add(int(t["purchaseOrderId"]))
    return list(grouped.values())


def _warm_po_cache_future_events(db):
    """Bulk-load PO purchase dates for all future-event purchases into the
    po_purchase_dates cache (5000/page - minutes), so per-group lookups
    afterwards are cache hits instead of one API call per PO."""
    from datetime import date as date_cls
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    token = None
    while True:
        path = (f"/purchases/search?purchaseStartDate=2020-01-01T00:00:00"
                f"&purchaseEndDate={now_str}&eventStartDate={today}&pageSize=250")
        if token:
            path += f"&paginationToken={token}"
        status, data = _sweep_api_get(path)
        if status != 200:
            return  # cache warming is best-effort; misses fall back to per-PO fetches
        pos = data.get("purchases", [])
        for po in pos:
            raw = po.get("purchaseDate") or ""
            db.execute(text("""
                INSERT INTO po_purchase_dates (po_id, purchase_date) VALUES (:pid, :d)
                ON CONFLICT (po_id) DO NOTHING
            """), {"pid": po.get("id"),
                   "d": date_cls.fromisoformat(raw[:10]) if raw else None})
        db.commit()
        token = data.get("paginationToken")
        if not pos or not token:
            return


def _run_inventory_refresh(db) -> dict:
    # Grouping consumes the paginated stream listing-by-listing; the raw
    # listings are never all in memory at once (only the grouped buckets).
    counter = {"listings": 0}
    grouped_all = _group_future_inventory(_iter_available_inventory(counter))
    _inventory_sync_status["progress"] = {"phase": "grouping", "listings_fetched": counter["listings"]}
    # Keep only purchasers who can receive a commission file - the Excel
    # export is this table's sole consumer and the commission engine skips
    # 0% purchasers entirely. Scoping here (not in the fetch) because the
    # account-wide unfiltered crawl is the only reliable fetch shape.
    commissioned = {str(r.buyer_user_id) for r in db.execute(text(
        "SELECT buyer_user_id FROM purchasers WHERE buyer_user_id IS NOT NULL AND default_commission_pct > 0"
    )).fetchall()}
    grouped = [g for g in grouped_all if str(g["buyer_user_id"]) in commissioned]

    # Most recent PO purchase date per group. Bulk-warm the cache first so
    # this is a handful of pages, not one API call per PO.
    _inventory_sync_status["progress"] = {"phase": "purchase dates", "listings_fetched": counter["listings"]}
    _warm_po_cache_future_events(db)
    all_pos = set().union(*(g["po_ids"] for g in grouped)) if grouped else set()
    po_dates = _get_po_dates(db, all_pos)
    for g in grouped:
        real = [po_dates.get(p) for p in g["po_ids"] if po_dates.get(p)]
        g["last_purchase_date"] = max(real) if real else None

    db.execute(text("DELETE FROM remaining_inventory"))
    for row in grouped:
        db.execute(text("""
            INSERT INTO remaining_inventory (buyer_user_id, event_name, qty, cost, last_purchase_date)
            VALUES (:buyer_user_id, :event_name, :qty, :cost, :last_purchase_date)
        """), {k: row[k] for k in ("buyer_user_id", "event_name", "qty", "cost", "last_purchase_date")})
    db.commit()

    return {"listings_fetched": counter["listings"], "inventory_rows": len(grouped)}


def _log_inventory_run_start(started_at, trigger) -> int | None:
    """Insert the run row immediately (finished_at NULL). A run whose process
    dies mid-crawl leaves this row behind as evidence - previously nothing was
    written until completion, so a killed run was indistinguishable from no
    run at all and external pollers saw a clean not-running state."""
    try:
        with engine.begin() as conn:
            return conn.execute(text("""
                INSERT INTO inventory_refresh_runs (trigger, started_at)
                VALUES (:trigger, :started_at) RETURNING run_id
            """), {"trigger": trigger, "started_at": started_at}).fetchone().run_id
    except Exception:
        return None


def _log_inventory_run_finish(run_id, started_at, trigger, result: dict | None, error: str | None):
    params = {
        "run_id": run_id,
        "trigger": trigger,
        "started_at": started_at,
        "listings": (result or {}).get("listings_fetched"),
        "rows": (result or {}).get("inventory_rows"),
        "error": error,
    }
    try:
        with engine.begin() as conn:
            if run_id is not None:
                conn.execute(text("""
                    UPDATE inventory_refresh_runs
                    SET finished_at = NOW(), listings_fetched = :listings,
                        inventory_rows = :rows, error = :error
                    WHERE run_id = :run_id
                """), params)
            else:
                conn.execute(text("""
                    INSERT INTO inventory_refresh_runs (trigger, started_at, finished_at, listings_fetched, inventory_rows, error)
                    VALUES (:trigger, :started_at, NOW(), :listings, :rows, :error)
                """), params)
    except Exception:
        pass


def _background_inventory_refresh(trigger: str = "manual"):
    global _inventory_sync_status
    started_at = datetime.now(timezone.utc)
    _inventory_sync_status = {"running": True, "result": None, "error": None, "progress": None}
    run_id = _log_inventory_run_start(started_at, trigger)
    try:
        with engine.connect() as conn:
            result = _run_inventory_refresh(conn)
        _log_inventory_run_finish(run_id, started_at, trigger, result, None)
        _inventory_sync_status = {"running": False, "result": result, "error": None, "progress": None}
    except Exception as e:
        _log_inventory_run_finish(run_id, started_at, trigger, None, str(e))
        _inventory_sync_status = {"running": False, "result": None, "error": str(e), "progress": _inventory_sync_status.get("progress")}


async def nightly_inventory_scheduler():
    """Started from main.py at app startup. Fires the inventory refresh once
    a day at INVENTORY_REFRESH_UTC_HOUR (default 09:00 UTC = 5am ET) - the
    crawl takes ~1.5-2h against ReachPro's rate limiter, so it runs while
    nobody is waiting and the cache is fresh every morning. Note: only fires
    while the service process is alive - a free-tier instance that spins down
    overnight will miss it."""
    import asyncio
    hour = int(os.environ.get("INVENTORY_REFRESH_UTC_HOUR", "9"))
    while True:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        if not _inventory_sync_status.get("running"):
            await asyncio.to_thread(_background_inventory_refresh, "scheduled")


@router.post("/inventory-refresh")
def inventory_refresh(background_tasks: BackgroundTasks):
    """Full refresh of remaining_inventory: fetches all ReachPro purchases,
    regroups by buyer/future-event, and replaces the table wholesale (inventory
    sells down over time, so a partial/incremental sync would go stale)."""
    if _inventory_sync_status.get("running"):
        return {"started": False, "message": "Inventory refresh already running"}
    background_tasks.add_task(_background_inventory_refresh)
    return {"started": True, "message": "Inventory refresh started in background — poll /sync/inventory-status"}


@router.get("/inventory-status")
def inventory_status():
    return _inventory_sync_status


@router.get("/inventory-last-refresh")
def inventory_last_refresh():
    """Timestamps for the inventory cache: when it last refreshed
    successfully (what the Excel export stamps) and how the latest attempt
    of any kind went (so a failing nightly run is visible)."""
    with engine.connect() as conn:
        last_success = conn.execute(text("""
            SELECT finished_at, trigger, listings_fetched, inventory_rows
            FROM inventory_refresh_runs
            WHERE error IS NULL AND finished_at IS NOT NULL
            ORDER BY finished_at DESC LIMIT 1
        """)).fetchone()
        last_attempt = conn.execute(text("""
            SELECT started_at, finished_at, trigger, error
            FROM inventory_refresh_runs
            ORDER BY started_at DESC LIMIT 1
        """)).fetchone()
        # Fallback for the period before run logging existed: the cache
        # rows' own synced_at is the successful-completion timestamp.
        synced_at = conn.execute(text("SELECT MAX(synced_at) FROM remaining_inventory")).scalar()

    # A run row with no finished_at is either in progress right now or its
    # process was killed mid-crawl (OOM, restart). Older than 4h = dead:
    # real runs take ~2h, and the DB is shared so the run may be another
    # instance's - only age distinguishes the two.
    attempt_error = last_attempt.error if last_attempt else None
    if last_attempt and last_attempt.finished_at is None and attempt_error is None:
        age_hours = (datetime.now(timezone.utc) - last_attempt.started_at).total_seconds() / 3600
        if age_hours > 4:
            attempt_error = (f"run started {last_attempt.started_at.isoformat()} never finished - "
                             "process likely restarted or ran out of memory mid-crawl")
    return {
        "last_success_at": (last_success.finished_at.isoformat() if last_success else (synced_at.isoformat() if synced_at else None)),
        "last_success_rows": last_success.inventory_rows if last_success else None,
        "last_attempt_at": ((last_attempt.finished_at or last_attempt.started_at).isoformat() if last_attempt else None),
        "last_attempt_error": attempt_error,
        "cache_synced_at": synced_at.isoformat() if synced_at else None,
    }


# ── Push payments to ReachPro ────────────────────────────────────────────────

_push_status: dict = {"running": False, "result": None, "error": None, "progress": None}

# Our marketplace name -> ReachPro's expected marketplace enum. Lysted is sold
# through other marketplaces, so its real enum comes from the "Sold To" value
# captured as the payout's notes at import time, not a fixed mapping.
PUSH_MARKETPLACE_MAP = {
    "Viagogo": "StubHub",
    "SeatGeek": "SeatGeek",
    "GoTickets": "GoTickets",
    "TickPick": "TickPick",
    "Gametime": "Gametime",
    "TicketNetwork": "TicketNetwork",
    "B2B": "Automatiq",
    "Tevo": "TicketEvolution",
}

LYSTED_SOLD_TO_MAP = {
    "stubhub": "StubHub", "viagogo": "StubHub", "vivid seats": "VividSeats", "vividseats": "VividSeats",
    "seatgeek": "SeatGeek", "ticketnetwork": "TicketNetwork", "ticket network": "TicketNetwork",
    "automatiq": "Automatiq", "go tickets": "GoTickets", "gotickets": "GoTickets", "tickpick": "TickPick",
    "gametime": "Gametime", "ticket evolution": "TicketEvolution", "ticketevolution": "TicketEvolution",
    "axs": "AXS", "fanxchange": "FanXchange", "lyte": "Lyte", "ticketmaster": "Ticketmaster",
}


def _resolve_push_marketplace(marketplace_name: str, notes: str):
    if marketplace_name == "Lysted":
        return LYSTED_SOLD_TO_MAP.get((notes or "").strip().lower())
    return PUSH_MARKETPLACE_MAP.get(marketplace_name)


def _api_call(method: str, path: str, body: dict = None):
    """Generic ReachPro API call supporting a JSON body (GET/POST/etc)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {**_get_headers(), "Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")[:400]
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def _get_or_create_payment(mkt_enum: str, batch_ref: str, total_amt: float, payment_date: str):
    """Idempotent: creates a new ReachPro payment header, or if one already
    exists for this batch_ref, finds and reuses its id instead of erroring."""
    status, resp = _api_call("POST", "/invoicepayments", {
        "marketplace": mkt_enum, "externalPaymentId": batch_ref, "paymentReferenceId": batch_ref,
        "paymentDate": payment_date, "receivedDate": payment_date, "paymentState": "Paid",
        "currencyCode": "USD", "paymentAmount": total_amt, "proceedsAmount": total_amt,
    })
    if status == 200:
        return resp.get("marketplacePaymentId"), None
    if status == 400 and "already exists" in str(resp).lower():
        url = "/invoicepayments?updateDateSince=2018-01-01T00:00:00"
        pages = 0
        while url and pages < 200:
            pages += 1
            st2, d2 = _api_get(url)
            if st2 != 200:
                break
            for p in (d2.get("payments") or []):
                if p.get("externalPaymentId") == batch_ref or p.get("paymentReferenceId") == batch_ref:
                    return (p.get("marketplacePaymentId") or p.get("id")), None
            token = d2.get("paginationToken") if isinstance(d2, dict) else None
            url = f"/invoicepayments?paginationToken={token}" if token else None
        return None, f"payment already exists but could not find its id for batch {batch_ref}"
    return None, f"HTTP {status}: {resp}"


def _get_pushable_rows(conn, import_file_ids: list[int] | None = None):
    """Rows eligible to push: matched to a real ReachPro sale, genuine Proceeds
    or a genuine Adjustment (never a bare synthetic Wastage row), and not
    already pushed. Parking passes are included - they're real sold/paid-out
    inventory and go through the same push flow as any other sale.
    Optionally scoped to a set of import_file_ids for a controlled/batch push."""
    query = """
        SELECT mp.payout_id, mp.marketplace_id, mp.marketplace_order_id, mp.amount, mp.notes,
               mp.import_file_id, mp.payment_type, m.name AS marketplace_name, s.reachpro_sale_id,
               pif.filename, pif.payment_date
        FROM marketplace_payouts mp
        JOIN marketplaces m ON m.marketplace_id = mp.marketplace_id
        JOIN sales s ON s.sale_id = mp.sale_id
        JOIN payout_import_files pif ON pif.import_file_id = mp.import_file_id
        WHERE mp.payment_type IN ('Payment', 'Adjustment')
          AND (mp.reachpro_push_status IS NULL OR mp.reachpro_push_status = 'pending')
          AND s.reachpro_sale_id IS NOT NULL
    """
    params = {}
    if import_file_ids is not None:
        query += " AND mp.import_file_id = ANY(:fids)"
        params["fids"] = import_file_ids
    return conn.execute(text(query), params).fetchall()


def _mark_pushed(db, payout_id, status, error=None):
    db.execute(text("""
        UPDATE marketplace_payouts SET reachpro_push_status = :status, reachpro_push_error = :err
        WHERE payout_id = :pid
    """), {"status": status, "err": error, "pid": payout_id})


def _run_push_payments(db, import_file_ids: list[int] | None = None) -> dict:
    from collections import defaultdict

    stats = {
        "files_processed": 0, "lines_pushed": 0, "lines_failed": 0,
        "payments_created_or_reused": 0, "skipped_no_payment_date": 0,
        "skipped_unmapped_marketplace": 0, "skipped_already_paid": 0,
    }

    rows = _get_pushable_rows(db, import_file_ids)
    by_file = defaultdict(list)
    for r in rows:
        by_file[r.import_file_id].append(r)

    total_files = len(by_file)
    for i, (import_file_id, file_rows) in enumerate(by_file.items(), 1):
        filename = file_rows[0].filename
        payment_date_raw = file_rows[0].payment_date
        _push_status["progress"] = {
            "phase": "processing", "file": filename, "file_index": i, "total_files": total_files,
            "lines_pushed": stats["lines_pushed"], "lines_failed": stats["lines_failed"],
        }

        if not payment_date_raw:
            stats["skipped_no_payment_date"] += len(file_rows)
            continue
        payment_date = payment_date_raw.isoformat() + "T00:00:00"

        invoice_cache = {}

        def get_invoice(order_id):
            if order_id not in invoice_cache:
                invoice_cache[order_id] = _fetch_invoice_direct(order_id)
            return invoice_cache[order_id]

        by_mkt_enum = defaultdict(list)
        for r in file_rows:
            mkt_enum = _resolve_push_marketplace(r.marketplace_name, r.notes)
            if not mkt_enum:
                # Lysted leaves "Sold To" blank on adjustment rows - fall back to
                # asking ReachPro directly which marketplace the matched sale is on.
                inv = get_invoice(r.marketplace_order_id)
                mkt_enum = inv.get("marketplace") if inv else None
            if not mkt_enum:
                stats["skipped_unmapped_marketplace"] += 1
                continue
            by_mkt_enum[mkt_enum].append(r)

        for mkt_enum, mkt_rows in by_mkt_enum.items():
            # Live safety check, Payment rows only: don't push a Payment line
            # onto an invoice ReachPro already shows as Paid - it may have been
            # settled outside this pipeline (manual UI payments, the legacy CSV
            # era), where external-ID dedup can't catch the duplicate.
            # Adjustments are exempt: a clawback normally arrives after the
            # invoice is fully paid, so "already Paid" is its expected state,
            # not a duplicate signal. (Reuses the sales sync's response parsing
            # via get_invoice, which handles all three response shapes.)
            eligible = []
            for r in mkt_rows:
                if r.payment_type == "Payment":
                    sale = get_invoice(r.marketplace_order_id)
                    if sale and sale.get("paymentStatus") == "Paid":
                        stats["skipped_already_paid"] += 1
                        _mark_pushed(db, r.payout_id, "pushed", "Already marked Paid in ReachPro (skipped push)")
                        continue
                eligible.append(r)

            if not eligible:
                continue

            batch_ref = f"{filename}-{mkt_enum}"
            total = round(sum(float(r.amount) for r in eligible), 2)

            pid, err = _get_or_create_payment(mkt_enum, batch_ref, total, payment_date)
            if not pid:
                for r in eligible:
                    _mark_pushed(db, r.payout_id, "rejected", err)
                stats["lines_failed"] += len(eligible)
                continue
            stats["payments_created_or_reused"] += 1

            for r in eligible:
                line_id = f"{r.marketplace_id}-{r.marketplace_order_id}-{r.payout_id}"
                amt = float(r.amount)
                if r.payment_type == "Adjustment":
                    line_type = "Charge" if amt < 0 else "Credit"
                    # isCredit drives ReachPro's own header rollup (creditAmount
                    # vs chargeAmount) independent of paymentLineType - confirmed
                    # empirically: a positive Credit line only lands in
                    # creditAmount when isCredit=True.
                    is_credit = amt > 0
                else:
                    line_type = "Proceeds"
                    is_credit = False

                # ReachPro also rejects a negative amount outright unless
                # isCredit=True (a separate API quirk discovered empirically in
                # the original import scripts) - retry True only if the API
                # specifically complains about it and we haven't already set it.
                for attempt in range(2):
                    status, resp = _api_call("POST", f"/invoicepayments/{pid}/paymentlines", {
                        "marketplacePaymentId": pid, "externalPaymentId": batch_ref,
                        "externalPaymentLineId": line_id, "externalInvoiceId": r.marketplace_order_id,
                        "invoiceId": r.reachpro_sale_id, "amount": amt, "currencyCode": "USD",
                        "isCredit": is_credit, "paymentLineType": line_type,
                    })
                    if status == 200 or not (status == 400 and "negative" in str(resp).lower() and not is_credit):
                        break
                    is_credit = True

                if status == 200:
                    _mark_pushed(db, r.payout_id, "pushed", None)
                    stats["lines_pushed"] += 1
                else:
                    _mark_pushed(db, r.payout_id, "rejected", f"HTTP {status}: {resp}"[:500])
                    stats["lines_failed"] += 1
                _push_status["progress"] = {
                    "phase": "processing", "file": filename, "file_index": i, "total_files": total_files,
                    "lines_pushed": stats["lines_pushed"], "lines_failed": stats["lines_failed"],
                }

        stats["files_processed"] += 1
        db.commit()

    return stats


def _background_push_payments(import_file_ids: list[int] | None = None):
    global _push_status
    _push_status = {"running": True, "result": None, "error": None, "progress": None}
    try:
        with engine.connect() as conn:
            result = _run_push_payments(conn, import_file_ids)
        _push_status = {"running": False, "result": result, "error": None, "progress": None}
    except Exception as e:
        _push_status = {"running": False, "result": None, "error": str(e), "progress": _push_status.get("progress")}


class PushPaymentsRequest(BaseModel):
    import_file_ids: Optional[list[int]] = None


@router.post("/push-payments")
def push_payments(background_tasks: BackgroundTasks, body: PushPaymentsRequest = PushPaymentsRequest()):
    """Push matched Payment and Adjustment payouts into ReachPro as invoice
    payments (as Proceeds, Charge, or Credit lines respectively).
    Skips rows whose file has no payment_date set, whose marketplace can't be
    mapped to a ReachPro enum, or that ReachPro already shows as Paid. Already-
    pushed rows are never reprocessed (tracked via reachpro_push_status).
    Pass import_file_ids to scope the push to specific files (e.g. one
    just-committed batch) instead of every ready row."""
    if _push_status.get("running"):
        return {"started": False, "message": "Payment push already running"}
    background_tasks.add_task(_background_push_payments, body.import_file_ids)
    return {"started": True, "message": "Payment push started in background — poll /sync/push-status"}


@router.get("/push-status")
def push_status():
    return _push_status


@router.get("/pending-push-count")
def pending_push_count():
    with engine.connect() as conn:
        rows = _get_pushable_rows(conn)
    with_date = sum(1 for r in rows if r.payment_date)
    return {
        "pending_count": len(rows),
        "ready_to_push": with_date,
        "missing_payment_date": len(rows) - with_date,
    }


@router.get("/files-missing-payment-date")
def files_missing_payment_date():
    """List already-imported files that have pushable rows but no payment_date
    set yet — these were imported before the payment-date field existed."""
    with engine.connect() as conn:
        rows = _get_pushable_rows(conn)
    by_file: dict = {}
    for r in rows:
        if r.payment_date:
            continue
        b = by_file.setdefault(r.import_file_id, {
            "import_file_id": r.import_file_id, "filename": r.filename,
            "marketplace": r.marketplace_name, "row_count": 0, "total_amount": 0.0,
        })
        b["row_count"] += 1
        b["total_amount"] += float(r.amount)
    result = list(by_file.values())
    for r in result:
        r["total_amount"] = round(r["total_amount"], 2)
    result.sort(key=lambda x: (x["marketplace"], x["filename"]))
    return {"files": result}


class PaymentDateUpdate(BaseModel):
    import_file_ids: list[int]
    payment_date: date


@router.put("/payout-files/payment-dates")
def set_payment_dates(body: PaymentDateUpdate):
    """Backfill payment_date on one or more already-imported files."""
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE payout_import_files SET payment_date = :pd
            WHERE import_file_id = ANY(:ids)
        """), {"pd": body.payment_date, "ids": body.import_file_ids})
    return {"updated": len(body.import_file_ids)}
