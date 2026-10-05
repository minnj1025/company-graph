-- 기업 관계 그래프 스키마 (MySQL 8). 설계 근거는 DESIGN.md 6장.
-- 시간은 세 가지를 따로 적는다 (이중 시간 모델 + 공개일).
--   세상 쪽: as_of_date(사실이 언제 기준인가), disclosed_date(언제 공시됐나), invalidated_date(정정으로 대체된 날)
--   우리 쪽: ingested_at(우리가 이 줄을 넣은 시각), retired_at(다시 뽑았더니 달라져서 우리가 이 줄을 내린 시각)
-- 날짜 D에 보이는 관계 = retired_at IS NULL AND disclosed_date <= D AND (invalidated_date IS NULL OR invalidated_date > D)
-- 줄은 고치거나 지우지 않는다. 추출기를 고쳐 다시 넣으면 달라진 줄만 retired_at 을 적고 새 줄을 더한다.

CREATE TABLE company (
  company_id   INT AUTO_INCREMENT PRIMARY KEY,
  corp_code    CHAR(8)      NULL COMMENT 'DART 고유번호. DART에 없는 회사는 NULL',
  jurir_no     VARCHAR(20)  NULL COMMENT '법인등록번호. DART와 공정위 자료를 잇는 열쇠. 해외 법인은 13자리가 아니다',
  biz_no       VARCHAR(20)  NULL COMMENT '사업자등록번호. 해외 법인은 10자리가 아니다',
  stock_code   CHAR(6)      NULL COMMENT '종목코드. 비상장은 NULL',
  name         VARCHAR(200) NOT NULL,
  corp_cls     CHAR(1)      NULL COMMENT 'Y 유가증권, K 코스닥, N 코넥스, E 기타',
  induty_code  VARCHAR(10)  NULL COMMENT 'DART 업종코드',
  ftc_group    VARCHAR(100) NULL COMMENT '공정위 기업집단명. 정답 대조용이며 추출 결과가 아니다',
  in_scope     BOOLEAN      NOT NULL DEFAULT FALSE COMMENT '수집 대상 178개사 여부',
  UNIQUE KEY uq_company_corp_code (corp_code),
  KEY ix_company_jurir_no (jurir_no),
  KEY ix_company_stock_code (stock_code)
);

CREATE TABLE company_alias (
  alias_id    INT AUTO_INCREMENT PRIMARY KEY,
  alias       VARCHAR(200) NOT NULL COMMENT '공시에 적힌 이름. 예: 현대기아, 현대모비스(주)',
  company_id  INT          NOT NULL,
  source      ENUM('auto', 'manual') NOT NULL,
  UNIQUE KEY uq_alias_company (alias, company_id),
  FOREIGN KEY (company_id) REFERENCES company (company_id)
);

-- 문서 대장: 공시 하나가 한 줄
CREATE TABLE document (
  rcept_no          CHAR(14)     PRIMARY KEY COMMENT 'DART 접수번호',
  company_id        INT          NOT NULL COMMENT '제출 대상 회사',
  report_nm         VARCHAR(300) NOT NULL,
  rcept_dt          DATE         NOT NULL COMMENT '접수일 = 공개일',
  doc_type          ENUM('annual', 'half', 'quarter', 'supply_contract', 'other') NOT NULL,
  bsns_year         SMALLINT     NULL COMMENT '정기보고서의 사업연도',
  is_correction     BOOLEAN      NOT NULL DEFAULT FALSE,
  corrects_rcept_no CHAR(14)     NULL COMMENT '이 공시가 정정한 원래 공시',
  is_latest         BOOLEAN      NOT NULL DEFAULT TRUE COMMENT '같은 건의 최신본 여부',
  content_sha256    CHAR(64)     NULL COMMENT '원문 해시. 원문 파일은 저장소에 넣지 않는다',
  fetched_at        DATETIME     NOT NULL,
  KEY ix_document_company_date (company_id, rcept_dt),
  FOREIGN KEY (company_id) REFERENCES company (company_id)
);

CREATE TABLE relation (
  relation_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  subject_company_id INT            NOT NULL COMMENT '주체. 지분은 보유한 쪽, 공급계약·주요 고객은 파는 쪽',
  object_company_id  INT            NULL COMMENT '상대. 이름을 기업에 못 붙였으면 NULL',
  object_name_raw    VARCHAR(300)   NOT NULL COMMENT '공시에 적힌 상대 이름 그대로',
  rel_type           ENUM('affiliate', 'equity', 'supply_contract', 'major_customer') NOT NULL,
  value_num          DECIMAL(24, 4) NULL COMMENT '지분율(%) 또는 금액(원)',
  value_unit         ENUM('pct', 'krw') NULL,
  as_of_date         DATE           NULL COMMENT '기준일: 그 사실이 언제 기준인가',
  disclosed_date     DATE           NOT NULL COMMENT '공개일: 근거 공시의 접수일',
  invalidated_date   DATE           NULL COMMENT '무효일: 정정으로 대체된 날',
  rcept_no           CHAR(14)       NOT NULL COMMENT '근거 공시',
  extract_method     ENUM('api', 'rule', 'llm') NOT NULL,
  trust_tier         TINYINT        NOT NULL COMMENT '1 공시+API·규칙, 2 공시+LLM, 3 뉴스',
  evidence_text      TEXT           NULL COMMENT 'LLM 추출의 근거 문장. 원문에 그대로 있어야 한다',
  attrs              JSON           NULL COMMENT '관계 종류별 부가 정보. 예: 계약명, 계약기간, 출자 목적',
  ingested_at        DATETIME       NULL DEFAULT CURRENT_TIMESTAMP COMMENT '우리가 이 줄을 넣은 시각',
  retired_at         DATETIME       NULL COMMENT '다시 뽑은 결과와 달라 내린 시각. NULL이면 지금 믿는 줄',
  extractor_version  VARCHAR(40)    NULL COMMENT '이 줄을 만든 추출기와 그 판. 예: equity-2',
  KEY ix_relation_subject (subject_company_id, rel_type, disclosed_date),
  KEY ix_relation_object (object_company_id, rel_type, disclosed_date),
  FOREIGN KEY (subject_company_id) REFERENCES company (company_id),
  FOREIGN KEY (object_company_id) REFERENCES company (company_id),
  FOREIGN KEY (rcept_no) REFERENCES document (rcept_no)
);

CREATE TABLE price_daily (
  stock_code  CHAR(6)        NOT NULL,
  trade_date  DATE           NOT NULL,
  close_price DECIMAL(14, 2) NOT NULL,
  volume      BIGINT         NOT NULL,
  PRIMARY KEY (stock_code, trade_date)
);

-- 품질 게이트에 걸린 건. 건수를 품질 지표로 쓴다
CREATE TABLE quality_log (
  log_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
  check_name ENUM('correction', 'duplicate', 'unit', 'range', 'cross_check', 'unlinked_name') NOT NULL,
  rcept_no   CHAR(14)     NULL,
  detail     VARCHAR(500) NOT NULL,
  created_at DATETIME     NOT NULL,
  KEY ix_quality_check (check_name)
);
