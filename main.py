import asyncio
import contextlib
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional
import redis
import redis.asyncio
import json
import uuid
import time
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
import os

import function_problems
import recommend
import solvedac_client
import solvedac_link
import solvedac_tags

# judge/worker.py의 LANGUAGES와 반드시 동기화해서 유지할 것.
# 여기서 막아두면 judge까지 안 가고 API 단에서 바로 422로 거절됨.
SupportedLanguage = Literal[
    "python", "c", "cpp", "java", "sql", "mysql",
    "javascript", "typescript", "ruby", "php", "perl",
]

def required_env(name: str) -> str:
    """빠지면 서버가 떠도 아무 요청도 처리하지 못하는 설정. 시작할 때 바로 알린다."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"환경 변수 {name}가 비어 있습니다. .env를 확인하세요 (.env.example 참고).")
    return value


supabase: Client = create_client(required_env("SUPABASE_URL"), required_env("SUPABASE_KEY"))

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    # solved.ac 연동 사용자의 통계를 밤마다 받는다(recommend.Recommender.run_nightly).
    nightly = asyncio.create_task(recommender.run_nightly()) if recommender.can_sync else None
    try:
        yield
    finally:
        if nightly is not None:
            nightly.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await nightly


app = FastAPI(title="LOJ Backend API", version="1.0.0", lifespan=lifespan)

# 브라우저가 이 API를 직접 부를 때 허용할 출처. 지정하면 그 목록만 허용하고, 비워 두면
# 개발용 프론트(localhost:3000)만 허용한다. 프론트가 같은 도메인의 /api로 부르면 필요 없다.
allowed_origins = [
    origin.strip()
    for origin in (os.getenv("ALLOWED_ORIGINS") or "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if origin.strip()
]

# 로그인 화면은 학교 계정만 고르게 하지만 그건 Google 화면의 안내일 뿐이라, 다른 Google
# 계정으로 받은 토큰도 유효하다. 여기에 도메인을 적으면(쉼표로 여러 개) 그 계정만 채점을
# 요청할 수 있다. 비워 두면 제한하지 않는다.
ALLOWED_EMAIL_DOMAINS = {
    domain.strip().lower().lstrip("@")
    for domain in os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(",")
    if domain.strip()
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(redis_url, socket_connect_timeout=5, socket_timeout=5)
# 예제 실행은 결과를 기다리는 동안 스레드를 붙잡지 않도록 비동기 연결로 구독한다.
async_r = redis.asyncio.from_url(redis_url, socket_connect_timeout=5, socket_timeout=5)
# 예제 실행 결과를 기다리는 최대 시간(초). 프록시의 응답 제한(보통 30초)보다 짧게 둔다.
TEST_RUN_TIMEOUT = 25
logger = logging.getLogger(__name__)

# 채점 요청 제한. 카운터와 잠금을 Redis에 두어 API 프로세스가 여럿이어도 같은 기준으로 센다.
# - 한 사용자가 RATE_WINDOW초 동안 보낼 수 있는 요청(제출과 예제 실행을 합쳐서)은 RATE_LIMIT번.
# - 채점이 끝나지 않은 제출이 있으면 새 제출을 받지 않고, 예제 실행도 한 번에 하나만 받는다.
#   횟수 제한만으로는 한 사람이 시간이 오래 걸리는 코드를 연달아 넣어 대기열 앞을 차지할 수 있다.
#   (화면은 채점 중에 버튼을 잠그므로 보통은 걸리지 않고, 스크립트나 여러 탭에서 보낼 때 걸린다.)
RATE_LIMIT = 10
RATE_WINDOW = 60
# 채점기가 멈춰 결과가 영영 안 나오는 제출 때문에 계속 막히지 않도록, 이보다 오래된 제출은 세지 않는다.
PENDING_SUBMISSION_WINDOW = timedelta(minutes=10)
# 제출 잠금은 확인부터 기록까지만 쥐고 있는다. 프로세스가 그사이 죽어도 이 시간(초) 뒤에 풀린다.
SUBMIT_LOCK_TTL = 30
RATE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""
ENQUEUE_SCRIPT = """
local count = redis.call('LLEN', KEYS[1])
local bytes = 0
for _, payload in ipairs(redis.call('LRANGE', KEYS[1], 0, -1)) do
    bytes = bytes + string.len(payload)
end
if count >= 100 or bytes + string.len(ARGV[1]) > 16777216 then return 0 end
redis.call('RPUSH', KEYS[1], ARGV[1])
return 1
"""


QUEUE_FULL = "채점 대기열이 가득 찼습니다. 잠시 후 다시 시도하세요."
JUDGE_UNAVAILABLE = "채점 서버에 연결하지 못했습니다. 잠시 후 다시 시도하세요."


def user_key(user_id: str) -> str:
    """Redis 키에 사용자 id를 그대로 남기지 않는다."""
    return hashlib.sha256(user_id.encode()).hexdigest()


def busy(message: str, retry_after: int = 5) -> HTTPException:
    return HTTPException(429, message, headers={"Retry-After": str(retry_after)})


def check_admission(user_id: str):
    try:
        count, ttl = r.eval(RATE_SCRIPT, 1, f"judge:rate:{user_key(user_id)}", RATE_WINDOW)
        queued = r.llen("submission_queue") if count <= RATE_LIMIT else 0
    except redis.RedisError:
        raise HTTPException(503, JUDGE_UNAVAILABLE)
    if count > RATE_LIMIT:
        wait = max(int(ttl), 1)
        raise busy(f"채점 요청이 너무 잦습니다. {wait}초 뒤에 다시 시도하세요.", wait)
    if queued >= 100:
        raise HTTPException(503, QUEUE_FULL, headers={"Retry-After": "10"})


def check_no_pending_submission(user_id: str):
    """채점이 끝나지 않은 내 제출이 있으면 새 제출을 받지 않는다."""
    since = (datetime.now(timezone.utc) - PENDING_SUBMISSION_WINDOW).isoformat()
    try:
        pending = (
            supabase.table("submissions").select("id")
            .eq("user_id", user_id)
            .in_("status", ["Pending", "Running"])
            .gte("created_at", since)
            .limit(1)
            .execute()
        )
    except Exception:
        logger.exception("Failed to check pending submissions")
        raise HTTPException(503, "제출 기록을 확인하지 못했습니다. 잠시 후 다시 시도하세요.")
    if pending.data:
        raise busy("앞선 제출을 채점하고 있습니다. 결과가 나온 뒤에 다시 제출하세요.")


@contextlib.contextmanager
def submission_lock(user_id: str):
    """
    '채점 중인 제출이 없는지 확인 → 제출 기록'을 한 사람당 한 번에 하나씩만 하게 한다.
    잠그지 않으면 동시에 보낸 요청이 모두 확인을 통과해 한꺼번에 들어간다.
    """
    key = f"judge:submitting:{user_key(user_id)}"
    try:
        acquired = r.set(key, "1", nx=True, ex=SUBMIT_LOCK_TTL)
    except redis.RedisError:
        raise HTTPException(503, JUDGE_UNAVAILABLE)
    if not acquired:
        raise busy("앞선 제출을 접수하고 있습니다. 잠시 후 다시 제출하세요.")
    try:
        yield
    finally:
        with contextlib.suppress(redis.RedisError):
            r.delete(key)


def enqueue(task: dict):
    payload = json.dumps(task)
    if len(payload.encode("utf-8")) > 1048576:
        raise HTTPException(422, "제출 내용이 너무 큽니다(1 MiB 제한).")
    if not r.eval(ENQUEUE_SCRIPT, 1, "submission_queue", payload):
        raise HTTPException(503, QUEUE_FULL, headers={"Retry-After": "10"})


def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    """
    프론트에서 보내는 `Authorization: Bearer <supabase access_token>`을 검증해서
    user_id를 뽑아낸다. /submit, /test_run 둘 다 이걸 통과해야 호출 가능함
    (로그인 안 한 사람이 채점 서버 자원을 그냥 쓰는 걸 막기 위함이기도 함).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    token = authorization.removeprefix("Bearer ").strip()

    try:
        user_response = supabase.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다.")

    user = getattr(user_response, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다.")

    if ALLOWED_EMAIL_DOMAINS:
        domain = (getattr(user, "email", None) or "").rpartition("@")[2].lower()
        if domain not in ALLOWED_EMAIL_DOMAINS:
            raise HTTPException(status_code=403, detail="학교 계정으로 로그인해야 이용할 수 있습니다.")

    return user.id


# solved.ac 계정 연동. 앱 설정(SOLVEDAC_CLIENT_ID 등)과 토큰 암호화 키가 모두 있어야 켜진다.
_solvedac_cipher = solvedac_link.TokenCipher.from_env()
_solvedac_store = solvedac_link.SupabaseTokenStore(supabase, _solvedac_cipher) if _solvedac_cipher else None
solvedac = solvedac_client.SolvedacClient.from_env(store=_solvedac_store, cache=async_r)
if solvedac.configured and _solvedac_store is None:
    logger.warning("SOLVEDAC_TOKEN_KEY가 비어 있어 solved.ac 연동을 끕니다.")
recommender = recommend.Recommender(client=solvedac, supabase=supabase, store=_solvedac_store, redis=async_r)
app.include_router(solvedac_link.create_router(
    client=solvedac, store=_solvedac_store, redis=async_r, current_user=get_current_user_id,
    # 연동을 마치면 추천에 쓸 통계를 바로 한 번 받는다.
    on_linked=recommender.sync_user,
))
# 관리자 화면의 태그·티어 자동 채우기와 태그 목록 받기. 앱 설정만 있으면 된다(토큰 불필요).
app.include_router(solvedac_tags.create_router(
    client=solvedac, supabase=supabase, current_user=get_current_user_id,
))
# 레벨에 맞는 문제 추천. solved.ac를 연동하지 않았어도 LOJ 기록만으로 동작한다.
app.include_router(recommend.create_router(recommender=recommender, current_user=get_current_user_id))


class Submission(BaseModel):
    problem_id: int = Field(gt=0)
    language: SupportedLanguage
    code: str = Field(min_length=1, max_length=100_000)
    contest_id: Optional[int] = Field(default=None, gt=0)
    # function: 함수(메서드)만 써서 낸다. 문제에 시그니처가 있어야 한다(function_problems.py).
    mode: Literal["program", "function"] = "program"

    @field_validator("code")
    @classmethod
    def nonempty_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Code must not be blank")
        return value


def parse_time(value: str) -> datetime:
    """Supabase가 돌려주는 ISO 8601 시각을 시간대가 붙은 datetime으로 바꾼다."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def problem_released(problem_id: int) -> bool:
    """아직 시작하지 않은 대회에 든 문제가 아닌지. DB의 problem_is_released()와 같은 기준이다."""
    links = (
        supabase.table("contest_problems")
        .select("contests(start_time)")
        .eq("problem_id", problem_id)
        .execute()
    )
    now = datetime.now(timezone.utc)
    return not any(
        parse_time(row["contests"]["start_time"]) > now
        for row in links.data or []
        if row.get("contests")
    )


def validate_judging_context(submission: Submission, public_only: bool = False):
    """
    채점을 큐에 넣기 전에 문제·콘테스트·테스트케이스가 유효한지 확인한다.
    테스트 데이터 자체는 judge가 DB에서 직접 읽으므로 여기서는 존재 여부만 본다.
    함수 구현으로 내면 문제의 시그니처(DB에 저장된 그대로)를 돌려준다.
    """
    try:
        # 시그니처는 함수 구현일 때만 읽는다. 마이그레이션 전에도 일반 제출은 그대로 된다.
        columns = "id, signature" if submission.mode == "function" else "id"
        problem = supabase.table("problems").select(columns).eq("id", submission.problem_id).execute()
        if not problem.data:
            raise HTTPException(404, "문제를 찾을 수 없습니다.")
        if submission.contest_id is None:
            # 아직 시작하지 않은 대회의 문제는 대회 밖에서 실행하거나 제출할 수 없다. 예제 실행은
            # 예제 입출력을 돌려주므로, 막지 않으면 문제 번호만으로 대회 전에 예제를 볼 수 있다.
            # (브라우저에서 직접 읽는 길은 DB의 RLS가 막는다.)
            if not problem_released(submission.problem_id):
                raise HTTPException(404, "문제를 찾을 수 없습니다.")
        else:
            contests = supabase.table("contests").select("start_time, end_time").eq("id", submission.contest_id).execute()
            if not contests.data:
                raise HTTPException(404, "콘테스트를 찾을 수 없습니다.")
            contest = contests.data[0]
            start = parse_time(contest["start_time"])
            end = parse_time(contest["end_time"])
            if not start <= datetime.now(timezone.utc) < end:
                raise HTTPException(409, "콘테스트 진행 시간에만 실행하거나 제출할 수 있습니다.")
            links = supabase.table("contest_problems").select("problem_id").eq("contest_id", submission.contest_id).eq("problem_id", submission.problem_id).execute()
            if not links.data:
                raise HTTPException(400, "콘테스트에 포함되지 않은 문제입니다.")
        query = supabase.table("test_cases").select("id").eq("problem_id", submission.problem_id)
        if public_only:
            query = query.eq("is_hidden", False)
        if not query.limit(1).execute().data:
            raise HTTPException(422, "실행 가능한 테스트케이스가 없습니다.")
        return problem.data[0].get("signature")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to load judging context")
        raise HTTPException(503, "채점 정보를 불러오지 못했습니다. 잠시 후 다시 시도하세요.")

# 함수형 문제: 푸는 화면의 시작 코드와 관리자 화면의 시그니처 도구
app.include_router(function_problems.create_router(
    supabase=supabase, current_user=get_current_user_id,
    released=lambda problem_id: problem_released(problem_id),
))


@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/submit")
def submit_code(submission: Submission, user_id: str = Depends(get_current_user_id)):
    check_admission(user_id)
    submission_id = str(uuid.uuid4())

    # 기록이 Pending으로 남으면 그때부터는 check_no_pending_submission이 다음 제출을 막는다.
    with submission_lock(user_id):
        check_no_pending_submission(user_id)
        raw_signature = validate_judging_context(submission)
        _, code = function_problems.judged_code(
            submission.mode, submission.language, submission.code, raw_signature,
        )
        row = {
            "id": submission_id,
            "problem_id": submission.problem_id,
            "language": submission.language,
            # 함수 구현이어도 기록에는 사용자가 쓴 코드를 남긴다. 채점기에는 합친 프로그램을 보낸다.
            "code": submission.code,
            "status": "Pending",
            "user_id": user_id,
            "contest_id": submission.contest_id,
        }
        if submission.mode != "program":
            # 기본값이 program이라 일반 제출은 적지 않는다(마이그레이션 전에도 제출이 되도록).
            row["mode"] = submission.mode
        try:
            supabase.table("submissions").insert(row).execute()
        except Exception:
            logger.exception("Failed to insert submission")
            raise HTTPException(503, "제출을 기록하지 못했습니다. 다시 시도하세요.")

    task_data = {
        "submission_id": submission_id,
        "problem_id": submission.problem_id,
        "language": submission.language,
        "code": code,
    }

    try:
        enqueue(task_data)
    except (redis.RedisError, HTTPException) as exc:
        logger.exception("Failed to enqueue submission %s", submission_id)
        try:
            supabase.table("submissions").update({"status": "System Error"}).eq("id", submission_id).execute()
        except Exception:
            logger.exception("Failed to mark submission as failed %s", submission_id)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(503, "채점 서버에 연결하지 못했습니다. 잠시 후 다시 제출하세요.")

    return {
        "submission_id": submission_id,
        "status": "pending",
        "message": "코드가 성공적으로 큐에 등록되었습니다.",
    }

@app.post("/test_run")
async def test_code(submission: Submission, user_id: str = Depends(get_current_user_id)):
    # 결과를 기다리는 동안은 이벤트 루프에서 쉬고, DB·Redis를 동기로 부르는 부분만
    # 스레드 풀로 보낸다. 요청마다 스레드를 끝까지 붙잡으면 동시에 수십 명이 예제를
    # 실행할 때 스레드 풀이 바닥나서 /submit까지 멈춘다.
    await run_in_threadpool(check_admission, user_id)
    # 예제 실행은 한 사람당 한 번에 하나. 결과를 기다리는 동안 쥐고 있다가 응답과 함께 놓는다.
    # 응답하기 전에 프로세스가 죽어도 기다림 제한 시간이 지나면 풀린다.
    slot = f"judge:testing:{user_key(user_id)}"
    try:
        acquired = await async_r.set(slot, "1", nx=True, ex=TEST_RUN_TIMEOUT + 5)
    except redis.RedisError:
        raise HTTPException(503, JUDGE_UNAVAILABLE)
    if not acquired:
        raise busy("예제를 실행하고 있습니다. 결과가 나온 뒤에 다시 실행하세요.")
    try:
        return await run_test(submission)
    finally:
        with contextlib.suppress(redis.RedisError):
            await async_r.delete(slot)


async def run_test(submission: Submission):
    raw_signature = await run_in_threadpool(validate_judging_context, submission, True)
    sig, code = function_problems.judged_code(
        submission.mode, submission.language, submission.code, raw_signature,
    )
    test_id = str(uuid.uuid4())

    task_data = {
        "submission_id": test_id,
        "is_test": True,
        "expires_at": time.time() + TEST_RUN_TIMEOUT,
        "problem_id": submission.problem_id,
        "language": submission.language,
        "code": code,
    }

    channel_name = f"test_result:{test_id}"
    try:
        async with async_r.pubsub() as pubsub:
            await pubsub.subscribe(channel_name)
            await run_in_threadpool(enqueue, task_data)
            deadline = time.monotonic() + TEST_RUN_TIMEOUT
            while (remaining := deadline - time.monotonic()) > 0:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=min(remaining, 1.0),
                )
                if message:
                    result = json.loads(message["data"])
                    if not isinstance(result, dict) or not isinstance(result.get("status"), str):
                        raise ValueError("Invalid judge response")
                    if sig is not None:
                        result = function_problems.add_display(sig, result)
                    return {"status": "success", "result": result}
    except redis.RedisError:
        logger.exception("Test-run queue unavailable")
        raise HTTPException(503, "채점 서버에 연결하지 못했습니다.")
    except (ValueError, TypeError):
        logger.exception("Invalid test-run response")
        raise HTTPException(502, "채점 서버에서 올바르지 않은 결과를 받았습니다.")
    raise HTTPException(504, "채점 대기 시간이 초과되었습니다. 잠시 후 다시 실행하세요.")
