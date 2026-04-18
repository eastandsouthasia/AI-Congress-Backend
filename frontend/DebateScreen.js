/**
 * DebateScreen - 백엔드 WebSocket 연동 버전
 *
 * 앱은 화면 표시 + TTS만 담당
 * 모든 AI 호출은 Railway 백엔드 서버가 처리
 */
import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, ScrollView, Text, StyleSheet,
  TouchableOpacity, Alert,
} from 'react-native';
import * as Speech from 'expo-speech';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { MEMBERS, COLORS } from '../constants/members';

// ─── 환경 설정 ───────────────────────────────
// Railway 배포 후 실제 주소로 교체하세요
const BACKEND_WS_URL = process.env.EXPO_PUBLIC_BACKEND_WS_URL || "ws://localhost:8000/debate";

// ─── 유틸리티 ────────────────────────────────
const parseSegments = (text) => {
  const lines = text.split('\n');
  const segments = [];
  let graphicBuffer = [], inGraphic = false;

  lines.forEach(line => {
    if (line.startsWith('[GRAPHIC]')) {
      inGraphic = true; graphicBuffer = [];
    } else if (inGraphic) {
      if (line.trim() === '' && graphicBuffer.length > 0) {
        segments.push({ type: 'graphic', content: graphicBuffer.join('\n') });
        inGraphic = false; graphicBuffer = [];
      } else { graphicBuffer.push(line); }
    } else if (line.startsWith('[DATA]')) {
      if (inGraphic) { segments.push({ type: 'graphic', content: graphicBuffer.join('\n') }); inGraphic = false; }
      segments.push({ type: 'data', content: line.replace('[DATA]', '').trim() });
    } else {
      segments.push({ type: 'text', content: line });
    }
  });
  if (inGraphic && graphicBuffer.length > 0)
    segments.push({ type: 'graphic', content: graphicBuffer.join('\n') });
  return segments.filter(s => s.content.trim() !== '');
};

const speakAndWaitSafe = (text, options) => new Promise((resolve) => {
  if (!text || !text.trim()) { resolve(); return; }
  let done = false;
  const finish = () => { if (!done) { done = true; resolve(); } };
  const timeout = setTimeout(finish, 60000);
  Speech.speak(text, {
    ...options,
    onDone: () => { clearTimeout(timeout); finish(); },
    onStopped: () => { clearTimeout(timeout); finish(); },
    onError: () => { clearTimeout(timeout); finish(); },
  });
});

const formatDebateLog = (issue, history) => {
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
    return `[${i + 1}] ${log.displayName}${tag} (${log.engineInfo || ''})\n${log.text}\n`;
  }).join('\n');
  return header + body + `\n------------------------------------------\nCopyright © 2025 AI Congress Simulation.\n==========================================`;
};

// ─── 메인 컴포넌트 ───────────────────────────
const DebateScreen = ({ issue, duration = 40, onFinish }) => {
  const [history, setHistory]       = useState([]);
  const [status, setStatus]         = useState("서버 연결 중...");
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [isFinished, setIsFinished] = useState(false);
  const [isSaving, setIsSaving]     = useState(false);

  const scrollRef     = useRef(null);
  const historyRef    = useRef([]);
  const ttsEnabledRef = useRef(true);
  const wsRef         = useRef(null);
  const ttsQueue      = useRef([]);      // TTS 순차 처리 큐
  const ttsRunning    = useRef(false);

  // ─── TTS 큐 처리 ───
  // 발언이 빠르게 오더라도 순서대로 재생
  const processTtsQueue = useCallback(async () => {
    if (ttsRunning.current) return;
    ttsRunning.current = true;
    while (ttsQueue.current.length > 0) {
      if (!ttsEnabledRef.current) { ttsQueue.current = []; break; }
      const { text, memberId } = ttsQueue.current.shift();
      const clean = text.replace(/\[REFUTE\]|\[ADMIT\]|\[DATA\]|\[GRAPHIC\]/g, "").trim();
      if (!clean) continue;

      let pitch = 1.0, rate = 0.88, volume = 1.0, voice = null;
      switch (memberId) {
        case "gemini":     pitch=1.08; rate=0.93; break;
        case "chatgpt":    pitch=0.96; rate=0.84; volume=0.98; break;
        case "perplexity": pitch=1.12; rate=1.02; break;
        case "grok":       pitch=0.85; rate=0.89; break;
        case "claude":     pitch=0.91; rate=0.81; volume=0.97; break;
        case "manus":      pitch=1.03; rate=0.96; break;
        case "deepseek":   pitch=1.15; rate=1.05; volume=0.95; break;
        case "glm5":       pitch=1.14; rate=0.94; break;
        case "llama4":     pitch=0.82; rate=0.87; break;
        case "kimi":       pitch=0.97; rate=0.79; volume=0.93; break;
      }
      try {
        const available = await Speech.getAvailableVoicesAsync();
        const korean = available.filter(v => v.language?.startsWith('ko') || v.identifier?.toLowerCase().includes('kr'));
        if (korean.length > 0) {
          const idx = memberId.split('').reduce((a,c)=>a+c.charCodeAt(0),0) % korean.length;
          voice = korean[idx].identifier;
        }
      } catch {}
      await speakAndWaitSafe(clean, { language:'ko-KR', pitch, rate, volume, voice });
    }
    ttsRunning.current = false;
  }, []);

  // ─── 발언 추가 ───
  const addLog = useCallback((data) => {
    const entry = {
      id:          Date.now() + Math.random(),
      memberId:    data.memberId,
      displayName: data.displayName,
      text:        data.text,
      type:        data.speechType || "NORMAL",
      engineInfo:  data.engineInfo || "",
      color:       data.color,
      avatar:      data.avatar,
    };
    setHistory(prev => {
      const next = [...prev, entry];
      historyRef.current = next;
      return next;
    });
    setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 100);

    // TTS 큐에 추가
    ttsQueue.current.push({ text: data.text, memberId: data.memberId });
    processTtsQueue();
  }, [processTtsQueue]);

  // ─── WebSocket 연결 ───
  useEffect(() => {
    const ws = new WebSocket(BACKEND_WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("서버 연결됨. 토론 시작 중...");
      ws.send(JSON.stringify({ issue, duration }));
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);

        switch (msg.type) {
          case "status":
            setStatus(msg.message);
            break;

          case "protocol":
            setStatus(`${msg.format} 형식으로 토론 시작 (의장: ${msg.chairName})`);
            break;

          case "speech":
            addLog(msg);
            break;

          case "result":
            // 결과를 VotingScreen으로 전달
            onFinish({
              type:    msg.resultType,
              content: msg.content,
              history: [...historyRef.current],
            });
            setIsFinished(true);
            setStatus("✅ 토론 종료 — 💾 기록을 저장하세요");
            break;

          case "done":
            setIsFinished(true);
            break;

          case "error":
            Alert.alert("서버 오류", msg.message);
            setStatus("⚠️ 오류 발생");
            setIsFinished(true);
            break;
        }
      } catch (e) {
        console.error("메시지 파싱 오류:", e);
      }
    };

    ws.onerror = (e) => {
      console.error("WebSocket 오류:", e);
      setStatus("⚠️ 서버 연결 오류");
      Alert.alert(
        "연결 실패",
        "백엔드 서버에 연결할 수 없습니다.\n서버 주소를 확인해주세요."
      );
      setIsFinished(true);
    };

    ws.onclose = () => {
      console.log("WebSocket 연결 종료");
    };

    return () => {
      Speech.stop();
      ws.close();
    };
  }, []);

  // ─── 저장/공유 ───
  const downloadDebateLog = async () => {
    if (isSaving) return;
    setIsSaving(true);
    try {
      const current = historyRef.current;
      if (!current || current.length === 0) {
        Alert.alert("알림", "저장할 토론 기록이 없습니다.");
        return;
      }
      const logText = formatDebateLog(issue, current);
      const fileName = `AI_Congress_${Date.now()}.txt`;
      const baseDir = FileSystem.documentDirectory || FileSystem.cacheDirectory;
      if (!baseDir) { Alert.alert("오류", "파일 저장 경로를 찾을 수 없습니다."); return; }

      const fileUri = baseDir + fileName;
      await FileSystem.writeAsStringAsync(fileUri, logText, { encoding: FileSystem.EncodingType.UTF8 });

      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(fileUri, { mimeType:'text/plain', dialogTitle:'AI 의회 토론 기록 공유', UTI:'public.plain-text' });
      } else {
        Alert.alert("저장 완료", `경로: ${fileUri}`);
      }
    } catch (error) {
      Alert.alert("저장 실패", `오류: ${error.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  // ─── 렌더링 ───
  return (
    <View style={styles.container}>
      <View style={styles.statusRow}>
        <Text style={styles.status} numberOfLines={2}>{status}</Text>
        <Text style={styles.durationBadge}>⏱ {duration}분</Text>
        {isFinished && (
          <TouchableOpacity
            style={[styles.saveBtn, isSaving && styles.saveBtnDisabled]}
            onPress={downloadDebateLog} disabled={isSaving}
          >
            <Text style={styles.saveBtnText}>{isSaving ? "저장 중..." : "💾 저장"}</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity
          style={[styles.ttsBtn, !ttsEnabled && styles.ttsBtnOff]}
          onPress={() => {
            Speech.stop();
            const next = !ttsEnabled;
            setTtsEnabled(next);
            ttsEnabledRef.current = next;
            if (!next) ttsQueue.current = [];
          }}
        >
          <Text style={styles.ttsBtnText}>{ttsEnabled ? "🔊" : "🔇"}</Text>
        </TouchableOpacity>
      </View>

      <ScrollView ref={scrollRef} style={styles.scroll}>
        {history.map((h, i) => {
          const member = MEMBERS.find(m => m.id === h.memberId);
          const color  = h.color || member?.color || COLORS.border;
          return (
            <View key={h.id || i} style={[
              styles.card,
              { borderLeftColor: color },
              h.type === "REFUTE" && styles.cardRefute,
              h.type === "ADMIT"  && styles.cardAdmit,
            ]}>
              <View style={styles.nameRow}>
                <Text style={[styles.name, { color }]}>
                  {h.avatar || member?.avatar || "💬"} {h.displayName}
                </Text>
                {h.engineInfo ? (
                  <Text style={styles.engineBadge}>{h.engineInfo}</Text>
                ) : null}
                {h.type === "REFUTE" && <Text style={styles.refuteBadge}>⚔ 반박</Text>}
                {h.type === "ADMIT"  && <Text style={styles.admitBadge}>✅ 수용</Text>}
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
                return <Text key={si} style={styles.text}>{seg.content}</Text>;
              })}
            </View>
          );
        })}
        <View style={{ height: 30 }} />
      </ScrollView>
    </View>
  );
};

const styles = StyleSheet.create({
  container:    { flex:1, backgroundColor:COLORS.background, paddingTop:50, paddingHorizontal:16 },
  statusRow:    { flexDirection:"row", alignItems:"center", marginBottom:12, gap:6, flexWrap:"wrap" },
  status:       { flex:1, color:COLORS.accent, fontWeight:"bold", fontSize:12, minWidth:100 },
  durationBadge:{ color:COLORS.gold, fontSize:11, fontWeight:"700", backgroundColor:"#1a1500", borderRadius:6, paddingHorizontal:7, paddingVertical:4, borderWidth:1, borderColor:COLORS.gold+"55" },
  saveBtn:      { backgroundColor:COLORS.success, borderRadius:8, paddingHorizontal:10, paddingVertical:6 },
  saveBtnDisabled:{ backgroundColor:COLORS.textMuted },
  saveBtnText:  { color:'#fff', fontSize:11, fontWeight:"bold" },
  ttsBtn:       { backgroundColor:COLORS.surface2, borderRadius:8, paddingHorizontal:10, paddingVertical:6, borderWidth:1, borderColor:COLORS.border2 },
  ttsBtnOff:    { borderColor:COLORS.error },
  ttsBtnText:   { fontSize:14 },
  scroll:       { flex:1 },
  card:         { backgroundColor:COLORS.card, padding:14, marginBottom:12, borderRadius:10, borderLeftWidth:4 },
  cardRefute:   { backgroundColor:"#1e1010" },
  cardAdmit:    { backgroundColor:"#0f1e0f" },
  nameRow:      { flexDirection:"row", alignItems:"center", marginBottom:6, flexWrap:"wrap", gap:6 },
  name:         { fontSize:11, fontWeight:"bold" },
  engineBadge:  { fontSize:9, color:COLORS.textMuted, backgroundColor:COLORS.surface2, borderRadius:4, paddingHorizontal:5, paddingVertical:2 },
  refuteBadge:  { fontSize:10, color:COLORS.error, fontWeight:"700" },
  admitBadge:   { fontSize:10, color:COLORS.success, fontWeight:"700" },
  text:         { color:COLORS.text, lineHeight:22, fontSize:14 },
  dataBox:      { flexDirection:'row', alignItems:'flex-start', backgroundColor:'#0d2137', borderRadius:6, padding:8, marginVertical:4, borderLeftWidth:3, borderLeftColor:COLORS.accent },
  dataIcon:     { fontSize:13, marginRight:6 },
  dataText:     { flex:1, color:COLORS.accent, fontSize:12, fontFamily:'monospace' },
  graphicBox:   { backgroundColor:'#0a1a0a', borderRadius:6, padding:10, marginVertical:4, borderWidth:1, borderColor:COLORS.success },
  graphicText:  { color:'#39d353', fontSize:12, fontFamily:'monospace' },
});

export default DebateScreen;
