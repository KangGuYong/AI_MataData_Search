DROP TABLE IF EXISTS biz.order_detail CASCADE;
DROP TABLE IF EXISTS biz.orders       CASCADE;
DROP TABLE IF EXISTS biz.product      CASCADE;
DROP TABLE IF EXISTS biz.customer     CASCADE;

CREATE TABLE biz.customer (
    customer_id   BIGSERIAL PRIMARY KEY,
    customer_name VARCHAR(100) NOT NULL,
    region        VARCHAR(50)  NOT NULL,
    grade         VARCHAR(20)  NOT NULL,
    joined_at     DATE         NOT NULL
);
COMMENT ON TABLE  biz.customer               IS '고객 기본정보';
COMMENT ON COLUMN biz.customer.customer_id   IS '고객 식별번호';
COMMENT ON COLUMN biz.customer.customer_name IS '고객명';
COMMENT ON COLUMN biz.customer.region        IS '고객 지역';
COMMENT ON COLUMN biz.customer.grade         IS '고객 등급';

CREATE TABLE biz.product (
    product_id   BIGSERIAL PRIMARY KEY,
    product_name VARCHAR(100)   NOT NULL,
    category     VARCHAR(50)    NOT NULL,
    unit_price   NUMERIC(15,2)  NOT NULL
);
COMMENT ON TABLE  biz.product              IS '상품 기본정보';
COMMENT ON COLUMN biz.product.product_name IS '상품명';
COMMENT ON COLUMN biz.product.category     IS '상품 분류';
COMMENT ON COLUMN biz.product.unit_price   IS '단가';

CREATE TABLE biz.orders (
    order_id     BIGSERIAL PRIMARY KEY,
    customer_id  BIGINT        NOT NULL REFERENCES biz.customer(customer_id),
    order_date   DATE          NOT NULL,
    total_amount NUMERIC(15,2) NOT NULL,
    status       VARCHAR(20)   NOT NULL
);
COMMENT ON TABLE  biz.orders              IS '고객의 주문 정보';
COMMENT ON COLUMN biz.orders.order_date   IS '주문일자';
COMMENT ON COLUMN biz.orders.total_amount IS '주문 총 금액';
COMMENT ON COLUMN biz.orders.status       IS '주문 상태';

CREATE TABLE biz.order_detail (
    order_detail_id BIGSERIAL PRIMARY KEY,
    order_id        BIGINT        NOT NULL REFERENCES biz.orders(order_id),
    product_id      BIGINT        NOT NULL REFERENCES biz.product(product_id),
    quantity        INTEGER       NOT NULL,
    amount          NUMERIC(15,2) NOT NULL
);
COMMENT ON TABLE  biz.order_detail          IS '주문 상세 항목';
COMMENT ON COLUMN biz.order_detail.quantity IS '판매 수량';
COMMENT ON COLUMN biz.order_detail.amount   IS '항목별 금액';

-- customer 200건. region 8종
INSERT INTO biz.customer (customer_name, region, grade, joined_at)
SELECT
    '고객' || LPAD(g::text, 3, '0'),
    (ARRAY['서울','경기','부산','대구','인천','광주','대전','울산'])[1 + (g % 8)],
    -- g % 4 로 하면 4가 8(region 주기)을 나누므로 등급이 지역의 결정함수가 된다.
    -- (g / 8) % 4 는 region 주기가 한 바퀴 돌 때마다 등급을 바꿔 상관을 끊는다.
    (ARRAY['VIP','GOLD','SILVER','BRONZE'])[1 + ((g / 8) % 4)],
    DATE '2022-01-01' + (g % 900)
FROM generate_series(1, 200) AS g;

-- product 50건. category 5종 (그중 '서울식품'이 값 검색 오탐 함정)
INSERT INTO biz.product (product_name, category, unit_price)
SELECT
    '상품' || LPAD(g::text, 3, '0'),
    (ARRAY['서울식품','가전','의류','도서','생활용품'])[1 + (g % 5)],
    (1000 + (g % 50) * 500)::numeric
FROM generate_series(1, 50) AS g;

-- orders 2000건. 2023~2025 분산
INSERT INTO biz.orders (customer_id, order_date, total_amount, status)
SELECT
    1 + (g % 200),
    DATE '2023-01-01' + (g % 1000),
    (10000 + (g % 890) * 1000)::numeric,
    (ARRAY['COMPLETED','SHIPPED','CANCELLED'])[1 + (g % 3)]
FROM generate_series(1, 2000) AS g;

-- order_detail 6000건
INSERT INTO biz.order_detail (order_id, product_id, quantity, amount)
SELECT
    1 + (g % 2000),
    1 + (g % 50),
    1 + (g % 9),
    (1000 + (g % 200) * 700)::numeric
FROM generate_series(1, 6000) AS g;

ANALYZE biz.customer;
ANALYZE biz.product;
ANALYZE biz.orders;
ANALYZE biz.order_detail;
