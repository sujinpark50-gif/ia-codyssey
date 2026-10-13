// API 주소를 정하는 유일한 곳. 비밀값 없음 (공개 API 주소만).
//
// API_BASE_URL 은 env-config.js 에서 온다.
// - 저장소의 env-config.js 는 "" (같은 출처) → Render 가 화면도 제공할 때·로컬 개발에서 그대로 쓴다.
// - Vercel 빌드(build.mjs)는 환경변수 API_BASE_URL 로 dist/env-config.js 를 새로 만든다 → Render API 직접 호출.
//   Render 의 ALLOWED_ORIGINS(CORS)에 Vercel 운영 도메인이 있어야 한다.
//
// URL 파라미터 등 외부 입력으로 주소를 바꾸는 기능은 일부러 두지 않았다 (로그인 토큰이 다른 서버로 가는 것을 막기 위해).

import { API_BASE_URL } from "./env-config.js";

export const API_BASE = String(API_BASE_URL || "").replace(/\/+$/, "");

export function apiUrl(path) {
  return API_BASE + path;
}
