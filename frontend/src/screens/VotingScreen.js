/**
 * VoteReportScreen — 국회 공식 의결보고서 화면
 *
 * 사용법:
 *   import VoteReportScreen from './VoteReportScreen';
 *
 *   // DebateScreen의 onFinish 콜백에서:
 *   onFinish={(result) => setVoteResult(result)}
 *
 *   // 렌더링:
 *   {voteResult && (
 *     <VoteReportScreen
 *       issue={issue}
 *       result={voteResult}           // { type: "VOTE"|"RESOLUTION", content: [...] | string }
 *       history={debateHistory}       // DebateScreen의 history
 *       members={MEMBERS}             // constants/members의 MEMBERS
 *       onClose={() => setVoteResult(null)}
 *     />
 *   )}
 */

import React, { useRef, useEffect, useState, useCallback } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity,
  StyleSheet, Animated, Easing, Alert,
} from 'react-native';
import * as FileSystem from 'expo-file-system';
import * as Sharing   from 'expo-sharing';
import * as Speech    from 'expo-speech';
// [버그A6 수정] AsyncStorage import 제거 — saveReport에서 중복 저장 삭제로 불필요

// ─── TTS 텍스트 정제 (DebateScreen과 동일) ────────────
const cleanForTTS = (text) => {
  if (!text) return "";
  return text
    .replace(/\[REFUTE\]|\[ADMIT\]|\[DATA\]|\[GRAPHIC\]|\[TABLE\]/g, "")
    .replace(/\[CHART:[a-z]+\]\{[^}]*\}/g, "")
    .replace(/\[TABLE:json\]\{[^}]*\}/g, "")
    .replace(/Gemini/gi, "제미나이").replace(/Llama4?/gi, "라마")
    .replace(/Mistral/gi, "미스트랄").replace(/GPT.?OSS/gi, "지피티")
    .replace(/Nemotron/gi, "엔비디아")
    .replace(/OLMo/gi, "올모").replace(/Trinity/gi, "트리니티").replace(/Nova/gi, "노바")
    .replace(/≥/g, "이상").replace(/≤/g, "이하")
    .replace(/>/g, "초과").replace(/</g, "미만")
    .replace(/\*{2}/g, "").replace(/\*/g, "")
    .replace(/[\u4E00-\u9FFF]+/g, "")
    .replace(/\|[-:| ]+\|/g, "").replace(/\|/g, " ")
    .trim();
};

// 의원별 음성 설정
const VOICE_SETTINGS = {
  gemini:   { pitch: 1.08, rate: 0.93 },
  llama4:   { pitch: 0.82, rate: 0.87 },
  mistral:  { pitch: 1.12, rate: 1.02 },
  gptoss:   { pitch: 0.96, rate: 0.84 },
  nemotron: { pitch: 0.91, rate: 0.81 },
  olmo:     { pitch: 1.02, rate: 0.88 },
  trinity:  { pitch: 1.08, rate: 0.90 },
  nova:     { pitch: 0.97, rate: 0.86 },
};

// ─── 음성 재생 패널 ───────────────────────────────────
function PlaybackPanel({ history, onClose }) {
  const [currentIdx, setCurrentIdx] = useState(0);
  const [isPlaying, setIsPlaying]   = useState(false);
  const [isLoading, setIsLoading]   = useState(false);
  const playingRef  = useRef(false);
  const stopSignal  = useRef(false);

  useEffect(() => {
    return () => { stopSignal.current = true; Speech.stop(); };
  }, []);

  // 재생 가능한 발언만 필터
  const playable = (history || []).filter(h => h.text && !h.text.startsWith("━━"));

  const speakItem = useCallback(async (idx) => {
    if (idx >= playable.length) {
      setIsPlaying(false); playingRef.current = false; setCurrentIdx(0); return;
    }
    if (stopSignal.current) return;

    const item = playable[idx];
    const ttsText = cleanForTTS(item.text);
    if (!ttsText) {
      // 빈 텍스트 건너뜀 — setTimeout으로 스택 분리 (연속 빈 항목 스택오버플로 방지)
      if (!stopSignal.current && playingRef.current) { setCurrentIdx(idx + 1); setTimeout(() => speakItem(idx + 1), 0); }
      return;
    }

    setIsLoading(true);
    const vs = VOICE_SETTINGS[item.memberId] || { pitch: 1.0, rate: 0.88 };
    setIsLoading(false);
    if (stopSignal.current || !playingRef.current) return;

    await new Promise((resolve) => {
      try {
        Speech.speak(ttsText, {
          language: 'ko-KR', pitch: vs.pitch, rate: vs.rate, volume: 1.0,
          onDone: resolve, onStopped: resolve, onError: resolve,
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
    stopSignal.current = false; playingRef.current = true;
    setIsPlaying(true);
    speakItem(currentIdx);
  };
  const handlePause = () => {
    stopSignal.current = true; playingRef.current = false;
    setIsPlaying(false); Speech.stop();
  };
  const handlePrev = () => {
    const next = Math.max(0, currentIdx - 1);
    setCurrentIdx(next);
    if (isPlaying) { Speech.stop(); stopSignal.current = false; speakItem(next); }
  };
  const handleNext = () => {
    const next = Math.min(playable.length - 1, currentIdx + 1);
    setCurrentIdx(next);
    if (isPlaying) { Speech.stop(); stopSignal.current = false; speakItem(next); }
  };

  const current    = playable[currentIdx];
  const progressPct = playable.length > 0
    ? Math.min(100, Math.round((currentIdx / playable.length) * 100)) : 0;

  return (
    <View style={pbStyles.panel}>
      {/* 현재 발언자 */}
      <View style={pbStyles.nowRow}>
        <Text style={pbStyles.nowAvatar}>{current?.avatar || "💬"}</Text>
        <View style={{ flex: 1 }}>
          <Text style={pbStyles.nowName} numberOfLines={1}>
            {current?.displayName || "—"}
          </Text>
          <Text style={pbStyles.nowPreview} numberOfLines={1}>
            {current?.text ? current.text.slice(0, 55) + (current.text.length > 55 ? "…" : "") : ""}
          </Text>
        </View>
        <TouchableOpacity onPress={onClose} style={pbStyles.closeBtn}>
          <Text style={pbStyles.closeBtnTxt}>✕</Text>
        </TouchableOpacity>
      </View>

      {/* 진행 바 */}
      <View style={pbStyles.progressBg}>
        <View style={[pbStyles.progressFill, { width: `${progressPct}%` }]} />
      </View>
      <Text style={pbStyles.progressLabel}>
        {currentIdx + 1} / {playable.length}  ({progressPct}%)
      </Text>

      {/* 컨트롤 */}
      <View style={pbStyles.controls}>
        <TouchableOpacity onPress={handlePrev} disabled={currentIdx === 0} style={pbStyles.ctrlBtn}>
          <Text style={[pbStyles.ctrlTxt, currentIdx === 0 && pbStyles.ctrlDim]}>⏮</Text>
        </TouchableOpacity>
        {isLoading ? (
          <View style={pbStyles.playBtn}><Text style={pbStyles.playTxt}>⏳</Text></View>
        ) : isPlaying ? (
          <TouchableOpacity style={pbStyles.playBtn} onPress={handlePause}>
            <Text style={pbStyles.playTxt}>⏸</Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity style={pbStyles.playBtn} onPress={handlePlay}>
            <Text style={pbStyles.playTxt}>▶</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity onPress={handleNext} disabled={currentIdx >= playable.length - 1} style={pbStyles.ctrlBtn}>
          <Text style={[pbStyles.ctrlTxt, currentIdx >= playable.length - 1 && pbStyles.ctrlDim]}>⏭</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

// ─── 색상 팔레트 (국회 공식 느낌) ─────────────────
const C = {
  bg:          '#0a0c10',   // 극심한 다크
  paper:       '#0f1218',   // 문서 배경
  headerBg:    '#0c1020',   // 헤더 배경
  gold:        '#c9a84c',   // 국회 금색
  goldLight:   '#e8cc7a',   // 밝은 금
  goldDim:     '#7a6030',   // 어두운 금
  red:         '#c0392b',   // 부결 빨강
  redDim:      '#7a2318',
  green:       '#1a6b3a',   // 가결 초록
  greenLight:  '#27ae60',
  blue:        '#1a3a6b',   // 찬성
  blueLight:   '#2980b9',
  text:        '#d4c9a8',   // 양피지색 텍스트
  textMuted:   '#7a7060',
  textDark:    '#4a4535',
  border:      '#2a2418',
  borderGold:  '#3a2f18',
  seal:        '#8b1a1a',   // 인장 빨강
  white:       '#f0ead8',   // 크림색
};

// ─── 의원 이름 매핑 ────────────────────────────────
// bias → 초기 확신도 (conviction_tracker.py의 _INITIAL_CONVICTION과 동일)
// [정합성 수정④] "기술주의·효율" 키 제거 — members.py에 해당 bias 의원 없음.
const INITIAL_CONVICTION = {
  "진보·개혁·공정":  55,
  "자유주의·분권":   30,
  "실용·데이터중심": 10,
  "보수·안정·점진": -30,
};

// 확신도(-100~+100) → 라벨·색상
const convictionLabel = (v) => {
  if (v >= 60)  return { label: "강한 찬성",  color: "#27ae60" };
  if (v >= 25)  return { label: "찬성",       color: "#2ecc71" };
  if (v >= 5)   return { label: "약한 찬성",  color: "#82c98a" };
  if (v >= -5)  return { label: "중립",       color: "#95a5a6" };
  if (v >= -25) return { label: "약한 반대",  color: "#e07b54" };
  if (v >= -60) return { label: "반대",       color: "#e74c3c" };
  return         { label: "강한 반대",  color: "#c0392b" };
};

const MEMBER_NAMES = {
  gemini:   '제미나이',
  llama4:   '라마',
  mistral:  '미스트랄',
  gptoss:   '지피티',
  nemotron: '엔비디아',
  olmo:     '올모',
  trinity:  '트리니티',
  nova:     '노바',
};

const MEMBER_AVATARS = {
  gemini:   '♊',   // members.py 기준
  llama4:   '🦙',
  // [버그8 수정] members.py avatar와 불일치하던 3개 항목 수정
  mistral:  '🌊',  // 기존: 🌪
  gptoss:   '⚡',  // 기존: 🤖
  nemotron: '🖥️', // 기존: ⚡
  olmo:     '🔬',
  trinity:  '⚔️',
  nova:     '☁️',
};

// ─── 투표 결과 파싱 (DebateScreen.js의 parseVoteResult와 동일 로직 유지) ──
// [버그수정] 오파싱 방지: 브라켓 prefix 또는 엄격한 시작 패턴으로만 판단.
const parseVoteText = (text = '') => {
  const t = text.trimStart();
  if (/^\[찬성\]/.test(t) || /^찬성[\s.,!]/.test(t) || t === '찬성') return 'FOR';
  if (/^\[반대\]/.test(t) || /^반대[\s.,!]/.test(t) || t === '반대') return 'AGAINST';
  if (/^\[기권\]/.test(t) || /^기권[\s.,!]/.test(t) || t === '기권') return 'ABSTAIN';
  return 'ABSTAIN';
};

const extractReason = (text = '') =>
  text.replace(/^\[?(?:찬성|반대|기권)\]?\s*/u, '').trim();

// ─── 회의록 텍스트 생성 ────────────────────────────
const buildReportText = (issue, result, history) => {
  const now = new Date();
  const dateStr = now.toLocaleString('ko-KR', {
    year: 'numeric', month: 'long', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });

  const divider = '═'.repeat(48);
  const thin    = '─'.repeat(48);

  let out = '';
  out += `${divider}\n`;
  out += `  대한민국 인공지능 의회\n`;
  out += `  AI NATIONAL ASSEMBLY\n`;
  out += `  공식 의결보고서\n`;
  out += `${divider}\n\n`;
  out += `  문서번호  : AI-${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}-${String(now.getTime()).slice(-4)}\n`;
  out += `  의결일시  : ${dateStr}\n`;
  out += `  안   건  : ${issue}\n`;
  out += `  총 발언수 : ${history?.length || 0}건\n`;
  out += `\n${thin}\n`;

  if (result.type === 'VOTE' && Array.isArray(result.content)) {
    const pro  = result.content.filter(v => parseVoteText(v.text) === 'FOR').length;
    const con  = result.content.filter(v => parseVoteText(v.text) === 'AGAINST').length;
    const abs  = result.content.filter(v => parseVoteText(v.text) === 'ABSTAIN').length;
    const pass = pro > con;

    out += `\n  【 표결 결과 】\n\n`;
    out += `  재석의원   : ${result.content.length}명\n`;
    out += `  찬   성   : ${pro}표\n`;
    out += `  반   대   : ${con}표\n`;
    out += `  기   권   : ${abs}표\n`;
    out += `\n  ▶ 의결결과 : ${pass ? '[ 가 결 ]' : '[ 부 결 ]'}\n`;
    out += `\n${thin}\n`;
    out += `\n  【 의원별 투표 내역 】\n\n`;
    result.content.forEach(v => {
      const name = MEMBER_NAMES[v.memberId] || v.memberId;
      const vote = parseVoteText(v.text);
      const mark = vote === 'FOR' ? '찬성' : vote === 'AGAINST' ? '반대' : '기권';
      const reason = extractReason(v.text);
      out += `  ${name} 의원  [${mark}]\n`;
      out += `  ${reason}\n\n`;
    });
  } else {
    out += `\n  【 공식 결의문 (Resolution) 】\n\n`;
    out += (result.content || '결의문 없음') + '\n';
  }

  out += `\n${thin}\n`;
  out += `\n  【 토론 발언록 요약 】\n\n`;
  (history || []).slice(0, 30).forEach((h, i) => {
    const tag = h.type === 'REFUTE' ? '[반박]' : h.type === 'ADMIT' ? '[수용]' : '';
    out += `  ${i+1}. ${h.displayName} ${tag}\n`;
    out += `     ${h.text.substring(0, 100)}${h.text.length > 100 ? '...' : ''}\n\n`;
  });

  out += `\n${divider}\n`;
  out += `  본 문서는 AI 의결 시스템에 의해 자동 생성되었습니다.\n`;
  out += `  AI National Assembly Automated Resolution System\n`;
  out += `  © ${now.getFullYear()} AI Congress Simulation\n`;
  out += `${divider}\n`;

  return out;
};

// ─── 저장 함수 ──────────────────────────────────────
const saveReport = async (issue, result, history) => {
  const text     = buildReportText(issue, result, history);
  const fileName = `AI_의결보고서_${Date.now()}.txt`;
  const baseDir  = FileSystem.documentDirectory || FileSystem.cacheDirectory;
  const fileUri  = baseDir + fileName;

  await FileSystem.writeAsStringAsync(fileUri, text, {
    encoding: FileSystem.EncodingType.UTF8,
  });

  const canShare = await Sharing.isAvailableAsync();
  if (canShare) {
    await Sharing.shareAsync(fileUri, {
      mimeType: 'text/plain',
      dialogTitle: 'AI 의회 의결보고서',
    });
  } else {
    Alert.alert('저장 완료', `경로: ${fileUri}`);
  }
  // [버그A6 수정] AsyncStorage 중복 저장 제거.
  // DebateScreen.js의 saveToStorage()가 result 수신 시 이미 저장하므로
  // 여기서 다시 저장하면 같은 토론이 보관함에 2개 쌓임.
};

// ══════════════════════════════════════════════════════
// 메인 컴포넌트
// ══════════════════════════════════════════════════════
export default function VoteReportScreen({ issue, result, history, members, conviction, onClose }) {
  const fadeAnim  = useRef(new Animated.Value(0)).current;
  const slideAnim = useRef(new Animated.Value(40)).current;
  const sealAnim  = useRef(new Animated.Value(0)).current;
  const [isSaving, setIsSaving] = useState(false);
  const [showReplay, setShowReplay] = useState(false);     // 텍스트 다시보기 모달
  const [showPlayback, setShowPlayback] = useState(false); // 음성 재생 패널

  // 입장 애니메이션
  useEffect(() => {
    Animated.sequence([
      Animated.parallel([
        Animated.timing(fadeAnim,  { toValue: 1, duration: 500, useNativeDriver: true }),
        Animated.timing(slideAnim, { toValue: 0, duration: 500, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      ]),
      Animated.timing(sealAnim, { toValue: 1, duration: 400, delay: 200, easing: Easing.out(Easing.back(1.5)), useNativeDriver: true }),
    ]).start();
  }, []);

  // 투표 집계
  const isVote = result.type === 'VOTE' && Array.isArray(result.content);
  const votes  = isVote ? result.content : [];
  const pro    = votes.filter(v => parseVoteText(v.text) === 'FOR').length;
  const con    = votes.filter(v => parseVoteText(v.text) === 'AGAINST').length;
  const abs    = votes.filter(v => parseVoteText(v.text) === 'ABSTAIN').length;
  const passed = pro > con;
  const total  = votes.length;

  const handleSave = useCallback(async () => {
    if (isSaving) return;
    setIsSaving(true);
    try {
      await saveReport(issue, result, history);
    } catch (e) {
      Alert.alert('내보내기 실패', e.message || '오류가 발생했습니다.\nAPK 빌드 후 정상 작동합니다.');
    } finally {
      setIsSaving(false);
    }
  }, [issue, result, history, isSaving]);

  // 공유: 다른 앱으로 텍스트 전송
  const handleShare = useCallback(async () => {
    try {
      const text = buildReportText(issue, result, history);
      const { Share } = require('react-native');
      await Share.share({ message: text, title: `AI 의회 의결보고서: ${issue}` });
    } catch (e) {
      Alert.alert('공유 실패', e.message || '공유할 수 없습니다.');
    }
  }, [issue, result, history]);

  // 다운로드: 파일로 저장
  const handleDownload = useCallback(async () => {
    if (isSaving) return;
    setIsSaving(true);
    try {
      const text = buildReportText(issue, result, history);
      const fileName = `AI_의결보고서_${Date.now()}.txt`;
      const baseDir = FileSystem.documentDirectory || FileSystem.cacheDirectory;
      const fileUri = baseDir + fileName;
      await FileSystem.writeAsStringAsync(fileUri, text, { encoding: FileSystem.EncodingType.UTF8 });
      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(fileUri, { mimeType: 'text/plain', dialogTitle: '파일 저장' });
      } else {
        Alert.alert('저장 완료', `파일 경로:\n${fileUri}`);
      }
    } catch (e) {
      Alert.alert('다운로드 실패', e.message || '파일을 저장할 수 없습니다.');
    } finally {
      setIsSaving(false);
    }
  }, [issue, result, history, isSaving]);

  // ── 찬성·반대·기권 바 너비 ──
  const barW = (n) => total > 0 ? `${Math.round((n / total) * 100)}%` : '0%';

  const now = new Date();
  const docNo = `AI-${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}-${String(now.getTime()).slice(-4)}`;
  const dateStr = now.toLocaleString('ko-KR', { year:'numeric', month:'long', day:'numeric', hour:'2-digit', minute:'2-digit' });

  return (
    <Animated.View style={[styles.overlay, { opacity: fadeAnim }]}>
      <Animated.View style={[styles.sheet, { transform: [{ translateY: slideAnim }] }]}>

        {/* ── 헤더 ── */}
        <View style={styles.header}>
          <View style={styles.headerTopRow}>
            <View style={styles.emblemWrap}>
              <Text style={styles.emblemIcon}>🏛</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.headerKo}>대한민국 인공지능 의회</Text>
              <Text style={styles.headerEn}>AI NATIONAL ASSEMBLY</Text>
            </View>
            <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
              <Text style={styles.closeBtnText}>✕</Text>
            </TouchableOpacity>
          </View>
          <View style={styles.headerDividerGold} />
          <Text style={styles.headerTitle}>공 식  의 결 보 고 서</Text>
          <Text style={styles.headerSubtitle}>OFFICIAL RESOLUTION REPORT</Text>
          <View style={styles.headerDividerGold} />
        </View>

        <ScrollView style={styles.scroll} showsVerticalScrollIndicator={false}>

          {/* ── 문서 정보 ── */}
          <View style={styles.docInfoBox}>
            <DocRow label="문 서 번 호" value={docNo} />
            <DocRow label="의 결 일 시" value={dateStr} />
            <DocRow label="안      건" value={issue} highlight />
            <DocRow label="총 발언 수" value={`${history?.length || 0}건`} />
          </View>

          <View style={styles.sectionDivider} />

          {isVote ? (
            <>
              {/* ── 표결 결과 배너 ── */}
              <View style={[styles.verdictBanner, passed ? styles.verdictPass : styles.verdictFail]}>
                <Animated.View style={{ transform: [{ scale: sealAnim }] }}>
                  <Text style={styles.verdictSeal}>{passed ? '가  결' : '부  결'}</Text>
                  <Text style={styles.verdictSealEn}>{passed ? 'PASSED' : 'REJECTED'}</Text>
                </Animated.View>
              </View>

              {/* ── 집계 ── */}
              <View style={styles.tallyBox}>
                <Text style={styles.tallyTitle}>【 표결 집계 】</Text>

                <View style={styles.tallyRow}>
                  <View style={styles.tallyItem}>
                    <Text style={styles.tallyNum}>{pro}</Text>
                    <Text style={[styles.tallyLabel, { color: C.greenLight }]}>찬  성</Text>
                  </View>
                  <View style={styles.tallyDivLine} />
                  <View style={styles.tallyItem}>
                    <Text style={[styles.tallyNum, { color: '#e74c3c' }]}>{con}</Text>
                    <Text style={[styles.tallyLabel, { color: '#e74c3c' }]}>반  대</Text>
                  </View>
                  <View style={styles.tallyDivLine} />
                  <View style={styles.tallyItem}>
                    <Text style={[styles.tallyNum, { color: C.textMuted }]}>{abs}</Text>
                    <Text style={[styles.tallyLabel, { color: C.textMuted }]}>기  권</Text>
                  </View>
                  <View style={styles.tallyDivLine} />
                  <View style={styles.tallyItem}>
                    <Text style={[styles.tallyNum, { color: C.gold }]}>{total}</Text>
                    <Text style={[styles.tallyLabel, { color: C.gold }]}>재  석</Text>
                  </View>
                </View>

                {/* 바 차트 */}
                <View style={styles.barChart}>
                  <BarRow label="찬성" color={C.greenLight} width={barW(pro)} count={pro} />
                  <BarRow label="반대" color="#e74c3c"      width={barW(con)} count={con} />
                  <BarRow label="기권" color={C.textMuted}  width={barW(abs)} count={abs} />
                </View>
              </View>

              <View style={styles.sectionDivider} />

              {/* ── 의원별 투표 내역 ── */}
              <Text style={styles.sectionTitle}>【 의원별 투표 내역 】</Text>
              {votes.map((v, i) => {
                const voteType = parseVoteText(v.text);
                const reason   = extractReason(v.text);
                const name     = MEMBER_NAMES[v.memberId] || v.memberId;
                const avatar   = MEMBER_AVATARS[v.memberId] || '💬';
                const member   = members?.find(m => m.id === v.memberId);
                const color    = member?.color || C.gold;
                return (
                  <View key={i} style={[styles.voteCard, { borderLeftColor: color }]}>
                    <View style={styles.voteCardHeader}>
                      <Text style={[styles.voteCardName, { color }]}>{avatar} {name} 의원</Text>
                      <VoteBadge type={voteType} />
                    </View>
                    <Text style={styles.voteCardReason}>{reason}</Text>
                  </View>
                );
              })}
            </>
          ) : (
            <>
              {/* ── 공동 결의안 ── */}
              <View style={styles.resolutionBox}>
                <Text style={styles.sectionTitle}>【 공동 결의안 】</Text>
                <Text style={styles.resolutionText}>{result.content || '결의문을 생성할 수 없습니다.'}</Text>
              </View>
            </>
          )}

          <View style={styles.sectionDivider} />

          {/* ── 확신도 변화 ── */}
          {conviction && Object.keys(conviction).length > 0 && (() => {
            const convMembers = (members || []).filter(m => conviction[m.id] !== undefined);
            if (convMembers.length === 0) return null;
            return (
              <>
                <Text style={styles.sectionTitle}>
                  {isVote ? "【 토론 중 확신도 변화 (투표 반영) 】" : "【 토론 중 확신도 변화 (결의 방향 참고) 】"}
                </Text>
                <Text style={styles.convSubtitle}>
                  {isVote
                    ? "토론 전 성향(bias)에서 출발해 [ADMIT]/[REFUTE] 발언으로 변화한 최종 확신도이며, 투표에 반영되었습니다."
                    : "토론 전 성향(bias)에서 출발해 [ADMIT]/[REFUTE] 발언으로 변화한 최종 확신도입니다. 결의문 작성 시 참고 지표로 사용되었습니다."}
                </Text>
                {convMembers.map((m) => {
                  const initVal = INITIAL_CONVICTION[m.bias] ?? 0;
                  const finalVal = conviction[m.id] ?? initVal;
                  const delta = finalVal - initVal;
                  const initInfo  = convictionLabel(initVal);
                  const finalInfo = convictionLabel(finalVal);
                  const avatar = MEMBER_AVATARS[m.id] || '💬';
                  const name   = MEMBER_NAMES[m.id]   || m.id;

                  // 바 너비: -100~+100 → 0~100% (50%가 0 기준점)
                  const toBarPct = (v) => Math.round((v + 100) / 2);
                  const initPct  = toBarPct(initVal);
                  const finalPct = toBarPct(finalVal);

                  return (
                    <View key={m.id} style={styles.convCard}>
                      {/* 헤더 */}
                      <View style={styles.convCardHeader}>
                        <Text style={[styles.convMemberName, { color: m.color || C.gold }]}>
                          {avatar} {name} 의원
                        </Text>
                        <View style={styles.convDeltaBadge}>
                          <Text style={[
                            styles.convDeltaText,
                            { color: delta > 0 ? '#27ae60' : delta < 0 ? '#e74c3c' : '#95a5a6' }
                          ]}>
                            {delta > 0 ? `+${delta.toFixed(0)}` : delta.toFixed(0)}
                          </Text>
                        </View>
                      </View>

                      {/* 초기 → 최종 라벨 */}
                      <View style={styles.convLabelRow}>
                        <View style={styles.convLabelItem}>
                          <Text style={styles.convLabelSub}>토론 전</Text>
                          <Text style={[styles.convLabelVal, { color: initInfo.color }]}>
                            {initInfo.label}
                          </Text>
                          <Text style={[styles.convLabelNum, { color: initInfo.color }]}>
                            {initVal > 0 ? `+${initVal}` : initVal}
                          </Text>
                        </View>
                        <Text style={styles.convArrow}>
                          {delta > 2 ? '→ 찬성 ▲' : delta < -2 ? '→ 반대 ▼' : '→ 유지 ─'}
                        </Text>
                        <View style={[styles.convLabelItem, { alignItems: 'flex-end' }]}>
                          <Text style={styles.convLabelSub}>토론 후</Text>
                          <Text style={[styles.convLabelVal, { color: finalInfo.color }]}>
                            {finalInfo.label}
                          </Text>
                          <Text style={[styles.convLabelNum, { color: finalInfo.color }]}>
                            {finalVal > 0 ? `+${finalVal.toFixed(0)}` : finalVal.toFixed(0)}
                          </Text>
                        </View>
                      </View>

                      {/* 확신도 바 */}
                      <View style={styles.convBarWrap}>
                        {/* 중앙선(0점) */}
                        <View style={styles.convBarCenter} />
                        {/* 초기 마커 */}
                        <View style={[styles.convBarMarker, {
                          left: `${initPct}%`,
                          borderColor: initInfo.color,
                        }]} />
                        {/* 최종 채움 */}
                        <View style={[styles.convBarFill, {
                          left:  `${Math.min(initPct, finalPct)}%`,
                          width: `${Math.abs(finalPct - initPct)}%`,
                          backgroundColor: delta >= 0 ? '#27ae60' : '#e74c3c',
                          opacity: 0.35,
                        }]} />
                        {/* 최종 마커 */}
                        <View style={[styles.convBarDot, {
                          left: `${finalPct}%`,
                          backgroundColor: finalInfo.color,
                        }]} />
                      </View>

                      {/* 축 레이블 */}
                      <View style={styles.convAxisRow}>
                        <Text style={styles.convAxisLabel}>강한 반대</Text>
                        <Text style={styles.convAxisLabel}>중립</Text>
                        <Text style={styles.convAxisLabel}>강한 찬성</Text>
                      </View>
                    </View>
                  );
                })}
                <View style={styles.sectionDivider} />
              </>
            );
          })()}

          {/* ── 발언록 요약 ── */}
          <Text style={styles.sectionTitle}>【 주요 발언록 】</Text>
          {(history || []).slice(0, 20).map((h, i) => {
            const member = members?.find(m => m.id === h.memberId);
            const color  = h.color || member?.color || C.goldDim;
            const tag    = h.type === 'REFUTE' ? '⚔반박' : h.type === 'ADMIT' ? '✅수용' : null;
            return (
              <View key={i} style={[styles.logCard, { borderLeftColor: color + '88' }]}>
                <View style={styles.logCardHeader}>
                  <Text style={[styles.logCardNum, { color: C.goldDim }]}>{i + 1}</Text>
                  <Text style={[styles.logCardName, { color }]}>{h.avatar || '💬'} {h.displayName}</Text>
                  {tag && <Text style={styles.logTag}>{tag}</Text>}
                </View>
                <Text style={styles.logCardText} numberOfLines={3}>{h.text}</Text>
              </View>
            );
          })}

          {/* ── 서명란 ── */}
          <View style={styles.signatureBox}>
            <View style={styles.sectionDivider} />
            <Text style={styles.signatureTitle}>서  명  란</Text>
            <View style={styles.signatureRow}>
              <SignCell label="의    장" />
              <SignCell label="서    기" />
              <SignCell label="감    사" />
            </View>
            <View style={styles.sectionDivider} />
            <Text style={styles.footerText}>
              본 문서는 AI 의결 시스템에 의해 자동 생성된 공식 기록물입니다.
            </Text>
            <Text style={styles.footerSubText}>
              AI National Assembly Automated Resolution System
            </Text>
            <Text style={styles.footerSubText}>
              © {now.getFullYear()} AI Congress Simulation
            </Text>
          </View>

          <View style={{ height: 32 }} />
        </ScrollView>

        {/* ── 하단 버튼 5개 ── */}
        <View style={styles.footer}>
          <TouchableOpacity style={[styles.footerBtn, styles.footerBtnClose]} onPress={onClose}>
            <Text style={styles.footerBtnText}>✕{'\n'}닫기</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.footerBtn, styles.footerBtnReplay]} onPress={() => setShowReplay(true)}>
            <Text style={styles.footerBtnText}>👁{'\n'}다시보기</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.footerBtn, styles.footerBtnVoice, showPlayback && styles.footerBtnVoiceActive]}
            onPress={() => setShowPlayback(v => !v)}
          >
            <Text style={styles.footerBtnText}>🔊{'\n'}음성재생</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.footerBtn, styles.footerBtnShare]} onPress={handleShare}>
            <Text style={styles.footerBtnText}>📤{'\n'}공유</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.footerBtn, styles.footerBtnSave, isSaving && styles.footerBtnDisabled]}
            onPress={handleDownload}
            disabled={isSaving}
          >
            <Text style={[styles.footerBtnText, { color: C.bg }]}>
              {isSaving ? '...' : '⬇'}{'\n'}{isSaving ? '처리중' : '내려받기'}
            </Text>
          </TouchableOpacity>
        </View>

        {/* ── 음성 재생 패널 (하단 고정) ── */}
        {showPlayback && (
          <PlaybackPanel
            history={history}
            onClose={() => setShowPlayback(false)}
          />
        )}

        {/* ── 다시보기 모달 (토론 회의록) ── */}
        {showReplay && (
          <View style={styles.replayOverlay}>
            <View style={styles.replaySheet}>
              <View style={styles.replayHeader}>
                <Text style={styles.replayTitle}>📜 토론 회의록</Text>
                <Text style={styles.replayIssue} numberOfLines={1}>{issue}</Text>
                <TouchableOpacity onPress={() => setShowReplay(false)} style={styles.replayCloseBtn}>
                  <Text style={styles.replayCloseText}>✕ 닫기</Text>
                </TouchableOpacity>
              </View>
              <ScrollView style={styles.replayScroll} contentContainerStyle={{ padding: 14 }}>
                {(history || []).map((h, i) => {
                  const member = members?.find(m => m.id === h.memberId);
                  const color = h.color || member?.color || C.gold;
                  const tag = h.type === 'REFUTE' ? '⚔반박' : h.type === 'ADMIT' ? '✅수용' : null;
                  return (
                    <View key={i} style={[styles.replayCard, { borderLeftColor: color + "99" }]}>
                      <View style={styles.replayCardHeader}>
                        <Text style={[styles.replayCardNum, { color: C.goldDim }]}>{i + 1}</Text>
                        <Text style={[styles.replayCardName, { color }]}>{h.avatar || '💬'} {h.displayName}</Text>
                        {tag && <Text style={styles.replayTag}>{tag}</Text>}
                        {h.timestamp && <Text style={styles.replayTime}>{h.timestamp}</Text>}
                      </View>
                      <Text style={styles.replayText}>{h.text}</Text>
                    </View>
                  );
                })}
                <View style={{ height: 40 }} />
              </ScrollView>
            </View>
          </View>
        )}

      </Animated.View>
    </Animated.View>
  );
}

// ── 서브 컴포넌트 ──────────────────────────────────
function DocRow({ label, value, highlight }) {
  return (
    <View style={styles.docRow}>
      <Text style={styles.docLabel}>{label}</Text>
      <Text style={styles.docColon}>│</Text>
      <Text style={[styles.docValue, highlight && styles.docValueHL]} numberOfLines={2}>{value}</Text>
    </View>
  );
}

function BarRow({ label, color, width, count }) {
  return (
    <View style={styles.barRow}>
      <Text style={[styles.barLabel, { color }]}>{label}</Text>
      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width, backgroundColor: color }]} />
      </View>
      <Text style={[styles.barCount, { color }]}>{count}표</Text>
    </View>
  );
}

function VoteBadge({ type }) {
  const cfg = {
    FOR:     { label: '찬  성', bg: '#0f2f1a', border: '#27ae60', text: '#27ae60' },
    AGAINST: { label: '반  대', bg: '#2f0f0f', border: '#e74c3c', text: '#e74c3c' },
    ABSTAIN: { label: '기  권', bg: '#1a1a1a', border: '#888',    text: '#888'    },
  }[type] || { label: '기권', bg: '#1a1a1a', border: '#888', text: '#888' };
  return (
    <View style={[styles.badge, { backgroundColor: cfg.bg, borderColor: cfg.border }]}>
      <Text style={[styles.badgeText, { color: cfg.text }]}>{cfg.label}</Text>
    </View>
  );
}

function SignCell({ label }) {
  return (
    <View style={styles.signCell}>
      <Text style={styles.signLabel}>{label}</Text>
      <View style={styles.signBox} />
    </View>
  );
}

// ══════════════════════════════════════════════════════
// 스타일
// ══════════════════════════════════════════════════════
const styles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.88)',
    justifyContent: 'flex-end',
    zIndex: 999,
  },
  sheet: {
    backgroundColor: C.paper,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderTopWidth: 2,
    borderTopColor: C.goldDim,
    maxHeight: '95%',
    flex: 1,
    marginTop: 40,
  },

  // ── 헤더 ──
  header: {
    backgroundColor: C.headerBg,
    paddingTop: 20,
    paddingHorizontal: 20,
    paddingBottom: 14,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderBottomWidth: 1,
    borderBottomColor: C.borderGold,
  },
  headerTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 10,
    gap: 12,
  },
  emblemWrap: {
    width: 40, height: 40,
    borderRadius: 20,
    backgroundColor: C.borderGold,
    borderWidth: 1,
    borderColor: C.goldDim,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emblemIcon: { fontSize: 22 },
  headerKo: {
    color: C.gold,
    fontSize: 13,
    fontWeight: '700',
    letterSpacing: 1,
  },
  headerEn: {
    color: C.goldDim,
    fontSize: 9,
    fontWeight: '600',
    letterSpacing: 2,
    marginTop: 1,
  },
  closeBtn: {
    width: 32, height: 32,
    borderRadius: 16,
    backgroundColor: '#1a1a1a',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: '#333',
  },
  closeBtnText: { color: '#888', fontSize: 14 },
  headerDividerGold: {
    height: 1,
    backgroundColor: C.goldDim,
    marginVertical: 8,
    opacity: 0.6,
  },
  headerTitle: {
    color: C.goldLight,
    fontSize: 18,
    fontWeight: '800',
    textAlign: 'center',
    letterSpacing: 4,
    marginVertical: 4,
  },
  headerSubtitle: {
    color: C.goldDim,
    fontSize: 9,
    fontWeight: '600',
    textAlign: 'center',
    letterSpacing: 3,
    marginBottom: 2,
  },

  scroll: { flex: 1, paddingHorizontal: 16 },

  // ── 문서 정보 ──
  docInfoBox: {
    backgroundColor: '#0c0e14',
    borderWidth: 1,
    borderColor: C.borderGold,
    borderRadius: 4,
    padding: 14,
    marginTop: 16,
    gap: 8,
  },
  docRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  docLabel: {
    color: C.textMuted,
    fontSize: 11,
    fontWeight: '600',
    width: 76,
    letterSpacing: 1,
  },
  docColon: { color: C.borderGold, fontSize: 11 },
  docValue: {
    flex: 1,
    color: C.text,
    fontSize: 11,
    lineHeight: 16,
    letterSpacing: 0.5,
  },
  docValueHL: {
    color: C.goldLight,
    fontWeight: '700',
    fontSize: 12,
  },

  sectionDivider: {
    height: 1,
    backgroundColor: C.borderGold,
    marginVertical: 16,
    opacity: 0.5,
  },
  sectionTitle: {
    color: C.gold,
    fontSize: 13,
    fontWeight: '700',
    letterSpacing: 2,
    marginBottom: 12,
    textAlign: 'center',
  },

  // ── 의결 배너 ──
  verdictBanner: {
    borderRadius: 6,
    borderWidth: 2,
    marginVertical: 8,
    paddingVertical: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  verdictPass: {
    backgroundColor: '#0a1f10',
    borderColor: C.greenLight,
  },
  verdictFail: {
    backgroundColor: '#1f0a0a',
    borderColor: C.red,
  },
  verdictSeal: {
    fontSize: 36,
    fontWeight: '900',
    color: C.white,
    textAlign: 'center',
    letterSpacing: 8,
  },
  verdictSealEn: {
    fontSize: 12,
    fontWeight: '700',
    color: C.textMuted,
    textAlign: 'center',
    letterSpacing: 4,
    marginTop: 4,
  },

  // ── 집계 ──
  tallyBox: {
    backgroundColor: '#0c0e14',
    borderWidth: 1,
    borderColor: C.borderGold,
    borderRadius: 4,
    padding: 16,
    marginBottom: 4,
  },
  tallyTitle: {
    color: C.gold,
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 2,
    textAlign: 'center',
    marginBottom: 14,
  },
  tallyRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'center',
    marginBottom: 16,
  },
  tallyItem: { alignItems: 'center', flex: 1 },
  tallyNum: {
    color: C.greenLight,
    fontSize: 28,
    fontWeight: '900',
    letterSpacing: 1,
  },
  tallyLabel: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 2,
    marginTop: 2,
  },
  tallyDivLine: {
    width: 1,
    height: 40,
    backgroundColor: C.borderGold,
  },

  // ── 바 차트 ──
  barChart: { gap: 8 },
  barRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  barLabel: { width: 28, fontSize: 10, fontWeight: '700', textAlign: 'right' },
  barTrack: {
    flex: 1,
    height: 10,
    backgroundColor: '#1a1a1a',
    borderRadius: 5,
    overflow: 'hidden',
  },
  barFill: { height: '100%', borderRadius: 5 },
  barCount: { width: 26, fontSize: 10, textAlign: 'right' },

  // ── 투표 카드 ──
  voteCard: {
    backgroundColor: '#0c0e14',
    borderLeftWidth: 3,
    borderRadius: 4,
    padding: 12,
    marginBottom: 8,
  },
  voteCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 6,
  },
  voteCardName: { fontSize: 12, fontWeight: '700', flex: 1 },
  badge: {
    borderWidth: 1,
    borderRadius: 3,
    paddingHorizontal: 10,
    paddingVertical: 3,
  },
  badgeText: { fontSize: 10, fontWeight: '800', letterSpacing: 2 },
  voteCardReason: {
    color: C.text,
    fontSize: 11,
    lineHeight: 17,
    opacity: 0.85,
  },

  // ── 결의안 ──
  resolutionBox: {
    backgroundColor: '#0c0e14',
    borderWidth: 1,
    borderColor: C.borderGold,
    borderRadius: 4,
    padding: 16,
    marginBottom: 8,
  },
  resolutionText: {
    color: C.text,
    fontSize: 12,
    lineHeight: 20,
    letterSpacing: 0.3,
  },

  // ── 발언록 ──
  logCard: {
    borderLeftWidth: 2,
    paddingLeft: 10,
    paddingVertical: 8,
    marginBottom: 8,
  },
  logCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  logCardNum: {
    fontSize: 10,
    fontWeight: '700',
    width: 18,
  },
  logCardName: { fontSize: 11, fontWeight: '700', flex: 1 },
  logTag: {
    fontSize: 9,
    color: C.gold,
    backgroundColor: C.borderGold,
    borderRadius: 3,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  logCardText: {
    color: C.textMuted,
    fontSize: 11,
    lineHeight: 16,
  },

  // ── 서명란 ──
  signatureBox: { paddingBottom: 8 },
  signatureTitle: {
    color: C.gold,
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 3,
    textAlign: 'center',
    marginBottom: 12,
  },
  signatureRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginBottom: 16,
    gap: 12,
  },
  signCell: { flex: 1, alignItems: 'center', gap: 6 },
  signLabel: {
    color: C.textMuted,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1,
  },
  signBox: {
    width: '100%',
    height: 36,
    borderWidth: 1,
    borderColor: C.borderGold,
    borderRadius: 3,
    backgroundColor: '#0a0c10',
  },
  footerText: {
    color: C.textMuted,
    fontSize: 10,
    textAlign: 'center',
    letterSpacing: 0.5,
    lineHeight: 16,
  },
  footerSubText: {
    color: C.textDark,
    fontSize: 9,
    textAlign: 'center',
    letterSpacing: 1,
    marginTop: 3,
  },

  // ── 하단 버튼 ──
  footer: {
    flexDirection: 'row',
    padding: 10,
    gap: 8,
    borderTopWidth: 1,
    borderTopColor: C.borderGold,
    backgroundColor: C.headerBg,
  },
  footerBtn: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
  },
  footerBtnClose: {
    backgroundColor: '#12141a',
    borderColor: C.borderGold,
  },
  footerBtnReplay: {
    backgroundColor: '#0d1828',
    borderColor: '#2980b966',
  },
  footerBtnVoice: {
    backgroundColor: '#1a0d28',
    borderColor: '#9b59b666',
  },
  footerBtnVoiceActive: {
    backgroundColor: '#2a1040',
    borderColor: '#9b59b6',
  },
  footerBtnShare: {
    backgroundColor: '#0d1e12',
    borderColor: '#27ae6066',
  },
  footerBtnSave: {
    backgroundColor: C.gold,
    borderColor: C.goldLight,
  },
  footerBtnDisabled: {
    backgroundColor: C.textDark,
    borderColor: '#333',
  },
  footerBtnText: {
    color: C.gold,
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1,
    textAlign: 'center',
    lineHeight: 15,
  },

  // ── 다시보기 모달 ──
  replayOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.92)',
    zIndex: 10,
    justifyContent: 'flex-end',
  },
  replaySheet: {
    backgroundColor: '#0d1018',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderTopWidth: 2,
    borderTopColor: C.goldDim,
    maxHeight: '90%',
    flex: 1,
    marginTop: 60,
  },
  replayHeader: {
    backgroundColor: C.headerBg,
    padding: 16,
    paddingTop: 20,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderBottomWidth: 1,
    borderBottomColor: C.borderGold,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  replayTitle: { color: C.goldLight, fontSize: 14, fontWeight: '800', letterSpacing: 1 },
  replayIssue: { flex: 1, color: C.textMuted, fontSize: 10 },
  replayCloseBtn: { paddingHorizontal: 10, paddingVertical: 5, backgroundColor: '#1a1a1a', borderRadius: 6, borderWidth: 1, borderColor: C.borderGold },
  replayCloseText: { color: C.gold, fontSize: 11, fontWeight: '700' },
  replayScroll: { flex: 1 },
  replayCard: {
    borderLeftWidth: 3,
    paddingLeft: 12,
    paddingVertical: 10,
    marginBottom: 10,
    backgroundColor: '#0c0f16',
    borderRadius: 6,
    paddingRight: 10,
  },
  replayCardHeader: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 5, flexWrap: 'wrap' },
  replayCardNum: { fontSize: 10, fontWeight: '700', width: 20 },
  replayCardName: { fontSize: 11, fontWeight: '700', flex: 1 },
  replayTag: { fontSize: 9, color: C.gold, backgroundColor: C.borderGold, borderRadius: 3, paddingHorizontal: 5, paddingVertical: 1 },
  replayTime: { fontSize: 9, color: C.textDark, fontWeight: '600' },
  replayText: { color: '#aab0c0', fontSize: 12, lineHeight: 18 },

  // ── 확신도 변화 ──
  convSubtitle: {
    fontSize: 10, color: C.textDark, lineHeight: 15,
    marginBottom: 12, marginTop: -4,
  },
  convCard: {
    backgroundColor: '#0c0f16',
    borderWidth: 1, borderColor: '#1c2336',
    borderRadius: 8, padding: 14, marginBottom: 10,
  },
  convCardHeader: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginBottom: 10,
  },
  convMemberName: { fontSize: 13, fontWeight: '700' },
  convDeltaBadge: {
    paddingHorizontal: 8, paddingVertical: 3,
    backgroundColor: '#161c2a', borderRadius: 20,
    borderWidth: 1, borderColor: '#2a3448',
  },
  convDeltaText: { fontSize: 12, fontWeight: '800' },
  convLabelRow: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginBottom: 12,
  },
  convLabelItem: { alignItems: 'flex-start' },
  convLabelSub:  { fontSize: 9,  color: C.textDark, marginBottom: 2 },
  convLabelVal:  { fontSize: 11, fontWeight: '700' },
  convLabelNum:  { fontSize: 10, fontWeight: '600', opacity: 0.8 },
  convArrow:     { fontSize: 10, color: C.textDark, flex: 1, textAlign: 'center' },

  // 바 그래프
  convBarWrap: {
    height: 20, backgroundColor: '#141820',
    borderRadius: 10, position: 'relative',
    overflow: 'hidden', marginBottom: 4,
  },
  convBarCenter: {
    position: 'absolute', left: '50%', top: 0, bottom: 0,
    width: 1, backgroundColor: '#2a3448',
  },
  convBarMarker: {
    position: 'absolute', top: 3, bottom: 3,
    width: 2, borderRadius: 1,
    borderWidth: 1,
    marginLeft: -1,
  },
  convBarFill: {
    position: 'absolute', top: 0, bottom: 0,
    minWidth: 2,
  },
  convBarDot: {
    position: 'absolute', top: '50%',
    width: 10, height: 10, borderRadius: 5,
    marginTop: -5, marginLeft: -5,
  },
  convAxisRow: {
    flexDirection: 'row', justifyContent: 'space-between',
  },
  convAxisLabel: { fontSize: 8, color: '#2a3448' },
});

// ─── 재생 패널 스타일 ─────────────────────────────────
const pbStyles = StyleSheet.create({
  panel: {
    position: 'absolute', bottom: 0, left: 0, right: 0,
    backgroundColor: '#0d0f16',
    borderTopWidth: 2, borderTopColor: '#7a4fb5',
    paddingHorizontal: 16, paddingTop: 12, paddingBottom: 24,
    elevation: 20,
    zIndex: 100,
  },
  nowRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10,
  },
  nowAvatar:   { fontSize: 22 },
  nowName:     { color: '#e8cc7a', fontSize: 12, fontWeight: '800' },
  nowPreview:  { color: '#4a5572', fontSize: 10, marginTop: 2 },
  closeBtn:    { padding: 6 },
  closeBtnTxt: { color: '#4a5572', fontSize: 14, fontWeight: '700' },

  progressBg:   { height: 3, backgroundColor: '#1c2030', borderRadius: 2, marginBottom: 4 },
  progressFill: { height: 3, backgroundColor: '#9b59b6', borderRadius: 2 },
  progressLabel:{ color: '#2a3448', fontSize: 10, textAlign: 'right', marginBottom: 10 },

  controls:  { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 28 },
  ctrlBtn:   { padding: 8 },
  ctrlTxt:   { fontSize: 22, color: '#e8cc7a' },
  ctrlDim:   { color: '#2a3448' },
  playBtn:   {
    width: 52, height: 52, borderRadius: 26,
    backgroundColor: '#9b59b6',
    alignItems: 'center', justifyContent: 'center',
  },
  playTxt:   { fontSize: 20, color: '#fff', fontWeight: '900' },
});