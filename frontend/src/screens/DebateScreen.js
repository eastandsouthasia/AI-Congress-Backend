/**
 * DebateScreen - TTS와 발언을 완벽하게 동기화한 버전
 * ✅ activeMembers prop 추가 → WebSocket으로 백엔드 전달
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, ScrollView, Text, StyleSheet,
  TouchableOpacity, Alert,
} from 'react-native';
import * as Speech from 'expo-speech';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { MEMBERS, COLORS } from '../constants/members';

const BACKEND_WS_URL =
  process.env.EXPO_PUBLIC_BACKEND_WS_URL ||
  "wss://ai-congress.up.railway.app/debate";

// ─── 유틸리티 ────────────────────────────────
const parseSegments = (text) => {
  const lines = text.split('\n');
  const segments = [];
  let buffer = [], inBlock = null; // inBlock: 'graphic' | 'table' | null

  const flushBlock = () => {
    if (inBlock && buffer.length > 0) {
      segments.push({ type: inBlock, content: buffer.join('\n') });
    }
    buffer = []; inBlock = null;
  };

  lines.forEach(line => {
    if (line.startsWith('[GRAPHIC]')) {
      flushBlock();
      inBlock = 'graphic'; buffer = [];
    } else if (line.startsWith('[TABLE]')) {
      flushBlock();
      inBlock = 'table'; buffer = [];
    } else if (inBlock) {
      // 빈 줄이면 블록 종료
      if (line.trim() === '' && buffer.length > 0) {
        flushBlock();
      } else {
        buffer.push(line);
      }
    } else if (line.startsWith('[DATA]')) {
      segments.push({ type: 'data', content: line.replace('[DATA]', '').trim() });
    } else {
      segments.push({ type: 'text', content: line });
    }
  });

  flushBlock(); // 파일 끝에 블록이 열려 있으면 닫기
  return segments.filter(s => s.content.trim() !== '');
};

// TTS 완료 대기
// 글자당 약 70ms (한국어 평균 낭독 속도 기준), 최소 1.5초, 최대 12초
// onDone이 늦거나 안 오는 경우를 대비한 안전 타임아웃
const speakAndWaitSafe = (text, options) => new Promise((resolve) => {
  if (!text || !text.trim()) { resolve(); return; }
  let done = false;
  const rate = options?.rate || 0.88;
  const estimatedMs = Math.min(12000, Math.max(1500, (text.length * 70) / rate));
  const finish = () => { if (!done) { done = true; resolve(); } };
  const timeout = setTimeout(finish, estimatedMs);
  try {
    Speech.speak(text, {
      ...options,
      onDone:    () => { clearTimeout(timeout); finish(); },
      onStopped: () => { clearTimeout(timeout); finish(); },
      onError:   () => { clearTimeout(timeout); finish(); },
    });
  } catch (e) {
    // Android Activity가 종료된 경우 (ExpoKeepAwake.activate 거부 등)
    console.warn("[TTS] Speech.speak 실패 (activity 종료):", e?.message);
    clearTimeout(timeout);
    finish();
  }
});

// 글자 수 기반 TTS 낭독 예상 시간 (ms) — 텍스트 타이핑 속도 맞추기용
// 한국어 평균: 글자당 약 70ms / rate
const estimateTTSDuration = (text, rate = 0.88) =>
  Math.min(12000, Math.max(1500, ((text || "").length * 70) / rate));

// 회의록 포맷
const formatDebateLog = (issue, history, voteResult = null) => {
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

  let resultSection = "";
  if (voteResult) {
    resultSection = `\n------------------------------------------\n최종 의결:\n`;
    if (voteResult.type === "VOTE") {
      const pro  = voteResult.content?.filter(v => v.text?.includes("찬성")).length || 0;
      const con  = voteResult.content?.filter(v => v.text?.includes("반대")).length || 0;
      const abs  = (voteResult.content?.length || 0) - pro - con;
      resultSection += `찬성 ${pro} / 반대 ${con} / 기권 ${abs}\n결과: ${pro > con ? "✅ 가결" : "❌ 부결"}\n`;
      voteResult.content?.forEach(v => {
        resultSection += `${v.memberId}: ${v.text}\n`;
      });
    } else {
      resultSection += `공동 결의안:\n${voteResult.content}`;
    }
    resultSection += `\n`;
  }
  return header + body + resultSection +
    `\n------------------------------------------\n본 문서는 AI 의결 시스템에 의해 작성되었습니다.\n==========================================`;
};

// AsyncStorage 저장
const saveToStorage = async (issue, history, voteResult) => {
  try {
    const existing = await AsyncStorage.getItem('debate_history');
    const list = existing ? JSON.parse(existing) : [];
    const newEntry = {
      id: Date.now(),
      date: new Date().toLocaleString('ko-KR'),
      issue,
      content: formatDebateLog(issue, history, voteResult),
      result: voteResult?.type === "VOTE"
        ? (voteResult.content?.filter(v => v.text?.includes("찬성")).length >
           voteResult.content?.filter(v => v.text?.includes("반대")).length ? "가결" : "부결")
        : "결의안",
    };
    await AsyncStorage.setItem('debate_history', JSON.stringify([newEntry, ...list].slice(0, 50)));
  } catch (e) { console.error("저장 실패:", e); }
};

// TTS 음성 설정 (한국어 음성 목록 캐싱 — 매 발언마다 재조회 방지)
let _cachedKoreanVoices = null;
const getVoiceSettings = async (memberId) => {
  let pitch = 1.0, rate = 0.88, volume = 1.0, voice = null;
  switch (memberId) {
    case "gemini":   pitch=1.08; rate=0.93; break;
    case "llama4":   pitch=0.82; rate=0.87; break;
    case "mistral":  pitch=1.12; rate=1.02; break;
    case "gptoss":   pitch=0.96; rate=0.84; volume=0.98; break;
    case "nemotron": pitch=0.91; rate=0.81; volume=0.97; break;
  }
  try {
    if (_cachedKoreanVoices === null) {
      const available = await Speech.getAvailableVoicesAsync();
      _cachedKoreanVoices = available.filter(
        v => v.language?.startsWith('ko') || v.identifier?.toLowerCase().includes('kr')
      );
    }
    if (_cachedKoreanVoices.length > 0) {
      const idx = memberId.split('').reduce((a,c) => a+c.charCodeAt(0), 0) % _cachedKoreanVoices.length;
      voice = _cachedKoreanVoices[idx].identifier;
    }
  } catch {}
  return { pitch, rate, volume, voice };
};

// ─── 메인 컴포넌트 ───────────────────────────
const DebateScreen = ({
  issue,
  duration = 15,
  debateFormat = "릴레이",
  conclusionType = "VOTE",
  activeMembers,          // ✅ 추가: 참여 의원 ID 배열
  onFinish,
}) => {
  const [history, setHistory]     = useState([]);
  const [status, setStatus]       = useState("서버 연결 중...");
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [isFinished, setIsFinished] = useState(false);
  const [isSaving, setIsSaving]   = useState(false);
  const [roundInfo, setRoundInfo] = useState("");

  const scrollRef     = useRef(null);
  const historyRef    = useRef([]);
  const ttsEnabledRef = useRef(true);
  const wsRef         = useRef(null);
  const voteResultRef = useRef(null);
  const speechQueue   = useRef([]);   // 발언 표시 큐
  const speechBusy    = useRef(false);// 발언 표시 중 여부
  const isMountedRef  = useRef(true); // 언마운트 후 TTS 호출 방지

  // 컴포넌트 언마운트 시 플래그 해제
  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

  // ─── 발언 표시 큐 처리: 한 번에 한 발언씩 순서대로 표시 ───
  const processSpeechQueue = useCallback(async () => {
    if (speechBusy.current) return;
    speechBusy.current = true;

    while (speechQueue.current.length > 0) {
      if (!isMountedRef.current) break; // 언마운트 후 큐 처리 중단
      const data = speechQueue.current.shift();
      const baseId   = Date.now() + Math.random();
      const fullText = data.text || "";
      const lines    = fullText.split('\n').filter(l => l.trim() !== '');

      // 카드 추가 (텍스트 빈 상태로)
      setHistory(prev => {
        const next = [...prev, {
          id:          baseId,
          memberId:    data.memberId    || "",
          displayName: data.displayName || "?",
          text:        "",
          type:        data.speechType  || "NORMAL",
          engineInfo:  data.engineInfo  || "",
          color:       data.color       || COLORS.border,
          avatar:      data.avatar      || "💬",
          timestamp:   data.timestamp   || new Date().toLocaleTimeString('ko-KR', {hour:'2-digit',minute:'2-digit',second:'2-digit'}),
        }];
        historyRef.current = next;
        return next;
      });
      setStatus(`🎙 ${data.displayName} 발언 중...`);
      setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 60);

      // ── TTS 텍스트 미리 정제 (화면 표시와 동시 시작하기 위해) ──
      const hanjaMap = {
        '愼重|慎重': '신중', '重要': '중요', '重大': '중대',
        '必要': '필요', '可能': '가능', '不可能': '불가능',
        '現在': '현재', '現實': '현실', '未來': '미래',
        '社會': '사회', '國家': '국가', '政府': '정부',
        '經濟': '경제', '政策': '정책', '制度': '제도',
        '問題': '문제', '解決': '해결', '方法': '방법',
        '結果': '결과', '原因': '원인', '根據': '근거',
        '主張': '주장', '反對': '반대', '贊成': '찬성',
        '分析': '분석', '判斷': '판단', '決定': '결정',
        '效率': '효율', '效果': '효과', '影響': '영향',
        '基準': '기준', '原則': '원칙', '價値': '가치',
        '自由': '자유', '平等': '평등', '正義': '정의',
        '安全': '안전', '危險': '위험', '保護': '보호',
        '發展': '발전', '成長': '성장', '改善': '개선',
        '統計': '통계', '資料': '자료', '報告': '보고',
        '議員': '의원', '議長': '의장', '本議員': '본의원',
        '贊反': '찬반', '論議': '논의', '討論': '토론',
        '强調': '강조', '指摘': '지적', '提示': '제시',
        '具體': '구체', '抽象': '추상', '複雜': '복잡',
        '簡單': '간단', '明確': '명확', '不明確': '불명확',
      };
      let ttsClean = "";
      if (ttsEnabledRef.current && fullText) {
        let conv = fullText;
        Object.entries(hanjaMap).forEach(([k, v]) => {
          conv = conv.replace(new RegExp(k, 'g'), v);
        });
        ttsClean = conv
          .replace(/\[REFUTE\]|\[ADMIT\]|\[DATA\]|\[GRAPHIC\]|\[TABLE\]/g, "")
          .replace(/Gemini/gi, "제미나이")
          .replace(/Llama4?/gi, "라마")
          .replace(/Mistral/gi, "미스트랄")
          .replace(/GPT.?OSS/gi, "지피티")
          .replace(/Nemotron/gi, "엔비디아")
          .replace(/≥/g, "이상").replace(/≤/g, "이하")
          .replace(/>/g, "초과").replace(/</g, "미만")
          .replace(/={2,}/g, "동일")
          .replace(/\*{2}/g, "").replace(/\*/g, "")
          .replace(/[\u4E00-\u9FFF\u3400-\u4DBF]+/g, "")
          .replace(/\uFE0F/g, "")
          .replace(/(?:^|\n)\s*-\s*/g, "\n")
          .replace(/\|[-:| ]+\|/g, "")
          .replace(/\|/g, " ")
          .trim();
      }

      // ── TTS와 텍스트 타이핑 동시 시작 ──
      const voiceSettings = await getVoiceSettings(data.memberId);
      const { pitch, rate, volume, voice } = voiceSettings;
      const ttsDurationMs = estimateTTSDuration(ttsClean || fullText, rate);

      // TTS를 await 없이 fire → Promise만 보관
      let ttsPromise = Promise.resolve();
      if (ttsEnabledRef.current && ttsClean) {
        ttsPromise = speakAndWaitSafe(ttsClean, { language: 'ko-KR', pitch, rate, volume, voice });
      }

      // 텍스트 타이핑: TTS 낭독 시간 비율에 맞춰 줄 단위로 표시
      if (lines.length > 0) {
        const totalChars = lines.reduce((s, l) => s + l.length, 0) || 1;
        let accumulated = "";
        for (let i = 0; i < lines.length; i++) {
          accumulated += (i === 0 ? "" : "\n") + lines[i];
          const snap = accumulated;
          setHistory(prev => {
            const next = prev.map(h => h.id === baseId ? { ...h, text: snap } : h);
            historyRef.current = next;
            return next;
          });
          // 다음 줄까지 대기: 이 줄의 글자 비율 × 총 낭독시간 (마지막 줄 제외)
          if (i < lines.length - 1) {
            const lineRatio = lines[i].length / totalChars;
            const lineDelay = Math.max(150, ttsDurationMs * lineRatio);
            await new Promise(r => setTimeout(r, lineDelay));
          }
        }
      } else {
        setHistory(prev => {
          const next = prev.map(h => h.id === baseId ? { ...h, text: fullText } : h);
          historyRef.current = next;
          return next;
        });
      }

      setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 50);

      // 텍스트가 먼저 끝난 경우 TTS 완료까지 대기 (다음 발언 차단)
      await ttsPromise;

      // TTS 꺼져 있으면 최소 간격 유지
      if (!ttsEnabledRef.current) {
        await new Promise(r => setTimeout(r, 600));
      }

      setStatus("다음 발언 준비 중...");

      // 발언 카드 간 여백
      await new Promise(r => setTimeout(r, 400));

      // 백엔드에 ACK 전송 — 다음 발언을 보내도 좋다는 신호
      try {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: "ready" }));
        }
      } catch (e) { console.warn("[ACK] 전송 실패:", e); }
    }

    speechBusy.current = false;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── 발언 추가: speechQueue에 넣고 순차 처리 ───
  const addLog = useCallback((data) => {
    speechQueue.current.push(data);
    processSpeechQueue();
  }, [processSpeechQueue]);

  // ─── WebSocket ───
  useEffect(() => {
    const ws = new WebSocket(BACKEND_WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("서버 연결됨. 토론 시작 중...");
      // ✅ activeMembers 포함하여 전송
      ws.send(JSON.stringify({
        issue,
        duration,
        debateFormat,
        conclusionType,
        activeMembers: activeMembers || [],
      }));
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);

        switch (msg.type) {
          case "status":
            setStatus(msg.message || "");
            break;
          case "protocol":
            setStatus(`${msg.format} 형식 · 의장: ${msg.chairName}`);
            break;
          case "round":
            setRoundInfo(msg.label || "");
            setStatus(msg.label || "");
            break;
          case "speech":
            addLog(msg);
            break;
          case "result":
            const voteResult = { type: msg.resultType, content: msg.content };
            voteResultRef.current = voteResult;
            // 남은 발언 큐 비우기 — 의결 후 발언이 이어지지 않도록
            speechQueue.current = [];
            Speech.stop();
            saveToStorage(issue, historyRef.current, voteResult);
            onFinish({
              type: msg.resultType,
              content: msg.content,
              history: [...historyRef.current],
            });
            setIsFinished(true);
            setStatus("✅ 토론 종료 — 기록이 보관함에 저장되었습니다");
            break;
          case "done":
            setIsFinished(true);
            break;
          case "error":
            Alert.alert("서버 오류", msg.message || "알 수 없는 오류");
            setStatus("⚠️ 오류 발생");
            setIsFinished(true);
            break;
        }
      } catch (e) {
        console.error("메시지 파싱 오류:", e);
      }
    };

    ws.onerror = () => {
      setStatus("⚠️ 서버 연결 실패");
      Alert.alert("연결 실패", `서버 주소를 확인하세요.\n${BACKEND_WS_URL}`);
      setIsFinished(true);
    };

    ws.onclose = () => console.log("[WS] 연결 종료");

    return () => {
      Speech.stop();
      speechQueue.current = [];
      if (ws.readyState === WebSocket.OPEN) ws.close();
    };
  }, [issue, duration, debateFormat, conclusionType, activeMembers, onFinish]);

  // ─── 파일 내보내기 ───
  const downloadDebateLog = async () => {
    if (isSaving) return;
    setIsSaving(true);
    try {
      const current = historyRef.current;
      if (!current || current.length === 0) {
        Alert.alert("알림", "저장할 토론 기록이 없습니다.");
        return;
      }
      const logText = formatDebateLog(issue, current, voteResultRef.current);
      const fileName = `AI_Congress_${Date.now()}.txt`;
      const baseDir = FileSystem.documentDirectory || FileSystem.cacheDirectory;
      const fileUri = baseDir + fileName;
      await FileSystem.writeAsStringAsync(fileUri, logText, { encoding: FileSystem.EncodingType.UTF8 });

      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(fileUri, { mimeType: 'text/plain', dialogTitle: 'AI 의회 토론 기록' });
      } else {
        Alert.alert("저장 완료", `경로: ${fileUri}`);
      }
    } catch (error) {
      Alert.alert("저장 실패", `오류: ${error.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <View style={styles.container}>
      {/* 상단 헤더 */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <Text style={styles.headerTitle}>🏛 AI 의회</Text>
          <Text style={styles.headerIssue} numberOfLines={1}>{issue}</Text>
        </View>
        <View style={styles.headerRight}>
          <View style={[styles.badge, debateFormat === "자유토론" && styles.badgePurple]}>
            <Text style={styles.badgeText}>
              {debateFormat === "릴레이" ? "🔄" : debateFormat === "집중토론" ? "⚡" : debateFormat === "전문가패널" ? "🎓" : "🌀"} {debateFormat}
            </Text>
          </View>
          <View style={styles.badge}>
            <Text style={styles.badgeText}>⏱ {duration}분</Text>
          </View>
          <TouchableOpacity
            style={[styles.iconBtn, !ttsEnabled && styles.iconBtnOff]}
            onPress={() => {
              Speech.stop();
              const next = !ttsEnabled;
              setTtsEnabled(next);
              ttsEnabledRef.current = next;
            }}
          >
            <Text style={styles.iconBtnText}>{ttsEnabled ? "🔊" : "🔇"}</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* 상태 바 */}
      <View style={styles.statusBar}>
        <View style={styles.statusDot} />
        <Text style={styles.statusText} numberOfLines={1}>{status}</Text>
        {isFinished && (
          <TouchableOpacity
            style={[styles.actionBtn, isSaving && styles.actionBtnDisabled]}
            onPress={downloadDebateLog}
            disabled={isSaving}
          >
            <Text style={styles.actionBtnText}>{isSaving ? "처리 중..." : "📤 내보내기"}</Text>
          </TouchableOpacity>
        )}
      </View>

      <ScrollView ref={scrollRef} style={styles.scroll} contentContainerStyle={styles.scrollContent}>
        {history.map((h, i) => {
          const member = MEMBERS.find(m => m.id === h.memberId);
          const color = h.color || member?.color || COLORS.border;
          const isRoundHeader = h.text?.startsWith("━━");

          if (isRoundHeader) {
            return (
              <View key={h.id || i} style={styles.roundHeader}>
                <View style={styles.roundHeaderLine} />
                <Text style={styles.roundHeaderText}>{h.text}</Text>
                <View style={styles.roundHeaderLine} />
              </View>
            );
          }

          const isChair = h.displayName?.startsWith("의장");

          return (
            <View key={h.id || i} style={[
              styles.card,
              { borderLeftColor: color },
              h.type === "REFUTE" && styles.cardRefute,
              h.type === "ADMIT"  && styles.cardAdmit,
              isChair && styles.cardChair,
            ]}>
              <View style={styles.cardHeader}>
                <View style={styles.avatarWrap}>
                  <Text style={styles.avatar}>{h.avatar || member?.avatar || "💬"}</Text>
                </View>
                <View style={styles.nameWrap}>
                  <Text style={[styles.name, { color }]}>{h.displayName}</Text>
                  {!!h.engineInfo && <Text style={styles.engineBadge}>{h.engineInfo}</Text>}
                </View>
                <View style={styles.metaRight}>
                  {h.type === "REFUTE" && <View style={styles.typeBadgeRefute}><Text style={styles.typeBadgeText}>⚔ 반박</Text></View>}
                  {h.type === "ADMIT"  && <View style={styles.typeBadgeAdmit}><Text style={styles.typeBadgeText}>✅ 수용</Text></View>}
                  {!!h.timestamp && <Text style={styles.timestamp}>{h.timestamp}</Text>}
                </View>
              </View>
              {parseSegments(h.text).map((seg, si) => {
                if (seg.type === 'data') return (
                  <View key={si} style={styles.dataBox}>
                    <Text style={styles.dataIcon}>📊</Text>
                    <Text style={styles.dataText}>{seg.content}</Text>
                  </View>
                );
                if (seg.type === 'graphic') return (
                  <View key={si} style={styles.graphicBox}>
                    <Text style={styles.graphicText}>{seg.content}</Text>
                  </View>
                );
                if (seg.type === 'table') return (
                  <View key={si} style={styles.tableBox}>
                    <Text style={styles.tableText}>{seg.content}</Text>
                  </View>
                );
                return <Text key={si} style={[styles.text, isChair && styles.textChair]}>{seg.content}</Text>;
              })}
            </View>
          );
        })}
        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
  );
};

const GOLD = "#c9a84c";
const GOLD2 = "#e8cc7a";
const NAVY = "#080c14";
const PANEL = "#10151f";
const PANEL2 = "#161d2b";
const SLATE = "#1c2436";

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: NAVY },

  // ── 헤더 ──
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingTop: 52, paddingBottom: 12, paddingHorizontal: 16,
    backgroundColor: PANEL,
    borderBottomWidth: 1, borderBottomColor: GOLD + "33",
  },
  headerLeft: { flex: 1, marginRight: 10 },
  headerTitle: { color: GOLD2, fontSize: 14, fontWeight: "900", letterSpacing: 3 },
  headerIssue: { color: "#8899bb", fontSize: 11, marginTop: 2 },
  headerRight: { flexDirection: "row", alignItems: "center", gap: 6 },

  badge: { backgroundColor: PANEL2, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 4, borderWidth: 1, borderColor: GOLD + "33" },
  badgePurple: { borderColor: "#9b59b6" + "55", backgroundColor: "#1a0d2e" },
  badgeText: { color: GOLD, fontSize: 10, fontWeight: "700" },
  iconBtn: { backgroundColor: PANEL2, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: GOLD + "33" },
  iconBtnOff: { borderColor: "#e74c3c55" },
  iconBtnText: { fontSize: 14 },

  // ── 상태 바 ──
  statusBar: {
    flexDirection: "row", alignItems: "center",
    paddingHorizontal: 16, paddingVertical: 8,
    backgroundColor: PANEL2,
    borderBottomWidth: 1, borderBottomColor: "#ffffff0a",
    gap: 8,
  },
  statusDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: "#4fc3f7" },
  statusText: { flex: 1, color: "#7899cc", fontSize: 11, fontWeight: "600" },
  actionBtn: { backgroundColor: "#1a3a1a", borderRadius: 6, paddingHorizontal: 10, paddingVertical: 5, borderWidth: 1, borderColor: "#27ae6055" },
  actionBtnDisabled: { backgroundColor: SLATE, borderColor: "#333" },
  actionBtnText: { color: "#27ae60", fontSize: 10, fontWeight: "700" },

  scroll: { flex: 1 },
  scrollContent: { paddingHorizontal: 14, paddingTop: 12 },

  // ── 라운드 헤더 ──
  roundHeader: {
    flexDirection: "row", alignItems: "center",
    marginVertical: 14, gap: 10,
  },
  roundHeaderLine: { flex: 1, height: 1, backgroundColor: GOLD + "33" },
  roundHeaderText: { color: GOLD + "aa", fontSize: 10, fontWeight: "700", letterSpacing: 2 },

  // ── 발언 카드 ──
  card: {
    backgroundColor: PANEL,
    paddingHorizontal: 14, paddingVertical: 12,
    marginBottom: 10, borderRadius: 10,
    borderLeftWidth: 3,
    borderWidth: 1, borderColor: "#ffffff08",
  },
  cardRefute: { backgroundColor: "#160e0e", borderColor: "#e74c3c18" },
  cardAdmit:  { backgroundColor: "#0c160c", borderColor: "#27ae6018" },
  cardChair:  { backgroundColor: PANEL2, borderColor: GOLD + "18", borderLeftColor: GOLD + "66" },

  cardHeader: { flexDirection: "row", alignItems: "flex-start", marginBottom: 10, gap: 10 },
  avatarWrap: {
    width: 34, height: 34, borderRadius: 17,
    backgroundColor: SLATE, alignItems: "center", justifyContent: "center",
    borderWidth: 1, borderColor: "#ffffff14",
  },
  avatar: { fontSize: 16 },
  nameWrap: { flex: 1 },
  name: { fontSize: 12, fontWeight: "800", letterSpacing: 0.5 },
  engineBadge: { fontSize: 9, color: "#4a5572", marginTop: 2 },

  metaRight: { alignItems: "flex-end", gap: 4 },
  timestamp: { color: "#3a4560", fontSize: 9, fontWeight: "600", letterSpacing: 0.5 },

  typeBadgeRefute: { backgroundColor: "#2a0f0f", borderRadius: 4, paddingHorizontal: 7, paddingVertical: 2, borderWidth: 1, borderColor: "#e74c3c44" },
  typeBadgeAdmit:  { backgroundColor: "#0c1f0c", borderRadius: 4, paddingHorizontal: 7, paddingVertical: 2, borderWidth: 1, borderColor: "#27ae6044" },
  typeBadgeText: { fontSize: 9, fontWeight: "700", color: "#aaa" },

  text:      { color: "#c8d4e8", lineHeight: 22, fontSize: 13.5, letterSpacing: 0.2 },
  textChair: { color: "#a8b8cc", fontStyle: "italic" },

  dataBox: { flexDirection: 'row', alignItems: 'flex-start', backgroundColor: '#091828', borderRadius: 6, padding: 10, marginVertical: 5, borderLeftWidth: 3, borderLeftColor: "#4fc3f7" },
  dataIcon: { fontSize: 13, marginRight: 7 },
  dataText: { flex: 1, color: "#4fc3f7", fontSize: 12, fontFamily: 'monospace' },
  graphicBox: { backgroundColor: '#091409', borderRadius: 6, padding: 10, marginVertical: 5, borderWidth: 1, borderColor: "#27ae6044" },
  graphicText: { color: '#39d353', fontSize: 12, fontFamily: 'monospace' },
  tableBox: { backgroundColor: '#0d1426', borderRadius: 6, padding: 10, marginVertical: 5, borderWidth: 1, borderColor: GOLD + "33" },
  tableText: { color: "#b8c8e0", fontSize: 11, fontFamily: 'monospace', lineHeight: 18 },
});

export default DebateScreen;