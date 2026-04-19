import React, { useState } from "react";
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  ScrollView, KeyboardAvoidingView, Platform, StatusBar,
} from "react-native";
import { MEMBERS, COLORS } from "../constants/members";
import MemberCard from "../components/MemberCard";

const EXAMPLE_ISSUES = [
  "AI 기술 개발에 대한 정부 규제가 필요한가?",
  "기본소득제는 도입되어야 하는가?",
  "핵발전소 확대는 기후 위기 해결책이 될 수 있는가?",
  "소셜미디어 알고리즘은 공공재로 규제받아야 하는가?",
  "자율주행차 사고의 법적 책임은 누구에게 있는가?",
];

const DURATION_PRESETS = [10, 20, 30, 40, 60, 90];

export default function InputScreen({ onStart, onShowHistory }) {
  const [issue, setIssue] = useState("");
  const [duration, setDuration] = useState(40);
  const [customDuration, setCustomDuration] = useState("");
  const [showCustom, setShowCustom] = useState(false);

  const handlePreset = (min) => {
    setDuration(min);
    setShowCustom(false);
    setCustomDuration("");
  };

  const handleCustomInput = (val) => {
    setCustomDuration(val);
    const n = parseInt(val, 10);
    if (!isNaN(n) && n >= 1 && n <= 180) setDuration(n);
  };

  const handleStart = () => {
    if (!issue.trim()) return;
    const finalDuration = showCustom
      ? Math.max(1, Math.min(180, parseInt(customDuration, 10) || 40))
      : duration;
    onStart(issue.trim(), finalDuration);
  };

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <StatusBar barStyle="light-content" backgroundColor={COLORS.background} />
      
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.container}
        keyboardShouldPersistTaps="handled"
      >
        {/* ✅ 보관함 버튼 (원래 코드에 추가된 로직) */}
        <TouchableOpacity 
          style={styles.historyEntryBtn} 
          onPress={onShowHistory}
        >
          <Text style={styles.historyEntryText}>📂 과거 토론 기록 보기</Text>
        </TouchableOpacity>

        {/* 헤더 (기본 UI) */}
        <View style={styles.header}>
          <Text style={styles.badge}>◈ AI CONGRESS SYSTEM</Text>
          <Text style={styles.title}>인공지능 의회</Text>
          <Text style={styles.subtitle}>AI Parliamentary Debate System</Text>
        </View>

        {/* 의원 카드 리스트 */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.membersScroll}>
          {MEMBERS.map((m) => (
            <MemberCard
              key={m.id}
              member={m}
              isActive={false}
              isSpeaking={false}
              vote={null}
              isChair={false}
            />
          ))}
        </ScrollView>

        <View style={styles.divider} />

        {/* 의안 입력 섹션 */}
        <Text style={styles.sectionLabel}>◈ 의안 입력 · ISSUE</Text>
        <TextInput
          style={styles.input}
          value={issue}
          onChangeText={setIssue}
          placeholder={"사회적 의제를 입력하세요...\n예: AI 규제가 필요한가?"}
          placeholderTextColor={COLORS.textMuted}
          multiline
          textAlignVertical="top"
        />

        {/* 예시 의안 칩 */}
        <Text style={styles.exampleLabel}>예시 의안</Text>
        {EXAMPLE_ISSUES.map((ex) => (
          <TouchableOpacity
            key={ex}
            style={styles.exampleChip}
            onPress={() => setIssue(ex)}
          >
            <Text style={styles.exampleText}>{ex}</Text>
          </TouchableOpacity>
        ))}

        <View style={styles.divider} />

        {/* 토론 시간 설정 섹션 */}
        <Text style={styles.sectionLabel}>◈ 토론 시간 설정 · DURATION</Text>
        <View style={styles.presetRow}>
          {DURATION_PRESETS.map((min) => (
            <TouchableOpacity
              key={min}
              style={[
                styles.presetBtn,
                !showCustom && duration === min && styles.presetBtnActive,
              ]}
              onPress={() => handlePreset(min)}
            >
              <Text
                style={[
                  styles.presetText,
                  !showCustom && duration === min && styles.presetTextActive,
                ]}
              >
                {min}분
              </Text>
            </TouchableOpacity>
          ))}
          <TouchableOpacity
            style={[styles.presetBtn, showCustom && styles.presetBtnActive]}
            onPress={() => setShowCustom(true)}
          >
            <Text style={[styles.presetText, showCustom && styles.presetTextActive]}>
              직접
            </Text>
          </TouchableOpacity>
        </View>

        {/* 시간 직접 입력 필드 */}
        {showCustom && (
          <View style={styles.customRow}>
            <TextInput
              style={styles.customInput}
              value={customDuration}
              onChangeText={handleCustomInput}
              placeholder="1~180"
              placeholderTextColor={COLORS.textMuted}
              keyboardType="number-pad"
              maxLength={3}
            />
            <Text style={styles.customUnit}>분  (1~180분 설정 가능)</Text>
          </View>
        )}

        {/* 현재 설정 시간 표시 */}
        <View style={styles.durationDisplay}>
          <Text style={styles.durationIcon}>⏱</Text>
          <Text style={styles.durationText}>
            토론 시간:{" "}
            <Text style={{ color: COLORS.gold, fontWeight: "700" }}>
              {showCustom
                ? (parseInt(customDuration, 10) || "?") + "분"
                : `${duration}분`}
            </Text>
            {"  "}
            <Text style={styles.durationSub}>
              ({showCustom
                ? (parseInt(customDuration, 10) || 0) * 60 + "초"
                : duration * 60 + "초"})
            </Text>
          </Text>
        </View>

        {/* 의회 소집 버튼 (최종 제출) */}
        <TouchableOpacity
          style={[styles.submitBtn, !issue.trim() && styles.submitDisabled]}
          onPress={handleStart}
          disabled={!issue.trim()}
          activeOpacity={0.8}
        >
          <Text style={styles.submitText}>⚖ 의회 소집 · CONVENE CONGRESS</Text>
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex:       { flex: 1, backgroundColor: COLORS.background },
  scroll:     { flex: 1 },
  container:  { padding: 20, paddingBottom: 48 },

  header:     { alignItems: "center", marginTop: 20, marginBottom: 24 },
  badge:      { fontSize: 10, color: COLORS.blue, letterSpacing: 4, marginBottom: 8 },
  title:      { fontSize: 28, fontWeight: "800", color: COLORS.text, letterSpacing: -0.5 },
  subtitle:   { fontSize: 12, color: COLORS.textMuted, marginTop: 4, letterSpacing: 1 },

  membersScroll: { marginBottom: 24 },

  divider:    { height: 1, backgroundColor: COLORS.border, marginBottom: 20 },
  sectionLabel: { fontSize: 10, color: COLORS.blue, letterSpacing: 3, marginBottom: 12 },

  input: {
    backgroundColor: COLORS.surface,
    borderWidth: 1, borderColor: COLORS.border2,
    borderRadius: 10, padding: 16,
    color: COLORS.text, fontSize: 15,
    minHeight: 110, marginBottom: 16, lineHeight: 22,
  },
  exampleLabel: { fontSize: 10, color: COLORS.textMuted, letterSpacing: 2, marginBottom: 8 },
  exampleChip: {
    backgroundColor: COLORS.surface,
    borderWidth: 1, borderColor: COLORS.border,
    borderRadius: 8, padding: 11, marginBottom: 7,
  },
  exampleText: { fontSize: 13, color: COLORS.textDim, lineHeight: 18 },

  presetRow:      { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 },
  presetBtn: {
    backgroundColor: COLORS.surface,
    borderWidth: 1, borderColor: COLORS.border2,
    borderRadius: 8, paddingVertical: 9, paddingHorizontal: 14,
  },
  presetBtnActive:  { backgroundColor: "#1a1500", borderColor: COLORS.gold },
  presetText:       { fontSize: 13, color: COLORS.textMuted, fontWeight: "600" },
  presetTextActive: { color: COLORS.gold },

  customRow:    { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 12 },
  customInput: {
    backgroundColor: COLORS.surface,
    borderWidth: 1.5, borderColor: COLORS.gold,
    borderRadius: 8, paddingHorizontal: 14, paddingVertical: 9,
    color: COLORS.gold, fontSize: 18, fontWeight: "700",
    width: 72, textAlign: "center",
  },
  customUnit:   { fontSize: 13, color: COLORS.textMuted },

  durationDisplay: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#1a1500",
    borderWidth: 1, borderColor: COLORS.gold + "55",
    borderRadius: 8, padding: 12, marginBottom: 20,
  },
  durationIcon: { fontSize: 16 },
  durationText: { fontSize: 13, color: COLORS.textDim },
  durationSub:  { fontSize: 11, color: COLORS.textMuted },

  submitBtn: {
    backgroundColor: COLORS.blue,
    borderRadius: 12, paddingVertical: 16, alignItems: "center",
  },
  submitDisabled: { backgroundColor: COLORS.surface2 },
  submitText:     { color: "#fff", fontSize: 14, fontWeight: "700", letterSpacing: 1.5 },

  // ✅ 보관함 버튼 스타일 (하단에 정확히 유지)
  historyEntryBtn: {
    backgroundColor: COLORS.surface,
    padding: 12,
    borderRadius: 10,
    marginTop: 10,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: COLORS.border2,
    alignItems: 'center',
  },
  historyEntryText: {
    color: COLORS.text,
    fontSize: 14,
    fontWeight: '600',
  },
});