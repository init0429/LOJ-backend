-- 제출 결과 설명. Supabase 대시보드의 SQL Editor에서 한 번 실행한다.
--
-- 채점기가 판정과 함께 적는다. 화면은 판정이 나온 뒤 이 칸을 읽어 "몇 번째 테스트에서
-- 틀렸는지"를 보여 준다. 이 칸이 없어도 채점기는 판정만 적고, 화면은 판정만 보여 준다.
--
-- 담기는 것: {"passed", "total", "failed_test"?, "example"?, "message"?}
--   example: 틀린 테스트가 공개 예제일 때만 {"input", "expected", "actual"}
--   message: 컴파일 오류 메시지
-- 숨겨진 테스트의 입력과 출력은 담지 않는다. 다만 컴파일 메시지에는 코드 일부가 보이므로
-- submissions.code처럼 본인만 읽게 막을 때 이 칸도 함께 막아야 한다.

alter table public.submissions add column if not exists feedback jsonb;
