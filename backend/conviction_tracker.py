"""
conviction_tracker.py — 의원별 확신도 추적기

각 의원의 찬반 확신도를 -100(완전 반대) ~ +100(완전 찬성) 범위로 추적합니다.

동작 원리:
  1. 발언 후 경량 LLM 호출로 논리 점수(1~10)와 설득 방향을 평가
  2. [ADMIT] → 발언자 본인 확신도를 상대 방향으로 이동
  3. [REFUTE] → 논리 점수가 높으면 반박 대상 의원 확신도를 이동
  4. 최종 투표 시 확신도를 반영하여 실제 토론 결과가 표결에 영향

bias → 초기 확신도 매핑:
  '진보·개혁·공정'  → +55   (안건이 변화/개혁적일 때 찬성 경향)
  '자유주의·분권'   → +30   (개인 자유 방향 시 찬성, 규제 시 반대)
  '실용·데이터중심' → +10   (데이터 보기 전 중립)
  '보수·안정·점진'  → -30   (급진적 변화에 회의적)
  기타              → 0

LLM 평가 비용 최소화:
  - 매 발언마다 호출하지 않고 [ADMIT]/[REFUTE] 태그 발언에만 호출
  - 실패 시 태그 기반 고정 이동으로 폴백
"""

import re
import json
import asyncio
from ai_caller import call_groq, call_gemini


# bias → 초기 확신도
# [정합성 수정④] "기술주의·효율" 키 제거.
# members.py의 5개 의원 중 해당 bias를 가진 의원이 없는 미사용 항목이었음.
_INITIAL_CONVICTION = {
    "진보·개혁·공정":  55,
    "자유주의·분권":   30,
    "실용·데이터중심": 10,
    "보수·안정·점진": -30,
}
_MOVE_DECAY = 0.85   # 발언이 쌓일수록 설득 효과 감소 (경화 효과)


class ConvictionTracker:
    def __init__(self, members: list, issue: str):
        self.issue   = issue
        self.members = {m["id"]: m for m in members}

        # 초기 확신도 설정
        self.convictions: dict[str, float] = {}
        for m in members:
            bias = m.get("bias", "중립")
            self.convictions[m["id"]] = float(_INITIAL_CONVICTION.get(bias, 0))

        # 발언 횟수 (경화 효과 계산용)
        self.speech_counts: dict[str, int] = {m["id"]: 0 for m in members}

        # 변화 이력 (프론트 전송용)
        self.history: list[dict] = []

        # [REFUTE] 발언자 → 직전 발언자 매핑 (평가 대상 추적)
        self._last_speakers: list[str] = []

    def _clamp(self, val: float) -> float:
        return max(-100.0, min(100.0, val))

    def get_conviction(self, member_id: str) -> float:
        return self.convictions.get(member_id, 0.0)

    def get_all(self) -> dict:
        return {mid: round(v, 1) for mid, v in self.convictions.items()}

    def record_speech(self, member_id: str):
        """발언 기록 (경화 효과 누적)"""
        self.speech_counts[member_id] = self.speech_counts.get(member_id, 0) + 1
        self._last_speakers.append(member_id)
        if len(self._last_speakers) > 10:
            self._last_speakers = self._last_speakers[-10:]

    def _decay_factor(self, member_id: str) -> float:
        """발언 횟수가 많을수록 설득 효과 감소 (경화)"""
        n = self.speech_counts.get(member_id, 0)
        return _MOVE_DECAY ** min(n, 6)

    def _find_target_of_refute(self, refuter_id: str) -> str | None:
        """[REFUTE] 발언의 반박 대상(직전 다른 발언자) 찾기"""
        for mid in reversed(self._last_speakers):
            if mid != refuter_id:
                return mid
        return None

    async def evaluate_speech(
        self,
        speaker_id: str,
        speech: str,
        speech_type: str,  # "ADMIT", "REFUTE", "NORMAL"
    ) -> dict:
        """
        발언을 평가하고 확신도를 업데이트합니다.
        반환: {changes: [{memberId, before, after, reason}], scores: {logic, persuasion}}
        """
        changes = []

        if speech_type == "ADMIT":
            changes = await self._handle_admit(speaker_id, speech)
        elif speech_type == "REFUTE":
            changes = await self._handle_refute(speaker_id, speech)
        # NORMAL 발언은 확신도 변화 없음 (단, 발언 횟수는 기록됨)

        self.record_speech(speaker_id)
        for ch in changes:
            self.history.append(ch)

        return {"changes": changes}

    async def _handle_admit(self, speaker_id: str, speech: str) -> list[dict]:
        """
        [ADMIT]: 발언자 본인이 상대 논리를 인정 → 본인 확신도 이동

        이동 방향은 인정한 내용을 평가해 결정:
          - 반대 방향 논리를 인정 → 찬성 방향으로 이동 (+)
          - 찬성 방향 논리를 인정 → 반대 방향으로 이동 (-)
        """
        before = self.convictions[speaker_id]
        decay  = self._decay_factor(speaker_id)

        # LLM으로 인정 방향과 크기 평가
        direction, magnitude = await self._eval_admit_direction(speaker_id, speech)

        move   = direction * magnitude * decay
        after  = self._clamp(before + move)
        self.convictions[speaker_id] = after

        member = self.members[speaker_id]
        return [{
            "memberId": speaker_id,
            "name":     member["name"],
            "before":   round(before, 1),
            "after":    round(after, 1),
            "delta":    round(after - before, 1),
            "reason":   f"[ADMIT] 인정으로 확신도 이동 ({'+' if move >= 0 else ''}{move:.1f})",
            "trigger":  "ADMIT",
        }]

    async def _handle_refute(self, speaker_id: str, speech: str) -> list[dict]:
        """
        [REFUTE]: 논리 점수 평가 후 점수가 높으면 반박 대상 의원 확신도 이동

        논리 점수 7 이상: 반박 대상 확신도를 발언자 방향으로 이동
        논리 점수 4~6: 소폭 이동
        논리 점수 3 이하: 변화 없음
        """
        target_id = self._find_target_of_refute(speaker_id)
        if not target_id or target_id not in self.convictions:
            return []

        logic_score, direction = await self._eval_refute_logic(speaker_id, target_id, speech)

        changes = []

        if logic_score >= 7:
            move_size = (logic_score - 5) * 3.0  # 7점→6, 10점→15
        elif logic_score >= 4:
            move_size = (logic_score - 3) * 1.5  # 4점→1.5, 6점→4.5
        else:
            return []  # 논리 미달 → 효과 없음

        # 반박 대상은 발언자의 확신도 방향으로 이동
        refuter_conviction = self.convictions[speaker_id]
        target_conviction  = self.convictions[target_id]

        # 발언자가 찬성(+)이면 대상을 찬성 방향으로, 반대(-)이면 반대 방향으로
        # [BUG-1 수정] 대소비교 → 부호 기반으로 변경.
        # 기존: refuter_conviction > target_conviction (대소 비교)
        # 문제: refuter=-5, target=-30이면 -5 > -30 → +1.0 → 반대측이 반박했는데
        #       target을 찬성 방향으로 이동시키는 역효과.
        # 수정: refuter 확신도의 부호로 결정. 찬성측(>0) 반박 → target을 찬성(+)으로,
        #       반대측(<=0) 반박 → target을 반대(-)로 끌어당김.
        move_direction = 1.0 if refuter_conviction > 0 else -1.0
        decay = self._decay_factor(target_id)
        move  = move_direction * move_size * decay

        before = target_conviction
        after  = self._clamp(before + move)
        self.convictions[target_id] = after

        target_member  = self.members[target_id]
        refuter_member = self.members[speaker_id]
        changes.append({
            "memberId": target_id,
            "name":     target_member["name"],
            "before":   round(before, 1),
            "after":    round(after, 1),
            "delta":    round(after - before, 1),
            "reason":   (
                f"{refuter_member['name']}의 [REFUTE] (논리점수 {logic_score}/10)로 "
                f"확신도 이동 ({'+' if move >= 0 else ''}{move:.1f})"
            ),
            "trigger":  "REFUTE",
            "logicScore": logic_score,
        })
        return changes

    async def _eval_admit_direction(self, speaker_id: str, speech: str) -> tuple[float, float]:
        """
        [ADMIT] 발언을 보고 인정 방향(+1 찬성/-1 반대)과 크기(1~15) 반환
        실패 시 기본값: 반대 방향으로 10 이동 (찬성 방향 주장을 인정했다고 가정)
        """
        speaker = self.members[speaker_id]
        current = self.convictions[speaker_id]

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 토론 분석 AI입니다. 아래 발언에서 [ADMIT] 태그가 포함된 부분을 분석하세요.\n"
                    "반드시 JSON으로만 응답하세요:\n"
                    "{\"direction\": 1 또는 -1, \"magnitude\": 1~15}\n"
                    "direction: +1이면 발언자가 찬성 방향 주장을 인정한 것(→ 찬성도 증가),\n"
                    "           -1이면 반대 방향 주장을 인정한 것(→ 찬성도 감소)\n"
                    "magnitude: 인정의 깊이 (1=가벼운 인정, 15=핵심 입장 전환)"
                )
            },
            {
                "role": "user",
                "content": (
                    f"안건: \"{self.issue}\"\n"
                    f"발언자({speaker['name']}) 현재 찬성도: {current:.0f} (-100~+100)\n"
                    f"발언: \"{speech[:300]}\"\n\n"
                    "JSON으로만 응답하세요."
                )
            }
        ]

        try:
            raw = await _call_eval(messages)
            s = raw.find('{'); e = raw.rfind('}')
            if s != -1 and e != -1:
                parsed = json.loads(raw[s:e+1])
                direction = float(parsed.get("direction", -1))
                magnitude = float(parsed.get("magnitude", 10))
                direction = 1.0 if direction > 0 else -1.0
                magnitude = max(1.0, min(15.0, magnitude))
                return direction, magnitude
        except Exception as ex:
            print(f"[ConvictionTracker] ADMIT 평가 실패: {ex}")

        # 폴백: 현재 확신도 반대 방향으로 10 이동 (입장 전환 신호)
        return (-1.0 if current >= 0 else 1.0), 10.0

    async def _eval_refute_logic(
        self, refuter_id: str, target_id: str, speech: str
    ) -> tuple[int, float]:
        """
        [REFUTE] 논리 점수(1~10)와 방향 반환
        실패 시 기본값: 점수 5 (중간 효과)
        """
        refuter = self.members[refuter_id]
        target  = self.members[target_id]

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 토론 심판 AI입니다. 아래 [REFUTE] 발언의 논리 점수를 평가하세요.\n"
                    "평가 기준:\n"
                    "  10: 반박 대상의 핵심 주장을 구체적 수치/사례로 완전히 논파\n"
                    "  7~9: 논리적 허점을 명확히 지적하고 근거 제시\n"
                    "  4~6: 부분적 반박, 근거 불충분\n"
                    "  1~3: 감정적 반박, 논리 없음\n"
                    "반드시 JSON으로만 응답하세요: {\"score\": 1~10, \"reason\": \"평가 이유 30자 이내\"}"
                )
            },
            {
                "role": "user",
                "content": (
                    f"안건: \"{self.issue}\"\n"
                    f"반박자: {refuter['name']} / 반박 대상: {target['name']}\n"
                    f"발언: \"{speech[:400]}\"\n\n"
                    "JSON으로만 응답하세요."
                )
            }
        ]

        try:
            raw = await _call_eval(messages)
            s = raw.find('{'); e = raw.rfind('}')
            if s != -1 and e != -1:
                parsed = json.loads(raw[s:e+1])
                score = int(parsed.get("score", 5))
                score = max(1, min(10, score))
                return score, 1.0
        except Exception as ex:
            print(f"[ConvictionTracker] REFUTE 평가 실패: {ex}")

        return 5, 1.0  # 폴백: 중간 효과

    def conviction_to_vote_instruction(self, member_id: str) -> str:
        """
        확신도를 최종 투표 지시문으로 변환
        debate_engine.get_vote()에서 system prompt에 추가
        """
        val = self.convictions.get(member_id, 0)
        m   = self.members.get(member_id, {})
        name = m.get("name", "")

        if val >= 60:
            tendency = "강하게 찬성 (확신도 매우 높음)"
            note     = "찬성 투표가 자연스럽습니다."
        elif val >= 25:
            tendency = "찬성 (확신도 높음)"
            note     = "찬성 투표가 적절합니다."
        elif val >= 5:
            tendency = "약한 찬성 (확신도 낮음)"
            note     = "찬성 또는 기권이 적절합니다."
        elif val >= -5:
            tendency = "중립 (확신도 없음)"
            note     = "기권이 가장 자연스럽습니다."
        elif val >= -25:
            tendency = "약한 반대 (확신도 낮음)"
            note     = "반대 또는 기권이 적절합니다."
        elif val >= -60:
            tendency = "반대 (확신도 높음)"
            note     = "반대 투표가 적절합니다."
        else:
            tendency = "강하게 반대 (확신도 매우 높음)"
            note     = "반대 투표가 자연스럽습니다."

        return (
            f"\n\n【토론 결과 반영 — 당신의 최종 확신도】\n"
            f"토론 전체를 통해 산출된 {name}의 확신도: {val:.0f}/100\n"
            f"상태: {tendency}\n"
            f"{note}\n"
            f"위 확신도는 당신이 토론 중 직접 수긍하거나([ADMIT]) 설득당한 발언들의 누적 결과입니다.\n"
            f"이것을 무시하고 성향(bias)만으로 투표하는 것은 비논리적입니다.\n"
            f"확신도와 일관된 투표를 하세요."
        )


async def _call_eval(messages: list) -> str:
    """
    평가 LLM 호출 — Groq 우선, 실패 시 Gemini 폴백
    평가용이므로 temperature=0.1로 고정, max_tokens는 기본값
    """
    try:
        return await call_groq(messages, temperature=0.1, model="llama-3.3-70b-versatile")
    except Exception:
        pass
    try:
        return await call_gemini(messages, temperature=0.1, model="gemini-2.5-flash")
    except Exception as e:
        raise e