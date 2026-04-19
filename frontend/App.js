import React, { useState } from "react";
import { View, StyleSheet, StatusBar } from "react-native";
import { COLORS } from "./src/constants/members";
import InputScreen from "./src/screens/InputScreen";
import DebateScreen from "./src/screens/DebateScreen";
import VotingScreen from "./src/screens/VotingScreen";
import HistoryScreen from "./src/screens/HistoryScreen"; // 새로 만들 파일 임포트

export default function App() {
  const [screen, setScreen]   = useState("input");
  const [issue, setIssue]     = useState("");
  const [duration, setDuration] = useState(40);
  const [result, setResult]   = useState(null);

  // ✅ duration도 함께 받아서 DebateScreen으로 전달
  const handleStart = (submittedIssue, submittedDuration) => {
    setIssue(submittedIssue);
    setDuration(submittedDuration || 40);
    setScreen("debate");
  };

  const handleFinish = (finalResult) => {
    setResult(finalResult);
    setScreen("voting");
  };

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor={COLORS.background} />
      
      {screen === "input" && (
        <InputScreen 
          onStart={handleStart} 
          onShowHistory={() => setScreen("history")} // 추가
        />
      )}
     
      {screen === "debate" && (
        <DebateScreen
          issue={issue}
          duration={duration}
          onFinish={handleFinish}
        />
      )}
      {screen === "voting" && (
        <VotingScreen
          issue={issue}
          result={result}
          onReset={() => { setResult(null); setScreen("input"); }}
        />
      )}
     {screen === "history" && (
        <HistoryScreen 
          onBack={() => setScreen("input")} // 다시 메인으로 돌아오는 기능
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: COLORS.background },
});