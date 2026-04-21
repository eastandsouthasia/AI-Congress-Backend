import React, { useState } from "react";
import {
  View, Text, TextInput, TouchableOpacity,
  ScrollView, StyleSheet, SafeAreaView, Switch,
} from "react-native";
import { COLORS, MEMBERS } from "../constants/members";

const DURATIONS = [10, 15, 20, 30, 45, 60];
const FORMATS   = ["릴레이", "집중토론", "전문가패널", "자유토론"];
const CONCLUSIONS = [
  { id: "VOTE",       label: "🗳 표결" },
  { id: "RESOLUTION", label: "📜 결의안" },
];

export default function InputScreen({ onStart, onShowHistory }) {
  const [issue,       setIssue]       = useState("");
  const [duration,    setDuration]    = useState(15);
  const [format,      setFormat]      = useState("릴레이");
  const [conclusion,  setConclusion]  = useState("VOTE");
  const [memberFlags, setMemberFlags] = useState(
    Object.fromEntries(MEMBERS.map(m => [m.id, true]))
  );

  const toggleMember = (id) =>
    setMemberFlags(prev => ({ ...prev, [id]: !prev[id] }));

  const activeMembers = MEMBERS.filter(m => memberFlags[m.id]).map(m => m.id);

  const handleStart = () => {
    if (!issue.trim()) return;
    if (activeMembers.length < 2) {
      alert("최소 2명의 의원을 선택해주세요.");
      return;
    }
    onStart(issue.trim(), duration, format, conclusion, activeMembers);
  };

  return (
    <SafeAreaView style={styles.safe}>
      {/* 의회장 헤더 */}
      <View style={styles.heroHeader}>
        <View style={styles.heroTop}>
          <Text style={styles.pillars}>⬛  ⬛  ⬛  ⬛  ⬛</Text>
        </View>
        <Text style={styles.heroTitle}>🏛 AI 의회</Text>
        <Text style={styles.heroSub}>ARTIFICIAL INTELLIGENCE CONGRESS</Text>
        <View style={styles.heroDivider} />
      </View>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled">

        {/* 안건 입력 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <View style={styles.sectionDot} />
            <Text style={styles.label}>안  건</Text>
          </View>
          <TextInput
            style={styles.input}
            placeholder="토론할 안건을 입력하세요..."
            placeholderTextColor={COLORS.textMuted}
            value={issue}
            onChangeText={setIssue}
            multiline
            maxLength={200}
          />
        </View>

        {/* 토론 시간 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <View style={styles.sectionDot} />
            <Text style={styles.label}>토론 시간</Text>
          </View>
          <View style={styles.row}>
            {DURATIONS.map(d => (
              <TouchableOpacity
                key={d}
                style={[styles.chip, duration === d && styles.chipActive]}
                onPress={() => setDuration(d)}
              >
                <Text style={[styles.chipText, duration === d && styles.chipTextActive]}>
                  {d}분{d === 60 ? " ★" : ""}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* 토론 형식 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <View style={styles.sectionDot} />
            <Text style={styles.label}>토론 형식</Text>
          </View>
          <View style={styles.row}>
            {FORMATS.map(f => (
              <TouchableOpacity
                key={f}
                style={[styles.chip, format === f && styles.chipActive]}
                onPress={() => setFormat(f)}
              >
                <Text style={[styles.chipText, format === f && styles.chipTextActive]}>{f}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* 결론 방식 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <View style={styles.sectionDot} />
            <Text style={styles.label}>결론 방식</Text>
          </View>
          <View style={styles.row}>
            {CONCLUSIONS.map(c => (
              <TouchableOpacity
                key={c.id}
                style={[styles.chip, conclusion === c.id && styles.chipActive]}
                onPress={() => setConclusion(c.id)}
              >
                <Text style={[styles.chipText, conclusion === c.id && styles.chipTextActive]}>
                  {c.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* 참여 의원 선택 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <View style={styles.sectionDot} />
            <Text style={styles.label}>참여 의원  <Text style={styles.labelCount}>{activeMembers.length}/{MEMBERS.length}명</Text></Text>
          </View>
          <View style={styles.memberGrid}>
            {MEMBERS.map(m => (
              <TouchableOpacity
                key={m.id}
                style={[styles.memberCard, memberFlags[m.id] && { borderColor: m.color, backgroundColor: m.color + "18" }]}
                onPress={() => toggleMember(m.id)}
                activeOpacity={0.8}
              >
                <Text style={styles.memberAvatar}>{m.avatar}</Text>
                <Text style={[styles.memberName, { color: memberFlags[m.id] ? m.color : COLORS.textMuted }]}>
                  {m.name}
                </Text>
                <View style={[styles.memberCheck, memberFlags[m.id] && { backgroundColor: m.color }]}>
                  {memberFlags[m.id] && <Text style={styles.memberCheckText}>✓</Text>}
                </View>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* 시작 버튼 */}
        <TouchableOpacity
          style={[styles.startBtn, (!issue.trim() || activeMembers.length < 2) && styles.startBtnDisabled]}
          onPress={handleStart}
          activeOpacity={0.8}
          disabled={!issue.trim() || activeMembers.length < 2}
        >
          <View style={styles.startBtnInner}>
            <Text style={styles.startGavel}>⚖</Text>
            <Text style={styles.startText}>토론 개회</Text>
          </View>
        </TouchableOpacity>

        {/* 기록 보기 */}
        <TouchableOpacity style={styles.historyBtn} onPress={onShowHistory}>
          <Text style={styles.historyText}>📂  토론 보관함</Text>
        </TouchableOpacity>

        <View style={{ height: 40 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const GOLD   = "#c9a84c";
const GOLD2  = "#e8cc7a";
const NAVY   = "#080c14";
const NAVY2  = "#0c1020";
const PANEL  = "#10151f";
const PANEL2 = "#161d2b";
const SLATE  = "#1c2436";

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: NAVY },

  /* ── 헤더 ── */
  heroHeader: {
    backgroundColor: PANEL,
    alignItems: "center",
    paddingTop: 16,
    paddingBottom: 20,
    borderBottomWidth: 1,
    borderBottomColor: GOLD + "33",
  },
  heroTop:    { marginBottom: 6 },
  pillars:    { fontSize: 9, color: GOLD + "55", letterSpacing: 8 },
  heroTitle:  { fontSize: 30, fontWeight: "900", color: GOLD2, letterSpacing: 5, textShadowColor: GOLD + "44", textShadowOffset: { width: 0, height: 0 }, textShadowRadius: 8 },
  heroSub:    { fontSize: 9, color: GOLD + "77", letterSpacing: 5, marginTop: 3 },
  heroDivider:{ width: 60, height: 1, backgroundColor: GOLD + "44", marginTop: 12 },

  scroll:  { flex: 1 },
  content: { padding: 18, paddingTop: 20 },

  /* ── 섹션 ── */
  section:       { marginBottom: 24 },
  sectionHeader: { flexDirection: "row", alignItems: "center", marginBottom: 10 },
  sectionDot:    { width: 2, height: 14, backgroundColor: GOLD, borderRadius: 1, marginRight: 10 },
  label:         { color: GOLD2, fontSize: 11, fontWeight: "800", letterSpacing: 3 },
  labelCount:    { color: "#3a4560", fontSize: 11, fontWeight: "400" },

  input: {
    backgroundColor: PANEL,
    color: "#c8d4e8",
    borderRadius: 10, padding: 14,
    borderWidth: 1, borderColor: GOLD + "33",
    fontSize: 14, minHeight: 88, textAlignVertical: "top",
    lineHeight: 22,
  },

  /* ── 칩 ── */
  row:            { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    paddingHorizontal: 14, paddingVertical: 9, borderRadius: 8,
    borderWidth: 1, borderColor: "#2a3448",
    backgroundColor: PANEL,
  },
  chipActive:     { backgroundColor: GOLD + "22", borderColor: GOLD + "88" },
  chipText:       { color: "#4a5a78", fontSize: 12, fontWeight: "600" },
  chipTextActive: { color: GOLD2, fontWeight: "800" },

  /* ── 의원 그리드 ── */
  memberGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  memberCard: {
    width: "47%", flexDirection: "row", alignItems: "center",
    backgroundColor: PANEL, borderRadius: 10,
    borderWidth: 1, borderColor: "#1c2436",
    paddingHorizontal: 12, paddingVertical: 11, gap: 10,
  },
  memberAvatar:    { fontSize: 20 },
  memberName:      { flex: 1, fontSize: 12, fontWeight: "700" },
  memberCheck:     {
    width: 20, height: 20, borderRadius: 10,
    borderWidth: 1, borderColor: "#2a3448",
    alignItems: "center", justifyContent: "center",
  },
  memberCheckText: { color: "#fff", fontSize: 12, fontWeight: "900" },

  /* ── 시작 버튼 ── */
  startBtn: {
    borderRadius: 12, marginTop: 6, overflow: "hidden",
    backgroundColor: GOLD,
    shadowColor: GOLD, shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.3, shadowRadius: 12,
    elevation: 8,
  },
  startBtnDisabled: { opacity: 0.3 },
  startBtnInner:    { flexDirection: "row", alignItems: "center", justifyContent: "center", padding: 18, gap: 12 },
  startGavel:       { fontSize: 20 },
  startText:        { color: NAVY, fontWeight: "900", fontSize: 16, letterSpacing: 4 },

  /* ── 보관함 버튼 ── */
  historyBtn:  { alignItems: "center", marginTop: 14, padding: 14, borderRadius: 10, borderWidth: 1, borderColor: "#1c2436", backgroundColor: PANEL },
  historyText: { color: "#3a4560", fontSize: 12, letterSpacing: 2, fontWeight: "600" },
});