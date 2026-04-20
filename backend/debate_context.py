"""
debate_context.py — 토론 컨텍스트 관리

전체 발언 로그를 유지하면서 LLM 컨텍스트 윈도우 초과를 방지합니다.
- all_logs: 전체 발언 원문 보존
- summary: 오래된 발언의 압축 요약
- to_messages(): 최근 N개 전문 + 요약을 LLM 메시지 배열로 변환
- compress_if_needed(): 발언 수 초과 시 자동 압축
"""

from ai_caller import call_groq


class DebateContext:
    RECENT_WINDOW    = 6   # LLM에 전달할 최근 발언 수
    COMPRESS_TRIGGER = 10  # 이 수를 넘으면 압축 실행
    COMPRESS_KEEP    = 4   # 압축 후 원문으로 남길 최근 발언 수

    def __init__(self):
        self.all_logs: list[dict] = []  # {"speaker": str, "text": str}
        self.summary: str = ""

    def push(self, speaker: str, text: str):
        self.all_logs.append({"speaker": speaker, "text": text})

    def to_messages(self) -> list[dict]:
        """LLM에 전달할 메시지 배열 생성 (요약 + 최근 전문)"""
        recent = self.all_logs[-self.RECENT_WINDOW:]
        older  = self.all_logs[: max(0, len(self.all_logs) - self.RECENT_WINDOW)]

        messages = []

        # 요약 블록 (오래된 발언 + 기존 요약)
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

        # 최근 발언 전문 (user/assistant 교대)
        for i, log in enumerate(recent):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"{log['speaker']}: {log['text']}",
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
        """발언 수가 COMPRESS_TRIGGER 초과 시 오래된 발언을 LLM으로 압축"""
        if len(self.all_logs) < self.COMPRESS_TRIGGER:
            return

        to_compress = self.all_logs[: len(self.all_logs) - self.COMPRESS_KEEP]
        if not to_compress:
            return

        compress_text = "\n".join(f"{l['speaker']}: {l['text']}" for l in to_compress)
        prev = f"이전 요약:\n{self.summary}\n\n" if self.summary else ""

        try:
            self.summary = await call_groq(
                messages=[
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
                ],
                temperature=0.3,
            )
            self.all_logs = self.all_logs[len(self.all_logs) - self.COMPRESS_KEEP :]
        except Exception as e:
            print(f"[DebateContext] 압축 실패: {e}")
