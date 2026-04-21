import axios from 'axios';
import { MEMBERS } from '../constants/members';

// ─────────────────────────────────────────────
// API 키
// ─────────────────────────────────────────────
const GROQ_API_KEY       = process.env.EXPO_PUBLIC_GROQ_API_KEY       || "";
const GEMINI_API_KEY     = process.env.EXPO_PUBLIC_GEMINI_API_KEY     || "";
const OPENROUTER_API_KEY = process.env.EXPO_PUBLIC_OPENROUTER_API_KEY || "";

// ─────────────────────────────────────────────
// 의원별 전용 엔진 매핑
// 각 의원이 실제로 다른 AI API를 호출
// ─────────────────────────────────────────────
// api.js
export const MEMBER_ENGINE_MAP = {
  gemini:   { engine: "gemini",      model: "gemini-2.5-pro" },
  llama4:   { engine: "groq",        model: "meta-llama/llama-4-scout-17b-16e-instruct" },
  mistral:  { engine: "openrouter",  model: "mistralai/mistral-small-3.2-24b-instruct:free" },
  gptoss:   { engine: "openrouter",  model: "openai/gpt-oss-120b:free" },       // ✅ 확정
  nemotron: { engine: "openrouter",  model: "nvidia/llama-3.1-nemotron-ultra-253b-v1:free" }, // ✅ 확정
};
// ─────────────────────────────────────────────
// 발언 끊김 방지: 불완전 문장 제거
// ─────────────────────────────────────────────
const ensureComplete = (text) => {
  if (!text || text.trim() === "") return text;
  const trimmed = text.trim();
  if (/[.!?。！？"'」』…]$/.test(trimmed)) return trimmed;
  const match = trimmed.match(/^([\s\S]*[.!?。！？"'」』…])/);
  if (match) return match[1].trim();
  return trimmed;
};

// ─────────────────────────────────────────────
// Groq 호출
// ─────────────────────────────────────────────
const groqClient = axios.create({
  baseURL: 'https://api.groq.com/openai/v1',
  headers: {
    'Authorization': `Bearer ${GROQ_API_KEY}`,
    'Content-Type': 'application/json',
  },
  timeout: 45000,
});

export const getChatCompletion = async (
  messages,
  temperature = 0.7,
  retryCount = 0,
  model = 'llama-3.3-70b-versatile'
) => {
  try {
    if (!GROQ_API_KEY) return "시스템 오류: Groq API 키가 없습니다.";
    const response = await groqClient.post('/chat/completions', {
      model, messages, temperature,
      presence_penalty: 0.4,
      max_tokens: 600,
    });
    return ensureComplete(response.data.choices?.[0]?.message?.content || "");
  } catch (error) {
    if (error.response?.status === 429 && retryCount < 3) {
      await new Promise(r => setTimeout(r, (retryCount + 1) * 3000));
      return getChatCompletion(messages, temperature, retryCount + 1, model);
    }
    if (error.code === 'ECONNABORTED') return "응답 시간 초과.";
    console.error("Groq 실패:", error.response?.data || error.message);
    return "발언 생성 중 오류가 발생했습니다.";
  }
};

// ─────────────────────────────────────────────
// Gemini 호출
// ─────────────────────────────────────────────
export const getGeminiCompletion = async (
  messages,
  temperature = 0.4,
  model = "gemini-2.5-flash"
) => {
  if (!GEMINI_API_KEY) throw new Error("GEMINI_API_KEY 없음");
  const systemMsg = messages.find(m => m.role === 'system')?.content || "";
  const rawContents = messages.filter(m => m.role !== 'system');
  const geminiContents = [];
  for (const msg of rawContents) {
    const role = msg.role === "assistant" ? "model" : "user";
    if (geminiContents.length > 0 && geminiContents[geminiContents.length - 1].role === role) {
      geminiContents[geminiContents.length - 1].parts[0].text += "\n" + msg.content;
    } else {
      geminiContents.push({ role, parts: [{ text: msg.content }] });
    }
  }
  if (!geminiContents.length || geminiContents[geminiContents.length - 1].role === "model") {
    geminiContents.push({ role: "user", parts: [{ text: "발언하세요." }] });
  }
  const response = await axios.post(
    `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${GEMINI_API_KEY}`,
    {
      contents: geminiContents,
      system_instruction: { parts: [{ text: systemMsg }] },
      generationConfig: { temperature, maxOutputTokens: 600 },
    },
    { timeout: 45000 }
  );
  return ensureComplete(response.data.candidates?.[0]?.content?.parts?.[0]?.text || "");
};

// ─────────────────────────────────────────────
// OpenRouter 호출
// ─────────────────────────────────────────────
export const getOpenRouterCompletion = async (
  messages,
  temperature = 0.5,
  model = "mistralai/mistral-small-3.2-24b-instruct:free"
) => {
  if (!OPENROUTER_API_KEY) throw new Error("OPENROUTER_API_KEY 없음");
  const response = await axios.post(
    'https://openrouter.ai/api/v1/chat/completions',
    { model, messages, temperature, max_tokens: 600, presence_penalty: 0.4 },
    {
      headers: {
        'Authorization': `Bearer ${OPENROUTER_API_KEY}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://ai-congress.app',
        'X-Title': 'AI Congress',
      },
      timeout: 20000,  // 50000→20000: 느린 모델 교체로 20초면 충분
    }
  );
  return ensureComplete(response.data.choices?.[0]?.message?.content || "");
};

// ─────────────────────────────────────────────
// 통합 엔진 라우터
// ─────────────────────────────────────────────
const callMemberEngine = async (member, messages, temperature) => {
  const config = MEMBER_ENGINE_MAP[member.id];
  if (!config) return getChatCompletion(messages, temperature);
  const { engine, model } = config;
  try {
    if (engine === "gemini")     return await getGeminiCompletion(messages, temperature, model);
    if (engine === "openrouter") return await getOpenRouterCompletion(messages, temperature, model);
    if (engine === "groq")       return await getChatCompletion(messages, temperature, 0, model);
  } catch (err) {
    console.warn(`[${member.name}/${engine}] 실패 → Groq 폴백: ${err.message}`);
  }
  return getChatCompletion(messages, temperature);
};

// ─────────────────────────────────────────────
// DebateContext 클래스
//
// 핵심: 모든 발언을 버리지 않고 보존
// - allLogs: 전체 발언 원문
// - summary: 오래된 발언의 압축 요약
// - 각 의원 호출 시: [요약] + [최근 6개 전문] 전달
// → 토론 처음부터 끝까지 전체 흐름 인지
// ─────────────────────────────────────────────
export class DebateContext {
  constructor() {
    this.allLogs = [];      // { speaker, text }
    this.summary = "";      // 압축 요약
    this.RECENT_WINDOW    = 6;
    this.COMPRESS_TRIGGER = 10;
    this.COMPRESS_KEEP    = 4;
  }

  push(speaker, text) {
    this.allLogs.push({ speaker, text });
  }

  // LLM에 전달할 메시지 배열 생성
  toMessages() {
    const recent = this.allLogs.slice(-this.RECENT_WINDOW);
    const older  = this.allLogs.slice(0, Math.max(0, this.allLogs.length - this.RECENT_WINDOW));
    const messages = [];

    // 요약 블록
    const summaryParts = [];
    if (this.summary) summaryParts.push(this.summary);
    if (older.length > 0) {
      summaryParts.push(older.map(l => `${l.speaker}: ${l.text}`).join('\n'));
    }
    if (summaryParts.length > 0) {
      messages.push({
        role: "user",
        content: "━━ 이전 토론 요약 (반드시 숙지) ━━\n" + summaryParts.join('\n') + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
      });
      messages.push({ role: "assistant", content: "이전 토론 내용을 모두 숙지했습니다." });
    }

    // 최근 전문 (user/assistant 교대)
    recent.forEach((log, i) => {
      messages.push({
        role: i % 2 === 0 ? "user" : "assistant",
        content: `${log.speaker}: ${log.text}`,
      });
    });

    return messages;
  }

  // 발언 수 초과 시 오래된 발언 자동 압축
  async compressIfNeeded() {
    if (this.allLogs.length < this.COMPRESS_TRIGGER) return;
    const toCompress = this.allLogs.slice(0, this.allLogs.length - this.COMPRESS_KEEP);
    if (toCompress.length === 0) return;
    const compressText = toCompress.map(l => `${l.speaker}: ${l.text}`).join('\n');
    const prev = this.summary ? `이전 요약:\n${this.summary}\n\n` : "";
    try {
      this.summary = await getChatCompletion([
        {
          role: "system",
          content:
            "당신은 의회 토론 서기입니다. 아래 발언들을 압축 요약하세요.\n" +
            "포함 사항: 각 의원의 핵심 주장, 제시된 [DATA], [ADMIT]로 수정된 입장, [REFUTE]로 반박된 내용.\n" +
            "각 의원의 현재 최종 입장이 명확히 드러나도록 300자 이내로 요약하세요.",
        },
        { role: "user", content: prev + "압축할 발언:\n" + compressText },
      ], 0.3);
      this.allLogs = this.allLogs.slice(this.allLogs.length - this.COMPRESS_KEEP);
    } catch (e) {
      console.warn("맥락 압축 실패:", e.message);
    }
  }

  toPlainText() {
    const parts = [];
    if (this.summary) parts.push("[이전 토론 요약]\n" + this.summary);
    parts.push(...this.allLogs.map(l => `${l.speaker}: ${l.text}`));
    return parts.join('\n');
  }

  get length() { return this.allLogs.length; }
}

// ─────────────────────────────────────────────
// 의장 의사일정 생성
// ─────────────────────────────────────────────
export const getChairProtocol = async (issue) => {
  // 의장 무작위 선정
  const chairIndex = Math.floor(Math.random() * MEMBERS.length);
  const chair = MEMBERS[chairIndex];
  const panelists = MEMBERS.filter((_, i) => i !== chairIndex);

  const memberList = MEMBERS.map(m => `- ${m.name} (id: ${m.id})`).join('\n');
  const messages = [
    {
      role: "system",
      content:
        `당신은 의장 ${chair.name}입니다. 안건 성격에 따라 최적 토론 형식과 발언 순서를 설계하세요.\n\n` +
        `의원 목록:\n${memberList}\n\n` +
        "순수 JSON만 반환:\n" +
        `{"format":"릴레이|집중토론|전문가패널|자유토론","order":["id",...],"proposal":"이유","conclusionType":"VOTE|RESOLUTION","chairId":"${chair.id}"}`
    },
    {
      role: "user",
      content: `안건: "${issue}"\n최적 형식을 선택하고 order를 구성하세요.`
    },
  ];
  try {
    const raw = await callMemberEngine(chair, messages, 0.3);
    const s = raw.indexOf('{'), e = raw.lastIndexOf('}');
    if (s === -1 || e === -1) throw new Error("JSON 없음");
    const parsed = JSON.parse(raw.substring(s, e + 1));
    const validatedOrder = (parsed.order || [])
      .filter(id => MEMBERS.some(m => m.id === id))
      .slice(0, 16);
    return {
      chairId: chair.id,
      chairName: chair.name,
      format: parsed.format || "릴레이",
      order: validatedOrder.length > 2 ? validatedOrder : MEMBERS.map(m => m.id),
      proposal: parsed.proposal || "기본 절차에 따라 진행합니다.",
      conclusionType: parsed.conclusionType === "RESOLUTION" ? "RESOLUTION" : "VOTE",
    };
  } catch (e) {
    console.warn("의사일정 실패:", e.message);
    return {
      chairId: chair.id,
      chairName: chair.name,
      format: "릴레이",
      order: MEMBERS.map(m => m.id),
      proposal: "기본 절차.",
      conclusionType: "VOTE",
    };
  }
};

// ─────────────────────────────────────────────
// 의사일정 검토
// ─────────────────────────────────────────────
export const getMemberReview = async (member, issue, format) => {
  const memberList = MEMBERS.map(m => `- ${m.name}`).join('\n');
  const messages = [
    {
      role: "system",
      content:
        `당신은 ${member.name} 의원입니다. 전문 분야: ${member.lens}\n` +
        `의원 목록 (이 이름만 사용):\n${memberList}\n\n` +
        "의장 제안에 무조건 동의하지 마세요. 전문 분야 기반으로 의견을 내세요.\n" +
        "자신을 '본 의원'이라 칭하고, 완전한 문장으로 60자 이내 답변하세요.",
    },
    {
      role: "user",
      content: `안건: "${issue}"\n의장이 '${format}' 형식을 제안했습니다. 의견을 말씀해 주세요.`,
    },
  ];
  try {
    return await callMemberEngine(member, messages, 0.6) || "본 의원은 의장의 제안에 동의합니다.";
  } catch {
    return "본 의원은 의장의 제안을 지지합니다.";
  }
};

// ─────────────────────────────────────────────
// 의원 발언 생성 (DebateContext 기반)
// ─────────────────────────────────────────────
export const getAIOpinion = async (member, issue, debateCtx, chairName, isChair = false) => {
  const memberList = MEMBERS.map(m => `- ${m.name}`).join('\n');

  // 직전 발언 명시
  const lastLog = debateCtx.allLogs[debateCtx.allLogs.length - 1];
  const lastSpeakerHint = lastLog
    ? `\n\n📌 직전 발언 (${lastLog.speaker}):\n"${lastLog.text.substring(0, 120)}"\n` +
      "→ 위 발언에 대해 동의·반박·보완 중 하나로 반드시 응답을 시작하세요."
    : "";

  const baseSystem =
    "당신은 AI 의회 토론 참여자입니다.\n" +
    `의원 목록 (이 이름만 사용):\n${memberList}\n\n` +
    "발언 태그:\n" +
    "[REFUTE]: 상대 논리·데이터 오류 지적\n" +
    "[ADMIT]: 상대가 더 타당 → 반드시 구체적 입장 수정 포함\n" +
    "[DATA]: 수치·통계. 예: [DATA] 2023년 AI 시장 1,500억 달러\n" +
    "[GRAPHIC]: 텍스트 시각화\n" +
    "  [GRAPHIC]\n  찬성 ████████░░ 78%\n  반대 ██░░░░░░░░ 22%\n\n" +
    "필수:\n" +
    "- 이전 토론 내용을 충분히 숙지하고 발언하세요.\n" +
    "- 이미 논의된 내용은 반복 금지. 새 관점·데이터만 추가하세요.\n" +
    "- 반드시 마침표·느낌표·물음표로 문장을 완전히 끝내세요.\n" +
    "- 200자 이내로 발언하세요.\n";

  const roleSystem = isChair
    ? `당신은 의장 ${member.name}입니다.\n` +
      "자신을 '의장' 또는 '본 의장'이라 칭하세요.\n" +
      "현재 찬반 입장을 간략히 요약하고 토론을 이어가세요. 개인 주장 금지.\n" +
      "100자 이내. 반드시 완전한 문장으로 끝내세요."
    : `당신은 ${member.name} 의원입니다. 전문 분야: ${member.lens}\n` +
      "자신을 '본 의원'이라 하세요.\n" +
      `의장: '${chairName} 의장님' / 다른 의원: '○○ 의원님'\n` +
      "전문 분야의 모든 지식과 데이터를 최대한 활용하세요.\n" +
      "[ADMIT] 이후 수정된 입장을 이후에도 일관되게 유지하세요.\n" +
      "반드시 완전한 문장으로 끝내세요.";

  const messages = [
    { role: "system", content: baseSystem + roleSystem },
    ...debateCtx.toMessages(),
    {
      role: "user",
      content:
        `안건: "${issue}"${lastSpeakerHint}\n\n` +
        "지금 당신의 발언 차례입니다. 앞선 토론 전체를 숙지한 상태에서 소신있게 발언하세요.",
    },
  ];

  try {
    const result = await callMemberEngine(member, messages, isChair ? 0.25 : 0.5);
    return result || `${member.name} 의원은 이 안건에 대해 신중한 검토가 필요하다고 봅니다.`;
  } catch {
    return `${member.name} 의원은 이 안건에 대해 더 많은 논의가 필요하다고 판단합니다.`;
  }
};

// ─────────────────────────────────────────────
// 공동 결의안 생성
// ─────────────────────────────────────────────
export const generateResolution = async (issue, debateCtx) => {
  const fullText = debateCtx.toPlainText();
  const messages = [
    {
      role: "system",
      content:
        "당신은 의회 서기입니다. 전체 토론 내용을 바탕으로 공식 결의문을 작성하세요.\n" +
        "형식: 1)전문(배경과 논의 경과) 2)결의 조항(번호 매기기) 3)서명란\n" +
        "[ADMIT]로 수용된 의견을 반드시 반영하세요. 500자 이내. 완전한 문장으로 끝내세요.",
    },
    {
      role: "user",
      content: `안건: "${issue}"\n\n전체 토론:\n${fullText}\n\n결의문을 작성하세요.`,
    },
  ];
  return getChatCompletion(messages, 0.5);
};

// ─────────────────────────────────────────────
// 최종 투표
// ─────────────────────────────────────────────
export const getFinalVote = async (member, issue, memberMemory, debateCtx) => {
  const memberList = MEMBERS.map(m => `- ${m.name}`).join('\n');
  const admitNote = memberMemory.some(m => m.includes("[ADMIT]"))
    ? "\n당신은 토론 중 일부 입장을 수정했습니다. 수정된 입장으로 투표하세요."
    : "";
  const fullSummary = debateCtx.summary
    ? `\n\n[전체 토론 요약]\n${debateCtx.summary}` : "";

  const messages = [
    {
      role: "system",
      content:
        `당신은 ${member.name} 의원입니다. 전문 분야: ${member.lens}.\n` +
        `의원 목록:\n${memberList}\n\n` +
        `토론 발언:\n"""\n${memberMemory.join('\n')}\n"""` +
        fullSummary + admitNote +
        "\n\n투표 규칙:\n" +
        "- 위 발언과 논리적으로 일관된 투표를 하세요.\n" +
        "- 형식: [찬성|반대|기권] 이유 (200자 이내, 완전한 문장으로)",
    },
    { role: "user", content: `안건 "${issue}"에 최종 투표하세요.` },
  ];
  try {
    return await callMemberEngine(member, messages, 0.2) || "[기권] 오류로 기권 처리합니다.";
  } catch {
    return "[기권] 시스템 오류로 기권 처리합니다.";
  }
};

// ─────────────────────────────────────────────
// 토론 기록 포맷팅 (저장용)
// ─────────────────────────────────────────────
export const formatDebateLog = (issue, history) => {
  if (!history || history.length === 0) return "기록된 발언이 없습니다.";
  const now = new Date().toLocaleString('ko-KR');
  const header =
    `==========================================\n` +
    `🏛️  AI 의회 토론 공식 기록물\n` +
    `==========================================\n` +
    `안건: ${issue}\n일시: ${now}\n총 발언: ${history.length}건\n` +
    `------------------------------------------\n\n`;
  const body = history.map((log, i) => {
    const tag = log.type === "REFUTE" ? " [반박]" : log.type === "ADMIT" ? " [수용]" : "";
    return `[${i + 1}] ${log.displayName}${tag}\n${log.text}\n`;
  }).join('\n');
  return header + body +
    `\n------------------------------------------\n` +
    `본 문서는 AI 의결 시스템에 의해 작성되었습니다.\n` +
    `Copyright © 2025 AI Congress Simulation.\n` +
    `==========================================`;
};