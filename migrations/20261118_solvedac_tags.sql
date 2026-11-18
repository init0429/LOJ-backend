-- solved.ac 태그 체계와 문제 티어. Supabase 대시보드의 SQL Editor에서 한 번 실행한다.
-- 20261118_row_level_security.sql을 먼저 실행해야 한다(그 파일의 함수를 정책에서 쓴다).
--
-- tags         solved.ac 태그 목록. 백엔드가 채운다(seed_solvedac_tags.py 또는 관리자 화면 버튼).
-- problem_tags 문제와 태그의 다대다. 관리자가 문제 관리 화면에서 고른다.
-- problems     solvedac_tier(0 Unrated, 1~30 Bronze V~Ruby I)와 boj_problem_id를 더한다.
--
-- 다시 실행해도 된다.

begin;

create table if not exists public.tags (
  -- solved.ac 태그 key(예: dp, graphs). 문제와 이어 주는 열쇠다.
  key          text        primary key,
  solvedac_id  integer     unique,
  -- 바로 위 태그들. 부모가 둘 이상인 태그도 있다(예: 2sat → graph, scc).
  parent_keys  text[]      not null default '{}',
  name_ko      text        not null,
  name_en      text,
  -- 다른 이름(예: "투 셋"). 관리자 화면의 태그 찾기에 쓴다.
  aliases      text[]      not null default '{}',
  is_meta      boolean     not null default false,
  rated        boolean     not null default true,
  updated_at   timestamptz not null default now()
);

alter table public.problems
  add column if not exists solvedac_tier  smallint check (solvedac_tier between 0 and 30),
  add column if not exists boj_problem_id integer  check (boj_problem_id > 0);

create table if not exists public.problem_tags (
  problem_id bigint not null references public.problems (id) on delete cascade,
  tag_key    text   not null references public.tags (key) on update cascade,
  primary key (problem_id, tag_key)
);
create index if not exists problem_tags_tag_key_idx on public.problem_tags (tag_key);

-- 새 표가 API에 자동으로 열리지 않는 프로젝트도 있어서 권한을 직접 준다. 행 단위로 막는 것은 아래 RLS다.
grant select on public.tags to anon, authenticated;
grant select on public.problem_tags to anon;
grant select, insert, update, delete on public.problem_tags to authenticated;
grant all on public.tags, public.problem_tags to service_role;

alter table public.tags enable row level security;
alter table public.problem_tags enable row level security;

-- 태그 목록은 공개 정보다. 쓰는 것은 백엔드(secret 키)뿐이다.
drop policy if exists tags_read on public.tags;
create policy tags_read on public.tags
  for select to anon, authenticated
  using (true);

-- 문제의 태그는 문제와 같은 때에 공개한다(시작 전 대회의 문제는 관리자만).
drop policy if exists problem_tags_read on public.problem_tags;
create policy problem_tags_read on public.problem_tags
  for select to anon, authenticated
  using (public.problem_is_released(problem_id));
drop policy if exists problem_tags_admin on public.problem_tags;
create policy problem_tags_admin on public.problem_tags
  for all to authenticated
  using (public.current_user_is_admin()) with check (public.current_user_is_admin());

commit;
