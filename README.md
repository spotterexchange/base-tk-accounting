# Reachpro Payment Reconciliation

A web app that reconciles **marketplace payouts** for a ticket-resale (brokerage)
business against sales recorded in **StubHub ReachPro**, then calculates and tracks
**commission payouts owed to the people who supplied the inventory** ("purchasers").

It replaces a manual, error-prone CSV-in-Excel process. The core problem it solves:
nine different ticket marketplaces each send payout files in their own format, those
payouts have to be matched back to the underlying sale in ReachPro, the payment has to
be recorded back *into* ReachPro, and then each purchaser has to be paid a commission on
the profit — with clawbacks, splits, wastage, and per-purchaser/per-event rate rules all
handled correctly.

> **New to the project? Read this file top to bottom once.** The "How it all fits
> together" section is the mental model; everything else is reference.

---

## 1. The domain in plain English

- **Marketplace** — where a ticket was sold (Viagogo, SeatGeek, Lysted, Gametime,
  TickPick, GoTickets, TicketNetwork, Tevo, B2B). Each pays out on its own schedule and
  file format.
- **Payout** — one line in a marketplace's remittance file: "we're paying you $X for
  order Y." Can be positive (a sale) or negative (a chargeback/clawback/correction).
- **Sale / Invoice** — the record of that transaction inside **ReachPro** (StubHub's
  point-of-sale API). It carries the true cost, proceeds, event, tags, and — critically —
  which **buyer** (purchaser) owned the tickets.
- **Purchaser** — a partner / broker / in-house account that supplied inventory. Each is
  identified in ReachPro by a `buyerUserId` GUID and earns a **commission** on the P&L
  of what they sold.
- **P&L** — proceeds − cost, per sale.
- **Commission** — `P&L × commission_pct`. The rate is per-purchaser, with optional
  per-event-tag and date-based overrides.
- **ReachPro** — the StubHub Reach Pro POS API (`https://pointofsaleapi.stubhub.net`,
  swagger at `/swagger/v1/swagger.json`). The app both **reads** from it (to match sales)
  and **writes** to it (to record that a marketplace paid an invoice).

The whole app is a pipeline that turns *"a stack of CSVs from 9 marketplaces"* into
*"correct commission checks for each purchaser, and correct payment records in ReachPro."*

---

## 2. How it all fits together (the pipeline)

```
   Marketplace CSVs                         ReachPro API
        │                                    ▲        │
        ▼                                    │ read   │ write
 ┌──────────────┐   parse+dedup   ┌──────────────────┐   push payments   ┌──────────┐
 │ Stage 1:     │────────────────▶│ marketplace_     │──────────────────▶│ ReachPro │
 │ Import       │                 │ payouts (rows)   │   (Stage 2)       │ invoice  │
 └──────────────┘                 └──────────────────┘                   │ payments │
                                          │                              └──────────┘
                    match to ReachPro sale│ (auto after import, or backlog sync)
                                          ▼
                                   ┌──────────────┐
                                   │ sales        │  ← proceeds, cost, pnl, tags,
                                   │ (+ splits)   │    purchaser splits, purchased_date
                                   └──────────────┘
                                          │
                    Stage 3: review adjustments / splits / offsets
                                          │
                                          ▼
                                   ┌──────────────┐   Stage 4: commission engine
                                   │ purchaser_   │──────────────────────────────▶ Excel export
                                   │ payouts      │   pending_review → approved → paid
                                   └──────────────┘
```

**The four workflow stages** (they map directly to the top-level nav):

1. **Import marketplace payouts** *(Marketplace Payouts page → import modal)*
   Upload CSVs → the app auto-detects each marketplace by filename → parses → shows a
   staging preview (new rows / duplicates / skipped) → you commit. On commit, matching to
   ReachPro runs **automatically**, scoped to just the files you imported. Every unmatched
   row must be resolved (manual re-match or dismiss-with-note) before that batch's **Push
   to ReachPro** unlocks.

2. **Backlog sync & push** *(Marketplace Payouts → Sync & Push tab)*
   A backlog-wide retry: re-attempts matching across *all* unmatched payouts (for sales
   ReachPro processed late), plus the broad sweeps (wastage, offline sales, tag/date
   drift), and pushes rows that became pushable after their original import.
   **Pushing creates real, irreversible ReachPro payment records — it is always
   human-triggered, never automatic.**

3. **Payout Prep** *(Payout Prep page)*
   Review **Adjustments** (assign a reason + commission treatment to clawback/correction
   rows), resolve **cancelled-sale offsets**, and confirm **multi-purchaser splits** before
   generating commissions.

4. **Generate purchaser payout reports** *(Purchaser Payouts page)*
   Run the commission engine over a date range → preview per purchaser → generate a batch
   → download a formatted Excel report → walk the batch through its lifecycle.

---

## 3. Tech stack & repo layout

- **Backend**: Python 3.14, FastAPI, SQLAlchemy (used with raw SQL via `text()`, not the
  ORM), openpyxl for Excel.
- **Frontend**: React 19 + Vite + Ant Design (`antd`), in `frontend/`.
- **Database**: PostgreSQL 18, hosted on **Render** (`accounting_backend` instance). No
  local Postgres — the app connects directly to Render from anywhere via `DATABASE_URL`.
- **File storage**: local filesystem in dev, S3 in production (columns exist for S3 URLs).
- **Auth**: JWT bearer tokens (7-day expiry), bcrypt password hashes. Every API route
  except `/auth` requires a valid token.
- **Hosting**: Render (always-on paid instance for this service).

```
backend/
  app/
    main.py            # FastAPI entry: routers, CORS, startup schedulers, /health
    config.py          # env settings (DATABASE_URL, SECRET_KEY, STUBHUB_BEARER_TOKEN)
    database.py        # SQLAlchemy engine + session
    auth.py            # JWT, password hashing, get_current_user / require_admin deps
    routers/
      auth.py             # POST /auth/login, GET /auth/me
      imports.py          # Stage 1: stage/commit payout files, skipped rows, batch status
      payouts.py          # list/filter marketplace payouts, unmatched, dismiss-match
      duplicate_reviews.py# payout_key collision review queue
      sync.py             # ★ ReachPro matching, push, inventory refresh, schedulers
      adjustments.py      # adjustment reason/treatment + cancelled-sale offset hunt
      sales.py            # split-sales listing + editing
      reports.py          # ★ commission engine, preview, generate, Excel export, lifecycle
      expenses.py         # per-purchaser manual expenses
      purchasers.py       # purchaser CRUD + commission override rules (admin)
      users.py            # user management (admin)
      cv_import.py        # CrowdVold sales import → create Offline sales in ReachPro
    services/
      parsers.py          # per-marketplace CSV parsers + marketplace auto-detection
      import_service.py   # staging preview + commit (dedup, savepoints, review queues)
      sales_import_service.py
  migrations/
    schema.sql         # full DB schema; run once to create all tables (+ seed marketplaces)
  scripts/
    clear_reachpro_payments.py
frontend/
  src/
    App.jsx            # nav shell: 3 main items + Admin section
    api.js             # axios baseURL + auth header
    auth.js            # token/role storage
    pages/             # one file per page/tab (see below)
    components/
      ResolveMatchActions.jsx  # shared retry-match / dismiss-with-note widget
```

**Frontend pages** (nav is **3 items + an Admin section**; most pages are tabbed):

| Nav item             | Page                  | Tabs / purpose |
|----------------------|-----------------------|----------------|
| Marketplace Payouts  | `ViewPayouts.jsx`     | **Payouts** (browse/filter) · **Duplicate Review** · **Sync & Push** |
|                      | `ImportPayouts.jsx`   | In-modal import wizard: stage → commit → auto-sync → resolve → per-batch push |
|                      | `DuplicateReview.jsx` | Review `payout_key` collisions flagged at import |
|                      | `SyncPush.jsx`        | Backlog-wide sync retry + push + payment-date backfill |
| Payout Prep          | `PayoutPrep.jsx`      | **Adjustments** (`Adjustments.jsx`) · **Splits** (`SplitSales.jsx`) |
| Purchaser Payouts    | `PurchaserPayouts.jsx`| **Generate** (`GeneratePayouts.jsx`) · **History** (`PayoutHistory.jsx`) |
| *Admin*              | `Purchasers.jsx`      | Purchasers, commission rates, override rules (rate edits are admin-only) |
| *Admin (admin only)* | `Users.jsx`           | User management |
| (CrowdVold)          | `CrowdVoldImport.jsx` | CrowdVold monthly sales import (see §8) |

---

## 4. Data model (the important tables)

Full DDL is in `backend/migrations/schema.sql`. The tables that matter most:

- **`marketplaces`** — the 9 supported marketplaces, seeded by the migration. Flags:
  `tracks_payment_date` (does the file/marketplace give a real payment date?). Two extra
  marketplaces — **`Wastage`** and **`Offline`** — are created on demand by the sync
  sweeps (see §6).
- **`payout_import_files`** — one row per uploaded CSV. `file_hash` is UNIQUE, so the exact
  same file can never be imported twice. Holds the file-level `payment_date`.
- **`marketplace_payouts`** — the heart of the app: one row per payout line. Key columns:
  - `payout_key` = `marketplace_id:order_id:amount` (UNIQUE) — the dedup key.
  - `sale_id` — set once matched to a ReachPro sale (NULL = unmatched).
  - `payment_type` — `Payment` (a real sale), `Adjustment` (correction/clawback), or the
    synthetic `Wastage`/`Payment` produced by sweeps.
  - `is_parking` — auto-detected parking passes; treated identically to ticket sales.
  - `reachpro_push_status` — `pending` / `pushed` / `rejected` (+ `reachpro_push_error`).
  - `commission_included_in_payout_id` — set when this row is locked into a commission
    batch (prevents double-paying).
  - `adjustment_*` — human-assigned reason + commission treatment for Adjustment rows.
  - `match_dismissed_*` — a human's "this will never match" decision + note.
  - `raw_data` (JSONB) — the original CSV row, kept forever.
- **`sales`** — a ReachPro invoice, mirrored locally. `reachpro_sale_id` UNIQUE.
  Holds `proceeds`, `cost`, `pnl`, `tags` (ReachPro invoice tag names), `purchased_date`
  (most recent PO purchase date across the sale's tickets), `cancellation_date` (the
  reliable "this sale is dead" signal — statuses alone lie), `fulfillment_status`, and the
  cancelled-sale `offset_resolution*` fields.
- **`sale_purchasers`** — many-to-many: a sale can be split across purchasers, with a
  `split_pct` each. Splits are derived from the per-ticket `buyerUserId` counts on the
  invoice.
- **`purchasers`** — partner/broker/in_house, `default_commission_pct`, and a
  `buyer_user_id` GUID that ties them to ReachPro tickets.
- **`commission_overrides`** — per-purchaser rate rules. A rule matches on `event_tag`
  substring, and/or `purchased_before` / `purchased_from` dates. Tag rules outrank
  date-only rules; first match wins; else the purchaser default.
- **`purchaser_payouts`** / **`purchaser_payout_lines`** — a generated commission batch
  and its per-payout-line breakdown. Lifecycle: `pending_review → approved → paid`
  (or `cancelled`; code also uses `committed`/`rolled_back` for the generate/rollback
  flow).
- **`purchaser_expenses`** — manual partner expenses that reduce P&L; claimed into a batch
  at generation, released on rollback.
- **Review / audit queues**: `import_skipped_rows` (rows the parser dropped),
  `import_duplicate_reviews` (payout_key collisions), `sync_runs` &
  `inventory_refresh_runs` (every sync/refresh attempt, logged at *start* so a killed run
  is visible as a failure).
- **Caches**: `po_purchase_dates` (PO id → purchase date; immutable, so never invalidated),
  `remaining_inventory` (future-event inventory per buyer, rebuilt wholesale nightly).
- **CrowdVold**: `cv_sales`, `cv_event_mappings` (see §8).

---

## 5. Stage 1 — Importing & parsing payout files

`services/parsers.py` has one parser per marketplace plus `detect_marketplace()`, which
maps a filename to a marketplace. File patterns (also in `CLAUDE.md`):

| Marketplace   | File pattern                        | Order ID field | Amount |
|---------------|-------------------------------------|----------------|--------|
| Viagogo       | `Viagogo *.csv`                     | TransactionID  | Proceeds (or −Charges / Credit) |
| SeatGeek      | `sp_*_charges.csv`                  | Order ID       | Amount |
| Lysted        | `lysted-remit-*.csv`                | Reference      | Total Payout (→ Payout) |
| Gametime      | `gametime-payout-*.csv`             | #              | Payout |
| TickPick      | `remittance_tickpick*.csv`          | Order Number   | Payout |
| GoTickets     | `gotickets_*.csv`                   | Order ID       | Amount |
| TicketNetwork | `TN_Fulfillment_Remittance_*.csv`   | ORDER ID       | TOTAL  |
| Tevo          | `Tevo_orders_*.csv`                 | ID             | Credit × 0.97 |
| B2B           | `b2b_settlement_transactions-*.csv` | Sale Id        | Net Payout |

Parser details worth knowing (they encode real, hard-won marketplace quirks):

- **Encoding**: files arrive as UTF-16, UTF-8 (±BOM), or Windows-1252. `_read_csv` sniffs
  and falls back to cp1252, which always decodes — so a weird file never 500s the import.
- **Payment vs Adjustment**: each parser classifies rows. A global rule in `parse_file`
  then reclassifies **any negative amount as an `Adjustment`** — a genuine sale payout can
  never be negative, so a negative is always a correction/chargeback.
- **Payment dates**: Gametime/TickPick/GoTickets derive a date from the *filename*; the
  others have no date signal and require the user to enter one at import (needed later for
  the ReachPro push and commission windowing).
- **Skipped rows**: blank/malformed order IDs, footer/summary rows, or wrong report types
  (e.g. a Tevo export with no `Credit` column) are dropped *loudly* and recorded in
  `import_skipped_rows`.

`services/import_service.py` does staging and commit:

- **`stage_files`** — parses everything, dedups against the DB *and* other files in the
  same batch, and returns a preview (new / duplicate / skipped counts) **without writing**.
- **`commit_files`** — writes for real. Two dedup layers: the file `file_hash` (blocks
  re-importing the same file) and the per-row `payout_key`. **Each row insert runs in its
  own `SAVEPOINT`**, so one bad row can't poison the rest of the file. Any `payout_key`
  collision is dropped (`ON CONFLICT DO NOTHING`) but recorded in
  `import_duplicate_reviews` for a human to confirm — **there is no automatic tiebreaker,
  by design.**

Endpoints: `POST /imports/stage`, `POST /imports/commit`, `GET /imports/skipped-rows`,
`GET /imports/batch-status`; duplicate queue at `GET/PUT /duplicate-reviews`.

---

## 6. Stage 2 — Matching & pushing to ReachPro (`routers/sync.py`)

This is the biggest and most important module. It's the only place that talks to the
ReachPro API. Base URL and account ID are constants at the top; auth is the
`STUBHUB_BEARER_TOKEN` bearer + an `Account-Id` header.

### Matching (sales sync)
`_run_sync` loads unmatched payouts (optionally scoped to just-imported files) and, for
each, calls `/invoices/marketplace/{order_id}` to find the ReachPro invoice — 20 threads
in parallel. On a hit, `_write_invoice_to_db` upserts a `sales` row (proceeds, cost, P&L,
tags, purchase date, cancellation date), derives **purchaser splits** from the invoice's
per-ticket `buyerUserId` counts, and links the payout to the sale. Known-unresolvable
order IDs (`DNE`, `carryover`, …) and human-dismissed rows are skipped.

- Runs **automatically** after an import (batch-scoped), and **on demand** for the whole
  backlog (`POST /sync/sales-from-api`). Progress is polled via `GET /sync/status`.
- Rejected/pending-sourcing invoices strip their tickets — the code falls back to the
  **originating listing** to recover buyer attribution and cost.

### Broad sweeps (full sync only)
A full (untargeted) sync also runs three sweeps over a rolling window:

- **`_sync_wasted`** — pulls `Wasted` invoices per buyer and writes a sale + a **negative
  payout** under a synthetic `Wastage` marketplace (represents inventory bought but never
  sold — a loss against the purchaser).
- **`_sync_offline`** — pulls **private ("Offline") sales** that have no marketplace payout
  file, and writes synthetic `Payment` rows so they earn commission like any other sale.
  Re-checks existing offline sales for price changes (proceeds are often entered as $0/$1
  placeholders and fixed later) — unless the row is already frozen inside a live commission
  batch.
- **`_sync_tags_and_dates`** — refreshes `tags`, `fulfillment_status`, `cancellation_date`
  (tag/status edits happen in ReachPro after a sale first synced) and back-fills
  `purchased_date` from a `/purchases/search` crawl (incremental, using the immutable
  `po_purchase_dates` cache).

### Pushing payments back to ReachPro
`_run_push_payments` (`POST /sync/push-payments`) is the **write path** — it records into
ReachPro that a marketplace actually paid an invoice. **This creates real, irreversible
payment records; it is always human-triggered.**

- Only pushes rows that are matched to a real sale, are `Payment` or `Adjustment` (never a
  bare synthetic Wastage row), and aren't already pushed (`reachpro_push_status`).
- Groups by import file → by ReachPro marketplace enum (`PUSH_MARKETPLACE_MAP`; Lysted's
  real marketplace comes from the "Sold To" note). Creates/reuses one **payment header**
  per `filename-marketplace` batch (idempotent via `externalPaymentId`), then posts one
  **payment line** per payout (`Proceeds`, `Charge`, or `Credit`).
- Safety checks encode ReachPro API quirks discovered empirically: skips a `Payment` line
  if ReachPro already shows the invoice `Paid`; retries with `isCredit=true` when ReachPro
  rejects a negative amount.

### Inventory refresh
`_run_inventory_refresh` (`POST /sync/inventory-refresh`) crawls **all future-event
inventory** account-wide, groups still-available tickets by (buyer, event name), and
**replaces `remaining_inventory` wholesale**. It's slow (~1.5–2h against the rate limiter)
and memory-sensitive (streamed as a generator to avoid OOMing the 512MB Render instance).
Runs nightly via `nightly_inventory_scheduler` (started in `main.py`, default 09:00 UTC)
and feeds the "Remaining Inventory" tab of each purchaser's Excel export.

> **Reliability pattern used throughout sync**: every long run inserts its `*_runs` row at
> *start* with `finished_at = NULL`, so a process killed mid-crawl (OOM / restart) shows up
> as a *failure* rather than silently vanishing.

---

## 7. Stages 3 & 4 — Adjustments, offsets, and the commission engine

### Payout Prep (`routers/adjustments.py`, `routers/sales.py`)
- **Adjustments** — each `Adjustment` payout gets a human-assigned **reason** (missed IHD,
  DOE, cancelled event, rejected replacements, other) and **commission treatment**:
  `full_amount`, `full_amount_minus_cost`, or `none`. This drives how it nets into P&L.
- **Cancelled-sale offset hunt** (`/adjustments/needs-offset`) — surfaces `Payment`s on
  cancelled sales that have **no offsetting clawback yet** (Automatiq/B2B files send the
  payout but never the clawback). A human either records a manual offset adjustment or
  marks the sale `ignored` with a note — otherwise a purchaser would earn commission on a
  sale that delivered nothing.
- **Splits** — review/adjust the per-purchaser split percentages the sync derived.

### Commission engine (`routers/reports.py`)
`_build_preview` is the core calculation. For a date range:

1. Pull eligible payouts (matched, not already in a batch, within the **effective date**
   window — the row's own payment date → the file's payment date → the import date, in that
   order of precedence).
2. For each payout, split it across its purchasers by `split_pct`.
3. Resolve the commission rate via `_resolve_commission` (overrides by tag/date, else the
   purchaser default).
4. Compute the P&L contribution per row via `_pnl_for_row` (a `Payment` nets cost; an
   `Adjustment` uses its assigned treatment; `Wastage` is the full negative amount).
5. `commission = pnl_share × rate`.
6. Attach each purchaser's **unclaimed expenses** (they reduce P&L; their commission
   impact = amount × default rate).
7. Surface two warnings before generation: **unpriced offline sales** (still $0/$1 in
   ReachPro) and **cancelled-unoffset sales**.

**Generate** (`POST /reports/generate`) freezes the preview into a `purchaser_payouts`
batch + lines, stamps each `marketplace_payout` with `commission_included_in_payout_id`
(so it can't be double-paid), and claims the expenses. **Rollback** reverses all of that.

**Download** (`GET /reports/purchaser-payouts/{id}/download`) builds a formatted **Excel
workbook** with three sheets: **Summary** (category breakdown + per-event breakdown +
expenses, with live `=SUM()` totals), **Raw Data** (every line, chargebacks/wastage
highlighted), and **Remaining Inventory** (from the cache, stamped with its last refresh
time).

Batch lifecycle endpoints: `/reports/purchaser-payouts` (list),
`.../{id}/commit`, `.../{id}/rollback`.

---

## 8. CrowdVold import (`routers/cv_import.py`)

A separate, self-contained feature (`CrowdVoldImport.jsx`) for a monthly **CrowdVold**
sales workbook. It stages rows from the workbook, lets a user **map CrowdVold events to
ReachPro events** (fuzzy search + manual mapping, or skip), previews per-row dispositions,
and — admin-only — **creates the approved rows in ReachPro as Offline sales** (marked
Fulfilled). Creation is **idempotent**: the CrowdVold order number is stored as the
ReachPro `marketplaceSaleId` and checked before every create, so retries never
double-create. It deliberately does *not* auto-trigger the offline sweep afterwards.

---

## 9. Auth & roles (`routers/auth.py`, `app/auth.py`)

- `POST /auth/login` returns a JWT (7-day expiry); `GET /auth/me` returns the current user.
- Every router except `/auth` is protected by `get_current_user` (wired in `main.py`).
- **Admin-only** (`require_admin`): user management, changes to purchaser commission terms
  (rates + override rules), and the CrowdVold create step. Regular users keep read access
  and expense management.
- Passwords are bcrypt-hashed; tokens are HS256 signed with `SECRET_KEY`.

---

## 10. Running locally

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt          # Windows paths per CLAUDE.md
.venv/Scripts/uvicorn app.main:app --host 127.0.0.1 --port 8001

cd ../frontend
npm install
npm run dev            # pinned to port 5174
```

Requires `backend/.env` (not committed) with:

- `DATABASE_URL` — the Render Postgres connection string.
- `SECRET_KEY` — JWT signing secret.
- `STUBHUB_BEARER_TOKEN` — ReachPro API bearer token.

Optional env: `ALLOWED_ORIGINS` (CORS allowlist), `INVENTORY_REFRESH_UTC_HOUR`,
`FULFILLMENT_URL`.

- API docs (Swagger UI): http://localhost:8001/docs
- Frontend: http://localhost:5174
- Health check: `GET /health` (also verifies the DB connection)

Ports are intentionally **8001/5174** (not the usual 8000/5173) so this can run beside a
sibling checkout. If you change them, keep `frontend/vite.config.js`,
`main.py` (CORS), and `frontend/src/api.js` (baseURL) in agreement.

**Database setup / reset**: there are no migrations tooling — the schema is a single file.
To create everything, run `backend/migrations/schema.sql` once against the database (it
also seeds the 9 marketplaces). To reset, drop & recreate the Render DB and re-run it.

---

## 11. ReachPro API reference

- **Base URL**: `https://pointofsaleapi.stubhub.net` (swagger: `/swagger/v1/swagger.json`)
- **Auth**: `Authorization: Bearer <STUBHUB_BEARER_TOKEN>` + `Account-Id` header.
- **Account ID**: `691ed4c2-dfe4-4435-82ee-d9fb49d1f2cc`
- **Endpoints the app uses**:
  - `GET /invoices/marketplace/{order_id}` — resolve a payout to a sale (matching).
  - `GET /invoices/search`, `GET /invoices/{id}` — bulk sweeps (offline, tags, status).
  - `GET /purchases/search`, `GET /purchases/{id}` — PO purchase dates.
  - `GET /inventory/search`, `GET /inventory/{id}` — remaining-inventory refresh + listing
    fallback for stripped invoices.
  - `POST /invoicepayments`, `POST /invoicepayments/{id}/paymentlines` — **write path**:
    record that a marketplace paid an invoice.

> ReachPro's API has several undocumented behaviors the code works around (marketplace
> filters are ignored on `/invoices/search`; `buyerUserId`-filtered pages cap at 10 items;
> negative amounts need `isCredit=true`; cancellation dates only appear on the per-invoice
> detail). These are documented inline in `sync.py` — read those comments before changing
> sync logic.

---

## 12. Business rules cheat-sheet

- **Dedup**: file hash blocks re-imports; `payout_key` = `marketplace_id:order_id:amount`
  dedups rows. Collisions go to human review — never auto-resolved.
- **Negative payouts** are stored as-is (as Adjustments) and net against a purchaser's
  commission (clawback).
- **Parking passes** (`is_parking`) go through the exact same match/push/commission
  pipeline as ticket sales — they're real inventory, not excluded.
- **Commission** = `P&L × commission_pct`, per purchaser, with per-event-tag / date
  overrides. 0%-commission purchasers are skipped entirely.
- **Purchaser payout lifecycle**: `pending_review → approved → paid` (or `cancelled`
  before paid); the generate flow additionally uses `committed` / `rolled_back`.
- **Payout export**: one row per marketplace-payout line item (not per sale).
- **Push to ReachPro** is irreversible and always human-triggered.
</content>
</invoke>
