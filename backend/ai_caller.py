"""
AI 호출 레이어 - 완전 무료 API 전용 버전

[무료 기준]
  - Gemini API (Google AI Studio 무료 티어): 15 RPM / 1M TPD, 과금 없음
    * google_search grounding: 500회/일 무료
  - OpenRouter :free 모델: 완전 무료, 결제 불필요

[Groq 완전 제거]
  - llama-3.3-70b-versatile (Groq) → 무료 티어 한도 초과 시 과금 → 제거
  - call_groq() 삭제
  - _ENGINE_SEMAPHORES / _BUCKETS 에서 groq 제거
  - _FALLBACK_ORDER 에서 groq 제거
  - call_research Step1-c / Step2 폴백에서 groq → openrouter 대체

[엔진 구조 — 2종]
  gemini:     Gemini 2.5 Flash (Google AI Studio 무료 티어)
  openrouter: OpenRouter :free 모델 전용
    폴백 순서: gemini → openrouter → openrouter 다른 무료 모델

[members.py 변경 내역]
  라마:  groq + llama-4-scout → openrouter + llama-3.3-70b-instruct:free
  올모:  allenai/olmo-3.1-32b-think:free → allenai/olmo-3-32b-think:free (ID 수정)
  노바:  amazon/nova-pro-v1 (유료) → nvidia/nemotron-3-super-120b-a12b:free
"""

import os
import re
import time
import asyncio
import httpx

GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ─────────────────────────────────────────────
# 엔진별 동시 호출 제한
# ─────────────────────────────────────────────
_ENGINE_SEMAPHORES = {
    "gemini":     asyncio.Semaphore(1),
    "openrouter": asyncio.Semaphore(2),
}

# ─────────────────────────────────────────────
# 토큰 버킷 레이트 리미터
# ─────────────────────────────────────────────
class TokenBucket:
    """
    rpm: 분당 최대 요청 수
    burst: 순간 최대 토큰 (기본 = rpm의 절반, 최소 1)
    """
    def __init__(self, rpm: int, burst: int = None):
        self.rpm         = rpm
        self.capacity    = burst or max(1, rpm // 2)
        self.tokens      = float(self.capacity)
        self.last_refill = time.monotonic()
        self._lock       = None  # asyncio.Lock은 이벤트루프 생성 후 초기화

    def _ensure_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()

    async def acquire(self):
        self._ensure_lock()
        async with self._lock:
            now     = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(
                self.capacity,
                self.tokens + elapsed * (self.rpm / 60.0)
            )
            self.last_refill = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return

            wait = (1.0 - self.tokens) / (self.rpm / 60.0)
            print(f"[RateLimit] {wait:.1f}초 대기 중...")
            await asyncio.sleep(wait)
            self.tokens = 0.0

    def penalize(self, seconds: float = 5.0):
        """429 수신 시 토큰 강제 소진. 패널티 5초로 축소 (이전: 10~20초)"""
        self.tokens = max(self.tokens - seconds * (self.rpm / 60.0), -self.capacity)


# 엔진별 버킷
# Gemini 무료 티어: 15 RPM → 보수적으로 12 RPM 운영
# OpenRouter :free: 공식 한도 없으나 20 RPM 이하로 안정 운영
_BUCKETS = {
    "gemini":     TokenBucket(rpm=12, burst=2),
    "openrouter": TokenBucket(rpm=20, burst=3),
}

# ─────────────────────────────────────────────
# 문장 완성 보장 (발언 끊김 방지)
# ─────────────────────────────────────────────
def ensure_complete(text: str) -> str:
    if not text or not text.strip():
        return text
    text = text.strip()
    if re.search(r'[.!?。！？"\'」』…]$', text):
        return text
    match = re.search(r'^([\s\S]*[.!?。！？"\'」』…])', text)
    if match:
        return match.group(1).strip()
    return text

# ─────────────────────────────────────────────
# Gemini 호출
# ─────────────────────────────────────────────
async def call_gemini(
    messages: list,
    temperature: float = 0.4,
    model: str = "gemini-2.5-flash",
    max_tokens: int = 300,
    retry: int = 0
) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 없음")

    # [BUG-5 수정] retry > 0이면 acquire 건너뜀
    if retry == 0:
        await _BUCKETS["gemini"].acquire()

    system_text = next(
        (m["content"] for m in messages if m["role"] == "system"), ""
    )
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "model" if m["role"] == "assistant" else "user"
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n" + m["content"]
        else:
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

    if not contents or contents[-1]["role"] == "model":
        contents.append({"role": "user", "parts": [{"text": "발언하세요."}]})

    url = (
        f"https://generativelanguage.googleapis.com/v1beta"
        f"/models/{model}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": system_text}]},
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(url, json=payload)

            if r.status_code == 429 and retry < 2:
                _BUCKETS["gemini"].penalize(5)
                retry_after = int(r.headers.get("Retry-After", (retry + 1) * 6))
                wait = min(retry_after, 20)
                print(f"[Gemini 429] {wait}초 대기 후 재시도")
                await asyncio.sleep(wait)
                return await call_gemini(messages, temperature, model, max_tokens, retry + 1)

            r.raise_for_status()
            content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                await asyncio.sleep(2)
                return await call_gemini(messages, temperature, model, max_tokens, retry + 1)
            raise ValueError("Gemini 응답 시간 초과")

# ─────────────────────────────────────────────
# OpenRouter 호출
# ─────────────────────────────────────────────
async def call_openrouter(
    messages: list,
    temperature: float = 0.5,
    model: str = "mistralai/mistral-small-3.1-24b-instruct:free",
    max_tokens: int = 350,
    retry: int = 0
) -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY 없음")

    # [BUG-5 수정] retry > 0이면 acquire 건너뜀
    if retry == 0:
        await _BUCKETS["openrouter"].acquire()

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://ai-congress.app",
        "X-Title": "AI Congress",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "presence_penalty": 0.4,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
            )

            if r.status_code == 429 and retry < 2:
                _BUCKETS["openrouter"].penalize(5)
                retry_after = int(r.headers.get("Retry-After", (retry + 1) * 6))
                wait = min(retry_after, 20)
                print(f"[OpenRouter 429] {wait}초 대기 후 재시도")
                await asyncio.sleep(wait)
                return await call_openrouter(messages, temperature, model, max_tokens, retry + 1)

            if r.status_code == 402:
                # 크레딧 부족: 다른 무료 모델로 교체
                if model != "mistralai/mistral-small-3.1-24b-instruct:free":
                    print(f"[OpenRouter 402] 크레딧 부족 → mistral 무료 폴백")
                    return await call_openrouter(
                        messages, temperature,
                        "mistralai/mistral-small-3.1-24b-instruct:free",
                        max_tokens, retry,
                    )
                raise ValueError("OpenRouter 크레딧 소진")

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                await asyncio.sleep(2)
                return await call_openrouter(messages, temperature, model, max_tokens, retry + 1)
            raise ValueError("OpenRouter 응답 시간 초과")

# ─────────────────────────────────────────────
# 의원 엔진 매핑 — ✅ 엔진 분산 재배치 (8명 기준)
#
# 변경 전 문제:
#   claude + chatgpt + llama4 → 모두 Groq → Groq RPM 폭주
#
# 현재 분산:
#   Groq:       llama4 (단독 사용 → 여유로움)
#   Gemini:     gemini
#   OpenRouter: mistral, gptoss, nemotron, olmo, trinity, nova
#
# ⚠️ 무료 모델 응답속도 기준:
#   빠름(~5s): gemini-2.5-flash, mistral-small:free
#   느림(30s+): deepseek-r1:free, grok-3-mini-beta → 사용 안 함
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# 엔진별 교차 폴백 순서 — groq 제거, gemini ↔ openrouter 교차만
# ─────────────────────────────────────────────
_FALLBACK_ORDER = {
    "gemini":     [("openrouter", call_openrouter)],
    "openrouter": [("gemini",     call_gemini)],
}

# ─────────────────────────────────────────────
# 통합 호출: 엔진별 버킷 + 교차 폴백
#
# member dict의 "engine"/"model" 필드를 직접 참조.
# members.py가 SSOT — 여기서는 그 값을 그대로 사용.
# 폴백 기본값: engine 누락 시 "openrouter", model 누락 시 mistral 무료 모델.
#
# 엔진 분산 (members.py 기준):
#   gemini:     제미나이 (Gemini 2.5 Flash)
#   openrouter: 라마, 미스트랄, 지피티, 엔비디아, 올모, 트리니티, 노바 (7명)
# ─────────────────────────────────────────────
async def call_member(member: dict, messages: list, temperature: float = 0.5) -> str:
    name   = member.get("name", "?")
    engine = member.get("engine", "openrouter")
    model  = member.get("model",  "mistralai/mistral-small-3.1-24b-instruct:free")
    sem    = _ENGINE_SEMAPHORES.get(engine, _ENGINE_SEMAPHORES["openrouter"])

    async with sem:
        # ── 1차: 전용 엔진 ──
        try:
            if engine == "gemini":
                return await call_gemini(messages, temperature, model)
            else:
                return await call_openrouter(messages, temperature, model)
        except Exception as e1:
            print(f"[{name}/{engine}] 1차 실패: {e1}")

        # ── 2차: 교차 폴백 ──
        for fallback_engine, fallback_fn in _FALLBACK_ORDER.get(engine, []):
            fallback_sem = _ENGINE_SEMAPHORES.get(fallback_engine, _ENGINE_SEMAPHORES["openrouter"])
            try:
                print(f"[{name}] {fallback_engine} 교차 폴백 시도")
                async with fallback_sem:
                    return await fallback_fn(messages, temperature, retry=1)
            except Exception as e2:
                print(f"[{name}/{fallback_engine}] 교차 폴백 실패: {e2}")
                continue

        # ── 3차: 최소 응답 ──
        print(f"[{name}] 모든 엔진 실패 → 최소 응답 반환")
        return "더 많은 논의가 필요하다고 판단합니다."

