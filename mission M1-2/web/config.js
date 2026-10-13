// API 주소를 정하는 유일한 곳. 비밀값 없음 (공개 API 주소만).
//
// env-config.js 의 API_MODE:
// - "same-origin": 저장소 기본값. Render 가 화면도 함께 제공할 때·로컬 개발 → 같은 출처 /api
// - "remote":      Vercel 빌드(build.mjs)가 환경변수 API_BASE_URL 로 만든 값 → Render API 직접 호출 (Render CORS 필요)
// - "disabled":    API_BASE_URL 없이 만든 Vercel 빌드(Preview) → API 를 부르지 않는다 (Vercel 자신의 /api 로 가지 않게)
//
// URL 파라미터 등 외부 입력으로 주소를 바꾸는 기능은 일부러 두지 않았다 (로그인 토큰이 다른 서버로 가는 것을 막기 위해).

import { API_BASE_URL, API_MODE as MODE } from "./env-config.js";

export const API_MODE = MODE || "same-origin";
export const API_BASE = API_MODE === "remote" ? String(API_BASE_URL || "").replace(/\/+$/, "") : "";

export function apiUrl(path) {
  if (API_MODE === "disabled") throw new Error("API_DISABLED");
  return API_BASE + path;
}
