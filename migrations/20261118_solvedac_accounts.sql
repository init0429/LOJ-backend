-- solved.ac 계정 연동. Supabase 대시보드의 SQL Editor에서 한 번 실행한다.
--
-- 토큰은 백엔드가 SOLVEDAC_TOKEN_KEY로 암호화해서 넣는다. 이 테이블은 백엔드(secret 키)만
-- 읽고 쓴다. 브라우저는 연동 상태를 백엔드 API(/integrations/solvedac)로만 본다.

create table if not exists public.solvedac_accounts (
  user_id            uuid        primary key references auth.users (id) on delete cascade,
  -- solved.ac 계정 id. 바뀌지 않으므로 "한 solved.ac 계정은 LOJ 계정 하나에만" 기준으로 쓴다.
  solvedac_user_id   bigint      not null unique,
  -- 표시용. 사용자가 solved.ac에서 바꿀 수 있어 연동하거나 동기화할 때 새로 적는다.
  handle             text        not null,
  access_token_enc   text        not null,
  refresh_token_enc  text,
  scopes             text        not null default '',
  expires_at         timestamptz not null,
  -- expired: 토큰을 갱신할 수 없다. 사용자가 다시 연결해야 한다.
  status             text        not null default 'active' check (status in ('active', 'expired')),
  linked_at          timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create index if not exists solvedac_accounts_handle_idx on public.solvedac_accounts (lower(handle));

-- 정책을 하나도 두지 않으므로 anon, authenticated 역할은 아무 행도 읽거나 쓸 수 없다.
-- (secret 키를 쓰는 백엔드는 RLS를 거치지 않는다.)
alter table public.solvedac_accounts enable row level security;
revoke all on table public.solvedac_accounts from anon, authenticated;
