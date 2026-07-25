from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Literal
import redis
import json
import uuid
import asyncio
from supabase import create_client, Client
import os

SupportedLanguage = Literal["python", "cpp", "java"]

url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(url, key)

app = FastAPI(title="LOJ Backend API", version="1.0.0")

allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(redis_url)

class Submission(BaseModel):
    problem_id: int
    language: SupportedLanguage
    code: str

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/submit")
async def submit_code(submission: Submission):
    submission_id = str(uuid.uuid4())

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
async def test_code(submission: Submission):
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