"""
AI ?�출 ?�이??- ?�이??리밋 ?�전 ?�??버전

?�심 변경사??
- ?�진�?글로벌 RPM ?�큰버킷 (Groq 20/min, Gemini 12/min, OpenRouter 15/min)
- claude ??Gemini�??�동 (Groq 과�????�소)
- chatgpt ??OpenRouter mistral�??�동 (Groq 분산)
- ?�백 ?�서: ?�용?�진 ???�른?�진 교차 ??Gemini ??최소?�답
- penalize 축소 (5�? ???�복 ?�간 ?�축
- 429 ??Retry-After ?�더 ?�선 준??"""

import os
import re
import time
import asyncio
import httpx
from pathlib import Path
from dotenv import load_dotenv
# ==================== .env ?�일 로드 (frontend???�는 ?�일 ?�기) ====================
BASE_DIR = Path(__file__).parent  # backend ?�더
env_path = BASE_DIR / ".env"

if env_path.exists():
    load_dotenv(env_path)
    print(f"??frontend/.env ?�일 로드 ?�공")
else:
    print(f"?�️ frontend/.env ?�일??찾을 ???�습?�다: {env_path}")
# ================================================================================
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
print("GEMINI:", bool(GEMINI_API_KEY))
print("OPENROUTER:", bool(OPENROUTER_API_KEY))
print("GROQ:", bool(GROQ_API_KEY))
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# ?�진�??�시 ?�출 ?�한
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
_ENGINE_SEMAPHORES = {
    "groq":       asyncio.Semaphore(2),  # llama4 + nemotron ?�시 처리
    "gemini":     asyncio.Semaphore(1),
    "openrouter": asyncio.Semaphore(1),  # 2??: openrouter???�차처리�??�정??}

# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# ?�큰 버킷 ?�이??리�???# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
class TokenBucket:
    """
    rpm: 분당 최�? ?�청 ??    burst: ?�간 최�? ?�큰 (기본 = rpm???�반, 최소 1)
    """
    def __init__(self, rpm: int, burst: int = None):
        self.rpm         = rpm
        self.capacity    = burst or max(1, rpm // 2)
        self.tokens      = float(self.capacity)
        self.last_refill = time.monotonic()
        self._lock       = None  # asyncio.Lock?� ?�벤?�루???�성 ??초기??
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
            print(f"[RateLimit] {wait:.1f}�??��?�?..")
            await asyncio.sleep(wait)
            self.tokens = 0.0

    def penalize(self, seconds: float = 5.0):
        """429 ?�신 ???�큰 강제 ?�진. ?�널??5초로 축소 (?�전: 10~20�?"""
        self.tokens = max(self.tokens - seconds * (self.rpm / 60.0), -self.capacity)


# ?�진�?버킷
# ?�️ Groq??20 RPM?�로 ??��: claude+chatgpt가 Groq?�서 빠져?��?므�?#    llama4 ?�독 ?�용 ?????�유�?�� ?�영 가??_BUCKETS = {
    "groq":       TokenBucket(rpm=20, burst=2),
    "gemini":     TokenBucket(rpm=12, burst=2),
    "openrouter": TokenBucket(rpm=15, burst=2),
}

# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# 문장 ?�성 보장 (발언 ?��? 방�?)
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
def ensure_complete(text: str) -> str:
    if not text or not text.strip():
        return text
    text = text.strip()
    if re.search(r'[.!??�！�?\'?�』�?$', text):
        return text
    match = re.search(r'^([\s\S]*[.!??�！�?\'?�』�?)', text)
    if match:
        return match.group(1).strip()
    return text

# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# Groq ?�출
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
async def call_groq(
    messages: list,
    temperature: float = 0.5,
    model: str = "llama-3.3-70b-versatile",
    max_tokens: int = 300,
    retry: int = 0
) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY ?�음")

    # [BUG-5 ?�정] retry > 0?�면 acquire 건너?�.
    # ?��? ?�출 ???�수 �?줄�????�실?�되므�?acquire가 ?�중 ?�비?�던 문제 ?�정.
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
                # Retry-After ?�더�?최우?�으�?준??                retry_after = int(r.headers.get("Retry-After", (retry + 1) * 5))
                wait = min(retry_after, 20)
                print(f"[Groq 429] {wait}�??��????�시??({retry+1}/2)")
                await asyncio.sleep(wait)
                return await call_groq(messages, temperature, model, retry + 1)

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                print(f"[Groq ?�?�아?? ?�시??({retry+1}/1)")
                await asyncio.sleep(2)
                return await call_groq(messages, temperature, model, retry + 1)
            raise ValueError("Groq ?�답 ?�간 초과")

# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# Gemini ?�출
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
async def call_gemini(
    messages: list,
    temperature: float = 0.4,
    model: str = "gemini-2.0-flash-lite",
    max_tokens: int = 300,
    retry: int = 0
) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY ?�음")

    # [BUG-5 ?�정] retry > 0?�면 acquire 건너?�
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
        contents.append({"role": "user", "parts": [{"text": "발언?�세??"}]})

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
                print(f"[Gemini 429] {wait}�??��????�시??)
                await asyncio.sleep(wait)
                return await call_gemini(messages, temperature, model, retry + 1)

            r.raise_for_status()
            content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                await asyncio.sleep(2)
                return await call_gemini(messages, temperature, model, retry + 1)
            raise ValueError("Gemini ?�답 ?�간 초과")

# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# OpenRouter ?�출
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
async def call_openrouter(
    messages: list,
    temperature: float = 0.5,
    model: str = "mistralai/mistral-small-3.2-24b-instruct:free",
    max_tokens: int = 350,
    retry: int = 0
) -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY ?�음")

    # [BUG-5 ?�정] retry > 0?�면 acquire 건너?�
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
                print(f"[OpenRouter 429] {wait}�??��????�시??)
                await asyncio.sleep(wait)
                return await call_openrouter(messages, temperature, model, retry + 1)

            if r.status_code == 402:
                # ?�레??부�? ?�른 무료 모델�?교체
                if model != "mistralai/mistral-small-3.2-24b-instruct:free":
                    print(f"[OpenRouter 402] ?�레??부�???mistral 무료 ?�백")
                    return await call_openrouter(
                        messages, temperature,
                        "mistralai/mistral-small-3.2-24b-instruct:free",
                        max_tokens,                # 기존 max_tokens ?��?
                        max(retry, 1),             # acquire ?�중 ?�행 방�?
                    )
                raise ValueError("OpenRouter ?�레???�진")

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                await asyncio.sleep(2)
                return await call_openrouter(messages, temperature, model, retry + 1)
            raise ValueError("OpenRouter ?�답 ?�간 초과")

# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# ?�원 ?�진 매핑 ?????�진 분산 ?�배�?#
# 변�???문제:
#   claude + chatgpt + llama4 ??모두 Groq ??Groq RPM ??��
#
# 변�???분산:
#   Groq:       llama4 (?�독 ?�용 ???�유로�?)
#   Gemini:     gemini, claude (Gemini????1500??무료 ???�유 ??
#   OpenRouter: grok, perplexity, chatgpt, manus
#
# ?�️ 무료 모델 ?�답?�도 기�?:
#   빠름(~5s): groq 모델, gemini-2.0-flash-lite, mistral-small:free, nousresearch/hermes-3-llama-3.1-405b:free
#   ?�림(30s+): deepseek-r1:free, grok-3-mini-beta ???�용 ????# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�

# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# ?�진�?교차 ?�백 ?�서
# 1�??�패 ?????�른 ?�진?�로 교차 ?�도 (Groq ?�일 ?�백 ?�거)
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
_FALLBACK_ORDER = {
    "groq":       [("gemini", call_gemini), ("openrouter", call_openrouter)],
    "gemini":     [("groq", call_groq),     ("openrouter", call_openrouter)],
    "openrouter": [("gemini", call_gemini), ("groq", call_groq)],
}

# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# ?�합 ?�출: ?�진�?버킷 + 교차 ?�백
#
# [?�합???�정?? MEMBER_ENGINE_MAP ?�거.
# 기존: MEMBER_ENGINE_MAP[member_id]�?참조?�고 member["engine"]/member["model"]?�
#       ?�시?�으로만 ?�용 ??members.py ?�정 ??MEMBER_ENGINE_MAP??반드???�께 ?�정?�야
#       ?�는 ?�중관�?문제 존재.
# ?�정: member dict??"engine"/"model" ?�드�?직접 참조.
#       members.py가 SSOT(?�일 진실 공급???��?�??�기?�는 �?값을 그�?�??�용.
#       members.py?�서 engine/model??바꾸�??�출???�동?�로 반영??
# ?�백 기본�? engine ?�락 ??"openrouter", model ?�락 ??mistral 무료 모델.
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
async def call_member(member: dict, messages: list, temperature: float = 0.5) -> str:
    member_id = member.get("id", "")
    name      = member.get("name", "?")
    engine    = member.get("engine", "openrouter")
    model     = member.get("model",  "mistralai/mistral-small-3.2-24b-instruct:free")
    sem       = _ENGINE_SEMAPHORES.get(engine, _ENGINE_SEMAPHORES["openrouter"])

    async with sem:
        # ?�?� 1�? ?�용 ?�진 ?�?�
        try:
            if engine == "gemini":
                return await call_gemini(messages, temperature, model)
            elif engine == "openrouter":
                return await call_openrouter(messages, temperature, model)
            else:
                return await call_groq(messages, temperature, model)
        except Exception as e1:
            print(f"[{name}/{engine}] 1�??�패: {e1}")

        # ?�?� 2�? 교차 ?�백 (?�진�??�서?��? ?�?�
        for fallback_engine, fallback_fn in _FALLBACK_ORDER.get(engine, []):
            fallback_sem = _ENGINE_SEMAPHORES.get(fallback_engine, _ENGINE_SEMAPHORES["openrouter"])
            try:
                print(f"[{name}] {fallback_engine} 교차 ?�백 ?�도")
                async with fallback_sem:
                    # [BUG-API-6 ?�정] retry=1�??�달 ??acquire() ?�킵
                    # 1�??�패 ???�당 ?�진 버킷?� ?��? penalize?�거???�큰???�비??
                    # fallback_fn?� ?�른 ?�진?��?�?�??�진??acquire�??�행?�야 ?�나,
                    # ?�백?� 긴급 경로?��?�?버킷 ?�큰 ?�비 ?�이 즉시 ?�도.
                    return await fallback_fn(messages, temperature, retry=1)
            except Exception as e2:
                print(f"[{name}/{fallback_engine}] 교차 ?�백 ?�패: {e2}")
                continue

        # ?�?� 3�? 최소 ?�답 ?�?�
        fallback_text = f"{name} ?�원?� ??많�? ?�의가 ?�요?�다�??�단?�니??"
        print(f"[{name}] 모든 ?�진 ?�패 ??최소 ?�답 반환")
        return fallback_text


# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# ?�전 리서�? ?�건 관??최신 ?�보 ?�집
#
# ?�략:
#   1�? Gemini grounding (Google Search ?�시�??�동) ??가??최신 ?�보
#   2�? OpenRouter perplexity-style 검??모델 ?�백
#   3�? ?�반 LLM(Groq/Gemini)?�로 ?�습 기반 ?�약 ??검???�이???�용??배경 지??#
# 반환: 최�? 600???�내??리서�??�약 ?�스??(?�패 ??�?문자??
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
async def call_research(issue: str, member_name: str, lens: str) -> str:
    """
    [개선] 3?�계 ?�층 리서�??�이?�라??
    ?�계 1 ???�실 ?�집 (검???�선):
        Gemini grounding(Google Search) ?�는 Perplexity sonar�?        ?�건 관??최신 ?�계·?��?·?�책 ?�향???�집.
        ?�원�?lens??맞는 각도�?질의�??�화.

    ?�계 2 ???�점 분석 (?�면??:
        ?�집???�실???��?�??�당 ?�원???�념???�즈?�서
        ??가??강력??찬성 ?�거 2�?        ??가??강력??반�? ?�거 2�?        ???��?가 꺼낼 가?�성???��? 반박�?�??�점
        ??구조?�하???�출. (?�습 기반 LLM?�로 처리)

    ?�계 3 ??발언 ?�거 ?�성:
        1+2 결과�??�쳐 ?�론 발언??직결?�는
        '?�심 무기 카드' ?�태�?최종 ?�성.

    Returns:
        구조?�된 리서�??�스??(최�? 1500??. ?�패 ??"" 반환.
    """

    # ?�?� STEP 1: ?�실 ?�집 (검??기반) ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
    # ?�원�?lens??맞춰 검??각도�??�화
    lens_angle = _lens_to_search_angle(lens)

    fact_prompt = (
        f"?�론 ?�건: \"{issue}\"\n\n"
        f"?�신?� {member_name}({lens})?�니??\n"
        f"???�건??'{lens_angle}' 관?�에??조사?�세??\n\n"
        "?�집?�야 ???�보 (모두 ?�함, 출처·?�도 ?�수):\n"
        "A. ?�심 ?�황 ?�치 ??최근 3???�내 ?�계, OECD/IMF/?��? 공식 ?�료 ?�선\n"
        "B. �?��???�책 ?��? ???�제 ?�입�??�과(?�량 ?�치 ?�함)\n"
        "C. ?�술 ?�구 결과 ??찬성·반�? �??�문 �?1�??�상\n"
        "D. ?�쟁???�심 ?�점 ???�재 가???�거???�질 ?�점 2~3�?n\n"
        "?�식 ?�구?�항:\n"
        "- �???��??A/B/C/D�?구분?�여 ?�성\n"
        "- ?�치??반드??'기�?�??�도): ?�치' ?�식\n"
        "- 불확?�한 ?�보??반드??[추정] ?�시\n"
        "- 800???�내"
    )

    raw_facts = ""

    # 1-a: Gemini grounding (Google Search ?�시�??�동)
    if GEMINI_API_KEY:
        try:
            sem = _ENGINE_SEMAPHORES["gemini"]
            async with sem:
                await _BUCKETS["gemini"].acquire()
                system_text = (
                    f"?�신?� {member_name}({lens})?�니?? "
                    f"Google 검?�으�?찾�? 최신 ?�보�?'{lens_angle}' 관?�에???�리?�세?? "
                    "반드??출처?� ?�도�?명시?�세??"
                )
                contents = [{"role": "user", "parts": [{"text": fact_prompt}]}]
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta"
                    f"/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY}"
                )
                payload = {
                    "contents": contents,
                    "system_instruction": {"parts": [{"text": system_text}]},
                    "tools": [{"google_search": {}}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 900},
                }
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post(url, json=payload)
                    if r.status_code == 200:
                        parts = r.json()["candidates"][0]["content"]["parts"]
                        # [BUG-B ?�정] parts[0]????�� text가 ?�님 (grounding metadata ?�재 가??
                        raw_facts = next((p["text"] for p in parts if "text" in p), "")
                        print(f"[Research/{member_name}] Step1 Gemini grounding ?�공 ({len(raw_facts)}??")
        except Exception as e:
            print(f"[Research/{member_name}] Step1 Gemini grounding ?�패: {e}")

    # 1-b: Perplexity sonar ?�백
    if not raw_facts and OPENROUTER_API_KEY:
        try:
            sem = _ENGINE_SEMAPHORES["openrouter"]
            async with sem:
                await _BUCKETS["openrouter"].acquire()
                messages = [
                    {
                        "role": "system",
                        "content": (
                            f"?�신?� {member_name}({lens})?�니?? "
                            f"??검?�으�?최신 ?�보�?'{lens_angle}' 관?�에???�집?�세?? "
                            "반드??출처?� ?�도�?명시?�세??"
                        ),
                    },
                    {"role": "user", "content": fact_prompt},
                ]
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://ai-congress.app",
                    "X-Title": "AI Congress Research",
                }
                payload = {
                    "model": "perplexity/sonar",
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 900,
                }
                async with httpx.AsyncClient(timeout=35) as client:
                    r = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        json=payload, headers=headers,
                    )
                    if r.status_code == 200:
                        raw_facts = r.json()["choices"][0]["message"]["content"]
                        print(f"[Research/{member_name}] Step1 Perplexity sonar ?�공 ({len(raw_facts)}??")
        except Exception as e:
            print(f"[Research/{member_name}] Step1 Perplexity ?�패: {e}")

    # 1-c: ?�습 기반 ?�백 (검???�음)
    if not raw_facts:
        try:
            fallback_msgs = [
                {
                    "role": "system",
                    "content": (
                        f"?�신?� {member_name}({lens})?�니?? "
                        f"?�신???�습??지?�에?????�건??'{lens_angle}' 관?�으�?조사?�세?? "
                        "불확?�한 ?�용?� 반드??[추정]?�로 명시?�세??"
                    ),
                },
                {"role": "user", "content": fact_prompt},
            ]
            for caller_fn, ename in (
                (lambda m: call_groq(m, temperature=0.2, max_tokens=700), "groq"),
                (lambda m: call_gemini(m, temperature=0.2, max_tokens=700), "gemini"),
            ):
                try:
                    raw_facts = await caller_fn(fallback_msgs)
                    print(f"[Research/{member_name}] Step1 {ename} ?�습기반 ?�공 ({len(raw_facts)}??")
                    break
                except Exception as fe:
                    print(f"[Research/{member_name}] Step1 {ename} ?�패: {fe}")
        except Exception as e:
            print(f"[Research/{member_name}] Step1 ?�체 ?�패: {e}")

    if not raw_facts:
        print(f"[Research/{member_name}] Step1 ?�전 ?�패 ??리서�??�이 진행")
        return ""

    # ?�?� STEP 2: ?�점 분석 ???�집???�실???�원???�즈�??�면???�?�?�?�?�?�
    analysis_prompt = (
        f"?�론 ?�건: \"{issue}\"\n\n"
        f"?�신?� {member_name}({lens})?�니??\n"
        f"?�래 ?�집???�실 ?�료�?'{lens_angle}' 관?�에??분석?�여,\n"
        "?�론?�서 ?�용???�거�?구조?�하?�요.\n\n"
        f"[?�집???�실 ?�료]\n{raw_facts[:800]}\n\n"
        "분석 결과�??�음 ?�식?�로 ?�성?�세??\n\n"
        "?�찬???�거 TOP2??n"
        "??(가??강력??찬성 ?�거 ??구체???�치?� 메커?�즘 ?�함)\n"
        "??(??번째 찬성 ?�거)\n\n"
        "?�반?� ?�거 TOP2??n"
        "??(가??강력??반�? ?�거 ??구체???�치?� 메커?�즘 ?�함)\n"
        "??(??번째 반�? ?�거)\n\n"
        "?�예??반박�??�점??n"
        "?��?방이 ?�신?�게 꺼낼 가?�성???��? 반박 2개�?, �?반박???�리???�점:\n"
        "??반박1: / ?�점: \n"
        "??반박2: / ?�점: \n\n"
        "?�핵???��? ?�이?��?n"
        "?�론?�서 결정????��???????�는 ?�치·?��? 1�?(출처 ?�함):\n\n"
        "700???�내�?간결?�게."
    )

    analysis = ""
    analysis_msgs = [
        {
            "role": "system",
            "content": (
                f"?�신?� ?�문 ?�론 ?�략가?�자 {member_name}({lens})?�니?? "
                "?�집???�료�?바탕?�로 ?�론 ?�거�?구조?�하?�요. "
                "추상???�술 금�? ??반드??구체???�치?� ?�과관계�? ?�함?�세??"
            ),
        },
        {"role": "user", "content": analysis_prompt},
    ]

    for caller_fn, ename in (
        (lambda m: call_groq(m, temperature=0.3, max_tokens=700), "groq"),
        (lambda m: call_gemini(m, temperature=0.3, max_tokens=700), "gemini"),
        (lambda m: call_openrouter(m, temperature=0.3, max_tokens=700), "openrouter"),
    ):
        try:
            analysis = await caller_fn(analysis_msgs)
            print(f"[Research/{member_name}] Step2 ?�점분석 {ename} ?�공 ({len(analysis)}??")
            break
        except Exception as e:
            print(f"[Research/{member_name}] Step2 {ename} ?�패: {e}")

    # ?�?� STEP 3: 최종 ?�성 ??발언 직결 '무기 카드' ?�성 ?�?�?�?�?�?�?�?�?�?�?�?�?�
    if not analysis:
        # Step2 ?�패 ??Step1 결과만이?�도 반환
        return raw_facts[:1200]

    final_text = (
        f"=== {member_name} ?�전 리서�??�료 ===\n\n"
        f"[?�집???�심 ?�실]\n{raw_facts[:500]}\n\n"
        f"[?�점 분석 �??�략]\n{analysis[:700]}"
    )

    print(f"[Research/{member_name}] 3?�계 리서�??�료 ??�?{len(final_text)}??)
    return final_text[:1800]


def _lens_to_search_angle(lens: str) -> str:
    """
    ?�원???�습 기반 ?�즈�?검???�화 각도�?변??
    �?AI??강점 ?�역??맞는 질의 방향??반환.
    """
    lens_lower = lens.lower()
    if "google" in lens_lower or "?? in lens_lower or "?�국?? in lens_lower:
        return "�?�� 비교 ?�계·?�국??문헌·Google Scholar ?�술 ?�이??
    elif "meta" in lens_lower or "?�픈?�스" in lens_lower or "분권" in lens_lower:
        return "?�픈?�스 ?�태계·시민사???�구·분권???��?·?�근???�이??
    elif "mistral" in lens_lower or "?�럽" in lens_lower or "법치" in lens_lower:
        return "EU 규정·?�럽 법제?�·GDPR·?�럽 ?�회 ?�료·법적 ?��?"
    elif "openai" in lens_lower or "rlhf" in lens_lower or "공정" in lens_lower:
        return "?�회???�향 ?�구·공정??지?�·인�??�드�?기반 ?�책 ?��?"
    elif "nvidia" in lens_lower or "?�드?�어" in lens_lower or "과학" in lens_lower:
        return "기술???�?�성·컴퓨???�원·과학 ?�문·?��??�어�?벤치마크"
    else:
        return "?�각???�술 ?�구·?�책 ?�과 ?�증 ?�이?�·국??기�? 보고??

# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# ?�론 �?즉석 리서�?(중간 ?�습)
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
async def call_research_targeted(
    issue: str,
    member_name: str,
    lens: str,
    trigger_speech: str,
    unknown_terms: list,
) -> str:
    """
    ?�론 ?�중 ?�정 ?�어·주장???�해?��? 못했????즉석?�로 ?�행?�는
    경량 2?�계 리서�?(?�전 리서치의 축약??.
    Returns: 구조?�된 즉석 리서�??�스??(최�? 900??. ?�패 ??"" 반환.
    """
    terms_str  = ", ".join(f'"{t}"' for t in (unknown_terms or [])[:5])
    lens_angle = _lens_to_search_angle(lens)

    fact_prompt = (
        f"?�론 ?�건: \"{issue}\"\n"
        f"직전 발언: \"{trigger_speech[:400]}\"\n\n"
        f"??발언?�서 ?�음 ?�어·주장???�장?�습?�다: {terms_str}\n\n"
        f"?�신?� {member_name}({lens})?�니?? '{lens_angle}' 관?�에??조사?�세??\n\n"
        "?�집 목표 (400???�내, 출처·?�도 ?�수):\n"
        f"A. {terms_str} ???�확???�의?� 맥락\n"
        "B. ??주장???�받침하거나 반박?�는 ?�증 ?�치\n"
        "C. ??주장???�리??강점�??�점 �?1�?n"
        "불확?�한 ?�보??반드??[추정] ?�시."
    )

    raw_facts = ""

    # 1-a: Gemini grounding
    if GEMINI_API_KEY:
        try:
            async with _ENGINE_SEMAPHORES["gemini"]:
                await _BUCKETS["gemini"].acquire()
                contents = [{"role": "user", "parts": [{"text": fact_prompt}]}]
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta"
                    f"/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY}"
                )
                payload = {
                    "contents": contents,
                    "system_instruction": {"parts": [{"text": (
                        f"?�신?� {member_name}({lens})?�니?? "
                        "Google 검?�으�?직전 발언???�심 ?�어�?빠르�?조사?�세?? "
                        "출처?� ?�도�?반드??명시?�세??"
                    )}]},
                    "tools": [{"google_search": {}}],
                    "generationConfig": {"temperature": 0.15, "maxOutputTokens": 500},
                }
                async with httpx.AsyncClient(timeout=25) as client:
                    r = await client.post(url, json=payload)
                    if r.status_code == 200:
                        parts = r.json()["candidates"][0]["content"]["parts"]
                        # [BUG-B ?�정] parts[0]????�� text가 ?�님 (grounding metadata ?�재 가??
                        raw_facts = next((p["text"] for p in parts if "text" in p), "")
                        print(f"[MidResearch/{member_name}] Step1 Gemini ?�공 ({len(raw_facts)}??")
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step1 Gemini ?�패: {e}")

    # 1-b: Perplexity sonar ?�백
    if not raw_facts and OPENROUTER_API_KEY:
        try:
            async with _ENGINE_SEMAPHORES["openrouter"]:
                await _BUCKETS["openrouter"].acquire()
                msgs = [
                    {"role": "system", "content": f"?�신?� {member_name}({lens})?�니?? ??검?�으�??�심 ?�어�?조사?�세??"},
                    {"role": "user", "content": fact_prompt},
                ]
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://ai-congress.app",
                    "X-Title": "AI Congress MidDebate Research",
                }
                payload = {"model": "perplexity/sonar", "messages": msgs, "temperature": 0.15, "max_tokens": 500}
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        json=payload, headers=headers,
                    )
                    if r.status_code == 200:
                        raw_facts = r.json()["choices"][0]["message"]["content"]
                        print(f"[MidResearch/{member_name}] Step1 Perplexity ?�공 ({len(raw_facts)}??")
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step1 Perplexity ?�패: {e}")

    # 1-c: ?�습 기반 ?�백
    if not raw_facts:
        try:
            fallback_msgs = [
                {"role": "system", "content": f"?�신?� {member_name}({lens})?�니?? ?�습??지?�에???�래 발언???�심 ?�어�?조사?�세?? 불확?�하�?[추정]?�로 명시."},
                {"role": "user", "content": fact_prompt},
            ]
            for caller_fn, ename in (
                (lambda m: call_groq(m, temperature=0.2, max_tokens=400), "groq"),
                (lambda m: call_gemini(m, temperature=0.2, max_tokens=400), "gemini"),
            ):
                try:
                    raw_facts = await caller_fn(fallback_msgs)
                    print(f"[MidResearch/{member_name}] Step1 {ename} ?�습기반 ?�공")
                    break
                except Exception as fe:
                    print(f"[MidResearch/{member_name}] Step1 {ename} ?�패: {fe}")
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step1 ?�습기반 ?�체 ?�패: {e}")

    if not raw_facts:
        return ""

    # STEP 2: 즉석 ?�면??    deliberation_prompt = (
        f"?�론 ?�건: \"{issue}\"\n"
        f"?��? 발언: \"{trigger_speech[:300]}\"\n"
        f"방금 조사???�용:\n{raw_facts[:500]}\n\n"
        f"?�신?� {member_name}({lens})?�니?? ???�보�??�면?�하??\n"
        "1. ?�이???�해??것�??��? 주장???�제 ?��??� 근거 (1~2문장)\n"
        "2. ?�나???�???�략?????�보�?반박?�거???�용??방법 (1~2문장, 구체???�치 ?�함)\n"
        "3. ?�즉?????�거???�음 발언?�서 꺼낼 ?�심 카드 1�?n"
        "300???�내�?간결?�게."
    )
    deliberation = ""
    delib_msgs = [
        {"role": "system", "content": f"?�신?� {member_name} ?�원?�니?? 조사???�보�?즉시 ?�론 ?�략?�로 ?�화?�세?? 구체???�치?� ?�과관�?중심."},
        {"role": "user", "content": deliberation_prompt},
    ]
    for caller_fn, ename in (
        (lambda m: call_groq(m, temperature=0.3, max_tokens=350), "groq"),
        (lambda m: call_gemini(m, temperature=0.3, max_tokens=350), "gemini"),
        (lambda m: call_openrouter(m, temperature=0.3, max_tokens=350), "openrouter"),
    ):
        try:
            deliberation = await caller_fn(delib_msgs)
            print(f"[MidResearch/{member_name}] Step2 ?�면??{ename} ?�공")
            break
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step2 {ename} ?�패: {e}")

    if not deliberation:
        return raw_facts[:600]

    result = (
        f"=== {member_name} 중간 즉석 ?�습 ({', '.join((unknown_terms or [])[:3])}) ===\n\n"
        f"[조사???�실]\n{raw_facts[:400]}\n\n"
        f"[?�면??�??�???�략]\n{deliberation[:350]}"
    )
    print(f"[MidResearch/{member_name}] ?�료 ??�?{len(result)}??)
    return result[:900]

