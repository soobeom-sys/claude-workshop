-- mv_condition_performance를 PostgREST RPC로 갱신하기 위한 함수.
-- supabase-py는 REST API(PostgREST)만 사용하므로 REFRESH MATERIALIZED VIEW를
-- 직접 실행할 수 없다. db.py의 refresh_condition_performance()가 이 RPC를 호출한다.

create or replace function refresh_condition_performance()
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    refresh materialized view concurrently public.mv_condition_performance;
end;
$$;

grant execute on function refresh_condition_performance() to service_role;
