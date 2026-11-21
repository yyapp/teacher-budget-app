-- Supabase SQL 에디터에서 실행하세요.
-- 테이블 생성 + 서비스 역할 키에 대한 RLS 정책( bypass )을 포함합니다.

-- ==========
-- budgets 테이블
-- ==========
create table if not exists budgets (
    id             uuid primary key default gen_random_uuid(),
    name           text not null,
    amount         numeric not null default 0,
    category       text,
    created_at     timestamptz default now(),
    updated_at     timestamptz default now()
);

alter table budgets enable row level security;

-- 서비스 역할 키는 모든 작업을 허용 (서버 전용)
create policy "service role full access on budgets"
    on budgets for all
    using (auth.jwt() ->> 'app_role' = 'service_role')
    with check (auth.jwt() ->> 'app_role' = 'service_role');

-- ==========
-- transactions 테이블
-- ==========
create table if not exists transactions (
    id                 uuid primary key default gen_random_uuid(),
    budget_id          uuid not null references budgets(id),
    description        text not null,
    amount             numeric not null default 0,
    category           text,
    quantity           integer not null default 1,
    unit_price         numeric,
    receipt_path       text,
    receipt_filename   text,
    additional_document_path     text,
    additional_document_filename text,
    line_items         jsonb not null default '[]'::jsonb,
    notes              text,
    status             text not null default 'draft' check (status in ('draft','confirmed','cancelled','refunded')),
    refund_amount      numeric,
    refund_reason      text,
    created_at         timestamptz default now(),
    updated_at         timestamptz default now()
);

alter table transactions
    add column if not exists additional_document_path text,
    add column if not exists additional_document_filename text,
    add column if not exists line_items jsonb not null default '[]'::jsonb;

alter table transactions enable row level security;

create policy "service role full access on transactions"
    on transactions for all
    using (auth.jwt() ->> 'app_role' = 'service_role')
    with check (auth.jwt() ->> 'app_role' = 'service_role');

-- ==========
-- cart_proposals 테이블
-- ==========
create table if not exists cart_proposals (
    id         uuid primary key default gen_random_uuid(),
    items      jsonb not null default '[]',
    notes      text,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

alter table cart_proposals enable row level security;

create policy "service role full access on cart_proposals"
    on cart_proposals for all
    using (auth.jwt() ->> 'app_role' = 'service_role')
    with check (auth.jwt() ->> 'app_role' = 'service_role');

-- ==========
-- Storage 버킷 생성 (Supabase 콘솔에서 생성 권장, SQL로도 가능)
-- ==========
-- insert into storage.buckets (id, name, public) values ('receipts', 'receipts', false);
-- create policy "service role can upload receipts"
--     on storage.objects for insert
--     with check (bucket_id = 'receipts' and auth.jwt() ->> 'app_role' = 'service_role');
-- create policy "service role can select receipts"
--     on storage.objects for select
--     using (bucket_id = 'receipts' and auth.jwt() ->> 'app_role' = 'service_role');
-- create policy "service role can delete receipts"
--     on storage.objects for delete
--     using (bucket_id = 'receipts' and auth.jwt() ->> 'app_role' = 'service_role');
