import json
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.services.parsers import parse_file, ParseResult


def get_marketplace_id(db: Session, name: str) -> int | None:
    row = db.execute(
        text("SELECT marketplace_id FROM marketplaces WHERE name = :name"),
        {"name": name}
    ).fetchone()
    return row[0] if row else None


def stage_files(db: Session, files: list[tuple[str, bytes]]) -> dict:
    """
    Validate and preview a list of (filename, content) pairs without writing to DB.
    Returns a summary for the user to review before committing.
    Deduplicates against both the DB and other files in the same batch.
    """
    # Parse all files first. A file that blows up the parser (bad encoding,
    # mangled structure) becomes a per-file error entry in the preview - it
    # must never 500 the whole staging request, which the browser reports as
    # an unhelpful "Network Error".
    parsed_results = []
    for filename, content in files:
        try:
            result = parse_file(content, filename)
        except Exception as e:
            result = ParseResult(
                marketplace_name="Unknown", filename=filename, file_hash="",
                rows=[], skipped=[],
                errors=[f"Could not parse file: {type(e).__name__}: {e}"],
            )
        marketplace_id = get_marketplace_id(db, result.marketplace_name) if result.marketplace_name != "Unknown" else None
        parsed_results.append((filename, content, result, marketplace_id))

    seen_keys: set[str] = set()  # cross-file dedup within this staging batch

    results = []
    for filename, content, result, marketplace_id in parsed_results:
        # Check if file already imported (by hash)
        already_imported = False
        if result.file_hash:
            row = db.execute(
                text("SELECT import_file_id FROM payout_import_files WHERE file_hash = :h"),
                {"h": result.file_hash}
            ).fetchone()
            if row:
                already_imported = True

        # Check each row against DB and other files in this batch
        new_rows = []
        duplicate_rows = []
        for payout in result.rows:
            payout_key = f"{marketplace_id}:{payout.marketplace_order_id}:{payout.amount:.2f}"
            if payout_key in seen_keys:
                duplicate_rows.append(payout.marketplace_order_id)
                continue
            exists = db.execute(
                text("SELECT 1 FROM marketplace_payouts WHERE payout_key = :k"),
                {"k": payout_key}
            ).fetchone()
            if exists:
                duplicate_rows.append(payout.marketplace_order_id)
            else:
                new_rows.append(payout)
                seen_keys.add(payout_key)

        # Some marketplaces (Gametime, TickPick, GoTickets) already derive a
        # per-file payment_date from the filename during parsing — reuse it as
        # the auto-filled payment date. Others have no filename date signal and
        # need the user to enter one manually before the file can be committed.
        auto_payment_date = result.rows[0].payment_date if result.rows else None

        results.append({
            "filename": filename,
            "marketplace": result.marketplace_name,
            "marketplace_id": marketplace_id,
            "file_hash": result.file_hash,
            "already_imported": already_imported,
            "total_rows_in_file": len(result.rows) + len(result.skipped),
            "importable_rows": len(result.rows),
            "new_rows": len(new_rows),
            "duplicate_rows": len(duplicate_rows),
            "skipped_rows": len(result.skipped),
            "skipped_details": result.skipped[:10],
            "errors": result.errors,
            "can_import": not already_imported and marketplace_id is not None and len(result.errors) == 0,
            "auto_payment_date": auto_payment_date,
        })

    total_new = sum(r["new_rows"] for r in results)
    total_dupes = sum(r["duplicate_rows"] for r in results)
    files_already_imported = sum(1 for r in results if r["already_imported"])
    files_unrecognized = sum(1 for r in results if r["marketplace"] == "Unknown")

    return {
        "files": results,
        "summary": {
            "total_files": len(results),
            "files_ready": sum(1 for r in results if r["can_import"]),
            "files_already_imported": files_already_imported,
            "files_unrecognized": files_unrecognized,
            "total_new_rows": total_new,
            "total_duplicate_rows": total_dupes,
        }
    }


def commit_files(db: Session, files: list[tuple[str, bytes]], imported_by: str, payment_dates: dict | None = None) -> dict:
    """
    Import validated files into the DB. Skips files already imported or unrecognized.
    payment_dates maps filename -> "YYYY-MM-DD", overriding the filename-derived
    date where the user entered one manually (required for marketplaces with no
    date signal in the filename).
    Returns a detailed import summary.
    """
    payment_dates = payment_dates or {}
    imported_files = 0
    total_rows_imported = 0
    total_rows_skipped = 0
    total_rows_errored = 0
    file_results = []

    for filename, content in files:
        result = parse_file(content, filename)
        marketplace_id = get_marketplace_id(db, result.marketplace_name)

        if not marketplace_id:
            file_results.append({
                "filename": filename,
                "status": "skipped",
                "reason": f"Unrecognized marketplace: '{result.marketplace_name}'",
                "rows_imported": 0,
            })
            continue

        if result.errors:
            file_results.append({
                "filename": filename,
                "status": "skipped",
                "reason": "; ".join(result.errors),
                "rows_imported": 0,
            })
            continue

        # Check file hash
        existing = db.execute(
            text("SELECT import_file_id FROM payout_import_files WHERE file_hash = :h"),
            {"h": result.file_hash}
        ).fetchone()
        if existing:
            file_results.append({
                "filename": filename,
                "status": "skipped",
                "reason": "File already imported (duplicate hash)",
                "rows_imported": 0,
            })
            continue

        # Payment date: prefer a manually-entered override, else fall back to
        # whatever the parser derived from the filename (if any).
        auto_payment_date = result.rows[0].payment_date if result.rows else None
        payment_date = payment_dates.get(filename) or auto_payment_date

        # Create import file record
        import_file_row = db.execute(
            text("""
                INSERT INTO payout_import_files (marketplace_id, filename, file_hash, row_count, imported_by, payment_date)
                VALUES (:mid, :fn, :fh, :rc, :ib, :pd)
                RETURNING import_file_id
            """),
            {
                "mid": marketplace_id,
                "fn": filename,
                "fh": result.file_hash,
                "rc": len(result.rows),
                "ib": imported_by,
                "pd": payment_date,
            }
        ).fetchone()
        import_file_id = import_file_row[0]

        # Persist every row the parser dropped (blank/malformed order ID, footer
        # rows, etc.) - otherwise this data only ever existed in the ephemeral
        # staging-preview response and was unrecoverable after commit.
        for skip in result.skipped:
            db.execute(
                text("""
                    INSERT INTO import_skipped_rows (import_file_id, filename, marketplace_id, reason, raw_row)
                    VALUES (:fid, :fn, :mid, :reason, :raw)
                """),
                {
                    "fid": import_file_id,
                    "fn": filename,
                    "mid": marketplace_id,
                    "reason": skip.get("reason", ""),
                    "raw": json.dumps(skip.get("row", {})),
                }
            )

        rows_imported = 0
        rows_skipped = 0
        rows_errored = 0
        duplicate_details = []  # sample of order IDs that already existed (payout_key conflict)
        row_errors = []         # sample of rows that failed for a reason other than a conflict
        for payout in result.rows:
            payout_key = f"{marketplace_id}:{payout.marketplace_order_id}:{payout.amount:.2f}"

            try:
                # Each row gets its own SAVEPOINT so that an unexpected failure on
                # one row (anything other than the expected ON CONFLICT) only rolls
                # back that row, instead of aborting the whole transaction and
                # silently discarding every row already inserted earlier in this file.
                with db.begin_nested():
                    result_row = db.execute(
                        text("""
                            INSERT INTO marketplace_payouts
                                (marketplace_id, marketplace_order_id, amount, payment_date,
                                 import_file_id, payout_key, payment_type, is_parking, notes, raw_data)
                            VALUES
                                (:mid, :oid, :amt, :pd, :fid, :pk, :payment_type, :is_parking, :notes, :rd)
                            ON CONFLICT (payout_key) DO NOTHING
                            RETURNING payout_id
                        """),
                        {
                            "mid": marketplace_id,
                            "oid": payout.marketplace_order_id,
                            "amt": payout.amount,
                            "pd": payout.payment_date,
                            "fid": import_file_id,
                            "pk": payout_key,
                            "payment_type": payout.payment_type,
                            "is_parking": payout.is_parking,
                            "notes": payout.notes,
                            "rd": json.dumps(payout.raw_data),
                        }
                    ).fetchone()
                if result_row:
                    rows_imported += 1
                else:
                    rows_skipped += 1  # ON CONFLICT hit - key already exists
                    # Flag every collision for a human to confirm it really is
                    # the same transaction re-arriving, not two distinct ones
                    # that happen to share marketplace+order_id+amount. No
                    # automatic tiebreaker - a person decides, always.
                    db.execute(
                        text("""
                            INSERT INTO import_duplicate_reviews
                                (import_file_id, filename, marketplace_id, marketplace_order_id, amount, payout_key, raw_row)
                            VALUES
                                (:fid, :fn, :mid, :oid, :amt, :pk, :raw)
                        """),
                        {
                            "fid": import_file_id,
                            "fn": filename,
                            "mid": marketplace_id,
                            "oid": payout.marketplace_order_id,
                            "amt": payout.amount,
                            "pk": payout_key,
                            "raw": json.dumps(payout.raw_data),
                        }
                    )
                    if len(duplicate_details) < 20:
                        duplicate_details.append({
                            "order_id": payout.marketplace_order_id,
                            "amount": payout.amount,
                        })
            except Exception as e:
                rows_errored += 1
                if len(row_errors) < 20:
                    row_errors.append({
                        "order_id": payout.marketplace_order_id,
                        "amount": payout.amount,
                        "error": str(e)[:300],
                    })

        db.commit()
        imported_files += 1
        total_rows_imported += rows_imported
        total_rows_skipped += rows_skipped
        total_rows_errored += rows_errored

        file_results.append({
            "filename": filename,
            "marketplace": result.marketplace_name,
            "status": "imported",
            "rows_imported": rows_imported,
            "rows_skipped_duplicate": rows_skipped,
            "rows_errored": rows_errored,
            "rows_skipped_parse": len(result.skipped),
            "import_file_id": import_file_id,
            "duplicate_details": duplicate_details,
            "row_errors": row_errors,
            "skipped_details": result.skipped[:10],
        })

    return {
        "files": file_results,
        "summary": {
            "files_imported": imported_files,
            "files_skipped": len(files) - imported_files,
            "total_rows_imported": total_rows_imported,
            "total_rows_skipped": total_rows_skipped,
            "total_rows_errored": total_rows_errored,
        }
    }
