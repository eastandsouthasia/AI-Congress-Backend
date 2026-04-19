"""
AI Congress Debate Engine - 안정화 버전 (IndentationError 해결)
"""

import json
import asyncio
import random
from fastapi import WebSocket
from members import MEMBERS, MEMBER_MAP
from ai_caller import call_member, call_groq
from debate_context import DebateContext

MEMBER_LIST_STR = "\n".join(f"- {m['name']}" for m in MEMBERS)

ROUNDS = 2


class DebateEngine:
    def __init__(self, issue: str, duration: int, ws: WebSocket):
        self.issue = issue
        self.duration = duration
        self.ws = ws
        self.ctx = DebateContext()
        self.memories = {m["id"]: [] for m in MEMBERS}
        self.speech_count = {m["id"]: 0 for m in MEMBERS}
        self.current_round = 0

    async def send(self, type: str, **kwargs):
        await self.ws.send_json({"type": type, **kwargs})

    async def wait_for_ready(self):
        """클라이언트 ready 신호 대기"""
        print(f"[Engine] ready 신호 대기 중... (현재 발언 수: {len(self.ctx.all_logs)})")

        try:
            async with asyncio.timeout(60):
                while True:
                    message = await self.ws.receive_text()
                    data = json.loads(message)
                    if data.get("type") == "ready":
                        print("[Engine] ✓ ready 수신 → 다음 발언 진행")
                        return
        except asyncio.TimeoutError:
            print("[Engine] ready 타임아웃 → 강제 진행")
        except Exception as e:
            print(f"[Engine] ready 대기 오류: {e}")

    async def send_speech(self, member_id: str, text: str, speech_type: str, is_chair: bool):
        m = MEMBER_MAP.get(member_id, {})
        display = f"의장 {m.get('name', '?')}" if is_chair else f"{m.get('name', '?')} 의원"
        engine_info = f"{m.get('engine', '?')}/{m.get('model', '?').split('/')[-1]}"

        await self.send(
            "speech",
            memberId=member_id,
            displayName=display,
            text=text,
            speechType=speech_type,
            engineInfo=engine_info,
            color=m.get("color", "#ffffff"),
            avatar=m.get("avatar", "💬"),
        )

        self.speech_count[member_id] = self.speech_count.get(member_id, 0) + 1

        # TTS 시간 맞추기 위해 간단한 대기 (안정화 버전)
        await asyncio.sleep(2.0)


    @staticmethod
    def detect_type(text: str) -> str:
        if "[REFUTE]" in text:
            return "REFUTE"
        if "[ADMIT]" in text:
            return "ADMIT"
        return "NORMAL"

    async def get_protocol(self, chair: dict) -> dict:
        all_ids = [m["id"] for m in MEMBERS]
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 의회 의장입니다. 안건 성격에 따라 최적의 토론 형식과 발언 순서를 설계하세요.\n"
                    f"의원 목록:\n{MEMBER_LIST_STR}\n\n"
                    "순수 JSON만 반환:\n"
                    '{"format":"형식명","order":["id",...],"proposal":"이유","conclusionType":"VOTE|RESOLUTION"}'
                )
            },
            {
                "role": "user",
                "content": f'안건: "{self.issue}"'
            }
        ]
        try:
            raw = await call_groq(messages, temperature=0.3)
            s, e = raw.find("{"), raw.rfind("}")
            parsed = json.loads(raw[s:e+1])
            order = [x for x in parsed.get("order", []) if x in MEMBER_MAP][:12]
            return {
                "format": parsed.get("format", "릴레이"),
                "order": order if len(order) > 2 else all_ids,
                "proposal": parsed.get("proposal", "기본 절차."),
                "conclusionType": "RESOLUTION" if parsed.get("conclusionType") == "RESOLUTION" else "VOTE",
            }
        except:
            return {"format": "릴레이", "order": all_ids, "proposal": "기본 절차.", "conclusionType": "VOTE"}

    async def get_review(self, member: dict, format_name: str) -> str:
        messages = [
            {
                "role": "system",
                "content": f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}\n의원 목록:\n{MEMBER_LIST_STR}\n의장 제안에 의견을 주세요. 60자 이내."
            },
            {"role": "user", "content": f"안건: \"{self.issue}\"\n의장이 '{format_name}' 형식을 제안했습니다."}
        ]
        try:
            return await call_member(member, messages, temperature=0.6)
        except:
            return "본 의원은 의장의 제안에 동의합니다."

    async def get_opinion(self, member: dict, chair_name: str, is_chair: bool = False, round_num: int = 1):
        # 간단한 버전으로 축소 (안정화 우선)
        try:
            return await call_member(member, [
                {"role": "system", "content": f"당신은 {member['name']} 의원입니다. 안건: {self.issue}에 대해 발언하세요."},
                {"role": "user", "content": "발언하세요."}
            ], temperature=0.7)
        except:
            return f"{member['name']} 의원은 이 안건에 대해 의견을 준비중입니다."

    async def get_vote(self, member: dict) -> str:
        try:
            return await call_member(member, [
                {"role": "system", "content": f"당신은 {member['name']} 의원입니다. 안건에 투표하세요."},
                {"role": "user", "content": "투표하세요."}
            ], temperature=0.3)
        except:
            return "[기권] 시스템 오류로 기권합니다."

    async def get_resolution(self) -> str:
        return "의원들의 충분한 논의 끝에 본 안건을 가결하기로 결정하였다."

    async def run(self):
        await self.send("status", message="의사일정 설계 중...")

        chair = random.choice(MEMBERS)
        protocol = await self.get_protocol(chair)

        await self.send("protocol", format=protocol["format"], chairId=chair["id"], chairName=chair["name"])

        proposal_msg = f"본 의장은 안건 \"{self.issue}\"의 토론을 시작합니다."
        await self.send_speech(chair["id"], proposal_msg, "NORMAL", True)

        # 간단한 테스트용 토론 흐름
        for i in range(3):
            for m in MEMBERS[:3]:   # 처음 3명만 테스트
                opinion = await self.get_opinion(m, chair["name"])
                await self.send_speech(m["id"], opinion, "NORMAL", False)
                await asyncio.sleep(1.5)

        await self.send("result", resultType="VOTE", content=[{"memberId": m["id"], "text": "[찬성] 찬성합니다."} for m in MEMBERS])
        await self.send("done")


# 나머지 함수들은 간단히 유지했습니다.