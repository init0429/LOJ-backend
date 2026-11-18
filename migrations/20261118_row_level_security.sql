-- 행 수준 보안(RLS) 정리. Supabase 대시보드의 SQL Editor에서 한 번 실행한다.
--
-- 지금은 브라우저에 실린 공개 키만으로 숨겨진 테스트케이스와 남의 제출 코드가 읽힌다.
-- 실행하고 나면:
--   test_cases        공개 예제만 누구나 읽는다. 숨겨진 테스트는 관리자만.
--   submissions       본인 것만 읽는다(관리자는 전부). 브라우저에서는 아무도 쓰지 못한다.
--   profiles          본인 것만 읽는다(관리자는 전부). 브라우저에서는 고치지 못한다.
--                     (is_admin을 스스로 켜는 것을 막는다.)
--   problems, contests, contest_problems, test_cases
--                     관리자만 쓰고 지운다.
--   시작 전 대회의 문제  관리자 말고는 문제, 예제, 대회 문제 목록을 읽지 못한다.
--   contest_drafts    본인 것만 읽고 쓴다.
--
-- 백엔드와 채점기는 secret 키라 RLS를 거치지 않으므로 영향이 없다. 문제 목록의 정답률은
-- 모든 제출을 읽는 대신 problem_stats() 함수로 받는다(프론트엔드도 함께 바꿨다).
--
-- 위 7개 표의 기존 정책은 모두 지우고 새로 만든다. 지우기 전 정책은 API로 보이지 않는
-- loj_backup.policies 표에 남긴다. 전체가 한 트랜잭션이라 중간에 실패하면 아무것도 바뀌지
-- 않는다. 마지막에 점검 결과가 표로 나온다. ok가 false인 줄이 있으면 확인할 것.

begin;

-- 0. 회원가입 트리거 확인
-- 새 사용자의 profiles 행을 만드는 auth.users 트리거가 security definer가 아니면, RLS를 켠 뒤
-- 가입이 "Database error saving new user"로 막힌다. 그런 경우 아무것도 바꾸지 않고 멈춘다.
do $$
declare
  risky text;
begin
  select string_agg(p.oid::regprocedure::text, ', ') into risky
  from pg_trigger t
  join pg_proc p on p.oid = t.tgfoid
  where t.tgrelid = 'auth.users'::regclass
    and not t.tgisinternal
    and not p.prosecdef
    and p.prosrc ilike '%profiles%';
  if risky is not null then
    raise exception '회원가입 트리거 함수 %이(가) security definer가 아닙니다. RLS를 켜면 가입이 막히므로 멈춥니다. 그 함수를 security definer로 바꾼 뒤 다시 실행하세요.', risky;
  end if;
end $$;

-- 1. 기존 정책을 남겨 두고 지운다
create schema if not exists loj_backup;
revoke all on schema loj_backup from public, anon, authenticated;
create table if not exists loj_backup.policies as
  select now() as saved_at, * from pg_policies where false;
insert into loj_backup.policies
  select now(), * from pg_policies
  where schemaname = 'public'
    and tablename in ('problems', 'test_cases', 'contests', 'contest_problems',
                      'submissions', 'profiles', 'contest_drafts');

do $$
declare
  p record;
begin
  for p in
    select schemaname, tablename, policyname from pg_policies
    where schemaname = 'public'
      and tablename in ('problems', 'test_cases', 'contests', 'contest_problems',
                        'submissions', 'profiles', 'contest_drafts')
  loop
    execute format('drop policy %I on %I.%I', p.policyname, p.schemaname, p.tablename);
  end loop;
end $$;

-- 2. 정책에서 쓰는 함수
-- 정책 안에서 profiles나 contest_problems를 그냥 조회하면 그 표의 RLS가 다시 걸려(관리자 확인은
-- 무한 재귀, 공개 여부는 잘못된 판단) security definer 함수로 RLS 밖에서 읽는다.

create or replace function public.current_user_is_admin()
returns boolean
language sql stable security definer set search_path = ''
as $$
  select coalesce(
    (select p.is_admin from public.profiles p where p.id = (select auth.uid())),
    false
  )
$$;

-- 아직 시작하지 않은 대회에 들어 있는 문제는 공개되지 않은 것으로 본다.
create or replace function public.problem_is_released(target bigint)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select not exists (
    select 1
    from public.contest_problems cp
    join public.contests c on c.id = cp.contest_id
    where cp.problem_id = target
      and c.start_time > now()
  )
$$;

-- 문제 목록의 정답률. 제출 행 대신 문제별 개수만 내준다.
create or replace function public.problem_stats()
returns table (problem_id bigint, submissions bigint, accepted bigint)
language sql stable security definer set search_path = ''
as $$
  select t.problem_id, t.submissions, t.accepted
  from (
    select s.problem_id::bigint as problem_id,
           count(*) as submissions,
           count(*) filter (where s.status = 'Accepted') as accepted
    from public.submissions s
    group by s.problem_id
  ) t
  where public.problem_is_released(t.problem_id)
$$;

revoke all on function public.current_user_is_admin() from public;
revoke all on function public.problem_is_released(bigint) from public;
revoke all on function public.problem_stats() from public;
grant execute on function public.current_user_is_admin() to anon, authenticated, service_role;
grant execute on function public.problem_is_released(bigint) to anon, authenticated, service_role;
grant execute on function public.problem_stats() to anon, authenticated, service_role;

-- 3. RLS 켜기
alter table public.problems         enable row level security;
alter table public.test_cases       enable row level security;
alter table public.contests         enable row level security;
alter table public.contest_problems enable row level security;
alter table public.submissions      enable row level security;
alter table public.profiles         enable row level security;
alter table public.contest_drafts   enable row level security;

-- 4. 정책
-- 관리자 정책(for all)은 읽기도 포함한다. 같은 동작의 정책이 여럿이면 하나만 통과해도 된다.

create policy problems_read on public.problems
  for select to anon, authenticated
  using (public.problem_is_released(id));
create policy problems_admin on public.problems
  for all to authenticated
  using (public.current_user_is_admin()) with check (public.current_user_is_admin());

create policy test_cases_read_examples on public.test_cases
  for select to anon, authenticated
  using (is_hidden = false and public.problem_is_released(problem_id));
create policy test_cases_admin on public.test_cases
  for all to authenticated
  using (public.current_user_is_admin()) with check (public.current_user_is_admin());

create policy contests_read on public.contests
  for select to anon, authenticated
  using (true);
create policy contests_admin on public.contests
  for all to authenticated
  using (public.current_user_is_admin()) with check (public.current_user_is_admin());

create policy contest_problems_read_started on public.contest_problems
  for select to anon, authenticated
  using (exists (
    select 1 from public.contests c
    where c.id = contest_problems.contest_id and c.start_time <= now()
  ));
create policy contest_problems_admin on public.contest_problems
  for all to authenticated
  using (public.current_user_is_admin()) with check (public.current_user_is_admin());

-- 제출은 백엔드만 넣고 채점기만 고친다. 브라우저용 쓰기 정책은 두지 않는다.
create policy submissions_read_own on public.submissions
  for select to authenticated
  using (user_id = (select auth.uid()) or public.current_user_is_admin());

-- 프로필은 가입 트리거가 만든다. 브라우저용 쓰기 정책은 두지 않는다.
create policy profiles_read_own on public.profiles
  for select to authenticated
  using (id = (select auth.uid()) or public.current_user_is_admin());

create policy contest_drafts_own on public.contest_drafts
  for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

-- 5. 점검
-- leaderboard가 security_invoker 뷰면 이제 본인 제출만 보고 순위를 계산하게 된다.
select item, ok, detail
from (
  select 1 as n, 'RLS 켜짐: ' || c.relname as item, c.relrowsecurity as ok,
         (select count(*) from pg_policies p
          where p.schemaname = 'public' and p.tablename = c.relname)::text || '개 정책' as detail
  from pg_class c
  where c.relnamespace = 'public'::regnamespace
    and c.relname in ('problems', 'test_cases', 'contests', 'contest_problems',
                      'submissions', 'profiles', 'contest_drafts')
  union all
  select 2, 'leaderboard가 모든 제출로 계산됨',
         not coalesce(c.reloptions && array['security_invoker=true', 'security_invoker=on', 'security_invoker=1'], false),
         case c.relkind when 'v' then '뷰' when 'm' then '구체화된 뷰' when 'r' then '표' else c.relkind::text end
         || coalesce(' ' || array_to_string(c.reloptions, ','), '')
  from pg_class c
  where c.relnamespace = 'public'::regnamespace and c.relname = 'leaderboard'
  union all
  select 3, '백업한 기존 정책 수', true, count(*)::text
  from loj_backup.policies
  where saved_at = now()
) checks
order by n, item;

commit;
