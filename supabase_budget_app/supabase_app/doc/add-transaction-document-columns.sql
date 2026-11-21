-- Supabase SQL Editor에서 한 번 실행하세요.
-- 거래당 추가 서류와 OCR 품목 목록을 저장하는 칸을 추가합니다.
alter table transactions
    add column if not exists additional_document_path text,
    add column if not exists additional_document_filename text,
    add column if not exists line_items jsonb not null default '[]'::jsonb;
