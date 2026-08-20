-- Run this in Supabase: Project -> SQL Editor -> New query -> Run

create table if not exists failed_payments (
    payment_id           text primary key,
    order_id             text not null,
    amount_inr           numeric not null,
    currency             text not null default 'INR',
    error_code           text not null,
    error_reason         text,
    root_cause           text not null,        -- ground truth (synthetic data only)
    predicted_root_cause text,                  -- what your classifier said
    diagnosis_confidence text,                  -- "high" | "low", drives the decide layer's review gate
    created_at           timestamptz not null,
    customer_email       text,
    customer_phone       text,
    attempt_count        int not null default 1,
    status               text not null default 'needs_diagnosis',
    action_taken         text,
    next_action_at       timestamptz,          -- when the act layer should execute action_taken
    human_approved_at    timestamptz,          -- set by decide/approve.py; stands the confidence and value gates down for this row (stopping rules still apply)
    recovered_amount_inr numeric,
    updated_at           timestamptz default now()
);

-- Separate table for the audit trail: every diagnosis, decision, and action
-- taken on a payment gets logged here as its own row. This is what "show
-- the audit trail" in the track's bar actually means -- an evaluator should
-- be able to pull every action taken on a single payment_id and see why.
create table if not exists audit_log (
    id          bigserial primary key,
    payment_id  text references failed_payments(payment_id),
    event       text not null,     -- e.g. 'ingested', 'diagnosed', 'retry_attempted', 'recovered', 'exhausted'
    detail      jsonb,
    created_at  timestamptz default now()
);

create index if not exists idx_audit_log_payment_id on audit_log(payment_id);

-- RLS on, no policies: only the secret/service_role key (used server-side
-- by this pipeline) can read or write. The publishable/anon key gets nothing.
alter table failed_payments enable row level security;
alter table audit_log enable row level security;
