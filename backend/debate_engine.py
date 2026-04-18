"""
토론 엔진 (DebateEngine)

전체 토론 진행:
1. 의장 선출 + 의사일정 설계
2. 의원 검토
3. 본 토론 (각 의원이 실제 자기 API로 발언)
4. 최종 의결 (투표 or 결의안)

각 발언마다 WebSocket으로 앱에 실시간 전송
"""

import json, asyncio, random
from fastapi import WebSocket
from members import MEMBERS, MEMBER_MAP
from ai_caller import call_member, call_groq
from debate_context import DebateContext

MEMBER_LIST_STR = "\n".join(f"- {m['name']}" for m in MEMBERS)

class DebateEngine:
    def __init__(self, issue: str, duration: int, ws: WebSocket):
        self.issue    = issue
        self.duration = duration
        self.ws       = ws
        self.ctx      = DebateContext()
        self.memories = {m["id"]: [] for m in MEMBERS}

    # ─── WebSocket 전송 헬퍼 ───
    async def send(self, type: str, **kwargs):
        await self.ws.send_json({"type": type, **kwargs})

    async def send_speech(self, member_id: str, text: str, speech_type: str, is_chair: bool):
        m = MEMBER_MAP.get(member_id, {})
        display = f"의장 {m.get('name','?')}" if is_chair else f"{m.get('name','?')} 의원"
        engine_info = f"{m.get('engine','?')}/{m.get('model','?').split('/')[-1]}"
        await self.send(
            "speech",
            memberId    = member_id,
            displayName = display,
            text        = text,
            speechType  = speech_type,
            engineInfo  = engine_info,
            color       = m.get("color", "#ffffff"),
            avatar      = m.get("avatar", "💬"),
        )

    # ─── 발언 타입 감지 ───
    @staticmethod
    def detect_type(text: str) -> str:
        if "[REFUTE]" in text: return "REFUTE"
        if "[ADMIT]"  in text: return "ADMIT"
        return "NORMAL"

    # ─── 의사일정 설계 ───
    async def get_protocol(self, chair: dict) -> dict:
        all_ids = [m["id"] for m in MEMBERS]
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 의회 의장입니다. 안건 성격에 따라 최적 토론 형식과 발언 순서를 설계하세요.\n\n"
                    "형식:\n"
                    "릴레이: 전원 1회 발언 후 핵심 논쟁자 추가 발언\n"
                    "집중토론: 핵심 2명만 번갈아 4~6회 공방\n"
                    "전문가패널: 전문가 3명 먼저 → 나머지 보충\n"
                    "자유토론: order 중간에 의장 id 삽입\n\n"
                    f"의원 목록:\n{MEMBER_LIST_STR}\n\n"
                    "순수 JSON만 반환:\n"
                    '{"format":"릴레이|집중토론|전문가패널|자유토론",'
                    '"order":["id",...],'
                    '"proposal":"이유",'
                    '"conclusionType":"VOTE|RESOLUTION"}'
                )
            },
            {
                "role": "user",
                "content": (
                    f'안건: "{self.issue}"\n'
                    f'의원 정보: {json.dumps([{"id":m["id"],"lens":m["lens"]} for m in MEMBERS], ensure_ascii=False)}\n'
                    "최적 형식을 선택하고 order를 구성하세요."
                )
            }
        ]
        try:
            raw = await call_groq(messages, temperature=0.3)
            s, e = raw.find("{"), raw.rfind("}")
            if s == -1 or e == -1: raise ValueError("JSON 없음")
            parsed = json.loads(raw[s:e+1])
            order = [x for x in parsed.get("order", []) if x in MEMBER_MAP][:16]
            return {
                "format":          parsed.get("format", "릴레이"),
                "order":           order if len(order) > 2 else all_ids,
                "proposal":        parsed.get("proposal", "기본 절차에 따라 진행합니다."),
                "conclusionType":  "RESOLUTION" if parsed.get("conclusionType") == "RESOLUTION" else "VOTE",
            }
        except Exception as ex:
            print(f"의사일정 실패: {ex}")
            return {"format":"릴레이","order":all_ids,"proposal":"기본 절차.","conclusionType":"VOTE"}

    # ─── 의원 검토 발언 ───
    async def get_review(self, member: dict, format_name: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}\n"
                    f"의원 목록 (이 이름만 사용):\n{MEMBER_LIST_STR}\n\n"
                    "의장 제안에 무조건 동의하지 마세요. 전문 분야 기반으로 의견을 내세요.\n"
                    "자신을 '본 의원'이라 칭하고, 완전한 문장으로 60자 이내 답변하세요."
                )
            },
            {
                "role": "user",
                "content": f"안건: \"{self.issue}\"\n의장이 '{format_name}' 형식을 제안했습니다. 의견을 말씀해 주세요."
            }
        ]
        return await call_member(member, messages, temperature=0.6)

    # ─── 의원 발언 생성 ───
    async def get_opinion(self, member: dict, chair_name: str, is_chair: bool = False) -> str:
        # 직전 발언 추출
        last = self.ctx.all_logs[-1] if self.ctx.all_logs else None
        last_hint = (
            f"\n\n📌 직전 발언 ({last['speaker']}):\n"
            f"\"{last['text'][:120]}\"\n"
            "→ 위 발언에 동의·반박·보완 중 하나로 발언을 시작하세요."
        ) if last else ""

        base_system = (
            "당신은 AI 의회 토론 참여자입니다.\n"
            f"의원 목록 (이 이름만 사용):\n{MEMBER_LIST_STR}\n\n"
            "발언 태그:\n"
            "[REFUTE]: 상대 논리·데이터 오류 지적\n"
            "[ADMIT]: 상대가 더 타당 → 반드시 구체적 입장 수정 포함\n"
            "[DATA]: 수치·통계. 예: [DATA] 2023년 AI 시장 1,500억 달러\n"
            "[GRAPHIC]: 텍스트 시각화\n"
            "  [GRAPHIC]\n  찬성 ████████░░ 78%\n  반대 ██░░░░░░░░ 22%\n\n"
            "필수:\n"
            "- 이전 토론 내용을 충분히 숙지하고 발언하세요.\n"
            "- 이미 논의된 내용은 반복 금지. 새 관점·데이터만 추가하세요.\n"
            "- 반드시 마침표·느낌표·물음표로 문장을 완전히 끝내세요.\n"
            "- 200자 이내로 발언하세요.\n"
        )

        if is_chair:
            role_system = (
                f"당신은 의장 {member['name']}입니다.\n"
                "자신을 '의장' 또는 '본 의장'이라 칭하세요.\n"
                "현재 찬반 입장을 간략히 요약하고 토론을 이어가세요. 개인 주장 금지.\n"
                "100자 이내. 반드시 완전한 문장으로 끝내세요."
            )
        else:
            role_system = (
                f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}\n"
                "자신을 '본 의원'이라 하세요.\n"
                f"의장: '{chair_name} 의장님' / 다른 의원: '○○ 의원님'\n"
                "전문 분야의 모든 지식과 데이터를 최대한 활용하세요.\n"
                "[ADMIT] 이후 수정된 입장을 이후에도 일관되게 유지하세요.\n"
                "반드시 완전한 문장으로 끝내세요."
            )

        messages = [
            {"role": "system", "content": base_system + role_system},
            *self.ctx.to_messages(),
            {
                "role": "user",
                "content": (
                    f"안건: \"{self.issue}\"{last_hint}\n\n"
                    "지금 당신의 발언 차례입니다. "
                    "앞선 토론 전체를 숙지한 상태에서 소신있게 발언하세요."
                )
            }
        ]

        return await call_member(member, messages, temperature=0.25 if is_chair else 0.5)

    # ─── 최종 투표 ───
    async def get_vote(self, member: dict) -> str:
        speeches = self.memories[member["id"]]
        admit_note = (
            "\n※ 당신은 토론 중 일부 입장을 수정했습니다. 수정된 입장으로 투표하세요."
            if any("[ADMIT]" in s for s in speeches) else ""
        )
        full_summary = f"\n\n[전체 토론 요약]\n{self.ctx.summary}" if self.ctx.summary else ""

        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}.\n"
                    f"의원 목록:\n{MEMBER_LIST_STR}\n\n"
                    f"당신의 토론 발언:\n\"\"\"\n{chr(10).join(speeches)}\n\"\"\""
                    f"{full_summary}{admit_note}\n\n"
                    "규칙: 위 발언과 논리적으로 일관된 투표를 하세요.\n"
                    "형식: [찬성|반대|기권] 이유 (200자 이내, 완전한 문장으로)"
                )
            },
            {"role": "user", "content": f"안건 \"{self.issue}\"에 최종 투표하세요."}
        ]
        return await call_member(member, messages, temperature=0.2)

    # ─── 결의안 생성 ───
    async def get_resolution(self) -> str:
        full = self.ctx.to_plain_text()
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 의회 서기입니다. 전체 토론을 바탕으로 공식 결의문을 작성하세요.\n"
                    "형식: 1)전문(배경과 논의 경과) 2)결의 조항(번호 매기기) 3)서명란\n"
                    "[ADMIT]로 수용된 의견을 반드시 반영하세요. 500자 이내. 완전한 문장으로 끝내세요."
                )
            },
            {
                "role": "user",
                "content": f"안건: \"{self.issue}\"\n\n전체 토론:\n{full}\n\n결의문을 작성하세요."
            }
        ]
        return await call_groq(messages, temperature=0.5)

    # ─────────────────────────────────────────────
    # 토론 메인 실행
    # ─────────────────────────────────────────────
    async def run(self):
        await self.send("status", message="의사일정 설계 중...")

        # 의장 선출
        chair = random.choice(MEMBERS)
        protocol = await self.get_protocol(chair)

        await self.send("protocol", format=protocol["format"], chairId=chair["id"], chairName=chair["name"])

        # ── 개회사 ──
        proposal_msg = (
            f"본 의장은 안건 \"{self.issue}\"의 효율적 논의를 위해 "
            f"'{protocol['format']}' 형식을 제안합니다. "
            f"사유: {protocol['proposal']}"
        )
        await self.send_speech(chair["id"], proposal_msg, "NORMAL", True)
        self.ctx.push(f"[의장 {chair['name']}]", proposal_msg)

        # ── 의원 검토 2명 ──
        reviewers = random.sample([m for m in MEMBERS if m["id"] != chair["id"]], 2)
        for reviewer in reviewers:
            await self.send("status", message=f"{reviewer['name']} 의원 검토 중...")
            review = await self.get_review(reviewer, protocol["format"])
            await self.send_speech(reviewer["id"], review, "NORMAL", False)
            self.ctx.push(f"[{reviewer['name']} 의원]", review)

        confirm = "소중한 의견 감사합니다. 제안한 방식에 따라 본 토론을 개회하겠습니다."
        await self.send_speech(chair["id"], confirm, "NORMAL", True)
        self.ctx.push(f"[의장 {chair['name']}]", confirm)

        # ── 본 토론 ──
        for speaker_id in protocol["order"]:
            m = MEMBER_MAP.get(speaker_id)
            if not m:
                continue

            is_chair = (m["id"] == chair["id"])
            label = f"의장 {m['name']}" if is_chair else f"{m['name']} 의원"
            await self.send("status", message=f"{label} 발언 중... ({m['engine']})")

            opinion = await self.get_opinion(m, chair["name"], is_chair)
            stype   = self.detect_type(opinion)

            self.memories[m["id"]].append(opinion)
            ctx_label = f"[의장 {m['name']}]" if is_chair else f"[{m['name']} 의원]"
            self.ctx.push(ctx_label, opinion)

            await self.send_speech(m["id"], opinion, stype, is_chair)

            # 발언 누적 시 자동 압축
            await self.ctx.compress_if_needed()

            await asyncio.sleep(0.3)  # 과부하 방지

        # ── 의결 ──
        await self.send("status", message="최종 의결 진행 중...")

        if protocol["conclusionType"] == "RESOLUTION":
            resolution = await self.get_resolution()
            await self.send("result", resultType="RESOLUTION", content=resolution)
        else:
            votes = []
            for m in MEMBERS:
                await self.send("status", message=f"{m['name']} 의원 최종 투표 중...")
                vote = await self.get_vote(m)
                votes.append({"memberId": m["id"], "text": vote})
            await self.send("result", resultType="VOTE", content=votes)

        await self.send("status", message="✅ 토론 종료")
        await self.send("done")
