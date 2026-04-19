import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { COLORS } from "../constants/members";

/**
 * MemberCard - 의원 카드 컴포넌트
 * Props:
 *   member     - 의원 객체 { id, name, avatar, color, ... }
 *   isActive   - 현재 발언 중 여부
 *   isSpeaking - TTS 재생 중 여부
 *   vote       - 투표 결과 문자열 (null | "찬성" | "반대" | "기권")
 *   isChair    - 의장 여부
 */
export default function MemberCard({ member, isActive, isSpeaking, vote, isChair }) {
  const borderColor = isActive ? member.color : COLORS.border;

  const voteColor =
    vote === "찬성" ? COLORS.success :
    vote === "반대" ? COLORS.error :
    COLORS.textMuted;

  return (
    <View style={[styles.card, { borderColor }]}>
      {/* 발언 중 깜빡이는 인디케이터 */}
      {isSpeaking && <View style={[styles.speakingDot, { backgroundColor: member.color }]} />}

      {/* 아바타 */}
      <Text style={styles.avatar}>{member.avatar}</Text>

      {/* 이름 */}
      <Text style={[styles.name, { color: member.color }]} numberOfLines={1}>
        {member.name}
      </Text>

      {/* 의장 뱃지 */}
      {isChair && (
        <View style={styles.chairBadge}>
          <Text style={styles.chairText}>의장</Text>
        </View>
      )}

      {/* 투표 결과 */}
      {vote && (
        <View style={[styles.voteBadge, { borderColor: voteColor }]}>
          <Text style={[styles.voteText, { color: voteColor }]}>{vote}</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 8,
    paddingHorizontal: 4,
    backgroundColor: COLORS.surface,
    borderRadius: 10,
    borderWidth: 1.5,
    marginHorizontal: 3,
    minWidth: 56,
    position: "relative",
  },
  speakingDot: {
    position: "absolute",
    top: 4,
    right: 4,
    width: 7,
    height: 7,
    borderRadius: 4,
  },
  avatar: {
    fontSize: 20,
    marginBottom: 4,
  },
  name: {
    fontSize: 8,
    fontWeight: "700",
    textAlign: "center",
  },
  chairBadge: {
    marginTop: 3,
    backgroundColor: "#1a1500",
    borderRadius: 4,
    paddingHorizontal: 4,
    paddingVertical: 1,
    borderWidth: 1,
    borderColor: COLORS.gold,
  },
  chairText: {
    fontSize: 7,
    color: COLORS.gold,
    fontWeight: "700",
  },
  voteBadge: {
    marginTop: 3,
    borderRadius: 4,
    paddingHorizontal: 4,
    paddingVertical: 1,
    borderWidth: 1,
  },
  voteText: {
    fontSize: 7,
    fontWeight: "700",
  },
});