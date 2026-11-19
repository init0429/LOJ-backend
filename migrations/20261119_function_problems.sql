-- 함수형 문제(함수만 써서 내는 방식). Supabase 대시보드의 SQL Editor에서 한 번 실행한다. 다시 실행해도 된다.
--
-- 실행하고 나면:
--   problems.signature      관리자가 적은 함수 시그니처. null이면 지금처럼 전체 프로그램만 받는다.
--                           {"function", "params": [{"name", "type"}], "returns", "output_param"}
--                           모양은 백엔드(signature 패키지)가 검사한다. 여기서는 객체인지와 크기만 막는다.
--   submissions.mode        제출 방식. program(전체 프로그램) 또는 function(함수 구현).
--                           function이어도 code에는 사용자가 쓴 코드가 그대로 남는다.
--   contest_drafts.mode     대회 화면에 임시 저장한 코드의 방식
--
-- 백엔드보다 먼저 실행한다. 실행하기 전에도 일반 제출은 그대로 되지만, 함수 구현 제출과
-- 시작 코드 API는 이 칸이 있어야 동작한다. 채점기는 바뀌지 않는다.
-- 기존 행은 모두 program이 된다. 표를 다시 쓰지 않으므로(기본값만 붙인다) 금방 끝난다.
-- 전체가 한 트랜잭션이라 중간에 실패하면 아무것도 바뀌지 않는다. 마지막에 점검 결과가 나온다.

begin;

-- 1. 문제의 함수 시그니처
alter table public.problems add column if not exists signature jsonb;
alter table public.problems drop constraint if exists problems_signature_valid;
alter table public.problems add constraint problems_signature_valid check (
  signature is null
  or (jsonb_typeof(signature) = 'object' and octet_length(signature::text) <= 4096)
);

-- 2. 제출 방식
alter table public.submissions add column if not exists mode text not null default 'program';
alter table public.submissions drop constraint if exists submissions_mode_valid;
alter table public.submissions add constraint submissions_mode_valid check (mode in ('program', 'function'));

-- 3. 대회 임시 저장의 방식
alter table public.contest_drafts add column if not exists mode text not null default 'program';
alter table public.contest_drafts drop constraint if exists contest_drafts_mode_valid;
alter table public.contest_drafts add constraint contest_drafts_mode_valid check (mode in ('program', 'function'));

-- 4. 점검
select item, ok, detail
from (
  select 1 as n, '칸 추가: ' || c.table_name || '.' || c.column_name as item, true as ok,
         c.data_type || coalesce(' default ' || c.column_default, '') as detail
  from information_schema.columns c
  where c.table_schema = 'public'
    and (c.table_name, c.column_name) in (('problems', 'signature'), ('submissions', 'mode'), ('contest_drafts', 'mode'))
  union all
  select 2, '추가된 칸 수', count(*) = 3, count(*)::text || '/3'
  from information_schema.columns c
  where c.table_schema = 'public'
    and (c.table_name, c.column_name) in (('problems', 'signature'), ('submissions', 'mode'), ('contest_drafts', 'mode'))
  union all
  select 3, '함수 구현을 받는 문제', true, count(*)::text || '개'
  from public.problems
  where signature is not null
) checks
order by n, item;

commit;
