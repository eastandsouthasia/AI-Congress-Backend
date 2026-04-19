// ──────────────────────────────────────────────
// TTS — 의원별 완전히 다른 목소리
//
// Android TTS pitch/rate 범위: 0.1 ~ 2.0
// 성별 구분:
//   여성: pitch 1.10~1.35 (높음)
//   남성: pitch 0.50~0.95 (낮음)
// 속도 구분:
//   느림: rate 0.80~0.90
//   보통: rate 0.95~1.05
//   빠름: rate 1.15~1.25
//
// 6명 목소리 프로파일:
//   제미나이  — 남성, 중저음(0.82), 보통(0.97)
//   챗지피티  — 남성, 저음(0.65),  느림(0.88)  ← 가장 낮고 느림
//   퍼플렉시티 — 여성, 고음(1.35),  빠름(1.15)  ← 가장 높고 빠름
//   그록      — 남성, 초저음(0.50), 매우느림(0.82) ← 가장 낮고 무거움
//   마누스    — 남성, 중음(0.95),   매우빠름(1.25) ← 빠르고 에너지넘침
//   클로드    — 여성, 중고음(1.10), 느림(0.90)   ← 따뜻하고 차분
// ──────────────────────────────────────────────
import * as Speech from "expo-speech";

export function stopSpeech() {
  Speech.stop();
}

export function speakAs(text, voiceParams) {
  return new Promise((resolve) => {
    Speech.stop();
    setTimeout(() => {
      Speech.speak(text, {
        language: "ko-KR",
        pitch: voiceParams.pitch,
        rate: voiceParams.rate,
        onDone: resolve,
        onError: resolve,
        onStopped: resolve,
      });
    }, 120);
  });
}
