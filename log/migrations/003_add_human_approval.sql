-- The durable record that a human released a held payment.
--
-- Without it, decide/approve.py only flips status back to 'diagnosed' and
-- the next decide pass re-applies the same confidence or value gate to the
-- same row -- needs_approval -> diagnosed -> needs_approval, forever. No
-- held payment could ever be recovered.
alter table failed_payments add column if not exists human_approved_at timestamptz;
