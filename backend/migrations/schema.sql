-- Payment Reconciliation Database Schema

CREATE TABLE purchasers (
    purchaser_id    SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    type            VARCHAR(20) NOT NULL CHECK (type IN ('partner', 'broker', 'in_house')),
    default_commission_pct NUMERIC(5,4) NOT NULL,
    notes           TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Commission override rules. A rule needs at least one condition:
--   event_tag        - substring match against performer name + sale tags
--   purchased_before - sale's purchased_date < this (NULL purchase date =
--                      infinitely old, so it matches any purchased_before)
--   purchased_from   - sale's purchased_date >= this (NULL never matches)
-- Tag rules outrank date-only rules; within a class, first match wins.
CREATE TABLE commission_overrides (
    override_id     SERIAL PRIMARY KEY,
    purchaser_id    INTEGER NOT NULL REFERENCES purchasers(purchaser_id),
    event_tag       VARCHAR(100),
    purchased_before DATE,
    purchased_from  DATE,
    commission_pct  NUMERIC(5,4) NOT NULL,
    UNIQUE (purchaser_id, event_tag)
);

CREATE TABLE marketplaces (
    marketplace_id  SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL UNIQUE,
    payout_interval VARCHAR(50),
    tracks_payment_date BOOLEAN NOT NULL DEFAULT FALSE,
    payout_file_format  TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE events (
    event_id        SERIAL PRIMARY KEY,
    performer       VARCHAR(255) NOT NULL,
    venue           VARCHAR(255),
    event_date      DATE NOT NULL,
    event_time      TIME,
    event_tag       VARCHAR(100),
    reachpro_event_id VARCHAR(100)
);

CREATE TABLE sales (
    sale_id         SERIAL PRIMARY KEY,
    reachpro_sale_id VARCHAR(100) UNIQUE,
    -- ReachPro invoice tag names (joined with '; ') - commission overrides
    -- match against these in addition to the event performer name
    tags            TEXT,
    -- Most recent purchaseDate across the sale's tickets' POs
    purchased_date  DATE,
    -- ReachPro's cancellationDate: the reliable "this sale is dead" signal.
    -- Statuses alone lie - e.g. 'Rejected' sales with a cancellation date
    -- never fulfilled, but don't say 'cancelled' anywhere
    cancellation_date DATE,
    -- Cancelled-sale offset hunt list resolution: 'ignored' (with a note
    -- saying why the unoffset payout is fine) drops the sale off the list.
    -- The other resolution path - an in-app manual offset adjustment -
    -- needs no marker here, it zeroes the payout sum instead.
    offset_resolution VARCHAR(20) CHECK (offset_resolution IN ('ignored')),
    offset_resolution_note TEXT,
    offset_resolved_at TIMESTAMPTZ,
    marketplace_id  INTEGER REFERENCES marketplaces(marketplace_id),
    event_id        INTEGER REFERENCES events(event_id),
    fulfillment_status VARCHAR(50),
    cost            NUMERIC(12,2),
    sale_price      NUMERIC(12,2),
    proceeds        NUMERIC(12,2),
    pnl             NUMERIC(12,2),
    commission_status VARCHAR(20) NOT NULL DEFAULT 'unpaid'
                    CHECK (commission_status IN ('unpaid', 'pending', 'paid')),
    marketplace_order_id VARCHAR(200),
    sale_date       TIMESTAMPTZ,
    quantity        INTEGER,
    reachpro_payment_id VARCHAR(100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE sale_purchasers (
    sale_id         INTEGER NOT NULL REFERENCES sales(sale_id),
    purchaser_id    INTEGER NOT NULL REFERENCES purchasers(purchaser_id),
    split_pct       NUMERIC(5,4) NOT NULL DEFAULT 1.0,
    PRIMARY KEY (sale_id, purchaser_id)
);

CREATE TABLE payout_import_files (
    import_file_id  SERIAL PRIMARY KEY,
    marketplace_id  INTEGER NOT NULL REFERENCES marketplaces(marketplace_id),
    filename        VARCHAR(500) NOT NULL,
    file_hash       VARCHAR(64) NOT NULL UNIQUE,
    row_count       INTEGER,
    s3_url          VARCHAR(1000),
    payment_date    DATE,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    imported_by     VARCHAR(255),
    notes           TEXT
);

CREATE TABLE marketplace_payouts (
    payout_id       SERIAL PRIMARY KEY,
    marketplace_id  INTEGER NOT NULL REFERENCES marketplaces(marketplace_id),
    sale_id         INTEGER REFERENCES sales(sale_id),
    marketplace_order_id VARCHAR(200),
    amount          NUMERIC(12,2) NOT NULL,
    payment_date    DATE,
    import_file_id  INTEGER REFERENCES payout_import_files(import_file_id),
    payout_key      VARCHAR(500) NOT NULL,
    commission_included_in_payout_id INTEGER,
    reachpro_push_status VARCHAR(20) DEFAULT 'pending'
                    CHECK (reachpro_push_status IN ('pending', 'pushed', 'rejected')),
    reachpro_push_error TEXT,
    payment_type    VARCHAR(50),
    is_parking      BOOLEAN NOT NULL DEFAULT FALSE,
    notes           TEXT,
    raw_data        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    adjustment_review_notes TEXT,
    adjustment_commission_treatment VARCHAR(30)
                    CHECK (adjustment_commission_treatment IN ('full_amount', 'full_amount_minus_cost', 'none')),
    adjustment_reason VARCHAR(30)
                    CHECK (adjustment_reason IN ('missed_ihd', 'doe', 'cancelled_event', 'rejected_replacements', 'other')),
    match_dismissed_at TIMESTAMPTZ,
    match_dismissed_by VARCHAR(255),
    match_dismissal_note TEXT,
    UNIQUE (payout_key)
);

CREATE TABLE purchaser_payouts (
    purchaser_payout_id SERIAL PRIMARY KEY,
    purchaser_id    INTEGER NOT NULL REFERENCES purchasers(purchaser_id),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending_review'
                    CHECK (status IN ('pending_review', 'approved', 'paid', 'cancelled')),
    payout_date     DATE,
    gross_pnl       NUMERIC(12,2),
    total_waste_deductions NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_other_deductions NUMERIC(12,2) NOT NULL DEFAULT 0,
    -- Expense dollars reduce P&L; the commission impact (expenses x the
    -- purchaser's default commission pct) is what nets against commission
    expense_commission_impact NUMERIC(12,2) NOT NULL DEFAULT 0,
    net_pnl         NUMERIC(12,2),
    commission_pct  NUMERIC(5,4),
    commission_amount NUMERIC(12,2),
    amount_paid     NUMERIC(12,2),
    payment_method  VARCHAR(100),
    export_file     VARCHAR(500),
    reviewed_by     VARCHAR(255),
    reviewed_at     TIMESTAMPTZ,
    paid_by         VARCHAR(255),
    paid_at         TIMESTAMPTZ,
    cancelled_by    VARCHAR(255),
    cancelled_at    TIMESTAMPTZ,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE marketplace_payouts
    ADD CONSTRAINT fk_clawback_payout
    FOREIGN KEY (commission_included_in_payout_id)
    REFERENCES purchaser_payouts(purchaser_payout_id);

-- Manual partner expenses. Claimed into a commission batch at generation
-- (included_in_payout_id set), released on rollback, frozen while the batch
-- lives - the same lifecycle as commission lines. Editable only while
-- unclaimed.
CREATE TABLE purchaser_expenses (
    expense_id      SERIAL PRIMARY KEY,
    purchaser_id    INTEGER NOT NULL REFERENCES purchasers(purchaser_id),
    expense_date    DATE NOT NULL,
    description     TEXT NOT NULL,
    amount          NUMERIC(12,2) NOT NULL,
    included_in_payout_id INTEGER REFERENCES purchaser_payouts(purchaser_payout_id),
    created_by      VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE purchaser_payout_lines (
    id              SERIAL PRIMARY KEY,
    purchaser_payout_id INTEGER NOT NULL REFERENCES purchaser_payouts(purchaser_payout_id),
    marketplace_payout_id INTEGER NOT NULL REFERENCES marketplace_payouts(payout_id),
    sale_id         INTEGER REFERENCES sales(sale_id),
    payout_amount   NUMERIC(12,2) NOT NULL,
    commission_pct_applied NUMERIC(5,4) NOT NULL,
    commission_amount NUMERIC(12,2) NOT NULL,
    UNIQUE (purchaser_payout_id, marketplace_payout_id)
);

CREATE TABLE payout_deductions (
    deduction_id    SERIAL PRIMARY KEY,
    purchaser_payout_id INTEGER NOT NULL REFERENCES purchaser_payouts(purchaser_payout_id),
    type            VARCHAR(50) NOT NULL
                    CHECK (type IN ('wasted_inventory', 'broken_order_fee', 'other')),
    amount          NUMERIC(12,2) NOT NULL,
    reference_id    INTEGER,
    description     TEXT
);

CREATE TABLE wasted_inventory (
    waste_id        SERIAL PRIMARY KEY,
    reachpro_record_id VARCHAR(100),
    purchaser_id    INTEGER NOT NULL REFERENCES purchasers(purchaser_id),
    event_id        INTEGER REFERENCES events(event_id),
    cost            NUMERIC(12,2) NOT NULL,
    quantity        INTEGER,
    waste_date      DATE,
    notes           TEXT,
    payout_deducted BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE sync_runs (
    run_id          SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    total_payouts   INT,
    processed       INT,
    sales_created   INT,
    already_existed INT,
    no_invoice      INT,
    no_buyer_id     INT,
    unmapped_purchaser INT,
    purchasers_hit  JSONB,
    error           TEXT,
    -- 'full' (untargeted backlog sync incl. offline/wastage/tags sweeps) or
    -- 'batch' (auto-sync scoped to just-imported files). The row is inserted
    -- at start (finished_at NULL) so a killed run is visible as a failure.
    scope           VARCHAR(10)
);

-- One row per inventory refresh attempt (scheduled nightly + manual), so
-- "when did this last succeed" and "why did last night's run fail" are both
-- answerable. The Excel export stamps the last successful finished_at on the
-- Remaining Inventory tab.
CREATE TABLE inventory_refresh_runs (
    run_id          SERIAL PRIMARY KEY,
    trigger         VARCHAR(20) NOT NULL DEFAULT 'manual'
                    CHECK (trigger IN ('manual', 'scheduled')),
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    listings_fetched INTEGER,
    inventory_rows  INTEGER,
    error           TEXT
);

CREATE TABLE remaining_inventory (
    id              SERIAL PRIMARY KEY,
    buyer_user_id   UUID NOT NULL,
    event_name      VARCHAR(255) NOT NULL,
    qty             INTEGER NOT NULL,
    cost            NUMERIC(12,2) NOT NULL,
    -- Most recent PO purchaseDate across the group's listings
    last_purchase_date DATE,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Cache of purchase-order purchase dates (PO ids are stable, dates immutable
-- in practice) so sale/inventory syncs don't refetch the same POs forever
CREATE TABLE po_purchase_dates (
    po_id           BIGINT PRIMARY KEY,
    purchase_date   DATE
);

CREATE TABLE import_skipped_rows (
    skipped_row_id  SERIAL PRIMARY KEY,
    import_file_id  INTEGER REFERENCES payout_import_files(import_file_id),
    filename        VARCHAR(500) NOT NULL,
    marketplace_id  INTEGER REFERENCES marketplaces(marketplace_id),
    reason          TEXT NOT NULL,
    raw_row         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Every time an import row's payout_key (marketplace + order ID + amount)
-- collides with a row already in marketplace_payouts, the incoming row is
-- dropped (ON CONFLICT DO NOTHING) but recorded here for a human to confirm
-- it really was the same transaction and not two distinct ones that happened
-- to share a key (no automatic tiebreaker - by design, a human decides).
CREATE TABLE import_duplicate_reviews (
    duplicate_review_id SERIAL PRIMARY KEY,
    import_file_id  INTEGER REFERENCES payout_import_files(import_file_id),
    filename        VARCHAR(500) NOT NULL,
    marketplace_id  INTEGER REFERENCES marketplaces(marketplace_id),
    marketplace_order_id VARCHAR(200),
    amount          NUMERIC(12,2),
    payout_key      VARCHAR(500) NOT NULL,
    raw_row         JSONB,
    reviewed        BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_by     VARCHAR(255),
    reviewed_at     TIMESTAMPTZ,
    review_notes    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE users (
    user_id         SERIAL PRIMARY KEY,
    email           VARCHAR(255) NOT NULL UNIQUE,
    name            VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'admin')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed marketplaces
INSERT INTO marketplaces (name, payout_interval, tracks_payment_date) VALUES
    ('Viagogo',       'weekly',    TRUE),
    ('Lysted',        'per-order', FALSE),
    ('SeatGeek',      'weekly',    TRUE),
    ('TickPick',      'weekly',    FALSE),
    ('Gametime',      'weekly',    TRUE),
    ('TicketNetwork', 'weekly',    TRUE),
    ('GoTickets',     'per-order', FALSE),
    ('B2B',           'per-order', FALSE),
    ('Tevo',          'per-order', FALSE);

-- CrowdVold Sales Import: stages rows from the monthly "CV SALES" workbook,
-- maps CrowdVold events to ReachPro events, and tracks which rows have been
-- pushed to ReachPro as Offline sales. order_number is CrowdVold's own id and
-- doubles as the ReachPro marketplaceSaleId, making creation idempotent.
CREATE TABLE cv_sales (
    cv_sale_id      SERIAL PRIMARY KEY,
    order_number    VARCHAR(50) NOT NULL UNIQUE,
    source_tab      VARCHAR(100),
    transaction_date TIMESTAMPTZ,
    event_name      TEXT NOT NULL,
    event_date      TIMESTAMPTZ,
    quantity        INTEGER NOT NULL,
    price           NUMERIC(12,2),
    total_amount    NUMERIC(12,2),
    seller_fees     NUMERIC(12,2),
    earnings        NUMERIC(12,2) NOT NULL,   -- net of CrowdVold fees; becomes totalNetProceeds
    status          VARCHAR(30) NOT NULL DEFAULT 'staged'
        CHECK (status IN ('staged','cancelled','excluded','created','create_failed')),
    status_note     TEXT,
    reachpro_invoice_id VARCHAR(50),
    fulfilled       BOOLEAN NOT NULL DEFAULT FALSE,
    inventory_id    BIGINT,                   -- listing the sale allocated from
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pushed_at       TIMESTAMPTZ
);

CREATE TABLE cv_event_mappings (
    mapping_id      SERIAL PRIMARY KEY,
    cv_event_name   TEXT NOT NULL,
    cv_event_date   DATE NOT NULL,
    reachpro_event_id BIGINT,
    reachpro_event_name TEXT,
    reachpro_event_date DATE,
    reachpro_venue  TEXT,
    skip            BOOLEAN NOT NULL DEFAULT FALSE,  -- user chose to exclude this event entirely
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (cv_event_name, cv_event_date)
);
