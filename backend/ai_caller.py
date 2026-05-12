"""
AI ?¸ì¶œ ?ˆì´??- ?ˆì´??ë¦¬ë°‹ ?„ì „ ?€??ë²„ì „

?µì‹¬ ë³€ê²½ì‚¬??
- ?”ì§„ë³?ê¸€ë¡œë²Œ RPM ? í°ë²„í‚· (Groq 20/min, Gemini 12/min, OpenRouter 15/min)
- claude ??Geminië¡??´ë™ (Groq ê³¼ë????´ì†Œ)
- chatgpt ??OpenRouter mistralë¡??´ë™ (Groq ë¶„ì‚°)
- ?´ë°± ?œì„œ: ?„ìš©?”ì§„ ???¤ë¥¸?”ì§„ êµì°¨ ??Gemini ??ìµœì†Œ?‘ë‹µ
- penalize ì¶•ì†Œ (5ì´? ???Œë³µ ?œê°„ ?¨ì¶•
- 429 ??Retry-After ?¤ë” ?°ì„  ì¤€??"""

import os
import re
import time
import asyncio
import httpx
from pathlib import Path
from dotenv import load_dotenv
# ==================== .env ?Œì¼ ë¡œë“œ (frontend???ˆëŠ” ?Œì¼ ?½ê¸°) ====================
BASE_DIR = Path(__file__).parent.parent  # AI-Congress-Backend ?´ë”
env_path = BASE_DIR / ".env"

if env_path.exists():
    load_dotenv(env_path)
    print(f"??frontend/.env ?Œì¼ ë¡œë“œ ?±ê³µ")
else:
    print(f"? ï¸ frontend/.env ?Œì¼??ì°¾ì„ ???†ìŠµ?ˆë‹¤: {env_path}")
# ================================================================================
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
print("GEMINI:", bool(GEMINI_API_KEY))
print("OPENROUTER:", bool(OPENROUTER_API_KEY))
print("GROQ:", bool(GROQ_API_KEY))
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# ?”ì§„ë³??™ì‹œ ?¸ì¶œ ?œí•œ
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
_ENGINE_SEMAPHORES = {
    "groq":       asyncio.Semaphore(2),  # llama4 + nemotron ?™ì‹œ ì²˜ë¦¬
    "gemini":     asyncio.Semaphore(1),
    "openrouter": asyncio.Semaphore(1),  # 2??: openrouter???œì°¨ì²˜ë¦¬ë¡??ˆì •??}

# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# ? í° ë²„í‚· ?ˆì´??ë¦¬ë???# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
class TokenBucket:
    """
    rpm: ë¶„ë‹¹ ìµœë? ?”ì²­ ??    burst: ?œê°„ ìµœë? ? í° (ê¸°ë³¸ = rpm???ˆë°˜, ìµœì†Œ 1)
    """
    def __init__(self, rpm: int, burst: int = None):
        self.rpm         = rpm
        self.capacity    = burst or max(1, rpm // 2)
        self.tokens      = float(self.capacity)
        self.last_refill = time.monotonic()
        self._lock       = None  # asyncio.Lock?€ ?´ë²¤?¸ë£¨???ì„± ??ì´ˆê¸°??
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
            print(f"[RateLimit] {wait:.1f}ì´??€ê¸?ì¤?..")
            await asyncio.sleep(wait)
            self.tokens = 0.0

    def penalize(self, seconds: float = 5.0):
        """429 ?˜ì‹  ??? í° ê°•ì œ ?Œì§„. ?¨ë„??5ì´ˆë¡œ ì¶•ì†Œ (?´ì „: 10~20ì´?"""
        self.tokens = max(self.tokens - seconds * (self.rpm / 60.0), -self.capacity)


# ?”ì§„ë³?ë²„í‚·
# ? ï¸ Groq??20 RPM?¼ë¡œ ??¶¤: claude+chatgptê°€ Groq?ì„œ ë¹ ì ¸?˜ê?ë¯€ë¡?#    llama4 ?¨ë… ?¬ìš© ?????¬ìœ ë¡?²Œ ?´ì˜ ê°€??_BUCKETS = {
    "groq":       TokenBucket(rpm=20, burst=2),
    "gemini":     TokenBucket(rpm=12, burst=2),
    "openrouter": TokenBucket(rpm=15, burst=2),
}

# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# ë¬¸ì¥ ?„ì„± ë³´ì¥ (ë°œì–¸ ?Šê? ë°©ì?)
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
def ensure_complete(text: str) -> str:
    if not text or not text.strip():
        return text
    text = text.strip()
    if re.search(r'[.!??‚ï¼ï¼?\'?ã€â€?$', text):
        return text
    match = re.search(r'^([\s\S]*[.!??‚ï¼ï¼?\'?ã€â€?)', text)
    if match:
        return match.group(1).strip()
    return text

# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# Groq ?¸ì¶œ
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
async def call_groq(
    messages: list,
    temperature: float = 0.5,
    model: str = "llama-3.3-70b-versatile",
    max_tokens: int = 300,
    retry: int = 0
) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY ?†ìŒ")

    # [BUG-5 ?˜ì •] retry > 0?´ë©´ acquire ê±´ë„ˆ?€.
    # ?¬ê? ?¸ì¶œ ???¨ìˆ˜ ì²?ì¤„ë????¬ì‹¤?‰ë˜ë¯€ë¡?acquireê°€ ?´ì¤‘ ?Œë¹„?˜ë˜ ë¬¸ì œ ?˜ì •.
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
                # Retry-After ?¤ë”ë¥?ìµœìš°? ìœ¼ë¡?ì¤€??                retry_after = int(r.headers.get("Retry-After", (retry + 1) * 5))
                wait = min(retry_after, 20)
                print(f"[Groq 429] {wait}ì´??€ê¸????¬ì‹œ??({retry+1}/2)")
                await asyncio.sleep(wait)
                return await call_groq(messages, temperature, model, retry + 1)

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                print(f"[Groq ?€?„ì•„?? ?¬ì‹œ??({retry+1}/1)")
                await asyncio.sleep(2)
                return await call_groq(messages, temperature, model, retry + 1)
            raise ValueError("Groq ?‘ë‹µ ?œê°„ ì´ˆê³¼")

# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# Gemini ?¸ì¶œ
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
async def call_gemini(
    messages: list,
    temperature: float = 0.4,
    model: str = "gemini-2.0-flash-lite",
    max_tokens: int = 300,
    retry: int = 0
) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY ?†ìŒ")

    # [BUG-5 ?˜ì •] retry > 0?´ë©´ acquire ê±´ë„ˆ?€
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
        contents.append({"role": "user", "parts": [{"text": "ë°œì–¸?˜ì„¸??"}]})

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
                print(f"[Gemini 429] {wait}ì´??€ê¸????¬ì‹œ??)
                await asyncio.sleep(wait)
                return await call_gemini(messages, temperature, model, retry + 1)

            r.raise_for_status()
            content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                await asyncio.sleep(2)
                return await call_gemini(messages, temperature, model, retry + 1)
            raise ValueError("Gemini ?‘ë‹µ ?œê°„ ì´ˆê³¼")

# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# OpenRouter ?¸ì¶œ
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
async def call_openrouter(
    messages: list,
    temperature: float = 0.5,
    model: str = "mistralai/mistral-small-3.2-24b-instruct:free",
    max_tokens: int = 350,
    retry: int = 0
) -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY ?†ìŒ")

    # [BUG-5 ?˜ì •] retry > 0?´ë©´ acquire ê±´ë„ˆ?€
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
                print(f"[OpenRouter 429] {wait}ì´??€ê¸????¬ì‹œ??)
                await asyncio.sleep(wait)
                return await call_openrouter(messages, temperature, model, retry + 1)

            if r.status_code == 402:
                # ?¬ë ˆ??ë¶€ì¡? ?¤ë¥¸ ë¬´ë£Œ ëª¨ë¸ë¡?êµì²´
                if model != "mistralai/mistral-small-3.2-24b-instruct:free":
                    print(f"[OpenRouter 402] ?¬ë ˆ??ë¶€ì¡???mistral ë¬´ë£Œ ?´ë°±")
                    return await call_openrouter(
                        messages, temperature,
                        "mistralai/mistral-small-3.2-24b-instruct:free",
                        max_tokens,                # ê¸°ì¡´ max_tokens ? ì?
                        max(retry, 1),             # acquire ?´ì¤‘ ?¤í–‰ ë°©ì?
                    )
                raise ValueError("OpenRouter ?¬ë ˆ???Œì§„")

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return ensure_complete(content)

        except httpx.TimeoutException:
            if retry < 1:
                await asyncio.sleep(2)
                return await call_openrouter(messages, temperature, model, retry + 1)
            raise ValueError("OpenRouter ?‘ë‹µ ?œê°„ ì´ˆê³¼")

# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# ?˜ì› ?”ì§„ ë§¤í•‘ ?????”ì§„ ë¶„ì‚° ?¬ë°°ì¹?#
# ë³€ê²???ë¬¸ì œ:
#   claude + chatgpt + llama4 ??ëª¨ë‘ Groq ??Groq RPM ??£¼
#
# ë³€ê²???ë¶„ì‚°:
#   Groq:       llama4 (?¨ë… ?¬ìš© ???¬ìœ ë¡œì?)
#   Gemini:     gemini, claude (Gemini????1500??ë¬´ë£Œ ???¬ìœ  ??
#   OpenRouter: grok, perplexity, chatgpt, manus
#
# ? ï¸ ë¬´ë£Œ ëª¨ë¸ ?‘ë‹µ?ë„ ê¸°ì?:
#   ë¹ ë¦„(~5s): groq ëª¨ë¸, gemini-2.0-flash-lite, mistral-small:free, nousresearch/hermes-3-llama-3.1-405b:free
#   ?ë¦¼(30s+): deepseek-r1:free, grok-3-mini-beta ???¬ìš© ????# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€

# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# ?”ì§„ë³?êµì°¨ ?´ë°± ?œì„œ
# 1ì°??¤íŒ¨ ?????¤ë¥¸ ?”ì§„?¼ë¡œ êµì°¨ ?œë„ (Groq ?¨ì¼ ?´ë°± ?œê±°)
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
_FALLBACK_ORDER = {
    "groq":       [("gemini", call_gemini), ("openrouter", call_openrouter)],
    "gemini":     [("groq", call_groq),     ("openrouter", call_openrouter)],
    "openrouter": [("gemini", call_gemini), ("groq", call_groq)],
}

# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# ?µí•© ?¸ì¶œ: ?”ì§„ë³?ë²„í‚· + êµì°¨ ?´ë°±
#
# [?•í•©???˜ì •?? MEMBER_ENGINE_MAP ?œê±°.
# ê¸°ì¡´: MEMBER_ENGINE_MAP[member_id]ë¥?ì°¸ì¡°?˜ê³  member["engine"]/member["model"]?€
#       ?œì‹œ?©ìœ¼ë¡œë§Œ ?¬ìš© ??members.py ?˜ì • ??MEMBER_ENGINE_MAP??ë°˜ë“œ???¨ê»˜ ?˜ì •?´ì•¼
#       ?˜ëŠ” ?´ì¤‘ê´€ë¦?ë¬¸ì œ ì¡´ì¬.
# ?˜ì •: member dict??"engine"/"model" ?„ë“œë¥?ì§ì ‘ ì°¸ì¡°.
#       members.pyê°€ SSOT(?¨ì¼ ì§„ì‹¤ ê³µê¸‰???´ë?ë¡??¬ê¸°?œëŠ” ê·?ê°’ì„ ê·¸ë?ë¡??¬ìš©.
#       members.py?ì„œ engine/model??ë°”ê¾¸ë©??¸ì¶œ???ë™?¼ë¡œ ë°˜ì˜??
# ?´ë°± ê¸°ë³¸ê°? engine ?„ë½ ??"openrouter", model ?„ë½ ??mistral ë¬´ë£Œ ëª¨ë¸.
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
async def call_member(member: dict, messages: list, temperature: float = 0.5) -> str:
    member_id = member.get("id", "")
    name      = member.get("name", "?")
    engine    = member.get("engine", "openrouter")
    model     = member.get("model",  "mistralai/mistral-small-3.2-24b-instruct:free")
    sem       = _ENGINE_SEMAPHORES.get(engine, _ENGINE_SEMAPHORES["openrouter"])

    async with sem:
        # ?€?€ 1ì°? ?„ìš© ?”ì§„ ?€?€
        try:
            if engine == "gemini":
                return await call_gemini(messages, temperature, model)
            elif engine == "openrouter":
                return await call_openrouter(messages, temperature, model)
            else:
                return await call_groq(messages, temperature, model)
        except Exception as e1:
            print(f"[{name}/{engine}] 1ì°??¤íŒ¨: {e1}")

        # ?€?€ 2ì°? êµì°¨ ?´ë°± (?”ì§„ë³??œì„œ?€ë¡? ?€?€
        for fallback_engine, fallback_fn in _FALLBACK_ORDER.get(engine, []):
            fallback_sem = _ENGINE_SEMAPHORES.get(fallback_engine, _ENGINE_SEMAPHORES["openrouter"])
            try:
                print(f"[{name}] {fallback_engine} êµì°¨ ?´ë°± ?œë„")
                async with fallback_sem:
                    # [BUG-API-6 ?˜ì •] retry=1ë¡??„ë‹¬ ??acquire() ?¤í‚µ
                    # 1ì°??¤íŒ¨ ???´ë‹¹ ?”ì§„ ë²„í‚·?€ ?´ë? penalize?ê±°??? í°???Œë¹„??
                    # fallback_fn?€ ?¤ë¥¸ ?”ì§„?´ë?ë¡?ê·??”ì§„??acquireë¥??¤í–‰?´ì•¼ ?˜ë‚˜,
                    # ?´ë°±?€ ê¸´ê¸‰ ê²½ë¡œ?´ë?ë¡?ë²„í‚· ? í° ?Œë¹„ ?†ì´ ì¦‰ì‹œ ?œë„.
                    return await fallback_fn(messages, temperature, retry=1)
            except Exception as e2:
                print(f"[{name}/{fallback_engine}] êµì°¨ ?´ë°± ?¤íŒ¨: {e2}")
                continue

        # ?€?€ 3ì°? ìµœì†Œ ?‘ë‹µ ?€?€
        fallback_text = f"{name} ?˜ì›?€ ??ë§ì? ?¼ì˜ê°€ ?„ìš”?˜ë‹¤ê³??ë‹¨?©ë‹ˆ??"
        print(f"[{name}] ëª¨ë“  ?”ì§„ ?¤íŒ¨ ??ìµœì†Œ ?‘ë‹µ ë°˜í™˜")
        return fallback_text


# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# ?¬ì „ ë¦¬ì„œì¹? ?ˆê±´ ê´€??ìµœì‹  ?•ë³´ ?˜ì§‘
#
# ?„ëµ:
#   1ì°? Gemini grounding (Google Search ?¤ì‹œê°??°ë™) ??ê°€??ìµœì‹  ?•ë³´
#   2ì°? OpenRouter perplexity-style ê²€??ëª¨ë¸ ?´ë°±
#   3ì°? ?¼ë°˜ LLM(Groq/Gemini)?¼ë¡œ ?™ìŠµ ê¸°ë°˜ ?”ì•½ ??ê²€???†ì´??? ìš©??ë°°ê²½ ì§€??#
# ë°˜í™˜: ìµœë? 600???´ë‚´??ë¦¬ì„œì¹??”ì•½ ?ìŠ¤??(?¤íŒ¨ ??ë¹?ë¬¸ì??
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
async def call_research(issue: str, member_name: str, lens: str) -> str:
    """
    [ê°œì„ ] 3?¨ê³„ ?¬ì¸µ ë¦¬ì„œì¹??Œì´?„ë¼??
    ?¨ê³„ 1 ???¬ì‹¤ ?˜ì§‘ (ê²€???°ì„ ):
        Gemini grounding(Google Search) ?ëŠ” Perplexity sonarë¡?        ?ˆê±´ ê´€??ìµœì‹  ?µê³„Â·?¬ë?Â·?•ì±… ?™í–¥???˜ì§‘.
        ?˜ì›ë³?lens??ë§ëŠ” ê°ë„ë¡?ì§ˆì˜ë¥??¹í™”.

    ?¨ê³„ 2 ???¼ì  ë¶„ì„ (?´ë©´??:
        ?˜ì§‘???¬ì‹¤??? ë?ë¡??´ë‹¹ ?˜ì›???´ë…???Œì¦ˆ?ì„œ
        ??ê°€??ê°•ë ¥??ì°¬ì„± ?¼ê±° 2ê°?        ??ê°€??ê°•ë ¥??ë°˜ë? ?¼ê±° 2ê°?        ???ë?ê°€ êº¼ë‚¼ ê°€?¥ì„±???’ì? ë°˜ë°•ê³?ê·??½ì 
        ??êµ¬ì¡°?”í•˜???„ì¶œ. (?™ìŠµ ê¸°ë°˜ LLM?¼ë¡œ ì²˜ë¦¬)

    ?¨ê³„ 3 ??ë°œì–¸ ?¼ê±° ?©ì„±:
        1+2 ê²°ê³¼ë¥??©ì³ ? ë¡  ë°œì–¸??ì§ê²°?˜ëŠ”
        '?µì‹¬ ë¬´ê¸° ì¹´ë“œ' ?•íƒœë¡?ìµœì¢… ?©ì„±.

    Returns:
        êµ¬ì¡°?”ëœ ë¦¬ì„œì¹??ìŠ¤??(ìµœë? 1500??. ?¤íŒ¨ ??"" ë°˜í™˜.
    """

    # ?€?€ STEP 1: ?¬ì‹¤ ?˜ì§‘ (ê²€??ê¸°ë°˜) ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
    # ?˜ì›ë³?lens??ë§ì¶° ê²€??ê°ë„ë¥??¹í™”
    lens_angle = _lens_to_search_angle(lens)

    fact_prompt = (
        f"? ë¡  ?ˆê±´: \"{issue}\"\n\n"
        f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ??\n"
        f"???ˆê±´??'{lens_angle}' ê´€?ì—??ì¡°ì‚¬?˜ì„¸??\n\n"
        "?˜ì§‘?´ì•¼ ???•ë³´ (ëª¨ë‘ ?¬í•¨, ì¶œì²˜Â·?°ë„ ?„ìˆ˜):\n"
        "A. ?µì‹¬ ?„í™© ?˜ì¹˜ ??ìµœê·¼ 3???´ë‚´ ?µê³„, OECD/IMF/?•ë? ê³µì‹ ?ë£Œ ?°ì„ \n"
        "B. êµ?‚´???•ì±… ?¬ë? ???¤ì œ ?„ì…êµ??¨ê³¼(?•ëŸ‰ ?˜ì¹˜ ?¬í•¨)\n"
        "C. ?™ìˆ  ?°êµ¬ ê²°ê³¼ ??ì°¬ì„±Â·ë°˜ë? ì¸??¼ë¬¸ ê°?1ê±??´ìƒ\n"
        "D. ?¼ìŸ???µì‹¬ ?ì  ???„ì¬ ê°€???¨ê±°???¤ì§ˆ ?¼ì  2~3ê°?n\n"
        "?•ì‹ ?”êµ¬?¬í•­:\n"
        "- ê°???ª©??A/B/C/Dë¡?êµ¬ë¶„?˜ì—¬ ?‘ì„±\n"
        "- ?˜ì¹˜??ë°˜ë“œ??'ê¸°ê?ëª??°ë„): ?˜ì¹˜' ?•ì‹\n"
        "- ë¶ˆí™•?¤í•œ ?•ë³´??ë°˜ë“œ??[ì¶”ì •] ?œì‹œ\n"
        "- 800???´ë‚´"
    )

    raw_facts = ""

    # 1-a: Gemini grounding (Google Search ?¤ì‹œê°??°ë™)
    if GEMINI_API_KEY:
        try:
            sem = _ENGINE_SEMAPHORES["gemini"]
            async with sem:
                await _BUCKETS["gemini"].acquire()
                system_text = (
                    f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ?? "
                    f"Google ê²€?‰ìœ¼ë¡?ì°¾ì? ìµœì‹  ?•ë³´ë¥?'{lens_angle}' ê´€?ì—???•ë¦¬?˜ì„¸?? "
                    "ë°˜ë“œ??ì¶œì²˜?€ ?°ë„ë¥?ëª…ì‹œ?˜ì„¸??"
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
                        # [BUG-B ?˜ì •] parts[0]????ƒ textê°€ ?„ë‹˜ (grounding metadata ?¼ì¬ ê°€??
                        raw_facts = next((p["text"] for p in parts if "text" in p), "")
                        print(f"[Research/{member_name}] Step1 Gemini grounding ?±ê³µ ({len(raw_facts)}??")
        except Exception as e:
            print(f"[Research/{member_name}] Step1 Gemini grounding ?¤íŒ¨: {e}")

    # 1-b: Perplexity sonar ?´ë°±
    if not raw_facts and OPENROUTER_API_KEY:
        try:
            sem = _ENGINE_SEMAPHORES["openrouter"]
            async with sem:
                await _BUCKETS["openrouter"].acquire()
                messages = [
                    {
                        "role": "system",
                        "content": (
                            f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ?? "
                            f"??ê²€?‰ìœ¼ë¡?ìµœì‹  ?•ë³´ë¥?'{lens_angle}' ê´€?ì—???˜ì§‘?˜ì„¸?? "
                            "ë°˜ë“œ??ì¶œì²˜?€ ?°ë„ë¥?ëª…ì‹œ?˜ì„¸??"
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
                        print(f"[Research/{member_name}] Step1 Perplexity sonar ?±ê³µ ({len(raw_facts)}??")
        except Exception as e:
            print(f"[Research/{member_name}] Step1 Perplexity ?¤íŒ¨: {e}")

    # 1-c: ?™ìŠµ ê¸°ë°˜ ?´ë°± (ê²€???†ìŒ)
    if not raw_facts:
        try:
            fallback_msgs = [
                {
                    "role": "system",
                    "content": (
                        f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ?? "
                        f"?¹ì‹ ???™ìŠµ??ì§€?ì—?????ˆê±´??'{lens_angle}' ê´€?ìœ¼ë¡?ì¡°ì‚¬?˜ì„¸?? "
                        "ë¶ˆí™•?¤í•œ ?´ìš©?€ ë°˜ë“œ??[ì¶”ì •]?¼ë¡œ ëª…ì‹œ?˜ì„¸??"
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
                    print(f"[Research/{member_name}] Step1 {ename} ?™ìŠµê¸°ë°˜ ?±ê³µ ({len(raw_facts)}??")
                    break
                except Exception as fe:
                    print(f"[Research/{member_name}] Step1 {ename} ?¤íŒ¨: {fe}")
        except Exception as e:
            print(f"[Research/{member_name}] Step1 ?„ì²´ ?¤íŒ¨: {e}")

    if not raw_facts:
        print(f"[Research/{member_name}] Step1 ?„ì „ ?¤íŒ¨ ??ë¦¬ì„œì¹??†ì´ ì§„í–‰")
        return ""

    # ?€?€ STEP 2: ?¼ì  ë¶„ì„ ???˜ì§‘???¬ì‹¤???˜ì›???Œì¦ˆë¡??´ë©´???€?€?€?€?€?€
    analysis_prompt = (
        f"? ë¡  ?ˆê±´: \"{issue}\"\n\n"
        f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ??\n"
        f"?„ë˜ ?˜ì§‘???¬ì‹¤ ?ë£Œë¥?'{lens_angle}' ê´€?ì—??ë¶„ì„?˜ì—¬,\n"
        "? ë¡ ?ì„œ ?¬ìš©???¼ê±°ë¥?êµ¬ì¡°?”í•˜?¸ìš”.\n\n"
        f"[?˜ì§‘???¬ì‹¤ ?ë£Œ]\n{raw_facts[:800]}\n\n"
        "ë¶„ì„ ê²°ê³¼ë¥??¤ìŒ ?•ì‹?¼ë¡œ ?‘ì„±?˜ì„¸??\n\n"
        "?ì°¬???¼ê±° TOP2??n"
        "??(ê°€??ê°•ë ¥??ì°¬ì„± ?¼ê±° ??êµ¬ì²´???˜ì¹˜?€ ë©”ì»¤?ˆì¦˜ ?¬í•¨)\n"
        "??(??ë²ˆì§¸ ì°¬ì„± ?¼ê±°)\n\n"
        "?ë°˜?€ ?¼ê±° TOP2??n"
        "??(ê°€??ê°•ë ¥??ë°˜ë? ?¼ê±° ??êµ¬ì²´???˜ì¹˜?€ ë©”ì»¤?ˆì¦˜ ?¬í•¨)\n"
        "??(??ë²ˆì§¸ ë°˜ë? ?¼ê±°)\n\n"
        "?ì˜ˆ??ë°˜ë°•ê³??½ì ??n"
        "?ë?ë°©ì´ ?¹ì‹ ?ê²Œ êº¼ë‚¼ ê°€?¥ì„±???’ì? ë°˜ë°• 2ê°œì?, ê·?ë°˜ë°•???¼ë¦¬???ˆì :\n"
        "??ë°˜ë°•1: / ?ˆì : \n"
        "??ë°˜ë°•2: / ?ˆì : \n\n"
        "?í•µ???¹ë? ?°ì´?°ã€?n"
        "? ë¡ ?ì„œ ê²°ì •????• ???????ˆëŠ” ?˜ì¹˜Â·?¬ë? 1ê°?(ì¶œì²˜ ?¬í•¨):\n\n"
        "700???´ë‚´ë¡?ê°„ê²°?˜ê²Œ."
    )

    analysis = ""
    analysis_msgs = [
        {
            "role": "system",
            "content": (
                f"?¹ì‹ ?€ ?„ë¬¸ ? ë¡  ?„ëµê°€?´ì {member_name}({lens})?…ë‹ˆ?? "
                "?˜ì§‘???ë£Œë¥?ë°”íƒ•?¼ë¡œ ? ë¡  ?¼ê±°ë¥?êµ¬ì¡°?”í•˜?¸ìš”. "
                "ì¶”ìƒ???œìˆ  ê¸ˆì? ??ë°˜ë“œ??êµ¬ì²´???˜ì¹˜?€ ?¸ê³¼ê´€ê³„ë? ?¬í•¨?˜ì„¸??"
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
            print(f"[Research/{member_name}] Step2 ?¼ì ë¶„ì„ {ename} ?±ê³µ ({len(analysis)}??")
            break
        except Exception as e:
            print(f"[Research/{member_name}] Step2 {ename} ?¤íŒ¨: {e}")

    # ?€?€ STEP 3: ìµœì¢… ?©ì„± ??ë°œì–¸ ì§ê²° 'ë¬´ê¸° ì¹´ë“œ' ?ì„± ?€?€?€?€?€?€?€?€?€?€?€?€?€
    if not analysis:
        # Step2 ?¤íŒ¨ ??Step1 ê²°ê³¼ë§Œì´?¼ë„ ë°˜í™˜
        return raw_facts[:1200]

    final_text = (
        f"=== {member_name} ?¬ì „ ë¦¬ì„œì¹??„ë£Œ ===\n\n"
        f"[?˜ì§‘???µì‹¬ ?¬ì‹¤]\n{raw_facts[:500]}\n\n"
        f"[?¼ì  ë¶„ì„ ë°??„ëµ]\n{analysis[:700]}"
    )

    print(f"[Research/{member_name}] 3?¨ê³„ ë¦¬ì„œì¹??„ë£Œ ??ì´?{len(final_text)}??)
    return final_text[:1800]


def _lens_to_search_angle(lens: str) -> str:
    """
    ?˜ì›???™ìŠµ ê¸°ë°˜ ?Œì¦ˆë¥?ê²€???¹í™” ê°ë„ë¡?ë³€??
    ê°?AI??ê°•ì  ?ì—­??ë§ëŠ” ì§ˆì˜ ë°©í–¥??ë°˜í™˜.
    """
    lens_lower = lens.lower()
    if "google" in lens_lower or "?? in lens_lower or "?¤êµ­?? in lens_lower:
        return "êµ? œ ë¹„êµ ?µê³„Â·?¤êµ­??ë¬¸í—ŒÂ·Google Scholar ?™ìˆ  ?°ì´??
    elif "meta" in lens_lower or "?¤í”ˆ?ŒìŠ¤" in lens_lower or "ë¶„ê¶Œ" in lens_lower:
        return "?¤í”ˆ?ŒìŠ¤ ?íƒœê³„Â·ì‹œë¯¼ì‚¬???°êµ¬Â·ë¶„ê¶Œ???¬ë?Â·?‘ê·¼???°ì´??
    elif "mistral" in lens_lower or "? ëŸ½" in lens_lower or "ë²•ì¹˜" in lens_lower:
        return "EU ê·œì •Â·? ëŸ½ ë²•ì œ?„Â·GDPRÂ·? ëŸ½ ?˜íšŒ ?ë£ŒÂ·ë²•ì  ?ë?"
    elif "openai" in lens_lower or "rlhf" in lens_lower or "ê³µì •" in lens_lower:
        return "?¬íšŒ???í–¥ ?°êµ¬Â·ê³µì •??ì§€?œÂ·ì¸ê°??¼ë“œë°?ê¸°ë°˜ ?•ì±… ?‰ê?"
    elif "nvidia" in lens_lower or "?˜ë“œ?¨ì–´" in lens_lower or "ê³¼í•™" in lens_lower:
        return "ê¸°ìˆ ???€?¹ì„±Â·ì»´í“¨???ì›Â·ê³¼í•™ ?¼ë¬¸Â·?”ì??ˆì–´ë§?ë²¤ì¹˜ë§ˆí¬"
    else:
        return "?¤ê°???™ìˆ  ?°êµ¬Â·?•ì±… ?¨ê³¼ ?¤ì¦ ?°ì´?°Â·êµ­??ê¸°ê? ë³´ê³ ??

# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# ? ë¡  ì¤?ì¦‰ì„ ë¦¬ì„œì¹?(ì¤‘ê°„ ?™ìŠµ)
# ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
async def call_research_targeted(
    issue: str,
    member_name: str,
    lens: str,
    trigger_speech: str,
    unknown_terms: list,
) -> str:
    """
    ? ë¡  ?„ì¤‘ ?¹ì • ?©ì–´Â·ì£¼ì¥???´í•´?˜ì? ëª»í–ˆ????ì¦‰ì„?¼ë¡œ ?¤í–‰?˜ëŠ”
    ê²½ëŸ‰ 2?¨ê³„ ë¦¬ì„œì¹?(?¬ì „ ë¦¬ì„œì¹˜ì˜ ì¶•ì•½??.
    Returns: êµ¬ì¡°?”ëœ ì¦‰ì„ ë¦¬ì„œì¹??ìŠ¤??(ìµœë? 900??. ?¤íŒ¨ ??"" ë°˜í™˜.
    """
    terms_str  = ", ".join(f'"{t}"' for t in (unknown_terms or [])[:5])
    lens_angle = _lens_to_search_angle(lens)

    fact_prompt = (
        f"? ë¡  ?ˆê±´: \"{issue}\"\n"
        f"ì§ì „ ë°œì–¸: \"{trigger_speech[:400]}\"\n\n"
        f"??ë°œì–¸?ì„œ ?¤ìŒ ?©ì–´Â·ì£¼ì¥???±ì¥?ˆìŠµ?ˆë‹¤: {terms_str}\n\n"
        f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ?? '{lens_angle}' ê´€?ì—??ì¡°ì‚¬?˜ì„¸??\n\n"
        "?˜ì§‘ ëª©í‘œ (400???´ë‚´, ì¶œì²˜Â·?°ë„ ?„ìˆ˜):\n"
        f"A. {terms_str} ???•í™•???•ì˜?€ ë§¥ë½\n"
        "B. ??ì£¼ì¥???·ë°›ì¹¨í•˜ê±°ë‚˜ ë°˜ë°•?˜ëŠ” ?¤ì¦ ?˜ì¹˜\n"
        "C. ??ì£¼ì¥???¼ë¦¬??ê°•ì ê³??½ì  ê°?1ê°?n"
        "ë¶ˆí™•?¤í•œ ?•ë³´??ë°˜ë“œ??[ì¶”ì •] ?œì‹œ."
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
                        f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ?? "
                        "Google ê²€?‰ìœ¼ë¡?ì§ì „ ë°œì–¸???µì‹¬ ?©ì–´ë¥?ë¹ ë¥´ê²?ì¡°ì‚¬?˜ì„¸?? "
                        "ì¶œì²˜?€ ?°ë„ë¥?ë°˜ë“œ??ëª…ì‹œ?˜ì„¸??"
                    )}]},
                    "tools": [{"google_search": {}}],
                    "generationConfig": {"temperature": 0.15, "maxOutputTokens": 500},
                }
                async with httpx.AsyncClient(timeout=25) as client:
                    r = await client.post(url, json=payload)
                    if r.status_code == 200:
                        parts = r.json()["candidates"][0]["content"]["parts"]
                        # [BUG-B ?˜ì •] parts[0]????ƒ textê°€ ?„ë‹˜ (grounding metadata ?¼ì¬ ê°€??
                        raw_facts = next((p["text"] for p in parts if "text" in p), "")
                        print(f"[MidResearch/{member_name}] Step1 Gemini ?±ê³µ ({len(raw_facts)}??")
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step1 Gemini ?¤íŒ¨: {e}")

    # 1-b: Perplexity sonar ?´ë°±
    if not raw_facts and OPENROUTER_API_KEY:
        try:
            async with _ENGINE_SEMAPHORES["openrouter"]:
                await _BUCKETS["openrouter"].acquire()
                msgs = [
                    {"role": "system", "content": f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ?? ??ê²€?‰ìœ¼ë¡??µì‹¬ ?©ì–´ë¥?ì¡°ì‚¬?˜ì„¸??"},
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
                        print(f"[MidResearch/{member_name}] Step1 Perplexity ?±ê³µ ({len(raw_facts)}??")
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step1 Perplexity ?¤íŒ¨: {e}")

    # 1-c: ?™ìŠµ ê¸°ë°˜ ?´ë°±
    if not raw_facts:
        try:
            fallback_msgs = [
                {"role": "system", "content": f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ?? ?™ìŠµ??ì§€?ì—???„ë˜ ë°œì–¸???µì‹¬ ?©ì–´ë¥?ì¡°ì‚¬?˜ì„¸?? ë¶ˆí™•?¤í•˜ë©?[ì¶”ì •]?¼ë¡œ ëª…ì‹œ."},
                {"role": "user", "content": fact_prompt},
            ]
            for caller_fn, ename in (
                (lambda m: call_groq(m, temperature=0.2, max_tokens=400), "groq"),
                (lambda m: call_gemini(m, temperature=0.2, max_tokens=400), "gemini"),
            ):
                try:
                    raw_facts = await caller_fn(fallback_msgs)
                    print(f"[MidResearch/{member_name}] Step1 {ename} ?™ìŠµê¸°ë°˜ ?±ê³µ")
                    break
                except Exception as fe:
                    print(f"[MidResearch/{member_name}] Step1 {ename} ?¤íŒ¨: {fe}")
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step1 ?™ìŠµê¸°ë°˜ ?„ì²´ ?¤íŒ¨: {e}")

    if not raw_facts:
        return ""

    # STEP 2: ì¦‰ì„ ?´ë©´??    deliberation_prompt = (
        f"? ë¡  ?ˆê±´: \"{issue}\"\n"
        f"?ë? ë°œì–¸: \"{trigger_speech[:300]}\"\n"
        f"ë°©ê¸ˆ ì¡°ì‚¬???´ìš©:\n{raw_facts[:500]}\n\n"
        f"?¹ì‹ ?€ {member_name}({lens})?…ë‹ˆ?? ???•ë³´ë¥??´ë©´?”í•˜??\n"
        "1. ?ì´???´í•´??ê²ƒã€??ë? ì£¼ì¥???¤ì œ ?˜ë??€ ê·¼ê±° (1~2ë¬¸ì¥)\n"
        "2. ?ë‚˜???€???„ëµ?????•ë³´ë¡?ë°˜ë°•?˜ê±°???œìš©??ë°©ë²• (1~2ë¬¸ì¥, êµ¬ì²´???˜ì¹˜ ?¬í•¨)\n"
        "3. ?ì¦‰?????¼ê±°???¤ìŒ ë°œì–¸?ì„œ êº¼ë‚¼ ?µì‹¬ ì¹´ë“œ 1ê°?n"
        "300???´ë‚´ë¡?ê°„ê²°?˜ê²Œ."
    )
    deliberation = ""
    delib_msgs = [
        {"role": "system", "content": f"?¹ì‹ ?€ {member_name} ?˜ì›?…ë‹ˆ?? ì¡°ì‚¬???•ë³´ë¥?ì¦‰ì‹œ ? ë¡  ?„ëµ?¼ë¡œ ?Œí™”?˜ì„¸?? êµ¬ì²´???˜ì¹˜?€ ?¸ê³¼ê´€ê³?ì¤‘ì‹¬."},
        {"role": "user", "content": deliberation_prompt},
    ]
    for caller_fn, ename in (
        (lambda m: call_groq(m, temperature=0.3, max_tokens=350), "groq"),
        (lambda m: call_gemini(m, temperature=0.3, max_tokens=350), "gemini"),
        (lambda m: call_openrouter(m, temperature=0.3, max_tokens=350), "openrouter"),
    ):
        try:
            deliberation = await caller_fn(delib_msgs)
            print(f"[MidResearch/{member_name}] Step2 ?´ë©´??{ename} ?±ê³µ")
            break
        except Exception as e:
            print(f"[MidResearch/{member_name}] Step2 {ename} ?¤íŒ¨: {e}")

    if not deliberation:
        return raw_facts[:600]

    result = (
        f"=== {member_name} ì¤‘ê°„ ì¦‰ì„ ?™ìŠµ ({', '.join((unknown_terms or [])[:3])}) ===\n\n"
        f"[ì¡°ì‚¬???¬ì‹¤]\n{raw_facts[:400]}\n\n"
        f"[?´ë©´??ë°??€???„ëµ]\n{deliberation[:350]}"
    )
    print(f"[MidResearch/{member_name}] ?„ë£Œ ??ì´?{len(result)}??)
    return result[:900]
