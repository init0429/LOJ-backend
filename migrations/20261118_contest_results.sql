-- 끝난 대회의 최종 순위. Supabase 대시보드의 SQL Editor에서 한 번 실행한다. 다시 실행해도 된다.
--
-- 제출은 RLS로 본인 것만 읽히므로, 대회 화면이 순위를 그릴 수 있게 끝난 대회의 제출 결과만
-- 따로 내준다. 코드와 채점 설명은 주지 않고 (사용자, 이름, 문제, 판정, 시각)만 준다.
-- 진행 중이거나 시작 전인 대회는 아무것도 돌려주지 않는다.

create or replace function public.contest_results(target bigint)
returns table (user_id uuid, username text, problem_id bigint, status text, created_at timestamptz)
language sql stable security definer set search_path = ''
as $$
  select s.user_id, p.username, s.problem_id::bigint, s.status, s.created_at
  from public.submissions s
  join public.contests c on c.id = s.contest_id
  left join public.profiles p on p.id = s.user_id
  where s.contest_id = target
    and c.end_time <= now()
$$;

revoke all on function public.contest_results(bigint) from public;
grant execute on function public.contest_results(bigint) to anon, authenticated, service_role;
