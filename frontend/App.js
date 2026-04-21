import React, { useState } from "react";
import { View, StyleSheet, StatusBar } from "react-native";
import { COLORS, MEMBERS } from "./src/constants/members";
import InputScreen from "./src/screens/InputScreen";
import DebateScreen from "./src/screens/DebateScreen";
import VotingScreen from "./src/screens/VotingScreen";
import HistoryScreen from "./src/screens/HistoryScreen";

export default function App() {
  const [screen, setScreen]           = useState("input");
  const [issue, setIssue]             = useState("");
  const [duration, setDuration]       = useState(15);
  const [debateFormat, setDebateFormat]     = useState("릴레이");
  const [conclusionType, setConclusionType] = useState("VOTE");
  // ✅ 참여 의원 ID 목록 (기본: 전원)
  const [activeMembers, setActiveMembers]   = useState(MEMBERS.map(m => m.id));
  const [result, setResult]           = useState(null);
  const [debateHistory, setDebateHistory] = useState([]);

  // ✅ activeMembers까지 5번째 파라미터로 수신
  const handleStart = (
    submittedIssue,
    submittedDuration,
    submittedFormat,
    submittedConclusion,
    submittedMembers,
  ) => {
    setIssue(submittedIssue);
    setDuration(submittedDuration || 15);
    setDebateFormat(submittedFormat || "릴레이");
    setConclusionType(submittedConclusion || "VOTE");
    setActiveMembers(submittedMembers || MEMBERS.map(m => m.id));
    setScreen("debate");
  };

  const handleFinish = (finalResult) => {
    setResult(finalResult);
    setDebateHistory(finalResult?.history || []);
    setScreen("voting");
  };

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor={COLORS.background} />

      {screen === "input" && (
        <InputScreen
          onStart={handleStart}
          onShowHistory={() => setScreen("history")}
        />
      )}

      {screen === "debate" && (
        <DebateScreen
          issue={issue}
          duration={duration}
          debateFormat={debateFormat}
          conclusionType={conclusionType}
          activeMembers={activeMembers}   // ✅ 추가
          onFinish={handleFinish}
        />
      )}

      {screen === "voting" && (
        <VotingScreen
          issue={issue}
          result={result}
          history={debateHistory}
          members={MEMBERS}
          onClose={() => { setResult(null); setDebateHistory([]); setScreen("input"); }}
        />
      )}

      {screen === "history" && (
        <HistoryScreen
          onBack={() => setScreen("input")}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: COLORS.background },
});