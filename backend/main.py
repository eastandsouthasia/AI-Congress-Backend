"""
main.py — AI Congress FastAPI 서버 진입점 (v2 · 턴 기반)

WebSocket 엔드포인트:
  ws://<host>/debate

프론트엔드(DebateScreen.js)가 연결 즉시 전송하는 JSON:
  {
    "issue":          str,   # 토론 안건
    "maxTurns":       int,   # 최대 발언 턴 수 (구 duration 대체)
    "debateFormat":   str,   # "릴레이" | "집중토론" | "전문가패널" | "자유토론"
    "conclusionType": str,   # "VOTE" | "RESOLUTION"
    "activeMembers":  list,  # 참여 의원 ID 배열 (없으면 전원 참여)
  }

v2 변경:
  - duration(분) → maxTurns(발언 횟수) 교체
  - maxTurns 범위 보정: 5 ~ 200턴
  - DebateEngine 생성자 파라미터 동기화
"""

import os
import json
import asyncio
import traceback
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from debate_engine import DebateEngine

app = FastAPI(title="AI Congress Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "AI Congress Backend is running"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "AI Congress Backend"}


@app.websocket("/debate")
async def debate_ws(ws: WebSocket):
    await ws.accept()
    print("[WS] 클라이언트 연결됨")

    try:
        raw  = await asyncio.wait_for(ws.receive_text(), timeout=30)
        data = json.loads(raw)

        issue           = str(data.get("issue", "")).strip()
        max_turns       = int(data.get("maxTurns", 25))
        debate_format   = str(data.get("debateFormat",   "릴레이"))
        conclusion_type = str(data.get("conclusionType", "VOTE"))
        active_members  = data.get("activeMembers", None)

    except asyncio.TimeoutError:
        print("[WS] 안건 수신 타임아웃")
        try:
            await ws.send_json({"type": "error", "message": "연결 시간이 초과되었습니다. 다시 시도해주세요."})
            await ws.send_json({"type": "done"})
        except Exception:
            pass
        return

    except Exception as e:
        print(f"[WS] 초기 메시지 파싱 오류: {e}")
        try:
            await ws.send_json({"type": "error", "message": f"메시지 파싱 오류: {e}"})
            await ws.send_json({"type": "done"})
        except Exception:
            pass
        return

    if not issue:
        await ws.send_json({"type": "error", "message": "안건이 없습니다."})
        await ws.send_json({"type": "done"})
        return

    # 턴 수 범위 보정 (5~200)
    max_turns = max(5, min(200, max_turns))

    print(
        f"[WS] 안건: {issue[:40]} / {max_turns}턴 / {debate_format} / "
        f"{conclusion_type} / 의원: {active_members}"
    )

    engine = DebateEngine(
        issue           = issue,
        max_turns       = max_turns,
        ws              = ws,
        debate_format   = debate_format,
        conclusion_type = conclusion_type,
        active_members  = active_members,
    )

    try:
        await engine.run()

    except WebSocketDisconnect:
        print(f"[WS] 클라이언트 연결 종료 — 안건: {issue[:30]}")

    except Exception as e:
        print(f"[WS] 엔진 예외 발생: {e}")
        traceback.print_exc()
        try:
            await ws.send_json({
                "type":    "error",
                "message": f"서버 오류가 발생했습니다: {str(e)[:100]}",
            })
            await ws.send_json({"type": "done"})
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")