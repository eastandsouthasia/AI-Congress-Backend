"""
토론 맥락 관리자 (DebateContext)

핵심 설계:
- 모든 발언을 allLogs에 원문 보존
- 발언이 쌓이면 오래된 것을 AI가 압축 요약
- 각 의원 호출 시: [압축 요약] + [최근 6개 전문] 전달
- 어느 의원이든 토론 처음부터 지금까지 전체 흐름 인지
"""

from ai_caller import call_groq

class DebateContext:
    RECENT_WINDOW    = 6   # 전문으로 전달할 최근 발언 수
    COMPRESS_TRIGGER = 10  # 이 수를 넘으면 압축 실행
    COMPRESS_KEEP    = 4   # 압축 후 전문으로 남길 발언 수

    def __init__(self):
        self.all_logs: list[dict] = []   # {"speaker": str, "text": str}
        self.summary: str = ""           # 압축된 과거 요약

    def push(self, speaker: str, text: str):
        self.all_logs.append({"speaker": speaker, "text": text})

    # LLM에 전달할 메시지 배열 생성
    # = [요약 블록] + [최근 N개 전문]
    def to_messages(self) -> list[dict]:
        recent = self.all_logs[-self.RECENT_WINDOW:]
        older  = self.all_logs[:-self.RECENT_WINDOW] if len(self.all_logs) > self.RECENT_WINDOW else []

        messages = []

        # 요약 블록 구성
        summary_parts = []
        if self.summary:
            summary_parts.append(self.summary)
        if older:
            summary_parts.append("\n".join(f"{l['speaker']}: {l['text']}" for l in older))

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

        # 최근 전문: user/assistant 교대 배치
        for i, log in enumerate(recent):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"{log['speaker']}: {log['text']}"
            })

        return messages

    # 발언 수가 임계값 초과 시 오래된 발언 자동 압축
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

            # 압축된 발언 제거, 최근만 남김
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
