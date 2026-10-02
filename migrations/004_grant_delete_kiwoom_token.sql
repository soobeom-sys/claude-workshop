-- market/token.py의 force_refresh()는 기존 공유 토큰을 무효화하기 위해
-- kiwoom_token의 해당 env 행을 DELETE한다. 001에서는 select/insert/update만
-- 부여했고 003의 일괄 GRANT에도 kiwoom_token은 빠져 있어서 DELETE가
-- 42501 permission denied로 실패한다(모든 Supabase 호출이 best-effort라
-- 매매는 계속되지만, 공유 캐시에 만료된 토큰 행이 남는다).

grant delete on public.kiwoom_token to service_role;
grant delete on public.kiwoom_token to postgres;
