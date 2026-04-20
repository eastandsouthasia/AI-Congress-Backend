"""
토론 맥락 관리자 (DebateContext)

✅ 버그 수정:
- to_messages()에서 "speaker: text" 포맷 제거
  → AI가 자기 발언에도 "[이름]:(콜론)" prefix를 붙이는 원인이었음
- 마지막 메시지가 assistant로 끝나는 경우 제거
  → AI가 직전 자기 발언을 다시 반복하는 원인이었음
- speaker 정보는 "▶ 이름" 헤더로만 전달
"""

from ai_caller import call_groq

class DebateContext:
    RECENT_WINDOW    = 6
    COMPRESS_TRIGGER = 10
    COMPRESS_KEEP    = 4

    def __init__(self):
        self.all_logs: list[dict] = []
        self.summary: str = ""

    def push(self, speaker: str, text: str):
        self.all_logs.append({"speaker": speaker, "text": text})

    def to_messages(self) -> list[dict]:
        recent = self.all_logs[-self.RECENT_WINDOW:]
        older  = self.all_logs[:-self.RECENT_WINDOW] if len(self.all_logs) > self.RECENT_WINDOW else []

        messages = []

        # 요약 블록
        summary_parts = []
        if self.summary:
            summary_parts.append(self.summary)
        if older:
            summary_parts.append(
                "\n".join(f"{l['speaker']}: {l['text']}" for l in older)
            )

        if summary_parts:
            messages.append({
                "role": "user",
                "content": (
                    "━━ 이전 토론 요약 (반드시 숙지) ━━\n" +
                    "\n".join(summary_parts) +
                    "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
            })
            messages.append({
                "role": "assistant",
                "content": "이전 토론 내용을 모두 숙지했습니다."
            })

        # ✅ 최근 발언: speaker를 "▶ 이름" 헤더로만 표기, 콜론 포맷 완전 제거
        for i, log in enumerate(recent):
            role = "user" if i % 2 == 0 else "assistant"
            content = f"▶ {log['speaker']}\n{log['text']}"
            messages.append({"role": role, "content": content})

        # ✅ 마지막이 assistant면 제거 → AI 자기 발언 반복 방지
        if messages and messages[-1]["role"] == "assistant":
            messages.pop()

        return messages

    async def compress_if_needed(self):
        if len(self.all_logs) < self.COMPRESS_TRIGGER:
            return

        to_compress = self.all_logs[:-self.COMPRESS_KEEP]
        if not to_compress:
            return

        compress_text = "\n".join(f"{l['speaker']}: {l['text']}" for l in to_compress)
        prev = f"이전 요약:\n{self.summary}\n\n" if self.summary else ""

        try:
            self.summary = await call_groq([
                {
                    "role": "system",
                    "content": (
                        "당신은 의회 토론 서기입니다. 아래 발언들을 압축 요약하세요.\n"
                        "포함 사항: 각 의원의 핵심 주장, 제시된 [DATA], "
                        "[ADMIT]로 수정된 입장, [REFUTE]로 반박된 내용.\n"
                        "각 의원의 현재 최종 입장이 명확히 드러나도록 300자 이내로 요약하세요."
                    )
                },
                {
                    "role": "user",
                    "content": prev + "압축할 발언:\n" + compress_text
                }
            ], temperature=0.3)

            self.all_logs = self.all_logs[-self.COMPRESS_KEEP:]
            print(f"[Context] 압축 완료. 현재 로그 수: {len(self.all_logs)}")

        except Exception as e:
            print(f"[Context] 압축 실패 (원본 유지): {e}")

    def to_plain_text(self) -> str:
        parts = []
        if self.summary:
            parts.append(f"[이전 토론 요약]\n{self.summary}")
        parts += [f"{l['speaker']}: {l['text']}" for l in self.all_logs]
        return "\n".join(parts)

    @property
    def length(self):
        return len(self.all_logs)