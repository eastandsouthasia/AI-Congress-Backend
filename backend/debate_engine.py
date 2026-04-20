"""
AI Congress Debate Engine
✅ 변경사항:
  1. active_members: 참여 의원만 필터링하여 토론 진행
  2. max_turns: 무료 API 한도 기반 자유토론 발언 상한
  3. 릴레이/집중/패널: 시간 비례 라운드 수 조정 (짧은 시간 → 1~2라운드)
  4. 시간 프리셋 최대 30분으로 현실화 (자유토론 포함)
"""

import json
import asyncio
import random
import time
from fastapi import WebSocket
from members import MEMBERS, MEMBER_MAP
from ai_caller import call_member, call_groq
from debate_context import DebateContext

# ─────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────
MAX_ROUNDS         = 3    # 최대 라운드 수
MIN_ROUNDS         = 1    # 최소 라운드 수

# 자유토론 발언 상한: 무료 API 기준
# Gemini: 일 500회 / Groq: 분당 30회
# 의원 1명 = 1 API call, 의장 사회도 포함
# 30분 × 3발언/분 = 90회가 현실적 상한
MAX_FREE_TURNS     = 60   # 안전 마진 포함한 자유토론 발언 상한


class DebateEngine:
    def __init__(
        self,
        issue: str,
        duration: int,
        ws: WebSocket,
        debate_format: str = "릴레이",
        conclusion_type: str = "VOTE",
        active_members: list = None,   # ✅ 참여 의원 ID 목록
    ):
        self.issue           = issue
        self.duration        = duration          # 분 단위 (최대 30)
        self.ws              = ws
        self.ctx             = DebateContext()
        self.debate_format   = debate_format
        self.conclusion_type = conclusion_type
        self.start_time      = None

        # ✅ 참여 의원 필터링
        # active_members가 None이거나 빈 리스트면 전원 참여
        if active_members and len(active_members) >= 2:
            self.members = [m for m in MEMBERS if m["id"] in active_members]
            # 정렬 순서 유지 (MEMBERS 원래 순서 기준)
            if len(self.members) < 2:
                self.members = list(MEMBERS)  # 필터 결과가 너무 적으면 전원
        else:
            self.members = list(MEMBERS)

        self.member_map   = {m["id"]: m for m in self.members}
        self.memories     = {m["id"]: [] for m in self.members}
        self.speech_count = {m["id"]: 0 for m in self.members}
        self.current_round = 0

        # ✅ 시간 비례 라운드 수 계산
        # 5분 이하: 1라운드, 10~19분: 2라운드, 20분 이상: 3라운드
        if duration <= 5:
            self.rounds = 1
        elif duration <= 19:
            self.rounds = 2
        else:
            self.rounds = 3

        # ✅ 자유토론 발언 상한: 시간과 의원 수 기반
        # 실제로는 분당 약 1.5~2.5 발언이 가능 (API 응답 시간 고려)
        turns_by_time = int(duration * 2.0)
        self.max_free_turns = min(turns_by_time, MAX_FREE_TURNS)

        print(
            f"[Engine] 참여 의원 {len(self.members)}명 / "
            f"{duration}분 / {self.rounds}라운드 / "
            f"자유토론 상한 {self.max_free_turns}회"
        )

        # 참여 의원 목록 문자열 (프롬프트용)
        self.member_list_str = "\n".join(f"- {m['name']}" for m in self.members)

    # ══════════════════════════════════════════════
    # 전송 헬퍼
    # ══════════════════════════════════════════════
    async def send(self, msg_type: str, **kwargs):
        await self.ws.send_json({"type": msg_type, **kwargs})

    async def send_speech(self, member: dict, text: str,
                          speech_type: str, is_chair: bool,
                          skip_wait: bool = False):
        """
        ✅ ready 대기 완전 제거 — 고정 딜레이로 대체
        skip_wait=True: 의장 사회 발언 (0.3초), False: 실제 발언 (1.2초)
        백엔드가 TTS 완료를 기다리지 않으므로 발언 전환 지연 없음
        """
        display     = f"의장 {member['name']}" if is_chair else f"{member['name']} 의원"
        model_str   = member.get("model", "?")
        engine_info = f"{member.get('engine','?')}/{model_str.split('/')[-1]}"
        await self.send(
            "speech",
            memberId    = member["id"],
            displayName = display,
            text        = text,
            speechType  = speech_type,
            engineInfo  = engine_info,
            color       = member.get("color", "#ffffff"),
            avatar      = member.get("avatar", "💬"),
        )
        self.speech_count[member["id"]] = self.speech_count.get(member["id"], 0) + 1
        # 고정 딜레이: 의장 사회(0.5초) vs 의원 발언(2.0초)
        await asyncio.sleep(0.5 if skip_wait else 2.0)


    @staticmethod
    def _strip_prefix(text: str) -> str:
        """
        AI가 실수로 붙인 '[이름]:' 또는 '[이름 의원]:' prefix를 제거.
        예) '[Gemini의원]: 발언내용' → '발언내용'
            '[의장 ChatGPT]: 발언내용' → '발언내용'
        """
        import re
        if not text:
            return text
        # 패턴: [임의텍스트]: 또는 [임의텍스트]:(공백)
        cleaned = re.sub(r'^\s*\[[^\]]{1,30}\]\s*:\s*', '', text.strip())
        # 연속 중복 제거 (두 번 붙은 경우)
        cleaned = re.sub(r'^\s*\[[^\]]{1,30}\]\s*:\s*', '', cleaned.strip())
        return cleaned.strip() if cleaned.strip() else text.strip()

    async def _wait_for_ready(self):
        # ✅ 더 이상 사용하지 않음 — send_speech의 고정 딜레이로 대체
        pass

    @staticmethod
    def detect_type(text: str) -> str:
        if "[REFUTE]" in text: return "REFUTE"
        if "[ADMIT]"  in text: return "ADMIT"
        return "NORMAL"

    def _elapsed_minutes(self) -> float:
        if self.start_time is None:
            return 0.0
        return (time.time() - self.start_time) / 60.0

    def _time_over(self) -> bool:
        return self._elapsed_minutes() >= self.duration

    # ══════════════════════════════════════════════
    # 의장 사회 발언 생성
    # ══════════════════════════════════════════════
    async def chair_speak(self, chair: dict, instruction: str, max_chars: int = 150) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 의장 {chair['name']}입니다. 현재 토론 형식: [{self.debate_format}]\n"
                    f"참여 의원 목록:\n{self.member_list_str}\n\n"
                    "역할: 사회자. 개인 주장 절대 금지. 지시된 사회 행위만 수행하세요.\n"
                    f"{max_chars}자 이내. 완전한 문장으로. 공식적인 의회 어투로."
                )
            },
            {
                "role": "user",
                "content": f"안건: \"{self.issue}\"\n\n사회 지시: {instruction}\n\n지금 발언하세요."
            }
        ]
        try:
            result = await call_member(chair, messages, temperature=0.3)
            return self._strip_prefix(result)
        except Exception as e:
            print(f"[의장 사회] 실패: {e}")
            return instruction

    # ══════════════════════════════════════════════
    # 의원 발언 생성
    # ══════════════════════════════════════════════
    async def get_opinion(
        self,
        member: dict,
        chair_name: str,
        format_guide: str = "",
        round_num: int = 1,
        is_rebuttal: bool = False,
        target_speech: str = None,
        free_mode: bool = False,
    ) -> str:

        if free_mode:
            action_guide = (
                "지금은 자유토론 중입니다. 순서 제한 없이 자유롭게 발언하세요.\n"
                "직전 발언에 즉각 반응하세요: 동조하면 [ADMIT], 반박하면 [REFUTE], "
                "새 주장이면 [DATA]를 활용하세요. 250자 이내."
            )
        elif is_rebuttal and target_speech:
            action_guide = (
                f"즉석 반박 상황: 방금 당신의 주장이 반박되었습니다:\n"
                f"\"{target_speech[:200]}\"\n"
                "반드시 이 반박에 정면으로 맞서 재반박하세요. 250자 이내."
            )
        else:
            max_round = self.rounds
            if round_num == 1:
                action_guide = "첫 발언입니다. 전문 분야 기반으로 찬반 입장과 핵심 근거를 명확히 밝히세요."
            elif round_num == max_round:
                action_guide = "최종 라운드입니다. 전체 토론을 검토하고 최종 입장을 강하게 천명하세요."
            else:
                action_guide = "앞선 발언의 논리적 약점을 지적하고 새 데이터로 반박하세요."
            action_guide += "\n250자 이내."

        last = self.ctx.all_logs[-1] if self.ctx.all_logs else None
        last_hint = (
            f"\n\n직전 발언 ({last['speaker']}):\n\"{last['text'][:150]}\"\n"
            "→ 반드시 이 발언에 반응(동의·반박·보완)하며 시작하세요."
        ) if last else ""

        # ✅ 의원별 페르소나·온도 추출 (이념 편향 제거 — 학습 데이터 기반으로 전환)
        persona     = member.get("persona", f"당신은 {member['name']}입니다.")
        temperature = member.get("temperature", 0.6)

        system = (
            f"당신은 AI 의회 토론 참여자입니다.\n"
            f"당신은 {member['name']} 의원입니다.\n\n"
            f"【당신의 정체성과 지식 기반】\n"
            f"{persona}\n\n"
            f"【핵심 원칙 — 반드시 준수】\n"
            "1. 인위적 역할극·이념·페르소나 금지. 당신이 실제로 학습한 지식과 데이터로만 발언하라.\n"
            "2. 주장은 반드시 '근거 → 논리 → 결론' 순서로 전개하라.\n"
            "3. 확실한 것은 자신 있게, 불확실한 것은 반드시 '불확실' 또는 '추정'으로 명시하라.\n"
            "4. 다른 의원의 데이터나 논리에 오류가 있으면 구체적으로 지적하라.\n"
            "5. 이미 나온 주장을 반복하지 말고, 당신의 학습 기반에서 나오는 고유한 관점을 추가하라.\n\n"
            f"참여 의원 목록 (이 이름만 사용):\n{self.member_list_str}\n\n"
            f"【현재 토론 형식: {self.debate_format}】\n"
            f"{format_guide}\n\n"
            "발언 태그 (반드시 활용):\n"
            "[REFUTE]: 상대 논리·데이터 오류를 구체적 근거로 지적\n"
            "[ADMIT]: 상대가 더 타당 → 반드시 본인 입장 수정 내용 명시\n"
            "[DATA]: 객관적 수치·통계. 예: [DATA] OECD 2023년 기준 15% 감소\n"
            "[GRAPHIC]: 텍스트 시각화\n\n"
            "필수 규칙:\n"
            f"- {action_guide}\n"
            "- 이미 나온 주장 반복 금지. 당신의 학습 기반 고유의 새 관점·데이터를 추가하라.\n"
            "- 다른 의원과 같은 결론이라도 반드시 다른 근거와 다른 언어로 표현하라.\n"
            "- 반드시 마침표·느낌표·물음표로 완전히 끝내세요.\n"
            "- 자신을 '본 의원'이라 하세요.\n"
            f"- 의장: '{chair_name} 의장님' / 다른 의원: '○○ 의원님'\n"
            "- [ADMIT] 후에는 수정된 입장을 이후 발언에서 일관되게 유지하세요.\n"
            "- 출력 형식 엄수: 발언 내용만 바로 출력. '[이름]:' '[이름 의원]:' 같은 이름 prefix 절대 금지.\n"
        )

        messages = [
            {"role": "system", "content": system},
            *self.ctx.to_messages(),
            {"role": "user", "content": f"안건: \"{self.issue}\"{last_hint}\n\n지금 발언하세요."},
        ]
        try:
            result = await call_member(member, messages, temperature=temperature)
            result = self._strip_prefix(result)
            return result or f"{member['name']} 의원은 신중한 검토가 필요하다고 봅니다."
        except Exception as ex:
            print(f"[{member['name']}] 발언 실패: {ex}")
            return f"{member['name']} 의원은 더 많은 논의가 필요하다고 판단합니다."

    # ══════════════════════════════════════════════
    # 최종 의결
    # ══════════════════════════════════════════════
    async def get_vote(self, member: dict) -> str:
        speeches   = self.memories[member["id"]]
        admit_note = (
            "\n※ 당신은 토론 중 일부 입장을 수정했습니다([ADMIT]). 수정된 최종 입장으로 투표하세요."
            if any("[ADMIT]" in s for s in speeches) else ""
        )
        full_summary = f"\n\n[전체 토론 요약]\n{self.ctx.summary}" if self.ctx.summary else ""
        persona = member.get("persona", "")
        temp    = member.get("temperature", 0.4)

        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 {member['name']} 의원입니다.\n"
                    f"【당신의 정체성과 지식 기반】 {persona}\n\n"
                    f"당신의 전체 토론 발언:\n\"\"\"\n"
                    f"{chr(10).join(speeches) if speeches else '발언 없음'}\n\"\"\""
                    f"{full_summary}{admit_note}\n\n"
                    "투표 규칙:\n"
                    "- 인위적 이념이나 편향이 아니라, 토론 중 제시된 실제 근거와 논리에 따라 투표하세요.\n"
                    "- 위 발언 내용과 논리적으로 완전히 일관된 투표를 하세요.\n"
                    "- 형식: [찬성|반대|기권] 이유 (200자 이내, 완전한 문장으로)"
                )
            },
            {"role": "user", "content": f"안건 \"{self.issue}\"에 최종 투표하세요."}
        ]
        try:
            return await call_member(member, messages, temperature=max(0.3, temp - 0.2))
        except:
            return "[기권] 시스템 오류로 기권합니다."

    async def get_resolution(self) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 의회 서기입니다. 전체 토론 내용을 바탕으로 공식 결의문을 작성하세요.\n"
                    "형식: 1)전문(배경과 논의 경과) 2)결의 조항(번호) 3)서명란\n"
                    "[ADMIT]로 수용된 의견을 반드시 반영하세요. 500자 이내. 완전한 문장."
                )
            },
            {
                "role": "user",
                "content": f"안건: \"{self.issue}\"\n\n전체 토론:\n{self.ctx.to_plain_text()}\n\n결의문을 작성하세요."
            }
        ]
        try:
            return await call_groq(messages, temperature=0.5)
        except:
            return "의원들의 충분한 논의를 바탕으로 본 안건을 검토하였다."

    async def run_conclusion(self, chair: dict):
        """최종 의결 공통 처리"""
        if self.debate_format == "자유토론":
            close_instruction = (
                f"자유토론이 종료되었습니다. "
                f"{'찬반 표결' if self.conclusion_type == 'VOTE' else '공동 결의안 채택'}을 실시하겠습니다."
            )
        else:
            close_instruction = (
                f"총 {self.rounds}라운드의 토론이 완료되었습니다. 최종 의결을 진행하겠습니다."
            )
        close_text = await self.chair_speak(chair, close_instruction, max_chars=200)
        self.ctx.push(f"[의장 {chair['name']}]", close_text)
        await self.send_speech(chair, close_text, "NORMAL", True)

        await self.send("status", message="최종 의결 진행 중...")
        if self.conclusion_type == "RESOLUTION":
            resolution = await self.get_resolution()
            await self.send("result", resultType="RESOLUTION", content=resolution)
        else:
            votes = []
            for m in self.members:
                await self.send("status", message=f"{m['name']} 의원 최종 투표 중...")
                vote = await self.get_vote(m)
                votes.append({"memberId": m["id"], "text": vote})
            await self.send("result", resultType="VOTE", content=votes)

        await self.send("status", message="✅ 토론 종료")
        await self.send("done")

    # ══════════════════════════════════════════════
    # 메인 진입점
    # ══════════════════════════════════════════════
    async def run(self):
        chair = random.choice(self.members)
        await self.send("protocol",
            format    = self.debate_format,
            chairId   = chair["id"],
            chairName = chair["name"],
        )
        print(f"[Engine] 의장: {chair['name']} / 형식: {self.debate_format} / 라운드: {self.rounds}")

        dispatch = {
            "릴레이":     self._run_relay,
            "집중토론":   self._run_focused,
            "전문가패널": self._run_panel,
            "자유토론":   self._run_free,
        }
        runner = dispatch.get(self.debate_format, self._run_relay)
        await runner(chair)

    # ══════════════════════════════════════════════
    # 1. 릴레이 토론
    # ══════════════════════════════════════════════
    async def _run_relay(self, chair: dict):
        fmt_guide = (
            "【릴레이 형식 규칙】\n"
            "의장이 지목한 순서대로 한 명씩 발언합니다.\n"
            "지목되지 않은 의원은 절대 끼어들 수 없습니다.\n"
            "발언은 최대 250자이며, 지목 즉시 발언을 시작하세요."
        )

        # ✅ 개회사만 AI 생성 (의미 있는 발언)
        open_text = await self.chair_speak(
            chair,
            f"안건 \"{self.issue}\"에 대한 릴레이 토론을 개회합니다. "
            f"총 {self.rounds}라운드로 진행되며, 의장 지목 순서에 따라 균등하게 발언합니다.",
            max_chars=180
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        non_chair = [m for m in self.members if m["id"] != chair["id"]]

        for round_num in range(1, self.rounds + 1):
            self.current_round = round_num

            # 라운드 선언: 1라운드는 개회사가 이미 역할을 하므로 생략
            # 2라운드부터만 의장이 선언 후 충분히 끊고 첫 지목으로 넘어감
            if round_num > 1:
                if round_num == self.rounds:
                    round_text = f"{round_num}라운드입니다. 전체 토론을 바탕으로 최종 입장을 밝혀주십시오."
                else:
                    round_text = f"{round_num}라운드입니다. 앞선 발언의 논리적 약점을 지적하고 반박해 주십시오."
                self.ctx.push(f"[의장 {chair['name']}]", round_text)
                await self.send_speech(chair, round_text, "NORMAL", True)  # 자연스럽게 끊어줌

            order = non_chair.copy()
            if round_num > 1:
                random.shuffle(order)

            prev_speaker_id = None

            for m in order:
                # 의장 지목 → API 호출(대기) → 발언 표시
                # 이것이 릴레이의 핵심: 지목 → (생성 중) → 발언 → 다음 지목
                nominate = f"{m['name']} 의원님, 발언해 주시기 바랍니다."
                self.ctx.push(f"[의장 {chair['name']}]", nominate)
                await self.send_speech(chair, nominate, "NORMAL", True, skip_wait=True)

                await self.send("status", message=f"⏳ {m['name']} 의원 발언 준비 중...")
                opinion = await self.get_opinion(
                    m, chair["name"],
                    format_guide=fmt_guide, round_num=round_num,
                )

                stype = self.detect_type(opinion)
                self.memories[m["id"]].append(opinion)
                self.ctx.push(f"[{m['name']} 의원]", opinion)
                await self.send_speech(m, opinion, stype, False)  # 2.0초 대기 → 다음 지목으로 자연스럽게

                # 즉석 반박권 (2라운드 이상, 마지막 라운드 제외)
                if (stype == "REFUTE"
                        and prev_speaker_id
                        and prev_speaker_id != m["id"]
                        and round_num >= 2
                        and round_num < self.rounds):
                    prev_m = self.member_map.get(prev_speaker_id)
                    if prev_m:
                        allow = f"{prev_m['name']} 의원님, 즉석 반박권을 인정합니다. 간략히 반박해 주십시오."
                        self.ctx.push(f"[의장 {chair['name']}]", allow)
                        await self.send_speech(chair, allow, "NORMAL", True, skip_wait=True)

                        await self.send("status", message=f"⏳ {prev_m['name']} 의원 반박 준비 중...")
                        rebuttal = await self.get_opinion(
                            prev_m, chair["name"],
                            format_guide=fmt_guide,
                            round_num=round_num,
                            is_rebuttal=True,
                            target_speech=opinion,
                        )
                        rstype = self.detect_type(rebuttal)
                        self.memories[prev_m["id"]].append(rebuttal)
                        self.ctx.push(f"[{prev_m['name']} 의원]", rebuttal)
                        await self.send_speech(prev_m, rebuttal, rstype, False)

                prev_speaker_id = m["id"]
                await self.ctx.compress_if_needed()

            # 라운드 전환 (마지막 라운드는 run_conclusion이 처리)
            if round_num < self.rounds:
                transition = f"{round_num}라운드가 종료되었습니다. 잠시 후 {round_num + 1}라운드를 시작합니다."
                self.ctx.push(f"[의장 {chair['name']}]", transition)
                await self.send_speech(chair, transition, "NORMAL", True)  # 충분히 끊어줌

        await self.run_conclusion(chair)

    # ══════════════════════════════════════════════
    # 2. 집중토론 — ✅ self.rounds 적용
    # ══════════════════════════════════════════════
    async def _run_focused(self, chair: dict):
        fmt_guide = (
            "【집중토론 형식 규칙】\n"
            "핵심 토론자 2인이 교대로 집중 대결합니다.\n"
            "나머지 의원은 질의 시간에만 발언할 수 있습니다.\n"
            "핵심 토론자는 반드시 상대방 발언에 직접 반박해야 합니다."
        )

        non_chair = [m for m in self.members if m["id"] != chair["id"]]
        # 최소 2명 필요, 없으면 릴레이로 폴백
        if len(non_chair) < 2:
            print("[Engine] 집중토론 인원 부족 → 릴레이 폴백")
            await self._run_relay(chair)
            return

        debaters  = random.sample(non_chair, 2)
        observers = [m for m in non_chair if m not in debaters]
        d_names   = f"{debaters[0]['name']} 의원, {debaters[1]['name']} 의원"

        open_text = await self.chair_speak(
            chair,
            f"안건 \"{self.issue}\"에 대한 집중토론을 개회합니다. "
            f"핵심 토론자는 {d_names}입니다. "
            f"두 분이 {self.rounds}라운드에 걸쳐 집중 대결하며, 나머지 의원께서는 이후 질의 시간까지 대기해 주십시오.",
            max_chars=260
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        for round_num in range(1, self.rounds + 1):
            self.current_round = round_num
            if self.rounds == 1:
                label = "집중 토론"
            elif round_num == 1:
                label = "입장 표명"
            elif round_num == self.rounds:
                label = "최종 변론"
            else:
                label = "정면 반박"

            round_text = await self.chair_speak(
                chair,
                f"집중토론 {round_num}라운드 [{label}]를 시작합니다.",
                max_chars=80
            )
            self.ctx.push(f"[의장 {chair['name']}]", round_text)
            await self.send_speech(chair, round_text, "NORMAL", True)

            pair = debaters.copy()
            if round_num > 1:
                random.shuffle(pair)

            for m in pair:
                nominate = f"{m['name']} 의원님, 발언해 주십시오."
                self.ctx.push(f"[의장 {chair['name']}]", nominate)
                await self.send_speech(chair, nominate, "NORMAL", True)

                await self.send("status",
                    message=f"[집중토론 {round_num}/{self.rounds}라운드] {m['name']} 발언 중...")
                opinion = await self.get_opinion(
                    m, chair["name"],
                    format_guide=fmt_guide,
                    round_num=round_num,
                )
                stype = self.detect_type(opinion)
                self.memories[m["id"]].append(opinion)
                self.ctx.push(f"[{m['name']} 의원]", opinion)
                await self.send_speech(m, opinion, stype, False)
                await self.ctx.compress_if_needed()

            if round_num < self.rounds:
                inter = await self.chair_speak(
                    chair,
                    f"{round_num}라운드 대결 정리. 핵심 쟁점을 요약하고 다음 라운드를 안내하세요.",
                    max_chars=200
                )
                self.ctx.push(f"[의장 {chair['name']}]", inter)
                await self.send_speech(chair, inter, "NORMAL", True)

        # 질의 시간 (참여 의원이 3명 이상일 때만)
        if observers:
            qa_open = await self.chair_speak(
                chair,
                "핵심토론이 완료되었습니다. 나머지 의원님들의 질의 시간입니다.",
                max_chars=150
            )
            self.ctx.push(f"[의장 {chair['name']}]", qa_open)
            await self.send_speech(chair, qa_open, "NORMAL", True)

            random.shuffle(observers)
            for m in observers:
                nominate = f"{m['name']} 의원님, 질의해 주십시오."
                self.ctx.push(f"[의장 {chair['name']}]", nominate)
                await self.send_speech(chair, nominate, "NORMAL", True)

                await self.send("status", message=f"[질의] {m['name']} 의원 발언 중...")
                opinion = await self.get_opinion(
                    m, chair["name"],
                    format_guide=fmt_guide,
                    round_num=2,
                )
                stype = self.detect_type(opinion)
                self.memories[m["id"]].append(opinion)
                self.ctx.push(f"[{m['name']} 의원]", opinion)
                await self.send_speech(m, opinion, stype, False)
                await self.ctx.compress_if_needed()

        await self.run_conclusion(chair)

    # ══════════════════════════════════════════════
    # 3. 전문가패널 — ✅ self.rounds 반영 (질의 라운드 조정)
    # ══════════════════════════════════════════════
    async def _run_panel(self, chair: dict):
        fmt_guide = (
            "【전문가패널 형식 규칙】\n"
            "전문가 패널이 먼저 심층 발언합니다.\n"
            "패널 발언 후 전체 질의·응답 시간이 진행됩니다.\n"
            "패널 발언 중 다른 의원의 개입은 허용하지 않습니다."
        )

        non_chair  = [m for m in self.members if m["id"] != chair["id"]]
        # 패널 수: 전체 의원의 절반 (최소 1, 최대 3)
        panel_count = max(1, min(3, len(non_chair) // 2))
        panels  = random.sample(non_chair, panel_count)
        general = [m for m in non_chair if m not in panels]
        p_names = ", ".join(f"{p['name']} 의원" for p in panels)

        open_text = await self.chair_speak(
            chair,
            f"안건 \"{self.issue}\"에 대한 전문가패널 토론을 개회합니다. "
            f"전문가 패널로 {p_names}을 선정했습니다. "
            "심층 발언 후 전체 질의·응답을 진행합니다. "
            "패널 발언 중에는 다른 의원의 개입을 엄격히 금합니다.",
            max_chars=300
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        panel_start = await self.chair_speak(
            chair,
            "패널 심층 발언을 시작합니다. 각 패널은 전문 분야의 깊이 있는 분석을 제시해 주십시오.",
            max_chars=110
        )
        self.ctx.push(f"[의장 {chair['name']}]", panel_start)
        await self.send_speech(chair, panel_start, "NORMAL", True)

        for m in panels:
            nominate = f"패널 {m['name']} 의원님, 전문가 발언을 시작해 주십시오."
            self.ctx.push(f"[의장 {chair['name']}]", nominate)
            await self.send_speech(chair, nominate, "NORMAL", True)

            await self.send("status", message=f"[전문가패널] {m['name']} 의원 심층 발언 중...")
            opinion = await self.get_opinion(
                m, chair["name"],
                format_guide=fmt_guide,
                round_num=1,
            )
            stype = self.detect_type(opinion)
            self.memories[m["id"]].append(opinion)
            self.ctx.push(f"[{m['name']} 의원]", opinion)
            await self.send_speech(m, opinion, stype, False)
            await self.ctx.compress_if_needed()

        summary = await self.chair_speak(
            chair,
            "패널 발언이 완료되었습니다. 전체 질의·응답 시간을 시작합니다.",
            max_chars=150
        )
        self.ctx.push(f"[의장 {chair['name']}]", summary)
        await self.send_speech(chair, summary, "NORMAL", True)

        # ✅ 질의 라운드 수도 self.rounds로 조정
        qa_rounds = max(1, self.rounds - 1)  # 릴레이보다 1라운드 적게
        for qa_round in range(1, qa_rounds + 1):
            if general:
                qa_text = await self.chair_speak(
                    chair,
                    f"전체 질의·응답 {qa_round}라운드를 시작합니다.",
                    max_chars=100
                )
                self.ctx.push(f"[의장 {chair['name']}]", qa_text)
                await self.send_speech(chair, qa_text, "NORMAL", True)

                shuffled_gen = general.copy()
                random.shuffle(shuffled_gen)
                for m in shuffled_gen:
                    nominate = f"{m['name']} 의원님, 패널에 질의해 주십시오."
                    self.ctx.push(f"[의장 {chair['name']}]", nominate)
                    await self.send_speech(chair, nominate, "NORMAL", True)

                    await self.send("status", message=f"[질의응답] {m['name']} 발언 중...")
                    opinion = await self.get_opinion(
                        m, chair["name"],
                        format_guide=fmt_guide,
                        round_num=2,
                    )
                    stype = self.detect_type(opinion)
                    self.memories[m["id"]].append(opinion)
                    self.ctx.push(f"[{m['name']} 의원]", opinion)
                    await self.send_speech(m, opinion, stype, False)
                    await self.ctx.compress_if_needed()

            for p in panels:
                nominate = f"패널 {p['name']} 의원님, 질의에 응답해 주십시오."
                self.ctx.push(f"[의장 {chair['name']}]", nominate)
                await self.send_speech(chair, nominate, "NORMAL", True)

                await self.send("status", message=f"[패널 응답] {p['name']} 발언 중...")
                opinion = await self.get_opinion(
                    p, chair["name"],
                    format_guide=fmt_guide,
                    round_num=2,
                )
                stype = self.detect_type(opinion)
                self.memories[p["id"]].append(opinion)
                self.ctx.push(f"[{p['name']} 의원]", opinion)
                await self.send_speech(p, opinion, stype, False)
                await self.ctx.compress_if_needed()

        await self.run_conclusion(chair)

    # ══════════════════════════════════════════════
    # 4. 자유토론 — ✅ max_free_turns + 시간 이중 체크
    # ══════════════════════════════════════════════
    async def _run_free(self, chair: dict):
        fmt_guide = (
            "【자유토론 형식 규칙】\n"
            "순서 제한 없이 누구든 자유롭게 발언합니다.\n"
            "직전 발언에 즉각 반응하세요: 동조([ADMIT]) 또는 반박([REFUTE]).\n"
            "새로운 근거·데이터([DATA])를 적극 활용하세요.\n"
            "시간 또는 발언 수 한도 종료 후 의장이 즉시 최종 의결을 강제합니다."
        )

        self.start_time   = time.time()
        deadline_mins     = self.duration
        warn_threshold    = deadline_mins * 0.8

        open_text = await self.chair_speak(
            chair,
            f"안건 \"{self.issue}\"에 대한 자유토론을 개회합니다. "
            f"총 {deadline_mins}분 또는 최대 {self.max_free_turns}회 발언까지 "
            "순서 제한 없이 자유롭게 발언하실 수 있습니다. "
            "시간 또는 발언 한도 종료 후에는 즉시 최종 의결로 이행합니다.",
            max_chars=300
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        warned    = False
        turn      = 0
        non_chair = [m for m in self.members if m["id"] != chair["id"]]

        # ✅ 시간 OR 발언 수 둘 다 체크
        while not self._time_over() and turn < self.max_free_turns:
            elapsed = self._elapsed_minutes()

            # 80% 시간 경고
            if not warned and elapsed >= warn_threshold:
                warned = True
                remaining_sec = int((deadline_mins - elapsed) * 60)
                remaining_turns = self.max_free_turns - turn
                warn_text = await self.chair_speak(
                    chair,
                    f"시간 알림: 자유토론 종료까지 약 {remaining_sec}초, "
                    f"잔여 발언 {remaining_turns}회 남았습니다. "
                    "핵심 주장을 마무리해 주시기 바랍니다.",
                    max_chars=140
                )
                self.ctx.push(f"[의장 {chair['name']}]", warn_text)
                await self.send_speech(chair, warn_text, "NORMAL", True)
                if self._time_over() or turn >= self.max_free_turns:
                    break

            # 발언자 선택
            speaker = None
            if self.ctx.all_logs:
                last_log  = self.ctx.all_logs[-1]
                last_text = last_log.get("text", "")
                last_spk  = last_log.get("speaker", "")

                if "[REFUTE]" in last_text:
                    candidates = [m for m in non_chair if m["name"] not in last_spk]
                    speaker = random.choice(candidates) if candidates else random.choice(non_chair)
                else:
                    recent_ids = set()
                    for log in self.ctx.all_logs[-2:]:
                        for m in non_chair:
                            if m["name"] in log.get("speaker", ""):
                                recent_ids.add(m["id"])
                    fresh = [m for m in non_chair if m["id"] not in recent_ids]
                    speaker = random.choice(fresh) if fresh else random.choice(non_chair)
            else:
                speaker = random.choice(non_chair)

            await self.send("status",
                message=f"[자유토론] {speaker['name']} 의원 발언 준비 중... "
                        f"({int(elapsed)}분 {int((elapsed % 1) * 60)}초 / {deadline_mins}분 "
                        f"| {turn + 1}/{self.max_free_turns}회)")

            opinion = await self.get_opinion(
                speaker, chair["name"],
                format_guide=fmt_guide,
                round_num=1,
                free_mode=True,
            )
            await self.send("status",
                message=f"[자유토론] {speaker['name']} 의원 발언 중...")
            stype = self.detect_type(opinion)
            self.memories[speaker["id"]].append(opinion)
            self.ctx.push(f"[{speaker['name']} 의원]", opinion)
            await self.send_speech(speaker, opinion, stype, False)
            await self.ctx.compress_if_needed()

            turn += 1

            # 매 5발언마다 의장 중간 정리 (TTS 대기 없이 즉시 다음 발언으로)
            if turn % 5 == 0 and not self._time_over() and turn < self.max_free_turns:
                inter = await self.chair_speak(
                    chair,
                    "잠시 중간 정리를 하겠습니다. 현재까지의 주요 찬반 논점을 요약하고 자유토론을 계속합니다.",
                    max_chars=160
                )
                self.ctx.push(f"[의장 {chair['name']}]", inter)
                await self.send_speech(chair, inter, "NORMAL", True, skip_wait=True)

        print(f"[Engine] 자유토론 종료: {turn}회 발언 / {self._elapsed_minutes():.1f}분 경과")
        await self.run_conclusion(chair)