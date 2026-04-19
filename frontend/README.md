# 인공지능 의회 (AI Congress)
**AI Parliamentary Debate System for Android**

## 개요
Gemini, ChatGPT, Perplexity, Grok, Manus, Claude — 6개의 AI가 대의원으로 참여해 사회적 이슈를 2라운드 토론하고 음성으로 발언한 뒤 찬성/반대/기권으로 표결하는 의회 앱.

---

## 프로젝트 구조
```
ai-congress/
├── App.js                        # 루트 네비게이터
├── src/
│   ├── constants/
│   │   └── members.js            # 의원 정의 + 색상 테마
│   ├── services/
│   │   └── api.js                # Anthropic API 호출
│   ├── components/
│   │   ├── MemberCard.js         # 의원 카드 (활성/투표 상태)
│   │   ├── SpeechBubble.js       # 발언 말풍선
│   │   └── TypingIndicator.js    # 발언 준비 중 애니메이션
│   └── screens/
│       ├── InputScreen.js        # 이슈 입력
│       ├── DebateScreen.js       # 토론 진행 + TTS
│       └── VotingScreen.js       # 표결 + 결과
```

---

## 설치 및 실행

### 1. 의존성 설치
```bash
npm install
```

### 2. API 키 설정
`src/services/api.js` 상단에서 키를 입력하거나, `.env` 파일에 추가:
```
EXPO_PUBLIC_ANTHROPIC_KEY=sk-ant-xxxxxxxx
```
> ⚠️ **보안 주의**: 실제 배포 시 API 키는 반드시 백엔드 서버에서 관리하세요.

### 3. 실행
```bash
# Expo Go 앱으로 테스트
npx expo start

# Android 에뮬레이터
npx expo start --android

# APK 빌드 (EAS 필요)
npx eas build --platform android --profile preview
```

---

## 주요 기능
| 기능 | 설명 |
|------|------|
| 🎙 TTS 발언 | expo-speech로 의원마다 다른 음높이·속도로 재생 |
| 🔄 2라운드 토론 | 개회사 → 반론 순으로 자동 진행 |
| ⚖ 표결 | 토론 전체 맥락 기반 찬성/반대/기권 자동 투표 |
| 📊 의결 결과 | 가결/부결 + 비율 게이지 + 의원별 투표 이유 |
| 🔇 TTS 토글 | 조용한 환경에서 텍스트만 확인 가능 |

---

## 의원 페르소나
| 의원 | 소속 | 성향 |
|------|------|------|
| Gemini | Google DeepMind | 데이터·근거 중심 |
| ChatGPT | OpenAI | 안전·민주주의 |
| Perplexity | Perplexity AI | 팩트체크·투명성 |
| Grok | xAI | 반체제·자유주의 |
| Manus | Monica AI | 실용·자동화 |
| Claude | Anthropic | 윤리·장기 안전 |
