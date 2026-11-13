-- Schema for the fictional infrastructure investment portfolio.
-- Run against the `portfolio` database (data/clean_and_load.py does this for you).
-- All money columns are in millions of US dollars (suffix _musd).
-- Re-runnable: drops tables in child-to-parent order before recreating them.

DROP TABLE IF EXISTS quarterly_financials;
DROP TABLE IF EXISTS deals;
DROP TABLE IF EXISTS assets;
DROP TABLE IF EXISTS financials;
DROP TABLE IF EXISTS companies;

-- One row per portfolio company.
CREATE TABLE companies (
    company_id        INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    company_code      CHAR(6)       NOT NULL COMMENT 'Business key, e.g. CO-007',
    name              VARCHAR(120)  NOT NULL,
    sector            ENUM('energy','data_centers','fiber','toll_roads','water') NOT NULL,
    subsector         VARCHAR(60)   NULL,
    country           VARCHAR(60)   NULL,
    hq_city           VARCHAR(80)   NULL,
    founded_year      SMALLINT UNSIGNED NULL,
    ownership_pct     DECIMAL(5,2)  NULL COMMENT 'Our equity stake, 0-100',
    acquisition_date  DATE          NULL,
    status            ENUM('active','exited') NOT NULL DEFAULT 'active',
    PRIMARY KEY (company_id),
    UNIQUE KEY uq_companies_code (company_code),
    KEY ix_companies_sector (sector),
    CONSTRAINT ck_companies_ownership CHECK (ownership_pct IS NULL OR ownership_pct BETWEEN 0 AND 100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Annual financials, one row per company per fiscal year.
CREATE TABLE financials (
    financial_id    INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    company_id      INT UNSIGNED  NOT NULL,
    fiscal_year     SMALLINT UNSIGNED NOT NULL,
    revenue_musd    DECIMAL(12,2) NULL,
    ebitda_musd     DECIMAL(12,2) NULL COMMENT 'Can be negative',
    net_debt_musd   DECIMAL(12,2) NULL,
    capex_musd      DECIMAL(12,2) NULL,
    PRIMARY KEY (financial_id),
    UNIQUE KEY uq_financials_company_year (company_id, fiscal_year),
    CONSTRAINT fk_financials_company FOREIGN KEY (company_id) REFERENCES companies (company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Quarterly results vs budget, one row per company per fiscal year per quarter,
-- Q1 2024 through Q3 2026 (the latest completed quarter).
-- For 2024-2025 the four quarters of revenue_musd / ebitda_musd add up to the annual figures
-- in financials. FY2026 is still open, so it has no annual row yet.
CREATE TABLE quarterly_financials (
    quarterly_financial_id INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    company_id             INT UNSIGNED  NOT NULL,
    fiscal_year            SMALLINT UNSIGNED NOT NULL,
    quarter                TINYINT UNSIGNED  NOT NULL COMMENT '1-4',
    revenue_musd           DECIMAL(12,2) NULL COMMENT 'Actual revenue for the quarter',
    revenue_target_musd    DECIMAL(12,2) NULL COMMENT 'Budgeted revenue for the quarter (actual below target = missed)',
    ebitda_musd            DECIMAL(12,2) NULL COMMENT 'Can be negative',
    PRIMARY KEY (quarterly_financial_id),
    UNIQUE KEY uq_quarterly_company_year_quarter (company_id, fiscal_year, quarter),
    CONSTRAINT fk_quarterly_company FOREIGN KEY (company_id) REFERENCES companies (company_id),
    CONSTRAINT ck_quarterly_quarter CHECK (quarter BETWEEN 1 AND 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Physical infrastructure owned by each company.
CREATE TABLE assets (
    asset_id           INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    company_id         INT UNSIGNED  NOT NULL,
    asset_name         VARCHAR(120)  NOT NULL,
    asset_type         VARCHAR(60)   NOT NULL COMMENT 'e.g. Solar farm, Data center campus, Toll road',
    country            VARCHAR(60)   NULL,
    city               VARCHAR(80)   NULL,
    capacity_value     DECIMAL(12,2) NULL,
    capacity_unit      ENUM('MW','km','ML/day') NULL COMMENT 'MW for energy and data centers, route km for fiber and roads, megalitres/day for water',
    commissioning_date DATE          NULL,
    book_value_musd    DECIMAL(12,2) NULL,
    PRIMARY KEY (asset_id),
    KEY ix_assets_company (company_id),
    CONSTRAINT fk_assets_company FOREIGN KEY (company_id) REFERENCES companies (company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Transactions: our entry, follow-on investments, refinancings and exits.
CREATE TABLE deals (
    deal_id          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    company_id       INT UNSIGNED  NOT NULL,
    deal_type        ENUM('acquisition','follow_on','refinancing','partial_exit','full_exit') NOT NULL,
    deal_date        DATE          NULL,
    deal_value_musd  DECIMAL(12,2) NULL COMMENT 'NULL = undisclosed',
    equity_stake_pct DECIMAL(5,2)  NULL,
    counterparty     VARCHAR(120)  NULL,
    status           ENUM('closed','pending','terminated') NOT NULL,
    PRIMARY KEY (deal_id),
    KEY ix_deals_company (company_id),
    KEY ix_deals_date (deal_date),
    CONSTRAINT fk_deals_company FOREIGN KEY (company_id) REFERENCES companies (company_id),
    CONSTRAINT ck_deals_stake CHECK (equity_stake_pct IS NULL OR equity_stake_pct BETWEEN 0 AND 100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
