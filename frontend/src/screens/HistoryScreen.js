import React, { useState, useEffect } from 'react';
import {
  View, Text, FlatList, TouchableOpacity,
  StyleSheet, Alert, Share, Modal, ScrollView,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { COLORS, MEMBERS } from '../constants/members';

const GOLD  = "#c9a84c";
const GOLD2 = "#e8cc7a";
const NAVY  = "#080c14";
const PANEL = "#10151f";
const PANEL2 = "#161d2b";
const SLATE = "#1c2436";

export default function HistoryScreen({ onBack }) {
  const [historyList, setHistoryList]   = useState([]);
  const [viewItem,    setViewItem]      = useState(null);

  const loadHistory = async () => {
    try {
      const saved = await AsyncStorage.getItem('debate_history');
      if (saved) setHistoryList(JSON.parse(saved));
    } catch (e) {
      Alert.alert("오류", "기록을 불러오지 못했습니다.");
    }
  };
  useEffect(() => { loadHistory(); }, []);

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

  const shareEntry = async (item) => {
    try {
      await Share.share({ message: item.content, title: `AI 의회: ${item.issue}` });
    } catch (e) {
      Alert.alert("오류", "공유할 수 없습니다.");
    }
  };

  const getResultColor = (result) => {
    if (result === "가결")   return "#27ae60";
    if (result === "부결")   return "#e74c3c";
    if (result === "결의안") return GOLD;
    return COLORS.textMuted;
  };

  /* 다시보기 모달 — 줄 단위 파싱으로 회의록 표시 */
  const renderViewModal = () => {
    if (!viewItem) return null;
    const lines = (viewItem.content || "").split('\n');
    return (
      <Modal visible animationType="slide" onRequestClose={() => setViewItem(null)}>
        <View style={styles.modalSafe}>
          <View style={styles.modalHeader}>
            <View style={{ flex: 1 }}>
              <Text style={styles.modalTitle}>📜 토론 회의록</Text>
              <Text style={styles.modalIssue} numberOfLines={1}>{viewItem.issue}</Text>
              <Text style={styles.modalDate}>{viewItem.date}</Text>
            </View>
            <TouchableOpacity onPress={() => setViewItem(null)} style={styles.modalCloseBtn}>
              <Text style={styles.modalCloseText}>✕ 닫기</Text>
            </TouchableOpacity>
          </View>
          <ScrollView style={styles.modalScroll} contentContainerStyle={{ padding: 16 }}>
            {lines.map((line, i) => {
              if (!line.trim()) return <View key={i} style={{ height: 5 }} />;
              const isHeader   = line.startsWith('===') || line.startsWith('---') || line.startsWith('══');
              const isSpeaker  = line.match(/^\[\d+\]/);
              const isResult   = line.startsWith('최종 의결') || line.startsWith('찬성') || line.startsWith('결과:') || line.startsWith('▶');
              const isSection  = line.startsWith('【') || line.startsWith('  【');
              return (
                <Text
                  key={i}
                  style={[
                    styles.modalLine,
                    isHeader  && styles.modalLineHeader,
                    isSpeaker && styles.modalLineSpeaker,
                    isResult  && styles.modalLineResult,
                    isSection && styles.modalLineSection,
                  ]}
                >
                  {line}
                </Text>
              );
            })}
            <View style={{ height: 50 }} />
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
        contentContainerStyle={{ paddingHorizontal: 14 }}
        renderItem={({ item }) => (
          <View style={styles.card}>
            <View style={styles.cardTop}>
              <Text style={styles.date}>{item.date}</Text>
              {item.result && (
                <View style={[styles.resultBadge, { borderColor: getResultColor(item.result) + "88", backgroundColor: getResultColor(item.result) + "14" }]}>
                  <Text style={[styles.resultText, { color: getResultColor(item.result) }]}>
                    {item.result}
                  </Text>
                </View>
              )}
            </View>
            <Text style={styles.issue} numberOfLines={2}>{item.issue}</Text>

            <View style={styles.actions}>
              <TouchableOpacity style={styles.btn} onPress={() => setViewItem(item)}>
                <Text style={styles.btnIcon}>👁</Text>
                <Text style={styles.btnText}>다시보기</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.btn} onPress={() => shareEntry(item)}>
                <Text style={styles.btnIcon}>📤</Text>
                <Text style={styles.btnText}>공유</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.btn, styles.delBtn]} onPress={() => deleteEntry(item.id)}>
                <Text style={styles.btnIcon}>🗑</Text>
                <Text style={[styles.btnText, { color: "#e74c3c" }]}>삭제</Text>
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
  container: { flex: 1, backgroundColor: NAVY },

  header: {
    flexDirection: 'row', alignItems: 'center',
    marginTop: 52, paddingHorizontal: 16, paddingBottom: 14,
    borderBottomWidth: 1, borderBottomColor: GOLD + "22",
    backgroundColor: PANEL,
  },
  backBtn:  { paddingVertical: 8, paddingRight: 14 },
  backText: { color: GOLD, fontSize: 14, fontWeight: "700" },
  title:    { flex: 1, fontSize: 15, fontWeight: 'bold', color: GOLD2, letterSpacing: 2 },
  clearBtn: { paddingVertical: 6, paddingHorizontal: 10, backgroundColor: "#e74c3c14", borderRadius: 6, borderWidth: 1, borderColor: "#e74c3c33" },
  clearText:{ color: "#e74c3c", fontSize: 11, fontWeight: "700" },
  countText:{ color: "#2a3550", fontSize: 10, marginBottom: 10, letterSpacing: 1, marginTop: 10, paddingHorizontal: 16 },

  card: {
    backgroundColor: PANEL,
    padding: 14, borderRadius: 12, marginBottom: 10,
    borderWidth: 1, borderColor: "#1c2436",
    borderLeftWidth: 3, borderLeftColor: GOLD + "44",
  },
  cardTop:     { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 7 },
  date:        { color: "#2a3550", fontSize: 10, fontWeight: "600" },
  resultBadge: { borderWidth: 1, borderRadius: 5, paddingHorizontal: 8, paddingVertical: 3 },
  resultText:  { fontSize: 10, fontWeight: "800", letterSpacing: 1 },
  issue:       { color: "#b8c8e0", fontSize: 13, fontWeight: '700', marginBottom: 14, lineHeight: 20 },

  actions: { flexDirection: 'row', gap: 8 },
  btn: {
    flex: 1, backgroundColor: PANEL2,
    paddingVertical: 9, borderRadius: 8, alignItems: 'center',
    borderWidth: 1, borderColor: SLATE,
    flexDirection: 'column', gap: 2,
  },
  delBtn: { backgroundColor: "#160c0c", borderColor: "#e74c3c22" },
  btnIcon: { fontSize: 14 },
  btnText: { color: GOLD2, fontSize: 10, fontWeight: '700' },

  emptyWrap: { alignItems: 'center', marginTop: 100 },
  emptyIcon: { fontSize: 48, marginBottom: 16, opacity: 0.5 },
  empty:     { color: "#2a3550", fontSize: 15, fontWeight: '700' },
  emptySub:  { color: "#1e2840", fontSize: 11, marginTop: 6 },

  /* 다시보기 모달 */
  modalSafe:    { flex: 1, backgroundColor: NAVY },
  modalHeader:  {
    flexDirection: 'row', alignItems: 'flex-start',
    padding: 16, paddingTop: 54,
    borderBottomWidth: 1, borderBottomColor: GOLD + "22",
    backgroundColor: PANEL,
    gap: 12,
  },
  modalTitle:    { color: GOLD2, fontSize: 14, fontWeight: "800", letterSpacing: 1 },
  modalIssue:    { color: "#4a5a78", fontSize: 11, marginTop: 3 },
  modalDate:     { color: "#2a3550", fontSize: 10, marginTop: 2 },
  modalCloseBtn: { paddingHorizontal: 12, paddingVertical: 7, backgroundColor: PANEL2, borderRadius: 8, borderWidth: 1, borderColor: GOLD + "33" },
  modalCloseText:{ color: GOLD, fontSize: 12, fontWeight: "700" },
  modalScroll:   { flex: 1 },
  modalLine:        { color: "#6878a0", fontSize: 12, lineHeight: 20, marginBottom: 1 },
  modalLineHeader:  { color: "#2a3550", fontSize: 9, letterSpacing: 2, marginVertical: 4 },
  modalLineSpeaker: { color: GOLD2, fontWeight: "800", fontSize: 12, marginTop: 10 },
  modalLineResult:  { color: "#27ae60", fontWeight: "800", fontSize: 12 },
  modalLineSection: { color: GOLD + "aa", fontWeight: "700", fontSize: 11, letterSpacing: 1, marginTop: 8 },
});