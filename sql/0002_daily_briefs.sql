-- Daily curated briefs for the `daily_brief` paid tool. Standalone Supabase
-- project (weather-intel). Idempotent. One row per brief_date; expires at
-- the next midnight UTC. Curated daily at 05:00 UTC by daily_curator.

create table if not exists daily_briefs (
    id              uuid primary key default gen_random_uuid(),
    brief_date      date not null unique,
    brief_data      jsonb not null,
    signal_count    integer default 0,
    attestation_hash text,
    generated_at    timestamptz default now(),
    expires_at      timestamptz not null,
    purchase_count  integer default 0
);
create index if not exists idx_daily_briefs_date on daily_briefs (brief_date desc);

-- Atomic purchase counter used by daily_curator.bump_purchase().
create or replace function increment_brief_purchase(p_brief_date date)
returns void language sql as $$
    update daily_briefs set purchase_count = purchase_count + 1
    where brief_date = p_brief_date;
$$;
