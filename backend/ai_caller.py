"""
AI 호출 레이어
Gemini / Groq / OpenRouter 세 엔진 통합 관리
각 의원은 자신의 실제 엔진으로 호출됨
"""

import os, re, asyncio
import httpx

GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ─────────────────────────────────────────────
# 문장 완성 보장 (끊김 방지)
# ─────────────────────────────────────────────
def ensure_complete(text: str) -> str:
    if not text or not text.strip():
        return text
    text = text.strip()
    # 완전한 종결 부호로 끝나면 그대로
    if re.search(r'[.!?。！？"\'」』…]$', text):
        return text
    # 마지막 완전한 문장까지 잘라서 반환
    match = re.search(r'^([\s\S]*[.!?。！？"\'」』…])', text)
    if match:
        return match.group(1).strip()
    return text

# ─────────────────────────────────────────────
# Gemini 호출
# ─────────────────────────────────────────────
async def call_gemini(messages: list, temperature: float = 0.5, model: str = "gemini-2.5-flash") -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 없음")

    system_text = next((m["content"] for m in messages if m["role"] == "system"), "")
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "model" if m["role"] == "assistant" else "user"
        # 연속 같은 role 병합
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n" + m["content"]
        else:
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

    # 마지막이 model이면 user 추가
    if not contents or contents[-1]["role"] == "model":
        contents.append({"role": "user", "parts": [{"text": "발언하세요."}]})

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": system_text}]},
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 700},
    }

    async with httpx.AsyncClient(timeout=50) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return ensure_complete(text)

# ─────────────────────────────────────────────
# Groq 호출
# ─────────────────────────────────────────────
async def call_groq(messages: list, temperature: float = 0.5, model: str = "llama-3.3-70b-versatile", retry: int = 0) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY 없음")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 700,
        "presence_penalty": 0.4,
    }

    async with httpx.AsyncClient(timeout=50) as client:
        try:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code == 429 and retry < 3:
                await asyncio.sleep((retry + 1) * 3)
                return await call_groq(messages, temperature, model, retry + 1)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(text)
        except httpx.TimeoutException:
            raise ValueError("Groq 응답 시간 초과")

# ─────────────────────────────────────────────
# OpenRouter 호출
# ─────────────────────────────────────────────
async def call_openrouter(messages: list, temperature: float = 0.5, model: str = "mistralai/mistral-small-3.2-24b-instruct:free") -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY 없음")

    url = "https://openrouter.ai/api/v1/chat/completions"
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
        "max_tokens": 700,
        "presence_penalty": 0.4,
    }

    async with httpx.AsyncClient(timeout=55) as client:
        try:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(text)
        except httpx.TimeoutException:
            raise ValueError("OpenRouter 응답 시간 초과")

# ─────────────────────────────────────────────
# 통합 호출 (의원 설정 기반 자동 라우팅)
# 실패 시 Groq 폴백
# ─────────────────────────────────────────────
async def call_member(member: dict, messages: list, temperature: float = 0.5) -> str:
    engine = member.get("engine", "groq")
    model  = member.get("model", "llama-3.3-70b-versatile")
    name   = member.get("name", "?")

    try:
        if engine == "gemini":
            return await call_gemini(messages, temperature, model)
        elif engine == "openrouter":
            return await call_openrouter(messages, temperature, model)
        else:
            return await call_groq(messages, temperature, model)

    except Exception as e:
        print(f"[{name}/{engine}] 실패 → Groq 폴백: {e}")
        try:
            return await call_groq(messages, temperature)
        except Exception as e2:
            print(f"[{name}] Groq 폴백도 실패: {e2}")
            return f"{name} 의원은 기술적 문제로 이번 발언을 생략합니다."
