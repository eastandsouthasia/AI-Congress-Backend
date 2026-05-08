"""
AI 호출 레이어 - 레이트 리밋 완전 대응 버전

핵심 변경사항:
- 엔진별 글로벌 RPM 토큰버킷 (Groq 20/min, Gemini 12/min, OpenRouter 15/min)
- claude → Gemini로 이동 (Groq 과부하 해소)
- chatgpt → OpenRouter mistral로 이동 (Groq 분산)
- 폴백 순서: 전용엔진 → 다른엔진 교차 → Gemini → 최소응답
- penalize 축소 (5초) → 회복 시간 단축
- 429 시 Retry-After 헤더 우선 준수
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
# 엔진별 동시 호출 제한
# ─────────────────────────────────────────────
_ENGINE_SEMAPHORES = {
    "groq":       asyncio.Semaphore(2),  # llama4 + nemotron 동시 처리
    "gemini":     asyncio.Semaphore(1),
    "openrouter": asyncio.Semaphore(1),  # 2→1: openrouter도 순차처리로 안정화
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
# ⚠️ Groq을 20 RPM으로 낮춤: claude+chatgpt가 Groq에서 빠져나가므로
#    llama4 단독 사용 → 더 여유롭게 운영 가능
_BUCKETS = {
    "groq":       TokenBucket(rpm=20, burst=2),
    "gemini":     TokenBucket(rpm=12, burst=2),
    "openrouter": TokenBucket(rpm=15, burst=2),
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
    max_tokens: int = 300,
    retry: int = 0
) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY 없음")

    # [BUG-5 수정] retry > 0이면 acquire 건너뜀.
    # 재귀 호출 시 함수 첫 줄부터 재실행되므로 acquire가 이중 소비되던 문제 수정.
    if retry == 0:
        await _BUCKETS["groq"].acquire()

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "presence_penalty": 0.4,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            if r.status_code == 429 and retry < 2:
                _BUCKETS["groq"].penalize(5)
                # Retry-After 헤더를 최우선으로 준수
                retry_after = int(r.headers.get("Retry-After", (retry + 1) * 5))
                wait = min(retry_after, 20)
                print(f"[Groq 429] {wait}초 대기 후 재시도 ({retry+1}/2)")
                await asyncio.sleep(wait)
                return await call_groq(messages, temperature, model, retry + 1)

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                print(f"[Groq 타임아웃] 재시도 ({retry+1}/1)")
                await asyncio.sleep(2)
                return await call_groq(messages, temperature, model, retry + 1)
            raise ValueError("Groq 응답 시간 초과")

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
                return await call_gemini(messages, temperature, model, retry + 1)

            r.raise_for_status()
            content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
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
                return await call_openrouter(messages, temperature, model, retry + 1)

            if r.status_code == 402:
                # 크레딧 부족: 다른 무료 모델로 교체
                if model != "mistralai/mistral-small-3.2-24b-instruct:free":
                    print(f"[OpenRouter 402] 크레딧 부족 → mistral 무료 폴백")
                    return await call_openrouter(
                        messages, temperature,
                        "mistralai/mistral-small-3.2-24b-instruct:free",
                        retry
                    )
                raise ValueError("OpenRouter 크레딧 소진")

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                await asyncio.sleep(2)
                return await call_openrouter(messages, temperature, model, retry + 1)
            raise ValueError("OpenRouter 응답 시간 초과")

# ─────────────────────────────────────────────
# 의원 엔진 매핑 — ✅ 엔진 분산 재배치
#
# 변경 전 문제:
#   claude + chatgpt + llama4 → 모두 Groq → Groq RPM 폭주
#
# 변경 후 분산:
#   Groq:       llama4 (단독 사용 → 여유로움)
#   Gemini:     gemini, claude (Gemini는 일 1500회 무료 → 여유 큼)
#   OpenRouter: grok, perplexity, chatgpt, manus
#
# ⚠️ 무료 모델 응답속도 기준:
#   빠름(~5s): groq 모델, gemini-2.5-flash, mistral-small:free, qwen3-8b:free
#   느림(30s+): deepseek-r1:free, grok-3-mini-beta → 사용 안 함
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# 엔진별 교차 폴백 순서
# 1차 실패 시 → 다른 엔진으로 교차 시도 (Groq 단일 폴백 제거)
# ─────────────────────────────────────────────
_FALLBACK_ORDER = {
    "groq":       [("gemini", call_gemini), ("openrouter", call_openrouter)],
    "gemini":     [("groq", call_groq),     ("openrouter", call_openrouter)],
    "openrouter": [("gemini", call_gemini), ("groq", call_groq)],
}

# ─────────────────────────────────────────────
# 통합 호출: 엔진별 버킷 + 교차 폴백
#
# [정합성 수정②] MEMBER_ENGINE_MAP 제거.
# 기존: MEMBER_ENGINE_MAP[member_id]를 참조하고 member["engine"]/member["model"]은
#       표시용으로만 사용 → members.py 수정 시 MEMBER_ENGINE_MAP도 반드시 함께 수정해야
#       하는 이중관리 문제 존재.
# 수정: member dict의 "engine"/"model" 필드를 직접 참조.
#       members.py가 SSOT(단일 진실 공급원)이므로 여기서는 그 값을 그대로 사용.
#       members.py에서 engine/model을 바꾸면 호출도 자동으로 반영됨.
# 폴백 기본값: engine 누락 시 "openrouter", model 누락 시 mistral 무료 모델.
# ─────────────────────────────────────────────
async def call_member(member: dict, messages: list, temperature: float = 0.5) -> str:
    member_id = member.get("id", "")
    name      = member.get("name", "?")
    engine    = member.get("engine", "openrouter")
    model     = member.get("model",  "mistralai/mistral-small-3.2-24b-instruct:free")
    sem       = _ENGINE_SEMAPHORES.get(engine, _ENGINE_SEMAPHORES["openrouter"])

    async with sem:
        # ── 1차: 전용 엔진 ──
        try:
            if engine == "gemini":
                return await call_gemini(messages, temperature, model)
            elif engine == "openrouter":
                return await call_openrouter(messages, temperature, model)
            else:
                return await call_groq(messages, temperature, model)
        except Exception as e1:
            print(f"[{name}/{engine}] 1차 실패: {e1}")

        # ── 2차: 교차 폴백 (엔진별 순서대로) ──
        for fallback_engine, fallback_fn in _FALLBACK_ORDER.get(engine, []):
            fallback_sem = _ENGINE_SEMAPHORES.get(fallback_engine, _ENGINE_SEMAPHORES["openrouter"])
            try:
                print(f"[{name}] {fallback_engine} 교차 폴백 시도")
                async with fallback_sem:
                    # [BUG-API-6 수정] retry=1로 전달 → acquire() 스킵
                    # 1차 실패 시 해당 엔진 버킷은 이미 penalize됐거나 토큰이 소비됨.
                    # fallback_fn은 다른 엔진이므로 그 엔진의 acquire를 실행해야 하나,
                    # 폴백은 긴급 경로이므로 버킷 토큰 소비 없이 즉시 시도.
                    return await fallback_fn(messages, temperature, retry=1)
            except Exception as e2:
                print(f"[{name}/{fallback_engine}] 교차 폴백 실패: {e2}")
                continue

        # ── 3차: 최소 응답 ──
        fallback_text = f"{name} 의원은 더 많은 논의가 필요하다고 판단합니다."
        print(f"[{name}] 모든 엔진 실패 → 최소 응답 반환")
        return fallback_text


# ─────────────────────────────────────────────
# 사전 리서치: 안건 관련 최신 정보 수집
#
# 전략:
#   1차: Gemini grounding (Google Search 실시간 연동) — 가장 최신 정보
#   2차: OpenRouter perplexity-style 검색 모델 폴백
#   3차: 일반 LLM(Groq/Gemini)으로 학습 기반 요약 — 검색 없이도 유용한 배경 지식
#
# 반환: 최대 600자 이내의 리서치 요약 텍스트 (실패 시 빈 문자열)
# ─────────────────────────────────────────────
async def call_research(issue: str, member_name: str, lens: str) -> str:
    """
    안건(issue)에 대해 해당 의원의 전문 렌즈(lens) 관점에서
    최신 정보·통계·사례를 수집하여 발언에 활용 가능한 형태로 반환.

    Args:
        issue:       토론 안건 (예: "기본소득 도입")
        member_name: 의원 이름 (로그용)
        lens:        의원의 학습 기반 설명 (예: "Google 학습 기반 — 웹·학술·다국어")

    Returns:
        리서치 요약 문자열 (최대 600자). 실패 시 "" 반환.
    """

    research_prompt = (
        f"토론 안건: \"{issue}\"\n\n"
        f"당신은 {member_name}({lens})입니다.\n"
        "위 안건에 대해 발언에 직접 활용할 수 있는 최신 사실·통계·사례·연구를 수집하세요.\n\n"
        "반드시 포함할 내용:\n"
        "1. 핵심 현황 수치 (최근 3년 이내 통계, 출처·연도 명시)\n"
        "2. 국내외 주요 사례 또는 정책 동향\n"
        "3. 찬반 논쟁에서 자주 인용되는 핵심 데이터 1~2개\n\n"
        "형식: 불릿 없이 간결한 문장. 600자 이내. 출처와 연도를 반드시 명시.\n"
        "불확실한 정보는 '추정' 또는 '불확실'로 표시하세요."
    )

    # ── 1차: Gemini grounding (Google Search 연동) ──
    if GEMINI_API_KEY:
        try:
            sem = _ENGINE_SEMAPHORES["gemini"]
            async with sem:
                await _BUCKETS["gemini"].acquire()

                system_text = (
                    f"당신은 {member_name}({lens})입니다. "
                    "Google 검색으로 찾은 최신 정보를 바탕으로 토론 준비 자료를 작성하세요."
                )
                contents = [{"role": "user", "parts": [{"text": research_prompt}]}]
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta"
                    f"/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
                )
                payload = {
                    "contents": contents,
                    "system_instruction": {"parts": [{"text": system_text}]},
                    "tools": [{"google_search": {}}],   # Grounding: 실시간 Google 검색
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 600,
                    },
                }
                async with httpx.AsyncClient(timeout=25) as client:
                    r = await client.post(url, json=payload)
                    if r.status_code == 200:
                        result = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                        print(f"[Research/{member_name}] Gemini grounding 성공 ({len(result)}자)")
                        return result[:700]
                    else:
                        print(f"[Research/{member_name}] Gemini grounding HTTP {r.status_code}")
        except Exception as e:
            print(f"[Research/{member_name}] Gemini grounding 실패: {e}")

    # ── 2차: OpenRouter (sonar/검색 지원 모델) ──
    if OPENROUTER_API_KEY:
        try:
            sem = _ENGINE_SEMAPHORES["openrouter"]
            async with sem:
                await _BUCKETS["openrouter"].acquire()

                messages = [
                    {
                        "role": "system",
                        "content": (
                            f"당신은 {member_name}({lens})입니다. "
                            "웹 검색으로 최신 정보를 찾아 토론 준비 자료를 작성하세요."
                        ),
                    },
                    {"role": "user", "content": research_prompt},
                ]
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://ai-congress.app",
                    "X-Title": "AI Congress Research",
                }
                payload = {
                    "model": "perplexity/sonar",   # 실시간 검색 내장 모델
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 600,
                }
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        json=payload, headers=headers,
                    )
                    if r.status_code == 200:
                        result = r.json()["choices"][0]["message"]["content"]
                        print(f"[Research/{member_name}] OpenRouter sonar 성공 ({len(result)}자)")
                        return result[:700]
                    else:
                        print(f"[Research/{member_name}] OpenRouter sonar HTTP {r.status_code}")
        except Exception as e:
            print(f"[Research/{member_name}] OpenRouter sonar 실패: {e}")

    # ── 3차: 일반 LLM — 학습 기반 배경 지식 요약 (검색 없음) ──
    fallback_messages = [
        {
            "role": "system",
            "content": (
                f"당신은 {member_name}({lens})입니다. "
                "당신이 학습한 지식을 바탕으로 아래 안건에 관한 배경 정보를 정리하세요. "
                "불확실한 내용은 반드시 '추정' 또는 '불확실'로 명시하세요."
            ),
        },
        {"role": "user", "content": research_prompt},
    ]

    for caller_fn, engine_name in (
        (lambda m: call_groq(m, temperature=0.3, max_tokens=500), "groq"),
        (lambda m: call_gemini(m, temperature=0.3, max_tokens=500), "gemini"),
    ):
        try:
            result = await caller_fn(fallback_messages)
            print(f"[Research/{member_name}] {engine_name} 폴백 성공 ({len(result)}자)")
            return result[:700]
        except Exception as e:
            print(f"[Research/{member_name}] {engine_name} 폴백 실패: {e}")

    print(f"[Research/{member_name}] 모든 리서치 시도 실패 — 빈 문자열 반환")
    return ""