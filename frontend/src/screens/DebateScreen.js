/**
 * DebateScreen v2
 *
 * [v2 핵심 변경]
 * 1. TTS ↔ 텍스트 동기화 완전 제거
 *    - ACK 전송 코드 삭제 (ready 메시지, ackSeq 불필요)
 *    - speakAndWaitSafe / processSpeechQueue 의 TTS 대기 제거
 *    - 발언 텍스트는 수신 즉시 화면에 표시
 *
 * 2. 토론 진행 중 TTS 완전 비활성화
 *    - 토론이 끝나면 VotingScreen(또는 별도 재생 패널)에서 독립 재생
 *    - 🔊 버튼 → 토론 완료 후 회의록 음성 재생 토글로 용도 변경
 *
 * 3. 턴 기반 진행 표시
 *    - 헤더의 "⏱ N분" → "💬 N/M턴" 배지로 교체
 *    - 백엔드가 speech 메시지에 turnCount, maxTurns 포함해서 전송
 *
 * 4. 토론 완료 후 인라인 음성 재생 패널
 *    - 발언 목록을 순서대로 TTS 재생
 *    - 재생/일시정지/이전/다음 컨트롤
 *    - 각 의원 목소리(pitch/rate)를 그대로 적용
 *
 * 5. WS 전송: duration → maxTurns
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

// ─── 유틸리티 ─────────────────────────────────
// ─── 차트·표 파서 (CHART:bar/line/pie, TABLE:json 지원) ──
const parseSegments = (text) => {
  const lines = text.split('\n');
  const segments = [];

  lines.forEach(line => {
    const trimmed = line.trim();

    // [CHART:bar/line/pie]{...JSON...}
    const chartMatch = trimmed.match(/^\[CHART:(bar|line|pie)\](\{.+\})$/);
    if (chartMatch) {
      try {
        segments.push({ type: `chart_${chartMatch[1]}`, data: JSON.parse(chartMatch[2]) });
      } catch { segments.push({ type: 'text', content: line }); }
      return;
    }
    // [TABLE:json]{...JSON...}
    const tableMatch = trimmed.match(/^\[TABLE:json\](\{.+\})$/);
    if (tableMatch) {
      try {
        segments.push({ type: 'table_json', data: JSON.parse(tableMatch[1]) });
      } catch { segments.push({ type: 'text', content: line }); }
      return;
    }
    // 레거시 [GRAPHIC] / [TABLE] — 텍스트 폴백
    if (trimmed.startsWith('[GRAPHIC]')) {
      segments.push({ type: 'graphic', content: trimmed.replace('[GRAPHIC]', '').trim() });
      return;
    }
    if (trimmed.startsWith('[TABLE]')) {
      segments.push({ type: 'graphic', content: trimmed.replace('[TABLE]', '').trim() });
      return;
    }
    // [DATA] 인라인
    if (trimmed.startsWith('[DATA]')) {
      segments.push({ type: 'data', content: trimmed.replace('[DATA]', '').trim() });
      return;
    }
    segments.push({ type: 'text', content: line });
  });

  return segments.filter(s => s.content !== undefined ? s.content.trim() !== '' : true);
};

// ─── 차트 색상 팔레트 ──────────────────────────────────
const CHART_COLORS = ['#4fc3f7','#81c784','#ffb74d','#e57373','#ba68c8','#4dd0e1','#aed581','#ff8a65'];

// ─── 막대 차트 ────────────────────────────────────────
const BarChart = ({ data }) => {
  const { title, labels = [], values = [], unit = '', source = '' } = data;
  const max = Math.max(...values.map(Math.abs), 0.01);
  const GAP = 7, LABEL_W = 72;
  return (
    <View style={cStyles.wrapper}>
      {!!title && <Text style={cStyles.title}>{title}</Text>}
      {labels.map((label, i) => {
        const val = values[i] ?? 0;
        const pct = Math.round((Math.abs(val) / max) * 100);
        const color = CHART_COLORS[i % CHART_COLORS.length];
        return (
          <View key={i} style={[cStyles.barRow, { marginBottom: GAP }]}>
            <Text style={[cStyles.barLabel, { width: LABEL_W }]} numberOfLines={1}>{label}</Text>
            <View style={cStyles.barTrack}>
              <View style={[cStyles.barFill, { width: `${pct}%`, backgroundColor: color }]} />
            </View>
            <Text style={[cStyles.barVal, { color }]}>{val}{unit}</Text>
          </View>
        );
      })}
      {!!source && <Text style={cStyles.source}>출처: {source}</Text>}
    </View>
  );
};

// ─── 꺾은선 차트 (순수 RN) ────────────────────────────
const LineChart = ({ data }) => {
  const { title, labels = [], values = [], unit = '', source = '' } = data;
  if (values.length < 2) return <BarChart data={data} />;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const H = 80, PAD = 8;
  const pts = values.map((v, i) => ({
    left: `${(i / (values.length - 1)) * 100}%`,
    bottom: PAD + ((v - min) / range) * (H - PAD * 2),
    v, color: CHART_COLORS[i % CHART_COLORS.length],
  }));
  return (
    <View style={cStyles.wrapper}>
      {!!title && <Text style={cStyles.title}>{title}</Text>}
      <View style={{ height: H + 24, position: 'relative', marginBottom: 4 }}>
        {[0, 0.5, 1].map((t, i) => (
          <View key={i} style={{ position: 'absolute', left: 0, right: 0, bottom: PAD + t * (H - PAD * 2), height: 1, backgroundColor: '#ffffff0a' }} />
        ))}
        {pts.map((p, i) => (
          <View key={i} style={{ position: 'absolute', width: 8, height: 8, borderRadius: 4, backgroundColor: p.color, left: p.left, bottom: p.bottom - 4, marginLeft: -4 }} />
        ))}
        {pts.map((p, i) => (
          <Text key={`v${i}`} style={{ position: 'absolute', left: p.left, bottom: p.bottom + 6, fontSize: 9, fontWeight: '700', color: p.color }}>{p.v}{unit}</Text>
        ))}
        <View style={{ position: 'absolute', bottom: -20, left: 0, right: 0, flexDirection: 'row', justifyContent: 'space-between' }}>
          {labels.map((l, i) => <Text key={i} style={cStyles.axisLabel} numberOfLines={1}>{l}</Text>)}
        </View>
      </View>
      {!!source && <Text style={[cStyles.source, { marginTop: 18 }]}>출처: {source}</Text>}
    </View>
  );
};

// ─── 파이 차트 (비율 바 + 범례) ──────────────────────
const PieChart = ({ data }) => {
  const { title, labels = [], values = [], unit = '', source = '' } = data;
  const total = values.reduce((a, b) => a + b, 0) || 1;
  return (
    <View style={cStyles.wrapper}>
      {!!title && <Text style={cStyles.title}>{title}</Text>}
      <View style={{ flexDirection: 'row', height: 20, borderRadius: 6, overflow: 'hidden', marginBottom: 10 }}>
        {values.map((v, i) => <View key={i} style={{ flex: v / total, backgroundColor: CHART_COLORS[i % CHART_COLORS.length] }} />)}
      </View>
      <View style={{ gap: 5 }}>
        {labels.map((l, i) => {
          const pct = ((values[i] / total) * 100).toFixed(1);
          const color = CHART_COLORS[i % CHART_COLORS.length];
          return (
            <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View style={{ width: 12, height: 12, borderRadius: 2, backgroundColor: color }} />
              <Text style={cStyles.legendText}>{l}</Text>
              <Text style={{ fontSize: 11, fontWeight: '700', color }}>{pct}%</Text>
            </View>
          );
        })}
      </View>
      {!!source && <Text style={cStyles.source}>출처: {source}</Text>}
    </View>
  );
};

// ─── JSON 테이블 ──────────────────────────────────────
const DataTable = ({ data }) => {
  const { title, headers = [], rows = [] } = data;
  return (
    <View style={cStyles.tableWrapper}>
      {!!title && <Text style={cStyles.title}>{title}</Text>}
      <View style={cStyles.tableHead}>
        {headers.map((h, i) => <Text key={i} style={[cStyles.tableCell, cStyles.tableHeadCell, { flex: 1 }]} numberOfLines={1}>{h}</Text>)}
      </View>
      {rows.map((row, ri) => (
        <View key={ri} style={[cStyles.tableRow, ri % 2 === 0 && cStyles.tableRowAlt]}>
          {row.map((cell, ci) => <Text key={ci} style={[cStyles.tableCell, { flex: 1 }]} numberOfLines={2}>{cell}</Text>)}
        </View>
      ))}
    </View>
  );
};

// ─── 차트 공통 스타일 ─────────────────────────────────
const cStyles = StyleSheet.create({
  wrapper:      { backgroundColor: '#0a1020', borderRadius: 10, padding: 12, marginVertical: 6, borderWidth: 1, borderColor: '#4fc3f744' },
  title:        { color: '#e8cc7a', fontSize: 11, fontWeight: '800', letterSpacing: 0.5, marginBottom: 10 },
  barRow:       { flexDirection: 'row', alignItems: 'center', gap: 8 },
  barLabel:     { color: '#8899bb', fontSize: 10, textAlign: 'right' },
  barTrack:     { flex: 1, height: 18, backgroundColor: '#ffffff0a', borderRadius: 4, overflow: 'hidden' },
  barFill:      { height: 18, borderRadius: 4 },
  barVal:       { fontSize: 10, fontWeight: '700', minWidth: 40, textAlign: 'right' },
  axisLabel:    { color: '#5a6a88', fontSize: 9, textAlign: 'center', flex: 1 },
  legendText:   { color: '#8899bb', fontSize: 11, flex: 1 },
  source:       { color: '#3a4560', fontSize: 9, marginTop: 6, fontStyle: 'italic' },
  tableWrapper: { backgroundColor: '#0d1426', borderRadius: 10, padding: 10, marginVertical: 6, borderWidth: 1, borderColor: '#c9a84c33' },
  tableHead:    { flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: '#c9a84c44', paddingBottom: 6, marginBottom: 4 },
  tableHeadCell:{ color: '#e8cc7a', fontWeight: '800', fontSize: 10 },
  tableRow:     { flexDirection: 'row', paddingVertical: 4 },
  tableRowAlt:  { backgroundColor: '#ffffff04' },
  tableCell:    { color: '#b8c8e0', fontSize: 10, paddingHorizontal: 2 },
});

// 찬반 파싱 (VotingScreen과 동일 로직)
const parseVoteResult = (text = "") => {
  const t = text.trimStart();
  if (/^\[찬성\]/.test(t) || /^찬성[\s.,!]/.test(t) || t === "찬성") return "FOR";
  if (/^\[반대\]/.test(t) || /^반대[\s.,!]/.test(t) || t === "반대") return "AGAINST";
  if (/^\[기권\]/.test(t) || /^기권[\s.,!]/.test(t) || t === "기권") return "ABSTAIN";
  return "ABSTAIN";
};

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
    const isResolution = voteResult.type === "RESOLUTION";
    resultSection = `\n------------------------------------------\n${isResolution ? "공식 결의문" : "최종 의결"}:\n`;
    if (voteResult.type === "VOTE") {
      const pro = voteResult.content?.filter(v => parseVoteResult(v.text) === "FOR").length || 0;
      const con = voteResult.content?.filter(v => parseVoteResult(v.text) === "AGAINST").length || 0;
      const abs = (voteResult.content?.length || 0) - pro - con;
      resultSection += `찬성 ${pro} / 반대 ${con} / 기권 ${abs}\n결과: ${pro > con ? "✅ 가결" : "❌ 부결"}\n`;
      voteResult.content?.forEach(v => {
        resultSection += `${v.memberId}: ${v.text}\n`;
      });
    } else {
      resultSection += `${voteResult.content}`;
    }
    resultSection += `\n`;
  }
  return header + body + resultSection +
    `\n------------------------------------------\n본 문서는 AI 의결 시스템에 의해 작성되었습니다.\n==========================================`;
};

const saveToStorage = async (issue, history, voteResult) => {
  try {
    const existing = await AsyncStorage.getItem('debate_history');
    const list = existing ? JSON.parse(existing) : [];
    const proCount = voteResult?.type === "VOTE"
      ? (voteResult.content?.filter(v => parseVoteResult(v.text) === "FOR").length || 0) : 0;
    const conCount = voteResult?.type === "VOTE"
      ? (voteResult.content?.filter(v => parseVoteResult(v.text) === "AGAINST").length || 0) : 0;
    const newEntry = {
      id: Date.now(),
      date: new Date().toLocaleString('ko-KR'),
      issue,
      content: formatDebateLog(issue, history, voteResult),
      result: voteResult?.type === "VOTE"
        ? (proCount > conCount ? "가결" : "부결") : "결의안",
    };
    await AsyncStorage.setItem('debate_history', JSON.stringify([newEntry, ...list].slice(0, 50)));
  } catch (e) { console.error("저장 실패:", e); }
};

// TTS 음성 설정 (의원별 고유 목소리)
let _cachedKoreanVoices = null;
export const invalidateVoiceCache = () => { _cachedKoreanVoices = null; };

const getVoiceSettings = async (memberId) => {
  let pitch = 1.0, rate = 0.88, volume = 1.0, voice = null;
  switch (memberId) {
    case "gemini":   pitch = 1.08; rate = 0.93; break;
    case "llama4":   pitch = 0.82; rate = 0.87; break;
    case "mistral":  pitch = 1.12; rate = 1.02; break;
    case "gptoss":   pitch = 0.96; rate = 0.84; volume = 0.98; break;
    case "nemotron": pitch = 0.91; rate = 0.81; volume = 0.97; break;
    case "olmo":     pitch = 1.02; rate = 0.88; break;
    case "trinity":  pitch = 1.08; rate = 0.90; break;
    case "nova":     pitch = 0.97; rate = 0.86; break;
  }
  try {
    if (_cachedKoreanVoices === null) {
      const available = await Speech.getAvailableVoicesAsync();
      _cachedKoreanVoices = available.filter(
        v => v.language?.startsWith('ko') || v.identifier?.toLowerCase().includes('kr')
      );
    }
    if (_cachedKoreanVoices.length > 0) {
      const idx = memberId.split('').reduce((a, c) => a + c.charCodeAt(0), 0) % _cachedKoreanVoices.length;
      voice = _cachedKoreanVoices[idx].identifier;
    }
  } catch {}
  return { pitch, rate, volume, voice };
};

// TTS용 텍스트 정제
const cleanForTTS = (text) => {
  if (!text) return "";
  const hanjaMap = {
    '愼重|慎重': '신중', '重要': '중요', '重大': '중대', '必要': '필요',
    '可能': '가능', '不可能': '불가능', '現在': '현재', '現實': '현실',
    '未來': '미래', '社會': '사회', '國家': '국가', '政府': '정부',
    '經濟': '경제', '政策': '정책', '制度': '제도', '問題': '문제',
    '解決': '해결', '方法': '방법', '結果': '결과', '原因': '원인',
    '根據': '근거', '主張': '주장', '反對': '반대', '贊成': '찬성',
    '分析': '분석', '判斷': '판단', '決定': '결정', '效率': '효율',
    '效果': '효과', '影響': '영향', '基準': '기준', '原則': '원칙',
    '價値': '가치', '自由': '자유', '平等': '평등', '正義': '정의',
    '安全': '안전', '危險': '위험', '保護': '보호', '發展': '발전',
    '成長': '성장', '改善': '개선', '統計': '통계', '資料': '자료',
    '報告': '보고', '議員': '의원', '議長': '의장', '本議員': '본의원',
  };
  let conv = text;
  Object.entries(hanjaMap).forEach(([k, v]) => {
    conv = conv.replace(new RegExp(k, 'g'), v);
  });
  return conv
    .replace(/\[REFUTE\]|\[ADMIT\]|\[DATA\]|\[GRAPHIC\]|\[TABLE\]/g, "")
    .replace(/\[CHART:[a-z]+\]\{[^}]*\}/g, "")
    .replace(/\[TABLE:json\]\{[^}]*\}/g, "")
    .replace(/Gemini/gi, "제미나이").replace(/Llama4?/gi, "라마")
    .replace(/Mistral/gi, "미스트랄").replace(/GPT.?OSS/gi, "지피티")
    .replace(/Nemotron/gi, "엔비디아")
    .replace(/OLMo/gi, "올모").replace(/Trinity/gi, "트리니티").replace(/Nova/gi, "노바")
    .replace(/≥/g, "이상").replace(/≤/g, "이하")
    .replace(/>/g, "초과").replace(/</g, "미만")
    .replace(/={2,}/g, "동일")
    .replace(/\*{2}/g, "").replace(/\*/g, "")
    .replace(/[\u4E00-\u9FFF\u3400-\u4DBF]+/g, "")
    .replace(/\uFE0F/g, "")
    .replace(/(?:^|\n)\s*-\s*/g, "\n")
    .replace(/\|[-:| ]+\|/g, "").replace(/\|/g, " ")
    .trim();
};

// ─── 재생 패널 컴포넌트 ─────────────────────────
const PlaybackPanel = ({ history, onClose }) => {
  const [currentIdx, setCurrentIdx] = useState(0);
  const [isPlaying, setIsPlaying]   = useState(false);
  const [isLoading, setIsLoading]   = useState(false);
  const playingRef  = useRef(false);
  const stopSignal  = useRef(false);

  // 컴포넌트 언마운트 시 재생 중지
  useEffect(() => {
    return () => { stopSignal.current = true; Speech.stop(); };
  }, []);

  // 재생 가능한 항목 — TTS 정제 후 비어있는 항목(CHART만 있는 발언 등) 사전 제거
  const playable = (history || []).filter(h => {
    if (!h.text) return false;
    return cleanForTTS(h.text).length > 0;
  });

  const speakItem = useCallback(async (idx) => {
    if (idx >= playable.length) {
      setIsPlaying(false);
      playingRef.current = false;
      setCurrentIdx(0);
      return;
    }
    if (stopSignal.current) return;

    const item = playable[idx];
    const ttsText = cleanForTTS(item.text);
    if (!ttsText) {
      // 빈 텍스트 건너뜀 — setTimeout으로 스택 분리
      if (!stopSignal.current && playingRef.current) {
        setCurrentIdx(idx + 1);
        setTimeout(() => speakItem(idx + 1), 0);
      }
      return;
    }

    setIsLoading(true);
    const { pitch, rate, volume, voice } = await getVoiceSettings(item.memberId || "");
    setIsLoading(false);
    if (stopSignal.current || !playingRef.current) return;

    await new Promise((resolve) => {
      try {
        Speech.speak(ttsText, {
          language: 'ko-KR', pitch, rate, volume, voice,
          onDone:    resolve,
          onStopped: resolve,
          onError:   resolve,
        });
      } catch { resolve(); }
    });

    if (!stopSignal.current && playingRef.current) {
      const next = idx + 1;
      setCurrentIdx(next);
      speakItem(next);
    }
  }, [playable]);

  const handlePlay = () => {
    if (isPlaying) return;
    stopSignal.current = false;
    playingRef.current = true;
    setIsPlaying(true);
    speakItem(currentIdx);
  };

  const handlePause = () => {
    stopSignal.current = true;
    playingRef.current = false;
    setIsPlaying(false);
    Speech.stop();
  };

  const handlePrev = () => {
    const next = Math.max(0, currentIdx - 1);
    setCurrentIdx(next);
    if (isPlaying) {
      stopSignal.current = true;
      Speech.stop();
      // Speech.stop()의 onStopped 콜백이 처리된 후 재생 시작
      setTimeout(() => {
        if (!isMountedRef?.current === false) return;
        stopSignal.current = false;
        speakItem(next);
      }, 80);
    }
  };

  const handleNext = () => {
    const next = Math.min(playable.length - 1, currentIdx + 1);
    setCurrentIdx(next);
    if (isPlaying) {
      stopSignal.current = true;
      Speech.stop();
      setTimeout(() => {
        if (!isMountedRef?.current === false) return;
        stopSignal.current = false;
        speakItem(next);
      }, 80);
    }
  };

  const current     = playable[currentIdx];
  const progressPct = playable.length > 0 ? ((currentIdx / playable.length) * 100).toFixed(0) : 0;

  return (
    <View style={playStyles.panel}>
      {/* 현재 발언자 */}
      <View style={playStyles.nowPlaying}>
        <Text style={playStyles.nowAvatar}>{current?.avatar || "💬"}</Text>
        <View style={{ flex: 1 }}>
          <Text style={playStyles.nowName} numberOfLines={1}>
            {current?.displayName || "—"}
          </Text>
          <Text style={playStyles.nowPreview} numberOfLines={2}>
            {current?.text ? current.text.slice(0, 60) + (current.text.length > 60 ? "..." : "") : ""}
          </Text>
        </View>
        <TouchableOpacity onPress={onClose} style={playStyles.closeBtn}>
          <Text style={playStyles.closeBtnText}>✕</Text>
        </TouchableOpacity>
      </View>

      {/* 진행 바 */}
      <View style={playStyles.progressBg}>
        <View style={[playStyles.progressFill, { width: `${progressPct}%` }]} />
      </View>
      <Text style={playStyles.progressLabel}>
        {currentIdx + 1} / {playable.length} 발언  ({progressPct}%)
      </Text>

      {/* 컨트롤 */}
      <View style={playStyles.controls}>
        <TouchableOpacity style={playStyles.ctrlBtn} onPress={handlePrev} disabled={currentIdx === 0}>
          <Text style={[playStyles.ctrlIcon, currentIdx === 0 && playStyles.ctrlDisabled]}>⏮</Text>
        </TouchableOpacity>

        {isLoading ? (
          <View style={playStyles.playBtn}>
            <Text style={playStyles.playIcon}>⏳</Text>
          </View>
        ) : isPlaying ? (
          <TouchableOpacity style={playStyles.playBtn} onPress={handlePause}>
            <Text style={playStyles.playIcon}>⏸</Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity style={playStyles.playBtn} onPress={handlePlay}>
            <Text style={playStyles.playIcon}>▶</Text>
          </TouchableOpacity>
        )}

        <TouchableOpacity
          style={playStyles.ctrlBtn}
          onPress={handleNext}
          disabled={currentIdx >= playable.length - 1}
        >
          <Text style={[playStyles.ctrlIcon, currentIdx >= playable.length - 1 && playStyles.ctrlDisabled]}>⏭</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
};

// ─── 메인 컴포넌트 ───────────────────────────
const DebateScreen = ({
  issue,
  maxTurns = 25,
  debateFormat = "릴레이",
  conclusionType = "VOTE",
  activeMembers,
  onFinish,
}) => {
  const [history, setHistory]         = useState([]);
  const [status, setStatus]           = useState("서버 연결 중...");
  const [isFinished, setIsFinished]   = useState(false);
  const [isSaving, setIsSaving]       = useState(false);
  const [showPlayback, setShowPlayback] = useState(false);
  const [turnDisplay, setTurnDisplay] = useState({ current: 0, max: maxTurns });

  const scrollRef     = useRef(null);
  const historyRef    = useRef([]);
  const wsRef         = useRef(null);
  const voteResultRef = useRef(null);
  const convictionRef = useRef({});
  const isMountedRef  = useRef(true);
  const onFinishRef   = useRef(onFinish);
  useEffect(() => { onFinishRef.current = onFinish; }, [onFinish]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      invalidateVoiceCache();
    };
  }, []);

  // ─── 발언 추가 (즉시 표시 — TTS 없음) ───
  const addLog = useCallback((data) => {
    if (!isMountedRef.current) return;
    const entry = {
      id:          Date.now() + Math.random(),
      memberId:    data.memberId    || "",
      displayName: data.displayName || "?",
      text:        data.text        || "",
      type:        data.speechType  || "NORMAL",
      engineInfo:  data.engineInfo  || "",
      color:       data.color       || COLORS.border,
      avatar:      data.avatar      || "💬",
      timestamp:   data.timestamp   || new Date().toLocaleTimeString('ko-KR',
                     { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    };

    // 턴 카운터 업데이트 (백엔드 전송값 우선, 없으면 계산)
    if (typeof data.turnCount === 'number') {
      setTurnDisplay({ current: data.turnCount, max: data.maxTurns || maxTurns });
    }

    setHistory(prev => {
      const next = [...prev, entry];
      historyRef.current = next;
      return next;
    });
    setStatus(`🎙 ${data.displayName} 발언`);
    setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 60);
  }, [maxTurns]);

  // ─── WebSocket ───
  useEffect(() => {
    const ws = new WebSocket(BACKEND_WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("서버 연결됨. 토론 시작 중...");
      ws.send(JSON.stringify({
        issue,
        maxTurns,
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
            setStatus(`${msg.format} · 의장: ${msg.chairName} · 최대 ${msg.maxTurns}턴`);
            setTurnDisplay({ current: 0, max: msg.maxTurns || maxTurns });
            break;
          case "round":
            setStatus(msg.label || "");
            break;
          case "speech":
            addLog(msg);
            break;
          case "result": {
            const voteResult = { type: msg.resultType, content: msg.content };
            voteResultRef.current = voteResult;
            saveToStorage(issue, historyRef.current, voteResult);
            if (isMountedRef.current) {
              setIsFinished(true);
              setStatus("✅ 토론 종료 — 기록이 보관함에 저장되었습니다");
              onFinishRef.current({
                type:       msg.resultType,
                content:    msg.content,
                history:    [...historyRef.current],
                conviction: convictionRef.current,
              });
            }
            break;
          }
          case "done":
            if (isMountedRef.current) setIsFinished(true);
            break;
          case "conviction":
            if (msg.all && typeof msg.all === 'object') {
              convictionRef.current = msg.all;
            }
            break;
          case "error":
            Alert.alert("서버 오류", msg.message || "알 수 없는 오류");
            setStatus("⚠️ 오류 발생");
            if (isMountedRef.current) setIsFinished(true);
            break;
        }
      } catch (e) {
        console.error("메시지 파싱 오류:", e);
      }
    };

    ws.onerror = () => {
      setStatus("⚠️ 서버 연결 실패");
      Alert.alert("연결 실패", `서버 주소를 확인하세요.\n${BACKEND_WS_URL}`);
      if (isMountedRef.current) setIsFinished(true);
    };

    ws.onclose = (event) => {
      console.log("[WS] 연결 종료");
      // done/result 없이 서버가 연결을 끊은 경우 UI가 '진행 중' 상태로 먹통이 되는 것을 방지
      if (isMountedRef.current) {
        setIsFinished(true);
        setStatus(event.wasClean ? "연결 종료" : "⚠️ 서버 연결이 끊겼습니다");
      }
    };

    return () => {
      Speech.stop();
      if (ws.readyState === WebSocket.OPEN) ws.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issue, maxTurns, debateFormat, conclusionType, activeMembers]);

  // ─── 파일 내보내기 ───
  const downloadDebateLog = async () => {
    if (isSaving) return;
    setIsSaving(true);
    try {
      const current = historyRef.current;
      if (!current || current.length === 0) {
        Alert.alert("알림", "저장할 토론 기록이 없습니다.");
      } else {
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
      }
    } catch (error) {
      Alert.alert("저장 실패", `오류: ${error.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  const turnPct = turnDisplay.max > 0
    ? Math.min(100, Math.round((turnDisplay.current / turnDisplay.max) * 100))
    : 0;

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
              {debateFormat === "릴레이" ? "🔄" :
               debateFormat === "집중토론" ? "⚡" :
               debateFormat === "전문가패널" ? "🎓" : "🌀"} {debateFormat}
            </Text>
          </View>
          {/* 턴 배지 */}
          <View style={styles.badge}>
            <Text style={styles.badgeText}>
              💬 {turnDisplay.current}/{turnDisplay.max}턴
            </Text>
          </View>
          {/* 토론 완료 후 음성 재생 버튼 */}
          {isFinished && (
            <TouchableOpacity
              style={[styles.iconBtn, showPlayback && styles.iconBtnActive]}
              onPress={() => setShowPlayback(v => !v)}
            >
              <Text style={styles.iconBtnText}>{showPlayback ? "🔊" : "🔈"}</Text>
            </TouchableOpacity>
          )}
        </View>
      </View>

      {/* 턴 진행 바 */}
      <View style={styles.turnBar}>
        <View style={[styles.turnBarFill, { width: `${turnPct}%` }]} />
      </View>

      {/* 상태 바 */}
      <View style={styles.statusBar}>
        <View style={[styles.statusDot, isFinished && styles.statusDotDone]} />
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

      {/* 발언 목록 */}
      <ScrollView
        ref={scrollRef}
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
      >
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
                  {h.type === "REFUTE" && (
                    <View style={styles.typeBadgeRefute}>
                      <Text style={styles.typeBadgeText}>⚔ 반박</Text>
                    </View>
                  )}
                  {h.type === "ADMIT" && (
                    <View style={styles.typeBadgeAdmit}>
                      <Text style={styles.typeBadgeText}>✅ 수용</Text>
                    </View>
                  )}
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
                if (seg.type === 'chart_bar')  return <BarChart  key={si} data={seg.data} />;
                if (seg.type === 'chart_line') return <LineChart key={si} data={seg.data} />;
                if (seg.type === 'chart_pie')  return <PieChart  key={si} data={seg.data} />;
                if (seg.type === 'table_json') return <DataTable key={si} data={seg.data} />;
                if (seg.type === 'graphic') return (
                  <View key={si} style={styles.graphicBox}>
                    <Text style={styles.graphicText}>{seg.content}</Text>
                  </View>
                );
                return (
                  <Text key={si} style={[styles.text, isChair && styles.textChair]}>
                    {seg.content}
                  </Text>
                );
              })}
            </View>
          );
        })}
        <View style={{ height: showPlayback ? 180 : 40 }} />
      </ScrollView>

      {/* 토론 완료 후 음성 재생 패널 (하단 고정) */}
      {isFinished && showPlayback && (
        <PlaybackPanel
          history={history.filter(h => h.text && !h.text.startsWith("━━"))}
          onClose={() => setShowPlayback(false)}
        />
      )}
    </View>
  );
};

// ─── 스타일 ──────────────────────────────────
const GOLD  = "#c9a84c";
const GOLD2 = "#e8cc7a";
const NAVY  = "#080c14";
const PANEL = "#10151f";
const PANEL2= "#161d2b";
const SLATE = "#1c2436";

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: NAVY },

  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingTop: 52, paddingBottom: 12, paddingHorizontal: 16,
    backgroundColor: PANEL,
    borderBottomWidth: 1, borderBottomColor: GOLD + "33",
  },
  headerLeft:  { flex: 1, marginRight: 10 },
  headerTitle: { color: GOLD2, fontSize: 14, fontWeight: "900", letterSpacing: 3 },
  headerIssue: { color: "#8899bb", fontSize: 11, marginTop: 2 },
  headerRight: { flexDirection: "row", alignItems: "center", gap: 6 },

  badge:       { backgroundColor: PANEL2, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 4, borderWidth: 1, borderColor: GOLD + "33" },
  badgePurple: { borderColor: "#9b59b6" + "55", backgroundColor: "#1a0d2e" },
  badgeText:   { color: GOLD, fontSize: 10, fontWeight: "700" },
  iconBtn:     { backgroundColor: PANEL2, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: GOLD + "33" },
  iconBtnActive: { borderColor: GOLD, backgroundColor: GOLD + "22" },
  iconBtnText: { fontSize: 14 },

  // 턴 진행 바
  turnBar: { height: 2, backgroundColor: SLATE },
  turnBarFill: { height: 2, backgroundColor: GOLD + "88" },

  statusBar: {
    flexDirection: "row", alignItems: "center",
    paddingHorizontal: 16, paddingVertical: 8,
    backgroundColor: PANEL2,
    borderBottomWidth: 1, borderBottomColor: "#ffffff0a",
    gap: 8,
  },
  statusDot:     { width: 6, height: 6, borderRadius: 3, backgroundColor: "#4fc3f7" },
  statusDotDone: { backgroundColor: "#27ae60" },
  statusText:    { flex: 1, color: "#7899cc", fontSize: 11, fontWeight: "600" },
  actionBtn:     { backgroundColor: "#1a3a1a", borderRadius: 6, paddingHorizontal: 10, paddingVertical: 5, borderWidth: 1, borderColor: "#27ae6055" },
  actionBtnDisabled: { backgroundColor: SLATE, borderColor: "#333" },
  actionBtnText: { color: "#27ae60", fontSize: 10, fontWeight: "700" },

  scroll:        { flex: 1 },
  scrollContent: { paddingHorizontal: 14, paddingTop: 12 },

  roundHeader: { flexDirection: "row", alignItems: "center", marginVertical: 14, gap: 10 },
  roundHeaderLine: { flex: 1, height: 1, backgroundColor: GOLD + "33" },
  roundHeaderText: { color: GOLD + "aa", fontSize: 10, fontWeight: "700", letterSpacing: 2 },

  card: {
    backgroundColor: PANEL,
    paddingHorizontal: 14, paddingVertical: 12,
    marginBottom: 10, borderRadius: 10,
    borderLeftWidth: 3, borderWidth: 1, borderColor: "#ffffff08",
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
  avatar:    { fontSize: 16 },
  nameWrap:  { flex: 1 },
  name:      { fontSize: 12, fontWeight: "800", letterSpacing: 0.5 },
  engineBadge: { fontSize: 9, color: "#4a5572", marginTop: 2 },

  metaRight:      { alignItems: "flex-end", gap: 4 },
  timestamp:      { color: "#3a4560", fontSize: 9, fontWeight: "600", letterSpacing: 0.5 },
  typeBadgeRefute:{ backgroundColor: "#2a0f0f", borderRadius: 4, paddingHorizontal: 7, paddingVertical: 2, borderWidth: 1, borderColor: "#e74c3c44" },
  typeBadgeAdmit: { backgroundColor: "#0c1f0c", borderRadius: 4, paddingHorizontal: 7, paddingVertical: 2, borderWidth: 1, borderColor: "#27ae6044" },
  typeBadgeText:  { fontSize: 9, fontWeight: "700", color: "#aaa" },

  text:      { color: "#c8d4e8", lineHeight: 22, fontSize: 13.5, letterSpacing: 0.2 },
  textChair: { color: "#a8b8cc", fontStyle: "italic" },

  dataBox:    { flexDirection: 'row', alignItems: 'flex-start', backgroundColor: '#091828', borderRadius: 6, padding: 10, marginVertical: 5, borderLeftWidth: 3, borderLeftColor: "#4fc3f7" },
  dataIcon:   { fontSize: 13, marginRight: 7 },
  dataText:   { flex: 1, color: "#4fc3f7", fontSize: 12, fontFamily: 'monospace' },
  graphicBox: { backgroundColor: '#091409', borderRadius: 6, padding: 10, marginVertical: 5, borderWidth: 1, borderColor: "#27ae6044" },
  graphicText:{ color: '#39d353', fontSize: 12, fontFamily: 'monospace' },
  tableBox:   { backgroundColor: '#0d1426', borderRadius: 6, padding: 10, marginVertical: 5, borderWidth: 1, borderColor: GOLD + "33" },
  tableText:  { color: "#b8c8e0", fontSize: 11, fontFamily: 'monospace', lineHeight: 18 },
});

// ─── 재생 패널 스타일 ─────────────────────────
const playStyles = StyleSheet.create({
  panel: {
    position: 'absolute', bottom: 0, left: 0, right: 0,
    backgroundColor: PANEL2,
    borderTopWidth: 1, borderTopColor: GOLD + "44",
    paddingHorizontal: 16, paddingTop: 12, paddingBottom: 20,
    elevation: 10,
  },
  nowPlaying: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10 },
  nowAvatar:  { fontSize: 24 },
  nowName:    { color: GOLD2, fontSize: 12, fontWeight: "800" },
  nowPreview: { color: "#5a6a88", fontSize: 10, marginTop: 2 },
  closeBtn:   { padding: 6 },
  closeBtnText:{ color: "#4a5572", fontSize: 14, fontWeight: "700" },

  progressBg:   { height: 3, backgroundColor: SLATE, borderRadius: 2, marginBottom: 4 },
  progressFill: { height: 3, backgroundColor: GOLD, borderRadius: 2 },
  progressLabel:{ color: "#3a4560", fontSize: 10, textAlign: 'right', marginBottom: 10 },

  controls:    { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 24 },
  ctrlBtn:     { padding: 8 },
  ctrlIcon:    { fontSize: 22, color: GOLD2 },
  ctrlDisabled:{ color: "#2a3448" },
  playBtn:     { width: 52, height: 52, borderRadius: 26, backgroundColor: GOLD, alignItems: 'center', justifyContent: 'center' },
  playIcon:    { fontSize: 20, color: NAVY, fontWeight: "900" },
});

export default DebateScreen;