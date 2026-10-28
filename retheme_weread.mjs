import { readFileSync, writeFileSync } from "fs";

const SRC = "d:/vscode/02项目/视频录制工具/video_capture_workbench_prototype_v1_1 (1).html";
const DST = "d:/vscode/02项目/视频录制工具/video_capture_workbench_prototype_v1_2_weread.html";

let html = readFileSync(SRC, "utf8");
const misses = [];
const rep = (find, replace) => {
  if (!html.includes(find)) { misses.push(find); return; }
  html = html.split(find).join(replace);
};

// 标题与脚注
rep("<title>视频录制工作台 · 提词框 V1.1 交互原型</title>", "<title>视频录制工作台 · 提词框 V1.2 · WeRead Blue × Carbon Frost</title>");
rep("UI Prototype v1.1 · Offline HTML", "UI Prototype v1.2 · WeRead Blue × Carbon Frost");

// 变量 / 字体 / 焦点色
rep(":root{--bg:#f4f6fa;--paper:#fff;--line:#e5e9f1;--ink:#1d2638;--muted:#748098;--blue:#4269e8;--blue2:#eaf0ff;--cyan:#17a4a4;", ":root{--bg:#F7F8FA;--paper:#FFFFFF;--line:#E4E7EC;--ink:#1A1A1A;--muted:#666666;--blue:#1B88EE;--blue-deep:#0E70D0;--blue2:#E6F2FE;--cyan:#00A3F5;");
rep("--shadow:0 9px 30px rgba(37,54,85,.05)", "--shadow:0 9px 30px rgba(10,10,10,.06)");
rep('font-family:"Microsoft YaHei UI","Microsoft YaHei","PingFang SC", "Segoe UI",sans-serif', 'font-family:"Noto Sans SC","Microsoft YaHei UI","Microsoft YaHei","PingFang SC","Segoe UI",sans-serif');
rep("outline:2px solid #83a2ff", "outline:2px solid #1B88EE");

// 渐变对（先于散色替换）
rep("linear-gradient(140deg,#5d88ff,#4267d6)", "linear-gradient(140deg,#00A3F5,#0E70D0)");
rep("linear-gradient(#5683f5,#c2d2ff)", "linear-gradient(#1B88EE,#A9D9FC)");
rep("linear-gradient(180deg,#bbcaff,#82a1f4)", "linear-gradient(180deg,#A9D9FC,#4FA8F0)");
rep("linear-gradient(180deg,#cedcff,#9eb8f3)", "linear-gradient(180deg,#C6E6FD,#8FCBF7)");
rep("linear-gradient(135deg,#2e4268,#6d8cdb)", "linear-gradient(135deg,#0E70D0,#00A3F5)");
rep("linear-gradient(130deg,#376e78,#5cbbb6)", "linear-gradient(130deg,#3A3A3A,#6E6E6E)");

// 上下文替换（先于短 hex）
rep("border:1px solid #354158;background:#202d44", "border:1px solid #262626;background:#161616");
rep("border-color:#7c9afa;background:#f2f5ff;color:#3157d3", "border-color:#1B88EE;background:#E6F2FE;color:#0E70D0");
rep("border-color:#b2c3ff;background:#f9fbff;color:#3155c8", "border-color:#9CD1FB;background:#F3FAFF;color:#0E70D0");
rep("border-bottom:1px solid #eef0f5", "border-bottom:1px solid #ECF0F3");
rep("border:1px solid #dfe5f0", "border:1px solid #E4E7EC");

// 深色面板 → Carbon 黑系
rep("#172033", "#0A0A0A");
rep("#172238", "#141414");
rep("#18243bb8", "#0A0A0AB8");
rep("#19243a", "#0A0A0A");
rep("#1a2941", "#0A0A0A");
rep("#21324b40", "#0A0A0A40");
rep("#1d335644", "#0A0A0A22");
rep("#1c2c4a26", "#0A0A0A26");
rep("#0c1e3d60", "#0A0A0A66");
rep("#202d46", "#141414");
rep("#23334f", "#1C1C1C");
rep("#3b4b66", "#2A2A2A");
rep("#1b2941", "#101010");
rep("#3e4e68", "#2A2A2A");
rep("#304362", "#262626");
rep("#30415d", "#262626");
rep("#536b8d", "#333333");
rep("#4a5e7c", "#333333");
rep("#425878", "#2A2A2A");
rep("#385377", "#2E2E2E");
rep("#24334d", "#1F1F1F");
rep("#28354c", "#0A0A0A");

// 蓝色系 → WeRead Blue
rep("#4269e8", "#1B88EE");
rep("#4169e8", "#1B88EE");
rep("#4169e9", "#1B88EE");
rep("#4169dc44", "#1B88EE40");
rep("#4d86ff", "#1B88EE");
rep("#4c72ef", "#1B88EE");
rep("#7c9afa", "#1B88EE");
rep("#7c9efa", "#1B88EE");
rep("#436ced", "#1B88EE");
rep("#5d87ff", "#1B88EE");
rep("#5d88ff", "#00A3F5");
rep("#9fb4ff", "#1B88EE");
rep("#9CD1FB", "#9CD1FB");
rep("#8dabff", "#1B88EE");
rep("#315dddcc", "#1B88EECC");
rep("#3155c8", "#0E70D0");
rep("#3157d3", "#0E70D0");
rep("#3557c4", "#0E70D0");
rep("#355ace", "#0E70D0");
rep("#3c61ce", "#0E70D0");
rep("#4c69cc", "#0E70D0");
rep("#4c6fe5", "#0E70D0");
rep("#365fd9", "#0E70D0");
rep("#2f55d3", "#0E70D0");
rep("#50709e", "#0E70D0");
rep("#536c97", "#0E70D0");
rep("#284aab", "#0A4E8C");
rep("#b2c3ff", "#9CD1FB");
rep("#c9d7ff", "#B5DAFB");
rep("#d0dbf7", "#C9E7FC");
rep("#dce8ff", "#C9E7FC");
rep("#dbe7fd", "#C9E7FC");
rep("#a8b4c8", "#B9BEC6");

// 浅蓝底 → E6F2FE 系
rep("#eaf0ff", "#E6F2FE");
rep("#edf2ff", "#E6F2FE");
rep("#eef2ff", "#E6F2FE");
rep("#eef5ff", "#E6F2FE");
rep("#edf4ff", "#E6F2FE");
rep("#f0f4ff", "#E6F2FE");
rep("#d6e5ff", "#9CD1FB");
rep("#d6e0f4", "#BFE3FC");

// 冷灰底
rep("#f4f6fa", "#F7F8FA");
rep("#f4f7fc", "#F7F8FA");
rep("#f9fafc", "#F7F8FA");
rep("#f8faff", "#F7F8FA");
rep("#f7f9fd", "#F7F8FA");
rep("#f7f9fc", "#F7F8FA");
rep("#f3f6fa", "#F0F2F5");
rep("#f5f7fb", "#F5F6F8");
rep("#f2f5fb", "#F5F6F8");
rep("#f6f8fd", "#F5F7FA");
rep("#fbfcff", "#FFFFFF");
rep("#e9eef7", "#EDEFF2");
rep("#e8f0ff", "#EDEFF2");
rep("#eaf0ff2", "#E6F2FE");

// 线条
rep("#e5e9f1", "#E4E7EC");
rep("#e5e9f0", "#E4E7EC");
rep("#e9edf5", "#E4E7EC");
rep("#e7ebf3", "#E4E7EC");
rep("#e6ebf4", "#E4E7EC");
rep("#e8edf4", "#E4E7EC");
rep("#e0e6f0", "#E4E7EC");
rep("#e7ecf4", "#E4E7EC");
rep("#e3e9f2", "#E4E7EC");
rep("#e4e9f3", "#E4E7EC");
rep("#dfe5ee", "#E4E7EC");
rep("#dfe5ef", "#E4E7EC");
rep("#dce5f5", "#E4E7EC");
rep("#edf0f5", "#ECF0F3");
rep("#edf1f5", "#ECF0F3");
rep("#cbd5e6", "#C9CFD8");
rep("#d4dbe7", "#CDD2D9");

// 文字灰阶（蓝灰 → 中性灰）
rep("#1d2638", "#1A1A1A");
rep("#283752", "#1A1A1A");
rep("#1f2b45", "#1A1A1A");
rep("#748098", "#666666");
rep("#c8d1e4", "#B8BCC2");
rep("#8e9cb8", "#8A8F98");
rep("#7e90b0", "#7A7F88");
rep("#adbad2", "#A6ABB3");
rep("#eff3ff", "#F5F6F7");
rep("#a3b2cc", "#9CA1A8");
rep("#667791", "#6E7378");
rep("#62718d", "#666666");
rep("#95a1b5", "#999999");
rep("#32425d", "#333333");
rep("#69758c", "#595959");
rep("#45536c", "#404040");
rep("#66748e", "#666666");
rep("#3c4d68", "#333333");
rep("#50607c", "#4D4D4D");
rep("#6d7d99", "#666666");
rep("#627394", "#666666");
rep("#8390a4", "#8C8C8C");
rep("#8c9ab1", "#8C8C8C");
rep("#8795a9", "#8C8C8C");
rep("#8793a7", "#8C8C8C");
rep("#8391a8", "#8C8C8C");
rep("#91a0b6", "#8C8C8C");
rep("#56667e", "#595959");
rep("#9daac0", "#8C8C8C");
rep("#8d9ab0", "#8C8C8C");
rep("#94a2b9", "#8C8C8C");
rep("#8a98ac", "#8C8C8C");
rep("#8a98ae", "#8C8C8C");
rep("#677993", "#666666");
rep("#8c9bb0", "#8C8C8C");
rep("#9aabc0", "#8C8C8C");
rep("#96a4b9", "#8C8C8C");
rep("#8796ad", "#8C8C8C");
rep("#94a2b7", "#8C8C8C");
rep("#9aa7bc", "#8C8C8C");
rep("#9aa7bb", "#8C8C8C");
rep("#8591a5", "#8C8C8C");
rep("#71809a", "#666666");
rep("#93a0b5", "#8C8C8C");
rep("#6d7b92", "#666666");
rep("#2f3a54", "#333333");
rep("#64738d", "#666666");
rep("#7e8ca4", "#8C8C8C");
rep("#25324a", "#333333");
rep("#354561", "#333333");
rep("#63718c", "#666666");
rep("#e8f0ff", "#EDEFF2");
rep("#a8bada", "#9CA1A8");
rep("#f2f6ff", "#F5F7FA");
rep("#a6bad9", "#8C929A");
rep("#c3d0e8", "#B8BCC2");
rep("#8ea4c5", "#8C929A");
rep("#bed0ea", "#9CA1A8");
rep("#a6b8d7", "#9CA1A8");
rep("#8496b1", "#6B6B6B");

writeFileSync(DST, html);

const hexes = {};
for (const m of html.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) {
  const k = m[0].toLowerCase();
  hexes[k] = (hexes[k] || 0) + 1;
}
console.log("MISSES:", JSON.stringify(misses));
console.log("LEFTOVER_HEXES:", JSON.stringify(hexes));
console.log("WRITTEN:", DST);
