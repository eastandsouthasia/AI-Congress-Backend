import React, { useState, useEffect } from 'react';
import {
  View, Text, FlatList, TouchableOpacity,
  StyleSheet, Alert, Share,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { COLORS } from '../constants/members';

export default function HistoryScreen({ onBack }) {
  const [historyList, setHistoryList] = useState([]);

  // ─── 데이터 불러오기 ───
  const loadHistory = async () => {
    try {
      const savedData = await AsyncStorage.getItem('debate_history');
      if (savedData) {
        // 저장된 순서 그대로 사용 (이미 최신순으로 저장됨)
        setHistoryList(JSON.parse(savedData));
      }
    } catch (e) {
      Alert.alert("오류", "기록을 불러오지 못했습니다.");
    }
  };

  useEffect(() => { loadHistory(); }, []);

  // ─── 삭제 기능 (버그 수정) ───
  const deleteEntry = async (id) => {
    Alert.alert("삭제", "이 기록을 삭제하시겠습니까?", [
      { text: "취소", style: "cancel" },
      {
        text: "삭제",
        style: "destructive",
        onPress: async () => {
          try {
            // ✅ filter 후 새 배열을 만들어 저장 (reverse() 제거 — 원본 배열 변형 버그 수정)
            const updated = historyList.filter(item => item.id !== id);
            setHistoryList(updated);
            await AsyncStorage.setItem('debate_history', JSON.stringify(updated));
          } catch (e) {
            Alert.alert("오류", "삭제에 실패했습니다.");
          }
        }
      }
    ]);
  };

  // ─── 전체 기록 삭제 ───
  const clearAll = async () => {
    Alert.alert("전체 삭제", "모든 토론 기록을 삭제하시겠습니까?", [
      { text: "취소", style: "cancel" },
      {
        text: "전체 삭제",
        style: "destructive",
        onPress: async () => {
          try {
            await AsyncStorage.removeItem('debate_history');
            setHistoryList([]);
          } catch (e) {
            Alert.alert("오류", "삭제에 실패했습니다.");
          }
        }
      }
    ]);
  };

  // ─── 공유하기 ───
  const shareEntry = async (item) => {
    try {
      await Share.share({
        message: item.content,
        title:   `AI 의회 토론 기록: ${item.issue}`,
      });
    } catch (e) {
      Alert.alert("오류", "공유할 수 없습니다.");
    }
  };

  // ─── 결과 배지 색상 ───
  const getResultColor = (result) => {
    if (result === "가결")  return COLORS.success;
    if (result === "부결")  return COLORS.error;
    if (result === "결의안") return COLORS.accent;
    return COLORS.textMuted;
  };

  return (
    <View style={styles.container}>
      {/* 헤더 */}
      <View style={styles.header}>
        <TouchableOpacity onPress={onBack} style={styles.backBtn}>
          <Text style={styles.backText}>← 뒤로</Text>
        </TouchableOpacity>
        <Text style={styles.title}>📂 토론 보관함</Text>
        {historyList.length > 0 && (
          <TouchableOpacity onPress={clearAll} style={styles.clearBtn}>
            <Text style={styles.clearText}>전체삭제</Text>
          </TouchableOpacity>
        )}
      </View>

      <Text style={styles.countText}>
        총 {historyList.length}건 저장됨
      </Text>

      <FlatList
        data={historyList}
        keyExtractor={(item) => item.id.toString()}
        renderItem={({ item }) => (
          <View style={styles.card}>
            <View style={styles.cardTop}>
              <Text style={styles.date}>{item.date}</Text>
              {/* ✅ 가결/부결/결의안 배지 */}
              {item.result && (
                <View style={[styles.resultBadge, { borderColor: getResultColor(item.result) }]}>
                  <Text style={[styles.resultText, { color: getResultColor(item.result) }]}>
                    {item.result}
                  </Text>
                </View>
              )}
            </View>
            <Text style={styles.issue} numberOfLines={2}>{item.issue}</Text>
            <View style={styles.actions}>
              <TouchableOpacity
                onPress={() => shareEntry(item)}
                style={styles.btn}
              >
                <Text style={styles.btnText}>📤 공유/보기</Text>
              </TouchableOpacity>
              <TouchableOpacity
                onPress={() => deleteEntry(item.id)}
                style={[styles.btn, styles.delBtn]}
              >
                <Text style={styles.btnText}>🗑 삭제</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}
        ListEmptyComponent={
          <View style={styles.emptyWrap}>
            <Text style={styles.emptyIcon}>📭</Text>
            <Text style={styles.empty}>저장된 토론 기록이 없습니다.</Text>
            <Text style={styles.emptySub}>토론이 종료되면 자동으로 저장됩니다.</Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background, padding: 16 },
  header:    {
    flexDirection: 'row', alignItems: 'center',
    marginBottom: 8, marginTop: 44,
  },
  backBtn:   { paddingVertical: 8, paddingRight: 12 },
  backText:  { color: COLORS.accent, fontSize: 16 },
  title:     { flex: 1, fontSize: 18, fontWeight: 'bold', color: COLORS.text },
  clearBtn:  { paddingVertical: 6, paddingHorizontal: 10, backgroundColor: COLORS.error + "33", borderRadius: 6 },
  clearText: { color: COLORS.error, fontSize: 12, fontWeight: "600" },
  countText: { color: COLORS.textMuted, fontSize: 12, marginBottom: 12 },

  card: {
    backgroundColor: COLORS.card,
    padding: 14, borderRadius: 10,
    marginBottom: 10,
    borderWidth: 1, borderColor: COLORS.border2,
  },
  cardTop:   { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  date:      { color: COLORS.textMuted, fontSize: 11 },
  resultBadge: {
    borderWidth: 1, borderRadius: 6,
    paddingHorizontal: 7, paddingVertical: 2,
  },
  resultText: { fontSize: 11, fontWeight: "700" },
  issue:     { color: COLORS.text, fontSize: 15, fontWeight: '600', marginBottom: 12 },
  actions:   { flexDirection: 'row', gap: 8 },
  btn:       {
    flex: 1, backgroundColor: COLORS.surface2,
    padding: 9, borderRadius: 7, alignItems: 'center',
    borderWidth: 1, borderColor: COLORS.border,
  },
  delBtn:    { backgroundColor: COLORS.error + '22', borderColor: COLORS.error + '55' },
  btnText:   { color: COLORS.text, fontSize: 12, fontWeight: '600' },

  emptyWrap: { alignItems: 'center', marginTop: 60 },
  emptyIcon: { fontSize: 40, marginBottom: 12 },
  empty:     { color: COLORS.textMuted, fontSize: 16, fontWeight: '600' },
  emptySub:  { color: COLORS.textMuted, fontSize: 12, marginTop: 6 },
});