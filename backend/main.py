"""
main.py — AI Congress FastAPI 서버 진입점

WebSocket 엔드포인트:
  ws://<host>/debate

프론트엔드(DebateScreen.js)가 연결 즉시 전송하는 JSON:
  {
    "issue":          str,   # 토론 안건
    "duration":       int,   # 토론 시간(분)
    "debateFormat":   str,   # "릴레이" | "집중토론" | "전문가패널" | "자유토론"
    "conclusionType": str,   # "VOTE" | "RESOLUTION"
    "activeMembers":  list,  # 참여 의원 ID 배열 (없으면 전원 참여)
  }

버그 수정:
  [BUG-MAIN-1] "name 'duration' is not defined"
    → receive_text()+json.loads()로 수신 후 모든 키를 명시적으로 추출.
      누락/타입오류 시 기본값으로 폴백. KeyError·TypeError 원천 차단.
  [BUG-MAIN-2] debateFormat/conclusionType camelCase 키 불일치
    → 프론트 camelCase 그대로 수신 후 snake_case 변수에 명시 매핑.
  [BUG-MAIN-3] WS 연결 후 첫 메시지 수신 타임아웃 미처리
    → asyncio.wait_for(timeout=30)로 감싸 30초 초과 시 오류 메시지 전송.
  [BUG-MAIN-4] DebateEngine.run() 예외 미처리 시 클라이언트에 알림 없음
    → except에서 ws.send_json({"type":"error",...}) + {"type":"done"} 전송.
  [BUG-MAIN-5] 파라미터 파싱 오류가 engine.run()의 except에 혼입됨
    → 파싱 try/except를 engine.run() try/except와 분리하여 오류 원인 명확히 구분.
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

    # ── [BUG-MAIN-1·2·3] 초기 메시지 수신 및 파싱 (파싱 오류를 run()과 분리) ──
    try:
        raw  = await asyncio.wait_for(ws.receive_text(), timeout=30)
        data = json.loads(raw)

        # camelCase → snake_case 명시 매핑 + 기본값 폴백 (KeyError·TypeError 불가)
        issue           = str(data.get("issue", "")).strip()
        duration        = int(data.get("duration", 15))
        debate_format   = str(data.get("debateFormat",   "릴레이"))
        conclusion_type = str(data.get("conclusionType", "VOTE"))
        # None이면 엔진에서 전원 참여로 처리
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

    # duration 범위 보정 (1~120분)
    duration = max(1, min(120, duration))

    print(
        f"[WS] 안건: {issue[:40]} / {duration}분 / {debate_format} / "
        f"{conclusion_type} / 의원: {active_members}"
    )

    # ── [BUG-MAIN-4] DebateEngine 실행 — 예외 시 클라이언트에 알림 ──────────
    engine = DebateEngine(
        issue           = issue,
        duration        = duration,
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
            pass  # WS가 이미 닫혔을 수 있음


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")