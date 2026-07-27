from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Literal, Optional
import redis
import json
import uuid
import asyncio
from supabase import create_client, Client
import os

# judge/worker.py의 LANGUAGES와 반드시 동기화해서 유지할 것.
# 여기서 막아두면 judge까지 안 가고 API 단에서 바로 422로 거절됨.
SupportedLanguage = Literal["python", "c", "cpp", "java", "sql"]

url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(url, key)

app = FastAPI(title="LOJ Backend API", version="1.0.0")

_extra_origins = os.getenv("ALLOWED_ORIGINS", "")
allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
] + [o.strip() for o in _extra_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(redis_url)


async def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
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

    return user.id



class Submission(BaseModel):
    problem_id: int
    language: SupportedLanguage
    code: str
    contest_id: Optional[int] = None  # 콘테스트 중 제출이면 채워짐

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/submit")
async def submit_code(submission: Submission, user_id: str = Depends(get_current_user_id)):
    submission_id = str(uuid.uuid4())

    # DB에서 테스트케이스 조회
    try:
        response = (
            supabase.table("test_cases")
            .select("input_data, expected_output")
            .eq("problem_id", submission.problem_id)
            .order("id")
            .execute()
        )
        test_cases = [
            {"input": tc["input_data"] + "\n", "output": tc["expected_output"]}
            for tc in response.data
        ]

        if not test_cases:
            # DB에 없는 경우 더미로 폴백
            test_cases = [
                {"input": "1 2\n", "output": "3"},
                {"input": "10 20\n", "output": "30"},
                {"input": "-5 5\n", "output": "0"},
            ]
    except Exception as e:
        return {"status": "error", "message": f"테스트케이스 조회 실패: {str(e)}"}

    try:
        supabase.table("submissions").insert({
            "id": submission_id,
            "problem_id": submission.problem_id,
            "language": submission.language,
            "code": submission.code,
            "status": "Pending",
            "user_id": user_id,
            "contest_id": submission.contest_id,
        }).execute()
    except Exception as e:
        return {"status": "error", "message": f"제출 기록 실패: {str(e)}"}

    task_data = {
        "submission_id": submission_id,
        "problem_id": submission.problem_id,
        "language": submission.language,
        "code": submission.code,
        "test_cases": test_cases,
    }

    r.rpush("submission_queue", json.dumps(task_data))

    return {
        "submission_id": submission_id,
        "status": "pending",
        "message": "코드가 성공적으로 큐에 등록되었습니다.",
    }

@app.post("/test_run")
async def test_code(submission: Submission, user_id: str = Depends(get_current_user_id)):
    test_id = str(uuid.uuid4())

    try:
        response = (
            supabase.table("test_cases")
            .select("input_data, expected_output")
            .eq("problem_id", submission.problem_id)
            .eq("is_hidden", False)
            .order("id")
            .execute()
        )
        test_cases = [
            {"input": tc["input_data"] + "\n", "output": tc["expected_output"]}
            for tc in response.data
        ]

        if not test_cases:
            return {"status": "error", "message": "테스트케이스가 없습니다."}
    except Exception as e:
        return {"status": "error", "message": f"테스트케이스 조회 실패: {str(e)}"}

    task_data = {
        "submission_id": test_id,
        "is_test": True,
        "problem_id": submission.problem_id,
        "language": submission.language,
        "code": submission.code,
        "test_cases": test_cases,
    }

    pubsub = r.pubsub()
    channel_name = f"test_result:{test_id}"
    pubsub.subscribe(channel_name)

    r.rpush("submission_queue", json.dumps(task_data))

    timeout = 15
    start_time = asyncio.get_event_loop().time()
    result = None

    while asyncio.get_event_loop().time() - start_time < timeout:
        message = pubsub.get_message(ignore_subscribe_messages=True)
        if message:
            result = json.loads(message["data"].decode("utf-8"))
            break
        await asyncio.sleep(0.1)

    pubsub.unsubscribe(channel_name)
    pubsub.close()

    if result:
        return {"status": "success", "result": result}
    else:
        return {"status": "error", "message": "채점 서버 응답 대기 시간이 초과되었습니다."}