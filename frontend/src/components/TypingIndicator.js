import React, { useEffect, useRef } from "react";
import { View, Text, StyleSheet, Animated } from "react-native";
import { GENDER_ICON } from "../constants/members";

export default function TypingIndicator({ member }) {
  const anims = [
    useRef(new Animated.Value(0)).current,
    useRef(new Animated.Value(0)).current,
    useRef(new Animated.Value(0)).current,
  ];

  useEffect(() => {
    const loops = anims.map((anim, i) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(i * 140),
          Animated.timing(anim, { toValue: -5, duration: 280, useNativeDriver: true }),
          Animated.timing(anim, { toValue: 0, duration: 280, useNativeDriver: true }),
        ])
      )
    );
    loops.forEach((l) => l.start());
    return () => loops.forEach((l) => l.stop());
  }, []);

  return (
    <View style={styles.container}>
      <View style={[styles.circle, { backgroundColor: member.bgColor, borderColor: member.color }]}>
        <Text style={styles.avatar}>{member.avatar}</Text>
      </View>
      <View style={[styles.bubble, { backgroundColor: member.bgColor, borderLeftColor: member.color }]}>
        <View style={styles.row}>
          {anims.map((anim, i) => (
            <Animated.View
              key={i}
              style={[styles.dot, { backgroundColor: member.color, transform: [{ translateY: anim }] }]}
            />
          ))}
        </View>
      </View>
      <Text style={styles.label}>
        {member.koreanName || member.name} {GENDER_ICON[member.gender]} 발언 준비 중...
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 14 },
  circle: {
    width: 32, height: 32, borderRadius: 16, borderWidth: 1.5,
    alignItems: "center", justifyContent: "center",
  },
  avatar: { fontSize: 15 },
  bubble: {
    borderLeftWidth: 2.5, borderTopRightRadius: 10, borderBottomRightRadius: 10,
    paddingVertical: 13, paddingHorizontal: 14,
  },
  row: { flexDirection: "row", gap: 4, alignItems: "center" },
  dot: { width: 6, height: 6, borderRadius: 3 },
  label: { fontSize: 10, color: "#555e6d", flex: 1 },
});
