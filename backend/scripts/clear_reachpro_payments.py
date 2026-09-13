"""
Cross-references every file in the "Processed Remission Files" folder against
ReachPro's actual /invoicepayments records, and deletes any payment (header +
its lines, via a single DELETE — confirmed to cascade) that matches one of
those files.

Matching works against both naming conventions ever used to create these
payments:
  - The original import_*.py scripts: externalPaymentId = "{PREFIX}-{filename
    without .csv}" (e.g. "VG-Viagogo 65801166")
  - This app's push feature: externalPaymentId = "{filename}-{marketplace
    enum}" (e.g. "Viagogo 65801166.csv-StubHub")
Both always contain the bare filename (minus .csv) as a substring, so matching
on that substring catches payments created either way without needing to know
which convention produced them.

Usage:
    .venv/Scripts/python.exe scripts/clear_reachpro_payments.py            # dry run — reports matches only
    .venv/Scripts/python.exe scripts/clear_reachpro_payments.py --execute  # actually deletes

Note observed during testing: deleting a payment removes the payment record,
but does NOT revert the invoice's own paymentStatus field back to "Unpaid" —
it stays "Paid" even after the payment backing it is gone. If you plan to
re-push these later, be aware the "already Paid" safety check in the push
feature will still see these invoices as Paid and skip them.
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.routers.sync import _api_get, _api_call  # noqa: E402

PROCESSED_DIR = r"C:\Users\ijkup\OneDrive\docs\TK\Reachpro payment reconciliation\Processed Remission Files"


def get_local_filenames() -> list[str]:
    files = glob.glob(os.path.join(PROCESSED_DIR, "*.csv"))
    return [os.path.basename(f)[:-4].strip() for f in files]


def fetch_all_payments() -> list[dict]:
    payments = []
    url = "/invoicepayments?updateDateSince=2018-01-01T00:00:00"
    pages = 0
    while url:
        pages += 1
        status, data = _api_get(url)
        if status != 200:
            print(f"  error on page {pages}: HTTP {status}")
            break
        batch = (data.get("payments") or []) if isinstance(data, dict) else []
        if not batch:
            break
        payments.extend(batch)
        if pages % 20 == 0:
            print(f"  fetched {len(payments)} payments so far ({pages} pages)...")
        token = data.get("paginationToken") if isinstance(data, dict) else None
        url = f"/invoicepayments?paginationToken={token}" if token else None
    return payments


def find_matches(local_names: list[str], payments: list[dict]) -> list[tuple[str, dict]]:
    matches = []
    for p in payments:
        ext_id = p.get("externalPaymentId") or ""
        ref_id = p.get("paymentReferenceId") or ""
        for name in local_names:
            if name in ext_id or name in ref_id:
                matches.append((name, p))
                break
    return matches


def main(execute: bool):
    local_names = get_local_filenames()
    print(f"Found {len(local_names)} local files in Processed Remission Files\n")

    print("Fetching all ReachPro payments (this may take a while)...")
    payments = fetch_all_payments()
    print(f"Fetched {len(payments)} total payments from ReachPro\n")

    matches = find_matches(local_names, payments)
    print(f"{len(matches)} ReachPro payments match a local file:\n")

    total_amount = 0.0
    total_lines = 0
    for name, p in matches:
        lines = p.get("paymentLines") or []
        total_lines += len(lines)
        total_amount += float(p.get("paymentAmount") or 0)
        print(f"  [{name}] paymentId={p.get('marketplacePaymentId')} "
              f"marketplace={p.get('marketplace')} amount={p.get('paymentAmount')} "
              f"lines={len(lines)} externalPaymentId={p.get('externalPaymentId')}")

    print(f"\nTOTAL: {len(matches)} payments, {total_lines} lines, ${total_amount:,.2f}")

    if not execute:
        print("\nDRY RUN — nothing deleted. Re-run with --execute to actually delete these.")
        return

    print("\nDeleting...")
    deleted, failed = 0, 0
    for name, p in matches:
        pid = p.get("marketplacePaymentId")
        for attempt in range(6):
            status, resp = _api_call("DELETE", f"/invoicepayments/{pid}")
            if status == 200:
                deleted += 1
                break
            if status == 429:
                wait = 65
                print(f"  rate limited on {pid}, waiting {wait}s (attempt {attempt + 1}/6)...")
                time.sleep(wait)
                continue
            failed += 1
            print(f"  FAILED to delete {pid}: HTTP {status} {resp}")
            break
        else:
            failed += 1
            print(f"  FAILED to delete {pid}: gave up after repeated rate limiting")
        time.sleep(0.3)

    print(f"\nDone. Deleted {deleted}, failed {failed}.")


if __name__ == "__main__":
    main(execute="--execute" in sys.argv)
