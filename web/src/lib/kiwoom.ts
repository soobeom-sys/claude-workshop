import "server-only";
import { sql } from "@/lib/db";

// 키움은 앱키당 유효 토큰이 1개뿐이라, 백엔드(market/token.py)·MCP와 Supabase kiwoom_token 테이블로 토큰을 공유한다.
// 로컬 파일 캐시는 쓰지 않는다 (Vercel에서도 동작해야 하므로).

const EXPIRY_MARGIN_SEC = 60;

const USE_MOCK = (process.env.KIWOOM_USE_MOCK ?? "true").trim().toLowerCase() === "true";
const TOKEN_ENV = USE_MOCK ? "mock" : "real";
const BASE_URL = USE_MOCK ? "https://mockapi.kiwoom.com" : "https://api.kiwoom.com";

type TokenEntry = { access_token: string; expires_at: number };

let memoryCache: TokenEntry | null = null;

function isValid(entry: TokenEntry | null | undefined): entry is TokenEntry {
  return !!entry?.access_token && entry.expires_at - EXPIRY_MARGIN_SEC > Date.now() / 1000;
}

async function readSharedToken(): Promise<TokenEntry | null> {
  const [row] = await sql<TokenEntry[]>`
    select access_token, expires_at::float8 as expires_at
    from kiwoom_token
    where env = ${TOKEN_ENV}
  `;
  return row ?? null;
}

async function writeSharedToken(entry: TokenEntry) {
  await sql`
    insert into kiwoom_token (env, access_token, expires_at, updated_at)
    values (${TOKEN_ENV}, ${entry.access_token}, ${entry.expires_at}, now())
    on conflict (env) do update
      set access_token = excluded.access_token,
          expires_at = excluded.expires_at,
          updated_at = excluded.updated_at
  `;
}

/** expires_dt(KST YYYYMMDDHHmmss) → unix epoch 초 */
function parseExpiresDt(value: string) {
  const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(value);
  if (!m) throw new Error(`키움 토큰 expires_dt 형식 오류: ${value}`);
  const [, y, mo, d, h, mi, s] = m;
  return Date.parse(`${y}-${mo}-${d}T${h}:${mi}:${s}+09:00`) / 1000;
}

async function issueToken(appKey: string, secretKey: string): Promise<TokenEntry> {
  const res = await fetch(`${BASE_URL}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json;charset=UTF-8", "api-id": "au10001" },
    body: JSON.stringify({ grant_type: "client_credentials", appkey: appKey, secretkey: secretKey }),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`키움 토큰 발급 HTTP ${res.status}`);
  const data = await res.json();
  if (data.return_code != null && Number(data.return_code) !== 0) {
    throw new Error(`키움 토큰 발급 실패: return_code=${data.return_code} ${data.return_msg ?? ""}`);
  }
  const token = data.token ?? data.access_token;
  if (!token || !data.expires_dt) throw new Error("키움 토큰 발급 응답에 token/expires_dt가 없습니다");
  return { access_token: token, expires_at: parseExpiresDt(String(data.expires_dt)) };
}

/** 메모리 → Supabase 공유 캐시 → (앱키가 있으면) 신규 발급 순으로 토큰을 찾는다. */
async function getToken() {
  if (isValid(memoryCache)) return memoryCache.access_token;

  const shared = await readSharedToken();
  if (isValid(shared)) {
    memoryCache = shared;
    return shared.access_token;
  }

  const appKey = process.env.KIWOOM_APP_KEY;
  const secretKey = process.env.KIWOOM_SECRET_KEY;
  if (!appKey || !secretKey) {
    throw new Error(`공유 토큰 없음/만료 (env=${TOKEN_ENV}) — 백엔드(main.py)가 갱신했는지 확인`);
  }

  const issued = await issueToken(appKey, secretKey);
  await writeSharedToken(issued);
  memoryCache = issued;
  return issued.access_token;
}

export type KiwoomMinuteCandle = {
  cntr_tm: string; // YYYYMMDDHHmmss (KST)
  open_pric: string;
  high_pric: string;
  low_pric: string;
  cur_prc: string;
  trde_qty: string;
};

/** ka10080 주식분봉차트조회 (1분봉, base_dt: YYYYMMDD) */
export async function fetchMinuteCandles(stockCode: string, date: string): Promise<KiwoomMinuteCandle[]> {
  const token = await getToken();
  const res = await fetch(`${BASE_URL}/api/dostk/chart`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json;charset=UTF-8",
      "api-id": "ka10080",
      authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ stk_cd: stockCode, tic_scope: "1", upd_stkpc_tp: "0", base_dt: date }),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`키움 ka10080 HTTP ${res.status}`);
  const data = await res.json();
  if (Number(data.return_code) !== 0) {
    throw new Error(`키움 ka10080 실패: return_code=${data.return_code} ${data.return_msg ?? ""}`);
  }
  return data.stk_min_pole_chart_qry ?? [];
}
