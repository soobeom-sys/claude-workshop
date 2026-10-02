import "server-only";
import postgres from "postgres";

// 개발 서버의 HMR이 모듈을 다시 불러와도 커넥션 풀이 늘어나지 않도록 globalThis에 보관한다.
const globalForDb = globalThis as unknown as { sql?: postgres.Sql };

function createClient() {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error("DATABASE_URL이 설정되지 않았습니다. web/.env.local을 확인하세요.");
  }
  // Supabase Session Pooler(5432)는 세션 단위로 커넥션을 잡으므로 풀 크기를 작게 유지한다.
  return postgres(url, { max: 5, idle_timeout: 20, connect_timeout: 10 });
}

export const sql = globalForDb.sql ?? createClient();

if (process.env.NODE_ENV !== "production") globalForDb.sql = sql;
