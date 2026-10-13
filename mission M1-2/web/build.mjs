// Vercel 빌드 (Build Command: node build.mjs, Output Directory: dist)
// 정적 파일을 dist/ 로 복사하고, 환경변수 API_BASE_URL 을 dist/env-config.js 에 공개 값으로 쓴다.
// 비밀값은 다루지 않는다. 프레임워크·패키지 설치 없음 (Node 기본 모듈만).
import { copyFileSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "dist");
const FILES = ["index.html", "styles.css", "app.js", "config.js"];

const apiBase = (process.env.API_BASE_URL || "").trim().replace(/\/+$/, "");
if (apiBase && !/^https:\/\/[a-z0-9.-]+(:\d+)?$/i.test(apiBase)) {
  console.error("API_BASE_URL 은 https://호스트 형태여야 합니다 (경로·쿼리 없이)");
  process.exit(1);
}
if (!apiBase && process.env.VERCEL_ENV === "production") {
  console.error("운영(Production) 빌드에는 API_BASE_URL 환경변수가 필요합니다");
  process.exit(1);
}

rmSync(out, { recursive: true, force: true });
mkdirSync(out);
for (const f of FILES) copyFileSync(join(here, f), join(out, f));
// API_BASE_URL 이 없는 Vercel 빌드(Preview)는 같은 출처로 두지 않는다 — Vercel 자신의 /api 를 부르지 않도록 "disabled"
const mode = apiBase ? "remote" : "disabled";
writeFileSync(join(out, "env-config.js"),
  `// build.mjs 가 빌드 시 생성 (공개 값만)\nexport const API_BASE_URL = ${JSON.stringify(apiBase)};\nexport const API_MODE = ${JSON.stringify(mode)};\n`);
console.log(`dist/ 생성: ${FILES.length + 1}개 파일, API_MODE=${mode}, API_BASE_URL=${apiBase || "(없음)"}`);
