FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 코드가 바뀔 때마다 다시 띄우는 --reload는 개발할 때만 켠다 (UVICORN_RELOAD=1).
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 ${UVICORN_RELOAD:+--reload}"]
