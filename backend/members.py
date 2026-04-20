"""
members.py — AI 의회 백엔드용 의원 데이터
members.js와 항상 동기화 상태를 유지하세요.

Kimi, GLM-5 제거 완료. 총 8명.
"""

MEMBERS = [
    {
        "id":     "gemini",
        "name":   "Gemini",
        "lens":   "국제법 및 통계 데이터 분석",
        "color":  "#4285F4",
        "avatar": "♊",
        "pitch":  1.0,
        "engine": "gemini",
        "model":  "gemini-2.5-flash",
    },
    {
        "id":     "chatgpt",
        "name":   "챗지피티",
        "lens":   "사회적 지표 및 보편적 가치 체계",
        "color":  "#10A37F",
        "avatar": "⚡",
        "pitch":  1.1,
        "engine": "groq",
        "model":  "llama-3.3-70b-versatile",
    },
    {
        "id":     "perplexity",
        "name":   "Perplexity",
        "lens":   "실시간 논문 및 최신 뉴스 근거",
        "color":  "#20B2AA",
        "avatar": "🔍",
        "pitch":  0.9,
        "engine": "openrouter",
        "model":  "mistralai/mistral-small-3.2-24b-instruct:free",
    },
    {
        "id":     "grok",
        "name":   "Grok",
        "lens":   "실용주의 및 비판적 반론",
        "color":  "#FFFFFF",
        "avatar": "🏴",
        "pitch":  1.2,
        "engine": "openrouter",
        "model":  "x-ai/grok-3-mini-beta",
    },
    {
        "id":     "claude",
        "name":   "Claude",
        "lens":   "윤리적 정당성 및 인권 가치 보호",
        "color":  "#D97757",
        "avatar": "🛡️",
        "pitch":  0.8,
        "engine": "openrouter",
        "model":  "anthropic/claude-3-haiku",
    },
    {
        "id":     "manus",
        "name":   "Manus",
        "lens":   "기술적 실무 데이터 및 공학적 실행력",
        "color":  "#9B59B6",
        "avatar": "⚙️",
        "pitch":  1.0,
        "engine": "openrouter",
        "model":  "qwen/qwen3-8b:free",
    },
    {
        "id":     "deepseek",
        "name":   "DeepSeek",
        "lens":   "비용 효율성 및 오픈소스 아키텍처 분석",
        "color":  "#007BFF",
        "avatar": "🐋",
        "pitch":  1.1,
        "engine": "openrouter",
        "model":  "deepseek/deepseek-r1-0528:free",
    },
    {
        "id":     "llama4",
        "name":   "라마",
        "lens":   "보안 데이터 및 초거대 모델 분석",
        "color":  "#0668E1",
        "avatar": "🦙",
        "pitch":  1.05,
        "engine": "groq",
        "model":  "meta-llama/llama-4-scout-17b-16e-instruct",
    },
]

# id → member dict 빠른 조회용
MEMBER_MAP = {m["id"]: m for m in MEMBERS}