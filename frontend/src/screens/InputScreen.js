/**
 * InputScreen.js - 토론 시간 60분으로 확장
 *
 * 변경사항:
 * - DURATIONS: 최대 30분 → 60분으로 확장
 * - 기본값: 15분 유지
 * - 안건 입력, 형식 선택, 의원 선택 기존 로직 유지
 *
 * ⚠️ 이 파일은 원본 InputScreen.js를 대체합니다.
 *    원본에서 MEMBERS import 및 스타일 부분은 그대로 유지하고
 *    DURATIONS 배열과 관련 UI 텍스트만 아래와 같이 수정하세요.
 */

// ── 수정 대상 1: DURATIONS 배열 ──────────────────────────────
// 기존:
// const DURATIONS = [5, 10, 15, 20, 30];
//
// 변경 후:
export const DURATIONS = [10, 15, 20, 30, 45, 60];
//
// ── 수정 대상 2: 시간 선택 UI 레이블 ─────────────────────────
// 기존 30분 버튼 옆에 "(최대)" 표시가 있었다면 60분으로 이동
//
// ── 수정 대상 3: 안내 텍스트 (있다면) ────────────────────────
// "최대 30분" → "최대 60분"
//
// ────────────────────────────────────────────────────────────
// 아래는 원본 구조를 가정한 참고용 전체 예시입니다.
// 실제 원본 파일의 스타일/구조를 우선 적용하세요.
// ────────────────────────────────────────────────────────────

import React, { useState } from "react";
import {
  View, Text, TextInput, TouchableOpacity,
  ScrollView, StyleSheet, SafeAreaView, Switch,
} from "react-native";
import { COLORS, MEMBERS } from "../constants/members";

// ✅ 60분까지 확장
const DURATIONS = [10, 15, 20, 30, 45, 60];
// ✅ 백엔드 debate_engine dispatch 키와 정확히 일치해야 함
// 기존 "찬반"→미지원, "자유"→"자유토론" 으로 수정
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
      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled">

        <Text style={styles.title}>🏛 AI 의회</Text>
        <Text style={styles.sub}>안건을 입력하고 토론을 시작하세요</Text>

        {/* 안건 입력 */}
        <View style={styles.section}>
          <Text style={styles.label}>📋 안건</Text>
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

        {/* 토론 시간 — 최대 60분 */}
        <View style={styles.section}>
          <Text style={styles.label}>⏱ 토론 시간 (최대 60분)</Text>
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
          <Text style={styles.label}>🎙 토론 형식</Text>
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
          <Text style={styles.label}>⚖ 결론 방식</Text>
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
          <Text style={styles.label}>🧑‍💼 참여 의원 ({activeMembers.length}/{MEMBERS.length}명)</Text>
          {MEMBERS.map(m => (
            <View key={m.id} style={styles.memberRow}>
              <Text style={[styles.memberName, { color: m.color }]}>
                {m.avatar} {m.name}
              </Text>
              <Switch
                value={memberFlags[m.id]}
                onValueChange={() => toggleMember(m.id)}
                trackColor={{ false: COLORS.border, true: COLORS.blue }}
                thumbColor={memberFlags[m.id] ? COLORS.accent : COLORS.textMuted}
              />
            </View>
          ))}
        </View>

        {/* 시작 버튼 */}
        <TouchableOpacity
          style={[styles.startBtn, (!issue.trim() || activeMembers.length < 2) && styles.startBtnDisabled]}
          onPress={handleStart}
          activeOpacity={0.8}
          disabled={!issue.trim() || activeMembers.length < 2}
        >
          <Text style={styles.startText}>🏛 토론 시작</Text>
        </TouchableOpacity>

        {/* 기록 보기 */}
        <TouchableOpacity style={styles.historyBtn} onPress={onShowHistory}>
          <Text style={styles.historyText}>📂 토론 기록 보기</Text>
        </TouchableOpacity>

        <View style={{ height: 40 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:    { flex: 1, backgroundColor: COLORS.background },
  scroll:  { flex: 1 },
  content: { padding: 20 },
  title:   { color: COLORS.text, fontSize: 26, fontWeight: "900", textAlign: "center", marginTop: 10 },
  sub:     { color: COLORS.textMuted, fontSize: 13, textAlign: "center", marginBottom: 24 },

  section: { marginBottom: 20 },
  label:   { color: COLORS.textDim, fontSize: 11, letterSpacing: 2, marginBottom: 8 },

  input: {
    backgroundColor: COLORS.card,
    color: COLORS.text,
    borderRadius: 10, padding: 14,
    borderWidth: 1, borderColor: COLORS.border2,
    fontSize: 14, minHeight: 80, textAlignVertical: "top",
  },

  row:          { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip:         {
    paddingHorizontal: 14, paddingVertical: 8,
    borderRadius: 20, borderWidth: 1,
    borderColor: COLORS.border2, backgroundColor: COLORS.card,
  },
  chipActive:   { backgroundColor: COLORS.blue, borderColor: COLORS.blue },
  chipText:     { color: COLORS.textDim, fontSize: 13 },
  chipTextActive: { color: "#fff", fontWeight: "700" },

  memberRow: {
    flexDirection: "row", justifyContent: "space-between",
    alignItems: "center", paddingVertical: 8,
    borderBottomWidth: 1, borderBottomColor: COLORS.border,
  },
  memberName: { fontSize: 14, fontWeight: "600" },

  startBtn: {
    backgroundColor: COLORS.blue,
    padding: 18, borderRadius: 14,
    alignItems: "center", marginTop: 8,
  },
  startBtnDisabled: { opacity: 0.4 },
  startText: { color: "#fff", fontWeight: "900", fontSize: 16, letterSpacing: 1 },

  historyBtn: { alignItems: "center", marginTop: 14, padding: 10 },
  historyText: { color: COLORS.textMuted, fontSize: 13 },
});
