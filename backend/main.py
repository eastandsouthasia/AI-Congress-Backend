"""
AI 의회 백엔드 서버
FastAPI + WebSocket + 다중 AI 엔진 (Gemini / Groq / OpenRouter)
"""

import os, json, asyncio, traceback
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
    return {"status": "ok"}

# ─────────────────────────────────────────────
# WebSocket 엔드포인트
# 앱이 연결하면 토론 전 과정을 실시간 스트리밍
# ─────────────────────────────────────────────
@app.websocket("/debate")
async def debate_ws(ws: WebSocket):
    await ws.accept()
    print("[WS] 클라이언트 연결됨")

    try:
        # 앱에서 { "issue": "...", "duration": 40 } 수신
        raw = await ws.receive_text()
        data = json.loads(raw)
        issue    = data.get("issue", "")
        duration = int(data.get("duration", 40))

        if not issue:
            await ws.send_json({"type": "error", "message": "안건이 없습니다."})
            return

        # 토론 엔진 실행 (발언마다 ws로 실시간 전송)
        engine = DebateEngine(issue, duration, ws)
        await engine.run()

    except WebSocketDisconnect:
        print("[WS] 클라이언트 연결 끊김")
    except Exception as e:
        print(f"[WS] 오류: {e}")
        traceback.print_exc()
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except:
            pass


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
