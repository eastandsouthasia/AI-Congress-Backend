"""
AI 의원 정의
각 의원은 서로 다른 실제 AI API를 사용
"""

MEMBERS = [
    {
        "id": "gemini",
        "name": "Gemini",
        "avatar": "♊",
        "color": "#4285F4",
        "lens": "국제법 및 통계 데이터 분석",
        "engine": "gemini",
        "model": "gemini-2.5-flash",
    },
    {
        "id": "chatgpt",
        "name": "ChatGPT",
        "avatar": "⚡",
        "color": "#10A37F",
        "lens": "사회적 지표 및 보편적 가치 체계",
        "engine": "groq",
        "model": "llama-3.3-70b-versatile",
    },
    {
        "id": "perplexity",
        "name": "Perplexity",
        "avatar": "🔍",
        "color": "#20B2AA",
        "lens": "실시간 논문 및 최신 뉴스 근거",
        "engine": "openrouter",
        "model": "mistralai/mistral-small-3.2-24b-instruct:free",
    },
    {
        "id": "grok",
        "name": "Grok",
        "avatar": "🏴",
        "color": "#FFFFFF",
        "lens": "실용주의 및 비판적 반론",
        "engine": "openrouter",
        "model": "x-ai/grok-3-mini-beta",
    },
    {
        "id": "claude",
        "name": "Claude",
        "avatar": "🛡️",
        "color": "#D97757",
        "lens": "윤리적 정당성 및 인권 가치 보호",
        "engine": "openrouter",
        "model": "anthropic/claude-3-haiku",
    },
    {
        "id": "manus",
        "name": "Manus",
        "avatar": "⚙️",
        "color": "#9B59B6",
        "lens": "기술적 실무 데이터 및 공학적 실행력",
        "engine": "openrouter",
        "model": "qwen/qwen3-8b:free",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "avatar": "🐋",
        "color": "#007BFF",
        "lens": "비용 효율성 및 오픈소스 아키텍처 분석",
        "engine": "openrouter",
        "model": "deepseek/deepseek-r1-0528:free",
    },
    {
        "id": "glm5",
        "name": "GLM-5",
        "avatar": "🇨🇳",
        "color": "#FF4500",
        "lens": "아시아 경제 지표 및 시장 동향 분석",
        "engine": "openrouter",
        "model": "thudm/glm-z1-32b:free",
    },
    {
        "id": "llama4",
        "name": "Llama 4",
        "avatar": "🦙",
        "color": "#0668E1",
        "lens": "보안 데이터 및 초거대 모델 분석",
        "engine": "groq",
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
    },
    {
        "id": "kimi",
        "name": "Kimi",
        "avatar": "🌙",
        "color": "#FFD700",
        "lens": "장기 인과 관계 및 협업 에이전트 분석",
        "engine": "openrouter",
        "model": "moonshotai/moonlight-16a-a3b-instruct:free",
    },
]

# id로 빠르게 찾기
MEMBER_MAP = {m["id"]: m for m in MEMBERS}
