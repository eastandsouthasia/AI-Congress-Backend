import React, { useState } from 'react';
import InputScreen from './src/screens/InputScreen';
import DebateScreen from './src/screens/DebateScreen';
import VotingScreen from './src/screens/VotingScreen';
import HistoryScreen from './src/screens/HistoryScreen';

export default function App() {
  const [screen, setScreen] = useState('input');
  const [debateParams, setDebateParams] = useState(null);
  const [voteResult, setVoteResult] = useState(null);

  const handleStart = (issue, maxTurns, format, conclusion, activeMembers) => {
    setDebateParams({ issue, maxTurns, format, conclusion, activeMembers });
    setScreen('debate');
  };

  const handleFinish = (result) => {
    setVoteResult(result);
    setScreen('voting');
  };

  const handleBackToInput = () => {
    setDebateParams(null);
    setVoteResult(null);
    setScreen('input');
  };

  if (screen === 'debate' && debateParams) {
    return (
      <DebateScreen
        {...debateParams}
        onFinish={handleFinish}
        onBack={handleBackToInput}
      />
    );
  }

  if (screen === 'voting' && voteResult) {
    return (
      <VotingScreen
        result={voteResult}
        onBack={handleBackToInput}
      />
    );
  }

  if (screen === 'history') {
    return (
      <HistoryScreen
        onBack={() => setScreen('input')}
      />
    );
  }

  return (
    <InputScreen
      onStart={handleStart}
      onShowHistory={() => setScreen('history')}
    />
  );
}
