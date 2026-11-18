-- 계정 설정과 이름 바꾸기. Supabase 대시보드의 SQL Editor에서 한 번 실행한다. 다시 실행해도 된다.
--
-- 실행하고 나면:
--   user_settings  설정 화면의 언어·에디터·보기 설정을 계정에 저장한다. 다른 기기에서 로그인해도
--                  같은 설정이 적용된다. 본인 것만 읽고 쓴다.
--   profiles       본인 이름(username)만 바꿀 수 있다. 다른 칸(is_admin 등)은 여전히 못 고친다.
--                  이름은 앞뒤 공백 없이 1~20자, 제어 문자는 쓸 수 없다.
--
-- 실행하기 전에는 설정이 그 브라우저에만 저장되고, 이름 바꾸기는 "바꾸지 못했습니다"로 끝난다.
-- 전체가 한 트랜잭션이라 중간에 실패하면 아무것도 바뀌지 않는다. 마지막에 점검 결과가 나온다.

begin;

-- 1. 계정 설정
-- 값의 모양은 프론트엔드가 검사한다(모르는 항목은 무시하고 기본값을 쓴다). 여기서는 객체인지와
-- 크기만 막는다.
create table if not exists public.user_settings (
  user_id    uuid        primary key references auth.users (id) on delete cascade,
  settings   jsonb       not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  constraint user_settings_shape check (jsonb_typeof(settings) = 'object' and pg_column_size(settings) <= 4096)
);

alter table public.user_settings enable row level security;

drop policy if exists user_settings_own on public.user_settings;
create policy user_settings_own on public.user_settings
  for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

revoke all on public.user_settings from anon;
grant select, insert, update, delete on public.user_settings to authenticated;
grant all on public.user_settings to service_role;

-- 2. 이름 바꾸기
-- 표 단위 수정 권한을 거두고 username 칸만 다시 준다. 정책이 본인 행으로 좁히므로
-- 남의 이름이나 is_admin은 고칠 수 없다.
revoke update on public.profiles from anon, authenticated;
grant update (username) on public.profiles to authenticated;

drop policy if exists profiles_rename_own on public.profiles;
create policy profiles_rename_own on public.profiles
  for update to authenticated
  using (id = (select auth.uid())) with check (id = (select auth.uid()));

-- 이미 있는 이름은 검사하지 않는다(not valid). 새로 넣거나 바꾸는 이름부터 적용된다.
alter table public.profiles drop constraint if exists profiles_username_valid;
alter table public.profiles add constraint profiles_username_valid check (
  username is null
  or (username = btrim(username) and char_length(username) between 1 and 20 and username !~ '[[:cntrl:]]')
) not valid;

-- 3. 점검
select item, ok, detail
from (
  select 1 as n, 'user_settings RLS 켜짐' as item, c.relrowsecurity as ok,
         (select count(*) from pg_policies p where p.schemaname = 'public' and p.tablename = 'user_settings')::text || '개 정책' as detail
  from pg_class c
  where c.oid = 'public.user_settings'::regclass
  union all
  select 2, '로그인 사용자가 바꿀 수 있는 profiles 칸', array_agg(column_name::text order by column_name) = array['username'],
         array_to_string(array_agg(column_name::text order by column_name), ', ')
  from information_schema.column_privileges
  where table_schema = 'public' and table_name = 'profiles'
    and grantee = 'authenticated' and privilege_type = 'UPDATE'
  union all
  select 3, '규칙에 맞지 않는 기존 이름(바꾸기 전까지는 그대로 둔다)', true, count(*)::text
  from public.profiles
  where not (username is null
             or (username = btrim(username) and char_length(username) between 1 and 20 and username !~ '[[:cntrl:]]'))
) checks
order by n;

commit;
