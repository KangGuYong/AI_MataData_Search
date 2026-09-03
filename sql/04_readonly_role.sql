-- PoC에서는 적용 보류. 운영 전환 시 실행한다.
-- psql -v ro_password='<비밀번호>' -f sql/04_readonly_role.sql
CREATE ROLE itos_ro LOGIN PASSWORD :'ro_password';
GRANT CONNECT ON DATABASE ggydb TO itos_ro;
GRANT USAGE ON SCHEMA biz TO itos_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA biz TO itos_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA biz GRANT SELECT ON TABLES TO itos_ro;
REVOKE ALL ON SCHEMA meta FROM itos_ro;
