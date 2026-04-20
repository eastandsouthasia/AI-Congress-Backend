import React, { useState } from "react";
import {
  View, Text, ScrollView, StyleSheet,
  TouchableOpacity, SafeAreaView, Alert, Modal,
} from "react-native";
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { COLORS, MEMBERS } from "../constants/members";

// 추가: 데이터 포인트 분석 함수
const analyzeDataPoints = (history = []) => {
  const stats = { data: 0, refute: 0, admit: 0, graphic: 0 };
  history.forEach(log => {
    const text = log.text || "";
    if (text.includes("[DATA]")) stats.data++;
    if (text.includes("[REFUTE]")) stats.refute++;
    if (text.includes("[ADMIT]")) stats.admit++;
    if (text.includes("[GRAPHIC]")) stats.graphic++;
  });
  return stats;
};

const parseVoteLabel = (text = "") => {
  if (text.includes("[찬성]") || text.includes("찬성")) return "찬성";
  if (text.includes("[반대]") || text.includes("반대")) return "반대";
  if (text.includes("[기권]") || text.includes("기권")) return "기권";
  return "기권";
};

const VOTE_COLOR = {
  찬성: COLORS.success,
  반대: COLORS.error,
  기권: COLORS.textMuted,
};

// 회의록 포맷 (DebateScreen과 동일)
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
      const pro = voteResult.content?.filter(v => v.text?.includes("찬성")).length || 0;
      const con = voteResult.content?.filter(v => v.text?.includes("반대")).length || 0;
      const abs = (voteResult.content?.length || 0) - pro - con;
      resultSection += `찬성 ${pro} / 반대 ${con} / 기권 ${abs}\n결과: ${pro > con ? "✅ 가결" : "❌ 부결"}\n`;
      voteResult.content?.forEach(v => { resultSection += `${v.memberId}: ${v.text}\n`; });
    } else {
      resultSection += `공동 결의안:\n${voteResult.content}`;
    }
    resultSection += `\n`;
  }
  return header + body + resultSection +
    `\n------------------------------------------\n본 문서는 AI 의결 시스템에 의해 작성되었습니다.\n==========================================`;
};

const VotingScreen = ({ issue, result, onReset }) => {
  const [isSaving, setIsSaving]       = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  // 다운로드 핸들러
  const handleDownload = async () => {
    if (isSaving) return;
    setIsSaving(true);
    try {
      const voteResult = { type: result.type, content: result.content };
      const logText = formatDebateLog(issue, result.history || [], voteResult);
      const fileName = `AI_Congress_${Date.now()}.txt`;
      const fileUri = (FileSystem.documentDirectory || FileSystem.cacheDirectory) + fileName;
      await FileSystem.writeAsStringAsync(fileUri, logText, { encoding: FileSystem.EncodingType.UTF8 });
      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(fileUri, { mimeType: 'text/plain', dialogTitle: 'AI 의회 토론 기록' });
      } else {
        Alert.alert("저장 완료", `경로: ${fileUri}`);
      }
    } catch (e) {
      Alert.alert("저장 실패", e.message);
    } finally {
      setIsSaving(false);
    }
  };

  // 데이터 지표 집계
  const dataStats = analyzeDataPoints(result.history);

  // 표결 집계
  const tally = { 찬성: 0, 반대: 0, 기권: 0 };
  if (result.type === "VOTE") {
    result.content.forEach(v => {
      const label = parseVoteLabel(v.text);
      tally[label] = (tally[label] || 0) + 1;
    });
  }
  const total = result.type === "VOTE" ? result.content.length : 0;
  const passed = tally["찬성"] > tally["반대"];

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.container}>
        <Text style={styles.title}>📋 의결 보고서</Text>

        <View style={styles.issueCard}>
          <Text style={styles.issueLabel}>안건</Text>
          <Text style={styles.issueText}>{issue}</Text>
        </View>

        <ScrollView style={styles.scroll} showsVerticalScrollIndicator={false}>
          
          {/* ── 데이터 강조 리포트 섹션 추가 ── */}
          <View style={styles.reportCard}>
            <Text style={styles.reportTitle}>📊 데이터 중심 토론 분석</Text>
            <View style={styles.reportGrid}>
              <View style={styles.reportItem}>
                <Text style={styles.reportVal}>{dataStats.data}</Text>
                <Text style={styles.reportLab}>객관적 지표</Text>
              </View>
              <View style={styles.reportItem}>
                <Text style={styles.reportVal}>{dataStats.refute}</Text>
                <Text style={styles.reportLab}>논리적 반박</Text>
              </View>
              <View style={styles.reportItem}>
                <Text style={styles.reportVal}>{dataStats.admit}</Text>
                <Text style={styles.reportLab}>합리적 수용</Text>
              </View>
            </View>
            <Text style={styles.reportDesc}>
              본 토론에서는 총 {dataStats.data}개의 구체적 데이터가 인용되었으며, 
              {dataStats.admit}번의 입장 수정을 통해 합리적 결론에 도달했습니다.
            </Text>
          </View>

          {result.type === "RESOLUTION" && (
            <View style={styles.resCard}>
              <Text style={styles.resTitle}>📜 공동 결의안</Text>
              <Text style={styles.resContent}>{result.content}</Text>
            </View>
          )}

          {result.type === "VOTE" && (
            <>
              <View style={[styles.verdictBanner, { borderColor: passed ? COLORS.success : COLORS.error }]}>
                <Text style={[styles.verdictText, { color: passed ? COLORS.success : COLORS.error }]}>
                  {passed ? "✅ 가결 (PASSED)" : "❌ 부결 (FAILED)"}
                </Text>
                <Text style={styles.tallyText}>
                  찬성 {tally["찬성"]} · 반대 {tally["반대"]} · 기권 {tally["기권"]}
                </Text>
              </View>

              <View style={styles.gaugeWrap}>
                {total > 0 && (
                  <>
                    <View style={[styles.gaugeBar, { flex: tally["찬성"] || 0.01, backgroundColor: COLORS.success }]} />
                    <View style={[styles.gaugeBar, { flex: tally["기권"] || 0.01, backgroundColor: COLORS.textMuted }]} />
                    <View style={[styles.gaugeBar, { flex: tally["반대"] || 0.01, backgroundColor: COLORS.error }]} />
                  </>
                )}
              </View>

              <Text style={styles.sectionLabel}>◈ 의원별 투표 내역</Text>
              {result.content.map((v, i) => {
                const member = MEMBERS.find(m => m.id === v.memberId);
                const label = parseVoteLabel(v.text);
                return (
                  <View key={i} style={[styles.voteItem, { borderLeftColor: member?.color ?? COLORS.border }]}>
                    <View style={styles.voteHeader}>
                      <Text style={[styles.voteMember, { color: member?.color ?? COLORS.textDim }]}>
                        {member?.avatar ?? ""} {member?.name ?? v.memberId}
                      </Text>
                      <View style={[styles.voteLabelBadge, { borderColor: VOTE_COLOR[label] }]}>
                        <Text style={[styles.voteLabelText, { color: VOTE_COLOR[label] }]}>{label}</Text>
                      </View>
                    </View>
                    <Text style={styles.voteReason}>{v.text.replace(/\[찬성\]|\[반대\]|\[기권\]/g, "").trim()}</Text>
                  </View>
                );
              })}
            </>
          )}
          <View style={{ height: 20 }} />
        </ScrollView>

        {/* 하단 버튼 3개 */}
        <View style={styles.btnRow}>
          <TouchableOpacity style={[styles.btn, styles.btnSecondary]} onPress={() => setShowHistory(true)} activeOpacity={0.8}>
            <Text style={styles.btnText}>📜 토론 다시 보기</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.btn, styles.btnSecondary, isSaving && styles.btnDisabled]} onPress={handleDownload} disabled={isSaving} activeOpacity={0.8}>
            <Text style={styles.btnText}>{isSaving ? "저장 중..." : "📤 다운로드"}</Text>
          </TouchableOpacity>
        </View>
        <TouchableOpacity style={[styles.btn, styles.btnPrimary]} onPress={onReset} activeOpacity={0.8}>
          <Text style={styles.btnText}>⚖ 새 안건 상정</Text>
        </TouchableOpacity>
      </View>

      {/* 토론 다시 보기 모달 */}
      <Modal visible={showHistory} animationType="slide" onRequestClose={() => setShowHistory(false)}>
        <SafeAreaView style={styles.modalSafe}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>📜 토론 전체 기록</Text>
            <TouchableOpacity onPress={() => setShowHistory(false)} style={styles.modalClose}>
              <Text style={styles.modalCloseText}>✕ 닫기</Text>
            </TouchableOpacity>
          </View>
          <ScrollView style={styles.modalScroll} contentContainerStyle={{ padding: 16 }}>
            {(result.history || []).map((h, i) => {
              const member = MEMBERS.find(m => m.id === h.memberId);
              const color = h.color || member?.color || COLORS.border;
              return (
                <View key={i} style={[styles.histCard, { borderLeftColor: color }]}>
                  <Text style={[styles.histName, { color }]}>
                    {h.avatar || member?.avatar || "💬"} {h.displayName}
                    {h.type === "REFUTE" ? "  ⚔ 반박" : h.type === "ADMIT" ? "  ✅ 수용" : ""}
                  </Text>
                  <Text style={styles.histText}>{h.text}</Text>
                </View>
              );
            })}
            <View style={{ height: 40 }} />
          </ScrollView>
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
};
const styles = StyleSheet.create({
  safe:        { flex: 1, backgroundColor: COLORS.background },
  container:   { flex: 1, padding: 20 },
  title: {
    color: COLORS.text, fontSize: 20, fontWeight: "bold",
    marginVertical: 16, textAlign: "center",
  },
  issueCard: {
    backgroundColor: COLORS.card,
    borderRadius: 10, padding: 14, marginBottom: 16,
    borderWidth: 1, borderColor: COLORS.border2,
  },
  issueLabel: { fontSize: 10, color: COLORS.textMuted, letterSpacing: 2, marginBottom: 4 },
  issueText:  { color: COLORS.accent, fontSize: 14, textAlign: "center", lineHeight: 20 },
// 💡 여기에 붙여넣으세요
  reportCard: {
    backgroundColor: "#161b22",
    borderRadius: 12, padding: 16, marginBottom: 16,
    borderWidth: 1, borderColor: COLORS.border2
  },
  reportTitle: { color: COLORS.accent, fontSize: 13, fontWeight: "bold", marginBottom: 12 },
  reportGrid: { flexDirection: "row", justifyContent: "space-between", marginBottom: 12 },
  reportItem: { alignItems: "center", flex: 1 },
  reportVal: { color: COLORS.text, fontSize: 18, fontWeight: "bold" },
  reportLab: { color: COLORS.textMuted, fontSize: 10, marginTop: 2 },
  reportDesc: { color: COLORS.textDim, fontSize: 11, lineHeight: 16, borderTopWidth: 1, borderTopColor: "#30363d", paddingTop: 8 },
  scroll: { flex: 1 },

  // 결의안
  resCard:    { backgroundColor: COLORS.card, padding: 20, borderRadius: 12 },
  resTitle:   { color: COLORS.accent, fontWeight: "bold", marginBottom: 10, fontSize: 15 },
  resContent: { color: COLORS.text, lineHeight: 24, fontSize: 14 },

  // 가결/부결
  verdictBanner: {
    borderWidth: 1.5, borderRadius: 10,
    padding: 14, alignItems: "center", marginBottom: 12,
    backgroundColor: COLORS.card,
  },
  verdictText: { fontSize: 18, fontWeight: "800", marginBottom: 4 },
  tallyText:   { fontSize: 13, color: COLORS.textDim },

  // 게이지
  gaugeWrap:  { flexDirection: "row", height: 8, borderRadius: 4, overflow: "hidden", marginBottom: 20 },
  gaugeBar:   { height: "100%" },

  sectionLabel: { fontSize: 10, color: COLORS.blue, letterSpacing: 3, marginBottom: 10 },

  // 의원 투표
  voteItem: {
    backgroundColor: COLORS.card,
    padding: 14, marginBottom: 10,
    borderRadius: 10, borderLeftWidth: 3,
  },
  voteHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 6 },
  voteMember: { fontWeight: "bold", fontSize: 13 },
  voteLabelBadge: {
    borderWidth: 1, borderRadius: 6,
    paddingHorizontal: 8, paddingVertical: 2,
  },
  voteLabelText: { fontSize: 11, fontWeight: "700" },
  voteReason:   { color: COLORS.textDim, fontSize: 12, lineHeight: 18 },

  // 버튼
  btnRow: { flexDirection: "row", gap: 8, marginTop: 12 },
  btn: {
    padding: 14, borderRadius: 12,
    alignItems: "center", flex: 1,
  },
  btnPrimary:   { backgroundColor: COLORS.blue, marginTop: 8, flex: 0, width: "100%" },
  btnSecondary: { backgroundColor: COLORS.surface2 || "#1c2128", borderWidth: 1, borderColor: COLORS.border2 },
  btnDisabled:  { opacity: 0.5 },
  btnText: { color: "#fff", fontWeight: "bold", fontSize: 13, letterSpacing: 0.5 },

  // 모달
  modalSafe:    { flex: 1, backgroundColor: COLORS.background },
  modalHeader:  { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: 16, borderBottomWidth: 1, borderBottomColor: COLORS.border2 },
  modalTitle:   { color: COLORS.text, fontSize: 16, fontWeight: "bold" },
  modalClose:   { padding: 8 },
  modalCloseText: { color: COLORS.accent, fontSize: 14 },
  modalScroll:  { flex: 1 },
  histCard:     { backgroundColor: COLORS.card, padding: 12, marginBottom: 10, borderRadius: 8, borderLeftWidth: 3 },
  histName:     { fontSize: 11, fontWeight: "bold", marginBottom: 6 },
  histText:     { color: COLORS.text, fontSize: 13, lineHeight: 20 },
});

export default VotingScreen;