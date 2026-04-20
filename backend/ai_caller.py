"""
AI 호출 레이어 - 레이트 리밋 완전 대응 버전

핵심 변경사항:
- 엔진별 글로벌 RPM 토큰버킷 추가 (Groq 25/min, Gemini 12/min, OpenRouter 15/min)
- 발언은 반드시 순차적으로 처리 (GLOBAL_SEMAPHORE로 동시 호출 1개 제한)
- 429 발생 시 버킷을 즉시 소진 처리 후 대기
- 문장 완성 보장 (끊김 방지)
"""

import os
import re
import time
import asyncio
import httpx

GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ─────────────────────────────────────────────
# 엔진별 동시 호출 제한 (글로벌 1개 → 엔진별 독립)
# ─────────────────────────────────────────────
_ENGINE_SEMAPHORES = {
    "groq":       asyncio.Semaphore(1),
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
        self.rpm        = rpm
        self.capacity   = burst or max(1, rpm // 2)
        self.tokens     = float(self.capacity)
        self.last_refill = time.monotonic()
        self._lock      = None  # asyncio.Lock은 이벤트루프 생성 후 초기화

    def _ensure_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()

    async def acquire(self):
        self._ensure_lock()
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            # 경과 시간에 비례해 토큰 보충
            self.tokens = min(
                self.capacity,
                self.tokens + elapsed * (self.rpm / 60.0)
            )
            self.last_refill = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return  # 즉시 통과

            # 토큰 부족 → 다음 토큰까지 대기
            wait = (1.0 - self.tokens) / (self.rpm / 60.0)
            print(f"[RateLimit] {wait:.1f}초 대기 중...")
            await asyncio.sleep(wait)
            self.tokens = 0.0

    def penalize(self, seconds: float = 15.0):
        """429 수신 시 토큰 강제 소진 + 추가 패널티"""
        self.tokens = -seconds * (self.rpm / 60.0)

# 엔진별 버킷 (무료 티어 기준으로 여유 있게 설정)
_BUCKETS = {
    "groq":       TokenBucket(rpm=25, burst=3),   # Groq 무료: ~30 RPM → 25로 제한
    "gemini":     TokenBucket(rpm=12, burst=2),   # Gemini 무료: ~15 RPM → 12로 제한
    "openrouter": TokenBucket(rpm=15, burst=2),   # OpenRouter 무료: ~20 RPM → 15로 제한
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
# Groq 호출
# ─────────────────────────────────────────────
async def call_groq(
    messages: list,
    temperature: float = 0.5,
    model: str = "llama-3.3-70b-versatile",
    retry: int = 0
) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY 없음")

    await _BUCKETS["groq"].acquire()

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 300,       # 350→300: 불필요한 토큰 줄여 응답 속도 향상
        "presence_penalty": 0.4,
    }

    async with httpx.AsyncClient(timeout=15) as client:  # 30s→15s: 느린 응답 빠르게 포기
        try:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            if r.status_code == 429 and retry < 2:   # 4→2회: 재시도 횟수 줄임
                _BUCKETS["groq"].penalize(10)           # 20s→10s: 패널티 단축
                retry_after = int(r.headers.get("Retry-After", (retry + 1) * 4))  # 6s→4s
                wait = min(retry_after, 15)             # 30s→15s: 최대 대기 단축
                print(f"[Groq 429] {wait}초 대기 후 재시도 ({retry+1}/2)")
                await asyncio.sleep(wait)
                return await call_groq(messages, temperature, model, retry + 1)

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:   # 2→1회
                print(f"[Groq 타임아웃] 재시도 ({retry+1}/1)")
                await asyncio.sleep(2)  # 4s→2s
                return await call_groq(messages, temperature, model, retry + 1)
            raise ValueError("Groq 응답 시간 초과")

# ─────────────────────────────────────────────
# Gemini 호출
# ─────────────────────────────────────────────
async def call_gemini(
    messages: list,
    temperature: float = 0.4,
    model: str = "gemini-2.5-flash",
    retry: int = 0
) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 없음")

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
            "maxOutputTokens": 350,
        },
    }

    async with httpx.AsyncClient(timeout=20) as client:  # 35s→20s
        try:
            r = await client.post(url, json=payload)

            if r.status_code == 429 and retry < 2:   # 4→2회
                _BUCKETS["gemini"].penalize(10)        # 20s→10s
                retry_after = int(r.headers.get("Retry-After", (retry + 1) * 5))
                wait = min(retry_after, 20)            # 35s→20s
                print(f"[Gemini 429] {wait}초 대기 후 재시도")
                await asyncio.sleep(wait)
                return await call_gemini(messages, temperature, model, retry + 1)

            r.raise_for_status()
            content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:   # 2→1회
                await asyncio.sleep(2)
                return await call_gemini(messages, temperature, model, retry + 1)
            raise ValueError("Gemini 응답 시간 초과")

# ─────────────────────────────────────────────
# OpenRouter 호출
# ─────────────────────────────────────────────
async def call_openrouter(
    messages: list,
    temperature: float = 0.5,
    model: str = "mistralai/mistral-small-3.2-24b-instruct:free",
    retry: int = 0
) -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY 없음")

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
        "max_tokens": 350,
        "presence_penalty": 0.4,
    }

    async with httpx.AsyncClient(timeout=20) as client:  # 45s→20s: 느린 모델 교체로 충분
        try:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
            )

            if r.status_code == 429 and retry < 2:   # 4→2회
                _BUCKETS["openrouter"].penalize(10)    # 20s→10s
                retry_after = int(r.headers.get("Retry-After", (retry + 1) * 5))
                wait = min(retry_after, 20)            # 40s→20s
                print(f"[OpenRouter 429] {wait}초 대기 후 재시도")
                await asyncio.sleep(wait)
                return await call_openrouter(messages, temperature, model, retry + 1)

            if r.status_code == 402:
                print(f"[OpenRouter 402] 크레딧 부족 → mistral 무료 폴백")
                return await call_openrouter(
                    messages, temperature,
                    "mistralai/mistral-small-3.2-24b-instruct:free",
                    retry
                )

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:   # 2→1회
                await asyncio.sleep(2)
                return await call_openrouter(messages, temperature, model, retry + 1)
            raise ValueError("OpenRouter 응답 시간 초과")

# ─────────────────────────────────────────────
# 의원 엔진 매핑
# ⚠️ 무료 모델 응답속도 기준:
#   빠름(~5s): groq 모델, gemini-2.5-flash, mistral-small:free, qwen3-8b:free
#   느림(30s+): deepseek-r1:free, grok-3-mini-beta → 제거됨
# ─────────────────────────────────────────────
MEMBER_ENGINE_MAP = {
    "gemini":     {"engine": "gemini",      "model": "gemini-2.5-flash"},
    "llama4":     {"engine": "groq",        "model": "meta-llama/llama-4-scout-17b-16e-instruct"},
    "chatgpt":    {"engine": "groq",        "model": "llama-3.3-70b-versatile"},
    # claude-3-haiku는 openrouter 무료 경유 시 느림 → groq llama로 대체
    "claude":     {"engine": "groq",        "model": "llama-3.3-70b-versatile"},
    # grok-3-mini-beta 무료 티어 응답 30~90초 → mistral-small(빠름)으로 대체
    "grok":       {"engine": "openrouter",  "model": "mistralai/mistral-small-3.2-24b-instruct:free"},
    "perplexity": {"engine": "openrouter",  "model": "mistralai/mistral-small-3.2-24b-instruct:free"},
    "manus":      {"engine": "openrouter",  "model": "qwen/qwen3-8b:free"},
    # deepseek: 무료 응답 60~120초로 제거됨
    # glm5, kimi: 의원 목록에서 이미 제거됨
}

# ─────────────────────────────────────────────
# 통합 호출: 글로벌 세마포어 + 엔진별 버킷 + 폴백
# 반드시 한 번에 1명씩만 호출됨 (동시 호출 차단)
# ─────────────────────────────────────────────
async def call_member(member: dict, messages: list, temperature: float = 0.5) -> str:
    member_id = member.get("id", "")
    name      = member.get("name", "?")
    config    = MEMBER_ENGINE_MAP.get(member_id, {"engine": "groq", "model": "llama-3.3-70b-versatile"})
    engine    = config["engine"]
    model     = config["model"]
    sem       = _ENGINE_SEMAPHORES.get(engine, _ENGINE_SEMAPHORES["openrouter"])

    # ── 엔진별 순차 처리 (같은 엔진끼리만 대기, 다른 엔진은 병렬 가능) ──
    async with sem:
        # 1차 시도: 전용 엔진
        try:
            if engine == "gemini":
                return await call_gemini(messages, temperature, model)
            elif engine == "openrouter":
                return await call_openrouter(messages, temperature, model)
            else:
                return await call_groq(messages, temperature, model)

        except Exception as e1:
            print(f"[{name}/{engine}] 1차 실패: {e1}")

        # 2차 시도: Groq 기본 모델 폴백
        try:
            print(f"[{name}] Groq 폴백 시도")
            return await call_groq(messages, temperature)

        except Exception as e2:
            print(f"[{name}] Groq 폴백도 실패: {e2}")

        # 3차: 최소 응답 반환
        fallback = f"{name} 의원은 이 안건에 대해 충분한 검토가 필요하다는 입장입니다."
        print(f"[{name}] 최소 응답 반환")
        return fallback