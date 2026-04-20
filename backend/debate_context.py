"""
토론 맥락 관리자 (DebateContext) - 장시간 토론 대응 버전

변경사항:
- COMPRESS_TRIGGER: 10 → 14 (압축 빈도 줄여 Groq 부하 경감)
- COMPRESS_KEEP: 4 → 5 (최근 맥락 더 많이 보존)
- RECENT_WINDOW: 6 → 7 (더 많은 최근 발언 전문 전달)
- compress_if_needed가 GLOBAL_SEMAPHORE 밖에서 호출되도록 주석 명시
"""

from ai_caller import call_groq

class DebateContext:
    RECENT_WINDOW    = 7    # 전문으로 전달할 최근 발언 수 (6→7)
    COMPRESS_TRIGGER = 14   # 압축 실행 임계값 (10→14, Groq 부하 감소)
    COMPRESS_KEEP    = 5    # 압축 후 전문으로 남길 발언 수 (4→5)

    def __init__(self):
        self.all_logs: list[dict] = []   # {"speaker": str, "text": str}
        self.summary: str = ""           # 압축된 과거 요약

    def push(self, speaker: str, text: str):
        self.all_logs.append({"speaker": speaker, "text": text})

    def to_messages(self) -> list[dict]:
        recent = self.all_logs[-self.RECENT_WINDOW:]
        older  = self.all_logs[:-self.RECENT_WINDOW] if len(self.all_logs) > self.RECENT_WINDOW else []

        messages = []

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

        for i, log in enumerate(recent):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"{log['speaker']}: {log['text']}"
            })

        return messages

    # ⚠️ 주의: 이 메서드는 GLOBAL_SEMAPHORE 바깥에서 호출해야 합니다.
    # debate_engine에서 발언 사이 대기 시간 중에 호출하세요.
    async def compress_if_needed(self):
        if len(self.all_logs) < self.COMPRESS_TRIGGER:
            return

        to_compress = self.all_logs[:-self.COMPRESS_KEEP]
        if not to_compress:
            return

        compress_text = "\n".join(f"{l['speaker']}: {l['text']}" for l in to_compress)
        prev = f"이전 요약:\n{self.summary}\n\n" if self.summary else ""

        try:
            # 압축은 Groq 호출이지만 GLOBAL_SEMAPHORE 밖이므로
            # 발언 간 대기 시간에 실행 → API 부하에 영향 없음
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
