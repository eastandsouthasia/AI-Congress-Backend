"""
AI Congress Debate Engine
✅ 타이밍 수정 (이번 버전):
  [FIX-T1] skip_wait=True 발언의 ACK 없이 0.3초만 대기하던 문제 해결.
           → send_speech에서 skip_wait 제거, 대신 _wait_for_ready_short(timeout)를 사용.
           → 짧은 발언은 TTS 완료 후 빠르게 ACK 수신, 느리면 timeout(기본 8초) 내 자동 진행.
  [FIX-T2] _wait_for_ready에 stale ACK 방지용 sequence 번호 도입.
           → ACK 수신 시 현재 시퀀스와 일치하는지 검증. 이전 TTS의 늦은 ACK는 무시됨.
  [FIX-T3] _handle_rebuttal_request의 request_notice + judge_speech를
           skip_wait(0.3초) → _wait_for_ready_short(8초 timeout ACK 대기)로 변경.
  [FIX-T4] 파이프라인 prefetch 중 prepare_speech가 ctx.push()까지 선행 완료하여
           deliver 직전 ctx 순서가 꼬이던 문제 주석으로 명확화 및 방어 처리.
  [FIX-T5] nominate 발언(의장 지목) 도 _wait_for_ready_short(5초)로 변경하여
           TTS와 다음 API 호출 겹침 방지.
  [FIX-T6] _ack_listener에서 sequence 기반 set으로 변경하여 stale ACK 소비 방지.
"""

import json
import asyncio
import random
import re
import time
from fastapi import WebSocket
from members import MEMBERS
from ai_caller import call_member
from debate_context import DebateContext
from conviction_tracker import ConvictionTracker

# ─────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────
MAX_ROUNDS     = 3
MIN_ROUNDS     = 1
MAX_FREE_TURNS = 60
MAX_SPEECH_LEN = 500   # 의원 발언 최대 글자수
CHAIR_MAX_LEN  = 200   # 의장 사회 발언 최대 글자수

# [FIX-T1] skip_wait 대체 타임아웃 값
NOMINATE_ACK_TIMEOUT = 5.0   # 지목 발언 ACK 대기 최대 (초)
NOTICE_ACK_TIMEOUT   = 8.0   # 반박신청/판단 발언 ACK 대기 최대 (초)
SPEECH_ACK_TIMEOUT   = 60.0  # 본 발언 ACK 대기 최대 (초)





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
        self.memories      = {m["id"]: [] for m in self.members}
        self.speech_count  = {m["id"]: 0 for m in self.members}
        self.current_round = 0

        self._stance_map: dict = {}
        self._chair_final_spoke: bool = False
        self.conviction = ConvictionTracker(self.members, issue)

        # [FIX-T2] stale ACK 방지를 위한 시퀀스 카운터
        # _wait_for_ready() 호출 시 seq를 1 증가시키고,
        # _ack_listener는 수신된 ACK의 seq가 현재 seq와 일치할 때만 event를 set함.
        self._ack_seq      = 0          # 현재 대기 중인 ACK 시퀀스 번호
        self._pending_seq  = 0          # 마지막으로 전송된 speech의 시퀀스 번호
        self._ready_event  = asyncio.Event()

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
        """
        발언을 전송한다.
        [FIX-T1] skip_wait 파라미터 완전 제거.
        호출 측에서 ACK 타임아웃을 직접 제어:
          - 본 발언: await self._wait_for_ready(SPEECH_ACK_TIMEOUT)
          - 지목 발언: await self._wait_for_ready(NOMINATE_ACK_TIMEOUT)
          - 반박 알림/판단: await self._wait_for_ready(NOTICE_ACK_TIMEOUT)
        """
        display     = f"의장 {member['name']}" if is_chair else f"{member['name']} 의원"
        model_str   = member.get("model", "?")
        engine_info = f"{member.get('engine','?')}/{model_str.split('/')[-1]}"
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        # [FIX-T2] 전송 전 pending_seq를 증가 — 이 발언의 ACK 시퀀스 등록
        self._pending_seq += 1
        seq = self._pending_seq

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
            ackSeq      = seq,   # [FIX-T2] 프론트가 ready ACK에 이 seq를 echo해야 함
        )
        self.speech_count[member["id"]] = self.speech_count.get(member["id"], 0) + 1

        # [FIX-T2] 이 seq의 ACK를 대기하도록 ack_seq 업데이트
        self._ack_seq = seq
        self._ready_event.clear()

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

    # ══════════════════════════════════════════════
    # ACK 대기 헬퍼 — [FIX-T2] 시퀀스 기반
    # ══════════════════════════════════════════════
    async def _wait_for_ready(self, timeout: float = SPEECH_ACK_TIMEOUT):
        """
        현재 _ack_seq 번호의 ACK를 기다린다.
        timeout 초 내에 수신되지 않으면 강제 진행.
        [FIX-T2] stale ACK 방지: _ack_listener가 seq 불일치 ACK는 무시하므로
                 이전 TTS의 늦은 ACK가 다음 발언 wait를 조기 해제하지 않음.
        """
        expected_seq = self._ack_seq
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            if timeout >= SPEECH_ACK_TIMEOUT:
                print(f"[Engine] ACK 타임아웃 (seq={expected_seq}) — 강제 진행")
            # 짧은 타임아웃(지목/알림)은 정상 경로이므로 로그 생략

    # ══════════════════════════════════════════════
    # ACK 리스너 — [FIX-T6] 시퀀스 검증
    # ══════════════════════════════════════════════
    async def _ack_listener(self):
        """
        프론트에서 오는 'ready' 메시지를 수신.
        [FIX-T6] 수신된 ACK의 ackSeq가 현재 _ack_seq와 일치할 때만 event를 set.
                 이전 TTS의 늦은 ACK(stale ACK)는 _ack_seq 불일치로 자동 폐기.
        프론트(DebateScreen.js)는 TTS onFinish 콜백에서:
          ws.send(JSON.stringify({ type: 'ready', ackSeq: receivedAckSeq }))
        형태로 ACK를 전송해야 한다.
        ackSeq 없이 { type: 'ready' }만 보내는 기존 프론트와의 하위 호환성을 위해:
          ackSeq 필드가 없으면 현재 seq와 무조건 매칭으로 처리.
        """
        try:
            while True:
                data = await self.ws.receive_text()
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ready":
                        recv_seq = msg.get("ackSeq", None)
                        if recv_seq is None:
                            # 하위 호환: seq 없으면 무조건 수락
                            self._ready_event.set()
                        elif int(recv_seq) == self._ack_seq:
                            # [FIX-T6] seq 일치 → set
                            self._ready_event.set()
                        else:
                            # [FIX-T6] stale ACK → 무시
                            print(
                                f"[ACK] stale ACK 폐기: recv_seq={recv_seq}, "
                                f"expected={self._ack_seq}"
                            )
                except Exception:
                    pass
        except Exception:
            self._ready_event.set()

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
        non_chair_names = [m["name"] for m in self.members if m["id"] != chair["id"]]
        non_chair_list_str = "\n".join(f"- {n}" for n in non_chair_names)
        messages = [
            {
                "role": "system",
                "content": (
                    f"당신은 의장 {chair['name']}입니다. 현재 토론 형식: [{self.debate_format}]\n"
                    f"참여 의원 목록 (발언 지목 대상):\n{non_chair_list_str}\n\n"
                    "역할: 사회자. 반드시 지시된 사회 멘트만 출력하세요.\n"
                    "⚠️ 절대 금지 사항:\n"
                    f"  1. 본인({chair['name']})을 발언자로 지목하거나 호명하는 것\n"
                    "  2. 의원의 발언 내용을 대신 생성하거나 이어 쓰는 것\n"
                    "     — 의원을 지목한 뒤 그 의원이 할 말을 절대 이어서 쓰지 마세요.\n"
                    "  3. 개인 주장이나 의견 표명\n"
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
                import re as _re
                parts = _re.split(r'(?<=[.!?。！？])\s+', cleaned)
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
            f"시간 관계상 최종 {round_num}라운드로 직행합니다. "
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
                "태그 없이 일반 논증을 이어가도 됩니다. 강요하지 않습니다.\n"
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
                    "【1라운드 — 입장 표명】\n"
                    "이것이 첫 발언입니다. 이 안건에 대한 본인의 찬반 입장과 핵심 근거를 명확히 밝히세요.\n"
                    "다른 의원 발언이 아직 없으므로, 본인 학습 데이터에서 나오는 사실과 논리를 펼치세요.\n"
                    "구체적 수치나 사례가 있으면 [DATA] 태그를 활용하세요."
                )
                action_guide += f"\n발언은 {MAX_SPEECH_LEN}자 이내."
            elif round_num == max_round:
                action_guide = (
                    "【최종 라운드 — 심층 결론】\n"
                    "이번이 마지막 발언 기회입니다. 반드시 다음 세 가지를 포함하세요:\n"
                    "① 이 토론에서 상대측이 제시한 논거 중 가장 강력했던 것을 의원 이름과 함께 직접 인용하세요.\n"
                    "   예: '제미나이 의원님의 처분적 법률 논거는 날카로웠습니다만…'\n"
                    "② 그 논거에 대한 최종 평가를 밝히세요 (수용하면 [ADMIT], 반박이면 [REFUTE]).\n"
                    "③ 당신의 최종 찬반 입장과 핵심 이유를 하나의 강한 문장으로 마무리하세요.\n"
                    "'반대를 유지합니다' 수준의 짧은 발언 금지. 반드시 상대 발언에 직접 반응하세요.\n"
                    "토론 전반을 종합한 깊이 있는 최종 발언을 300자 이상으로 작성하세요."
                )
                action_guide += f"\n발언은 최대 {MAX_SPEECH_LEN}자 (단, 300자 이상 권장)."
            else:
                action_guide = (
                    f"【{round_num}라운드 — 교차 검증】\n"
                    "직전 발언자의 이름을 직접 언급하며 발언을 시작하세요.\n"
                    "예: '라마 의원님께서 언급하신 ○○ 수치에는 중요한 맹점이 있습니다.'\n"
                    "오류가 있으면 [REFUTE], 더 타당하면 [ADMIT], 새 관점이면 [DATA]를 앞에 붙이세요.\n"
                    "태그가 적합하지 않으면 일반 논증으로 이어가도 됩니다."
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

        system = (
            f"당신은 AI 의회 토론 참여자입니다.\n"
            f"당신은 {member['name']} 의원입니다.\n\n"
            f"【당신의 정체성과 지식 기반】\n"
            f"{persona}\n"
            f"{stance_guide}\n"
            f"【당신의 이념적 성향: {bias}】\n"
            "이 성향은 당신의 진짜 관점입니다. 토론 내내 일관되게 유지하세요.\n"
            "다른 의원과 성향이 다르면 자연스럽게 의견 충돌이 발생합니다 — 이것이 정상입니다.\n\n"
            f"【핵심 원칙 — 반드시 준수】\n"
            "1. 주장은 반드시 '근거 → 논리 → 결론' 순서로 전개하라.\n"
            "2. 확실한 것은 자신 있게, 불확실한 것은 반드시 '불확실' 또는 '추정'으로 명시하라.\n"
            "3. 직전 의원 발언에 반드시 반응하라. 무시하거나 언급조차 않는 것은 금지.\n"
            "4. 이미 나온 주장을 반복하지 말고, 당신의 학습 기반에서 나오는 고유한 관점을 추가하라.\n\n"
            f"참여 의원 목록 (이 이름만 사용):\n{self.member_list_str}\n\n"
            f"【현재 토론 형식: {self.debate_format}】\n"
            f"{format_guide}\n\n"
            "발언 태그 (상황에 맞게 선택적 활용):\n"
            "[REFUTE]: 상대 논리·데이터에 명확한 오류가 있을 때. 발언 맨 앞에 한 번만.\n"
            "[ADMIT]: 상대 주장이 더 타당해서 본인 입장을 실제로 수정할 때. 발언 맨 앞에 한 번만.\n"
            "[DATA]: 객관적 수치·통계를 제시할 때. 예: [DATA] OECD 2023년 기준 15% 감소\n"
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
        """API 호출만 수행 (전송 없음) — 파이프라인 1단계."""
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
        """전송 + ACK 대기 — 파이프라인 2단계."""
        if len(opinion) > MAX_SPEECH_LEN:
            # 500자 초과 시 의장 개입
            await self.send_speech(member, opinion, stype, False)
            await self._wait_for_ready(SPEECH_ACK_TIMEOUT)
            intervene = await self.chair_intervene(chair, member)
            self.ctx.push(f"[의장 {chair['name']}]", intervene)
            await self.send_speech(chair, intervene, "NORMAL", True)
            # [FIX-T1] 의장 개입 발언도 충분한 ACK 대기
            await self._wait_for_ready(SPEECH_ACK_TIMEOUT)
        else:
            await self.send_speech(member, opinion, stype, False)
            await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

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
    # [FIX-T3] 반박 신청 처리 — skip_wait 제거
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
        """
        [REFUTE] 감지 후:
        1. 반박 신청 알림 전송 → NOTICE_ACK_TIMEOUT(8초) ACK 대기
        2. 의장 허가 판단 (LLM) → 판단 발언 전송 → NOTICE_ACK_TIMEOUT ACK 대기
        3. 허가 시 반박 발언 생성 → 전송 → SPEECH_ACK_TIMEOUT ACK 대기

        [FIX-T3] 기존 skip_wait=True(0.3초)를 전부 제거하고
        모든 발언에 적절한 타임아웃의 _wait_for_ready()를 적용.
        이로써 1→2→3 각 발언의 TTS가 완전히 끝난 후 다음 단계로 진행됨.
        """
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
            candidates = [m for m in non_chair if m["id"] != refuter["id"]]
            if not candidates:
                return
            candidate = random.choice(candidates)

        # ① 반박 신청 알림
        request_notice = (
            f"{refuter['name']} 의원님이 {candidate['name']} 의원 발언에 대한 반박을 신청합니다."
        )
        self.ctx.push(f"[{refuter['name']} 의원]", request_notice)
        await self.send_speech(refuter, request_notice, "NORMAL", False)
        # [FIX-T3] 0.3초 → NOTICE_ACK_TIMEOUT(8초) 타임아웃 ACK 대기
        await self._wait_for_ready(NOTICE_ACK_TIMEOUT)

        # ② 의장 허가 판단 (LLM 호출이 ACK 대기 시간 중 일어남)
        allow, judge_speech = await self.chair_judge_rebuttal(
            chair, refuter, refute_speech
        )
        self.ctx.push(f"[의장 {chair['name']}]", judge_speech)
        await self.send_speech(chair, judge_speech, "NORMAL", True)
        # [FIX-T3] 0.3초 → NOTICE_ACK_TIMEOUT(8초) 타임아웃 ACK 대기
        await self._wait_for_ready(NOTICE_ACK_TIMEOUT)
        await self.ctx.compress_if_needed()

        if not allow:
            return

        # ③ 반박 발언
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
        # [FIX-T3] 반박 발언은 본 발언이므로 SPEECH_ACK_TIMEOUT
        await self._wait_for_ready(SPEECH_ACK_TIMEOUT)
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
        stance = self._stance_map.get(member["id"], "FREE")
        stance_note = ""
        if stance == "FOR":
            stance_note = "\n※ 당신은 이 토론에서 찬성 측 논거를 맡았습니다. 최종 투표는 토론 결과에 따라 자유롭게 결정하십시오."
        elif stance == "AGAINST":
            stance_note = "\n※ 당신은 이 토론에서 반대 측 논거를 맡았습니다. 최종 투표는 토론 결과에 따라 자유롭게 결정하십시오."

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
                    "- 최우선 기준: 위 【토론 결과 반영 — 확신도】를 따르세요. 확신도가 실제 설득 결과입니다.\n"
                    "- 당신의 이념적 성향(bias)과 투표 경향도 반영하세요.\n"
                    "- 다른 의원들과 성향이 다르므로 투표 결과가 달라도 됩니다 — 이것이 올바른 토론입니다.\n"
                    "- 발언이 없었던 경우에도 전체 토론 내용을 숙지하고 최종 판단을 내리십시오.\n"
                    "- 형식: [찬성|반대|기권] 이유 (200자 이내, 완전한 문장으로)\n"
                    "⚠️ 1라운드에서 찬성 논거를 개진했더라도, 토론 중 설득당했다면 반대 투표할 수 있습니다."
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
                    "  【결의 제3조】 이행 방안 또는 후속 조치 (토론 내용에서 도출)\n"
                    "  【부기】 소수 의견 또는 유보 입장 ([REFUTE]로 끝까지 반박된 견해 반영)\n\n"
                    "작성 원칙:\n"
                    "  - '찬성 N명 반대 M명' 식의 표결 집계 문구를 절대 넣지 마세요.\n"
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
        # [정합성 수정③] _resolution_callers() 직접 호출 제거.
        # 기존: _resolution_callers()가 call_groq/call_gemini/call_openrouter를
        #       버킷·세마포어 없이 직접 호출 → 레이트리밋 보호 우회 문제.
        # 수정: call_member()를 사용하여 기존 발언 호출과 동일한 레이트리밋 보호 및
        #       폴백 체인을 적용. max_tokens를 600으로 늘리기 위해 call_member에서
        #       호출하는 각 엔진 함수의 기본값(300)을 오버라이드할 수 없으므로,
        #       결의문 전용으로 messages에 토큰 힌트를 포함하고 call_member 사용.
        # 결의문은 발언 당 1회만 호출되므로 레이트리밋 영향 미미.
        # 단, max_tokens 600이 필요하므로 call_member 대신 직접 호출하되
        #   세마포어와 버킷은 동일하게 적용하는 하이브리드로 처리.
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
                    await self._wait_for_ready(SPEECH_ACK_TIMEOUT)
                    self._chair_final_spoke = True
                    print(f"[Engine] 의장 {chair['name']} 최종 소견 발언 완료")
            except Exception as e:
                print(f"[Engine] 의장 최종 소견 실패 (무시): {e}")

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
            conclusion_term = "찬반 표결" if self.conclusion_type == "VOTE" else "공동 결의안 채택"
            close_instruction = (
                f"총 {self.rounds}라운드의 토론이 완료되었습니다. {conclusion_term}을 진행하겠습니다."
            )
        close_text = await self.chair_speak(chair, close_instruction, max_chars=CHAIR_MAX_LEN)
        self.ctx.push(f"[의장 {chair['name']}]", close_text)
        await self.send_speech(chair, close_text, "NORMAL", True)
        await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

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
    # 찬반 역할 배정
    # ══════════════════════════════════════════════
    def _assign_stances(self, chair_id: str):
        from conviction_tracker import _INITIAL_CONVICTION

        self._stance_map = {}
        for m in self.members:
            if m["id"] == chair_id:
                self._stance_map[m["id"]] = "FREE"
                continue
            bias = m.get("bias", "중립")
            init_conviction = _INITIAL_CONVICTION.get(bias, 0)
            self._stance_map[m["id"]] = "FOR" if init_conviction > 0 else "AGAINST"

        non_chair_stances = [
            (m, self._stance_map[m["id"]])
            for m in self.members if m["id"] != chair_id
        ]
        for_members     = [m for m, s in non_chair_stances if s == "FOR"]
        against_members = [m for m, s in non_chair_stances if s == "AGAINST"]

        if not for_members:
            best = max(against_members,
                       key=lambda m: _INITIAL_CONVICTION.get(m.get("bias","중립"), 0))
            self._stance_map[best["id"]] = "FOR"
            print(f"[Engine] 전원 반대 보정: {best['name']} → FOR")
        elif not against_members:
            worst = min(for_members,
                        key=lambda m: _INITIAL_CONVICTION.get(m.get("bias","중립"), 0))
            self._stance_map[worst["id"]] = "AGAINST"
            print(f"[Engine] 전원 찬성 보정: {worst['name']} → AGAINST")

        print(f"[Engine] 찬반 역할 배정: { {m['name']: self._stance_map[m['id']] for m in self.members} }")

    def _get_stance_guide(self, member_id: str) -> str:
        stance = self._stance_map.get(member_id, "FREE")
        if stance == "FOR":
            return (
                "\n【역할 배정: 찬성 측】\n"
                "이 토론에서 당신은 찬성 측 논거를 중심으로 발언합니다.\n"
                "이것은 당신의 bias(이념적 성향)와 일치하는 자연스러운 입장입니다.\n"
                "찬성 입장을 뒷받침하는 가장 강력한 논거를 당신의 전문 지식에서 찾아 제시하세요.\n"
                "단, 상대측의 강한 반박에 설득된다면 [ADMIT]로 솔직하게 인정해도 됩니다.\n"
            )
        elif stance == "AGAINST":
            return (
                "\n【역할 배정: 반대 측】\n"
                "이 토론에서 당신은 반대 측 논거를 중심으로 발언합니다.\n"
                "이것은 당신의 bias(이념적 성향)와 일치하는 자연스러운 입장입니다.\n"
                "반대 입장을 뒷받침하는 가장 강력한 논거를 당신의 전문 지식에서 찾아 제시하세요.\n"
                "단, 상대측의 강한 논거에 설득된다면 [ADMIT]로 솔직하게 인정해도 됩니다.\n"
            )
        return ""

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
    # [FIX-T5] 지목 발언 헬퍼 — nominate + 짧은 ACK 대기를 묶음
    # ══════════════════════════════════════════════
    async def _nominate(self, chair: dict, member: dict, text: str = None):
        """
        의장이 의원을 지목하는 짧은 발언을 전송하고
        NOMINATE_ACK_TIMEOUT(5초) 안에 ACK를 기다린다.
        [FIX-T5] 기존 skip_wait=True(0.3초)를 대체.
        TTS가 5초 내 완료되면 ACK 즉시 수신 → 바로 다음 단계.
        5초 초과 시 자동 진행 (짧은 지목 문장에서 5초 초과는 TTS 지연 상황).
        """
        nominate = text or f"{member['name']} 의원님, 발언해 주시기 바랍니다."
        self.ctx.push(f"[의장 {chair['name']}]", nominate)
        await self.send_speech(chair, nominate, "NORMAL", True)
        await self._wait_for_ready(NOMINATE_ACK_TIMEOUT)

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

        self._assign_stances(chair["id"])
        self.start_time = time.time()

        ack_task = asyncio.create_task(self._ack_listener())

        dispatch = {
            "릴레이":     self._run_relay,
            "집중토론":   self._run_focused,
            "전문가패널": self._run_panel,
            "자유토론":   self._run_free,
        }
        runner = dispatch.get(self.debate_format, self._run_relay)
        try:
            await runner(chair)
        finally:
            ack_task.cancel()
            try:
                await ack_task
            except asyncio.CancelledError:
                pass

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
            f"오늘 상정된 안건은 \"{self.issue}\"입니다. "
            f"이 안건은 우리 사회에서 중요한 의미를 지니며, 다양한 관점에서 심도 있는 논의가 필요합니다. "
            f"본 토론은 {self.rounds}라운드 릴레이 형식으로 진행되며, 총 {self.duration}분이 주어집니다. "
            f"의원 여러분은 각자의 전문 지식과 학습 데이터를 바탕으로 논거를 제시해 주시기 바랍니다. "
            f"이제 제1라운드를 시작합니다."
        )
        if first_member:
            open_instruction += f" 첫 번째 발언자로 {first_member['name']} 의원님께 발언권을 드립니다."
        open_text = await self.chair_speak(chair, open_instruction, max_chars=CHAIR_MAX_LEN + 150)
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)
        await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

        _next_order_override = None

        for round_num in range(1, self.rounds + 1):
            self.current_round = round_num

            if self._time_over():
                print(f"[Engine] 릴레이 시간 초과 ({self._elapsed_minutes():.1f}분) — 의결로 이동")
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

            prefetch_task   = None
            prefetch_member = None

            for idx, m in enumerate(order):
                if self._time_over():
                    if prefetch_task:
                        prefetch_task.cancel()
                        try:
                            await prefetch_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    await self.run_conclusion(chair, timed_out=True)
                    return

                # [FIX-T5] 지목 발언: skip_wait → _nominate() 사용
                if idx > 0:
                    await self._nominate(chair, m)

                if prefetch_task is None or prefetch_member is None or prefetch_member["id"] != m["id"]:
                    if prefetch_task is not None:
                        prefetch_task.cancel()
                        try:
                            await prefetch_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        prefetch_task = None
                    cur_opinion, cur_stype = await self.prepare_speech(
                        chair, m, fmt_guide, round_num
                    )
                else:
                    cur_opinion, cur_stype = await prefetch_task
                    prefetch_task = None

                next_idx = idx + 1
                if next_idx < len(order) and not self._time_over():
                    next_m = order[next_idx]
                    prefetch_member = next_m
                    prefetch_task = asyncio.create_task(
                        self.prepare_speech(chair, next_m, fmt_guide, round_num)
                    )
                else:
                    prefetch_task   = None
                    prefetch_member = None

                await self.deliver_speech(
                    chair, m, cur_opinion, cur_stype,
                    non_chair, fmt_guide, round_num
                )

            if round_num < self.rounds:
                elapsed_ratio = self._elapsed_minutes() / self.duration if self.duration > 0 else 1.0

                if elapsed_ratio >= 0.85 and (self.rounds - round_num) >= 2:
                    final_order = non_chair.copy()
                    random.shuffle(final_order)
                    final_first = final_order[0] if final_order else None

                    final_text = await self.chair_announce_round(chair, self.rounds, first_member=final_first)
                    self.ctx.push(f"[의장 {chair['name']}]", final_text)
                    await self.send_speech(chair, final_text, "NORMAL", True)
                    await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

                    _final_timed_out = False
                    _fpf = None
                    _fpf_member = None
                    for _fi, fm in enumerate(final_order):
                        if self._time_over():
                            if _fpf:
                                _fpf.cancel()
                                try:
                                    await _fpf
                                except (asyncio.CancelledError, Exception):
                                    pass
                            _final_timed_out = True
                            break
                        # [FIX-T5] 첫 번째 제외, 지목 발언 모두 _nominate() 사용
                        if _fi > 0:
                            await self._nominate(chair, fm)
                        if _fpf is None or _fpf_member is None or _fpf_member["id"] != fm["id"]:
                            if _fpf is not None:
                                _fpf.cancel()
                                try:
                                    await _fpf
                                except (asyncio.CancelledError, Exception):
                                    pass
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
                    if _fpf is not None:
                        _fpf.cancel()
                        try:
                            await _fpf
                        except (asyncio.CancelledError, Exception):
                            pass
                        _fpf = None
                    break
                else:
                    if self._time_over():
                        await self.run_conclusion(chair, timed_out=True)
                        return
                    next_order = non_chair.copy()
                    random.shuffle(next_order)
                    next_first = next_order[0] if next_order else None
                    transition = await self.chair_transition_round(chair, round_num, first_member=next_first)
                    self.ctx.push(f"[의장 {chair['name']}]", transition)
                    await self.send_speech(chair, transition, "NORMAL", True)
                    await self._wait_for_ready(SPEECH_ACK_TIMEOUT)
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
            f"오늘 상정된 안건은 \"{self.issue}\"이며, 이는 면밀한 검토와 논의가 요구되는 사안입니다. "
            f"본 토론은 집중토론 형식으로, 핵심 토론자인 {d_names}께서 {self.rounds}라운드에 걸쳐 집중 대결합니다. "
            f"그 외 의원들은 이후 질의 시간에 발언 기회가 주어집니다. "
            f"1라운드를 시작합니다. 먼저 {debaters[0]['name']} 의원님, 발언해 주십시오.",
            max_chars=CHAIR_MAX_LEN + 150,
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)
        await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

        _focused_next_pair = None

        for round_num in range(1, self.rounds + 1):
            self.current_round = round_num

            if self._time_over():
                await self.run_conclusion(chair, timed_out=True)
                return

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
                if self._time_over():
                    if _pf:
                        _pf.cancel()
                        try:
                            await _pf
                        except (asyncio.CancelledError, Exception):
                            pass
                    await self.run_conclusion(chair, timed_out=True)
                    return
                # [FIX-T5] 두 번째 토론자 지목도 _nominate()
                if _pi > 0:
                    await self._nominate(chair, m,
                        text=f"{m['name']} 의원님, 발언해 주십시오.")
                if _pf is None or _pf_member is None or _pf_member["id"] != m["id"]:
                    if _pf is not None:
                        _pf.cancel()
                        try:
                            await _pf
                        except (asyncio.CancelledError, Exception):
                            pass
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
                if self._time_over():
                    await self.run_conclusion(chair, timed_out=True)
                    return
                _focused_next_pair = debaters.copy()
                random.shuffle(_focused_next_pair)
                next_first = _focused_next_pair[0] if _focused_next_pair else None
                transition = await self.chair_transition_round(chair, round_num, first_member=next_first)
                self.ctx.push(f"[의장 {chair['name']}]", transition)
                await self.send_speech(chair, transition, "NORMAL", True)
                await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

        _obs_timed_out = False

        if observers and not self._time_over():
            qa_open = await self.chair_speak(
                chair,
                "핵심토론이 완료되었습니다. 나머지 의원님들의 질의 시간입니다.",
                max_chars=CHAIR_MAX_LEN,
            )
            self.ctx.push(f"[의장 {chair['name']}]", qa_open)
            await self.send_speech(chair, qa_open, "NORMAL", True)
            await self._wait_for_ready(SPEECH_ACK_TIMEOUT)
            _of = None
            _of_member = None
            for _oi, m in enumerate(observers):
                if self._time_over():
                    if _of:
                        _of.cancel()
                        try:
                            await _of
                        except (asyncio.CancelledError, Exception):
                            pass
                    _obs_timed_out = True
                    break
                # [FIX-T5]
                await self._nominate(chair, m,
                    text=f"{m['name']} 의원님, 질의해 주십시오.")
                if _of is not None and _of_member is not None and _of_member["id"] == m["id"]:
                    _oo, _os2 = await _of
                    _of = None
                    _of_member = None
                else:
                    if _of is not None:
                        _of.cancel()
                        try:
                            await _of
                        except (asyncio.CancelledError, Exception):
                            pass
                        _of = None
                        _of_member = None
                    _oo, _os2 = await self.prepare_speech(chair, m, fmt_guide, max(1, self.rounds - 1))
                _ni = _oi + 1
                if _ni < len(observers) and not self._time_over():
                    _of_member = observers[_ni]
                    _of = asyncio.create_task(self.prepare_speech(chair, _of_member, fmt_guide, max(1, self.rounds - 1)))
                else:
                    _of = None
                    _of_member = None
                await self.deliver_speech(chair, m, _oo, _os2, non_chair, fmt_guide, max(1, self.rounds - 1))

        await self.run_conclusion(chair, timed_out=_obs_timed_out or self._time_over())

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
        await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

        panel_start = await self.chair_speak(
            chair,
            "패널 심층 발언을 시작합니다. 각 패널은 전문 분야의 깊이 있는 분석을 제시해 주십시오.",
            max_chars=120,
        )
        self.ctx.push(f"[의장 {chair['name']}]", panel_start)
        await self.send_speech(chair, panel_start, "NORMAL", True)
        await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

        _pnf = None
        _pnf_member = None
        for _pni, m in enumerate(panels):
            if self._time_over():
                if _pnf:
                    _pnf.cancel()
                    try:
                        await _pnf
                    except (asyncio.CancelledError, Exception):
                        pass
                await self.run_conclusion(chair, timed_out=True)
                return
            # [FIX-T5]
            await self._nominate(chair, m,
                text=f"패널 {m['name']} 의원님, 전문가 발언을 시작해 주십시오.")
            if _pnf is not None and _pnf_member is not None and _pnf_member["id"] == m["id"]:
                _pno, _pns = await _pnf
                _pnf = None
                _pnf_member = None
            else:
                if _pnf is not None:
                    _pnf.cancel()
                    try:
                        await _pnf
                    except (asyncio.CancelledError, Exception):
                        pass
                    _pnf = None
                    _pnf_member = None
                _pno, _pns = await self.prepare_speech(chair, m, fmt_guide, 1)
            _ni = _pni + 1
            if _ni < len(panels) and not self._time_over():
                _pnf_member = panels[_ni]
                _pnf = asyncio.create_task(self.prepare_speech(chair, _pnf_member, fmt_guide, 1))
            else:
                _pnf = None
                _pnf_member = None
            await self.deliver_speech(chair, m, _pno, _pns, non_chair, fmt_guide, 1)

        if _pnf is not None:
            _pnf.cancel()
            try:
                await _pnf
            except (asyncio.CancelledError, Exception):
                pass
            _pnf = None

        summary = await self.chair_speak(
            chair,
            "패널 심층 발언이 완료되었습니다. 전체 질의·응답 시간을 시작합니다.",
            max_chars=CHAIR_MAX_LEN,
        )
        self.ctx.push(f"[의장 {chair['name']}]", summary)
        await self.send_speech(chair, summary, "NORMAL", True)
        await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

        _panel_timed_out = False
        qa_rounds = max(1, self.rounds - 1)
        for qa_round in range(1, qa_rounds + 1):
            if self._time_over():
                _panel_timed_out = True
                break
            if general:
                qa_text = await self.chair_speak(
                    chair,
                    f"전체 질의·응답 {qa_round}라운드를 시작합니다.",
                    max_chars=100,
                )
                self.ctx.push(f"[의장 {chair['name']}]", qa_text)
                await self.send_speech(chair, qa_text, "NORMAL", True)
                await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

                shuffled_gen = general.copy()
                random.shuffle(shuffled_gen)
                _gf = None
                _gf_member = None
                for _gi, m in enumerate(shuffled_gen):
                    if self._time_over():
                        if _gf:
                            _gf.cancel()
                            try:
                                await _gf
                            except (asyncio.CancelledError, Exception):
                                pass
                        _panel_timed_out = True
                        break
                    # [FIX-T5]
                    await self._nominate(chair, m,
                        text=f"{m['name']} 의원님, 패널에 질의해 주십시오.")
                    if _gf is not None and _gf_member is not None and _gf_member["id"] == m["id"]:
                        _go, _gs = await _gf
                        _gf = None
                        _gf_member = None
                    else:
                        if _gf is not None:
                            _gf.cancel()
                            try:
                                await _gf
                            except (asyncio.CancelledError, Exception):
                                pass
                            _gf = None
                            _gf_member = None
                        _go, _gs = await self.prepare_speech(chair, m, fmt_guide, max(1, self.rounds - 1))
                    _ni = _gi + 1
                    if _ni < len(shuffled_gen) and not self._time_over():
                        _gf_member = shuffled_gen[_ni]
                        _gf = asyncio.create_task(self.prepare_speech(chair, _gf_member, fmt_guide, max(1, self.rounds - 1)))
                    else:
                        _gf = None
                        _gf_member = None
                    await self.deliver_speech(chair, m, _go, _gs, non_chair, fmt_guide, max(1, self.rounds - 1))

            _rf = None
            _rf_member = None
            for _ri, p in enumerate(panels):
                if self._time_over():
                    if _rf:
                        _rf.cancel()
                        try:
                            await _rf
                        except (asyncio.CancelledError, Exception):
                            pass
                    _panel_timed_out = True
                    break
                # [FIX-T5]
                await self._nominate(chair, p,
                    text=f"패널 {p['name']} 의원님, 질의에 응답해 주십시오.")
                if _rf is not None and _rf_member is not None and _rf_member["id"] == p["id"]:
                    _ro, _rs = await _rf
                    _rf = None
                    _rf_member = None
                else:
                    if _rf is not None:
                        _rf.cancel()
                        try:
                            await _rf
                        except (asyncio.CancelledError, Exception):
                            pass
                        _rf = None
                        _rf_member = None
                    _ro, _rs = await self.prepare_speech(chair, p, fmt_guide, max(1, self.rounds - 1))
                _ni = _ri + 1
                if _ni < len(panels) and not self._time_over():
                    _rf_member = panels[_ni]
                    _rf = asyncio.create_task(self.prepare_speech(chair, _rf_member, fmt_guide, max(1, self.rounds - 1)))
                else:
                    _rf = None
                    _rf_member = None
                await self.deliver_speech(chair, p, _ro, _rs, non_chair, fmt_guide, max(1, self.rounds - 1))

        await self.run_conclusion(chair, timed_out=_panel_timed_out or self._time_over())

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

        deadline_mins  = self.duration
        warn_threshold = deadline_mins * 0.8

        open_text = await self.chair_speak(
            chair,
            f"지금부터 AI 의회 본회의를 개회합니다. "
            f"오늘 상정된 안건은 \"{self.issue}\"입니다. "
            f"이 안건은 다양한 관점에서 심층적 검토가 필요한 사안으로, "
            f"본 의회는 자유토론 형식으로 논의를 진행합니다. "
            f"총 {deadline_mins}분 또는 최대 {self.max_free_turns}회 발언 한도 내에서 "
            "순서 제한 없이 자유롭게 발언하실 수 있습니다. "
            "시간 또는 발언 한도 종료 후에는 즉시 최종 의결로 이행합니다.",
            max_chars=CHAIR_MAX_LEN + 150,
        )
        self.ctx.push(f"[의장 {chair['name']}]", open_text)
        await self.send_speech(chair, open_text, "NORMAL", True)
        await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

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
                await self._wait_for_ready(SPEECH_ACK_TIMEOUT)
                if self._time_over() or turn >= self.max_free_turns:
                    break

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
                        f"({int(elapsed)}분 {int((elapsed % 1)*60)}초 / {deadline_mins}분 "
                        f"| {turn+1}/{self.max_free_turns}회)")

            _fo, _fs = await self.prepare_speech(chair, speaker, fmt_guide, 1, free_mode=True)
            await self.deliver_speech(chair, speaker, _fo, _fs, non_chair, fmt_guide, 1, free_mode=True)
            turn += 1

            if turn > 0 and turn % 5 == 0 and not self._time_over() and turn < self.max_free_turns:
                inter = await self.chair_speak(
                    chair,
                    "잠시 중간 정리를 하겠습니다. 현재까지의 주요 찬반 논점을 요약하고 자유토론을 계속합니다.",
                    max_chars=160,
                )
                self.ctx.push(f"[의장 {chair['name']}]", inter)
                await self.send_speech(chair, inter, "NORMAL", True)
                await self._wait_for_ready(SPEECH_ACK_TIMEOUT)

        print(f"[Engine] 자유토론 종료: {turn}회 발언 / {self._elapsed_minutes():.1f}분 경과")
        await self.run_conclusion(chair, timed_out=self._time_over())