import React, { useState, useEffect } from 'react';
import {
  View, Text, FlatList, TouchableOpacity,
  StyleSheet, Alert, Share, Modal, ScrollView,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { COLORS, MEMBERS } from '../constants/members';

const GOLD  = "#c9a84c";
const GOLD2 = "#e8cc7a";
const NAVY  = "#0a0e1a";
const PANEL = "#141928";

export default function HistoryScreen({ onBack }) {
  const [historyList, setHistoryList]   = useState([]);
  const [viewItem,    setViewItem]      = useState(null); // 다시보기 모달

  const loadHistory = async () => {
    try {
      const saved = await AsyncStorage.getItem('debate_history');
      if (saved) setHistoryList(JSON.parse(saved));
    } catch (e) {
      Alert.alert("오류", "기록을 불러오지 못했습니다.");
    }
  };
  useEffect(() => { loadHistory(); }, []);

  /* 삭제 */
  const deleteEntry = async (id) => {
    Alert.alert("삭제", "이 기록을 삭제하시겠습니까?", [
      { text: "취소", style: "cancel" },
      {
        text: "삭제", style: "destructive",
        onPress: async () => {
          const updated = historyList.filter(item => item.id !== id);
          setHistoryList(updated);
          await AsyncStorage.setItem('debate_history', JSON.stringify(updated));
        }
      }
    ]);
  };

  /* 전체 삭제 */
  const clearAll = async () => {
    Alert.alert("전체 삭제", "모든 토론 기록을 삭제하시겠습니까?", [
      { text: "취소", style: "cancel" },
      {
        text: "전체 삭제", style: "destructive",
        onPress: async () => {
          await AsyncStorage.removeItem('debate_history');
          setHistoryList([]);
        }
      }
    ]);
  };

  /* 공유 */
  const shareEntry = async (item) => {
    try {
      await Share.share({ message: item.content, title: `AI 의회: ${item.issue}` });
    } catch (e) {
      Alert.alert("오류", "공유할 수 없습니다.");
    }
  };

  const getResultColor = (result) => {
    if (result === "가결")   return COLORS.success;
    if (result === "부결")   return COLORS.error;
    if (result === "결의안") return COLORS.accent;
    return COLORS.textMuted;
  };

  /* 다시보기 — 텍스트를 줄 단위로 파싱해서 카드 형태로 표시 */
  const renderViewModal = () => {
    if (!viewItem) return null;
    const lines = (viewItem.content || "").split('\n');
    return (
      <Modal visible animationType="slide" onRequestClose={() => setViewItem(null)}>
        <View style={styles.modalSafe}>
          <View style={styles.modalHeader}>
            <View style={{ flex: 1 }}>
              <Text style={styles.modalTitle}>📜 토론 기록 다시보기</Text>
              <Text style={styles.modalIssue} numberOfLines={1}>{viewItem.issue}</Text>
            </View>
            <TouchableOpacity onPress={() => setViewItem(null)} style={styles.modalCloseBtn}>
              <Text style={styles.modalCloseText}>✕</Text>
            </TouchableOpacity>
          </View>
          <ScrollView style={styles.modalScroll} contentContainerStyle={{ padding: 16 }}>
            {lines.map((line, i) => {
              if (!line.trim()) return <View key={i} style={{ height: 6 }} />;
              const isHeader = line.startsWith('===') || line.startsWith('---');
              const isSpeaker = line.match(/^\[\d+\]/);
              const isResult  = line.startsWith('최종 의결') || line.startsWith('찬성') || line.startsWith('결과:');
              return (
                <Text
                  key={i}
                  style={[
                    styles.modalLine,
                    isHeader   && styles.modalLineHeader,
                    isSpeaker  && styles.modalLineSpeaker,
                    isResult   && styles.modalLineResult,
                  ]}
                >
                  {line}
                </Text>
              );
            })}
            <View style={{ height: 40 }} />
          </ScrollView>
        </View>
      </Modal>
    );
  };

  return (
    <View style={styles.container}>
      {/* 헤더 */}
      <View style={styles.header}>
        <TouchableOpacity onPress={onBack} style={styles.backBtn}>
          <Text style={styles.backText}>← 뒤로</Text>
        </TouchableOpacity>
        <Text style={styles.title}>📂  토론 보관함</Text>
        {historyList.length > 0 && (
          <TouchableOpacity onPress={clearAll} style={styles.clearBtn}>
            <Text style={styles.clearText}>전체삭제</Text>
          </TouchableOpacity>
        )}
      </View>

      <Text style={styles.countText}>총 {historyList.length}건 저장됨</Text>

      <FlatList
        data={historyList}
        keyExtractor={(item) => item.id.toString()}
        renderItem={({ item }) => (
          <View style={styles.card}>
            <View style={styles.cardTop}>
              <Text style={styles.date}>{item.date}</Text>
              {item.result && (
                <View style={[styles.resultBadge, { borderColor: getResultColor(item.result) }]}>
                  <Text style={[styles.resultText, { color: getResultColor(item.result) }]}>
                    {item.result}
                  </Text>
                </View>
              )}
            </View>
            <Text style={styles.issue} numberOfLines={2}>{item.issue}</Text>

            {/* 버튼 3개 */}
            <View style={styles.actions}>
              <TouchableOpacity style={styles.btn} onPress={() => setViewItem(item)}>
                <Text style={styles.btnText}>👁 다시보기</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.btn} onPress={() => shareEntry(item)}>
                <Text style={styles.btnText}>📤 공유</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.btn, styles.delBtn]} onPress={() => deleteEntry(item.id)}>
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

      {renderViewModal()}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: NAVY, paddingHorizontal: 16 },

  header: { flexDirection: 'row', alignItems: 'center', marginBottom: 8, marginTop: 50, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: GOLD + "33" },
  backBtn:  { paddingVertical: 8, paddingRight: 12 },
  backText: { color: GOLD, fontSize: 15, fontWeight: "600" },
  title:    { flex: 1, fontSize: 17, fontWeight: 'bold', color: GOLD2, letterSpacing: 2 },
  clearBtn: { paddingVertical: 6, paddingHorizontal: 10, backgroundColor: COLORS.error + "22", borderRadius: 6, borderWidth: 1, borderColor: COLORS.error + "44" },
  clearText:{ color: COLORS.error, fontSize: 11, fontWeight: "700" },
  countText:{ color: COLORS.textMuted, fontSize: 11, marginBottom: 12, letterSpacing: 1 },

  card: {
    backgroundColor: PANEL,
    padding: 14, borderRadius: 10, marginBottom: 10,
    borderWidth: 1, borderColor: GOLD + "22",
    borderLeftWidth: 3, borderLeftColor: GOLD + "66",
  },
  cardTop:     { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  date:        { color: COLORS.textMuted, fontSize: 11 },
  resultBadge: { borderWidth: 1, borderRadius: 5, paddingHorizontal: 7, paddingVertical: 2 },
  resultText:  { fontSize: 11, fontWeight: "700" },
  issue:       { color: "#e8e8e8", fontSize: 14, fontWeight: '600', marginBottom: 12, lineHeight: 20 },

  actions: { flexDirection: 'row', gap: 7 },
  btn: {
    flex: 1, backgroundColor: "#1c2235",
    padding: 9, borderRadius: 7, alignItems: 'center',
    borderWidth: 1, borderColor: GOLD + "33",
  },
  delBtn:  { backgroundColor: COLORS.error + '18', borderColor: COLORS.error + '44' },
  btnText: { color: GOLD2, fontSize: 11, fontWeight: '700' },

  emptyWrap: { alignItems: 'center', marginTop: 80 },
  emptyIcon: { fontSize: 44, marginBottom: 14 },
  empty:     { color: COLORS.textMuted, fontSize: 16, fontWeight: '600' },
  emptySub:  { color: COLORS.textMuted, fontSize: 12, marginTop: 6 },

  /* 다시보기 모달 */
  modalSafe:    { flex: 1, backgroundColor: NAVY },
  modalHeader:  {
    flexDirection: 'row', alignItems: 'center',
    padding: 16, paddingTop: 52,
    borderBottomWidth: 1, borderBottomColor: GOLD + "33",
    backgroundColor: PANEL,
  },
  modalTitle:    { color: GOLD2, fontSize: 15, fontWeight: "bold", letterSpacing: 1 },
  modalIssue:    { color: COLORS.textMuted, fontSize: 11, marginTop: 2 },
  modalCloseBtn: { padding: 8, marginLeft: 8 },
  modalCloseText:{ color: GOLD, fontSize: 18, fontWeight: "bold" },
  modalScroll:   { flex: 1 },
  modalLine:     { color: "#ccc", fontSize: 13, lineHeight: 22, marginBottom: 2 },
  modalLineHeader:  { color: GOLD + "88", fontSize: 10, letterSpacing: 1 },
  modalLineSpeaker: { color: GOLD2, fontWeight: "700", fontSize: 13, marginTop: 8 },
  modalLineResult:  { color: COLORS.success, fontWeight: "700" },
});