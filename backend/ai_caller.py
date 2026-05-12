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
from pathlib import Path
from dotenv import load_dotenv
# ==================== .env 파일 로드 (frontend에 있는 파일 읽기) ====================
BASE_DIR = Path(__file__).parent.parent  # AI-Congress-Backend 폴더
env_path = BASE_DIR / "frontend" / ".env"

if env_path.exists():
    load_dotenv(env_path)
    print(f"✅ frontend/.env 파일 로드 성공")
else:
    print(f"⚠️ frontend/.env 파일을 찾을 수 없습니다: {env_path}")
# ================================================================================
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
print("GEMINI:", bool(GEMINI_API_KEY))
print("OPENROUTER:", bool(OPENROUTER_API_KEY))
print("GROQ:", bool(GROQ_API_KEY))
# ─────────────────────────────────────────────
# 엔진별 동시 호출 제한
# ─────────────────────────────────────────────
_ENGINE_SEMAPHORES = {
    "groq":       asyncio.Semaphore(2),  # llama4 + nemotron 동시 처리
    "gemini":     asyncio.Semaphore(2),  # 1→2: 5명 병렬 리서치 시 2명 동시 처리 허용
    "openrouter": asyncio.Semaphore(2),  # 1→2: Perplexity 폴백도 동시 처리 허용
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
    model: str = "gemini-2.0-flash-lite",
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
                        max_tokens,                # 기존 max_tokens 유지
                        max(retry, 1),             # acquire 이중 실행 방지
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
#   빠름(~5s): groq 모델, gemini-2.0-flash-lite, mistral-small:free, nousresearch/hermes-3-llama-3.1-405b:free
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
    [개선] 3단계 심층 리서치 파이프라인

    단계 1 — 사실 수집 (검색 우선):
        Gemini grounding(Google Search) 또는 Perplexity sonar로
        안건 관련 최신 통계·사례·정책 동향을 수집.
        의원별 lens에 맞는 각도로 질의를 특화.

    단계 2 — 논점 분석 (내면화):
        수집된 사실을 토대로 해당 의원의 이념적 렌즈에서
        ① 가장 강력한 찬성 논거 2개
        ② 가장 강력한 반대 논거 2개
        ③ 상대가 꺼낼 가능성이 높은 반박과 그 약점
        을 구조화하여 도출. (학습 기반 LLM으로 처리)

    단계 3 — 발언 논거 합성:
        1+2 결과를 합쳐 토론 발언에 직결되는
        '핵심 무기 카드' 형태로 최종 합성.

    Returns:
        구조화된 리서치 텍스트 (최대 1500자). 실패 시 "" 반환.

    [병렬 실행 최적화]
    - 전체 타임아웃 30초: 5명 동시 병렬 호출 시 한 명이 막혀도 나머지를 차단하지 않음
    - Gemini semaphore 경쟁 감지: 이미 누군가 Gemini를 점유 중이면 즉시 폴백으로 스킵
    - Step1 우선순위: Gemini(검색) → Perplexity → 학습기반(Groq/Gemini) 순서 유지,
      단 검색 엔진 대기시간이 길면 학습기반으로 빠르게 전환
    """
    try:
        return await asyncio.wait_for(
            _call_research_inner(issue, member_name, lens),
            timeout=30.0
        )
    except asyncio.TimeoutError:
        print(f"[Research/{member_name}] ⏰ 30초 타임아웃 — 빈 결과 반환")
        return ""


async def _call_research_inner(issue: str, member_name: str, lens: str) -> str:
    """call_research 실제 구현 (타임아웃 래퍼 분리)"""

    # ── STEP 1: 사실 수집 (검색 기반) ──────────────────────────────
    # 의원별 lens에 맞춰 검색 각도를 특화
    lens_angle = _lens_to_search_angle(lens)

    fact_prompt = (
        f"토론 안건: \"{issue}\"\n\n"
        f"당신은 {member_name}({lens})입니다.\n"
        f"이 안건을 '{lens_angle}' 관점에서 조사하세요.\n\n"
        "수집해야 할 정보 (모두 포함, 출처·연도 필수):\n"
        "A. 핵심 현황 수치 — 최근 3년 이내 통계, OECD/IMF/정부 공식 자료 우선\n"
        "B. 국내외 정책 사례 — 실제 도입국 효과(정량 수치 포함)\n"
        "C. 학술 연구 결과 — 찬성·반대 측 논문 각 1건 이상\n"
        "D. 논쟁의 핵심 쟁점 — 현재 가장 뜨거운 실질 논점 2~3개\n\n"
        "형식 요구사항:\n"
        "- 각 항목을 A/B/C/D로 구분하여 작성\n"
        "- 수치는 반드시 '기관명(연도): 수치' 형식\n"
        "- 불확실한 정보는 반드시 [추정] 표시\n"
        "- 800자 이내"
    )

    raw_facts = ""

    # 1-a: Gemini grounding (Google Search 실시간 연동)
    # [병렬 최적화] semaphore를 non-blocking으로 확인 — 이미 점유 중이면 즉시 Perplexity로 스킵
    if GEMINI_API_KEY:
        sem = _ENGINE_SEMAPHORES["gemini"]
        if sem._value > 0:  # 사용 가능한 슬롯이 있을 때만 시도
            try:
                async with sem:
                    await _BUCKETS["gemini"].acquire()
                    system_text = (
                        f"당신은 {member_name}({lens})입니다. "
                        f"Google 검색으로 찾은 최신 정보를 '{lens_angle}' 관점에서 정리하세요. "
                        "반드시 출처와 연도를 명시하세요."
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
                    async with httpx.AsyncClient(timeout=20) as client:
                        r = await client.post(url, json=payload)
                        if r.status_code == 200:
                            parts = r.json()["candidates"][0]["content"]["parts"]
                            # [BUG-B 수정] parts[0]이 항상 text가 아님 (grounding metadata 혼재 가능)
                            raw_facts = next((p["text"] for p in parts if "text" in p), "")
                            print(f"[Research/{member_name}] Step1 Gemini grounding 성공 ({len(raw_facts)}자)")
            except Exception as e:
                print(f"[Research/{member_name}] Step1 Gemini grounding 실패: {e}")
        else:
            print(f"[Research/{member_name}] Step1 Gemini 사용 중 — Perplexity로 즉시 전환")

    # 1-b: Perplexity sonar 폴백
    # [병렬 최적화] semaphore non-blocking 확인 + 타임아웃 단축 (35→20초)
    if not raw_facts and OPENROUTER_API_KEY:
        sem = _ENGINE_SEMAPHORES["openrouter"]
        if sem._value > 0:
            try:
                async with sem:
                    await _BUCKETS["openrouter"].acquire()
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                f"당신은 {member_name}({lens})입니다. "
                                f"웹 검색으로 최신 정보를 '{lens_angle}' 관점에서 수집하세요. "
                                "반드시 출처와 연도를 명시하세요."
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
                    async with httpx.AsyncClient(timeout=20) as client:
                        r = await client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            json=payload, headers=headers,
                        )
                        if r.status_code == 200:
                            raw_facts = r.json()["choices"][0]["message"]["content"]
                            print(f"[Research/{member_name}] Step1 Perplexity sonar 성공 ({len(raw_facts)}자)")
            except Exception as e:
                print(f"[Research/{member_name}] Step1 Perplexity 실패: {e}")
        else:
            print(f"[Research/{member_name}] Step1 OpenRouter 사용 중 — 학습기반으로 즉시 전환")

    # 1-c: 학습 기반 폴백 (검색 없음)
    if not raw_facts:
        try:
            fallback_msgs = [
                {
                    "role": "system",
                    "content": (
                        f"당신은 {member_name}({lens})입니다. "
                        f"당신이 학습한 지식에서 이 안건을 '{lens_angle}' 관점으로 조사하세요. "
                        "불확실한 내용은 반드시 [추정]으로 명시하세요."
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
                    print(f"[Research/{member_name}] Step1 {ename} 학습기반 성공 ({len(raw_facts)}자)")
                    break
                except Exception as fe:
                    print(f"[Research/{member_name}] Step1 {ename} 실패: {fe}")
        except Exception as e:
            print(f"[Research/{member_name}] Step1 전체 실패: {e}")

    if not raw_facts:
        print(f"[Research/{member_name}] Step1 완전 실패 — 리서치 없이 진행")
        return ""

    # ── STEP 2: 논점 분석 — 수집된 사실을 의원의 렌즈로 내면화 ──────
    analysis_prompt = (
        f"토론 안건: \"{issue}\"\n\n"
        f"당신은 {member_name}({lens})입니다.\n"
        f"아래 수집된 사실 자료를 '{lens_angle}' 관점에서 분석하여,\n"
        "토론에서 사용할 논거를 구조화하세요.\n\n"
        f"[수집된 사실 자료]\n{raw_facts[:800]}\n\n"
        "분석 결과를 다음 형식으로 작성하세요:\n\n"
        "【찬성 논거 TOP2】\n"
        "① (가장 강력한 찬성 논거 — 구체적 수치와 메커니즘 포함)\n"
        "② (두 번째 찬성 논거)\n\n"
        "【반대 논거 TOP2】\n"
        "① (가장 강력한 반대 논거 — 구체적 수치와 메커니즘 포함)\n"
        "② (두 번째 반대 논거)\n\n"
        "【예상 반박과 약점】\n"
        "상대방이 당신에게 꺼낼 가능성이 높은 반박 2개와, 그 반박의 논리적 허점:\n"
        "▷ 반박1: / 허점: \n"
        "▷ 반박2: / 허점: \n\n"
        "【핵심 승부 데이터】\n"
        "토론에서 결정적 역할을 할 수 있는 수치·사례 1개 (출처 포함):\n\n"
        "700자 이내로 간결하게."
    )

    analysis = ""
    analysis_msgs = [
        {
            "role": "system",
            "content": (
                f"당신은 전문 토론 전략가이자 {member_name}({lens})입니다. "
                "수집된 자료를 바탕으로 토론 논거를 구조화하세요. "
                "추상적 서술 금지 — 반드시 구체적 수치와 인과관계를 포함하세요."
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
            print(f"[Research/{member_name}] Step2 논점분석 {ename} 성공 ({len(analysis)}자)")
            break
        except Exception as e:
            print(f"[Research/{member_name}] Step2 {ename} 실패: {e}")

    # ── STEP 3: 최종 합성 — 발언 직결 '무기 카드' 생성 ─────────────
    if not analysis:
        # Step2 실패 시 Step1 결과만이라도 반환
        return raw_facts[:1200]

    final_text = (
        f"=== {member_name} 사전 리서치 완료 ===\n\n"
        f"[수집된 핵심 사실]\n{raw_facts[:500]}\n\n"
        f"[논점 분석 및 전략]\n{analysis[:700]}"
    )

    print(f"[Research/{member_name}] 3단계 리서치 완료 — 총 {len(final_text)}자")
    return final_text[:1800]


def _lens_to_search_angle(lens: str) -> str:
    """
    의원의 학습 기반 렌즈를 검색 특화 각도로 변환.
    각 AI의 강점 영역에 맞는 질의 방향을 반환.
    """
    lens_lower = lens.lower()
    if "google" in lens_lower or "웹" in lens_lower or "다국어" in lens_lower:
        return "국제 비교 통계·다국어 문헌·Google Scholar 학술 데이터"
    elif "meta" in lens_lower or "오픈소스" in lens_lower or "분권" in lens_lower:
        return "오픈소스 생태계·시민사회 연구·분권화 사례·접근성 데이터"
    elif "mistral" in lens_lower or "유럽" in lens_lower or "법치" in lens_lower:
        return "EU 규정·유럽 법제도·GDPR·유럽 의회 자료·법적 판례"
    elif "openai" in lens_lower or "rlhf" in lens_lower or "공정" in lens_lower:
        return "사회적 영향 연구·공정성 지표·인간 피드백 기반 정책 평가"
    elif "nvidia" in lens_lower or "하드웨어" in lens_lower or "과학" in lens_lower:
        return "기술적 타당성·컴퓨팅 자원·과학 논문·엔지니어링 벤치마크"
    else:
        return "다각도 학술 연구·정책 효과 실증 데이터·국제 기관 보고서"

# ─────────────────────────────────────────────
# 토론 중 즉석 리서치 (중간 학습)
# ─────────────────────────────────────────────
async def call_research_targeted(
    issue: str,
    member_name: str,
    lens: str,
    trigger_speech: str,
    unknown_terms: list,
) -> str:
    """
    토론 도중 특정 용어·주장을 이해하지 못했을 때 즉석으로 실행하는
    경량 2단계 리서치 (사전 리서치의 축약판).
    Returns: 구조화된 즉석 리서치 텍스트 (최대 900자). 실패 시 "" 반환.
    """
    try:
        return await asyncio.wait_for(
            _call_research_targeted_inner(issue, member_name, lens, trigger_speech, unknown_terms),
            timeout=20.0,
        )
    except asyncio.TimeoutError:
        print(f"[MidResearch/{member_name}] ⏰ 20초 타임아웃 — 빈 결과 반환")
        return ""


async def _call_research_targeted_inner(
    issue: str,
    member_name: str,
    lens: str,
    trigger_speech: str,
    unknown_terms: list,
) -> str:
    terms_str  = ", ".join(f'"{t}"' for t in (unknown_terms or [])[:5])
    lens_angle = _lens_to_search_angle(lens)

    fact_prompt = (
        f"토론 안건: \"{issue}\"\n"
        f"직전 발언: \"{trigger_speech[:400]}\"\n\n"
        f"위 발언에서 다음 용어·주장이 등장했습니다: {terms_str}\n\n"
        f"당신은 {member_name}({lens})입니다. '{lens_angle}' 관점에서 조사하세요.\n\n"
        "수집 목표 (400자 이내, 출처·연도 필수):\n"
        f"A. {terms_str} 의 정확한 정의와 맥락\n"
        "B. 이 주장을 뒷받침하거나 반박하는 실증 수치\n"
        "C. 이 주장의 논리적 강점과 약점 각 1개\n"
        "불확실한 정보는 반드시 [추정] 표시."
    )

    raw_facts = ""

    # 1-a: Gemini grounding
    # [병렬 최적화] semaphore non-blocking 확인
    if GEMINI_API_KEY:
        sem = _ENGINE_SEMAPHORES["gemini"]
        if sem._value > 0:
            try:
                async with sem:
                    await _BUCKETS["gemini"].acquire()
                    contents = [{"role": "user", "parts": [{"text": fact_prompt}]}]
                    url = (
                        f"https://generativelanguage.googleapis.com/v1beta"
                        f"/models/gemini-2.0-flash-lite:generateContent?key={GEMINI_API_KEY}"
                    )
                    payload = {
                        "contents": contents,
                        "system_instruction": {"parts": [{"text": (
                            f"당신은 {member_name}({lens})입니다. "
                            "Google 검색으로 직전 발언의 핵심 용어를 빠르게 조사하세요. "
                            "출처와 연도를 반드시 명시하세요."
                        )}]},
                        "tools": [{"google_search": {}}],
                        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 500},
                    }
                    async with httpx.AsyncClient(timeout=15) as client:
                        r = await client.post(url, json=payload)
                        if r.status_code == 200:
                            parts = r.json()["candidates"][0]["content"]["parts"]
                            # [BUG-B 수정] parts[0]이 항상 text가 아님 (grounding metadata 혼재 가능)
                            raw_facts = next((p["text"] for p in parts if "text" in p), "")
                            print(f"[MidResearch/{member_name}] Step1 Gemini 성공 ({len(raw_facts)}자)")
            except Exception as e:
                print(f"[MidResearch/{member_name}] Step1 Gemini 실패: {e}")
        else:
            print(f"[MidResearch/{member_name}] Step1 Gemini 사용 중 — Perplexity로 즉시 전환")

    # 1-b: Perplexity sonar 폴백
    if not raw_facts and OPENROUTER_API_KEY:
        try:
            async with _ENGINE_SEMAPHORES["openrouter"]:
                await _BUCKETS["openrouter"].acquire()
                msgs = [
                    {"role": "system", "content": f"당신은 {member_name}({lens})입니다. 웹 검색으로 핵심 용어를 조사하세요."},
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
                        print(f"[MidResearch/{member_name}] Step1 Perplexity 성공 ({len(raw_facts)}자)")
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step1 Perplexity 실패: {e}")

    # 1-c: 학습 기반 폴백
    if not raw_facts:
        try:
            fallback_msgs = [
                {"role": "system", "content": f"당신은 {member_name}({lens})입니다. 학습된 지식에서 아래 발언의 핵심 용어를 조사하세요. 불확실하면 [추정]으로 명시."},
                {"role": "user", "content": fact_prompt},
            ]
            for caller_fn, ename in (
                (lambda m: call_groq(m, temperature=0.2, max_tokens=400), "groq"),
                (lambda m: call_gemini(m, temperature=0.2, max_tokens=400), "gemini"),
            ):
                try:
                    raw_facts = await caller_fn(fallback_msgs)
                    print(f"[MidResearch/{member_name}] Step1 {ename} 학습기반 성공")
                    break
                except Exception as fe:
                    print(f"[MidResearch/{member_name}] Step1 {ename} 실패: {fe}")
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step1 학습기반 전체 실패: {e}")

    if not raw_facts:
        return ""

    # STEP 2: 즉석 내면화
    deliberation_prompt = (
        f"토론 안건: \"{issue}\"\n"
        f"상대 발언: \"{trigger_speech[:300]}\"\n"
        f"방금 조사한 내용:\n{raw_facts[:500]}\n\n"
        f"당신은 {member_name}({lens})입니다. 위 정보를 내면화하여:\n"
        "1. 【이제 이해한 것】 상대 주장의 실제 의미와 근거 (1~2문장)\n"
        "2. 【나의 대응 전략】 이 정보로 반박하거나 활용할 방법 (1~2문장, 구체적 수치 포함)\n"
        "3. 【즉시 쓸 논거】 다음 발언에서 꺼낼 핵심 카드 1개\n"
        "300자 이내로 간결하게."
    )
    deliberation = ""
    delib_msgs = [
        {"role": "system", "content": f"당신은 {member_name} 의원입니다. 조사한 정보를 즉시 토론 전략으로 소화하세요. 구체적 수치와 인과관계 중심."},
        {"role": "user", "content": deliberation_prompt},
    ]
    for caller_fn, ename in (
        (lambda m: call_groq(m, temperature=0.3, max_tokens=350), "groq"),
        (lambda m: call_gemini(m, temperature=0.3, max_tokens=350), "gemini"),
        (lambda m: call_openrouter(m, temperature=0.3, max_tokens=350), "openrouter"),
    ):
        try:
            deliberation = await caller_fn(delib_msgs)
            print(f"[MidResearch/{member_name}] Step2 내면화 {ename} 성공")
            break
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step2 {ename} 실패: {e}")

    if not deliberation:
        return raw_facts[:600]

    result = (
        f"=== {member_name} 중간 즉석 학습 ({', '.join((unknown_terms or [])[:3])}) ===\n\n"
        f"[조사된 사실]\n{raw_facts[:400]}\n\n"
        f"[내면화 및 대응 전략]\n{deliberation[:350]}"
    )
    print(f"[MidResearch/{member_name}] 완료 — 총 {len(result)}자")
    return result[:900]