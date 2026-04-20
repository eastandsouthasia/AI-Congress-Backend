"""
AI 의회 백엔드 서버 - 안정화 버전
✅ activeMembers 파라미터 수신 추가
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

@app.get("/health")
async def health():
    return {"status": "ok", "service": "AI Congress Backend"}

@app.get("/")
async def root():
    return {"message": "AI Congress Backend is running"}


@app.websocket("/debate")
async def debate_ws(ws: WebSocket):
    await ws.accept()
    print("[WS] 클라이언트 연결됨")

    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=30)
        data = json.loads(raw)

        issue           = data.get("issue", "").strip()
        duration        = int(data.get("duration", 15))
        debate_format   = data.get("debateFormat", "릴레이")
        conclusion_type = data.get("conclusionType", "VOTE")
        # ✅ 참여 의원 ID 목록 수신 (없으면 None → 엔진에서 전원 사용)
        active_members  = data.get("activeMembers", None)

        if not issue:
            await ws.send_json({"type": "error", "message": "안건이 없습니다."})
            return

        print(
            f"[WS] 안건: {issue[:40]}... / {duration}분 / {debate_format} / "
            f"{conclusion_type} / 의원: {active_members}"
        )

        engine = DebateEngine(
            issue, duration, ws,
            debate_format=debate_format,
            conclusion_type=conclusion_type,
            active_members=active_members,   # ✅ 추가
        )
        await engine.run()

    except WebSocketDisconnect:
        print("[WS] 클라이언트가 연결을 끊었습니다.")

    except asyncio.TimeoutError:
        print("[WS] 안건 수신 타임아웃")
        try:
            await ws.send_json({"type": "error", "message": "연결 시간이 초과되었습니다. 다시 시도해주세요."})
        except:
            pass

    except Exception as e:
        print(f"[WS] 예외 발생: {e}")
        traceback.print_exc()
        try:
            await ws.send_json({
                "type": "error",
                "message": f"서버 오류가 발생했습니다: {str(e)[:100]}"
            })
            await ws.send_json({"type": "done"})
        except:
            pass


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
