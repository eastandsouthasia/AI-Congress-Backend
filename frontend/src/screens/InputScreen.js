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
const NAVY   = "#0a0e1a";
const NAVY2  = "#0f1420";
const PANEL  = "#141928";

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: NAVY },

  /* ── 헤더 ── */
  heroHeader: {
    backgroundColor: NAVY,
    alignItems: "center",
    paddingTop: 10,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: GOLD + "44",
  },
  heroTop:    { marginBottom: 4 },
  pillars:    { fontSize: 10, color: GOLD + "66", letterSpacing: 6 },
  heroTitle:  { fontSize: 28, fontWeight: "900", color: GOLD2, letterSpacing: 4 },
  heroSub:    { fontSize: 9, color: GOLD + "88", letterSpacing: 5, marginTop: 2 },
  heroDivider:{ width: 80, height: 1, backgroundColor: GOLD + "55", marginTop: 10 },

  scroll:  { flex: 1 },
  content: { padding: 20 },

  /* ── 섹션 ── */
  section:       { marginBottom: 22 },
  sectionHeader: { flexDirection: "row", alignItems: "center", marginBottom: 10 },
  sectionDot:    { width: 3, height: 14, backgroundColor: GOLD, borderRadius: 2, marginRight: 8 },
  label:         { color: GOLD2, fontSize: 12, fontWeight: "700", letterSpacing: 3 },
  labelCount:    { color: COLORS.textMuted, fontSize: 11, fontWeight: "400" },

  input: {
    backgroundColor: PANEL,
    color: "#e8e8e8",
    borderRadius: 8, padding: 14,
    borderWidth: 1, borderColor: GOLD + "44",
    fontSize: 14, minHeight: 80, textAlignVertical: "top",
  },

  /* ── 칩 ── */
  row:            { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip:           { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 6, borderWidth: 1, borderColor: GOLD + "44", backgroundColor: PANEL },
  chipActive:     { backgroundColor: GOLD, borderColor: GOLD },
  chipText:       { color: GOLD + "cc", fontSize: 13 },
  chipTextActive: { color: NAVY, fontWeight: "800" },

  /* ── 의원 그리드 ── */
  memberGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  memberCard: {
    width: "47%", flexDirection: "row", alignItems: "center",
    backgroundColor: PANEL, borderRadius: 8,
    borderWidth: 1, borderColor: "#ffffff11",
    paddingHorizontal: 10, paddingVertical: 10, gap: 8,
  },
  memberAvatar:    { fontSize: 20 },
  memberName:      { flex: 1, fontSize: 12, fontWeight: "600" },
  memberCheck:     { width: 18, height: 18, borderRadius: 9, borderWidth: 1, borderColor: "#ffffff33", alignItems: "center", justifyContent: "center" },
  memberCheckText: { color: "#fff", fontSize: 11, fontWeight: "900" },

  /* ── 시작 버튼 ── */
  startBtn: {
    borderRadius: 10, marginTop: 8, overflow: "hidden",
    backgroundColor: GOLD,
    borderWidth: 1, borderColor: GOLD2,
  },
  startBtnDisabled: { opacity: 0.35 },
  startBtnInner:    { flexDirection: "row", alignItems: "center", justifyContent: "center", padding: 18, gap: 10 },
  startGavel:       { fontSize: 20 },
  startText:        { color: NAVY, fontWeight: "900", fontSize: 17, letterSpacing: 3 },

  /* ── 보관함 버튼 ── */
  historyBtn:  { alignItems: "center", marginTop: 14, padding: 12, borderRadius: 8, borderWidth: 1, borderColor: GOLD + "33" },
  historyText: { color: GOLD + "99", fontSize: 13, letterSpacing: 2 },
});