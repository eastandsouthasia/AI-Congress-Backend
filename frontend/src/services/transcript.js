// ──────────────────────────────────────────────
// 회의록 생성 및 공유
// React Native 내장 Share만 사용 (외부 패키지 없음)
// ──────────────────────────────────────────────
import { Share } from "react-native";
import { MEMBERS } from "../constants/members";

function getMemberName(id) {
  const m = MEMBERS.find((m) => m.id === id);
  return m ? `${m.koreanName || m.name} (${m.org})` : id;
}

function formatKoreanDate(date) {
  const y = date.getFullYear();
  const mo = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  const h = String(date.getHours()).padStart(2, "0");
  const mi = String(date.getMinutes()).padStart(2, "0");
  return `${y}년 ${mo}월 ${d}일 ${h}시 ${mi}분`;
}

function getSpeechTypeLabel(speech) {
  if (speech.type === "system") return null;
  if (speech.type === "chair") {
    const labels = {
      opening_declaration: "【개회 선언 · 의제 공표 · 토론 부침】",
      call_speaker:        "【발언 요청】",
      start_debate:        "【토론 개시 선언】",
      designate:           "【토론자 지목】",
      bridge:              "【진행】",
      summary:             "【토론 요약 및 정리】",
    };
    return labels[speech.chairType] || "【의장 발언】";
  }
  if (speech.type === "opening")    return "【개회 발언 · Round 1】";
  if (speech.type === "designated") return `【지목 발언 · 상대: ${speech.targetName || "?"}】`;
  if (speech.type === "exchange")   return `【논박 교환 · ${speech.exchangeTurn}/3회】`;
  if (speech.type === "statement")  return "【토론 발언】";
  return "";
}

function wrapText(text, indent, maxWidth) {
  const words = text.split(" ");
  const lines = [];
  let line = indent;
  for (const word of words) {
    if ((line + word).length > maxWidth) {
      lines.push(line.trimEnd());
      line = indent + word + " ";
    } else {
      line += word + " ";
    }
  }
  if (line.trim()) lines.push(line.trimEnd());
  return lines;
}

export function buildTranscript({
  issue, speeches, votes, chairId, counts, passed,
  sessionDate, durationMinutes,
}) {
  const chair = MEMBERS.find((m) => m.id === chairId);
  const chairLine = chair
    ? `${chair.koreanName || chair.name} (${chair.org})`
    : "미정";
  const voters = MEMBERS.filter((m) => m.id !== chairId);
  const dateStr = formatKoreanDate(sessionDate || new Date());
  const durationStr = durationMinutes ? `${durationMinutes}분` : "40분";
  const L = [];

  L.push("================================================================");
  L.push("              인공지능 의회 (AI CONGRESS)");
  L.push("                       회  의  록");
  L.push("================================================================");
  L.push("");
  L.push(`  일     시 : ${dateStr}`);
  L.push(`  의     제 : ${issue}`);
  L.push(`  토 론 시 간 : ${durationStr}`);
  L.push(`  의     장 : ${chairLine} (투표권 없음)`);
  L.push(`  참 석 자 : ${voters.map((m) => `${m.koreanName || m.name}(${m.org})`).join(", ")}`);
  L.push(`  투 표 권 자 : ${voters.length}명`);
  L.push("");
  L.push("================================================================");
  L.push("");
  L.push("[ 회의 경과 ]");
  L.push("");

  let idx = 0;
  for (const speech of speeches) {
    if (speech.type === "system") {
      L.push(`  ▶ ${speech.text}`);
      L.push("");
      continue;
    }
    idx++;
    const typeLabel = getSpeechTypeLabel(speech) || "";
    const isChair = speech.speakerId === "chair";
    const speaker = isChair
      ? `${chair?.koreanName || chair?.name || "의장"} (의장)`
      : getMemberName(speech.speakerId);

    L.push(`  [${String(idx).padStart(3, "0")}] ${typeLabel}`);
    L.push(`        발 언 자 : ${speaker}`);
    L.push(`        발 언 내 용 :`);
    wrapText(speech.text, "          ", 68).forEach((l) => L.push(l));
    L.push("");
  }

  L.push("================================================================");
  L.push("");
  L.push("[ 표결 결과 ]");
  L.push("");
  L.push(`  의     제 : ${issue}`);
  L.push(`  투 표 권 자 : ${voters.length}명`);
  L.push("");

  if (votes && Object.keys(votes).length > 0) {
    L.push("  [ 개인별 투표 내역 ]");
    L.push("");
    for (const m of voters) {
      const v = votes[m.id];
      if (v) {
        const name = (m.koreanName || m.name).padEnd(10);
        L.push(`    · ${name} : ${v.vote}`);
        L.push(`      사유: ${v.reason}`);
        L.push("");
      }
    }
    L.push("  [ 집계 ]");
    L.push(`    찬  성 : ${counts?.["찬성"] ?? 0}표`);
    L.push(`    반  대 : ${counts?.["반대"] ?? 0}표`);
    L.push(`    기  권 : ${counts?.["기권"] ?? 0}표`);
    L.push("");
    L.push("  [ 의결 결과 ]");
    L.push(`    ${passed ? "✅ 가   결 (PASSED)" : "❌ 부   결 (FAILED)"}`);
  } else {
    L.push("  (표결 데이터 없음)");
  }

  L.push("");
  L.push("================================================================");
  L.push("");
  L.push("  이상으로 회의를 마칩니다.");
  L.push("");
  L.push(`                        ${dateStr} 작성`);
  L.push("                        인공지능 의회 사무국");
  L.push("");
  L.push("================================================================");

  // Windows 메모장 호환 줄바꿈
  return L.join("\r\n");
}

// ── 파일 공유 (FileSystem 없이 Share.share 텍스트만 사용) ──
export async function exportTranscript(params) {
  const text = buildTranscript(params);
  const { issue, sessionDate } = params;

  const dateTag = (sessionDate || new Date())
    .toISOString().slice(0, 10).replace(/-/g, "");
  const issueTag = issue.replace(/[^\w가-힣]/g, "_").slice(0, 20);
  const fileName = `AI의회_회의록_${dateTag}_${issueTag}.txt`;

  // Android: message로 텍스트 공유 (메모장 저장, 카카오, 이메일 등)
  const result = await Share.share(
    {
      title: fileName,
      message: text,
    },
    {
      dialogTitle: "회의록 저장 / 공유",
      subject: fileName,
    }
  );

  return fileName;
}
