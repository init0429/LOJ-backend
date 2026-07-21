from fastapi import FastAPI
from pydantic import BaseModel
import redis
import json
import uuid
from supabase import create_client, Client
import os

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(redis_url)

class Submission(BaseModel):
  problem_id: int
  language: str
  code: str

@app.post("/submit")
async def submit_code(submission: Submission):
  submission_id = str(uuid.uuid4())

  # DB에서 테스트케이스 조회
  try:
    response = supabase.table("test_cases").select("input_data, expected_output").eq("problem_id", submission.problem_id).execute()
    test_cases = [{"input": tc["input_data"] + "\n", "output": tc["expected_output"]} for tc in response.data]
    
    if not test_cases:
      # DB에 없는 경우 기존 하드코딩된 더미로 폴백 (테스트 환경 지원)
      test_cases = [
        {"input": "1 2\n", "output": "3"},
        {"input": "10 20\n", "output": "30"},
        {"input": "-5 5\n", "output": "0"},
      ]
  except Exception as e:
    return {"status": "error", "message": f"테스트케이스 조회 실패: {str(e)}"}

  supabase.table("submissions").insert({
    "id": submission_id,
    "problem_id": submission.problem_id,
    "language": submission.language,
    "code": submission.code,
    "status": "Pending"
  }).execute()

  task_data = {
    "submission_id": submission_id,
    "problem_id": submission.problem_id,
    "language": submission.language,
    "code": submission.code,
    "test_cases": test_cases
  }

  r.rpush("submission_queue", json.dumps(task_data))

  return {
    "submission_id": submission_id, 
    "status": "pending", 
    "message": "코드가 성공적으로 큐에 등록되었습니다."
  }
