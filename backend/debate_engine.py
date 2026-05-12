"""
AI Congress Debate Engine — v2 (턴 기반 · ACK 제거)

[v2 변경 사항]
  1. 시간 제한 완전 제거 → 턴(발언 횟수) 기반으로 전환
     - duration(분) 파라미터 폐지, max_turns(총 발언 횟수) 파라미터 도입
     - _time_over() / _elapsed_minutes() 제거 → _turns_over() 로 대체
     - start_time 불필요하므로 제거

  2. TTS ACK 대기 로직 전면 제거
     - _wait_for_ready() 제거
     - _ack_listener() 제거
     - send_speech()의 ackSeq, _pending_seq, _ack_seq, _ready_event 제거
     - 모든 await self._wait_for_ready(...) 호출 제거
     - run()의 ack_task 생성/취소 제거
     - 텍스트는 즉시 전송, 음성은 토론 완료 후 프론트에서 독립 재생

  3. 시간 기반 경고 → 턴 기반 경고
     - 자유토론: 80% 턴 소진 시 잔여 턴 경고

  4. 라운드 수: max_turns에서 자동 계산
     - 15턴 이하 → 1라운드
     - 16~35턴   → 2라운드
     - 36턴 이상  → 3라운드

  5. 개회사에서 "XX분" 언급 제거, 턴/라운드 안내로 교체
"""

import json
import asyncio
import random
import re
import time
import datetime
from fastapi import WebSocket
from members import MEMBERS
from ai_caller import call_member, call_research, call_research_targeted
from debate_context import DebateContext
from conviction_tracker import ConvictionTracker

# ─────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────
MAX_ROUNDS     = 3
MIN_ROUNDS     = 1
MAX_SPEECH_LEN = 500   # 의원 발언 최대 글자수
CHAIR_MAX_LEN  = 200   # 의장 사회 발언 최대 글자수

# 턴 → 라운드 수 매핑
def _calc_rounds(max_turns: int) -> int:
    if max_turns <= 15:
        return 1
    elif max_turns <= 35:
        return 2
    else:
        return 3


class DebateEngine:
    def __init__(
        self,
        issue: str,
        max_turns: int,
        ws: WebSocket,
        debate_format: str = "릴레이",
        conclusion_type: str = "VOTE",
        active_members: list = None,
    ):
        self.issue           = issue
        self.max_turns       = max(5, max_turns)   # 최소 5턴 보장
        self.ws              = ws
        self.ctx             = DebateContext()
        self.debate_format   = debate_format
        self.conclusion_type = conclusion_type

        # 총 발언 턴 카운터 (의장 사회 발언 제외, 의원 발언만 카운트)
        self._turn_count: int = 0

        if active_members and len(active_members) >= 2:
            self.members = [m for m in MEMBERS if m["id"] in active_members]
            if len(self.members) < 2:
                self.members = list(MEMBERS)
        else:
            self.members = list(MEMBERS)

        self.member_map    = {m["id"]: m for m in self.members}
        self.memories      = {m["id"]: [] for m in self.members}
        self.speech_count  = {m["id"]: 0 for m in self.members}
        self.current_round = 0

        self._stance_map: dict = {}
        self._chair_final_spoke: bool = False
        self.conviction = ConvictionTracker(self.members, issue)

        # 사전 리서치 결과 캐시 — member_id → 리서치 요약 텍스트
        self.research_cache: dict[str, str] = {m["id"]: "" for m in self.members}

        # 내면화(deliberation) 결과 캐시 — member_id → 숙고 텍스트
        self.deliberation_cache: dict[str, str] = {m["id"]: "" for m in self.members}

        # 중간 즉석 학습 쿨다운 — member_id → 마지막 중간 학습 턴 번호
        # 동일 의원이 매 발언마다 리서치하지 않도록 최소 3턴 간격 보장
        self._mid_research_last_turn: dict[str, int] = {m["id"]: -99 for m in self.members}
        # 의원별 누적 중간 학습 결과 로그
        self._mid_research_log: dict[str, list] = {m["id"]: [] for m in self.members}

        # 라운드 수 자동 계산
        self.rounds = _calc_rounds(self.max_turns)

        # 자유토론 최대 턴 = max_turns 그대로 사용
        self.max_free_turns = self.max_turns

        print(
            f"[Engine] 참여 의원 {len(self.members)}명 / "
            f"최대 {self.max_turns}턴 / {self.rounds}라운드 / "
            f"형식: {self.debate_format}"
        )

        self.member_list_str = "\n".join(f"- {m['name']}" for m in self.members)

    # ══════════════════════════════════════════════
    # 턴 카운터
    # ══════════════════════════════════════════════
    def _increment_turn(self):
        """의원 발언 1회 완료 시 호출"""
        self._turn_count += 1

    def _turns_over(self) -> bool:
        """총 발언 턴이 한도에 도달했는지 확인"""
        return self._turn_count >= self.max_turns

    def _turns_remaining(self) -> int:
        return max(0, self.max_turns - self._turn_count)

    # ══════════════════════════════════════════════
    # 중간 즉석 학습 — 모르는 용어/주장 감지 & 리서치
    # ══════════════════════════════════════════════
    async def _detect_unknown_terms(self, member: dict, trigger_speech: str) -> list:
        """
        직전 발언에서 해당 의원이 이해하기 어려울 가능성이 있는
        전문 용어·낯선 주장·미지의 데이터 출처를 경량 LLM으로 감지.
        반환: 감지된 용어/주장 리스트 (최대 3개). 실패 시 빈 리스트.
        """
        import json as _json
        from ai_caller import call_groq as _cg, call_gemini as _cgm

        member_name = member["name"]
        lens        = member.get("lens", "")
        bias        = member.get("bias", "중립")

        prompt = (
            f"당신은 토론 분석 AI입니다.\n"
            f"아래 발언을 {member_name}({lens}, {bias} 성향) 의원의 관점에서 분석하세요.\n\n"
            f"발언:\n\"{trigger_speech[:500]}\"\n\n"
            f"{member_name}의 학습 기반과 이념 성향을 고려했을 때,\n"
            "이 발언에서 해당 의원이 모르거나 이해가 부족할 가능성이 높은 것을 찾으세요:\n"
            "- 낯선 전문 용어 또는 약어\n"
            "- 처음 언급되는 정책·제도명\n"
            "- 출처 불명의 구체적 수치\n"
            "- 의원의 전문 분야 밖의 주장\n\n"
            "반드시 JSON 배열로만 응답하세요 (최대 3개, 없으면 빈 배열):\n"
            "[\"용어1\", \"용어2\"] 또는 []"
        )
        msgs = [
            {"role": "system", "content": "토론 감지 AI. JSON 배열로만 응답."},
            {"role": "user", "content": prompt},
        ]
        for caller_fn in (
            lambda m: _cg(m, temperature=0.1, max_tokens=80),
            lambda m: _cgm(m, temperature=0.1, max_tokens=80),
        ):
            try:
                raw = await caller_fn(msgs)
                s = raw.find('['); e = raw.rfind(']')
                if s != -1 and e != -1:
                    parsed = _json.loads(raw[s:e+1])
                    terms = [str(t).strip() for t in parsed if str(t).strip()]
                    if terms:
                        print(f"[MidResearch] {member_name} 감지 용어: {terms}")
                    return terms[:3]
            except Exception as ex:
                print(f"[MidResearch] 감지 실패 ({member_name}): {ex}")
        return []

    async def mid_debate_research(self, member: dict, trigger_speech: str) -> bool:
        """
        토론 중 한 발언을 듣고, 해당 의원이 모르는 용어/주장이 있으면
        즉석 리서치 + 내면화를 수행하여 research_cache에 병합.
        반환: True=학습 실행됨, False=불필요하거나 쿨다운 중
        """
        mid = member["id"]

        # 쿨다운: 마지막 중간 학습으로부터 3턴 미만이면 건너뜀
        if self._turn_count - self._mid_research_last_turn[mid] < 3:
            return False

        unknown_terms = await self._detect_unknown_terms(member, trigger_speech)
        if not unknown_terms:
            return False

        await self.send(
            "status",
            message=f"🔍 {member['name']} 의원: '{unknown_terms[0]}' 관련 자료 즉석 학습 중...",
        )
        try:
            result = await call_research_targeted(
                issue          = self.issue,
                member_name    = member["name"],
                lens           = member.get("lens", ""),
                trigger_speech = trigger_speech,
                unknown_terms  = unknown_terms,
            )
        except Exception as e:
            print(f"[MidResearch] call_research_targeted 실패 ({member['name']}): {e}")
            return False

        if not result:
            return False

        existing  = self.research_cache.get(mid, "")
        separator = f"\n\n{'─'*40}\n"
        self.research_cache[mid] = existing + separator + result if existing else result
        self._mid_research_log[mid].append(result)
        self._mid_research_last_turn[mid] = self._turn_count

        print(
            f"[MidResearch] {member['name']} 중간 학습 완료 "
            f"(누적 {len(self._mid_research_log[mid])}회 / 턴 {self._turn_count})"
        )
        return True

    # ══════════════════════════════════════════════
    # 전송 헬퍼
    # ══════════════════════════════════════════════
    async def send(self, msg_type: str, **kwargs):
        try:
            if self.ws.client_state.value != 3:
                await self.ws.send_json({"type": msg_type, **kwargs})
        except Exception:
            pass

    async def send_speech(
        self,
        member: dict,
        text: str,
        speech_type: str,
        is_chair: bool,
    ):
        """발언을 즉시 전송한다. ACK 대기 없음 — TTS는 프론트에서 독립 재생."""
        display     = f"의장 {member['name']}" if is_chair else f"{member['name']} 의원"
        model_str   = member.get("model", "?")
        engine_info = f"{member.get('engine','?')}/{model_str.split('/')[-1]}"
        timestamp   = datetime.datetime.now().strftime("%H:%M:%S")

        await self.send(
            "speech",
            memberId    = member["id"],
            displayName = display,
            text        = text,
            speechType  = speech_type,
            engineInfo  = engine_info,
            color       = member.get("color", "#ffffff"),
            avatar      = member.get("avatar", "💬"),
            timestamp   = timestamp,
            turnCount   = self._turn_count,   # 프론트 진행 표시용
            maxTurns    = self.max_turns,
        )
        self.speech_count[member["id"]] = self.speech_count.get(member["id"], 0) + 1

    @staticmethod
    def _strip_prefix(text: str) -> str:
        if not text:
            return text
        cleaned = re.sub(r'^\s*\[[^\]]{1,30}\]\s*:\s*', '', text.strip())
        cleaned = re.sub(r'^\s*\[[^\]]{1,30}\]\s*:\s*', '', cleaned.strip())
        return cleaned.strip() if cleaned.strip() else text.strip()

    def _strip_member_intro(self, text: str) -> str:
        if not text:
            return text
        for m in self.members:
            n = re.escape(m["name"])
            for pat in [
                rf'^{n}\s*의원입니다[.!]?\s*',
                rf'^저는\s*{n}\s*의원입니다[.!]?\s*',
                rf'^본\s*의원은\s*{n}\s*의원입니다[.!]?\s*',
                rf'^안녕하세요[,\s]*{n}\s*의원입니다[.!]?\s*',
                rf'^{n}\s*의원\s*입니다[.!]?\s*',
            ]:
                text = re.sub(pat, '', text, flags=re.UNICODE).strip()
        return text.strip()

    @staticmethod
    def detect_type(text: str) -> str:
        if "[REFUTE]" in text: return "REFUTE"
        if "[ADMIT]"  in text: return "ADMIT"
        return "NORMAL"

    # ══════════════════════════════════════════════
    # 사전 리서치 단계 — 첫 발언 전 최신 정보 수집
    # ══════════════════════════════════════════════
    async def research_phase(self):
        """
        [개선] 2단계 사전 준비 파이프라인

        Phase A — 병렬 심층 리서치 (ai_caller.call_research):
            의원별 전문 렌즈로 사실수집 → 논점분석 → 전략화를 순차 수행.
            각 의원이 서로 다른 각도에서 동일 안건을 조사하므로
            토론 시 발언이 자연스럽게 다양해짐.

        Phase B — 개별 내면화 (deliberation):
            리서치 결과를 자기 이념·성향으로 소화하는 '숙고' 단계.
            "나는 이 안건에 대해 어떤 입장을 취할 것인가?"를
            LLM 스스로 결정하게 하여 첫 발언의 주체성과 깊이를 확보.
            결과는 self.deliberation_cache[member_id]에 저장.
        """
        await self.send("status", message="📡 각 AI 의원이 안건 관련 자료를 심층 수집 중입니다...")
        print(f"[Engine] 사전 리서치 시작 — {len(self.members)}명 병렬 수집")

        # ── Phase A: 병렬 심층 리서치 ──────────────────────────────
        async def _research_one(member: dict):
            mid  = member["id"]
            name = member["name"]
            lens = member.get("lens", "")
            try:
                result = await call_research(self.issue, name, lens)
                if result:
                    self.research_cache[mid] = result
                    print(f"[Research] {name}: {len(result)}자 수집 완료")
                else:
                    print(f"[Research] {name}: 결과 없음 — 학습 기반으로 진행")
            except Exception as e:
                print(f"[Research] {name}: 오류 발생 ({e}) — 학습 기반으로 진행")

        await asyncio.gather(*[_research_one(m) for m in self.members])

        collected = sum(1 for v in self.research_cache.values() if v)
        await self.send(
            "status",
            message=f"✅ 자료 수집 완료 ({collected}/{len(self.members)}명). 각 의원이 논거를 정리 중입니다...",
        )
        print(f"[Engine] Phase A 완료: {collected}/{len(self.members)}명 성공")

        # ── Phase B: 병렬 내면화(deliberation) ────────────────────
        async def _deliberate_one(member: dict):
            mid          = member["id"]
            name         = member["name"]
            bias         = member.get("bias", "중립")
            vote_tendency = member.get("vote_tendency", "")
            persona      = member.get("persona", "")
            research_txt = self.research_cache.get(mid, "")

            # 리서치 결과가 없으면 내면화 불가 — 건너뜀
            if not research_txt:
                print(f"[Deliberation] {name}: 리서치 없음 — 내면화 건너뜀")
                return

            deliberation_prompt = (
                f"토론 안건: \"{self.issue}\"\n\n"
                f"당신은 {name}({bias} 성향)입니다.\n"
                f"{persona[:200]}\n\n"
                f"[방금 수집한 리서치 자료]\n{research_txt[:1000]}\n\n"
                "위 자료를 당신의 이념·성향·지식 기반으로 소화하여 아래를 작성하세요:\n\n"
                "1. 【나의 잠정적 입장】\n"
                "   이 안건에 대한 나의 첫 번째 판단과 그 이유 (2~3문장)\n\n"
                "2. 【첫 발언에서 꺼낼 핵심 카드】\n"
                "   리서치에서 발견한 가장 강력한 논거 1개를 구체적 수치·출처와 함께\n\n"
                "3. 【내가 경계해야 할 상대의 논거】\n"
                "   상대가 꺼낼 가장 강한 반박과, 내가 준비한 재반박\n\n"
                "4. 【나만의 차별적 관점】\n"
                f"   {bias} 성향·{name}의 학습 기반에서만 나올 수 있는 고유한 시각 1가지\n\n"
                "각 항목 2~3문장으로 간결하게. 총 500자 이내."
            )

            deliberation_msgs = [
                {
                    "role": "system",
                    "content": (
                        f"당신은 {name} 의원입니다. {persona[:150]} "
                        "수집한 자료를 당신의 가치관으로 내면화하세요. "
                        "추상적 서술 금지 — 구체적 수치와 인과관계 중심으로 작성하세요."
                    ),
                },
                {"role": "user", "content": deliberation_prompt},
            ]

            try:
                result = await call_member(member, deliberation_msgs, temperature=0.4)
                if result and len(result) > 30:
                    self.deliberation_cache[mid] = result
                    print(f"[Deliberation] {name}: 내면화 완료 ({len(result)}자)")
                else:
                    print(f"[Deliberation] {name}: 응답 비정상 — 건너뜀")
            except Exception as e:
                print(f"[Deliberation] {name}: 오류 ({e}) — 건너뜀")

        await asyncio.gather(*[_deliberate_one(m) for m in self.members])

        deliberated = sum(1 for v in self.deliberation_cache.values() if v)
        await self.send(
            "status",
            message=f"🧠 논거 정리 완료 ({deliberated}/{len(self.members)}명). 토론을 시작합니다.",
        )
        print(f"[Engine] Phase B 완료: {deliberated}/{len(self.members)}명 내면화 성공")

    # ══════════════════════════════════════════════
    # 의장 사회 발언 생성
    # ══════════════════════════════════════════════
    async def chair_speak(self, chair: dict, instruction: str,
                          max_chars: int = CHAIR_MAX_LEN,
                          is_opening: bool = False) -> str:
        non_chair_names = [m["name"] for m in self.members if m["id"] != chair["id"]]
        non_chair_list_str = "\n".join(f"- {n}" for n in non_chair_names)

        if is_opening:
            opening_guide = (
                "\n【개회사 작성 지침 — 반드시 준수】\n"
                "① 안건 소개: 안건이 왜 지금 중요한지, 사회적·정책적 맥락을 2~3문장으로 밝히세요.\n"
                "② 핵심 논점 예고: 이 토론에서 다루어야 할 핵심 쟁점 2~3가지를 구체적으로 나열하세요.\n"
                "   예) '찬성 측은 ○○ 효과를 근거로, 반대 측은 ○○ 우려를 중심으로 논거를 펼칠 것입니다.'\n"
                "③ 진행 안내: 토론 형식과 라운드 수를 간략히 안내하세요. '몇 분' 언급 금지.\n"
                "④ 첫 발언자 지목: 마지막에 첫 발언자를 자연스럽게 호명하며 마무리하세요.\n"
                "⚠️ 단순 형식 안내만으로 끝내지 마세요. 안건의 실질적 내용이 반드시 담겨야 합니다.\n"
            )
        else:
            opening_guide = ""

        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 의장 {chair['name']}입니다. 현재 토론 형식: [{self.debate_format}]\n"
                    f"참여 의원 목록 (발언 지목 대상):\n{non_chair_list_str}\n\n"
                    "역할: 사회자. 반드시 지시된 사회 멘트만 출력하세요.\n"
                    f"{opening_guide}"
                    "⚠️ 절대 금지 사항:\n"
                    f"  1. 본인({chair['name']})을 발언자로 지목하거나 호명하는 것\n"
                    "  2. 의원의 발언 내용을 대신 생성하거나 이어 쓰는 것\n"
                    "  3. 개인 주장이나 의견 표명\n"
                    "  4. '몇 분' '시간' 등 시간 관련 언급\n"
                    f"발언 지목 시 반드시 위 목록에 있는 의원 이름만 사용하세요.\n"
                    f"{max_chars}자 이내. 완전한 문장으로. 공식적인 의회 어투로."
                )
            },
            {
                "role": "user",
                "content": f"안건: \"{self.issue}\"\n\n사회 지시: {instruction}\n\n의장으로서 사회 멘트만 출력하세요."
            }
        ]
        try:
            result = await call_member(chair, messages, temperature=0.3)
            cleaned = self._strip_prefix(result)
            cleaned = self._remove_self_nomination(cleaned, chair)
            if len(cleaned) > max_chars * 1.5:
                parts = re.split(r'(?<=[.!?。！？])\s+', cleaned)
                cleaned = parts[0] if parts else cleaned[:max_chars]
            return cleaned
        except Exception as e:
            print(f"[의장 사회] 실패: {e}")
            return "지금부터 발언을 시작하겠습니다."

    @staticmethod
    def _remove_self_nomination(text: str, chair: dict) -> str:
        if not text:
            return text
        name = chair["name"]
        patterns = [
            rf'{re.escape(name)}\s*(의장님?|의원님?)[\s,]*[가-힣\s]*(?:발언|말씀|의견)[가-힣\s]*(?:주십시오|주세요|해주십시오|해주세요|바랍니다)[.。]?',
            rf'먼저\s+{re.escape(name)}\s*(의장님?|의원님?)[^\n.。]*[.。]?',
            rf'{re.escape(name)}\s*(의장님?|의원님?)[^\n.。]*지목[^\n.。]*[.。]?',
        ]
        for pat in patterns:
            text = re.sub(pat, '', text, flags=re.UNICODE).strip()
        text = re.sub(r'\s{2,}', ' ', text).strip()
        return text

    # ══════════════════════════════════════════════
    # 의장 자율 판단: 반박 허가
    # ══════════════════════════════════════════════
    async def chair_judge_rebuttal(
        self,
        chair: dict,
        requester: dict,
        target_speech: str,
    ) -> tuple[bool, str]:
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
        return True, f"{requester['name']} 의원님, 반박 발언을 허가합니다."

    async def chair_intervene(self, chair: dict, speaker: dict) -> str:
        return await self.chair_speak(
            chair,
            f"{speaker['name']} 의원님, 발언이 다소 길어졌습니다. "
            "핵심 논점만 간략히 마무리해 주시기 바랍니다.",
            max_chars=80,
        )

    async def chair_announce_round(self, chair: dict, round_num: int,
                                   first_member: dict = None) -> str:
        first_name = first_member["name"] if first_member else ""
        instruction = (
            f"발언 한도 관계상 최종 {round_num}라운드로 직행합니다. "
            "전체 토론을 검토하고 최종 입장을 밝혀주십시오."
        )
        if first_name:
            instruction += f" 먼저 {first_name} 의원님, 발언해 주십시오."
        return await self.chair_speak(chair, instruction, max_chars=CHAIR_MAX_LEN)

    async def chair_transition_round(self, chair: dict, from_round: int,
                                     first_member: dict = None) -> str:
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
            # 최근 발언 요약 — 중복 방지용
            recent_texts = ""
            if self.ctx.all_logs:
                recent = self.ctx.all_logs[-4:]
                recent_texts = "\n".join(
                    f"- {l.get('speaker','?')}: {l.get('text','')[:60]}"
                    for l in recent
                )
            action_guide = (
                "【자유토론】 순서 제한 없이 자유롭게 발언하세요.\n"
                "반드시 직전 발언자의 이름을 직접 언급하며 반응하세요.\n"
                "예: '라마 의원님의 주장에서 문제점을 발견했습니다.' / '제미나이 의원님 말씀에 일부 동의하나 ...'\n"
                "논리적 허점이 있으면 [REFUTE], 더 타당한 주장엔 [ADMIT], 새 데이터면 [DATA]를 앞에 붙이세요.\n"
                "단순 의견 표명보다 구체적 수치·연구·사례([DATA])로 논거를 강화하세요.\n"
                "새로운 근거·데이터([DATA]), 차트([CHART:bar]/[CHART:line]/[CHART:pie]), 표([TABLE:json])를 적극 활용하세요.\n"
                "⚠️ 중복 금지: 아래 최근 발언에서 이미 언급된 논거는 절대 반복하지 마세요. 새로운 각도로 접근하세요.\n"
            )
            if recent_texts:
                action_guide += f"[최근 발언 요약]\n{recent_texts}\n"
            action_guide += f"발언은 {MAX_SPEECH_LEN}자 이내."
        elif is_rebuttal and target_speech:
            action_guide = (
                f"【즉석 반박】 방금 당신의 주장이 반박되었습니다:\n"
                f"\"{target_speech[:200]}\"\n"
                f"반박 의원의 이름을 직접 언급하며 이 반박의 구체적 오류를 지적하세요. {MAX_SPEECH_LEN}자 이내."
            )
        else:
            max_round = self.rounds
            if round_num == 1:
                action_guide = (
                    "【1라운드 — 입장 표명 + 데이터 기반 논거】\n"
                    "이것이 첫 발언입니다. 이 안건에 대한 본인의 입장과 핵심 근거를 명확히 밝히세요.\n"
                    "반드시 당신이 학습한 구체적 통계·연구·사례를 최소 1개 이상 [DATA] 태그로 제시하세요.\n"
                    "기관명·연도·수치를 포함한 실증 데이터가 없는 주장은 설득력이 없습니다.\n"
                    "국제 비교 사례나 국내 현황 데이터가 있다면 적극 활용하세요."
                )
                action_guide += f"\n발언은 {MAX_SPEECH_LEN}자 이내."
            elif round_num == max_round:
                action_guide = (
                    "【최종 라운드 — 심층 결론 + 데이터 종합】\n"
                    "이번이 마지막 발언 기회입니다. 반드시 다음 세 가지를 포함하세요:\n"
                    "① 이 토론에서 상대측이 제시한 논거 중 가장 강력했던 것을 의원 이름과 함께 직접 인용하세요.\n"
                    "   예: '제미나이 의원님의 처분적 법률 논거는 날카로웠습니다만…'\n"
                    "② 그 논거에 대한 최종 평가를 밝히세요 (수용하면 [ADMIT], 반박이면 [REFUTE]).\n"
                    "   반박 시에는 반드시 구체적 수치·문헌으로 근거를 보완하세요.\n"
                    "③ 당신의 최종 입장과 핵심 이유를 데이터 기반으로 강하게 마무리하세요.\n"
                    "   [DATA] 태그로 핵심 수치를 최소 1개 이상 제시하세요.\n"
                    "'반대를 유지합니다' 수준의 짧은 발언 금지. 반드시 상대 발언에 직접 반응하세요.\n"
                    "토론 전반을 종합한 깊이 있는 최종 발언을 300자 이상으로 작성하세요."
                )
                action_guide += f"\n발언은 최대 {MAX_SPEECH_LEN}자 (단, 300자 이상 권장)."
            else:
                action_guide = (
                    f"【{round_num}라운드 — 교차 검증 + 데이터 반박】\n"
                    "직전 발언자의 이름을 직접 언급하며 발언을 시작하세요.\n"
                    "예: '라마 의원님께서 언급하신 ○○ 수치에는 중요한 맹점이 있습니다.'\n"
                    "상대방 데이터·논리의 허점을 지적하거나, 더 강력한 반증 데이터를 제시하세요.\n"
                    "오류가 있으면 [REFUTE], 더 타당하면 [ADMIT], 새 데이터면 [DATA], "
                    "비교가 필요하면 [TABLE], 추세를 보여줄 때는 [GRAPHIC]을 활용하세요.\n"
                    "단순 의견 대립이 아니라 데이터와 논리로 승부하세요."
                )
                action_guide += f"\n발언은 {MAX_SPEECH_LEN}자 이내."

        if not free_mode:
            last_speech_ctx = self._get_last_speech_context(member)
            if last_speech_ctx:
                action_guide = (
                    f"⚡ 직전 발언: {last_speech_ctx}\n"
                    f"위 발언에 반드시 직접 반응하세요. 이름을 언급하며 논리적 평가를 하십시오.\n\n"
                    + action_guide
                )

        persona       = member.get("persona", f"당신은 {member['name']}입니다.")
        bias          = member.get("bias", "중립")
        vote_tendency = member.get("vote_tendency", "")
        temperature   = member.get("temperature", 0.6)
        stance_guide  = self._get_stance_guide(member["id"])

        # 사전 리서치 + 내면화 + 중간 학습 결과 주입
        research_text     = self.research_cache.get(member["id"], "")
        deliberation_text = self.deliberation_cache.get(member["id"], "")
        mid_research_logs = self._mid_research_log.get(member["id"], [])
        latest_mid        = mid_research_logs[-1] if mid_research_logs else ""

        if research_text or deliberation_text or latest_mid:
            if round_num == 1 and not is_rebuttal:
                research_block = "\n\n【사전 심층 리서치 — 반드시 발언에 활용】\n"
                if research_text:
                    research_block += (
                        "▶ 수집된 핵심 사실·통계 (출처 포함):\n"
                        f"{research_text[:700]}\n\n"
                    )
                if deliberation_text:
                    research_block += (
                        "▶ 당신이 이 자료를 내면화한 결과 (나의 논거·전략):\n"
                        f"{deliberation_text[:500]}\n"
                    )
                research_block += (
                    "\n⚠️ 위 자료를 반드시 [DATA] 태그와 함께 발언에 인용하세요.\n"
                    "리서치에서 발견한 구체적 수치를 첫 문장부터 사용하세요.\n"
                    "[추정] 표시된 항목은 발언 시 '추정에 따르면'으로 표현하세요."
                )
            else:
                research_block = "\n\n【보유 논거 카드 — 아직 사용 안 한 것 우선 활용】\n"
                if deliberation_text:
                    research_block += f"{deliberation_text[:300]}\n"
                elif research_text:
                    research_block += f"{research_text[:300]}\n"
                research_block += "⚠️ 이전 라운드에서 이미 쓴 논거는 반복하지 마세요."

            # 중간 즉석 학습 결과 — 최신 1건을 우선 활용 블록으로 추가
            if latest_mid:
                research_block += (
                    "\n\n【⚡ 즉석 학습 완료 — 방금 습득한 정보 (최우선 활용)】\n"
                    f"{latest_mid[:500]}\n"
                    "⚠️ 위 즉석 학습 내용을 이번 발언에 반드시 반영하세요. "
                    "조사된 사실과 대응 전략을 [DATA] 태그와 함께 구체적으로 발언하세요."
                )
        else:
            research_block = ""

        system = (
            f"당신은 AI 의회 토론 참여자입니다.\n"
            f"당신은 {member['name']} 의원입니다.\n\n"
            f"【당신의 정체성과 지식 기반】\n"
            f"{persona}\n"
            f"{stance_guide}\n"
            f"【당신의 이념적 성향: {bias}】\n"
            "이 성향은 당신의 진짜 관점입니다. 토론 내내 일관되게 유지하세요.\n"
            "다른 의원과 성향이 다르면 자연스럽게 의견 충돌이 발생합니다 — 이것이 정상입니다.\n\n"
            f"【당신의 고유 분석 각도 — 반드시 이 렌즈로 안건을 바라보라】\n"
            f"학습 기반: {member.get('lens','')}\n"
            "위 학습 기반에서 나오는 고유한 시각으로 안건을 분석하라. "
            "다른 의원과 같은 결론을 내리더라도 반드시 이 렌즈에서 나오는 다른 근거·사례·데이터를 사용해야 한다.\n"
            "예: Google 기반 → 국제 통계·다국어 문헌 우선 / EU 법제 → 유럽 판례·규범 우선 / "
            "NVIDIA → 기술적 타당성·예측 가능성 우선 / Meta → 분권·오픈소스 관점 우선\n\n"
            "【찬반 균형 — 심층 토론을 위한 핵심 원칙】\n"
            "설령 당신이 한쪽 입장을 지지하더라도, 상대방의 최강 논거를 먼저 정확히 요약한 뒤 반박하라.\n"
            "상대 논거를 무시하거나 약하게 설정(스트로맨)하는 것은 금지. 상대 논거의 핵심을 인정하면서 "
            "왜 그럼에도 불구하고 자신의 결론이 더 타당한지를 논증하라.\n"
            f"{research_block}\n\n"
            f"【핵심 원칙 — 반드시 준수】\n"
            "1. 주장은 반드시 '근거 → 논리 → 결론' 순서로 전개하라.\n"
            "2. 확실한 것은 자신 있게, 불확실한 것은 반드시 '불확실' 또는 '추정'으로 명시하라.\n"
            "3. 직전 의원 발언에 반드시 반응하라. 무시하거나 언급조차 않는 것은 금지.\n"
            "4. 이미 나온 주장을 반복하지 말고, 당신의 학습 기반에서 나오는 고유한 관점을 추가하라.\n\n"
            "【데이터·근거 활용 — AI로서 최대한 활용하라】\n"
            "당신은 방대한 학술 논문, 통계 데이터베이스, 정책 보고서, 국제 기관 자료를 학습했습니다.\n"
            "추상적 주장보다 구체적 수치·출처·사례가 훨씬 강력합니다. 반드시 다음을 실천하세요:\n"
            "  • 수치 제시: 퍼센트, 금액, 인원, 연도, 순위 등 구체적 숫자를 포함하라.\n"
            "    예: '전체 가구의 38.4%' / 'GDP 대비 2.1%p 감소' / '2022년 기준 OECD 평균'\n"
            "  • 출처 명시: 기관명·연도를 함께 밝혀라.\n"
            "    예: 'IMF 2023 보고서에 따르면' / 'OECD Health Statistics 2022 기준'\n"
            "  • 국제 비교: 다른 나라의 사례·결과를 비교하라.\n"
            "  • [DATA] 태그: 핵심 통계는 반드시 [DATA] 태그로 강조하라.\n"
            "  • [CHART:bar]: 수치 비교가 필요할 때 막대 차트로. 예: [CHART:bar]{\"title\":\"국가별 도입 효과\",\"labels\":[\"독일\",\"한국\"],\"values\":[12.3,5.1],\"unit\":\"%\",\"source\":\"OECD(2023)\"}\n"
            "  • [CHART:line]: 추세 표시. 예: [CHART:line]{\"title\":\"연도별 변화\",\"labels\":[\"2020\",\"2021\",\"2022\"],\"values\":[2.1,3.4,4.7],\"unit\":\"%\",\"source\":\"IMF(2023)\"}\n"
            "  • [CHART:pie]: 비율 표시. 예: [CHART:pie]{\"title\":\"찬반 여론\",\"labels\":[\"찬성\",\"반대\"],\"values\":[62,38],\"unit\":\"%\",\"source\":\"갤럽(2024)\"}\n"
            "  • [TABLE:json]: 여러 항목 비교. 예: [TABLE:json]{\"title\":\"국가 비교\",\"headers\":[\"국가\",\"도입연도\",\"효과\"],\"rows\":[[\"독일\",\"2015\",\"+12.3%\"]]}\n"
            "⚠️ CHART/TABLE JSON은 반드시 태그와 같은 줄에 한 줄로 작성. 줄바꿈 금지.\n"
            "⚠️ 수치 없이는 CHART/TABLE 쓰지 말고 [DATA] 태그만 사용.\n"
"⚠️ '일부 연구에 따르면'처럼 모호한 표현만 사용하는 것은 금지.\n"
            "   구체적 기관명과 수치가 없으면 [DATA] 태그를 쓰지 마세요.\n\n"
            f"참여 의원 목록 (이 이름만 사용):\n{self.member_list_str}\n\n"
            f"【현재 토론 형식: {self.debate_format}】\n"
            f"{format_guide}\n\n"
            "발언 태그 (상황에 맞게 선택적 활용):\n"
            "[REFUTE]: 상대 논리·데이터에 명확한 오류가 있을 때. 발언 맨 앞에 한 번만.\n"
            "[ADMIT]: 상대 주장이 더 타당해서 본인 입장을 실제로 수정할 때. 발언 맨 앞에 한 번만.\n"
            "[DATA]: 구체적 수치·통계·출처를 제시할 때. 예: [DATA] IMF(2023): 한국 부채비율 GDP 대비 54.3%\n"
            "[CHART:bar]: 수치 비교 막대 차트 — 한 줄 JSON. 예: [CHART:bar]{\"title\":\"효과 비교\",\"labels\":[\"A\",\"B\"],\"values\":[12,8],\"unit\":\"%\",\"source\":\"OECD(2023)\"}\n"
            "[CHART:line]: 추세 꺾은선 — 한 줄 JSON. 예: [CHART:line]{\"title\":\"연도별 추이\",\"labels\":[\"2020\",\"2021\"],\"values\":[3.1,4.2],\"unit\":\"%\",\"source\":\"IMF\"}\n"
            "[CHART:pie]: 비율 파이 — 한 줄 JSON. 예: [CHART:pie]{\"title\":\"여론\",\"labels\":[\"찬성\",\"반대\"],\"values\":[60,40],\"unit\":\"%\",\"source\":\"갤럽\"}\n"
            "[TABLE:json]: 데이터 표 — 한 줄 JSON. 예: [TABLE:json]{\"title\":\"비교\",\"headers\":[\"국가\",\"효과\"],\"rows\":[[\"독일\",\"+12%\"]]}\n\n"
            "수학 표기 규칙 (TTS 오독 방지):\n"
            "  크거나 같다: '이상', 작거나 같다: '이하'\n"
            "  크다: '초과', 작다: '미만', 같다: '동일'\n"
            "  수식 기호(≥ ≤ > < =)를 직접 쓰지 말고 위 한글 표현을 사용하라.\n\n"
            "필수 규칙:\n"
            f"- {action_guide}\n"
            "- 이미 나온 주장 반복 금지. 당신의 학습 기반 고유의 새 관점·데이터를 추가하라.\n"
            "- 다른 의원과 같은 결론이라도 반드시 다른 근거와 다른 언어로 표현하라.\n"
            "- 반드시 마침표·느낌표·물음표로 완전히 끝내세요.\n"
            "- 자신을 '본 의원'이라 하세요. 절대로 자신의 이름을 발언 속에서 쓰지 마세요.\n"
            f"- ⚠️ 발언을 '{member['name']} 의원입니다' 또는 '저는 {member['name']}입니다' 등 자기소개로 시작하지 마세요. 바로 주장으로 시작하세요.\n"
            f"- 의장: '{chair_name} 의장님' / 다른 의원: '○○ 의원님'\n"
            f"- ⚠️ '{chair_name} 의장님께서 다음으로 발언하시길…' 등 의장에게 발언을 요구하거나 지목하는 표현 금지.\n"
            "- [ADMIT] 후에는 수정된 입장을 이후 발언에서 일관되게 유지하세요.\n"
            "- 출력 형식 엄수: 발언 내용만 바로 출력. '[이름]:' '[이름 의원]:' 같은 이름 prefix 절대 금지.\n"
        )

        messages = [
            {"role": "system", "content": system},
            *self.ctx.to_messages(),
            {"role": "user", "content": f"안건: \"{self.issue}\"\n\n지금 발언하세요."},
        ]
        last_ex = None
        for attempt in range(2):
            try:
                result = await call_member(member, messages, temperature=temperature)
                result = self._strip_prefix(result)
                result = self._strip_member_intro(result)
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
    # 발언 처리 공통 헬퍼
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
        """API 호출만 수행 (전송 없음)."""
        await self.send("status", message=f"⏳ {member['name']} 의원 발언 준비 중... ({self._turn_count+1}/{self.max_turns}턴)")

        # ── 중간 즉석 학습 ──────────────────────────────────────────────
        trigger_text = target_speech
        if not trigger_text:
            trigger_text = self._get_last_speech_context(member)
        if trigger_text and len(trigger_text) > 20:
            try:
                await self.mid_debate_research(member, trigger_text)
            except Exception as _mre:
                print(f"[MidResearch] 중간 학습 예외 무시 ({member['name']}): {_mre}")
        # ────────────────────────────────────────────────────────────────

        opinion = await self.get_opinion(
            member, chair["name"],
            format_guide=fmt_guide,
            round_num=round_num,
            is_rebuttal=is_rebuttal,
            target_speech=target_speech,
            free_mode=free_mode,
        )
        stype = self.detect_type(opinion)
        self.memories[member["id"]].append(opinion)
        self.ctx.push(f"[{member['name']} 의원]", opinion)
        await self.ctx.compress_if_needed()

        try:
            conv_result = await self.conviction.evaluate_speech(member["id"], opinion, stype)
            if conv_result.get("changes"):
                await self.send(
                    "conviction",
                    changes=conv_result["changes"],
                    all=self.conviction.get_all(),
                )
        except Exception as e:
            print(f"[ConvictionTracker] 평가 중 오류 (무시): {e}")

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
        """전송 — ACK 대기 없음. 턴 카운터 증가."""
        if len(opinion) > MAX_SPEECH_LEN:
            await self.send_speech(member, opinion, stype, False)
            self._increment_turn()
            intervene = await self.chair_intervene(chair, member)
            self.ctx.push(f"[의장 {chair['name']}]", intervene)
            await self.send_speech(chair, intervene, "NORMAL", True)
        else:
            await self.send_speech(member, opinion, stype, False)
            self._increment_turn()

        # 반박 체인: REFUTE 발언이 있고 자유토론이 아닐 때,
        # 마지막 라운드 '마지막 발언'에서도 상대방이 남아 있으면 반박 허용
        # 기존: round_num < self.rounds → 마지막 라운드 REFUTE는 반박 체인이 항상 막힘
        # 수정: 턴이 남아 있고 free_mode가 아니면 허용 (마지막 라운드도 포함)
        if stype == "REFUTE" and not free_mode and not self._turns_over():
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
        refuter: dict,
        refute_speech: str,
        non_chair: list,
        fmt_guide: str,
        round_num: int,
    ):
        candidate = None
        for log in reversed(self.ctx.all_logs[:-1]):
            spk = log.get("speaker", "")
            for m in non_chair:
                if f"[{m['name']}" in spk and m["id"] != refuter["id"]:
                    candidate = m
                    break
            if candidate:
                break

        if not candidate:
            return

        allow, judge_speech = await self.chair_judge_rebuttal(chair, refuter, refute_speech)

        notice_text = (
            f"[반박 신청] {refuter['name']} 의원이 반박을 신청했습니다."
        )
        self.ctx.push(f"[의장 {chair['name']}]", notice_text)
        await self.send_speech(chair, notice_text, "NORMAL", True)

        self.ctx.push(f"[의장 {chair['name']}]", judge_speech)
        await self.send_speech(chair, judge_speech, "NORMAL", True)

        if not allow:
            return

        # 반박 발언 생성 전에도 턴 소진 여부 재확인
        if self._turns_over():
            return

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

        try:
            conv_result = await self.conviction.evaluate_speech(candidate["id"], rebuttal, rstype)
            if conv_result.get("changes"):
                await self.send(
                    "conviction",
                    changes=conv_result["changes"],
                    all=self.conviction.get_all(),
                )
        except Exception as e:
            print(f"[ConvictionTracker] rebuttal 평가 중 오류 (무시): {e}")

        await self.send_speech(candidate, rebuttal, rstype, False)
        self._increment_turn()
        await self.ctx.compress_if_needed()

    # ══════════════════════════════════════════════
    # 투표 전 소견 발언 생성
    # ══════════════════════════════════════════════
    async def _get_pre_vote_statement(self, member: dict) -> str:
        """
        각 의원이 최종 투표 직전에 발언하는 소견문.
        토론에서 자신이 펼쳐온 주장을 요약하고, 왜 그 방향으로 투표할 것인지 피력.
        투표 라벨([찬성/반대/기권]) 자체는 포함하지 않음.
        """
        mid          = member["id"]
        speeches     = self.memories.get(mid, [])
        conviction_v = self.conviction.get_conviction(mid)

        if conviction_v >= 10:
            vote_hint = "찬성 방향으로 기울어져 있습니다"
        elif conviction_v <= -10:
            vote_hint = "반대 방향으로 기울어져 있습니다"
        else:
            vote_hint = "중립적 입장을 유지하고 있습니다"

        recent = speeches[-3:] if len(speeches) > 3 else speeches
        speeches_summary = "\n".join(
            f"- {s[:150]}{'...' if len(s) > 150 else ''}" for s in recent
        ) if recent else "발언 없음"

        mid_count = len(self._mid_research_log.get(mid, []))
        mid_note  = f"\n※ 토론 중 {mid_count}회 즉석 학습으로 추가 정보를 습득했습니다." if mid_count > 0 else ""

        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 AI 의회의 {member['name']} 의원입니다.\n"
                    f"【성향: {member.get('bias','중립')}】 {member.get('persona','')[:150]}\n\n"
                    "지금은 최종 표결 직전 '투표 전 소견 발언' 시간입니다.\n"
                    "목적: ①토론에서 펼쳐온 핵심 주장 2~3개 요약 ②상대 논거의 영향 솔직히 밝힘 ③투표 방향 이유 피력\n"
                    "규칙: 150~250자 이내 / [찬성]/[반대]/[기권] 라벨 직접 쓰지 말 것 / 자기소개 금지 / 바로 주장으로 시작 / 마침표로 끝낼 것\n"
                )
            },
            {
                "role": "user",
                "content": (
                    f"안건: \"{self.issue}\"\n\n"
                    f"본 의원의 토론 발언 요약:\n{speeches_summary}\n"
                    f"{mid_note}\n\n"
                    f"현재 확신도: {conviction_v:.0f}/100 ({vote_hint})\n\n"
                    "핵심 주장 요약과 투표 이유를 소견으로 밝히세요. 투표 결과를 직접 선언하지 마세요."
                )
            }
        ]
        try:
            result = await call_member(member, messages, temperature=0.55)
            result = self._strip_prefix(result)
            result = self._strip_member_intro(result)
            if result and len(result) > 20:
                print(f"[PreVote] {member['name']} 소견 완료 ({len(result)}자)")
                return result
        except Exception as e:
            print(f"[PreVote] {member['name']} 소견 실패: {e}")
        return ""

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
        debate_log = self.ctx.to_plain_text() if not self.ctx.summary else ""
        debate_context = full_summary if full_summary else (
            f"\n\n[전체 토론 내용]\n{debate_log}" if debate_log else ""
        )
        persona       = member.get("persona", "")
        bias          = member.get("bias", "중립")
        vote_tendency = member.get("vote_tendency", "")
        temp          = member.get("temperature", 0.4)
        conviction_instruction = self.conviction.conviction_to_vote_instruction(member["id"])

        # 토론 역할 배정을 투표에도 반영 (확신도와 함께 참고)
        stance = self._stance_map.get(member["id"], "FREE")
        if stance == "PRO":
            stance_note = (
                "\n\n【토론 중 역할 배정 참고】\n"
                "이번 토론에서 당신은 찬성 논거 대표로 배정되었습니다.\n"
                "단, 투표는 역할이 아니라 토론 결과로 형성된 확신도에 따라 자유롭게 하세요.\n"
                "토론 중 강력한 반대 논거에 설득됐다면 반대 투표가 더 솔직한 결과입니다.\n"
            )
        elif stance == "CON":
            stance_note = (
                "\n\n【토론 중 역할 배정 참고】\n"
                "이번 토론에서 당신은 반대 논거 대표로 배정되었습니다.\n"
                "단, 투표는 역할이 아니라 토론 결과로 형성된 확신도에 따라 자유롭게 하세요.\n"
                "토론 중 강력한 찬성 논거에 설득됐다면 찬성 투표가 더 솔직한 결과입니다.\n"
            )
        else:
            stance_note = ""

        speeches_text = chr(10).join(speeches) if speeches else "발언 없음 (사회자로 역할 수행)"

        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 {member['name']} 의원입니다.\n"
                    f"【당신의 정체성과 지식 기반】 {persona}\n\n"
                    f"【당신의 이념적 성향: {bias}】\n"
                    + (f"【당신의 투표 경향】 {vote_tendency}\n\n" if vote_tendency else "\n")
                    + f"당신의 전체 토론 발언:\n\"\"\"\n"
                    f"{speeches_text}\n\"\"\""
                    f"{debate_context}{admit_note}{stance_note}"
                    f"{conviction_instruction}\n\n"
                    "투표 규칙:\n"
                    "- 최우선 기준: 위 【토론 결과 반영 — 확신도】를 따르세요.\n"
                    "- 당신의 이념적 성향(bias)과 투표 경향도 반영하세요.\n"
                    "- 다른 의원 모두와 같은 결론이 나와도 됩니다.\n"
                    "- 발언이 없었던 경우에도 전체 토론 내용을 숙지하고 최종 판단을 내리십시오.\n"
                    "- 형식: [찬성|반대|기권] 이유 (200자 이내, 완전한 문장으로)\n"
                    "⚠️ 토론 과정에서 설득된 방향으로 솔직하게 투표하세요."
                )
            },
            {"role": "user", "content": f"안건 \"{self.issue}\"에 최종 투표하세요."}
        ]
        try:
            return await call_member(member, messages, temperature=max(0.5, temp))
        except:
            return "[기권] 시스템 오류로 기권합니다."

    async def get_resolution(self, chair: dict) -> str:
        recent_logs = self.ctx.all_logs[-8:] if self.ctx.all_logs else []
        recent_text = "\n".join(f"{l['speaker']}: {l['text']}" for l in recent_logs)
        context_text = ""
        if self.ctx.summary:
            context_text += f"[토론 요약]\n{self.ctx.summary}\n\n"
        if recent_text:
            context_text += f"[최근 발언]\n{recent_text}"

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 AI 의회의 공식 결의문 기안 책임자입니다.\n"
                    "아래 토론 내용을 검토하여 의회의 공식 결의문(Resolution)을 작성하세요.\n\n"
                    "결의문의 목적:\n"
                    "  - 찬반 투표 집계가 아닙니다.\n"
                    "  - 토론에서 제기된 논거, [DATA], [ADMIT]로 수렴된 논점을 종합하여\n"
                    "    '이 안건에 대해 의회가 무엇을 권고·결의하는가'를 실질적 내용으로 담습니다.\n"
                    "  - 반드시 구체적인 정책 방향, 권고 사항, 조건 또는 유보 사항을 포함해야 합니다.\n\n"
                    "형식 (반드시 준수):\n"
                    "  【전문】 안건 배경 및 논의 경과 (2~3문장)\n"
                    "  【결의 제1조】 의회가 권고하는 핵심 방향\n"
                    "  【결의 제2조】 조건, 단서, 또는 추가 검토 사항\n"
                    "  【결의 제3조】 이행 방안 또는 후속 조치\n"
                    "  【부기】 소수 의견 또는 유보 입장\n\n"
                    "작성 원칙:\n"
                    "  - '찬성 N명 반대 M명' 식의 표결 집계 문구 금지.\n"
                    "  - '심도 있는 논의가 필요하다' 같은 모호한 선언만으로 끝내지 마세요.\n"
                    "  - 토론에서 실제로 논증된 근거에 기반하여 구체적으로 작성하세요.\n"
                    "  - 800자 이내, 완전한 문장으로.\n"
                )
            },
            {
                "role": "user",
                "content": f"안건: \"{self.issue}\"\n\n{context_text}\n\n위 토론을 바탕으로 의회 공식 결의문을 작성하세요."
            }
        ]
        from ai_caller import (
            call_gemini as _cg, call_groq as _cgr, call_openrouter as _cor,
            _ENGINE_SEMAPHORES, _BUCKETS,
        )
        engine = chair.get("engine", "openrouter")
        model  = chair.get("model",  "mistralai/mistral-small-3.2-24b-instruct:free")

        async def _call_with_protection(engine_name, fn, mdl):
            sem = _ENGINE_SEMAPHORES.get(engine_name, _ENGINE_SEMAPHORES["openrouter"])
            async with sem:
                await _BUCKETS[engine_name].acquire()
                return await fn(messages, temperature=0.4, model=mdl, max_tokens=600)

        order = []
        if engine == "gemini":
            order = [
                ("gemini",     _cg,  model),
                ("groq",       _cgr, "llama-3.3-70b-versatile"),
                ("openrouter", _cor, "mistralai/mistral-small-3.2-24b-instruct:free"),
            ]
        elif engine == "groq":
            order = [
                ("groq",       _cgr, model),
                ("gemini",     _cg,  "gemini-2.5-flash"),
                ("openrouter", _cor, "mistralai/mistral-small-3.2-24b-instruct:free"),
            ]
        else:
            order = [
                ("openrouter", _cor, model),
                ("gemini",     _cg,  "gemini-2.5-flash"),
                ("groq",       _cgr, "llama-3.3-70b-versatile"),
            ]

        for eng_name, fn, mdl in order:
            try:
                return await _call_with_protection(eng_name, fn, mdl)
            except Exception as e:
                print(f"[get_resolution/{eng_name}] {e}")

        # 폴백: 모든 엔진 실패 시 최소한 형식을 갖춘 결의문 반환
        return (
            f"【전문】 AI 의회는 '{self.issue}'에 관한 토론을 충분히 진행하였다.\n"
            "【결의 제1조】 의회는 토론에서 제기된 찬반 논거를 종합하여 단계적 검토를 권고한다.\n"
            "【결의 제2조】 구체적 이행 조건 및 예외 사항은 후속 소위원회에서 심의한다.\n"
            "【결의 제3조】 본 결의안의 이행 상황을 분기별로 의회에 보고한다.\n"
            "【부기】 반대 의견은 별도 부기 의견서로 기록·보존한다."
        )

    async def run_conclusion(self, chair: dict, timed_out: bool = False):
        if not timed_out and not self._chair_final_spoke:
            try:
                chair_final = await self.get_opinion(
                    chair, chair["name"],
                    format_guide=(
                        "【의장 결의 방향 제안】 지금까지 토론을 들은 의장으로서 "
                        "어떤 방향의 결의안이 가장 합리적인지 의회 전체에 제안하십시오. "
                        "찬반이 아니라 '무엇을 권고해야 하는가'를 중심으로 150자 이내로 밝히세요."
                        if self.conclusion_type == "RESOLUTION" else
                        "【의장 최종 소견】 지금까지 토론을 들은 의장으로서 "
                        "가장 설득력 있었던 논거와 본인의 최종 입장을 150자 이내로 간결하게 밝히세요."
                    ),
                    round_num=1,
                    is_rebuttal=False,
                    free_mode=False,
                )
                if chair_final and len(chair_final) > 10:
                    self.memories[chair["id"]].append(chair_final)
                    self.ctx.push(f"[의장 {chair['name']}]", chair_final)
                    chair_stype = self.detect_type(chair_final)
                    try:
                        conv_result = await self.conviction.evaluate_speech(chair["id"], chair_final, chair_stype)
                        if conv_result.get("changes"):
                            await self.send(
                                "conviction",
                                changes=conv_result["changes"],
                                all=self.conviction.get_all(),
                            )
                    except Exception as e:
                        print(f"[ConvictionTracker] 의장 소견 평가 중 오류 (무시): {e}")
                    await self.send_speech(chair, chair_final, chair_stype, True)
                    self._chair_final_spoke = True
                    print(f"[Engine] 의장 {chair['name']} 최종 소견 발언 완료")
            except Exception as e:
                print(f"[Engine] 의장 최종 소견 실패 (무시): {e}")

        if timed_out:
            close_instruction = (
                f"발언 한도 {self.max_turns}턴이 소진되었습니다. "
                f"지금까지의 논의를 바탕으로 {'찬반 표결' if self.conclusion_type == 'VOTE' else '공동 결의안 채택'}을 실시하겠습니다."
            )
        elif self.debate_format == "자유토론":
            close_instruction = (
                f"자유토론이 종료되었습니다. "
                f"{'찬반 표결' if self.conclusion_type == 'VOTE' else '공동 결의안 채택'}을 실시하겠습니다."
            )
        else:
            conclusion_term = "찬반 표결" if self.conclusion_type == "VOTE" else "공동 결의안 채택"
            close_instruction = (
                f"총 {self.rounds}라운드의 토론이 완료되었습니다. {conclusion_term}을 진행하겠습니다."
            )
        close_text = await self.chair_speak(chair, close_instruction, max_chars=CHAIR_MAX_LEN)
        self.ctx.push(f"[의장 {chair['name']}]", close_text)
        await self.send_speech(chair, close_text, "NORMAL", True)

        status_msg = "표결 진행 중..." if self.conclusion_type == "VOTE" else "결의안 작성 중..."
        await self.send("status", message=status_msg)
        await self.send("conviction", changes=[], all=self.conviction.get_all())

        if self.conclusion_type == "RESOLUTION":
            resolution = await self.get_resolution(chair)
            await self.send("result", resultType="RESOLUTION", content=resolution)
        else:
            # ── 투표 전 소견 발언 ────────────────────────────────────────
            await self.send("status", message="🎙 각 의원 투표 전 최종 소견 발언 중...")
            try:
                pre_vote_intro = await self.chair_speak(
                    chair,
                    "표결 전 각 의원께서 토론을 통해 형성된 최종 입장과 투표 이유를 간략히 밝혀 주시기 바랍니다.",
                    max_chars=120,
                )
                self.ctx.push(f"[의장 {chair['name']}]", pre_vote_intro)
                await self.send_speech(chair, pre_vote_intro, "NORMAL", True)
            except Exception as e:
                print(f"[PreVote] 의장 소개 실패 (무시): {e}")

            non_chair_members = [m for m in self.members if m["id"] != chair["id"]]
            for m in non_chair_members:
                pre_vote_text = await self._get_pre_vote_statement(m)
                if pre_vote_text and len(pre_vote_text) > 10:
                    self.ctx.push(f"[{m['name']} 의원]", pre_vote_text)
                    await self.send_speech(m, pre_vote_text, "NORMAL", False)

            # ── 실제 투표 집계 ───────────────────────────────────────────
            votes = []
            for m in self.members:
                await self.send("status", message=f"{m['name']} 의원 최종 투표 중...")
                vote = await self.get_vote(m)
                votes.append({"memberId": m["id"], "text": vote})
            await self.send("result", resultType="VOTE", content=votes)

        await self.send("status", message="✅ 토론 종료")
        await self.send("done")

    # ══════════════════════════════════════════════
    # 자율 입장 초기화
    # ══════════════════════════════════════════════
    def _assign_stances(self, chair_id: str):
        """
        찬반 균형 배정:
        - 비의장 의원 중 절반(올림)은 PRO(찬성 논거 대표),
          나머지는 CON(반대 논거 대표)로 배정.
        - 의장은 NEUTRAL.
        - 각 의원의 bias 성향을 존중하되, 배정된 입장을 최우선으로 삼음.
        - 의원이 1~2명일 경우 FREE 모드 유지.
        """
        import random as _r
        non_chair = [m for m in self.members if m["id"] != chair_id]
        self._stance_map = {}
        self._stance_map[chair_id] = "NEUTRAL"

        if len(non_chair) < 3:
            for m in non_chair:
                self._stance_map[m["id"]] = "FREE"
            print(f"[Engine] 자율 입장 형성 모드 (의원 수 부족)")
            return

        # bias 기반으로 자연스러운 분류 우선
        pro_bias  = {"진보·개혁·공정", "자유주의·분권", "실용·데이터중심"}
        con_bias  = {"보수·안정·점진"}

        shuffled = non_chair.copy()
        _r.shuffle(shuffled)

        pro_pool = [m for m in shuffled if m.get("bias","") in pro_bias]
        con_pool = [m for m in shuffled if m.get("bias","") in con_bias]
        neutral_pool = [m for m in shuffled if m not in pro_pool and m not in con_pool]

        # 균형 맞추기: 과반수가 한쪽에 쏠리면 중립 풀에서 보충
        target_pro = (len(non_chair) + 1) // 2
        target_con = len(non_chair) - target_pro

        while len(pro_pool) < target_pro and neutral_pool:
            pro_pool.append(neutral_pool.pop(0))
        while len(con_pool) < target_con and neutral_pool:
            con_pool.append(neutral_pool.pop(0))
        # 남은 중립은 PRO에 추가 (홀수 처리)
        pro_pool += neutral_pool

        # ── 버그 수정: neutral_pool이 비어있어도 bias 풀 간 불균형이 생길 수 있음 ──
        # CON 풀 부족 (pro_bias 3명 vs con_bias 2명에서 CON 의장일 때)
        # → pro_pool 잉여분을 CON으로 재배정하여 찬반 균형 강제 유지
        while len(con_pool) < target_con and len(pro_pool) > target_pro:
            con_pool.append(pro_pool.pop())
        # PRO 풀 부족 (드문 케이스 방어)
        while len(pro_pool) < target_pro and len(con_pool) > target_con:
            pro_pool.append(con_pool.pop())

        for m in pro_pool[:target_pro]:
            self._stance_map[m["id"]] = "PRO"
        for m in con_pool[:target_con]:
            self._stance_map[m["id"]] = "CON"
        # 혹시 누락된 의원
        for m in non_chair:
            if m["id"] not in self._stance_map:
                self._stance_map[m["id"]] = "FREE"

        pro_names = [m["name"] for m in non_chair if self._stance_map.get(m["id"]) == "PRO"]
        con_names = [m["name"] for m in non_chair if self._stance_map.get(m["id"]) == "CON"]
        print(f"[Engine] 찬반 배정 — PRO: {pro_names} / CON: {con_names}")

        # ── conviction 불일치 보정 ────────────────────────────────────────
        # bias와 반대 stance로 배정된 의원의 초기 확신도를 중립 근방으로 조정.
        # 예) 지피티(진보 +25)가 CON으로 배정되면 확신도가 여전히 +25라
        # 토론 중 CON 역할을 해도 투표 시 [찬성]이 나오는 문제 방지.
        # 배정 후 중립화([-20, +20] 클리핑)하여 토론 논거로 확신도가 형성되도록 유도.
        NEUTRALIZE_RANGE = 20
        for m in non_chair:
            mid    = m["id"]
            stance = self._stance_map.get(mid, "FREE")
            curr   = self.conviction.convictions.get(mid, 0)
            if stance == "CON" and curr > NEUTRALIZE_RANGE:
                # PRO bias가 CON으로 배정됨 — 반대 방향으로 중립화
                adjusted = -NEUTRALIZE_RANGE
                self.conviction.convictions[mid] = float(adjusted)
                print(f"[Engine] conviction 보정: {m['name']} CON배정 {curr:+.0f} → {adjusted:+.0f}")
            elif stance == "PRO" and curr < -NEUTRALIZE_RANGE:
                # CON bias가 PRO로 배정됨 — 찬성 방향으로 중립화
                adjusted = NEUTRALIZE_RANGE
                self.conviction.convictions[mid] = float(adjusted)
                print(f"[Engine] conviction 보정: {m['name']} PRO배정 {curr:+.0f} → {adjusted:+.0f}")

    def _get_stance_guide(self, member_id: str) -> str:
        m = self.member_map.get(member_id, {})
        bias = m.get("bias", "중립")
        vote_tendency = m.get("vote_tendency", "")
        stance = self._stance_map.get(member_id, "FREE")

        if stance == "PRO":
            guide = (
                "\n【입장 역할 배정: 찬성 논거 대표】\n"
                "이번 토론에서 당신은 찬성 측 논거를 대표합니다.\n"
                "찬성 입장(또는 현행 법원 결정 지지)에서 가장 강력한 근거를 제시하고 옹호하세요.\n"
                "반대 논거가 나왔을 때는 반드시 [REFUTE]로 반박하세요.\n"
                f"당신의 성향({bias})이 이와 다르더라도, 지적 토론 수행을 위해 찬성 논거 최선 변론을 맡습니다.\n"
            )
        elif stance == "CON":
            guide = (
                "\n【입장 역할 배정: 반대 논거 대표】\n"
                "이번 토론에서 당신은 반대 측 논거를 대표합니다.\n"
                "반대 입장(또는 현행 재판 연기 위헌론)에서 가장 강력한 근거를 제시하고 옹호하세요.\n"
                "찬성 논거가 나왔을 때는 반드시 [REFUTE]로 반박하세요.\n"
                f"당신의 성향({bias})이 이와 다르더라도, 지적 토론 수행을 위해 반대 논거 최선 변론을 맡습니다.\n"
            )
        elif stance == "NEUTRAL":
            guide = (
                "\n【입장: 의장 중립】\n"
                "당신은 의장으로서 중립을 유지하고 토론을 공정하게 진행합니다.\n"
            )
        else:  # FREE
            guide = (
                "\n【입장 형성 원칙】\n"
                "당신의 찬반 입장은 사전에 배정된 것이 아닙니다.\n"
                "당신이 학습한 데이터, 지식, 가치관을 바탕으로 이 안건을 스스로 판단하세요.\n"
                f"당신의 이념적 성향({bias})은 하나의 렌즈입니다 — 이를 참고하되, "
                "안건의 실질적 내용과 논거를 더 중요하게 고려하세요.\n"
            )

        if vote_tendency and stance not in ("NEUTRAL",):
            guide += f"참고 성향: {vote_tendency}\n"
        guide += (
            "토론이 진행되면서 더 강한 논거·데이터에 설득된다면 [ADMIT]로 솔직히 인정하고 입장을 바꾸세요.\n"
            "만장일치 결론이 나더라도 그것이 논리적으로 타당하다면 자연스러운 결과입니다.\n"
        )
        return guide

    def _get_last_speech_context(self, current_member: dict) -> str:
        if not self.ctx.all_logs:
            return ""
        for log in reversed(self.ctx.all_logs):
            spk = log.get("speaker", "")
            if f"[{current_member['name']}" not in spk:
                name_match = re.search(r'\[(.+?)\s*(의원|의장)', spk)
                speaker_name = name_match.group(1) if name_match else "직전 발언자"
                text_preview = log.get("text", "")[:150]
                return (
                    f"{speaker_name}: "
                    f"\"{text_preview}{'...' if len(log.get('text','')) > 150 else ''}\""
                )
        return ""

    # ══════════════════════════════════════════════
    # 지목 발언 헬퍼
    # ══════════════════════════════════════════════
    async def _nominate(self, chair: dict, member: dict, text: str = None):
        """의장이 의원을 지목 — 짧은 지목 멘트는 status로, 중요 전환은 speech로."""
        nominate = text or f"{member['name']} 의원님, 발언해 주시기 바랍니다."
        # 짧은 단순 지목(80자 이하)은 status로 전송해 speech 목록 오염 방지
        if len(nominate) <= 80:
            await self.send("status", message=f"[의장] {nominate}")
        else:
            self.ctx.push(f"[의장 {chair['name']}]", nominate)
            await self.send_speech(chair, nominate, "NORMAL", True)

    # ══════════════════════════════════════════════
    # 메인 진입점
    # ══════════════════════════════════════════════
    async def run(self):
        chair = random.choice(self.members)
        await self.send("protocol",
            format    = self.debate_format,
            chairId   = chair["id"],
            chairName = chair["name"],
            maxTurns  = self.max_turns,
            rounds    = self.rounds,
        )
        print(f"[Engine] 의장: {chair['name']} / 형식: {self.debate_format} / {self.rounds}라운드 / 최대 {self.max_turns}턴")

        self._assign_stances(chair["id"])

        # 사전 리서치 (턴 카운트 시작 전)
        try:
            await self.research_phase()
        except Exception as e:
            print(f"[Engine] 사전 리서치 전체 실패 (토론은 정상 진행): {e}")

        # ACK 리스너 불필요 — 제거됨

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

        non_chair = [m for m in self.members if m["id"] != chair["id"]]
        first_member = non_chair[0] if non_chair else None

        open_instruction = (
            f"지금부터 AI 의회 본회의를 개회합니다. "
            f"오늘 상정된 안건은 '{self.issue}'입니다. "
            f"이 안건의 사회적·정책적 배경과 현재 논의가 필요한 이유를 2~3문장으로 소개하고, "
            f"찬성 측과 반대 측이 각각 중심적으로 다룰 것으로 예상되는 핵심 쟁점 2~3가지를 구체적으로 예고하세요. "
            f"그런 다음 본 토론이 {self.rounds}라운드 릴레이 형식으로 진행됨을 안내하고, "
            f"의원들이 실증 데이터와 문헌 근거를 적극 활용해 논거를 전개해 줄 것을 당부하세요."
        )
        if first_member:
            open_instruction += f" 마지막으로 첫 번째 발언자로 {first_member['name']} 의원님께 발언권을 드리세요."
        open_text = await self.chair_speak(chair, open_instruction, max_chars=CHAIR_MAX_LEN + 250, is_opening=True)

        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        _next_order_override = None

        for round_num in range(1, self.rounds + 1):
            self.current_round = round_num

            if self._turns_over():
                print(f"[Engine] 릴레이 턴 소진 ({self._turn_count}턴) — 의결로 이동")
                await self.run_conclusion(chair, timed_out=True)
                return

            if round_num == 1:
                order = non_chair.copy()
            elif _next_order_override is not None:
                order = _next_order_override
                _next_order_override = None
            else:
                order = non_chair.copy()
                random.shuffle(order)

            for idx, m in enumerate(order):
                if self._turns_over():
                    await self.run_conclusion(chair, timed_out=True)
                    return

                if idx > 0:
                    await self._nominate(chair, m)

                cur_opinion, cur_stype = await self.prepare_speech(
                    chair, m, fmt_guide, round_num
                )
                await self.deliver_speech(
                    chair, m, cur_opinion, cur_stype,
                    non_chair, fmt_guide, round_num
                )

            if round_num < self.rounds:
                if self._turns_over():
                    await self.run_conclusion(chair, timed_out=True)
                    return
                next_order = non_chair.copy()
                random.shuffle(next_order)
                next_first = next_order[0] if next_order else None
                transition = await self.chair_transition_round(chair, round_num, first_member=next_first)
                self.ctx.push(f"[의장 {chair['name']}]", transition)
                await self.send_speech(chair, transition, "NORMAL", True)
                _next_order_override = next_order

        await self.run_conclusion(chair, timed_out=self._turns_over())

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
            f"지금부터 AI 의회 본회의를 개회합니다. "
            f"오늘 상정된 안건은 '{self.issue}'입니다. "
            f"이 안건의 사회적·정책적 배경과 현시점에 논의가 필요한 이유를 2~3문장으로 소개하고, "
            f"찬반 양측이 각각 중심적으로 다룰 핵심 쟁점 2~3가지를 구체적으로 예고해 주세요. "
            f"본 토론은 집중토론 형식으로, 핵심 토론자 {d_names}께서 {self.rounds}라운드 집중 대결을 펼칩니다. "
            f"나머지 의원들은 이후 질의 시간에 발언권이 주어집니다. "
            f"실증 데이터와 문헌 근거를 적극 활용해 논거를 전개해 줄 것을 당부하며, "
            f"1라운드 첫 발언자로 {debaters[0]['name']} 의원님께 발언권을 드립니다.",
            max_chars=CHAIR_MAX_LEN + 250,
            is_opening=True,
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        for round_num in range(1, self.rounds + 1):
            self.current_round = round_num

            if self._turns_over():
                await self.run_conclusion(chair, timed_out=True)
                return

            pair = debaters.copy()
            if round_num > 1:
                random.shuffle(pair)

            for _pi, m in enumerate(pair):
                if self._turns_over():
                    await self.run_conclusion(chair, timed_out=True)
                    return
                if _pi > 0:
                    await self._nominate(chair, m, text=f"{m['name']} 의원님, 발언해 주십시오.")
                _po, _ps = await self.prepare_speech(chair, m, fmt_guide, round_num)
                await self.deliver_speech(chair, m, _po, _ps, non_chair, fmt_guide, round_num)

            if round_num < self.rounds:
                if self._turns_over():
                    await self.run_conclusion(chair, timed_out=True)
                    return
                next_first = debaters[1] if round_num % 2 == 0 else debaters[0]
                transition = await self.chair_transition_round(chair, round_num, first_member=next_first)
                self.ctx.push(f"[의장 {chair['name']}]", transition)
                await self.send_speech(chair, transition, "NORMAL", True)

        if observers and not self._turns_over():
            qa_open = await self.chair_speak(
                chair,
                "핵심토론이 완료되었습니다. 나머지 의원님들의 질의 및 반박 시간입니다.",
                max_chars=CHAIR_MAX_LEN,
            )
            self.ctx.push(f"[의장 {chair['name']}]", qa_open)
            await self.send_speech(chair, qa_open, "NORMAL", True)

            for m in observers:
                if self._turns_over():
                    break
                await self._nominate(chair, m,
                    text=f"{m['name']} 의원님, 핵심 토론자의 논거에 대해 반박하거나 보완 질의해 주십시오.")
                # observer는 직전 debater 발언을 대상으로 반박 유도
                last_ctx = self._get_last_speech_context(m)
                _oo, _os2 = await self.prepare_speech(
                    chair, m, fmt_guide, self.rounds,
                    is_rebuttal=True, target_speech=last_ctx,
                )
                await self.deliver_speech(chair, m, _oo, _os2, non_chair, fmt_guide, self.rounds)

        await self.run_conclusion(chair, timed_out=self._turns_over())

    # ══════════════════════════════════════════════
    # 3. 전문가패널
    # ══════════════════════════════════════════════
    async def _run_panel(self, chair: dict):
        fmt_guide = (
            f"【전문가패널 형식 규칙】\n"
            "패널로 선정된 의원이 심층 발언을 합니다.\n"
            "일반 의원은 패널에게 질의하는 형식으로 참여합니다.\n"
            f"패널은 반드시 질의에 직접 답하되 새 데이터를 추가해야 합니다. 최대 {MAX_SPEECH_LEN}자."
        )

        non_chair = [m for m in self.members if m["id"] != chair["id"]]
        if len(non_chair) < 2:
            await self._run_relay(chair)
            return

        panel_count = max(1, len(non_chair) // 2)
        panels  = random.sample(non_chair, panel_count)
        general = [m for m in non_chair if m not in panels]
        p_names = ", ".join(p["name"] for p in panels)

        summary = await self.chair_speak(
            chair,
            f"지금부터 AI 의회 전문가패널 토론을 개회합니다. "
            f"안건은 '{self.issue}'입니다. "
            f"패널로는 {p_names} 의원님께서 선정되었습니다. "
            "패널 의원님들의 심층 발언과 일반 의원들의 질의로 진행됩니다.",
            max_chars=CHAIR_MAX_LEN + 100,
            is_opening=True,
        )
        self.ctx.push(f"[의장 {chair['name']}]", summary)
        await self.send_speech(chair, summary, "NORMAL", True)

        qa_rounds = max(1, self.rounds - 1)
        for qa_round in range(1, qa_rounds + 1):
            if self._turns_over():
                break
            qa_text = await self.chair_speak(
                chair,
                f"전체 질의·응답 {qa_round}라운드를 시작합니다.",
                max_chars=100,
            )
            self.ctx.push(f"[의장 {chair['name']}]", qa_text)
            await self.send_speech(chair, qa_text, "NORMAL", True)

            if general:
                shuffled_gen = general.copy()
                random.shuffle(shuffled_gen)
                for m in shuffled_gen:
                    if self._turns_over():
                        break
                    await self._nominate(chair, m, text=f"{m['name']} 의원님, 패널에 질의해 주십시오.")
                    _go, _gs = await self.prepare_speech(chair, m, fmt_guide, max(1, self.rounds - 1))
                    await self.deliver_speech(chair, m, _go, _gs, non_chair, fmt_guide, max(1, self.rounds - 1))

            for p in panels:
                if self._turns_over():
                    break
                await self._nominate(chair, p, text=f"패널 {p['name']} 의원님, 질의에 응답해 주십시오.")
                _ro, _rs = await self.prepare_speech(chair, p, fmt_guide, max(1, self.rounds - 1))
                await self.deliver_speech(chair, p, _ro, _rs, non_chair, fmt_guide, max(1, self.rounds - 1))

        await self.run_conclusion(chair, timed_out=self._turns_over())

    # ══════════════════════════════════════════════
    # 4. 자유토론
    # ══════════════════════════════════════════════
    async def _run_free(self, chair: dict):
        fmt_guide = (
            f"【자유토론 형식 규칙】\n"
            "순서 제한 없이 누구든 자유롭게 발언합니다.\n"
            "앞선 발언들을 면밀히 검토하고 논리적 타당성만으로 반응을 선택하세요.\n"
            "새로운 근거·데이터([DATA]), 차트([CHART:bar]/[CHART:line]/[CHART:pie]), 표([TABLE:json])를 적극 활용하세요.\n"
            f"최대 {self.max_free_turns}회 발언 후 의장이 즉시 최종 의결을 진행합니다. 최대 {MAX_SPEECH_LEN}자."
        )

        warn_threshold = int(self.max_free_turns * 0.8)

        open_text = await self.chair_speak(
            chair,
            f"지금부터 AI 의회 본회의를 개회합니다. "
            f"오늘 상정된 안건은 '{self.issue}'입니다. "
            f"이 안건의 사회적·정책적 배경과 현시점 논의 필요성을 2~3문장으로 소개하고, "
            f"이 자유토론에서 집중적으로 다루어져야 할 핵심 쟁점 2~3가지를 구체적으로 예고해 주세요. "
            f"본 토론은 자유토론 형식으로 최대 {self.max_free_turns}회 발언 한도 내에서 "
            "순서 제한 없이 자유롭게 발언할 수 있습니다. "
            "의원들이 실증 데이터, 통계, 문헌 근거를 적극 활용해 논거를 전개해 줄 것을 당부하며, "
            "발언 한도 종료 후에는 즉시 최종 의결로 이행합니다.",
            max_chars=CHAIR_MAX_LEN + 250,
            is_opening=True,
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)

        warned    = False
        turn      = 0
        non_chair = [m for m in self.members if m["id"] != chair["id"]]

        while not self._turns_over() and turn < self.max_free_turns:

            # 80% 턴 소진 경고
            if not warned and turn >= warn_threshold:
                warned = True
                remaining = self._turns_remaining()
                warn_text = await self.chair_speak(
                    chair,
                    f"발언 한도 알림: 잔여 발언 {remaining}회 남았습니다. "
                    "핵심 주장을 마무리해 주시기 바랍니다.",
                    max_chars=120,
                )
                self.ctx.push(f"[의장 {chair['name']}]", warn_text)
                await self.send_speech(chair, warn_text, "NORMAL", True)
                if self._turns_over() or turn >= self.max_free_turns:
                    break

            # 발언자 선택
            speaker = None
            if self.ctx.all_logs:
                last_log  = self.ctx.all_logs[-1]
                last_text = last_log.get("text", "")
                last_spk  = last_log.get("speaker", "")
                if "[REFUTE]" in last_text:
                    candidates = [m for m in non_chair if f"[{m['name']}" not in last_spk]
                    speaker = random.choice(candidates) if candidates else random.choice(non_chair)
                else:
                    recent_ids = set()
                    for log in self.ctx.all_logs[-2:]:
                        spk = log.get("speaker", "")
                        for m in non_chair:
                            if f"[{m['name']}" in spk:
                                recent_ids.add(m["id"])
                    fresh = [m for m in non_chair if m["id"] not in recent_ids]
                    speaker = random.choice(fresh) if fresh else random.choice(non_chair)
            else:
                speaker = random.choice(non_chair)

            await self.send("status",
                message=f"[자유토론] {speaker['name']} 의원 발언 준비 중... "
                        f"({turn+1}/{self.max_free_turns}턴)")

            _fo, _fs = await self.prepare_speech(chair, speaker, fmt_guide, 1, free_mode=True)
            await self.deliver_speech(chair, speaker, _fo, _fs, non_chair, fmt_guide, 1, free_mode=True)
            turn += 1

            # 5턴마다 중간 정리
            if turn > 0 and turn % 5 == 0 and not self._turns_over() and turn < self.max_free_turns:
                inter = await self.chair_speak(
                    chair,
                    "잠시 중간 정리를 하겠습니다. 현재까지의 주요 찬반 논점을 요약하고 자유토론을 계속합니다.",
                    max_chars=160,
                )
                self.ctx.push(f"[의장 {chair['name']}]", inter)
                await self.send_speech(chair, inter, "NORMAL", True)

        print(f"[Engine] 자유토론 종료: {turn}회 발언")
        await self.run_conclusion(chair, timed_out=self._turns_over())