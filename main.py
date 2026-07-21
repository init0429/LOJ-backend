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

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(redis_url)

class Submission(BaseModel):
  problem_id: int
  language: str
  code: str

@app.post("/submit")
async def submit_code(submission: Submission):
  submission_id = str(uuid.uuid4())

  dummy_test_cases = [
    {"input": "1 2\n", "output": "3"},
    {"input": "10 20\n", "output": "30"},
    {"input": "-5 5\n", "output": "0"},
  ]

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
    "test_cases": dummy_test_cases
  }

  r.rpush("submission_queue", json.dumps(task_data))

  return {
    "submission_id": submission_id, 
    "status": "pending", 
    "message": "코드가 성공적으로 큐에 등록되었습니다."
  }
