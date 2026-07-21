from fastapi import FastAPI
from pydantic import BaseModel
import redis
import json
import uuid
import os

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

  task_data = {
    "submission_id": submission_id,
    "problem_id": submission.problem_id,
    "language": submission.language,
    "code": submission.code
  }

  r.rpush("submission_queue", json.dumps(task_data))

  return {
    "submission_id": submission_id, 
    "status": "pending", 
    "message": "코드가 성공적으로 큐에 등록되었습니다."
  }
