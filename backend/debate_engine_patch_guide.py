"""
debate_engine.py 수정 가이드
────────────────────────────────────────────────────────────────
원본 debate_engine.py에 아래 변경사항을 적용하세요.
debate_engine.py 원본 파일을 공유해주시면 직접 수정해드립니다.
────────────────────────────────────────────────────────────────

[변경 1] 최대 토론 시간 상수 수정
  기존: MAX_DURATION_MIN = 30  (또는 duration 상한 체크 로직)
  변경: MAX_DURATION_MIN = 60

[변경 2] 발언 간격(INTER_SPEECH_DELAY) 추가
  각 의원 발언 후 다음 발언 전에 최소 대기를 삽입합니다.
  이 대기 시간 동안 TTS가 재생되고 맥락 압축도 실행됩니다.

  예시 코드:
  ─────────────────────────────────────────────
  # 의원 수에 따른 발언 간격 (초)
  # 8명 기준 1분에 최대 ~3회 호출 → 레이트 리밋 안전
  INTER_SPEECH_DELAY = 5  # 발언 후 최소 5초 대기

  async def _speak(self, member, context):
      msg = build_messages(member, context)   # 기존 로직
      text = await call_member(member, msg)   # ← GLOBAL_SEMAPHORE 내부
      await self.ws.send_json({...})
      
      # ✅ 발언 후 대기 (TTS 재생 + 레이트 리밋 여유 확보)
      await asyncio.sleep(INTER_SPEECH_DELAY)
      
      # ✅ 압축은 GLOBAL_SEMAPHORE 바깥, 발언 사이 대기 중에 실행
      await context.compress_if_needed()
  ─────────────────────────────────────────────

[변경 3] 토론 라운드 수 조정
  기존 로직이 "duration분 동안 라운드 반복"이라면:
  - 60분 × 60초 = 3600초 지원
  - 의원 1명당 발언 소요 예상: API 응답 10~25초 + TTS + 대기 5초 ≈ 40초
  - 8명 × 40초 = 1라운드 약 320초(5.3분)
  - 60분 / 5.3분 ≈ 11라운드 가능

  기존 로직이 "총 발언 횟수"로 제한한다면:
  - 60분 기준 최대 발언 횟수 = 약 88회 (60 × 60 / 40)
  - 안전하게 80회로 상한 설정 권장

[변경 4] 타임아웃 표시 수정 (프론트 연동)
  DebateScreen.js에서 "30분" 하드코딩된 텍스트가 있다면 → "60분"으로 수정
  타이머 상한도 duration 파라미터를 따르도록 확인

[변경 5] DebateScreen.js 타이머 상한 확인
  기존: const MAX_SECONDS = 30 * 60;
  변경: const MAX_SECONDS = duration * 60;  // 이미 동적이면 그대로 OK

────────────────────────────────────────────────────────────────
debate_engine.py를 공유해주시면 위 변경사항을 직접 적용한
완성 파일을 드리겠습니다.
────────────────────────────────────────────────────────────────
"""
