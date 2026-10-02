-- service_role(백엔드)이 001에서 만든 테이블/뷰에 실제로 접근할 수 있도록 권한을 부여한다.
-- 001 적용 당시 kiwoom_token에는 명시적으로 GRANT했지만 나머지 테이블은
-- Supabase 기본 프로비저닝이 자동으로 부여해줄 것으로 가정했는데, 이 프로젝트에서는
-- service_role에 TRUNCATE/REFERENCES/TRIGGER만 있고 SELECT/INSERT/UPDATE/DELETE가
-- 없어서 db.py의 모든 쓰기/읽기가 42501 permission denied로 실패했다.

grant select, insert, update, delete on public.hts_conditions to service_role;
grant select, insert, update, delete on public.signals to service_role;
grant select, insert, update, delete on public.trades to service_role;
grant select, insert, update, delete on public.sell_orders to service_role;
grant select, insert, update, delete on public.daily_summaries to service_role;
grant select on public.v_open_positions to service_role;
grant select on public.mv_condition_performance to service_role;

-- hts_conditions.id(serial)를 service_role이 INSERT할 때 시퀀스 값도 증가시켜야 한다.
grant usage, select on sequence hts_conditions_id_seq to service_role;

-- 앞으로 새 테이블을 만들 때도 같은 문제가 재발하지 않도록 기본 권한을 설정한다.
alter default privileges in schema public
    grant select, insert, update, delete on tables to service_role;
