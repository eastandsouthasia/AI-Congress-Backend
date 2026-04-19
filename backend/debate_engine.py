"""
토론 엔진 v2 - 진짜 토론 구조

핵심 변경:
1. 다중 라운드제: 각 의원이 여러 번 발언
2. 즉석 반박권: 강한 반박이 나오면 피반박자가 즉시 재반박
3. 의장 중간 개입: 각 라운드 후 찬반 현황 정리
4. 라운드별 온도 상승: 토론이 진행될수록 더 날카로운 논박 유도
5. 발언 간 대기: TTS와 속도 맞추기 위해 서버측 딜레이 조정
"""

import json, asyncio, random
from fastapi import WebSocket
from members import MEMBERS, MEMBER_MAP
from ai_caller import call_member, call_groq
from debate_context import DebateContext

MEMBER_LIST_STR = "\n".join(f"- {m['name']}" for m in MEMBERS)

# 라운드 설정
ROUNDS = 3           # 본 토론 라운드 수
MIN_SPEECHES = 2     # 의원당 최소 발언 수
REBUTTAL_THRESHOLD = "[REFUTE]"  # 즉석 반박 트리거

class DebateEngine:
    def __init__(self, issue: str, duration: int, ws: WebSocket):
        self.issue    = issue
        self.duration = duration
        self.ws       = ws
        self.ctx      = DebateContext()
        self.memories = {m["id"]: [] for m in MEMBERS}
        # 의원별 발언 횟수 추적
        self.speech_count = {m["id"]: 0 for m in MEMBERS}
        # 현재 라운드
        self.current_round = 0

    # ─── 전송 헬퍼 ───
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
        self.speech_count[member_id] = self.speech_count.get(member_id, 0) + 1

    @staticmethod
    def detect_type(text: str) -> str:
        if "[REFUTE]" in text: return "REFUTE"
        if "[ADMIT]"  in text: return "ADMIT"
        return "NORMAL"

    # ─── 의사일정 생성 ───
    async def get_protocol(self, chair: dict) -> dict:
        all_ids = [m["id"] for m in MEMBERS]
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 의회 의장입니다. 안건 성격에 따라 토론 형식과 1라운드 발언 순서를 설계하세요.\n"
                    "형식: 릴레이(전원발언), 집중토론(핵심2명), 전문가패널(전문가3명→전체)\n\n"
                    f"의원 목록:\n{MEMBER_LIST_STR}\n\n"
                    "순수 JSON만 반환:\n"
                    '{"format":"형식명","order":["id",...],'
                    '"proposal":"이유","conclusionType":"VOTE|RESOLUTION"}'
                )
            },
            {
                "role": "user",
                "content": (
                    f'안건: "{self.issue}"\n'
                    f'의원: {json.dumps([{"id":m["id"],"lens":m["lens"]} for m in MEMBERS], ensure_ascii=False)}'
                )
            }
        ]
        try:
            raw = await call_groq(messages, temperature=0.3)
            s, e = raw.find("{"), raw.rfind("}")
            parsed = json.loads(raw[s:e+1])
            order = [x for x in parsed.get("order", []) if x in MEMBER_MAP][:12]
            return {
                "format":         parsed.get("format", "릴레이"),
                "order":          order if len(order) > 2 else all_ids,
                "proposal":       parsed.get("proposal", "기본 절차."),
                "conclusionType": "RESOLUTION" if parsed.get("conclusionType") == "RESOLUTION" else "VOTE",
            }
        except:
            return {"format":"릴레이","order":all_ids,"proposal":"기본 절차.","conclusionType":"VOTE"}

    # ─── 의원 검토 ───
    async def get_review(self, member: dict, format_name: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}\n"
                    f"의원 목록:\n{MEMBER_LIST_STR}\n\n"
                    "의장 제안에 무조건 동의하지 마세요. 완전한 문장으로 60자 이내."
                )
            },
            {"role": "user", "content": f"안건: \"{self.issue}\"\n의장이 '{format_name}' 형식 제안. 의견은?"}
        ]
        return await call_member(member, messages, temperature=0.6)

    # ─── 핵심: 라운드별 의원 발언 ───
    # round_num: 현재 라운드 (1부터 시작)
    # is_rebuttal: 즉석 반박 여부
    # target_speech: 반박 대상 발언 (즉석 반박 시)
    async def get_opinion(
        self,
        member: dict,
        chair_name: str,
        is_chair: bool = False,
        round_num: int = 1,
        is_rebuttal: bool = False,
        target_speech: str = None,
    ) -> str:

        # 라운드가 높을수록 더 날카로운 논박 요구
        round_instruction = {
            1: "첫 발언입니다. 안건에 대한 본인의 입장과 핵심 근거를 명확히 밝히세요.",
            2: "2라운드입니다. 앞선 발언들의 약점을 구체적으로 지적하고 반박하세요. 새로운 데이터를 제시하세요.",
            3: "최종 라운드입니다. 전체 토론을 검토하고 본인의 최종 입장을 강력하게 천명하세요.",
        }.get(round_num, "앞선 발언을 검토하고 소신있게 발언하세요.")

        if is_rebuttal and target_speech:
            rebuttal_instruction = (
                f"\n\n🚨 즉석 반박 상황:\n"
                f"방금 당신의 주장이 강하게 반박당했습니다:\n"
                f'"{target_speech[:150]}"\n'
                f"반드시 이 반박에 정면으로 맞서 재반박하세요. 물러서지 마세요."
            )
        else:
            last = self.ctx.all_logs[-1] if self.ctx.all_logs else None
            rebuttal_instruction = (
                f"\n\n📌 직전 발언({last['speaker']}):\n\"{last['text'][:120]}\"\n"
                "→ 위 발언에 동의·반박·보완 중 하나로 발언을 시작하세요."
            ) if last else ""

        base_system = (
            "당신은 AI 의회 토론 참여자입니다.\n"
            f"의원 목록 (이 이름만 사용):\n{MEMBER_LIST_STR}\n\n"
            "발언 태그:\n"
            "[REFUTE]: 상대 논리·데이터 오류 지적 — 구체적 반증 필수\n"
            "[ADMIT]: 상대가 더 타당 → 반드시 본인 입장 수정 명시\n"
            "[DATA]: 수치·통계. 예: [DATA] OECD 2023년 보고서 기준 15% 감소\n"
            "[GRAPHIC]: 텍스트 시각화\n"
            "  [GRAPHIC]\n  찬성 ████████░░ 78%\n  반대 ██░░░░░░░░ 22%\n\n"
            "필수 규칙:\n"
            f"- {round_instruction}\n"
            "- 이미 나온 주장 반복 금지. 반드시 새 관점·새 데이터 추가.\n"
            "- 반드시 마침표·느낌표·물음표로 완전히 끝내세요.\n"
            "- 250자 이내.\n"
        )

        if is_chair:
            role_system = (
                f"당신은 의장 {member['name']}입니다.\n"
                "찬반 현황을 파악해 간략히 요약하고 토론을 이어가세요. 개인 주장 금지.\n"
                "150자 이내. 완전한 문장으로."
            )
        else:
            role_system = (
                f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}\n"
                "자신을 '본 의원'이라 하세요.\n"
                f"의장: '{chair_name} 의장님' / 다른 의원: '○○ 의원님'\n"
                "전문 분야의 모든 지식·데이터를 최대한 활용하세요.\n"
                "[ADMIT] 후에는 수정된 입장을 일관되게 유지하세요.\n"
                "완전한 문장으로 끝내세요."
            )

        messages = [
            {"role": "system", "content": base_system + role_system},
            *self.ctx.to_messages(),
            {
                "role": "user",
                "content": f"안건: \"{self.issue}\"{rebuttal_instruction}\n\n발언하세요.",
            }
        ]

        result = await call_member(member, messages, temperature=0.25 if is_chair else 0.6)
        return result or f"{member['name']} 의원은 이 안건을 신중히 검토해야 한다고 봅니다."

    # ─── 최종 투표 ───
    async def get_vote(self, member: dict) -> str:
        speeches = self.memories[member["id"]]
        admit_note = (
            "\n※ 토론 중 일부 입장을 수정했습니다. 수정된 입장으로 투표하세요."
            if any("[ADMIT]" in s for s in speeches) else ""
        )
        full_summary = f"\n\n[전체 토론 요약]\n{self.ctx.summary}" if self.ctx.summary else ""

        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 {member['name']} 의원입니다. 전문 분야: {member['lens']}.\n"
                    f"의원 목록:\n{MEMBER_LIST_STR}\n\n"
                    f"당신의 전체 토론 발언:\n\"\"\"\n{chr(10).join(speeches)}\n\"\"\""
                    f"{full_summary}{admit_note}\n\n"
                    "규칙: 위 발언과 논리적으로 완전히 일관된 투표를 하세요.\n"
                    "형식: [찬성|반대|기권] 이유 (200자 이내, 완전한 문장으로)"
                )
            },
            {"role": "user", "content": f"안건 \"{self.issue}\"에 최종 투표하세요."}
        ]
        return await call_member(member, messages, temperature=0.2)

    # ─── 결의안 ───
    async def get_resolution(self) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 의회 서기입니다. 전체 토론을 바탕으로 공식 결의문을 작성하세요.\n"
                    "형식: 1)전문 2)결의 조항(번호) 3)서명란\n"
                    "[ADMIT]로 수용된 의견을 반드시 반영하세요. 500자 이내. 완전한 문장."
                )
            },
            {"role": "user", "content": f"안건: \"{self.issue}\"\n\n전체 토론:\n{self.ctx.to_plain_text()}\n\n결의문을 작성하세요."}
        ]
        return await call_groq(messages, temperature=0.5)

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
            chairName=chair["name"]
        )

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
            await asyncio.sleep(0.5)

        confirm = "소중한 의견 감사합니다. 제안한 방식에 따라 본 토론을 개회하겠습니다."
        await self.send_speech(chair["id"], confirm, "NORMAL", True)
        self.ctx.push(f"[의장 {chair['name']}]", confirm)

        # ══════════════════════════════════════════
        # 본 토론: 다중 라운드
        # ══════════════════════════════════════════
        order = [m for m in [MEMBER_MAP.get(sid) for sid in protocol["order"]] if m]

        for round_num in range(1, ROUNDS + 1):
            self.current_round = round_num

            # 의장이 라운드 시작 선언
            round_labels = {1: "1라운드: 초기 입장 표명", 2: "2라운드: 반박 및 재반박", 3: "3라운드: 최종 입장 확정"}
            round_msg = f"━━ {round_labels.get(round_num, f'{round_num}라운드')} 개시 ━━"
            await self.send_speech(chair["id"], round_msg, "NORMAL", True)
            self.ctx.push(f"[의장 {chair['name']}]", round_msg)

            # 이번 라운드 발언 순서 (라운드마다 순서 조금 섞기)
            round_order = order.copy()
            if round_num > 1:
                # 2라운드부터는 앞 토론에서 많이 반박당한 의원을 앞으로
                random.shuffle(round_order)

            prev_speaker_id = None  # 즉석 반박 추적용

            for m in round_order:
                is_chair_member = (m["id"] == chair["id"])
                label = f"의장 {m['name']}" if is_chair_member else f"{m['name']} 의원"
                await self.send("status", message=f"[{round_num}라운드] {label} 발언 중... ({m['engine']})")

                opinion = await self.get_opinion(
                    m,
                    chair["name"],
                    is_chair=is_chair_member,
                    round_num=round_num,
                )
                stype = self.detect_type(opinion)

                self.memories[m["id"]].append(opinion)
                ctx_label = f"[의장 {m['name']}]" if is_chair_member else f"[{m['name']} 의원]"
                self.ctx.push(ctx_label, opinion)
                await self.send_speech(m["id"], opinion, stype, is_chair_member)

                # ── 즉석 반박권 ──
                # 강한 반박([REFUTE])이 나왔고, 이전 발언자가 있으면
                # 피반박자에게 즉시 재반박 기회 부여
                if stype == "REFUTE" and prev_speaker_id and prev_speaker_id != m["id"]:
                    prev_member = MEMBER_MAP.get(prev_speaker_id)
                    if prev_member and round_num < ROUNDS:  # 마지막 라운드는 즉석 반박 없음
                        await self.send("status",
                            message=f"⚡ {prev_member['name']} 의원 즉석 반박권 행사!")
                        await asyncio.sleep(0.3)

                        rebuttal = await self.get_opinion(
                            prev_member,
                            chair["name"],
                            is_chair=False,
                            round_num=round_num,
                            is_rebuttal=True,
                            target_speech=opinion,
                        )
                        rstype = self.detect_type(rebuttal)
                        self.memories[prev_member["id"]].append(rebuttal)
                        self.ctx.push(f"[{prev_member['name']} 의원]", rebuttal)
                        await self.send_speech(prev_member["id"], rebuttal, rstype, False)

                prev_speaker_id = m["id"]

                # 맥락 압축
                await self.ctx.compress_if_needed()
                await asyncio.sleep(0.3)

            # ── 라운드 종료: 의장 중간 정리 ──
            if round_num < ROUNDS:
                await self.send("status", message=f"의장 {round_num}라운드 정리 중...")
                summary_opinion = await self.get_opinion(
                    chair,
                    chair["name"],
                    is_chair=True,
                    round_num=round_num,
                )
                self.ctx.push(f"[의장 {chair['name']}]", summary_opinion)
                await self.send_speech(chair["id"], summary_opinion, "NORMAL", True)
                await asyncio.sleep(0.5)

        # ══════════════════════════════════════════
        # 최종 의결
        # ══════════════════════════════════════════
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
                await asyncio.sleep(0.2)
            await self.send("result", resultType="VOTE", content=votes)

        await self.send("status", message="✅ 토론 종료")
        await self.send("done")