"""
AI Congress Debate Engine
✅ 변경사항:
  1. 의장 자율 사회 (절충안): 라운드 선언·전환·반박 허가 판단은 AI 생성
     순서 기본값은 엔진이 유지 → 의장 추가 호출 최소화
  2. 의원 자발적 반박 신청: [REFUTE] 발언 후 반박 희망 의원이 신청
     의장이 허가 여부를 자율 판단 (OpenRouter 의장이면 Groq 폴백)
  3. 발언 길이 500자로 확대, 의장 발언 과다 시 개입
  4. TTS 수학기호 오독 방지: ≥ ≤ > < = 한글 변환
  5. [TABLE] 태그 추가: 텍스트 테이블 허용
  6. action_guide 자율 판단으로 전환 (2라운드도 반박 강제 없음)
"""

import json
import asyncio
import random
import re
import time
from fastapi import WebSocket
from members import MEMBERS, MEMBER_MAP
from ai_caller import call_member, call_groq
from debate_context import DebateContext

# ─────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────
MAX_ROUNDS     = 3
MIN_ROUNDS     = 1
MAX_FREE_TURNS = 60
MAX_SPEECH_LEN = 500   # 의원 발언 최대 글자수 (기존 250 → 500)
CHAIR_MAX_LEN  = 200   # 의장 사회 발언 최대 글자수


class DebateEngine:
    def __init__(
        self,
        issue: str,
        duration: int,
        ws: WebSocket,
        debate_format: str = "릴레이",
        conclusion_type: str = "VOTE",
        active_members: list = None,
    ):
        self.issue           = issue
        self.duration        = duration
        self.ws              = ws
        self.ctx             = DebateContext()
        self.debate_format   = debate_format
        self.conclusion_type = conclusion_type
        self.start_time      = None

        if active_members and len(active_members) >= 2:
            self.members = [m for m in MEMBERS if m["id"] in active_members]
            if len(self.members) < 2:
                self.members = list(MEMBERS)
        else:
            self.members = list(MEMBERS)

        self.member_map    = {m["id"]: m for m in self.members}
        self._ready_event  = asyncio.Event()
        self.memories      = {m["id"]: [] for m in self.members}
        self.speech_count  = {m["id"]: 0 for m in self.members}
        self.current_round = 0

        if duration <= 5:
            self.rounds = 1
        elif duration <= 19:
            self.rounds = 2
        else:
            self.rounds = 3

        turns_by_time = int(duration * 2.0)
        self.max_free_turns = min(turns_by_time, MAX_FREE_TURNS)

        print(
            f"[Engine] 참여 의원 {len(self.members)}명 / "
            f"{duration}분 / {self.rounds}라운드 / "
            f"자유토론 상한 {self.max_free_turns}회"
        )

        self.member_list_str = "\n".join(f"- {m['name']}" for m in self.members)

    # ══════════════════════════════════════════════
    # 전송 헬퍼
    # ══════════════════════════════════════════════
    async def send(self, msg_type: str, **kwargs):
        await self.ws.send_json({"type": msg_type, **kwargs})

    async def send_speech(self, member: dict, text: str,
                          speech_type: str, is_chair: bool,
                          skip_wait: bool = False):
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
        if skip_wait:
            await asyncio.sleep(0.3)

    @staticmethod
    def _strip_prefix(text: str) -> str:
        if not text:
            return text
        cleaned = re.sub(r'^\s*\[[^\]]{1,30}\]\s*:\s*', '', text.strip())
        cleaned = re.sub(r'^\s*\[[^\]]{1,30}\]\s*:\s*', '', cleaned.strip())
        return cleaned.strip() if cleaned.strip() else text.strip()

    async def _wait_for_ready(self, timeout: float = 60.0):
        """프론트에서 'ready' ACK가 올 때까지 대기 (최대 timeout초)"""
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
            self._ready_event.clear()
        except asyncio.TimeoutError:
            print("[Engine] ACK 타임아웃 — 강제 진행")

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
    async def chair_speak(self, chair: dict, instruction: str,
                          max_chars: int = CHAIR_MAX_LEN) -> str:
        # 의장을 제외한 의원 목록 — 발언 지목 대상은 이 중에서만
        non_chair_names = [m["name"] for m in self.members if m["id"] != chair["id"]]
        non_chair_list_str = "\n".join(f"- {n}" for n in non_chair_names)
        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 의장 {chair['name']}입니다. 현재 토론 형식: [{self.debate_format}]\n"
                    f"참여 의원 목록 (발언 지목 대상):\n{non_chair_list_str}\n\n"
                    "역할: 사회자. 개인 주장 절대 금지. 지시된 사회 행위만 수행하세요.\n"
                    f"⚠️ 절대 금지: 본인({chair['name']})을 발언자로 지목하거나 호명하지 마세요. "
                    "의장은 사회만 봅니다.\n"
                    f"발언 지목 시 반드시 위 목록에 있는 의원 이름만 사용하세요.\n"
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
            cleaned = self._strip_prefix(result)
            # ── 사후 안전망: AI가 의장 자신을 지목하는 문구 제거 ──
            cleaned = self._remove_self_nomination(cleaned, chair)
            return cleaned
        except Exception as e:
            print(f"[의장 사회] 실패: {e}")
            return instruction

    @staticmethod
    def _remove_self_nomination(text: str, chair: dict) -> str:
        """
        AI 생성 텍스트에서 의장이 자기 자신을 지목하는 문구를 제거한다.
        예: "라마 의장님, 발언해 주십시오." → 제거
        """
        if not text:
            return text
        name = chair["name"]
        # 자기 지목 패턴: "의장 이름 + (의장님|의원님), 발언|말씀" 류
        patterns = [
            rf'{re.escape(name)}\s*(의장님?|의원님?)[\s,]*[가-힣\s]*(?:발언|말씀|의견)[가-힣\s]*(?:주십시오|주세요|해주십시오|해주세요|바랍니다)[.。]?',
            rf'먼저\s+{re.escape(name)}\s*(의장님?|의원님?)[^\n.。]*[.。]?',
            rf'{re.escape(name)}\s*(의장님?|의원님?)[^\n.。]*지목[^\n.。]*[.。]?',
        ]
        for pat in patterns:
            text = re.sub(pat, '', text, flags=re.UNICODE).strip()
        # 연속 공백·빈 문장부호 정리
        text = re.sub(r'\s{2,}', ' ', text).strip()
        return text

    # ══════════════════════════════════════════════
    # 의장 자율 판단: 반박 신청 허가 여부
    # OpenRouter 의장이면 Groq 폴백
    # ══════════════════════════════════════════════
    async def chair_judge_rebuttal(
        self,
        chair: dict,
        requester: dict,
        target_speech: str,
    ) -> tuple[bool, str]:
        """
        반박 신청이 들어왔을 때 의장이 허가/거부를 자율 판단.
        반환: (허가 여부: bool, 의장 발언 텍스트: str)
        """
        non_chair_names = [m["name"] for m in self.members if m["id"] != chair["id"]]
        non_chair_list_str = "\n".join(f"- {n}" for n in non_chair_names)
        prompt_system = (
            f"당신은 의장 {chair['name']}입니다.\n"
            f"발언 가능 의원 목록 (본인 제외):\n{non_chair_list_str}\n\n"
            "역할: 공정한 사회자. 반박 신청에 대해 허가 또는 거부를 판단하세요.\n"
            "허가 기준: 반박이 토론의 실질적 진전에 기여할 때. "
            "거부 기준: 이미 충분히 논의됐거나, 발언 흐름을 지나치게 끊을 때.\n"
            f"⚠️ speech 필드에서 본인({chair['name']})을 발언자로 지목하지 마세요.\n"
            "반드시 JSON으로만 응답하세요:\n"
            "{\"allow\": true or false, \"speech\": \"의장 발언 (80자 이내)\"}"
        )
        prompt_user = (
            f"안건: \"{self.issue}\"\n"
            f"{requester['name']} 의원이 다음 발언에 대한 반박을 신청했습니다:\n"
            f"\"{target_speech[:200]}\"\n\n"
            "허가하시겠습니까? JSON으로 응답하세요."
        )
        messages = [
            {"role": "system", "content": prompt_system},
            {"role": "user",   "content": prompt_user},
        ]
        try:
            # OpenRouter 의장이면 Groq 폴백 (속도·한도 절약)
            if chair.get("engine") == "openrouter":
                raw = await call_groq(messages, temperature=0.3)
            else:
                raw = await call_member(chair, messages, temperature=0.3)

            s = raw.find('{'); e = raw.rfind('}')
            if s != -1 and e != -1:
                parsed = json.loads(raw[s:e+1])
                allow  = bool(parsed.get("allow", False))
                speech = str(parsed.get("speech", ""))
                speech = self._remove_self_nomination(self._strip_prefix(speech), chair)
                return allow, speech
        except Exception as ex:
            print(f"[의장 반박 판단] 실패: {ex}")
        # 파싱 실패 시 기본값: 허가
        return True, f"{requester['name']} 의원님, 반박 발언을 허가합니다."

    # ══════════════════════════════════════════════
    # 의장 개입: 발언 과다 시 제지
    # ══════════════════════════════════════════════
    async def chair_intervene(self, chair: dict, speaker: dict) -> str:
        return await self.chair_speak(
            chair,
            f"{speaker['name']} 의원님, 발언이 다소 길어졌습니다. "
            "핵심 논점만 간략히 마무리해 주시기 바랍니다.",
            max_chars=80,
        )

    # ══════════════════════════════════════════════
    # 의장 라운드 선언 (AI 자율 생성)
    # ══════════════════════════════════════════════
    async def chair_announce_round(self, chair: dict, round_num: int,
                                   first_member: dict = None) -> str:
        """시간 부족으로 최종 라운드 직행할 때만 사용."""
        first_name = first_member["name"] if first_member else ""
        instruction = (
            f"시간 관계상 최종 {round_num}라운드로 직행합니다. "
            "전체 토론을 검토하고 최종 입장을 밝혀주십시오."
        )
        if first_name:
            instruction += f" 먼저 {first_name} 의원님, 발언해 주십시오."
        return await self.chair_speak(chair, instruction, max_chars=CHAIR_MAX_LEN)

    # ══════════════════════════════════════════════
    # 의장 라운드 전환 (라운드→라운드 연결 + 첫 의원 지목 통합)
    # ══════════════════════════════════════════════
    async def chair_transition_round(self, chair: dict, from_round: int,
                                     first_member: dict = None) -> str:
        """
        이전 라운드 종료 + 다음 라운드 개시 + 첫 의원 지목을 하나의 발언으로 처리.
        이 함수 호출 후 바로 first_member의 발언이 시작됨 (중간 발언 없음).
        """
        next_round = from_round + 1
        is_final   = (next_round == self.rounds)
        first_name = first_member["name"] if first_member else ""

        if is_final:
            instruction = (
                f"{from_round}라운드가 종료되었습니다. "
                "지금까지의 논점을 간략히 정리하겠습니다. "
                f"이제 최종 {next_round}라운드입니다. "
                "전체 토론을 검토하고 최종 입장을 밝혀주십시오."
            )
        else:
            instruction = (
                f"{from_round}라운드가 종료되었습니다. "
                "지금까지의 주요 논점을 간략히 정리하고 "
                f"제{next_round}라운드를 시작합니다."
            )
        if first_name:
            instruction += f" 먼저 {first_name} 의원님, 발언해 주십시오."
        return await self.chair_speak(chair, instruction, max_chars=CHAIR_MAX_LEN + 50)

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
                "앞선 발언들을 면밀히 검토하고, 오직 논리적 타당성만으로 반응을 선택하세요:\n"
                "허점이나 오류가 있으면 [REFUTE]로 반박하고,\n"
                "더 타당한 주장이라면 [ADMIT]로 수긍하며 본인 입장을 구체적으로 수정하고,\n"
                "새로운 관점·데이터가 있으면 [DATA]로 보완하세요.\n"
                f"어떤 반응을 선택할지는 상대 논리의 질로만 결정하세요. {MAX_SPEECH_LEN}자 이내."
            )
        elif is_rebuttal and target_speech:
            action_guide = (
                f"즉석 반박 상황: 방금 당신의 주장이 반박되었습니다:\n"
                f"\"{target_speech[:200]}\"\n"
                f"반드시 이 반박에 정면으로 맞서 재반박하세요. {MAX_SPEECH_LEN}자 이내."
            )
        else:
            max_round = self.rounds
            if round_num == 1:
                action_guide = (
                    "첫 발언입니다. 전문 분야 기반으로 찬반 입장과 핵심 근거를 명확히 밝히세요."
                )
            elif round_num == max_round:
                action_guide = (
                    "최종 라운드입니다. 지금까지의 전체 토론을 검토하고 최종 입장을 밝히세요.\n"
                    "토론 과정에서 설득력 있는 반론이 있었다면 [ADMIT]로 입장을 수정해도 됩니다.\n"
                    "끝까지 본인 주장이 타당하다면 핵심 근거를 재확인하며 강하게 천명하세요."
                )
            else:
                action_guide = (
                    "앞선 발언들을 면밀히 검토하고, 오직 논리적 타당성만으로 반응을 선택하세요.\n"
                    "논리적 허점이나 사실 오류가 있으면 [REFUTE]로 구체적 근거를 들어 반박하고,\n"
                    "더 타당한 주장이라면 [ADMIT]로 수긍하며 본인 입장을 구체적으로 수정하고,\n"
                    "새로운 관점·데이터가 있으면 [DATA]로 보완하세요.\n"
                    "어떤 반응을 선택할지는 상대 논리의 질로만 결정하세요."
                )
            action_guide += f"\n{MAX_SPEECH_LEN}자 이내."

        last = self.ctx.all_logs[-1] if self.ctx.all_logs else None
        last_hint = (
            f"\n\n직전 발언 ({last['speaker']}):\n\"{last['text'][:150]}\"\n"
            "→ 반드시 이 발언에 반응(동의·반박·보완)하며 시작하세요."
        ) if last else ""

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
            "[GRAPHIC]: 텍스트 시각화. 예:\n"
            "  [GRAPHIC]\n"
            "  찬성 ████████░░ 78%\n"
            "  반대 ██░░░░░░░░ 22%\n"
            "[TABLE]: 텍스트 표. 예:\n"
            "  [TABLE]\n"
            "  | 항목 | 찬성측 | 반대측 |\n"
            "  |------|--------|--------|\n"
            "  | 경제 | 성장   | 불안정 |\n\n"
            "수학 표기 규칙 (TTS 오독 방지):\n"
            "  크거나 같다: '이상', 작거나 같다: '이하'\n"
            "  크다: '초과', 작다: '미만', 같다: '동일'\n"
            "  수식 기호(≥ ≤ > < =)를 직접 쓰지 말고 위 한글 표현을 사용하라.\n\n"
            "필수 규칙:\n"
            f"- {action_guide}\n"
            "- 이미 나온 주장 반복 금지. 당신의 학습 기반 고유의 새 관점·데이터를 추가하라.\n"
            "- 다른 의원과 같은 결론이라도 반드시 다른 근거와 다른 언어로 표현하라.\n"
            "- 반드시 마침표·느낌표·물음표로 완전히 끝내세요.\n"
            "- 자신을 '본 의원'이라 하세요.\n"
            f"- 의장: '{chair_name} 의장님' / 다른 의원: '○○ 의원님'\n"
            f"- ⚠️ '{chair_name} 의장님께서 다음으로 발언하시길…' 등 의장에게 발언을 요구하거나 지목하는 표현 금지.\n"
            "- [ADMIT] 후에는 수정된 입장을 이후 발언에서 일관되게 유지하세요.\n"
            "- 출력 형식 엄수: 발언 내용만 바로 출력. '[이름]:' '[이름 의원]:' 같은 이름 prefix 절대 금지.\n"
        )

        messages = [
            {"role": "system", "content": system},
            *self.ctx.to_messages(),
            {"role": "user", "content": f"안건: \"{self.issue}\"{last_hint}\n\n지금 발언하세요."},
        ]
        last_ex = None
        for attempt in range(2):
            try:
                result = await call_member(member, messages, temperature=temperature)
                result = self._strip_prefix(result)
                if result and len(result) > 10:
                    return result
                print(f"[{member['name']}] 응답 비정상(시도 {attempt+1}): {repr(result)}")
            except Exception as ex:
                last_ex = ex
                print(f"[{member['name']}] 발언 실패(시도 {attempt+1}): {ex}")
                if attempt == 0:
                    await asyncio.sleep(2)
        print(f"[{member['name']}] 2회 모두 실패: {last_ex}")
        return f"{member['name']} 의원은 신중한 검토가 필요하다고 봅니다."

    # ══════════════════════════════════════════════
    # 의원 발언 처리 공통 헬퍼
    # (발언 생성 → 과다 시 의장 개입 → 반박 신청 처리)
    # ══════════════════════════════════════════════
    async def prepare_speech(
        self,
        chair: dict,
        member: dict,
        fmt_guide: str,
        round_num: int,
        is_rebuttal: bool = False,
        target_speech: str = None,
        free_mode: bool = False,
    ) -> tuple:
        """
        API 호출만 수행 (전송 없음) — 파이프라인 1단계.
        반환: (opinion, stype)
        """
        await self.send("status", message=f"⏳ {member['name']} 의원 발언 준비 중...")
        opinion = await self.get_opinion(
            member, chair["name"],
            format_guide=fmt_guide,
            round_num=round_num,
            is_rebuttal=is_rebuttal,
            target_speech=target_speech,
            free_mode=free_mode,
        )
        stype = self.detect_type(opinion)
        # ctx에 push — 다음 의원 API 호출 시 이 발언이 컨텍스트에 포함됨
        self.memories[member["id"]].append(opinion)
        self.ctx.push(f"[{member['name']} 의원]", opinion)
        await self.ctx.compress_if_needed()
        return opinion, stype

    async def deliver_speech(
        self,
        chair: dict,
        member: dict,
        opinion: str,
        stype: str,
        non_chair: list,
        fmt_guide: str,
        round_num: int,
        free_mode: bool = False,
    ) -> str:
        """
        전송 + ACK 대기 — 파이프라인 2단계.
        반환: opinion 원문
        """
        # 500자 초과 시 의장 개입
        if len(opinion) > MAX_SPEECH_LEN:
            await self.send_speech(member, opinion, stype, False)
            await self._wait_for_ready()
            intervene = await self.chair_intervene(chair, member)
            self.ctx.push(f"[의장 {chair['name']}]", intervene)
            await self.send_speech(chair, intervene, "NORMAL", True, skip_wait=True)
        else:
            await self.send_speech(member, opinion, stype, False)
            await self._wait_for_ready()

        # [REFUTE] 처리 (자유토론·마지막 라운드 제외)
        if stype == "REFUTE" and not free_mode and round_num < self.rounds:
            await self._handle_rebuttal_request(
                chair, member, opinion, non_chair, fmt_guide, round_num
            )
        return opinion

    async def do_speech(
        self,
        chair: dict,
        member: dict,
        fmt_guide: str,
        round_num: int,
        non_chair: list,
        is_rebuttal: bool = False,
        target_speech: str = None,
        free_mode: bool = False,
    ) -> str:
        """
        prepare + deliver 통합 (파이프라인 불필요한 경우에 사용).
        """
        opinion, stype = await self.prepare_speech(
            chair, member, fmt_guide, round_num,
            is_rebuttal=is_rebuttal,
            target_speech=target_speech,
            free_mode=free_mode,
        )
        return await self.deliver_speech(
            chair, member, opinion, stype,
            non_chair, fmt_guide, round_num, free_mode=free_mode,
        )

    # ══════════════════════════════════════════════
    # 반박 신청 처리
    # ══════════════════════════════════════════════
    async def _handle_rebuttal_request(
        self,
        chair: dict,
        refuter: dict,        # 방금 [REFUTE]한 의원
        refute_speech: str,   # 해당 발언 원문
        non_chair: list,
        fmt_guide: str,
        round_num: int,
    ):
        """
        [REFUTE] 감지 후:
        1. 반박 대상이 될 의원(직전 발언자 우선)을 찾아 반박 신청
        2. 의장이 허가 여부 자율 판단
        3. 허가 시 해당 의원 반박 발언 생성
        """
        # 직전 발언자 중 refuter가 아닌 의원 찾기
        candidate = None
        for log in reversed(self.ctx.all_logs[:-1]):  # 방금 발언 제외
            for m in non_chair:
                if m["name"] in log.get("speaker", "") and m["id"] != refuter["id"]:
                    candidate = m
                    break
            if candidate:
                break
        if not candidate:
            candidates = [m for m in non_chair if m["id"] != refuter["id"]]
            if not candidates:
                return
            candidate = random.choice(candidates)

        # 반박 신청 알림
        request_notice = (
            f"{candidate['name']} 의원님이 반박을 신청합니다."
        )
        self.ctx.push(f"[{candidate['name']} 의원]", request_notice)
        await self.send_speech(candidate, request_notice, "NORMAL", False, skip_wait=True)

        # 의장 허가 판단
        allow, judge_speech = await self.chair_judge_rebuttal(
            chair, candidate, refute_speech
        )
        self.ctx.push(f"[의장 {chair['name']}]", judge_speech)
        await self.send_speech(chair, judge_speech, "NORMAL", True, skip_wait=True)

        if not allow:
            return

        # 허가된 경우 반박 발언 생성
        await self.send("status", message=f"⏳ {candidate['name']} 의원 반박 준비 중...")
        rebuttal = await self.get_opinion(
            candidate, chair["name"],
            format_guide=fmt_guide,
            round_num=round_num,
            is_rebuttal=True,
            target_speech=refute_speech,
        )
        rstype = self.detect_type(rebuttal)
        self.memories[candidate["id"]].append(rebuttal)
        self.ctx.push(f"[{candidate['name']} 의원]", rebuttal)
        await self.send_speech(candidate, rebuttal, rstype, False)
        await self.ctx.compress_if_needed()

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

    async def run_conclusion(self, chair: dict, timed_out: bool = False):
        """최종 의결 공통 처리"""
        await asyncio.sleep(3.0)  # 마지막 발언 완전 전달 대기

        elapsed = int(self._elapsed_minutes())
        if timed_out:
            close_instruction = (
                f"토론 시간 {elapsed}분이 경과하였습니다. "
                "예정된 토론 시간이 종료되어 더 이상의 발언을 받지 않겠습니다. "
                f"지금까지의 논의를 바탕으로 {'찬반 표결' if self.conclusion_type == 'VOTE' else '공동 결의안 채택'}을 실시하겠습니다."
            )
        elif self.debate_format == "자유토론":
            close_instruction = (
                f"자유토론이 종료되었습니다. "
                f"{'찬반 표결' if self.conclusion_type == 'VOTE' else '공동 결의안 채택'}을 실시하겠습니다."
            )
        else:
            close_instruction = (
                f"총 {self.rounds}라운드의 토론이 완료되었습니다. 최종 의결을 진행하겠습니다."
            )
        close_text = await self.chair_speak(chair, close_instruction, max_chars=CHAIR_MAX_LEN)
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
    # ACK 수신 루프 (백그라운드 태스크)
    # ══════════════════════════════════════════════
    async def _ack_listener(self):
        """프론트에서 오는 'ready' 메시지를 수신해 이벤트 세트"""
        try:
            while True:
                data = await self.ws.receive_text()
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ready":
                        self._ready_event.set()
                except Exception:
                    pass
        except Exception:
            self._ready_event.set()

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

        asyncio.create_task(self._ack_listener())

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
            f"【릴레이 형식 규칙】\n"
            "의장이 지목한 순서대로 한 명씩 발언합니다.\n"
            "지목되지 않은 의원은 절대 끼어들 수 없습니다.\n"
            f"발언은 최대 {MAX_SPEECH_LEN}자이며, 지목 즉시 발언을 시작하세요."
        )

        self.start_time = time.time()

        non_chair = [m for m in self.members if m["id"] != chair["id"]]
        first_member = non_chair[0] if non_chair else None

        # 개회사 + 1라운드 선언 + 첫 의원 지목을 하나로 통합
        open_instruction = (
            f"안건 \"{self.issue}\"에 대한 릴레이 토론을 개회합니다. "
            f"총 {self.rounds}라운드로 진행되며, 의장 지목 순서에 따라 균등하게 발언합니다. "
            f"1라운드를 시작합니다."
        )
        if first_member:
            open_instruction += f" 첫 번째로 {first_member['name']} 의원님, 의견을 개진해 주십시오."
        open_text = await self.chair_speak(chair, open_instruction, max_chars=CHAIR_MAX_LEN + 50)
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        # chair_announce_round는 2라운드부터만 호출, 안전을 위해 유지
        self._first_relay_member_name = first_member["name"] if first_member else ""
        _next_order_override = None  # 라운드 전환 시 미리 결정된 다음 라운드 순서

        for round_num in range(1, self.rounds + 1):
            self.current_round = round_num

            if self._time_over():
                print(f"[Engine] 릴레이 시간 초과 ({self._elapsed_minutes():.1f}분) — 의결로 이동")
                await self.run_conclusion(chair, timed_out=True)
                return

            # 발언 순서 결정
            # - 1라운드: 개회사 기준 순서 (첫 의원 이미 지목됨)
            # - 2라운드~: 전환 발언에서 이미 결정된 순서 사용 (override)
            if round_num == 1:
                order = non_chair.copy()
            elif _next_order_override is not None:
                order = _next_order_override
                _next_order_override = None
            else:
                order = non_chair.copy()
                random.shuffle(order)

            # ── 파이프라인: 현재 의원 TTS 재생 중 다음 의원 API 미리 호출 ──
            # prefetch_task: (opinion, stype) future, prefetch_member: 해당 의원
            prefetch_task  = None
            prefetch_member = None

            for idx, m in enumerate(order):
                if self._time_over():
                    if prefetch_task:
                        prefetch_task.cancel()
                    await self.run_conclusion(chair, timed_out=True)
                    return

                # 1라운드 첫 번째 의원은 개회사에서 이미 지목
                # 2라운드~ 첫 번째 의원은 전환 발언에서 이미 지목
                if idx == 0:
                    pass  # 지목 생략
                else:
                    nominate = f"{m['name']} 의원님, 발언해 주시기 바랍니다."
                    self.ctx.push(f"[의장 {chair['name']}]", nominate)
                    await self.send_speech(chair, nominate, "NORMAL", True, skip_wait=True)

                # ── 현재 의원 발언 준비 ──
                if prefetch_task is None or prefetch_member is None or prefetch_member["id"] != m["id"]:
                    # prefetch 없거나 다른 의원용이면 직접 호출
                    if prefetch_task is not None:
                        prefetch_task.cancel()
                        prefetch_task = None
                    cur_opinion, cur_stype = await self.prepare_speech(
                        chair, m, fmt_guide, round_num
                    )
                else:
                    # 올바른 prefetch 결과 수거
                    cur_opinion, cur_stype = await prefetch_task
                    prefetch_task = None

                # 다음 의원 API 미리 호출 (백그라운드) — deliver와 동시 진행
                next_idx = idx + 1
                if next_idx < len(order) and not self._time_over():
                    next_m = order[next_idx]
                    prefetch_member = next_m
                    prefetch_task = asyncio.create_task(
                        self.prepare_speech(chair, next_m, fmt_guide, round_num)
                    )
                else:
                    prefetch_task  = None
                    prefetch_member = None

                # 현재 의원 발언 전송 + ACK 대기 (TTS 재생 중 next API 호출 진행)
                await self.deliver_speech(
                    chair, m, cur_opinion, cur_stype,
                    non_chair, fmt_guide, round_num
                )

            # 라운드 전환 (마지막 라운드 제외 — 전환 발언에 다음 라운드 선언 + 첫 의원 지목 통합)
            if round_num < self.rounds:
                elapsed_ratio = self._elapsed_minutes() / self.duration if self.duration > 0 else 1.0

                if elapsed_ratio >= 0.85 and round_num < self.rounds - 1:
                    # 시간 85% 이상 → 최종 라운드 직행
                    final_order = non_chair.copy()
                    random.shuffle(final_order)
                    final_first = final_order[0] if final_order else None

                    final_text = await self.chair_announce_round(chair, self.rounds, first_member=final_first)
                    self.ctx.push(f"[의장 {chair['name']}]", final_text)
                    await self.send_speech(chair, final_text, "NORMAL", True)

                    _final_timed_out = False
                    _fpf = None
                    _fpf_member = None
                    for _fi, fm in enumerate(final_order):
                        if self._time_over():
                            if _fpf: _fpf.cancel()
                            _final_timed_out = True
                            break
                        # 첫 번째 의원은 최종라운드 선언에서 이미 지목됨 — nominate 생략
                        if _fi > 0:
                            fn = f"{fm['name']} 의원님, 발언해 주시기 바랍니다."
                            self.ctx.push(f"[의장 {chair['name']}]", fn)
                            await self.send_speech(chair, fn, "NORMAL", True, skip_wait=True)
                        if _fpf is None or _fpf_member is None or _fpf_member["id"] != fm["id"]:
                            if _fpf is not None:
                                _fpf.cancel()
                            _fop, _fst = await self.prepare_speech(chair, fm, fmt_guide, self.rounds)
                        else:
                            _fop, _fst = await _fpf
                            _fpf = None
                        _ni = _fi + 1
                        if _ni < len(final_order) and not self._time_over():
                            _fpf_member = final_order[_ni]
                            _fpf = asyncio.create_task(self.prepare_speech(chair, _fpf_member, fmt_guide, self.rounds))
                        else:
                            _fpf = None
                            _fpf_member = None
                        await self.deliver_speech(chair, fm, _fop, _fst, non_chair, fmt_guide, self.rounds)
                    if _final_timed_out:
                        await self.run_conclusion(chair, timed_out=True)
                        return
                    break
                else:
                    # 다음 라운드의 첫 의원을 미리 결정해서 전환 발언에 통합
                    next_order = non_chair.copy()
                    random.shuffle(next_order)
                    next_first = next_order[0] if next_order else None
                    transition = await self.chair_transition_round(chair, round_num, first_member=next_first)
                    self.ctx.push(f"[의장 {chair['name']}]", transition)
                    await self.send_speech(chair, transition, "NORMAL", True)
                    # 다음 라운드 순서를 next_order로 교체 (이미 전환 발언에서 순서 확정)
                    # 루프의 order는 다음 iteration에서 재생성되므로 저장만 해둠
                    _next_order_override = next_order

        await self.run_conclusion(chair, timed_out=self._time_over())

    # ══════════════════════════════════════════════
    # 2. 집중토론
    # ══════════════════════════════════════════════
    async def _run_focused(self, chair: dict):
        fmt_guide = (
            f"【집중토론 형식 규칙】\n"
            "핵심 토론자 2인이 교대로 집중 대결합니다.\n"
            "나머지 의원은 질의 시간에만 발언할 수 있습니다.\n"
            f"핵심 토론자는 반드시 상대방 발언에 직접 반박해야 합니다. 최대 {MAX_SPEECH_LEN}자."
        )

        self.start_time = time.time()
        non_chair = [m for m in self.members if m["id"] != chair["id"]]

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
            f"{self.rounds}라운드에 걸쳐 집중 대결하며, 나머지 의원께서는 이후 질의 시간까지 대기해 주십시오. "
            f"1라운드를 시작합니다. 먼저 {debaters[0]['name']} 의원님, 발언해 주십시오.",
            max_chars=CHAIR_MAX_LEN + 50,
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        _focused_next_pair = None  # 라운드 전환 시 미리 결정된 다음 라운드 순서

        for round_num in range(1, self.rounds + 1):
            self.current_round = round_num

            if self._time_over():
                await self.run_conclusion(chair, timed_out=True)
                return

            # 1라운드 선언은 개회사에 포함 — 별도 announce 생략
            # 2라운드~는 transition 발언이 선언 + 첫 지목까지 포함했음
            if round_num == 1:
                pair = debaters.copy()
            elif _focused_next_pair is not None:
                pair = _focused_next_pair
                _focused_next_pair = None
            else:
                pair = debaters.copy()
                random.shuffle(pair)

            _pf = None
            _pf_member = None
            for _pi, m in enumerate(pair):
                # 1라운드는 개회사에서, 2라운드~는 전환발언에서 첫 의원 이미 지목
                if _pi == 0:
                    pass  # 첫 번째 지목 생략
                else:
                    nominate = f"{m['name']} 의원님, 발언해 주십시오."
                    self.ctx.push(f"[의장 {chair['name']}]", nominate)
                    await self.send_speech(chair, nominate, "NORMAL", True, skip_wait=True)
                if _pf is None or _pf_member is None or _pf_member["id"] != m["id"]:
                    if _pf is not None:
                        _pf.cancel()
                    _po, _ps = await self.prepare_speech(chair, m, fmt_guide, round_num)
                else:
                    _po, _ps = await _pf
                    _pf = None
                _ni = _pi + 1
                if _ni < len(pair) and not self._time_over():
                    _pf_member = pair[_ni]
                    _pf = asyncio.create_task(self.prepare_speech(chair, _pf_member, fmt_guide, round_num))
                else:
                    _pf = None
                    _pf_member = None
                await self.deliver_speech(chair, m, _po, _ps, non_chair, fmt_guide, round_num)

            if round_num < self.rounds:
                # 다음 라운드 첫 발언자 미리 결정 → 전환 발언에 통합
                _focused_next_pair = debaters.copy()
                random.shuffle(_focused_next_pair)
                next_first = _focused_next_pair[0] if _focused_next_pair else None
                transition = await self.chair_transition_round(chair, round_num, first_member=next_first)
                self.ctx.push(f"[의장 {chair['name']}]", transition)
                await self.send_speech(chair, transition, "NORMAL", True)

        # 질의 시간
        if observers:
            qa_open = await self.chair_speak(
                chair,
                "핵심토론이 완료되었습니다. 나머지 의원님들의 질의 시간입니다.",
                max_chars=CHAIR_MAX_LEN,
            )
            self.ctx.push(f"[의장 {chair['name']}]", qa_open)
            await self.send_speech(chair, qa_open, "NORMAL", True)

            random.shuffle(observers)
            _obs_timed_out = False
            _of = None
            for _oi, m in enumerate(observers):
                if self._time_over():
                    if _of: _of.cancel()
                    _obs_timed_out = True
                    break
                nominate = f"{m['name']} 의원님, 질의해 주십시오."
                self.ctx.push(f"[의장 {chair['name']}]", nominate)
                await self.send_speech(chair, nominate, "NORMAL", True, skip_wait=True)
                if _of is None:
                    _oo, _os2 = await self.prepare_speech(chair, m, fmt_guide, 2)
                else:
                    _oo, _os2 = await _of
                _ni = _oi + 1
                if _ni < len(observers) and not self._time_over():
                    _of = asyncio.create_task(self.prepare_speech(chair, observers[_ni], fmt_guide, 2))
                else:
                    _of = None
                await self.deliver_speech(chair, m, _oo, _os2, non_chair, fmt_guide, 2)

        await self.run_conclusion(chair, timed_out=_obs_timed_out if observers else False)

    # ══════════════════════════════════════════════
    # 3. 전문가패널
    # ══════════════════════════════════════════════
    async def _run_panel(self, chair: dict):
        fmt_guide = (
            f"【전문가패널 형식 규칙】\n"
            "전문가 패널이 먼저 심층 발언합니다.\n"
            "패널 발언 후 전체 질의·응답 시간이 진행됩니다.\n"
            f"패널 발언 중 다른 의원의 개입은 허용하지 않습니다. 최대 {MAX_SPEECH_LEN}자."
        )

        self.start_time = time.time()
        non_chair   = [m for m in self.members if m["id"] != chair["id"]]
        panel_count = max(1, min(3, len(non_chair) // 2))
        panels      = random.sample(non_chair, panel_count)
        general     = [m for m in non_chair if m not in panels]
        p_names     = ", ".join(f"{p['name']} 의원" for p in panels)

        open_text = await self.chair_speak(
            chair,
            f"안건 \"{self.issue}\"에 대한 전문가패널 토론을 개회합니다. "
            f"전문가 패널로 {p_names}을 선정했습니다. "
            "심층 발언 후 전체 질의·응답을 진행합니다. "
            "패널 발언 중에는 다른 의원의 개입을 엄격히 금합니다.",
            max_chars=CHAIR_MAX_LEN,
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        panel_start = await self.chair_speak(
            chair,
            "패널 심층 발언을 시작합니다. 각 패널은 전문 분야의 깊이 있는 분석을 제시해 주십시오.",
            max_chars=120,
        )
        self.ctx.push(f"[의장 {chair['name']}]", panel_start)
        await self.send_speech(chair, panel_start, "NORMAL", True)

        _pnf = None
        for _pni, m in enumerate(panels):
            if self._time_over():
                if _pnf: _pnf.cancel()
                await self.run_conclusion(chair, timed_out=True)
                return
            nominate = f"패널 {m['name']} 의원님, 전문가 발언을 시작해 주십시오."
            self.ctx.push(f"[의장 {chair['name']}]", nominate)
            await self.send_speech(chair, nominate, "NORMAL", True, skip_wait=True)
            if _pnf is None:
                _pno, _pns = await self.prepare_speech(chair, m, fmt_guide, 1)
            else:
                _pno, _pns = await _pnf
            _ni = _pni + 1
            if _ni < len(panels) and not self._time_over():
                _pnf = asyncio.create_task(self.prepare_speech(chair, panels[_ni], fmt_guide, 1))
            else:
                _pnf = None
            await self.deliver_speech(chair, m, _pno, _pns, non_chair, fmt_guide, 1)

        summary = await self.chair_speak(
            chair,
            "패널 발언이 완료되었습니다. 전체 질의·응답 시간을 시작합니다.",
            max_chars=CHAIR_MAX_LEN,
        )
        self.ctx.push(f"[의장 {chair['name']}]", summary)
        await self.send_speech(chair, summary, "NORMAL", True)

        qa_rounds = max(1, self.rounds - 1)
        for qa_round in range(1, qa_rounds + 1):
            if self._time_over():
                break
            if general:
                qa_text = await self.chair_speak(
                    chair,
                    f"전체 질의·응답 {qa_round}라운드를 시작합니다.",
                    max_chars=100,
                )
                self.ctx.push(f"[의장 {chair['name']}]", qa_text)
                await self.send_speech(chair, qa_text, "NORMAL", True)

                shuffled_gen = general.copy()
                random.shuffle(shuffled_gen)
                _gf = None
                for _gi, m in enumerate(shuffled_gen):
                    if self._time_over():
                        if _gf: _gf.cancel()
                        break
                    nominate = f"{m['name']} 의원님, 패널에 질의해 주십시오."
                    self.ctx.push(f"[의장 {chair['name']}]", nominate)
                    await self.send_speech(chair, nominate, "NORMAL", True, skip_wait=True)
                    if _gf is None:
                        _go, _gs = await self.prepare_speech(chair, m, fmt_guide, 2)
                    else:
                        _go, _gs = await _gf
                    _ni = _gi + 1
                    if _ni < len(shuffled_gen) and not self._time_over():
                        _gf = asyncio.create_task(self.prepare_speech(chair, shuffled_gen[_ni], fmt_guide, 2))
                    else:
                        _gf = None
                    await self.deliver_speech(chair, m, _go, _gs, non_chair, fmt_guide, 2)

            _rf = None
            for _ri, p in enumerate(panels):
                if self._time_over():
                    if _rf: _rf.cancel()
                    break
                nominate = f"패널 {p['name']} 의원님, 질의에 응답해 주십시오."
                self.ctx.push(f"[의장 {chair['name']}]", nominate)
                await self.send_speech(chair, nominate, "NORMAL", True, skip_wait=True)
                if _rf is None:
                    _ro, _rs = await self.prepare_speech(chair, p, fmt_guide, 2)
                else:
                    _ro, _rs = await _rf
                _ni = _ri + 1
                if _ni < len(panels) and not self._time_over():
                    _rf = asyncio.create_task(self.prepare_speech(chair, panels[_ni], fmt_guide, 2))
                else:
                    _rf = None
                await self.deliver_speech(chair, p, _ro, _rs, non_chair, fmt_guide, 2)

        await self.run_conclusion(chair, timed_out=self._time_over())

    # ══════════════════════════════════════════════
    # 4. 자유토론
    # ══════════════════════════════════════════════
    async def _run_free(self, chair: dict):
        fmt_guide = (
            f"【자유토론 형식 규칙】\n"
            "순서 제한 없이 누구든 자유롭게 발언합니다.\n"
            "앞선 발언들을 면밀히 검토하고 논리적 타당성만으로 반응을 선택하세요.\n"
            "새로운 근거·데이터([DATA]), 시각화([GRAPHIC]), 표([TABLE])를 적극 활용하세요.\n"
            f"시간 또는 발언 수 한도 종료 후 의장이 즉시 최종 의결을 진행합니다. 최대 {MAX_SPEECH_LEN}자."
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
            max_chars=CHAIR_MAX_LEN,
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        warned    = False
        turn      = 0
        non_chair = [m for m in self.members if m["id"] != chair["id"]]

        while not self._time_over() and turn < self.max_free_turns:
            elapsed = self._elapsed_minutes()

            if not warned and elapsed >= warn_threshold:
                warned = True
                remaining_sec   = int((deadline_mins - elapsed) * 60)
                remaining_turns = self.max_free_turns - turn
                warn_text = await self.chair_speak(
                    chair,
                    f"시간 알림: 자유토론 종료까지 약 {remaining_sec}초, "
                    f"잔여 발언 {remaining_turns}회 남았습니다. "
                    "핵심 주장을 마무리해 주시기 바랍니다.",
                    max_chars=140,
                )
                self.ctx.push(f"[의장 {chair['name']}]", warn_text)
                await self.send_speech(chair, warn_text, "NORMAL", True)
                if self._time_over() or turn >= self.max_free_turns:
                    break

            # 발언자 선택 (엔진이 관리, 최근 발언자 제외)
            speaker = None
            if self.ctx.all_logs:
                last_log  = self.ctx.all_logs[-1]
                last_text = last_log.get("text", "")
                if "[REFUTE]" in last_text:
                    candidates = [m for m in non_chair if m["name"] not in last_log.get("speaker","")]
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
                        f"({int(elapsed)}분 {int((elapsed % 1)*60)}초 / {deadline_mins}분 "
                        f"| {turn+1}/{self.max_free_turns}회)")

            # prepare: API 호출 (컨텍스트 반영)
            _fo, _fs = await self.prepare_speech(chair, speaker, fmt_guide, 1, free_mode=True)

            # 다음 발언자 미리 선택 & API 호출 (deliver와 병렬)
            _next_task = None
            if not self._time_over() and turn + 1 < self.max_free_turns:
                # 다음 발언자 선택 로직 (현재 speaker 제외)
                _recent = set()
                for _lg in self.ctx.all_logs[-2:]:
                    for _nm in non_chair:
                        if _nm["name"] in _lg.get("speaker", ""):
                            _recent.add(_nm["id"])
                _recent.add(speaker["id"])
                _fresh = [_nm for _nm in non_chair if _nm["id"] not in _recent]
                _next_speaker = random.choice(_fresh) if _fresh else random.choice(non_chair)
                _next_task = asyncio.create_task(
                    self.prepare_speech(chair, _next_speaker, fmt_guide, 1, free_mode=True)
                )

            # deliver: 전송 + ACK 대기 (위 _next_task와 병렬 진행)
            await self.deliver_speech(chair, speaker, _fo, _fs, non_chair, fmt_guide, 1, free_mode=True)
            turn += 1

            # 다음 발언자 prefetch 결과 보관 (다음 루프에서 바로 사용)
            if _next_task and not self._time_over() and turn < self.max_free_turns:
                # 결과를 ctx에 미리 등록하지 않고 태스크만 보관
                # → 루프 상단 speaker 선택을 건너뛰고 바로 사용
                _fo2, _fs2 = await _next_task
                # deliver
                if not self._time_over() and turn < self.max_free_turns:
                    await self.send("status",
                        message=f"[자유토론] {_next_speaker['name']} 의원 발언 준비 완료 "
                                f"| {turn+1}/{self.max_free_turns}회)")
                    await self.deliver_speech(chair, _next_speaker, _fo2, _fs2, non_chair, fmt_guide, 1, free_mode=True)
                    turn += 1
                    speaker = _next_speaker  # 루프 상단 중복 선택 방지용 힌트 (실제 선택은 루프가 담당)

            # 매 5발언마다 의장 중간 정리
            if turn % 5 == 0 and not self._time_over() and turn < self.max_free_turns:
                inter = await self.chair_speak(
                    chair,
                    "잠시 중간 정리를 하겠습니다. 현재까지의 주요 찬반 논점을 요약하고 자유토론을 계속합니다.",
                    max_chars=160,
                )
                self.ctx.push(f"[의장 {chair['name']}]", inter)
                await self.send_speech(chair, inter, "NORMAL", True, skip_wait=True)

        print(f"[Engine] 자유토론 종료: {turn}회 발언 / {self._elapsed_minutes():.1f}분 경과")
        await self.run_conclusion(chair, timed_out=self._time_over())