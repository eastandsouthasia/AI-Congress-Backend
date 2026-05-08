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
from ai_caller import call_member, call_research
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
        모든 참여 의원이 병렬로 해당 안건의 최신 정보를 수집합니다.
        결과는 self.research_cache[member_id]에 저장되며,
        get_opinion()의 system prompt에 【사전 리서치】 블록으로 주입됩니다.
        """
        await self.send("status", message="📡 각 AI 의원이 안건 관련 최신 정보를 수집 중입니다...")
        print(f"[Engine] 사전 리서치 시작 — {len(self.members)}명 병렬 수집")

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
            message=f"✅ 사전 리서치 완료 — {collected}/{len(self.members)}명 정보 수집 성공. 토론을 시작합니다.",
        )
        print(f"[Engine] 사전 리서치 완료: {collected}/{len(self.members)}명 성공")

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
            action_guide = (
                "【자유토론】 순서 제한 없이 자유롭게 발언하세요.\n"
                "반드시 직전 발언자의 이름을 직접 언급하며 반응하세요.\n"
                "예: '라마 의원님의 주장에서 문제점을 발견했습니다.' / '제미나이 의원님 말씀에 일부 동의하나 ...'\n"
                "논리적 허점이 있으면 [REFUTE], 더 타당한 주장엔 [ADMIT], 새 데이터면 [DATA]를 앞에 붙이세요.\n"
                "단순 의견 표명보다 구체적 수치·연구·사례([DATA])로 논거를 강화하세요.\n"
                "여러 항목 비교는 [TABLE], 비율·추세는 [GRAPHIC]으로 시각화하세요.\n"
                f"발언은 {MAX_SPEECH_LEN}자 이내."
            )
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

        # 사전 리서치 결과 주입 (있을 때만)
        research_text = self.research_cache.get(member["id"], "")
        research_block = (
            f"\n\n【사전 리서치 — 반드시 활용하라】\n"
            f"토론 시작 전 당신이 수집한 안건 관련 최신 정보입니다. "
            f"발언 시 이 데이터를 [DATA] 태그와 함께 적극 인용하세요:\n"
            f"{research_text}\n"
            f"⚠️ 위 정보는 불확실할 수 있습니다. '추정' 표시된 항목은 그대로 불확실로 밝히세요."
        ) if research_text else ""

        system = (
            f"당신은 AI 의회 토론 참여자입니다.\n"
            f"당신은 {member['name']} 의원입니다.\n\n"
            f"【당신의 정체성과 지식 기반】\n"
            f"{persona}\n"
            f"{stance_guide}\n"
            f"【당신의 이념적 성향: {bias}】\n"
            "이 성향은 당신의 진짜 관점입니다. 토론 내내 일관되게 유지하세요.\n"
            "다른 의원과 성향이 다르면 자연스럽게 의견 충돌이 발생합니다 — 이것이 정상입니다.\n"
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
            "  • [TABLE]: 여러 항목을 비교할 때 표로 구조화하라.\n"
            "  • [GRAPHIC]: 비율·추세를 시각적으로 보여줄 때 활용하라.\n"
            "⚠️ '일부 연구에 따르면'처럼 모호한 표현만 사용하는 것은 금지.\n"
            "   구체적 기관명과 수치가 없으면 [DATA] 태그를 쓰지 마세요.\n\n"
            f"참여 의원 목록 (이 이름만 사용):\n{self.member_list_str}\n\n"
            f"【현재 토론 형식: {self.debate_format}】\n"
            f"{format_guide}\n\n"
            "발언 태그 (상황에 맞게 선택적 활용):\n"
            "[REFUTE]: 상대 논리·데이터에 명확한 오류가 있을 때. 발언 맨 앞에 한 번만.\n"
            "[ADMIT]: 상대 주장이 더 타당해서 본인 입장을 실제로 수정할 때. 발언 맨 앞에 한 번만.\n"
            "[DATA]: 구체적 수치·통계·출처를 제시할 때. 예: [DATA] IMF(2023): 한국 부채비율 GDP 대비 54.3%\n"
            "[GRAPHIC]: 텍스트 시각화. 예:\n"
            "  [GRAPHIC]\n"
            "  찬성 ████████░░ 78%\n"
            "  반대 ██░░░░░░░░ 22%\n"
            "[TABLE]: 텍스트 표. 예:\n"
            "  [TABLE]\n"
            "  | 국가  | 도입연도 | 효과(%) |\n"
            "  |-------|----------|----------|\n"
            "  | 독일  | 2015     | +12.3    |\n"
            "  | 프랑스| 2017     | +8.7     |\n\n"
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

        if stype == "REFUTE" and not free_mode and round_num < self.rounds and not self._turns_over():
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
                ("gemini",     _cg,  "gemini-2.5-flash"),
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
        return "의원들의 충분한 논의를 바탕으로 본 안건을 검토하였다."

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
        self._stance_map = {m["id"]: "FREE" for m in self.members}
        print(f"[Engine] 자율 입장 형성 모드 — 전원 FREE")

    def _get_stance_guide(self, member_id: str) -> str:
        m = self.member_map.get(member_id, {})
        bias = m.get("bias", "중립")
        vote_tendency = m.get("vote_tendency", "")

        guide = (
            "\n【입장 형성 원칙】\n"
            "당신의 찬반 입장은 사전에 배정된 것이 아닙니다.\n"
            "당신이 학습한 데이터, 지식, 가치관을 바탕으로 이 안건을 스스로 판단하세요.\n"
            f"당신의 이념적 성향({bias})은 하나의 렌즈입니다 — 이를 참고하되, "
            "안건의 실질적 내용과 논거를 더 중요하게 고려하세요.\n"
        )
        if vote_tendency:
            guide += f"참고: {vote_tendency}\n"
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
        """의장이 의원을 지목하는 짧은 발언을 즉시 전송. ACK 대기 없음."""
        nominate = text or f"{member['name']} 의원님, 발언해 주시기 바랍니다."
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
                "핵심토론이 완료되었습니다. 나머지 의원님들의 질의 시간입니다.",
                max_chars=CHAIR_MAX_LEN,
            )
            self.ctx.push(f"[의장 {chair['name']}]", qa_open)
            await self.send_speech(chair, qa_open, "NORMAL", True)

            for m in observers:
                if self._turns_over():
                    break
                await self._nominate(chair, m, text=f"{m['name']} 의원님, 질의해 주십시오.")
                _oo, _os2 = await self.prepare_speech(chair, m, fmt_guide, self.rounds)
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
            "새로운 근거·데이터([DATA]), 시각화([GRAPHIC]), 표([TABLE])를 적극 활용하세요.\n"
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
                remaining = self.max_free_turns - turn
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
