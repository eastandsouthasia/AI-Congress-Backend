"""
debate_context.py — 토론 컨텍스트 관리

전체 발언 로그를 유지하면서 LLM 컨텍스트 윈도우 초과를 방지합니다.
- all_logs: 전체 발언 원문 보존
- summary: 오래된 발언의 압축 요약
- to_messages(): 최근 N개 전문 + 요약을 LLM 메시지 배열로 변환
- compress_if_needed(): 발언 수 초과 시 자동 압축

수정된 버그:
  버그10: 압축 트리거 반복 실행 방지 (NEXT_TRIGGER_GAP 도입)
  버그11: to_messages() role 교대를 실제 마지막 role 기반으로 수정
  버그12: 압축 실패 시 all_logs 강제 트리밍으로 무제한 증가 방지
"""

from ai_caller import call_groq, call_gemini, call_openrouter


class DebateContext:
    RECENT_WINDOW    = 6   # LLM에 전달할 최근 발언 수
    COMPRESS_TRIGGER = 10  # 이 수를 넘으면 압축 실행
    COMPRESS_KEEP    = 4   # 압축 후 원문으로 남길 최근 발언 수
    # [버그10] 압축 후 다음 트리거까지 최소 추가 발언 수
    # 압축 후 COMPRESS_KEEP(4) 개가 남으므로, 그로부터 NEXT_TRIGGER_GAP(8)개가
    # 추가로 쌓여야(총 12개) 재압축 → 매 발언마다 반복 압축 방지
    NEXT_TRIGGER_GAP = 8

    def __init__(self):
        self.all_logs: list[dict] = []  # {"speaker": str, "text": str}
        self.summary: str = ""
        # [버그10] 마지막 압축 완료 시점의 all_logs 크기
        self._last_compress_size: int = 0

    def push(self, speaker: str, text: str):
        self.all_logs.append({"speaker": speaker, "text": text})

    def to_messages(self) -> list[dict]:
        """LLM에 전달할 메시지 배열 생성 (요약 + 최근 전문)"""
        recent = self.all_logs[-self.RECENT_WINDOW:]
        older  = self.all_logs[: max(0, len(self.all_logs) - self.RECENT_WINDOW)]

        messages = []

        # 요약 블록 (기존 summary + 오래된 발언 원문)
        summary_parts = []
        if self.summary:
            summary_parts.append(self.summary)
        if older:
            summary_parts.append("\n".join(f"{l['speaker']}: {l['text']}" for l in older))

        if summary_parts:
            messages.append({
                "role": "user",
                "content": "━━ 이전 토론 요약 (반드시 숙지) ━━\n"
                           + "\n".join(summary_parts)
                           + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            })
            messages.append({
                "role": "assistant",
                "content": "이전 토론 내용을 모두 숙지했습니다.",
            })

        # [버그11 수정] 인덱스 기반 role 교대 제거.
        # 메시지 배열의 마지막 role을 보고 교대 배정하여
        # 요약 블록 유무와 무관하게 항상 올바른 user/assistant 교대를 보장.
        last_role = messages[-1]["role"] if messages else "assistant"
        for log in recent:
            next_role = "user" if last_role == "assistant" else "assistant"
            messages.append({
                "role": next_role,
                "content": f"{log['speaker']}: {log['text']}",
            })
            last_role = next_role

        # [버그11 추가] Gemini 등 일부 모델은 assistant로 끝나는 히스토리에서
        # 오류를 반환하므로, messages가 assistant로 끝나면 더미 user 메시지 추가.
        # call_gemini()에서 별도 방어 코드가 있으나 여기서도 통합 처리.
        if messages and messages[-1]["role"] == "assistant":
            messages.append({
                "role": "user",
                "content": "계속 발언해 주십시오.",
            })

        return messages

    def to_plain_text(self) -> str:
        """결의문 생성 등 전체 텍스트가 필요할 때 사용"""
        parts = []
        if self.summary:
            parts.append(f"[이전 토론 요약]\n{self.summary}")
        parts.extend(f"{l['speaker']}: {l['text']}" for l in self.all_logs)
        return "\n".join(parts)

    async def compress_if_needed(self):
        """발언 수가 COMPRESS_TRIGGER 초과 시 오래된 발언을 LLM으로 압축

        [버그10] _last_compress_size 추적으로 NEXT_TRIGGER_GAP 미만이면 건너뜀.
        [버그12] API 실패 시에도 all_logs를 강제 트리밍하여 무제한 증가 방지.
        """
        current_size = len(self.all_logs)

        # 기본 트리거 조건
        if current_size < self.COMPRESS_TRIGGER:
            return

        # [버그10] 마지막 압축 이후 NEXT_TRIGGER_GAP개 미만 추가면 건너뜀
        if (current_size - self._last_compress_size) < self.NEXT_TRIGGER_GAP:
            return

        to_compress = self.all_logs[: current_size - self.COMPRESS_KEEP]
        if not to_compress:
            return

        compress_text = "\n".join(f"{l['speaker']}: {l['text']}" for l in to_compress)
        prev = f"이전 요약:\n{self.summary}\n\n" if self.summary else ""

        try:
            # [버그7 수정] call_groq 직접 호출 → groq 실패 시 gemini, openrouter 폴백
            compress_messages = [
                {
                    "role": "system",
                    "content": (
                        "당신은 의회 토론 서기입니다. 아래 발언들을 압축 요약하세요.\n"
                        "포함 사항: 각 의원의 핵심 주장, 제시된 [DATA], "
                        "[ADMIT]로 수정된 입장, [REFUTE]로 반박된 내용.\n"
                        "각 의원의 현재 최종 입장이 명확히 드러나도록 300자 이내로 요약하세요."
                    ),
                },
                {
                    "role": "user",
                    "content": prev + "압축할 발언:\n" + compress_text,
                },
            ]
            new_summary = None
            for caller in (
                lambda m: call_groq(m, temperature=0.3),
                lambda m: call_gemini(m, temperature=0.3),
                lambda m: call_openrouter(m, temperature=0.3),
            ):
                try:
                    new_summary = await caller(compress_messages)
                    break
                except Exception as fe:
                    print(f"[DebateContext] 압축 폴백 실패: {fe}")
            if new_summary is None:
                raise ValueError("모든 엔진 압축 실패")
            self.summary = new_summary
            self.all_logs = self.all_logs[current_size - self.COMPRESS_KEEP:]
            self._last_compress_size = len(self.all_logs)
            print(f"[DebateContext] 압축 완료: {current_size}개 → {len(self.all_logs)}개 유지")

        except Exception as e:
            print(f"[DebateContext] 압축 실패: {e}")
            # [버그12] API 실패해도 all_logs가 무제한으로 커지지 않도록 강제 트리밍.
            # 오래된 발언은 버리되, _last_compress_size를 갱신하여 재시도 폭주 방지.
            max_safe = self.COMPRESS_KEEP * 4  # 최대 16개
            if len(self.all_logs) > max_safe:
                dropped = len(self.all_logs) - max_safe
                self.all_logs = self.all_logs[-max_safe:]
                self._last_compress_size = len(self.all_logs)
                print(f"[DebateContext] 압축 실패 폴백: {dropped}개 강제 제거")