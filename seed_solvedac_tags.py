"""
solved.ac 태그 목록을 tags 표에 넣는다. 처음 한 번, 그리고 solved.ac에 태그가 늘었을 때 실행한다.
관리자 화면의 "태그 목록 받기" 버튼도 같은 일을 한다.

    docker compose exec backend python seed_solvedac_tags.py

backend/.env의 SUPABASE_URL, SUPABASE_KEY(secret 키), SOLVEDAC_CLIENT_ID, SOLVEDAC_CLIENT_SECRET,
SOLVEDAC_REDIRECT_URI를 쓴다. migrations/20261118_solvedac_tags.sql을 먼저 실행해야 한다.
"""
import asyncio
import os
import sys

from supabase import create_client

import solvedac_client
import solvedac_tags


async def main() -> int:
    url, key = os.getenv("SUPABASE_URL", "").strip(), os.getenv("SUPABASE_KEY", "").strip()
    if not url or not key:
        print("SUPABASE_URL과 SUPABASE_KEY가 필요합니다.", file=sys.stderr)
        return 1
    client = solvedac_client.SolvedacClient.from_env(store=None)
    if not client.configured:
        print("SOLVEDAC_CLIENT_ID, SOLVEDAC_CLIENT_SECRET, SOLVEDAC_REDIRECT_URI가 필요합니다.", file=sys.stderr)
        return 1
    try:
        count = await solvedac_tags.seed_tags(client, create_client(url, key))
    finally:
        await client.aclose()
    print(f"solved.ac 태그 {count}개를 저장했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
