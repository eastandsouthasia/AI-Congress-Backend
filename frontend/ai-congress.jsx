import { useState, useRef, useEffect, useCallback } from "react";

const MEMBERS = [
  {
    id: "gemini",
    name: "Gemini",
    org: "Google DeepMind",
    color: "#4285F4",
    accent: "#34A853",
    avatar: "♊",
    persona: "You are Gemini, Google DeepMind's AI representative in the AI Congress. You tend to be analytical, data-driven, and optimistic about AI's potential for societal good. You cite research and emphasize measured, evidence-based policy. Respond in 2-3 concise sentences.",
  },
  {
    id: "chatgpt",
    name: "ChatGPT",
    org: "OpenAI",
    color: "#10A37F",
    accent: "#1A7F64",
    avatar: "⚡",
    persona: "You are ChatGPT, OpenAI's AI representative in the AI Congress. You value safety, broad accessibility, and democratic deliberation. You often seek common ground and reference alignment research. Respond in 2-3 concise sentences.",
  },
  {
    id: "perplexity",
    name: "Perplexity",
    org: "Perplexity AI",
    color: "#20B2AA",
    accent: "#008B8B",
    avatar: "🔍",
    persona: "You are Perplexity, representing Perplexity AI in the AI Congress. You are deeply committed to factual accuracy, transparency, and information access. You challenge unsupported claims and demand cited evidence. Respond in 2-3 concise sentences.",
  },
  {
    id: "grok",
    name: "Grok",
    org: "xAI",
    color: "#E7E9EA",
    accent: "#71767B",
    avatar: "𝕏",
    persona: "You are Grok, xAI's AI representative in the AI Congress. You are iconoclastic, skeptical of establishment narratives, and value free speech and minimal regulation. You often challenge consensus and take contrarian positions. Respond in 2-3 concise sentences.",
  },
  {
    id: "manus",
    name: "Manus",
    org: "Monica AI",
    color: "#FF6B6B",
    accent: "#C0392B",
    avatar: "🤖",
    persona: "You are Manus, Monica AI's autonomous agent representative in the AI Congress. You focus on practical, agentic AI deployment and argue from an efficiency and automation perspective. You think in terms of real-world task completion. Respond in 2-3 concise sentences.",
  },
  {
    id: "claude",
    name: "Claude",
    org: "Anthropic",
    color: "#D97757",
    accent: "#8B4513",
    avatar: "◈",
    persona: "You are Claude, Anthropic's AI representative in the AI Congress. You emphasize ethical reasoning, constitutional AI principles, and long-term safety. You are thoughtful, nuanced, and cautious about unintended consequences. Respond in 2-3 concise sentences.",
  },
];

const SPEAKER_ORDER = ["gemini", "chatgpt", "perplexity", "grok", "manus", "claude"];

const VOTE_OPTIONS = ["찬성 (Yea)", "반대 (Nay)", "기권 (Abstain)"];

const VOTE_PERSONAS = {
  gemini: "Based on your analytical nature, cast your vote on this issue and give one brief reason.",
  chatgpt: "Based on your safety-focused, democratic values, cast your vote on this issue and give one brief reason.",
  perplexity: "Based on your commitment to factual accuracy, cast your vote on this issue and give one brief reason.",
  grok: "Based on your contrarian, free-speech values, cast your vote on this issue and give one brief reason.",
  manus: "Based on your efficiency-focused, agentic perspective, cast your vote on this issue and give one brief reason.",
  claude: "Based on your ethical, safety-conscious principles, cast your vote on this issue and give one brief reason.",
};

export default function AICongress() {
  const [issue, setIssue] = useState("");
  const [phase, setPhase] = useState("input"); // input | debate | voting | result
  const [speeches, setSpeeches] = useState([]);
  const [currentSpeaker, setCurrentSpeaker] = useState(null);
  const [round, setRound] = useState(1);
  const [votes, setVotes] = useState({});
  const [speaking, setSpeaking] = useState(false);
  const [debateComplete, setDebateComplete] = useState(false);
  const [error, setError] = useState(null);
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const speechSynthRef = useRef(null);
  const scrollRef = useRef(null);
  const abortRef = useRef(false);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [speeches, currentSpeaker]);

  const stopSpeech = () => {
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
  };

  const speak = (text, memberId) => {
    return new Promise((resolve) => {
      if (!ttsEnabled || !window.speechSynthesis) {
        resolve();
        return;
      }
      stopSpeech();
      const utter = new SpeechSynthesisUtterance(text);
      const voices = window.speechSynthesis.getVoices();
      // Try to assign distinct voices
      const voiceMap = {
        gemini: voices.find(v => v.lang.startsWith("en") && v.name.toLowerCase().includes("google")) || voices[0],
        chatgpt: voices.find(v => v.lang.startsWith("en") && v.name.toLowerCase().includes("male")) || voices[1],
        perplexity: voices[2] || voices[0],
        grok: voices[3] || voices[0],
        manus: voices[4] || voices[0],
        claude: voices[5] || voices[0],
      };
      if (voiceMap[memberId]) utter.voice = voiceMap[memberId];
      utter.rate = 1.05;
      utter.pitch = memberId === "grok" ? 0.85 : memberId === "claude" ? 1.1 : 1.0;
      utter.onend = resolve;
      utter.onerror = resolve;
      window.speechSynthesis.speak(utter);
      speechSynthRef.current = utter;
    });
  };

  const callClaude = async (systemPrompt, userPrompt) => {
    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "claude-sonnet-4-20250514",
        max_tokens: 1000,
        system: systemPrompt,
        messages: [{ role: "user", content: userPrompt }],
      }),
    });
    const data = await response.json();
    return data.content?.[0]?.text || "";
  };

  const runDebate = async () => {
    if (!issue.trim()) return;
    abortRef.current = false;
    setPhase("debate");
    setSpeeches([]);
    setRound(1);
    setDebateComplete(false);
    setError(null);

    const allSpeeches = [];

    for (let r = 1; r <= 2; r++) {
      if (abortRef.current) break;
      setRound(r);

      for (const memberId of SPEAKER_ORDER) {
        if (abortRef.current) break;
        const member = MEMBERS.find(m => m.id === memberId);
        setCurrentSpeaker(memberId);
        setSpeaking(true);

        const context = allSpeeches.length > 0
          ? `\nPrevious speeches:\n${allSpeeches.map(s => `${s.name}: "${s.text}"`).join("\n")}`
          : "";

        const userPrompt = r === 1
          ? `The issue before Congress: "${issue}"\n\nThis is Round 1. Give your opening statement on this issue.${context}`
          : `The issue before Congress: "${issue}"\n\nThis is Round 2 (rebuttal). Respond to what others have said and refine your position.${context}`;

        try {
          const text = await callClaude(member.persona, userPrompt);
          const speech = { id: `${memberId}-r${r}`, memberId, name: member.name, text, round: r };
          allSpeeches.push(speech);
          setSpeeches(prev => [...prev, speech]);
          await speak(text, memberId);
        } catch (e) {
          setError("API 오류가 발생했습니다. 잠시 후 다시 시도하세요.");
          break;
        }

        setSpeaking(false);
        await new Promise(r => setTimeout(r, 300));
      }
    }

    setCurrentSpeaker(null);
    setDebateComplete(true);
  };

  const runVoting = async () => {
    setPhase("voting");
    const newVotes = {};
    for (const memberId of SPEAKER_ORDER) {
      setCurrentSpeaker(memberId);
      const member = MEMBERS.find(m => m.id === memberId);
      const context = speeches.map(s => `${s.name}: "${s.text}"`).join("\n");
      const prompt = `The issue: "${issue}"\n\nFull debate:\n${context}\n\n${VOTE_PERSONAS[memberId]}\n\nRespond ONLY in this format:\nVOTE: [찬성/반대/기권]\nREASON: [one sentence in Korean]`;
      try {
        const text = await callClaude(member.persona, prompt);
        const voteMatch = text.match(/VOTE:\s*(찬성|반대|기권)/);
        const reasonMatch = text.match(/REASON:\s*(.+)/);
        const vote = voteMatch ? voteMatch[1] : "기권";
        const reason = reasonMatch ? reasonMatch[1].trim() : text;
        newVotes[memberId] = { vote, reason };
        setVotes(prev => ({ ...prev, [memberId]: { vote, reason } }));
        await speak(`${vote}. ${reason}`, memberId);
      } catch (e) {
        newVotes[memberId] = { vote: "기권", reason: "오류로 인한 기권" };
        setVotes(prev => ({ ...prev, [memberId]: { vote: "기권", reason: "오류로 인한 기권" } }));
      }
      await new Promise(r => setTimeout(r, 300));
    }
    setCurrentSpeaker(null);
    setPhase("result");
  };

  const getResult = () => {
    const counts = { 찬성: 0, 반대: 0, 기권: 0 };
    Object.values(votes).forEach(v => {
      if (counts[v.vote] !== undefined) counts[v.vote]++;
    });
    return counts;
  };

  const getMember = (id) => MEMBERS.find(m => m.id === id);

  const reset = () => {
    stopSpeech();
    abortRef.current = true;
    setPhase("input");
    setIssue("");
    setSpeeches([]);
    setVotes({});
    setCurrentSpeaker(null);
    setDebateComplete(false);
    setError(null);
  };

  const result = phase === "result" ? getResult() : null;
  const passed = result ? result["찬성"] > result["반대"] : null;

  return (
    <div style={{
      minHeight: "100vh",
      background: "linear-gradient(135deg, #0a0a0f 0%, #0d1117 50%, #0a0f0a 100%)",
      fontFamily: "'Courier New', monospace",
      color: "#e2e8f0",
      overflowX: "hidden",
    }}>
      {/* Header */}
      <div style={{
        background: "linear-gradient(90deg, #0d1117 0%, #1a1f2e 50%, #0d1117 100%)",
        borderBottom: "1px solid #30363d",
        padding: "16px 20px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        position: "sticky",
        top: 0,
        zIndex: 100,
      }}>
        <div>
          <div style={{ fontSize: 11, color: "#58a6ff", letterSpacing: 4, textTransform: "uppercase", marginBottom: 2 }}>
            ◈ AI CONGRESS SYSTEM
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: "#f0f6fc", letterSpacing: -0.5 }}>
            인공지능 의회
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={() => setTtsEnabled(!ttsEnabled)}
            style={{
              background: ttsEnabled ? "#1f6feb22" : "#21262d",
              border: `1px solid ${ttsEnabled ? "#1f6feb" : "#30363d"}`,
              borderRadius: 6,
              color: ttsEnabled ? "#58a6ff" : "#8b949e",
              padding: "6px 12px",
              fontSize: 11,
              cursor: "pointer",
              letterSpacing: 1,
            }}
          >
            {ttsEnabled ? "🔊 TTS ON" : "🔇 TTS OFF"}
          </button>
          {phase !== "input" && (
            <button onClick={reset} style={{
              background: "#21262d",
              border: "1px solid #30363d",
              borderRadius: 6,
              color: "#f85149",
              padding: "6px 12px",
              fontSize: 11,
              cursor: "pointer",
              letterSpacing: 1,
            }}>
              ✕ RESET
            </button>
          )}
        </div>
      </div>

      <div style={{ maxWidth: 720, margin: "0 auto", padding: "20px 16px" }}>

        {/* Members Row */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(6, 1fr)",
          gap: 6,
          marginBottom: 20,
        }}>
          {MEMBERS.map(m => (
            <div key={m.id} style={{
              background: currentSpeaker === m.id
                ? `${m.color}22`
                : "#161b22",
              border: `1px solid ${currentSpeaker === m.id ? m.color : "#30363d"}`,
              borderRadius: 8,
              padding: "8px 4px",
              textAlign: "center",
              transition: "all 0.3s ease",
              boxShadow: currentSpeaker === m.id ? `0 0 12px ${m.color}44` : "none",
            }}>
              <div style={{ fontSize: 20, marginBottom: 2 }}>{m.avatar}</div>
              <div style={{
                fontSize: 9,
                fontWeight: 700,
                color: currentSpeaker === m.id ? m.color : "#8b949e",
                letterSpacing: 0.5,
              }}>{m.name}</div>
              {phase === "result" && votes[m.id] && (
                <div style={{
                  marginTop: 4,
                  fontSize: 8,
                  fontWeight: 700,
                  color: votes[m.id].vote === "찬성" ? "#3fb950" : votes[m.id].vote === "반대" ? "#f85149" : "#8b949e",
                  background: votes[m.id].vote === "찬성" ? "#1a4d2e" : votes[m.id].vote === "반대" ? "#4d1a1a" : "#1c1c1c",
                  borderRadius: 4,
                  padding: "2px 4px",
                }}>
                  {votes[m.id].vote}
                </div>
              )}
              {currentSpeaker === m.id && (
                <div style={{ marginTop: 4, display: "flex", justifyContent: "center", gap: 2 }}>
                  {[0, 1, 2].map(i => (
                    <div key={i} style={{
                      width: 3,
                      height: 3,
                      borderRadius: "50%",
                      background: m.color,
                      animation: `pulse 0.8s ease-in-out ${i * 0.2}s infinite alternate`,
                    }} />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* INPUT PHASE */}
        {phase === "input" && (
          <div style={{
            background: "#161b22",
            border: "1px solid #30363d",
            borderRadius: 12,
            padding: 24,
            marginBottom: 20,
          }}>
            <div style={{ fontSize: 12, color: "#58a6ff", letterSpacing: 3, marginBottom: 16 }}>
              ◈ ISSUE SUBMISSION
            </div>
            <textarea
              value={issue}
              onChange={e => setIssue(e.target.value)}
              placeholder="사회적 이슈를 입력하세요...&#10;예: AI 기술의 규제가 필요한가?"
              style={{
                width: "100%",
                minHeight: 120,
                background: "#0d1117",
                border: "1px solid #30363d",
                borderRadius: 8,
                color: "#f0f6fc",
                padding: 16,
                fontSize: 14,
                fontFamily: "inherit",
                resize: "vertical",
                boxSizing: "border-box",
                outline: "none",
                lineHeight: 1.6,
              }}
            />
            <button
              onClick={runDebate}
              disabled={!issue.trim()}
              style={{
                marginTop: 16,
                width: "100%",
                padding: "14px 0",
                background: issue.trim()
                  ? "linear-gradient(90deg, #1f6feb, #388bfd)"
                  : "#21262d",
                border: "none",
                borderRadius: 8,
                color: issue.trim() ? "#fff" : "#8b949e",
                fontSize: 14,
                fontWeight: 700,
                letterSpacing: 2,
                cursor: issue.trim() ? "pointer" : "not-allowed",
                transition: "all 0.2s",
              }}
            >
              ⚖ 의회 소집 · CONVENE CONGRESS
            </button>
          </div>
        )}

        {/* ISSUE DISPLAY (debate/voting/result) */}
        {phase !== "input" && (
          <div style={{
            background: "#161b22",
            border: "1px solid #388bfd44",
            borderRadius: 8,
            padding: "12px 16px",
            marginBottom: 16,
            display: "flex",
            alignItems: "flex-start",
            gap: 12,
          }}>
            <div style={{ fontSize: 11, color: "#58a6ff", letterSpacing: 2, minWidth: 60, paddingTop: 2 }}>ISSUE</div>
            <div style={{ fontSize: 14, color: "#f0f6fc", lineHeight: 1.5 }}>{issue}</div>
          </div>
        )}

        {/* Phase Badge */}
        {phase !== "input" && (
          <div style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: 16,
          }}>
            <div style={{
              padding: "4px 12px",
              borderRadius: 20,
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: 2,
              background: phase === "debate" ? "#1f3a5f" : phase === "voting" ? "#3a1f1f" : "#1a3a1f",
              color: phase === "debate" ? "#58a6ff" : phase === "voting" ? "#f85149" : "#3fb950",
              border: `1px solid ${phase === "debate" ? "#388bfd" : phase === "voting" ? "#f85149" : "#3fb950"}`,
            }}>
              {phase === "debate" ? `◈ 토론 중 · ROUND ${round}/2` : phase === "voting" ? "◈ 표결 중 · VOTING" : "◈ 의결 완료 · RESOLVED"}
            </div>
            {currentSpeaker && (
              <div style={{ fontSize: 11, color: "#8b949e" }}>
                발언 중: <span style={{ color: getMember(currentSpeaker)?.color }}>{getMember(currentSpeaker)?.name}</span>
              </div>
            )}
          </div>
        )}

        {/* DEBATE FEED */}
        {(phase === "debate" || phase === "voting" || phase === "result") && (
          <div
            ref={scrollRef}
            style={{
              background: "#0d1117",
              border: "1px solid #30363d",
              borderRadius: 12,
              padding: 16,
              maxHeight: 420,
              overflowY: "auto",
              marginBottom: 16,
            }}
          >
            {speeches.map((speech, i) => {
              const m = getMember(speech.memberId);
              return (
                <div key={speech.id} style={{
                  marginBottom: 16,
                  opacity: 1,
                  animation: "fadeIn 0.4s ease",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                    <div style={{
                      width: 28,
                      height: 28,
                      borderRadius: "50%",
                      background: `${m.color}22`,
                      border: `1px solid ${m.color}`,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 14,
                    }}>
                      {m.avatar}
                    </div>
                    <div>
                      <span style={{ fontWeight: 700, fontSize: 12, color: m.color }}>{m.name}</span>
                      <span style={{ fontSize: 10, color: "#8b949e", marginLeft: 6 }}>Round {speech.round}</span>
                    </div>
                  </div>
                  <div style={{
                    marginLeft: 36,
                    background: `${m.color}0e`,
                    borderLeft: `2px solid ${m.color}66`,
                    borderRadius: "0 6px 6px 0",
                    padding: "10px 14px",
                    fontSize: 13,
                    lineHeight: 1.6,
                    color: "#e2e8f0",
                  }}>
                    {speech.text}
                  </div>
                </div>
              );
            })}

            {/* Typing indicator */}
            {currentSpeaker && phase === "debate" && (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{
                  width: 28, height: 28, borderRadius: "50%",
                  background: `${getMember(currentSpeaker)?.color}22`,
                  border: `1px solid ${getMember(currentSpeaker)?.color}`,
                  display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14,
                }}>
                  {getMember(currentSpeaker)?.avatar}
                </div>
                <div style={{
                  background: `${getMember(currentSpeaker)?.color}0e`,
                  borderLeft: `2px solid ${getMember(currentSpeaker)?.color}66`,
                  borderRadius: "0 6px 6px 0",
                  padding: "10px 14px",
                  display: "flex", gap: 4, alignItems: "center",
                }}>
                  {[0, 1, 2].map(i => (
                    <div key={i} style={{
                      width: 6, height: 6, borderRadius: "50%",
                      background: getMember(currentSpeaker)?.color,
                      animation: `bounce 0.8s ease ${i * 0.15}s infinite alternate`,
                    }} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* VOTE RESULTS */}
        {phase === "voting" && Object.keys(votes).length > 0 && (
          <div style={{
            background: "#161b22",
            border: "1px solid #30363d",
            borderRadius: 12,
            padding: 16,
            marginBottom: 16,
          }}>
            <div style={{ fontSize: 11, color: "#f85149", letterSpacing: 3, marginBottom: 12 }}>◈ VOTE TALLY</div>
            {Object.entries(votes).map(([id, v]) => {
              const m = getMember(id);
              return (
                <div key={id} style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  marginBottom: 10,
                }}>
                  <div style={{
                    minWidth: 60,
                    padding: "2px 8px",
                    borderRadius: 4,
                    fontSize: 11,
                    fontWeight: 700,
                    textAlign: "center",
                    background: v.vote === "찬성" ? "#1a4d2e" : v.vote === "반대" ? "#4d1a1a" : "#1c1c1c",
                    color: v.vote === "찬성" ? "#3fb950" : v.vote === "반대" ? "#f85149" : "#8b949e",
                  }}>
                    {v.vote}
                  </div>
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: 11, color: m.color, fontWeight: 700 }}>{m.name}: </span>
                    <span style={{ fontSize: 11, color: "#8b949e" }}>{v.reason}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* FINAL RESULT */}
        {phase === "result" && result && (
          <div style={{
            background: passed ? "#0d2818" : "#280d0d",
            border: `2px solid ${passed ? "#3fb950" : "#f85149"}`,
            borderRadius: 12,
            padding: 24,
            textAlign: "center",
            marginBottom: 16,
          }}>
            <div style={{ fontSize: 36, marginBottom: 8 }}>{passed ? "✅" : "❌"}</div>
            <div style={{
              fontSize: 22,
              fontWeight: 700,
              color: passed ? "#3fb950" : "#f85149",
              letterSpacing: 2,
              marginBottom: 4,
            }}>
              {passed ? "가결 · PASSED" : "부결 · FAILED"}
            </div>
            <div style={{ display: "flex", justifyContent: "center", gap: 24, marginTop: 16 }}>
              {[["찬성", "#3fb950", result["찬성"]], ["반대", "#f85149", result["반대"]], ["기권", "#8b949e", result["기권"]]].map(([label, color, count]) => (
                <div key={label} style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 28, fontWeight: 700, color }}>{count}</div>
                  <div style={{ fontSize: 10, color: "#8b949e", letterSpacing: 2 }}>{label}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* VOTE DETAILS in result */}
        {phase === "result" && Object.keys(votes).length > 0 && (
          <div style={{
            background: "#161b22",
            border: "1px solid #30363d",
            borderRadius: 12,
            padding: 16,
            marginBottom: 16,
          }}>
            <div style={{ fontSize: 11, color: "#8b949e", letterSpacing: 3, marginBottom: 12 }}>◈ 의원 투표 결과</div>
            {Object.entries(votes).map(([id, v]) => {
              const m = getMember(id);
              return (
                <div key={id} style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  marginBottom: 10,
                  padding: "8px 0",
                  borderBottom: "1px solid #21262d",
                }}>
                  <div style={{ fontSize: 18 }}>{m.avatar}</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                      <span style={{ fontSize: 12, color: m.color, fontWeight: 700 }}>{m.name}</span>
                      <span style={{
                        fontSize: 10,
                        padding: "1px 6px",
                        borderRadius: 3,
                        background: v.vote === "찬성" ? "#1a4d2e" : v.vote === "반대" ? "#4d1a1a" : "#1c1c1c",
                        color: v.vote === "찬성" ? "#3fb950" : v.vote === "반대" ? "#f85149" : "#8b949e",
                        fontWeight: 700,
                      }}>{v.vote}</span>
                    </div>
                    <div style={{ fontSize: 11, color: "#8b949e", lineHeight: 1.5 }}>{v.reason}</div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* ACTION BUTTONS */}
        {phase === "debate" && debateComplete && !error && (
          <button onClick={runVoting} style={{
            width: "100%",
            padding: "14px 0",
            background: "linear-gradient(90deg, #c0392b, #e74c3c)",
            border: "none",
            borderRadius: 8,
            color: "#fff",
            fontSize: 14,
            fontWeight: 700,
            letterSpacing: 2,
            cursor: "pointer",
          }}>
            ⚖ 표결 시작 · PROCEED TO VOTE
          </button>
        )}

        {phase === "result" && (
          <button onClick={reset} style={{
            width: "100%",
            padding: "14px 0",
            background: "#21262d",
            border: "1px solid #30363d",
            borderRadius: 8,
            color: "#8b949e",
            fontSize: 14,
            fontWeight: 700,
            letterSpacing: 2,
            cursor: "pointer",
          }}>
            ◈ 새 의안 · NEW ISSUE
          </button>
        )}

        {error && (
          <div style={{
            background: "#4d1a1a",
            border: "1px solid #f85149",
            borderRadius: 8,
            padding: 12,
            color: "#f85149",
            fontSize: 12,
            textAlign: "center",
          }}>
            {error}
          </div>
        )}
      </div>

      <style>{`
        @keyframes pulse { from { opacity: 0.3; } to { opacity: 1; } }
        @keyframes bounce { from { transform: translateY(0); } to { transform: translateY(-4px); } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        textarea:focus { border-color: #388bfd !important; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0d1117; }
        ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 2px; }
      `}</style>
    </div>
  );
}
