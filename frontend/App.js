import React, { useState } from "react";
import { View, StyleSheet, StatusBar } from "react-native";
import { COLORS, MEMBERS } from "./src/constants/members";
import InputScreen from "./src/screens/InputScreen";
import DebateScreen from "./src/screens/DebateScreen";
import VotingScreen from "./src/screens/VotingScreen";
import HistoryScreen from "./src/screens/HistoryScreen";

export default function App() {
  const [screen, setScreen]               = useState("input");
  const [issue, setIssue]                 = useState("");
  const [maxTurns, setMaxTurns]           = useState(25);   // duration → maxTurns
  const [debateFormat, setDebateFormat]   = useState("릴레이");
  const [conclusionType, setConclusionType] = useState("VOTE");
  const [activeMembers, setActiveMembers] = useState(MEMBERS.map(m => m.id));
  const [result, setResult]               = useState(null);
  const [debateHistory, setDebateHistory] = useState([]);
  const [conviction, setConviction]       = useState({});

  const handleStart = (
    submittedIssue,
    submittedMaxTurns,
    submittedFormat,
    submittedConclusion,
    submittedMembers,
  ) => {
    setIssue(submittedIssue);
    setMaxTurns(submittedMaxTurns || 25);
    setDebateFormat(submittedFormat || "릴레이");
    setConclusionType(submittedConclusion || "VOTE");
    setActiveMembers(submittedMembers || MEMBERS.map(m => m.id));
    setScreen("debate");
  };

  const handleFinish = (finalResult) => {
    setResult(finalResult);
    setDebateHistory(finalResult?.history || []);
    setConviction(finalResult?.conviction || {});
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
          maxTurns={maxTurns}
          debateFormat={debateFormat}
          conclusionType={conclusionType}
          activeMembers={activeMembers}
          onFinish={handleFinish}
        />
      )}

      {screen === "voting" && (
        <VotingScreen
          issue={issue}
          result={result}
          history={debateHistory}
          members={MEMBERS}
          conviction={conviction}
          onClose={() => {
            setResult(null);
            setDebateHistory([]);
            setConviction({});
            setScreen("input");
          }}
        />
      )}

      {screen === "history" && (
        <HistoryScreen onBack={() => setScreen("input")} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: COLORS.background },
});