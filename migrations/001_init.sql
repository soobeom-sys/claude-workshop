-- claude-workshop: 키움증권 AI 자동매매 시스템 초기 스키마
-- Supabase 프로젝트: kiwoom-db

create extension if not exists pgcrypto;

-- 1. hts_conditions: HTS 조건식 마스터
create table if not exists hts_conditions (
    id serial primary key,
    name text unique not null,
    style text,
    target_pct numeric,
    stop_pct numeric,
    max_hold_min integer,
    created_at timestamptz default now()
);

-- 2. signals: HTS 조건식 신호
create table if not exists signals (
    id uuid primary key default gen_random_uuid(),
    stock_code varchar not null,
    stock_name varchar,
    condition_id integer references hts_conditions(id),
    condition_name varchar not null,
    signal_price integer,
    signal_change_pct numeric,
    volume bigint,
    signal_time timestamptz not null,
    ai_action varchar,
    ai_summary text,
    ai_model varchar,
    ai_latency_ms integer,
    created_at timestamptz default now()
);

create index if not exists idx_signals_stock_code on signals(stock_code);
create index if not exists idx_signals_condition_id on signals(condition_id);
create index if not exists idx_signals_signal_time on signals(signal_time);

-- 3. trades: 체결된 매수 주문
create table if not exists trades (
    id uuid primary key default gen_random_uuid(),
    signal_id uuid references signals(id),
    stock_code varchar not null,
    stock_name varchar,
    entry_price integer not null,
    quantity integer not null,
    entry_time timestamptz not null,
    condition_id integer references hts_conditions(id),
    strategy_json jsonb,
    exchange varchar default 'KRX',
    exit_price integer,
    exit_time timestamptz,
    exit_reason varchar,
    pnl_amount integer,
    pnl_pct numeric,
    created_at timestamptz default now()
);

create index if not exists idx_trades_stock_code on trades(stock_code);
create index if not exists idx_trades_condition_id on trades(condition_id);
create index if not exists idx_trades_signal_id on trades(signal_id);
create index if not exists idx_trades_exit_time on trades(exit_time);

-- 4. sell_orders: 매도 주문 (분할매도 대응, trade당 여러 개)
create table if not exists sell_orders (
    id uuid primary key default gen_random_uuid(),
    trade_id uuid references trades(id),
    order_no varchar not null,
    quantity integer not null,
    target_price integer not null,
    created_at timestamptz default now()
);

create index if not exists idx_sell_orders_trade_id on sell_orders(trade_id);

-- 5. daily_summaries: 일별 요약
create table if not exists daily_summaries (
    id uuid primary key default gen_random_uuid(),
    date date unique not null,
    total_signals integer default 0,
    total_trades integer default 0,
    win_count integer default 0,
    loss_count integer default 0,
    total_pnl integer default 0,
    created_at timestamptz default now()
);

-- 6. kiwoom_token: 키움 OAuth 토큰 공유 캐시 (백엔드·MCP·웹이 함께 사용)
create table if not exists kiwoom_token (
    env text primary key,        -- 'mock' 또는 'real'
    access_token text not null,
    expires_at numeric not null, -- 만료 시각 (unix epoch, 초)
    updated_at timestamptz default now()
);

alter table kiwoom_token disable row level security;

grant select, insert, update on kiwoom_token to service_role;
grant select, insert, update on kiwoom_token to postgres;

-- 7. 뷰

-- v_open_positions: 현재 보유 포지션 (exit_time이 NULL인 trades)
create or replace view v_open_positions as
select *
from trades
where exit_time is null;

-- mv_condition_performance: 조건식별 매매 성과
create materialized view if not exists mv_condition_performance as
select
    hc.id as condition_id,
    hc.name as condition_name,
    count(t.id) filter (where t.exit_time is not null) as total_trades,
    count(t.id) filter (where t.pnl_amount > 0) as win_count,
    count(t.id) filter (where t.pnl_amount < 0) as loss_count,
    coalesce(sum(t.pnl_amount), 0) as total_pnl,
    coalesce(avg(t.pnl_pct), 0) as avg_pnl_pct,
    case
        when count(t.id) filter (where t.exit_time is not null) > 0
        then round(
            count(t.id) filter (where t.pnl_amount > 0)::numeric
            / count(t.id) filter (where t.exit_time is not null) * 100,
            2
        )
        else null
    end as win_rate_pct
from hts_conditions hc
left join trades t on t.condition_id = hc.id
group by hc.id, hc.name;

create unique index if not exists idx_mv_condition_performance_condition_id
    on mv_condition_performance(condition_id);
