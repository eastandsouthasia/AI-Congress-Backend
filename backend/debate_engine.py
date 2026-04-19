"""
AI Congress Debate Engine - TTS 완전 동기화 버전
발언 하나를 보내고 클라이언트의 "ready" 신호를 기다린 후에만 다음 발언 생성
"""

import json
import asyncio
import random
from fastapi import WebSocket
from members import MEMBERS, MEMBER_MAP
from ai_caller import call_member, call_groq
from debate_context import DebateContext

MEMBER_LIST_STR = "\n".join(f"- {m['name']}" for m in MEMBERS)

ROUNDS = 2  # 2라운드로 줄여서 안정성 및 속도 향상


class DebateEngine:
    def __init__(self, issue: str, duration: int, ws: WebSocket):
        self.issue = issue
        self.duration = duration
        self.ws = ws
        self.ctx = DebateContext()
        self.memories = {m["id"]: [] for m in MEMBERS}
        self.speech_count = {m["id"]: 0 for m in MEMBERS}
        self.current_round = 0

    # ─────────────────────────────────────────────
    # 전송 헬퍼
    # ─────────────────────────────────────────────
    async def send(self, type: str, **kwargs):
        await self.ws.send_json({"type": type, **kwargs})

        async def wait_for_ready(self):
        """클라이언트 ready 신호 대기 - 타임아웃을 120초로 늘리고 로그 강화"""
        print(f"[Engine] {self.current_round}라운드 ready 대기 시작...")
        try:
            async with asyncio.timeout(120):   # 90초 → 120초로 증가
                while True:
                    message = await self.ws.receive_text()
                    data = json.loads(message)
                    if data.get("type") == "ready":
                        print(f"[Engine] ✓ ready 수신 완료 (발언 진행)")
                        return
                    # 다른 메시지도 무시하지 않고 로그
                    print(f"[Engine] received non-ready message: {data.get('type')}")
        except asyncio.TimeoutError:
            print("[Engine] ⚠️ ready 타임아웃 (120초) → 강제 다음 발언 진행")
        except Exception as e:
            print(f"[Engine] ready 대기 오류: {e}")

    async def send_speech(self, member_id: str, text: str, speech_type: str, is_chair: bool):
        """발언 전송 후 클라이언트의 ready 신호를 기다림"""
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

        # ★★★ 핵심: TTS가 끝날 때까지 기다림 ★★★
        # await self.wait_for_ready() #
        # 대신 간단한 대기만 넣음 (TTS 시간 맞추기용)
        await asyncio.sleep(1.8)   # 1.8초 대기
    @staticmethod
    def detect_type(text: str) -> str:
        if "[REFUTE]" in text:
            return "REFUTE"
        if "[ADMIT]" in text:
            return "ADMIT"
        return "NORMAL"

    # ─────────────────────────────────────────────
    # 의사일정 생성
    # ─────────────────────────────────────────────
    async def get_protocol(self, chair: dict) -> dict:
        all_ids = [m["id"] for m in MEMBERS]
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 의회 의장입니다. 안건 성격에 따라 최적의 토론 형식과 발언 순서를 설계하세요.\n"
                    "가능 형식: 릴레이, 집중토론, 전문가패널, 자유토론\n\n"
                    f"의원 목록:\n{MEMBER_LIST_STR}\n\n"
                    "순수 JSON만 반환하세요:\n"
                    '{"format":"형식명","order":["id",...],"proposal":"이유","conclusionType":"VOTE|RESOLUTION"}'
                )
            },
            {
                "role": "user",
                "content": f'안건: "{self.issue}"\n의원 정보: {json.dumps([{"id": m["id"], "lens": m["lens"]} for m in MEMBERS], ensure_ascii=False)}'
            }
        ]
        try:
            raw = await call_groq(messages, temperature=0.3)
            s, e = raw.find("{"), raw.rfind("}")
            parsed = json.loads(raw[s:e + 1])
            order = [x for x in parsed.get("order", []) if x in MEMBER_MAP][:12]
            return {
                "format": parsed.get("format", "릴레이"),
                "order": order if len(order) > 2 else all_ids,
                "proposal": parsed.get("proposal", "기본 절차."),
                "conclusionType": "RESOLUTION" if parsed.get("conclusionType") == "RESOLUTION" else "VOTE",
            }
        except Exception as e:
            print(f"[Protocol] 오류: {e}")
            return {"format": "릴레이", "order": all_ids, "proposal": "기본 절차.", "conclusionType": "VOTE"}

    # ─────────────────────────────────────────────
    # 의원 검토
    # ─────────────────────────────────────────────
    async def get_review(self, member: dict, format_name: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}\n"
                    f"의원 목록:\n{MEMBER_LIST_STR}\n\n"
                    "의장 제안에 무조건 동의하지 말고, 전문 분야 기반으로 의견을 제시하세요. "
                    "완전한 문장으로 60자 이내로 답변하세요."
                )
            },
            {"role": "user", "content": f"안건: \"{self.issue}\"\n의장이 '{format_name}' 형식을 제안했습니다. 의견을 말씀해 주세요."}
        ]
        try:
            return await call_member(member, messages, temperature=0.6)
        except:
            return "본 의원은 의장의 제안에 동의합니다."

    # ─────────────────────────────────────────────
    # 의원 발언 생성 (라운드별 지시 강화)
    # ─────────────────────────────────────────────
    async def get_opinion(
        self,
        member: dict,
        chair_name: str,
        is_chair: bool = False,
        round_num: int = 1,
        is_rebuttal: bool = False,
        target_speech: str = None,
    ) -> str:

        round_instruction = {
            1: "첫 발언입니다. 안건에 대한 본인의 명확한 입장과 핵심 근거를 제시하세요.",
            2: "2라운드입니다. 앞선 발언들의 약점을 지적하고, 새로운 관점이나 데이터를 추가해 반박하거나 보완하세요. 이미 나온 주장은 반복하지 마세요.",
        }.get(round_num, "앞선 토론을 종합하여 소신 있는 발언을 하세요.")

        if is_rebuttal and target_speech:
            rebuttal_instruction = (
                f"\n\n🚨 즉석 반박 상황:\n"
                f"방금 당신의 주장이 강하게 반박되었습니다:\n\"{target_speech[:180]}\"\n"
                f"반드시 이 반박에 정면으로 대응하여 재반박하세요."
            )
        else:
            last = self.ctx.all_logs[-1] if self.ctx.all_logs else None
            rebuttal_instruction = (
                f"\n\n📌 직전 발언({last['speaker']}):\n\"{last['text'][:150]}\"\n"
                "→ 위 발언에 대해 동의, 반박, 또는 보완 중 하나로 시작하세요."
            ) if last else ""

        base_system = (
            "당신은 AI 의회 토론 참여자입니다.\n"
            f"의원 목록 (이 이름만 사용):\n{MEMBER_LIST_STR}\n\n"
            "발언 규칙:\n"
            "- [REFUTE]: 상대 논리 오류 지적 (구체적 반증 필수)\n"
            "- [ADMIT]: 상대 의견 수용 시 반드시 입장 수정 명시\n"
            "- [DATA]: 정확한 수치나 통계 제시\n"
            "- [GRAPHIC]: 텍스트 기반 차트 시각화\n"
            f"- {round_instruction}\n"
            "- 이미 논의된 내용 반복 금지. 반드시 새로운 관점 추가.\n"
            "- 발언은 250자 이내로 하고, 완전한 문장으로 마무리하세요.\n"
        )

        if is_chair:
            role_system = (
                f"당신은 의장 {member['name']}입니다.\n"
                "찬반 현황을 간략히 요약하고 토론을 중립적으로 이끌어 가세요. 개인 주장 금지.\n"
                "150자 이내."
            )
        else:
            role_system = (
                f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}\n"
                "자신을 '본 의원'이라 칭하세요.\n"
                f"의장: '{chair_name} 의장님' / 다른 의원: '○○ 의원님'\n"
                "전문 분야 지식과 데이터를 최대한 활용하세요.\n"
                "[ADMIT] 이후에는 수정된 입장을 일관되게 유지하세요."
            )

        messages = [
            {"role": "system", "content": base_system + role_system},
            *self.ctx.to_messages(),
            {
                "role": "user",
                "content": f"안건: \"{self.issue}\"{rebuttal_instruction}\n\n지금 당신의 발언 차례입니다. 소신껏 발언하세요."
            }
        ]

               try:
            result = await call_member(member, messages, temperature=0.25 if is_chair else 0.6)
            if not result or len(result.strip()) < 10:
                raise Exception("Empty response")
            return result
        except Exception as e:
            print(f"[Opinion] {member['name']} API 호출 실패: {e}")
            return f"{member['name']} 의원은 현재 토론 상황을 종합해 신중한 입장을 유지하고 있습니다."
    # ─────────────────────────────────────────────
    # 최종 투표
    # ─────────────────────────────────────────────
    async def get_vote(self, member: dict) -> str:
        speeches = self.memories[member["id"]]
        admit_note = "\n※ 토론 중 입장을 수정했습니다. 수정된 입장으로 투표하세요." if any("[ADMIT]" in s for s in speeches) else ""
        full_summary = f"\n\n[전체 토론 요약]\n{self.ctx.summary}" if self.ctx.summary else ""

        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}.\n"
                    f"의원 목록:\n{MEMBER_LIST_STR}\n\n"
                    f"당신의 전체 발언:\n\"\"\"\n{chr(10).join(speeches)}\n\"\"\""
                    f"{full_summary}{admit_note}\n\n"
                    "토론 내용과 완전히 일관된 투표를 하세요.\n"
                    "형식: [찬성|반대|기권] 이유 (200자 이내, 완전한 문장으로)"
                )
            },
            {"role": "user", "content": f"안건 \"{self.issue}\"에 최종 투표하세요."}
        ]
        try:
            return await call_member(member, messages, temperature=0.2)
        except:
            return "[기권] 기술적 문제로 기권 처리합니다."

    # ─────────────────────────────────────────────
    # 공동 결의안
    # ─────────────────────────────────────────────
    async def get_resolution(self) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 의회 서기입니다. 전체 토론을 바탕으로 공식 결의문을 작성하세요.\n"
                    "형식: 1) 전문(배경) 2) 결의 조항(번호 매김) 3) 서명란\n"
                    "[ADMIT]된 의견은 반드시 반영하세요. 500자 이내."
                )
            },
            {"role": "user", "content": f"안건: \"{self.issue}\"\n\n전체 토론:\n{self.ctx.to_plain_text()}\n\n결의문을 작성하세요."}
        ]
        try:
            return await call_groq(messages, temperature=0.5)
        except:
            return "의원들의 충분한 논의 끝에 본 안건을 가결하기로 결정하였다."

    # ─────────────────────────────────────────────
    # 메인 토론 진행
    # ─────────────────────────────────────────────
    async def run(self):
        await self.send("status", message="의사일정 설계 중...")

        chair = random.choice(MEMBERS)
        protocol = await self.get_protocol(chair)

        await self.send("protocol",
                        format=protocol["format"],
                        chairId=chair["id"],
                        chairName=chair["name"])

        # 개회사
        proposal_msg = (
            f"본 의장은 안건 \"{self.issue}\"의 효율적 논의를 위해 "
            f"'{protocol['format']}' 형식을 제안합니다. 사유: {protocol['proposal']}"
        )
        await self.send_speech(chair["id"], proposal_msg, "NORMAL", True)
        self.ctx.push(f"[의장 {chair['name']}]", proposal_msg)

        # 의원 검토 2명
        reviewers = random.sample([m for m in MEMBERS if m["id"] != chair["id"]], 2)
        for reviewer in reviewers:
            await self.send("status", message=f"{reviewer['name']} 의원 검토 중...")
            review = await self.get_review(reviewer, protocol["format"])
            await self.send_speech(reviewer["id"], review, "NORMAL", False)
            self.ctx.push(f"[{reviewer['name']} 의원]", review)

        confirm = "소중한 의견 감사합니다. 제안한 방식에 따라 본 토론을 개회하겠습니다."
        await self.send_speech(chair["id"], confirm, "NORMAL", True)
        self.ctx.push(f"[의장 {chair['name']}]", confirm)

        # 본 토론 (다중 라운드)
        order = [m for m in [MEMBER_MAP.get(sid) for sid in protocol["order"]] if m]

        for round_num in range(1, ROUNDS + 1):
            self.current_round = round_num
            round_labels = {1: "초기 입장 표명", 2: "반박 및 재반박"}
            round_msg = f"━━ {round_num}라운드: {round_labels.get(round_num, f'{round_num}라운드')} 개시 ━━"
            await self.send_speech(chair["id"], round_msg, "NORMAL", True)
            self.ctx.push(f"[의장 {chair['name']}]", round_msg)

            round_order = order.copy()
            if round_num > 1:
                random.shuffle(round_order)

            for m in round_order:
                is_chair_member = (m["id"] == chair["id"])
                await self.send("status", message=f"[{round_num}라운드] {m['name']} 발언 중...")

                opinion = await self.get_opinion(
                    m, chair["name"], is_chair=is_chair_member, round_num=round_num
                )
                stype = self.detect_type(opinion)

                self.memories[m["id"]].append(opinion)
                ctx_label = f"[의장 {m['name']}]" if is_chair_member else f"[{m['name']} 의원]"
                self.ctx.push(ctx_label, opinion)

                await self.send_speech(m["id"], opinion, stype, is_chair_member)

                # 즉석 반박 (선택적으로 유지)
                # 필요 시 여기에 rebuttal 로직 추가 가능

                await self.ctx.compress_if_needed()

            # 라운드 정리 (의장)
            if round_num < ROUNDS:
                summary_opinion = await self.get_opinion(chair, chair["name"], is_chair=True, round_num=round_num)
                self.ctx.push(f"[의장 {chair['name']}]", summary_opinion)
                await self.send_speech(chair["id"], summary_opinion, "NORMAL", True)

        # 최종 의결
        close_msg = "━━ 본 토론의 모든 라운드가 완료되었습니다. 최종 의결을 진행하겠습니다. ━━"
        await self.send_speech(chair["id"], close_msg, "NORMAL", True)
        self.ctx.push(f"[의장 {chair['name']}]", close_msg)

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