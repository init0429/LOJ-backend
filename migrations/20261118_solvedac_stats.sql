-- solved.ac 연동 사용자의 실력 통계. Supabase 대시보드의 SQL Editor에서 한 번 실행한다.
--
-- 백엔드가 밤마다(그리고 연동 직후 한 번) 사용자의 토큰으로 받아 둔다. 추천은 요청할 때
-- solved.ac를 다시 부르지 않고 이 표를 읽는다. 백엔드(secret 키)만 읽고 쓴다.
-- 연동을 해제하면(solvedac_accounts 행 삭제) 함께 지워진다. 다시 실행해도 된다.

create table if not exists public.solvedac_stats (
  user_id      uuid        primary key references public.solvedac_accounts (user_id) on delete cascade,
  -- 받을 때의 handle. 추천에서 "이 사용자가 푼 BOJ 문제 빼기"에 쓴다.
  handle       text        not null,
  -- 0 Unrated, 1~30 Bronze V~Ruby I, 31 Master
  tier         smallint    check (tier between 0 and 31),
  rating       integer,
  solved_count integer,
  -- 태그별 [{"key", "solved", "tried"}]. tried는 시도했지만 풀지 못한 문제 수다.
  tag_stats    jsonb       not null default '[]'::jsonb,
  synced_at    timestamptz not null default now()
);

alter table public.solvedac_stats enable row level security;
revoke all on public.solvedac_stats from anon, authenticated;
grant all on public.solvedac_stats to service_role;
