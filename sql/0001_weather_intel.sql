-- Weather & Climate Intelligence — schema for weather_aggregator + weather-intel-mcp.
-- Standalone Supabase project. Idempotent. Weather is mostly live-proxied from
-- free APIs (Open-Meteo, NWS) with a TTL cache; alerts get an hourly snapshot.

-- ── generic TTL cache (forecast/historical/normals/ag/current/travel) ────────
create table if not exists weather_cache (
  cache_key  text primary key,        -- sha1(tool + rounded params)
  tool       text,
  lat        numeric,
  lon        numeric,
  payload    jsonb,
  expires_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists idx_weather_cache_expires on weather_cache (expires_at);

-- ── active alerts snapshot (refreshed hourly by the aggregator) ──────────────
create table if not exists weather_alerts (
  alert_id   text primary key,
  event      text,
  severity   text,
  urgency    text,
  certainty  text,
  headline   text,
  area_desc  text,
  states     text,                    -- comma-joined affected state codes
  onset      timestamptz,
  expires    timestamptz,
  sender     text,
  payload    jsonb,
  updated_at timestamptz not null default now()
);
create index if not exists idx_alerts_states on weather_alerts (states);
create index if not exists idx_alerts_severity on weather_alerts (severity);
create index if not exists idx_alerts_expires on weather_alerts (expires);

-- ── free-tier counter + payments ─────────────────────────────────────────────
create table if not exists weather_query_usage (
  agent_key text not null, day date not null,
  count integer not null default 0, updated_at timestamptz not null default now(),
  primary key (agent_key, day)
);
create or replace function weather_claim_free_query(p_agent_key text, p_day date, p_cap integer)
returns jsonb language plpgsql as $$
declare cur integer; ok boolean;
begin
  insert into weather_query_usage (agent_key, day, count, updated_at)
  values (p_agent_key, p_day, 0, now())
  on conflict (agent_key, day) do nothing;
  select count into cur from weather_query_usage
    where agent_key = p_agent_key and day = p_day for update;
  if cur < p_cap then
    update weather_query_usage set count = count + 1, updated_at = now()
      where agent_key = p_agent_key and day = p_day;
    ok := true; cur := cur + 1;
  else ok := false; end if;
  return jsonb_build_object('allowed', ok, 'count', cur, 'cap', p_cap);
end; $$;

create table if not exists weather_payments (
  tx_signature text primary key, intent text, agent_key text, tool text,
  amount_usdc numeric, payer_wallet text, recipient text, status text,
  block_time bigint, created_at timestamptz not null default now()
);
