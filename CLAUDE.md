# Reachpro Payment Reconciliation App

## Project Overview
A payment reconciliation web application for a ticket resale business. Replaces a manual CSV-based process. Tracks marketplace payouts, matches them to sales, and calculates purchaser commission payouts.

## Tech Stack
- **Backend**: Python 3.14 / FastAPI / SQLAlchemy
- **Database**: PostgreSQL 18, hosted on Render Postgres (`accounting_backend` instance) — no local Postgres needed
- **Frontend**: React 19 / Vite / antd, in `frontend/`
- **File Storage**: AWS S3 (production), local filesystem (dev)
- **Hosting**: Render (production)

## Project Structure
```
reachpro-recon/
  backend/
    app/
      main.py          # FastAPI app entry point
      config.py        # Settings from .env
      database.py      # SQLAlchemy engine + session
      routers/
        imports.py     # Stage 1: payout file upload endpoints
      services/
        parsers.py     # Per-marketplace CSV parsers
        import_service.py  # Staging + commit logic
    migrations/
      schema.sql       # Full DB schema (run once to create tables)
    .env               # Local secrets (not in git)
    requirements.txt
  frontend/
    src/pages/               # Nav is 3 items + Admin, most pages nest as tabs
      ViewPayouts.jsx        # "Marketplace Payouts": tabs Payouts | Duplicate Review | Sync & Push
      ImportPayouts.jsx      # In-modal import wizard incl. auto-sync + resolve + per-batch push
      DuplicateReview.jsx    # Tab: review queue for payout_key collisions on import
      SyncPush.jsx           # Tab: backlog-wide sync retry + push + payment-date backfill
      PayoutPrep.jsx         # "Payout Prep": tabs Adjustments | Splits
      Adjustments.jsx        # Commission-treatment/reason review for Adjustment rows
      SplitSales.jsx
      PurchaserPayouts.jsx   # "Purchaser Payouts": tabs Generate | History
      GeneratePayouts.jsx
      PayoutHistory.jsx
      Purchasers.jsx         # Admin
      Login.jsx              # JWT login (all API routes require auth)
    src/components/
      ResolveMatchActions.jsx  # Retry-with-corrected-ID / dismiss-with-note, shared
```

## Running Locally
```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8001

cd ../frontend
npm install
npm run dev   # pinned to port 5174 in vite.config.js
```
Requires `backend/.env` with `DATABASE_URL`, `SECRET_KEY`, `STUBHUB_BEARER_TOKEN` — not committed, get these from another device/teammate that already has them.
API docs: http://localhost:8001/docs — Frontend: http://localhost:5174

Ports are pinned to 8001/5174 (not the usual 8000/5173) so this can run alongside a sibling `reachpro-recon` checkout without colliding. If you're not running both side by side, the non-default ports are harmless — just make sure `frontend/vite.config.js`, `backend/app/main.py` (CORS allowlist), and `frontend/src/api.js` (baseURL) all agree if you ever change them.

## Database
- Hosted on Render Postgres — connection string lives in `backend/.env` as `DATABASE_URL`
- No local Postgres install needed; the app connects directly to Render from anywhere
- To reset: drop and recreate the Render database, then re-run `migrations/schema.sql`

## Marketplaces (9 supported)
| Marketplace   | File Pattern                        | Order ID Field  | Amount Field  |
|---------------|-------------------------------------|-----------------|---------------|
| Viagogo       | `Viagogo *.csv`                     | TransactionID   | Proceeds      |
| SeatGeek      | `sp_*_charges.csv`                  | Order ID        | Amount        |
| Lysted        | `lysted-remit-*.csv`                | Reference       | Payout        |
| Gametime      | `gametime-payout-*.csv`             | #               | Payout        |
| TickPick      | `remittance_tickpick*.csv`          | Order Number    | Payout        |
| GoTickets     | `gotickets_*.csv`                   | Order ID        | Amount (net)  |
| TicketNetwork | `TN_Fulfillment_Remittance_*.csv`   | ORDER ID        | TOTAL         |
| Tevo          | `Tevo_orders_*.csv`                 | ID              | Credit * 0.97 |
| B2B           | `b2b_settlement_transactions-*.csv` | Sale Id         | Net Payout    |

## Key Business Rules
- Payout dedup: file hash (blocks re-import) + payout_key = `marketplace_id:order_id:amount`
  - Each row insert runs in its own SAVEPOINT so one bad row can't poison the rest of the file's import
  - Any payout_key collision (row dropped as a duplicate) is persisted to `import_duplicate_reviews` for human review — see the Duplicate Review page. No automatic tiebreaker by design.
  - Rows the parser drops entirely (blank/malformed order ID, footer rows) are persisted to `import_skipped_rows`, not just shown in the ephemeral staging preview
- Negative marketplace payouts are stored as-is and netted against purchaser payouts (clawback)
- Parking passes (`is_parking` flag, auto-detected from raw CSV text) go through the exact same match/push/commission pipeline as ticket sales — they are real sold/paid-out inventory, not excluded
- Commission = P&L × commission_pct (per-purchaser, with per-event-tag overrides)
- Purchaser payout lifecycle: `pending_review → approved → paid` (or `cancelled` before paid)
- Payout export: 1 row per MarketplacePayout line item (not per sale)

## Workflow Stages
1. **Import marketplace payouts** — upload CSVs via the modal on Marketplace Payouts → stage preview → commit. Matching to ReachPro then runs **automatically**, scoped to the committed files; the same screen requires every unmatched row to be resolved (manual-match retry or dismiss-with-note) before its per-batch **Push to ReachPro** unlocks.
2. **Backlog sync & push** — the Sync & Push tab on Marketplace Payouts retries matching across the whole backlog (for sales ReachPro processed late) and pushes rows that became pushable after their import session. Push creates real, irreversible ReachPro payment records — always human-triggered, never automatic.
3. **Payout Prep** — review Adjustments (reason + commission treatment) and multi-purchaser Splits before generating.
4. **Generate purchaser payout reports** — calculate commission → Excel export → batch lifecycle (Purchaser Payouts page, Generate + History tabs).

## Requirements Doc (reference only, machine-local paths - not needed to run the app)
Full data model and requirements were originally at `Payment Reconciliation Requirements v1.3.docx` and a set of original import scripts, both on the machine this project was first built on. Not required for running or developing the app day-to-day; `parsers.py` already has the per-marketplace CSV logic these were the source for.

## ReachPro API
- Base URL: `https://pointofsaleapi.stubhub.net`
- Auth: Bearer token via `STUBHUB_BEARER_TOKEN` env var
- Account ID: `691ed4c2-dfe4-4435-82ee-d9fb49d1f2cc`
- Key endpoints: `/invoices/marketplace/{id}` (resolve sale), `/invoicepayments` (create payment)
