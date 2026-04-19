import React, { useEffect, useRef } from "react";
import { View, Text, StyleSheet, Animated } from "react-native";
import { COLORS, GENDER_ICON } from "../constants/members";

const CHAIR_TYPE_LABEL = {
  opening_declaration: "⚖ 개회 선언 · 의제 공표",
  call_speaker: "⚖ 발언 요청",
  start_debate: "⚖ 토론 개시",
  designate: "⚖ 토론자 지목",
  bridge: "⚖ 진행",
  summary: "⚖ 토론 요약 및 정리",
};

export default function SpeechBubble({ speech, member, isChair }) {
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const slideAnim = useRef(new Animated.Value(10)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fadeAnim, { toValue: 1, duration: 380, useNativeDriver: true }),
      Animated.timing(slideAnim, { toValue: 0, duration: 380, useNativeDriver: true }),
    ]).start();
  }, []);

  const chairLabel = isChair ? CHAIR_TYPE_LABEL[speech.chairType] : null;

  return (
    <Animated.View style={[styles.container, { opacity: fadeAnim, transform: [{ translateY: slideAnim }] }]}>
      <View style={styles.header}>
        <View style={[styles.avatarCircle, { backgroundColor: member.bgColor, borderColor: member.color }]}>
          <Text style={styles.avatarText}>{member.avatar}</Text>
        </View>
        <View style={styles.headerInfo}>
          <View style={styles.headerRow}>
            <Text style={[styles.memberName, { color: member.color }]}>{member.koreanName || member.name}</Text>
            {member.gender && member.gender !== "neutral" && (
              <Text style={styles.genderIcon}>{GENDER_ICON[member.gender]}</Text>
            )}
            {chairLabel && (
              <View style={styles.chairBadge}>
                <Text style={styles.chairBadgeText}>{chairLabel}</Text>
              </View>
            )}
            {speech.type === "exchange" && (
              <View style={styles.exchangeBadge}>
                <Text style={styles.exchangeText}>논박 {speech.exchangeTurn}/3</Text>
              </View>
            )}
            {speech.type === "designated" && speech.targetName && (
              <View style={styles.targetBadge}>
                <Text style={styles.targetText}>→ {speech.targetName} 지목</Text>
              </View>
            )}
          </View>
          {speech.round && (
            <Text style={styles.roundLabel}>
              {speech.round === 1 ? "Round 1 · 개회 발언" : "Round 2 · 토론"} · {member.org || ""}
            </Text>
          )}
        </View>
      </View>
      <View style={[
        styles.bubble,
        { borderLeftColor: member.color, backgroundColor: member.bgColor },
        isChair && styles.chairBubble,
      ]}>
        <Text style={[styles.speechText, isChair && styles.chairSpeechText]}>{speech.text}</Text>
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: { marginBottom: 14 },
  header: { flexDirection: "row", alignItems: "flex-start", gap: 10, marginBottom: 6 },
  avatarCircle: {
    width: 32, height: 32, borderRadius: 16, borderWidth: 1.5,
    alignItems: "center", justifyContent: "center",
  },
  avatarText: { fontSize: 15 },
  headerInfo: { flex: 1 },
  headerRow: { flexDirection: "row", alignItems: "center", gap: 5, flexWrap: "wrap" },
  memberName: { fontSize: 13, fontWeight: "700" },
  genderIcon: { fontSize: 9, color: "#555e6d" },
  chairBadge: {
    backgroundColor: "#1a1500", borderRadius: 4, paddingHorizontal: 5, paddingVertical: 1,
  },
  chairBadgeText: { fontSize: 8, color: COLORS.gold, fontWeight: "700" },
  exchangeBadge: { backgroundColor: "#1a2a1a", borderRadius: 4, paddingHorizontal: 5, paddingVertical: 1 },
  exchangeText: { fontSize: 9, color: COLORS.green, fontWeight: "700" },
  targetBadge: { backgroundColor: "#1a2a3a", borderRadius: 4, paddingHorizontal: 5, paddingVertical: 1 },
  targetText: { fontSize: 9, color: COLORS.blue, fontWeight: "700" },
  roundLabel: { fontSize: 10, color: COLORS.textMuted, marginTop: 1 },
  bubble: {
    marginLeft: 42, borderLeftWidth: 2.5,
    borderTopRightRadius: 10, borderBottomRightRadius: 10,
    paddingVertical: 11, paddingHorizontal: 13,
  },
  chairBubble: { borderLeftWidth: 3, borderStyle: "solid" },
  speechText: { fontSize: 13.5, color: COLORS.text, lineHeight: 21 },
  chairSpeechText: { color: "#f5e6c8", fontStyle: "italic" },
});
