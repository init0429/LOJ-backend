"""
함수형 문제의 API. 시그니처 검사와 언어별 코드는 signature 패키지가 만든다.

채점
- judged_code: mode가 function이면 제출 코드를 완성된 프로그램으로 합친다. 채점기는 그 프로그램을
  일반 문제처럼 컴파일·실행한다. DB의 submissions.code에는 사용자가 쓴 코드를 그대로 둔다.
- add_display: 예제 실행 결과의 입력, 기대 출력, 내 출력을 함수 인자 표기로 바꿔 덧붙인다.

엔드포인트
- GET  /problems/{id}/templates  푸는 화면: 시그니처, 언어별 시작 코드, 예제의 인자 표기
- POST /signature/preview        관리자: 시그니처 검사, 시작 코드 미리 보기, 테스트가 규칙에 맞는지
- POST /signature/convert        관리자: 인자 표기 예제를 저장할 입출력 텍스트로
"""
import json
import logging
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import signature
from signature import Signature, SignatureError

logger = logging.getLogger(__name__)

# DB의 problems_signature_valid 제약과 같은 한도
MAX_SIGNATURE_BYTES = 4096
# 관리자 화면이 한 번에 검사를 맡기는 테스트. 이보다 큰 테스트는 화면에서 빼고 보낸다.
MAX_TESTS = 100
MAX_TEST_TEXT = 200_000

NOT_SUPPORTED = "이 문제는 함수 구현으로 낼 수 없습니다. 전체 프로그램으로 내 주세요."
BROKEN = "이 문제의 함수 정보가 잘못되어 있어 함수 구현으로 낼 수 없습니다. 관리자에게 알려 주세요."


def stored_signature(raw) -> Optional[Signature]:
    """DB에 저장된 시그니처. 없거나 잘못됐으면 None(잘못된 것은 로그에 남긴다)."""
    if raw is None:
        return None
    try:
        return signature.parse_signature(raw)
    except SignatureError as error:
        logger.warning("Invalid stored signature %r: %s", raw, error)
        return None


def judged_code(mode: str, language: str, code: str, raw_signature):
    """채점기에 보낼 코드와, 함수 구현이면 그 시그니처. (signature | None, code)"""
    if mode == "program":
        return None, code
    if raw_signature is None:
        raise HTTPException(422, NOT_SUPPORTED)
    sig = stored_signature(raw_signature)
    if sig is None:
        raise HTTPException(422, BROKEN)
    try:
        return sig, signature.compose(sig, language, code)
    except SignatureError as error:
        raise HTTPException(422, str(error))


def show_input(sig: Signature, text: str) -> Optional[str]:
    try:
        return signature.format_args(sig, signature.decode_input(sig, text))
    except (SignatureError, ValueError):
        return None


def show_output(sig: Signature, text: str) -> Optional[str]:
    try:
        return signature.format_value(sig.output_type, signature.decode_output(sig.output_type, text))
    except (SignatureError, ValueError):
        return None


# 출력이 답 모양이라고 볼 수 있는 판정. 런타임 에러의 출력에는 오류 메시지가 섞여 있다.
_ANSWERED = ("Accepted", "Wrong Answer")


def add_display(sig: Signature, result: dict) -> dict:
    """
    예제 실행 결과의 테스트마다 display = {"input", "expected", "actual"}를 덧붙인다.
    바꾸지 못한 칸(잘린 입력, 답 모양이 아닌 출력)은 None이라 화면이 원문을 보여 준다.
    """
    for case in result.get("test_cases") or []:
        if not isinstance(case, dict):
            continue
        text = lambda key: case.get(key) if isinstance(case.get(key), str) else ""
        case["display"] = {
            "input": show_input(sig, text("input")),
            "expected": show_output(sig, text("expected")),
            "actual": show_output(sig, text("actual")) if case.get("status") in _ANSWERED else None,
        }
    return result


def _same_output(a: str, b: str) -> bool:
    """채점기의 outputs_match와 같은 비교(전체 앞뒤 공백, 줄 끝 공백은 무시)."""
    lines = lambda text: [line.rstrip() for line in text.replace("\r", "").strip().split("\n")]
    return lines(a) == lines(b)


def check_test(sig: Signature, input_text: str, output_text: str) -> dict:
    """
    저장된 테스트 하나가 함수 구현으로도 풀 수 있는 모양인지 본다.
    normalized_output: 답이 맞는 값이지만 드라이버가 출력하는 모양과 다를 때, 그 모양
    """
    report = {"input": None, "output": None, "error": None, "normalized_output": None}
    try:
        report["input"] = signature.format_args(sig, signature.decode_input(sig, input_text))
    except SignatureError as error:
        report["error"] = f"입력: {error}"
        return report
    try:
        t = sig.output_type
        report["output"] = signature.format_value(t, signature.decode_output(t, output_text))
        normalized = signature.normalize_output(t, output_text)
    except SignatureError as error:
        report["error"] = f"출력: {error}"
        return report
    if not _same_output(normalized, output_text):
        report["error"] = "출력: 함수 구현의 답은 다른 모양으로 출력됩니다. 저장된 답과 글자가 같아야 맞습니다."
        report["normalized_output"] = normalized
    return report


class SignatureBody(BaseModel):
    signature: dict


class TestText(BaseModel):
    input: str = Field(default="", max_length=MAX_TEST_TEXT)
    output: str = Field(default="", max_length=MAX_TEST_TEXT)


class PreviewBody(SignatureBody):
    tests: list[TestText] = Field(default_factory=list, max_length=MAX_TESTS)


class ConvertBody(SignatureBody):
    input: str = Field(max_length=MAX_TEST_TEXT)
    output: Optional[str] = Field(default=None, max_length=MAX_TEST_TEXT)


def parse_body_signature(raw: dict) -> Signature:
    if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > MAX_SIGNATURE_BYTES:
        raise HTTPException(422, "시그니처가 너무 깁니다.")
    try:
        return signature.parse_signature(raw)
    except SignatureError as error:
        raise HTTPException(422, str(error))


def create_router(*, supabase, current_user: Callable, released: Callable[[int], bool]) -> APIRouter:
    """released(problem_id): 공개된 문제인지(시작 전 대회의 문제가 아닌지)"""
    router = APIRouter()

    def is_admin(user_id: str) -> bool:
        rows = supabase.table("profiles").select("is_admin").eq("id", user_id).execute().data
        return bool(rows and rows[0].get("is_admin"))

    async def require_admin(user_id: str = Depends(current_user)) -> str:
        try:
            allowed = await run_in_threadpool(is_admin, user_id)
        except Exception:
            logger.exception("관리자 여부를 확인하지 못했습니다")
            raise HTTPException(503, "관리자 여부를 확인하지 못했습니다. 잠시 후 다시 시도하세요.")
        if not allowed:
            raise HTTPException(403, "관리자만 쓸 수 있습니다.")
        return user_id

    def load_templates(problem_id: int) -> dict:
        try:
            rows = supabase.table("problems").select("signature").eq("id", problem_id).execute().data
            if not rows or not released(problem_id):
                raise HTTPException(404, "문제를 찾을 수 없습니다.")
            sig = stored_signature(rows[0].get("signature"))
            if sig is None:
                return {"signature": None, "starters": {}, "examples": []}
            cases = (
                supabase.table("test_cases")
                .select("id, input_data, expected_output")
                .eq("problem_id", problem_id)
                .eq("is_hidden", False)
                .order("id")
                .execute()
                .data
            ) or []
        except HTTPException:
            raise
        except Exception:
            logger.exception("Failed to load templates for problem %s", problem_id)
            raise HTTPException(503, "문제 정보를 불러오지 못했습니다. 잠시 후 다시 시도하세요.")
        return {
            "signature": sig.to_json(),
            "starters": signature.starters(sig),
            "examples": [
                {
                    "id": case["id"],
                    "input": show_input(sig, case.get("input_data") or ""),
                    "output": show_output(sig, case.get("expected_output") or ""),
                }
                for case in cases
            ],
        }

    @router.get("/problems/{problem_id}/templates")
    async def templates(problem_id: int = Path(gt=0)):
        return await run_in_threadpool(load_templates, problem_id)

    def run_preview(body: PreviewBody) -> dict:
        sig = parse_body_signature(body.signature)
        return {
            "signature": sig.to_json(),
            "starters": signature.starters(sig),
            "tests": [check_test(sig, test.input, test.output) for test in body.tests],
        }

    @router.post("/signature/preview")
    async def preview(body: PreviewBody, _: str = Depends(require_admin)):
        # 큰 테스트를 여럿 읽는 동안 다른 요청이 멈추지 않도록 스레드에서 한다.
        return await run_in_threadpool(run_preview, body)

    @router.post("/signature/convert")
    async def convert(body: ConvertBody, _: str = Depends(require_admin)):
        sig = parse_body_signature(body.signature)
        try:
            input_text = signature.encode_input(sig, signature.parse_leetcode_input(sig, body.input))
            output_text = None
            if body.output is not None and body.output.strip():
                output_text = signature.encode_output(
                    sig.output_type, signature.parse_leetcode_output(sig, body.output),
                )
        except SignatureError as error:
            raise HTTPException(422, str(error))
        return {"input": input_text, "output": output_text}

    return router
