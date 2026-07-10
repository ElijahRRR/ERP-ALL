--
-- PostgreSQL database dump
--

\restrict SgNvWLXFulOw5qreyHCGA846HgrOWoZSgRugL6QdZNE2vc8VaKhlHOeSA481Qbh

-- Dumped from database version 17.9 (Homebrew)
-- Dumped by pg_dump version 17.9 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO nextderboy;

--
-- Name: audit_hits; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.audit_hits (
    id integer NOT NULL,
    audit_run_id uuid NOT NULL,
    stage character varying(8) NOT NULL,
    rule_code character varying(64) NOT NULL,
    severity character varying(32) NOT NULL,
    penalty integer DEFAULT 0 NOT NULL,
    detail jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.audit_hits OWNER TO nextderboy;

--
-- Name: audit_hits_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.audit_hits_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.audit_hits_id_seq OWNER TO nextderboy;

--
-- Name: audit_hits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.audit_hits_id_seq OWNED BY public.audit_hits.id;


--
-- Name: audit_runs; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.audit_runs (
    id uuid NOT NULL,
    pipeline_run_id uuid,
    product_id integer NOT NULL,
    verdict character varying(16) NOT NULL,
    score_final integer DEFAULT 100 NOT NULL,
    stage_stopped_at character varying(8),
    l0_verdict character varying(16),
    l1_verdict character varying(16),
    l2_verdict character varying(16),
    l3_verdict character varying(16),
    l4_verdict character varying(16),
    walmart_product_type character varying(128),
    hit_codes jsonb,
    reason_summary text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.audit_runs OWNER TO nextderboy;

--
-- Name: batch_events; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.batch_events (
    id integer NOT NULL,
    batch_id character varying(64),
    stage character varying(64),
    status character varying(32),
    payload jsonb,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.batch_events OWNER TO nextderboy;

--
-- Name: batch_events_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.batch_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.batch_events_id_seq OWNER TO nextderboy;

--
-- Name: batch_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.batch_events_id_seq OWNED BY public.batch_events.id;


--
-- Name: batch_jobs; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.batch_jobs (
    id character varying(64) NOT NULL,
    type character varying(64) NOT NULL,
    source_page character varying(64),
    status character varying(32) NOT NULL,
    total integer,
    succeeded integer,
    failed integer,
    skipped integer,
    pending integer,
    target_ids jsonb,
    params jsonb,
    feed_jobs jsonb,
    dry_run_summary jsonb,
    result_summary jsonb,
    risk_level character varying(16),
    undoable_until timestamp with time zone,
    created_by character varying(64),
    created_at timestamp with time zone DEFAULT now(),
    started_at timestamp with time zone,
    completed_at timestamp with time zone
);


ALTER TABLE public.batch_jobs OWNER TO nextderboy;

--
-- Name: blacklist_brand_ip_stats; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.blacklist_brand_ip_stats (
    brand text NOT NULL,
    total_hits integer DEFAULT 0,
    e_hits integer DEFAULT 0,
    precision_pct numeric(5,2),
    severity text,
    override_severity text,
    override_note text,
    override_by text,
    last_analyzed_at timestamp with time zone,
    c_hits integer DEFAULT 0,
    c_precision_pct numeric(5,2) DEFAULT 0
);


ALTER TABLE public.blacklist_brand_ip_stats OWNER TO nextderboy;

--
-- Name: blacklist_brands; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.blacklist_brands (
    brand text NOT NULL,
    source text,
    added_at timestamp with time zone DEFAULT now(),
    raw jsonb
);


ALTER TABLE public.blacklist_brands OWNER TO nextderboy;

--
-- Name: brand_company_graph; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.brand_company_graph (
    id integer NOT NULL,
    brand character varying(256) NOT NULL,
    brand_normalized character varying(256) NOT NULL,
    real_company character varying(256) NOT NULL,
    source character varying(32) DEFAULT 'uspto'::character varying NOT NULL,
    confidence double precision DEFAULT '1'::double precision NOT NULL,
    is_blacklist boolean DEFAULT false NOT NULL,
    tro_case_numbers jsonb,
    details jsonb,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.brand_company_graph OWNER TO nextderboy;

--
-- Name: brand_company_graph_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.brand_company_graph_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.brand_company_graph_id_seq OWNER TO nextderboy;

--
-- Name: brand_company_graph_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.brand_company_graph_id_seq OWNED BY public.brand_company_graph.id;


--
-- Name: brand_store_assignments; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.brand_store_assignments (
    id integer NOT NULL,
    brand character varying(256) NOT NULL,
    brand_normalized character varying(256) NOT NULL,
    store_id integer NOT NULL,
    exclusive boolean DEFAULT true NOT NULL,
    active_sku_count integer DEFAULT 0 NOT NULL,
    created_by character varying(64),
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.brand_store_assignments OWNER TO nextderboy;

--
-- Name: brand_store_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.brand_store_assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.brand_store_assignments_id_seq OWNER TO nextderboy;

--
-- Name: brand_store_assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.brand_store_assignments_id_seq OWNED BY public.brand_store_assignments.id;


--
-- Name: category_applications; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.category_applications (
    id integer NOT NULL,
    store_id integer,
    store_name character varying(128) NOT NULL,
    category_code character varying(64) NOT NULL,
    category_label character varying(128),
    submitted_at date,
    reviewed_at date,
    status character varying(32) NOT NULL,
    docs_count integer,
    rejection_reason text
);


ALTER TABLE public.category_applications OWNER TO nextderboy;

--
-- Name: category_applications_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.category_applications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.category_applications_id_seq OWNER TO nextderboy;

--
-- Name: category_applications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.category_applications_id_seq OWNED BY public.category_applications.id;


--
-- Name: collection_failures; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.collection_failures (
    id integer NOT NULL,
    asin character varying(32) NOT NULL,
    batch_id character varying(64),
    reason character varying(64) NOT NULL,
    attempts integer,
    last_at timestamp with time zone DEFAULT now(),
    hint text,
    action character varying(32),
    resolved boolean
);


ALTER TABLE public.collection_failures OWNER TO nextderboy;

--
-- Name: collection_failures_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.collection_failures_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.collection_failures_id_seq OWNER TO nextderboy;

--
-- Name: collection_failures_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.collection_failures_id_seq OWNED BY public.collection_failures.id;


--
-- Name: collection_jobs; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.collection_jobs (
    id character varying(64) NOT NULL,
    batch_name character varying(256) NOT NULL,
    total_asins integer NOT NULL,
    completed integer,
    failed integer,
    pending integer,
    status character varying(32) NOT NULL,
    zip_code character varying(16),
    needs_screenshot boolean,
    concurrency integer,
    created_by character varying(64),
    created_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone
);


ALTER TABLE public.collection_jobs OWNER TO nextderboy;

--
-- Name: collection_results; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.collection_results (
    id integer NOT NULL,
    batch_id character varying(64),
    asin character varying(32) NOT NULL,
    scraped_at timestamp with time zone,
    duration_ms integer,
    http_status integer,
    content_hash character varying(64),
    field_completeness double precision,
    change_type character varying(32),
    error text,
    note text
);


ALTER TABLE public.collection_results OWNER TO nextderboy;

--
-- Name: collection_results_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.collection_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.collection_results_id_seq OWNER TO nextderboy;

--
-- Name: collection_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.collection_results_id_seq OWNED BY public.collection_results.id;


--
-- Name: compliance_rules; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.compliance_rules (
    id integer NOT NULL,
    stage character varying(8) NOT NULL,
    code character varying(64) NOT NULL,
    description text NOT NULL,
    severity character varying(32) NOT NULL,
    penalty integer,
    enabled boolean,
    hits_30d integer,
    config jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.compliance_rules OWNER TO nextderboy;

--
-- Name: compliance_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.compliance_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.compliance_rules_id_seq OWNER TO nextderboy;

--
-- Name: compliance_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.compliance_rules_id_seq OWNED BY public.compliance_rules.id;


--
-- Name: daily_quota_usage; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.daily_quota_usage (
    id bigint NOT NULL,
    usage_date date NOT NULL,
    store_id integer NOT NULL,
    action character varying(32) NOT NULL,
    count_used integer DEFAULT 0 NOT NULL,
    by_tasks jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.daily_quota_usage OWNER TO nextderboy;

--
-- Name: daily_quota_usage_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.daily_quota_usage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.daily_quota_usage_id_seq OWNER TO nextderboy;

--
-- Name: daily_quota_usage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.daily_quota_usage_id_seq OWNED BY public.daily_quota_usage.id;


--
-- Name: feed_items; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.feed_items (
    id bigint NOT NULL,
    feed_id bigint NOT NULL,
    listing_id integer,
    asin text NOT NULL,
    sku text NOT NULL,
    status text,
    ingestion_errors jsonb,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.feed_items OWNER TO nextderboy;

--
-- Name: feed_items_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.feed_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.feed_items_id_seq OWNER TO nextderboy;

--
-- Name: feed_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.feed_items_id_seq OWNED BY public.feed_items.id;


--
-- Name: feeds; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.feeds (
    id bigint NOT NULL,
    walmart_feed_id text,
    store_id integer,
    feed_type text NOT NULL,
    pipeline_run_id uuid,
    body_size_bytes integer,
    body_sha256 text,
    body_storage text,
    status text DEFAULT 'submitted'::text NOT NULL,
    total integer,
    success integer,
    failed integer,
    submitted_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    poll_count integer DEFAULT 0,
    next_poll_at timestamp with time zone,
    raw_response jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.feeds OWNER TO nextderboy;

--
-- Name: feeds_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.feeds_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.feeds_id_seq OWNER TO nextderboy;

--
-- Name: feeds_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.feeds_id_seq OWNED BY public.feeds.id;


--
-- Name: listing_errors; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.listing_errors (
    id integer NOT NULL,
    listing_id integer,
    product_id integer,
    run_id uuid,
    walmart_error_code character varying(64) NOT NULL,
    walmart_error_message text,
    category character varying(16) NOT NULL,
    our_resolution character varying(32),
    count_7d integer,
    count_30d integer,
    trend jsonb,
    brands jsonb,
    pts jsonb,
    feedback_applied_to integer[],
    created_at timestamp with time zone DEFAULT now(),
    resolved_at timestamp with time zone
);


ALTER TABLE public.listing_errors OWNER TO nextderboy;

--
-- Name: listing_errors_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.listing_errors_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.listing_errors_id_seq OWNER TO nextderboy;

--
-- Name: listing_errors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.listing_errors_id_seq OWNED BY public.listing_errors.id;


--
-- Name: listing_promotions; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.listing_promotions (
    id bigint NOT NULL,
    listing_id integer NOT NULL,
    promo_type text NOT NULL,
    reduced_price numeric(10,2) NOT NULL,
    original_price numeric(10,2),
    start_at timestamp with time zone NOT NULL,
    end_at timestamp with time zone NOT NULL,
    feed_id bigint,
    state text DEFAULT 'scheduled'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.listing_promotions OWNER TO nextderboy;

--
-- Name: listing_promotions_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.listing_promotions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.listing_promotions_id_seq OWNER TO nextderboy;

--
-- Name: listing_promotions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.listing_promotions_id_seq OWNED BY public.listing_promotions.id;


--
-- Name: listing_specs; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.listing_specs (
    id bigint NOT NULL,
    product_id integer,
    listing_id integer,
    feed_id bigint,
    audit_run_id uuid,
    source character varying(32) NOT NULL,
    feed_type character varying(32),
    pt character varying(128),
    category_path text,
    fields jsonb NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.listing_specs OWNER TO nextderboy;

--
-- Name: listing_specs_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.listing_specs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.listing_specs_id_seq OWNER TO nextderboy;

--
-- Name: listing_specs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.listing_specs_id_seq OWNED BY public.listing_specs.id;


--
-- Name: listings; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.listings (
    id integer NOT NULL,
    product_id integer,
    asin character varying(32) NOT NULL,
    store_id integer,
    store_name character varying(128) NOT NULL,
    sku character varying(128) NOT NULL,
    walmart_wpid character varying(64),
    walmart_sku character varying(64),
    state character varying(32) NOT NULL,
    current_price double precision,
    current_qty integer,
    upc character varying(32),
    feed_job_id character varying(64),
    pipeline_run_id uuid,
    listed_at timestamp with time zone,
    last_error_at timestamp with time zone,
    last_synced_at timestamp with time zone,
    extra jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    published_status text,
    unpublished_reasons jsonb,
    buy_box boolean,
    error_count integer DEFAULT 0,
    retry_count integer DEFAULT 0,
    retired_at timestamp with time zone,
    deleted_at timestamp with time zone,
    last_feed_id bigint
);


ALTER TABLE public.listings OWNER TO nextderboy;

--
-- Name: listings_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.listings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.listings_id_seq OWNER TO nextderboy;

--
-- Name: listings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.listings_id_seq OWNED BY public.listings.id;


--
-- Name: llm_cache; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.llm_cache (
    input_hash text NOT NULL,
    model text NOT NULL,
    request jsonb NOT NULL,
    response jsonb NOT NULL,
    hit_count integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    last_hit_at timestamp with time zone
);


ALTER TABLE public.llm_cache OWNER TO nextderboy;

--
-- Name: llm_usage; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.llm_usage (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    asin text,
    audit_run_id uuid,
    stage text,
    provider text NOT NULL,
    model text NOT NULL,
    prompt_tokens integer DEFAULT 0,
    completion_tokens integer DEFAULT 0,
    total_tokens integer DEFAULT 0,
    cached_input_tokens integer DEFAULT 0,
    image_count integer DEFAULT 0,
    cost_cny numeric(12,6) DEFAULT '0'::numeric,
    duration_ms integer,
    cached boolean DEFAULT false,
    extra jsonb
);


ALTER TABLE public.llm_usage OWNER TO nextderboy;

--
-- Name: llm_usage_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.llm_usage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.llm_usage_id_seq OWNER TO nextderboy;

--
-- Name: llm_usage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.llm_usage_id_seq OWNED BY public.llm_usage.id;


--
-- Name: order_lines; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.order_lines (
    id bigint NOT NULL,
    order_id bigint NOT NULL,
    line_number character varying(16),
    sku character varying(128) NOT NULL,
    asin character varying(20),
    listing_id integer,
    product_id integer,
    product_name character varying(512),
    quantity integer DEFAULT 1 NOT NULL,
    unit_price numeric(12,2),
    line_status character varying(32),
    ship_carrier character varying(32),
    tracking_number character varying(128),
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.order_lines OWNER TO nextderboy;

--
-- Name: order_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.order_lines_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.order_lines_id_seq OWNER TO nextderboy;

--
-- Name: order_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.order_lines_id_seq OWNED BY public.order_lines.id;


--
-- Name: payout_accounts; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.payout_accounts (
    id character varying(64) NOT NULL,
    name character varying(128) NOT NULL,
    account_masked character varying(64),
    type character varying(32) NOT NULL,
    stores jsonb,
    kyc character varying(32),
    status character varying(32),
    month_income double precision,
    pending double precision,
    frozen double precision,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.payout_accounts OWNER TO nextderboy;

--
-- Name: pipeline_events; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.pipeline_events (
    id integer NOT NULL,
    run_id uuid NOT NULL,
    stage character varying(64) NOT NULL,
    sub_step character varying(64),
    status character varying(32) NOT NULL,
    external_refs jsonb,
    payload_in jsonb,
    payload_out jsonb,
    error_code character varying(128),
    error_detail jsonb,
    duration_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.pipeline_events OWNER TO nextderboy;

--
-- Name: pipeline_events_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.pipeline_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.pipeline_events_id_seq OWNER TO nextderboy;

--
-- Name: pipeline_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.pipeline_events_id_seq OWNED BY public.pipeline_events.id;


--
-- Name: pipeline_runs; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.pipeline_runs (
    id uuid NOT NULL,
    input_asin character varying(32) NOT NULL,
    input_store_id integer,
    pipeline_type character varying(32) DEFAULT 'list_new'::character varying NOT NULL,
    status character varying(32) DEFAULT 'pending'::character varying NOT NULL,
    current_stage character varying(64),
    final_verdict character varying(32),
    product_id integer,
    completed_at timestamp with time zone,
    error_summary text,
    created_by character varying(64),
    notes text,
    extra jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.pipeline_runs OWNER TO nextderboy;

--
-- Name: products_master; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.products_master (
    id integer NOT NULL,
    asin character varying(32) NOT NULL,
    title text,
    brand character varying(256),
    manufacturer text,
    amazon_category_path text,
    main_image_url text,
    walmart_product_type character varying(128),
    last_source_price double precision,
    last_source_in_stock boolean,
    first_seen_at timestamp with time zone,
    last_scraped_at timestamp with time zone,
    extra jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    lifecycle_state character varying(32) DEFAULT 'collected'::character varying
);


ALTER TABLE public.products_master OWNER TO nextderboy;

--
-- Name: products_master_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.products_master_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.products_master_id_seq OWNER TO nextderboy;

--
-- Name: products_master_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.products_master_id_seq OWNED BY public.products_master.id;


--
-- Name: products_raw_amazon; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.products_raw_amazon (
    id integer NOT NULL,
    product_id integer NOT NULL,
    run_id uuid,
    raw_data jsonb NOT NULL,
    content_hash character varying(64),
    zip_code character varying(16),
    screenshot_path text,
    scraped_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.products_raw_amazon OWNER TO nextderboy;

--
-- Name: products_raw_amazon_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.products_raw_amazon_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.products_raw_amazon_id_seq OWNER TO nextderboy;

--
-- Name: products_raw_amazon_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.products_raw_amazon_id_seq OWNED BY public.products_raw_amazon.id;


--
-- Name: proxies; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.proxies (
    id character varying(64) NOT NULL,
    name character varying(128) NOT NULL,
    type character varying(32) NOT NULL,
    url_masked text,
    region character varying(32),
    ip character varying(64),
    latency integer,
    jobs integer,
    success_rate_1h double precision,
    avg_latency_ms integer,
    bandwidth_used_mbps double precision,
    status character varying(32),
    note text
);


ALTER TABLE public.proxies OWNER TO nextderboy;

--
-- Name: purchaser_configs; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.purchaser_configs (
    id bigint NOT NULL,
    purchaser_id bigint NOT NULL,
    fulfillment_method character varying(8) NOT NULL,
    price_min numeric(10,2) DEFAULT 0 NOT NULL,
    price_max numeric(10,2),
    min_inclusive boolean DEFAULT true NOT NULL,
    max_inclusive boolean DEFAULT false NOT NULL,
    includes_tax boolean DEFAULT false NOT NULL,
    includes_shipping boolean DEFAULT false NOT NULL,
    fx_rate numeric(6,4) NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT purchaser_configs_fulfillment_method_check CHECK (((fulfillment_method)::text = ANY ((ARRAY['FBA'::character varying, 'FBM'::character varying])::text[])))
);


ALTER TABLE public.purchaser_configs OWNER TO nextderboy;

--
-- Name: purchaser_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.purchaser_configs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.purchaser_configs_id_seq OWNER TO nextderboy;

--
-- Name: purchaser_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.purchaser_configs_id_seq OWNED BY public.purchaser_configs.id;


--
-- Name: purchasers; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.purchasers (
    id bigint NOT NULL,
    name character varying(64) NOT NULL,
    contact character varying(128),
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.purchasers OWNER TO nextderboy;

--
-- Name: purchasers_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.purchasers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.purchasers_id_seq OWNER TO nextderboy;

--
-- Name: purchasers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.purchasers_id_seq OWNED BY public.purchasers.id;


--
-- Name: scheduled_tasks; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.scheduled_tasks (
    id bigint NOT NULL,
    title character varying(256) NOT NULL,
    task_type character varying(32) NOT NULL,
    store_id integer,
    scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    total integer DEFAULT 0,
    processed integer DEFAULT 0,
    succeeded integer DEFAULT 0,
    failed integer DEFAULT 0,
    daily_quota integer,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    priority integer DEFAULT 5 NOT NULL,
    scheduled_at timestamp with time zone,
    next_action_at timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    last_progress_at timestamp with time zone,
    created_by character varying(64) DEFAULT 'system'::character varying,
    notes text,
    error_summary text,
    audit_log jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.scheduled_tasks OWNER TO nextderboy;

--
-- Name: scheduled_tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.scheduled_tasks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.scheduled_tasks_id_seq OWNER TO nextderboy;

--
-- Name: scheduled_tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.scheduled_tasks_id_seq OWNED BY public.scheduled_tasks.id;


--
-- Name: store_incidents; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.store_incidents (
    id character varying(64) NOT NULL,
    store_id integer,
    store_name character varying(128) NOT NULL,
    cluster character varying(32),
    tier character varying(32),
    kind character varying(16) NOT NULL,
    reason_code character varying(64),
    reason text,
    poa_status character varying(32),
    poa_text text,
    next_appeal date,
    fund_pending boolean,
    fund_amount double precision,
    fund_appeal_date date,
    resolved boolean,
    resolved_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.store_incidents OWNER TO nextderboy;

--
-- Name: store_kpi_snapshots; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.store_kpi_snapshots (
    id integer NOT NULL,
    store_id integer,
    store_name character varying(128) NOT NULL,
    snapshot_date date NOT NULL,
    captured_at timestamp with time zone DEFAULT now() NOT NULL,
    source character varying(32),
    otd double precision,
    cancellation double precision,
    vtr double precision,
    srr double precision,
    refund_rate double precision,
    negative_review double precision,
    return_rate double precision,
    inr double precision,
    composite character varying(8),
    raw_payload jsonb
);


ALTER TABLE public.store_kpi_snapshots OWNER TO nextderboy;

--
-- Name: store_kpi_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.store_kpi_snapshots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.store_kpi_snapshots_id_seq OWNER TO nextderboy;

--
-- Name: store_kpi_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.store_kpi_snapshots_id_seq OWNED BY public.store_kpi_snapshots.id;


--
-- Name: store_pricing_rules; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.store_pricing_rules (
    id integer NOT NULL,
    store_id integer NOT NULL,
    fulfillment character varying(8) NOT NULL,
    price_low numeric(10,2) NOT NULL,
    price_high numeric(10,2) NOT NULL,
    multiplier numeric(6,3) NOT NULL,
    source character varying(32) DEFAULT 'manual'::character varying NOT NULL,
    notes text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_store_pricing_rules_ck_pricing_fulfillment CHECK (((fulfillment)::text = ANY ((ARRAY['FBA'::character varying, 'FBM'::character varying])::text[]))),
    CONSTRAINT ck_store_pricing_rules_ck_pricing_multiplier CHECK ((multiplier > (0)::numeric)),
    CONSTRAINT ck_store_pricing_rules_ck_pricing_range CHECK ((price_high > price_low))
);


ALTER TABLE public.store_pricing_rules OWNER TO nextderboy;

--
-- Name: store_pricing_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.store_pricing_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.store_pricing_rules_id_seq OWNER TO nextderboy;

--
-- Name: store_pricing_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.store_pricing_rules_id_seq OWNED BY public.store_pricing_rules.id;


--
-- Name: store_quota_config; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.store_quota_config (
    store_id integer NOT NULL,
    daily_list_quota integer,
    daily_retire_quota integer,
    daily_inv_update_quota integer,
    daily_price_update_quota integer,
    notes text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.store_quota_config OWNER TO nextderboy;

--
-- Name: store_rules; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.store_rules (
    id integer NOT NULL,
    store_id integer NOT NULL,
    rule_type character varying(64) NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.store_rules OWNER TO nextderboy;

--
-- Name: store_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.store_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.store_rules_id_seq OWNER TO nextderboy;

--
-- Name: store_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.store_rules_id_seq OWNED BY public.store_rules.id;


--
-- Name: stores; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.stores (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    marketplace character varying(16) DEFAULT 'us'::character varying NOT NULL,
    client_id character varying(128) NOT NULL,
    client_secret text NOT NULL,
    proxy_type character varying(16),
    proxy_host character varying(128),
    proxy_port integer,
    proxy_username character varying(64),
    proxy_password character varying(64),
    status character varying(32) DEFAULT 'active'::character varying NOT NULL,
    tier character varying(32) DEFAULT 'none'::character varying NOT NULL,
    category_cluster character varying(32),
    sku_quota_total integer,
    sku_quota_used integer DEFAULT 0 NOT NULL,
    registered_at timestamp with time zone,
    paused_at timestamp with time zone,
    terminated_at timestamp with time zone,
    is_active boolean DEFAULT true NOT NULL,
    notes text,
    extra jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.stores OWNER TO nextderboy;

--
-- Name: stores_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.stores_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.stores_id_seq OWNER TO nextderboy;

--
-- Name: stores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.stores_id_seq OWNED BY public.stores.id;


--
-- Name: sync_runs; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.sync_runs (
    sync_id bigint NOT NULL,
    job text NOT NULL,
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone,
    rows_in integer,
    rows_out integer,
    status text,
    error_text text
);


ALTER TABLE public.sync_runs OWNER TO nextderboy;

--
-- Name: sync_runs_sync_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.sync_runs_sync_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sync_runs_sync_id_seq OWNER TO nextderboy;

--
-- Name: sync_runs_sync_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.sync_runs_sync_id_seq OWNED BY public.sync_runs.sync_id;


--
-- Name: system_config; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.system_config (
    key character varying(64) NOT NULL,
    value jsonb NOT NULL,
    description text,
    updated_by character varying(64),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.system_config OWNER TO nextderboy;

--
-- Name: system_orders; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.system_orders (
    id bigint NOT NULL,
    order_id bigint NOT NULL,
    system_number character varying(64) NOT NULL,
    carrier character varying(64),
    tracking_number character varying(128),
    tracking_url text,
    qty integer DEFAULT 0,
    sku_count integer DEFAULT 0,
    cost_cny numeric(12,2) DEFAULT 0,
    total_cny numeric(12,2) DEFAULT 0,
    status character varying(32) DEFAULT 'pending'::character varying,
    notes text,
    created_by character varying(64),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.system_orders OWNER TO nextderboy;

--
-- Name: system_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.system_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.system_orders_id_seq OWNER TO nextderboy;

--
-- Name: system_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.system_orders_id_seq OWNED BY public.system_orders.id;


--
-- Name: tro_cases; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.tro_cases (
    case_number character varying(64) NOT NULL,
    plaintiff character varying(256) NOT NULL,
    plaintiff_cn character varying(256),
    brands jsonb NOT NULL,
    state character varying(8),
    filing_date date,
    affected_sku_count integer,
    handled boolean,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.tro_cases OWNER TO nextderboy;

--
-- Name: upc_pool; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.upc_pool (
    id integer NOT NULL,
    upc character varying(32) NOT NULL,
    source character varying(32) DEFAULT 'purchased'::character varying NOT NULL,
    batch_label character varying(64),
    status character varying(16) DEFAULT 'available'::character varying NOT NULL,
    claimed_by_run_id uuid,
    claimed_by_sku character varying(128),
    claimed_by_store_id integer,
    claimed_at timestamp with time zone,
    used_at timestamp with time zone,
    verified_wpid character varying(64),
    verification_status character varying(32),
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.upc_pool OWNER TO nextderboy;

--
-- Name: upc_pool_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.upc_pool_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.upc_pool_id_seq OWNER TO nextderboy;

--
-- Name: upc_pool_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.upc_pool_id_seq OWNED BY public.upc_pool.id;


--
-- Name: violation_groundtruth; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.violation_groundtruth (
    asin text NOT NULL,
    source_sheet text NOT NULL,
    raw_reason text,
    reason_category text,
    labeled_by text DEFAULT 'keyword_filter'::text,
    labeled_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.violation_groundtruth OWNER TO nextderboy;

--
-- Name: walmart_categories; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_categories (
    id integer NOT NULL,
    category character varying(128) NOT NULL,
    product_type_group character varying(128),
    product_type character varying(128) NOT NULL,
    description text,
    requires_certificate boolean,
    zh_seller_forbidden boolean,
    requirements jsonb,
    source character varying(32)
);


ALTER TABLE public.walmart_categories OWNER TO nextderboy;

--
-- Name: walmart_categories_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.walmart_categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.walmart_categories_id_seq OWNER TO nextderboy;

--
-- Name: walmart_categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.walmart_categories_id_seq OWNED BY public.walmart_categories.id;


--
-- Name: walmart_category_map; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_category_map (
    amazon_category text NOT NULL,
    walmart_product_type text NOT NULL,
    confidence text,
    requires_certificate boolean DEFAULT false,
    zh_seller_forbidden boolean DEFAULT false,
    requirements text,
    notes text,
    amazon_leaf text,
    browse_node_id text,
    rank_in_pt integer,
    match_type text,
    source_batch text,
    synced_at timestamp with time zone DEFAULT now(),
    raw jsonb
);


ALTER TABLE public.walmart_category_map OWNER TO nextderboy;

--
-- Name: walmart_error_records; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_error_records (
    id bigint NOT NULL,
    source_sheet text NOT NULL,
    sheet_id text,
    report_date date,
    shop text,
    asin text,
    sku text,
    title text,
    walmart_pt text,
    status text,
    status2 text,
    price numeric,
    raw_reason text NOT NULL,
    error_code character(1),
    recorded_at timestamp with time zone,
    feed_id text,
    synced_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.walmart_error_records OWNER TO nextderboy;

--
-- Name: walmart_error_records_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.walmart_error_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.walmart_error_records_id_seq OWNER TO nextderboy;

--
-- Name: walmart_error_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.walmart_error_records_id_seq OWNED BY public.walmart_error_records.id;


--
-- Name: walmart_orders; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_orders (
    id bigint NOT NULL,
    store_id integer NOT NULL,
    purchase_order_id character varying(64) NOT NULL,
    customer_order_id character varying(64),
    status character varying(32),
    order_date timestamp with time zone,
    ship_date_target timestamp with time zone,
    buyer_email_alias character varying(128),
    buyer_name character varying(128),
    ship_state character varying(8),
    ship_postal character varying(16),
    total_amount numeric(12,2),
    currency character varying(8) DEFAULT 'USD'::character varying,
    line_count integer DEFAULT 0,
    raw jsonb,
    synced_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    review_status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    reviewed_by character varying(64),
    reviewed_at timestamp with time zone,
    review_note text,
    purchaser_config_id bigint,
    purchase_status character varying(16) DEFAULT 'awaiting'::character varying NOT NULL,
    purchased_at timestamp with time zone,
    real_outbound_cost_usd numeric(10,2)
);


ALTER TABLE public.walmart_orders OWNER TO nextderboy;

--
-- Name: walmart_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.walmart_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.walmart_orders_id_seq OWNER TO nextderboy;

--
-- Name: walmart_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.walmart_orders_id_seq OWNED BY public.walmart_orders.id;


--
-- Name: walmart_prohibited_policy; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_prohibited_policy (
    id integer NOT NULL,
    category_en text,
    category_zh text,
    overall_status text,
    preapproval text,
    zh_seller_risk text,
    prohibited_items text,
    conditional_items text,
    preapproval_items text,
    legal_refs text,
    zh_seller_notes text,
    full_policy text,
    official_url text,
    policy_updated_at date,
    raw jsonb,
    synced_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.walmart_prohibited_policy OWNER TO nextderboy;

--
-- Name: walmart_prohibited_policy_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.walmart_prohibited_policy_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.walmart_prohibited_policy_id_seq OWNER TO nextderboy;

--
-- Name: walmart_prohibited_policy_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.walmart_prohibited_policy_id_seq OWNED BY public.walmart_prohibited_policy.id;


--
-- Name: walmart_pt_meta; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_pt_meta (
    walmart_product_type text NOT NULL,
    walmart_category text,
    walmart_ptg text,
    access_state text,
    zh_can_do text,
    zh_seller_forbidden boolean,
    requirements text,
    notes text,
    total_fields integer,
    required_count integer,
    required_fields text,
    raw jsonb,
    synced_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.walmart_pt_meta OWNER TO nextderboy;

--
-- Name: walmart_pt_spec; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_pt_spec (
    walmart_product_type text NOT NULL,
    total_fields integer,
    required_count integer,
    required_fields jsonb,
    real_cert_fields jsonb,
    has_real_cert boolean DEFAULT false,
    soft_cert_fields jsonb,
    has_soft_cert boolean DEFAULT false,
    fields jsonb,
    synced_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.walmart_pt_spec OWNER TO nextderboy;

--
-- Name: walmart_returns; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_returns (
    id bigint NOT NULL,
    store_id integer NOT NULL,
    return_order_id character varying(64) NOT NULL,
    customer_order_id character varying(64),
    sku character varying(128),
    listing_id integer,
    product_id integer,
    status character varying(32),
    refund_status character varying(32),
    logistics_status character varying(32),
    return_method character varying(32),
    reason_code character varying(64),
    reason_desc text,
    quantity integer DEFAULT 1,
    refunded_quantity integer DEFAULT 0,
    refund_amount numeric(12,2),
    currency character varying(8) DEFAULT 'USD'::character varying,
    created_at_walmart timestamp with time zone,
    updated_at_walmart timestamp with time zone,
    raw jsonb,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.walmart_returns OWNER TO nextderboy;

--
-- Name: walmart_returns_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.walmart_returns_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.walmart_returns_id_seq OWNER TO nextderboy;

--
-- Name: walmart_returns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.walmart_returns_id_seq OWNED BY public.walmart_returns.id;


--
-- Name: audit_hits id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.audit_hits ALTER COLUMN id SET DEFAULT nextval('public.audit_hits_id_seq'::regclass);


--
-- Name: batch_events id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.batch_events ALTER COLUMN id SET DEFAULT nextval('public.batch_events_id_seq'::regclass);


--
-- Name: brand_company_graph id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.brand_company_graph ALTER COLUMN id SET DEFAULT nextval('public.brand_company_graph_id_seq'::regclass);


--
-- Name: brand_store_assignments id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.brand_store_assignments ALTER COLUMN id SET DEFAULT nextval('public.brand_store_assignments_id_seq'::regclass);


--
-- Name: category_applications id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.category_applications ALTER COLUMN id SET DEFAULT nextval('public.category_applications_id_seq'::regclass);


--
-- Name: collection_failures id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.collection_failures ALTER COLUMN id SET DEFAULT nextval('public.collection_failures_id_seq'::regclass);


--
-- Name: collection_results id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.collection_results ALTER COLUMN id SET DEFAULT nextval('public.collection_results_id_seq'::regclass);


--
-- Name: compliance_rules id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.compliance_rules ALTER COLUMN id SET DEFAULT nextval('public.compliance_rules_id_seq'::regclass);


--
-- Name: daily_quota_usage id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.daily_quota_usage ALTER COLUMN id SET DEFAULT nextval('public.daily_quota_usage_id_seq'::regclass);


--
-- Name: feed_items id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.feed_items ALTER COLUMN id SET DEFAULT nextval('public.feed_items_id_seq'::regclass);


--
-- Name: feeds id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.feeds ALTER COLUMN id SET DEFAULT nextval('public.feeds_id_seq'::regclass);


--
-- Name: listing_errors id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_errors ALTER COLUMN id SET DEFAULT nextval('public.listing_errors_id_seq'::regclass);


--
-- Name: listing_promotions id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_promotions ALTER COLUMN id SET DEFAULT nextval('public.listing_promotions_id_seq'::regclass);


--
-- Name: listing_specs id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_specs ALTER COLUMN id SET DEFAULT nextval('public.listing_specs_id_seq'::regclass);


--
-- Name: listings id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listings ALTER COLUMN id SET DEFAULT nextval('public.listings_id_seq'::regclass);


--
-- Name: llm_usage id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.llm_usage ALTER COLUMN id SET DEFAULT nextval('public.llm_usage_id_seq'::regclass);


--
-- Name: order_lines id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.order_lines ALTER COLUMN id SET DEFAULT nextval('public.order_lines_id_seq'::regclass);


--
-- Name: pipeline_events id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.pipeline_events ALTER COLUMN id SET DEFAULT nextval('public.pipeline_events_id_seq'::regclass);


--
-- Name: products_master id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_master ALTER COLUMN id SET DEFAULT nextval('public.products_master_id_seq'::regclass);


--
-- Name: products_raw_amazon id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_raw_amazon ALTER COLUMN id SET DEFAULT nextval('public.products_raw_amazon_id_seq'::regclass);


--
-- Name: purchaser_configs id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.purchaser_configs ALTER COLUMN id SET DEFAULT nextval('public.purchaser_configs_id_seq'::regclass);


--
-- Name: purchasers id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.purchasers ALTER COLUMN id SET DEFAULT nextval('public.purchasers_id_seq'::regclass);


--
-- Name: scheduled_tasks id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.scheduled_tasks ALTER COLUMN id SET DEFAULT nextval('public.scheduled_tasks_id_seq'::regclass);


--
-- Name: store_kpi_snapshots id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_kpi_snapshots ALTER COLUMN id SET DEFAULT nextval('public.store_kpi_snapshots_id_seq'::regclass);


--
-- Name: store_pricing_rules id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_pricing_rules ALTER COLUMN id SET DEFAULT nextval('public.store_pricing_rules_id_seq'::regclass);


--
-- Name: store_rules id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_rules ALTER COLUMN id SET DEFAULT nextval('public.store_rules_id_seq'::regclass);


--
-- Name: stores id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.stores ALTER COLUMN id SET DEFAULT nextval('public.stores_id_seq'::regclass);


--
-- Name: sync_runs sync_id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.sync_runs ALTER COLUMN sync_id SET DEFAULT nextval('public.sync_runs_sync_id_seq'::regclass);


--
-- Name: system_orders id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.system_orders ALTER COLUMN id SET DEFAULT nextval('public.system_orders_id_seq'::regclass);


--
-- Name: upc_pool id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.upc_pool ALTER COLUMN id SET DEFAULT nextval('public.upc_pool_id_seq'::regclass);


--
-- Name: walmart_categories id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_categories ALTER COLUMN id SET DEFAULT nextval('public.walmart_categories_id_seq'::regclass);


--
-- Name: walmart_error_records id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_error_records ALTER COLUMN id SET DEFAULT nextval('public.walmart_error_records_id_seq'::regclass);


--
-- Name: walmart_orders id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_orders ALTER COLUMN id SET DEFAULT nextval('public.walmart_orders_id_seq'::regclass);


--
-- Name: walmart_prohibited_policy id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_prohibited_policy ALTER COLUMN id SET DEFAULT nextval('public.walmart_prohibited_policy_id_seq'::regclass);


--
-- Name: walmart_returns id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_returns ALTER COLUMN id SET DEFAULT nextval('public.walmart_returns_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: audit_hits pk_audit_hits; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.audit_hits
    ADD CONSTRAINT pk_audit_hits PRIMARY KEY (id);


--
-- Name: audit_runs pk_audit_runs; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.audit_runs
    ADD CONSTRAINT pk_audit_runs PRIMARY KEY (id);


--
-- Name: batch_events pk_batch_events; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.batch_events
    ADD CONSTRAINT pk_batch_events PRIMARY KEY (id);


--
-- Name: batch_jobs pk_batch_jobs; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.batch_jobs
    ADD CONSTRAINT pk_batch_jobs PRIMARY KEY (id);


--
-- Name: blacklist_brand_ip_stats pk_blacklist_brand_ip_stats; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.blacklist_brand_ip_stats
    ADD CONSTRAINT pk_blacklist_brand_ip_stats PRIMARY KEY (brand);


--
-- Name: blacklist_brands pk_blacklist_brands; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.blacklist_brands
    ADD CONSTRAINT pk_blacklist_brands PRIMARY KEY (brand);


--
-- Name: brand_company_graph pk_brand_company_graph; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.brand_company_graph
    ADD CONSTRAINT pk_brand_company_graph PRIMARY KEY (id);


--
-- Name: brand_store_assignments pk_brand_store_assignments; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.brand_store_assignments
    ADD CONSTRAINT pk_brand_store_assignments PRIMARY KEY (id);


--
-- Name: category_applications pk_category_applications; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.category_applications
    ADD CONSTRAINT pk_category_applications PRIMARY KEY (id);


--
-- Name: collection_failures pk_collection_failures; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.collection_failures
    ADD CONSTRAINT pk_collection_failures PRIMARY KEY (id);


--
-- Name: collection_jobs pk_collection_jobs; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.collection_jobs
    ADD CONSTRAINT pk_collection_jobs PRIMARY KEY (id);


--
-- Name: collection_results pk_collection_results; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.collection_results
    ADD CONSTRAINT pk_collection_results PRIMARY KEY (id);


--
-- Name: compliance_rules pk_compliance_rules; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.compliance_rules
    ADD CONSTRAINT pk_compliance_rules PRIMARY KEY (id);


--
-- Name: daily_quota_usage pk_daily_quota_usage; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.daily_quota_usage
    ADD CONSTRAINT pk_daily_quota_usage PRIMARY KEY (id);


--
-- Name: feed_items pk_feed_items; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.feed_items
    ADD CONSTRAINT pk_feed_items PRIMARY KEY (id);


--
-- Name: feeds pk_feeds; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.feeds
    ADD CONSTRAINT pk_feeds PRIMARY KEY (id);


--
-- Name: listing_errors pk_listing_errors; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_errors
    ADD CONSTRAINT pk_listing_errors PRIMARY KEY (id);


--
-- Name: listing_promotions pk_listing_promotions; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_promotions
    ADD CONSTRAINT pk_listing_promotions PRIMARY KEY (id);


--
-- Name: listing_specs pk_listing_specs; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_specs
    ADD CONSTRAINT pk_listing_specs PRIMARY KEY (id);


--
-- Name: listings pk_listings; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listings
    ADD CONSTRAINT pk_listings PRIMARY KEY (id);


--
-- Name: llm_cache pk_llm_cache; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.llm_cache
    ADD CONSTRAINT pk_llm_cache PRIMARY KEY (input_hash);


--
-- Name: llm_usage pk_llm_usage; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.llm_usage
    ADD CONSTRAINT pk_llm_usage PRIMARY KEY (id);


--
-- Name: order_lines pk_order_lines; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.order_lines
    ADD CONSTRAINT pk_order_lines PRIMARY KEY (id);


--
-- Name: payout_accounts pk_payout_accounts; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.payout_accounts
    ADD CONSTRAINT pk_payout_accounts PRIMARY KEY (id);


--
-- Name: pipeline_events pk_pipeline_events; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.pipeline_events
    ADD CONSTRAINT pk_pipeline_events PRIMARY KEY (id);


--
-- Name: pipeline_runs pk_pipeline_runs; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT pk_pipeline_runs PRIMARY KEY (id);


--
-- Name: products_master pk_products_master; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_master
    ADD CONSTRAINT pk_products_master PRIMARY KEY (id);


--
-- Name: products_raw_amazon pk_products_raw_amazon; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_raw_amazon
    ADD CONSTRAINT pk_products_raw_amazon PRIMARY KEY (id);


--
-- Name: proxies pk_proxies; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.proxies
    ADD CONSTRAINT pk_proxies PRIMARY KEY (id);


--
-- Name: scheduled_tasks pk_scheduled_tasks; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.scheduled_tasks
    ADD CONSTRAINT pk_scheduled_tasks PRIMARY KEY (id);


--
-- Name: store_incidents pk_store_incidents; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_incidents
    ADD CONSTRAINT pk_store_incidents PRIMARY KEY (id);


--
-- Name: store_kpi_snapshots pk_store_kpi_snapshots; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_kpi_snapshots
    ADD CONSTRAINT pk_store_kpi_snapshots PRIMARY KEY (id);


--
-- Name: store_pricing_rules pk_store_pricing_rules; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_pricing_rules
    ADD CONSTRAINT pk_store_pricing_rules PRIMARY KEY (id);


--
-- Name: store_quota_config pk_store_quota_config; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_quota_config
    ADD CONSTRAINT pk_store_quota_config PRIMARY KEY (store_id);


--
-- Name: store_rules pk_store_rules; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_rules
    ADD CONSTRAINT pk_store_rules PRIMARY KEY (id);


--
-- Name: stores pk_stores; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.stores
    ADD CONSTRAINT pk_stores PRIMARY KEY (id);


--
-- Name: sync_runs pk_sync_runs; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.sync_runs
    ADD CONSTRAINT pk_sync_runs PRIMARY KEY (sync_id);


--
-- Name: system_config pk_system_config; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.system_config
    ADD CONSTRAINT pk_system_config PRIMARY KEY (key);


--
-- Name: tro_cases pk_tro_cases; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.tro_cases
    ADD CONSTRAINT pk_tro_cases PRIMARY KEY (case_number);


--
-- Name: upc_pool pk_upc_pool; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.upc_pool
    ADD CONSTRAINT pk_upc_pool PRIMARY KEY (id);


--
-- Name: violation_groundtruth pk_violation_groundtruth; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.violation_groundtruth
    ADD CONSTRAINT pk_violation_groundtruth PRIMARY KEY (asin, source_sheet);


--
-- Name: walmart_categories pk_walmart_categories; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_categories
    ADD CONSTRAINT pk_walmart_categories PRIMARY KEY (id);


--
-- Name: walmart_category_map pk_walmart_category_map; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_category_map
    ADD CONSTRAINT pk_walmart_category_map PRIMARY KEY (amazon_category, walmart_product_type);


--
-- Name: walmart_error_records pk_walmart_error_records; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_error_records
    ADD CONSTRAINT pk_walmart_error_records PRIMARY KEY (id);


--
-- Name: walmart_orders pk_walmart_orders; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_orders
    ADD CONSTRAINT pk_walmart_orders PRIMARY KEY (id);


--
-- Name: walmart_prohibited_policy pk_walmart_prohibited_policy; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_prohibited_policy
    ADD CONSTRAINT pk_walmart_prohibited_policy PRIMARY KEY (id);


--
-- Name: walmart_pt_meta pk_walmart_pt_meta; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_pt_meta
    ADD CONSTRAINT pk_walmart_pt_meta PRIMARY KEY (walmart_product_type);


--
-- Name: walmart_pt_spec pk_walmart_pt_spec; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_pt_spec
    ADD CONSTRAINT pk_walmart_pt_spec PRIMARY KEY (walmart_product_type);


--
-- Name: walmart_returns pk_walmart_returns; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_returns
    ADD CONSTRAINT pk_walmart_returns PRIMARY KEY (id);


--
-- Name: purchaser_configs purchaser_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.purchaser_configs
    ADD CONSTRAINT purchaser_configs_pkey PRIMARY KEY (id);


--
-- Name: purchasers purchasers_name_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.purchasers
    ADD CONSTRAINT purchasers_name_key UNIQUE (name);


--
-- Name: purchasers purchasers_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.purchasers
    ADD CONSTRAINT purchasers_pkey PRIMARY KEY (id);


--
-- Name: system_orders system_orders_order_id_system_number_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.system_orders
    ADD CONSTRAINT system_orders_order_id_system_number_key UNIQUE (order_id, system_number);


--
-- Name: system_orders system_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.system_orders
    ADD CONSTRAINT system_orders_pkey PRIMARY KEY (id);


--
-- Name: brand_company_graph uq_brand_company; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.brand_company_graph
    ADD CONSTRAINT uq_brand_company UNIQUE (brand, real_company);


--
-- Name: brand_store_assignments uq_brand_store; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.brand_store_assignments
    ADD CONSTRAINT uq_brand_store UNIQUE (brand_normalized, store_id);


--
-- Name: compliance_rules uq_compliance_rules_code; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.compliance_rules
    ADD CONSTRAINT uq_compliance_rules_code UNIQUE (code);


--
-- Name: feeds uq_feeds_walmart_feed_id; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.feeds
    ADD CONSTRAINT uq_feeds_walmart_feed_id UNIQUE (walmart_feed_id);


--
-- Name: store_kpi_snapshots uq_kpi_store_date_src; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_kpi_snapshots
    ADD CONSTRAINT uq_kpi_store_date_src UNIQUE (store_id, snapshot_date, source);


--
-- Name: listings uq_listings_sku; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listings
    ADD CONSTRAINT uq_listings_sku UNIQUE (sku);


--
-- Name: walmart_orders uq_orders_store_po; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_orders
    ADD CONSTRAINT uq_orders_store_po UNIQUE (store_id, purchase_order_id);


--
-- Name: products_master uq_products_master_asin; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_master
    ADD CONSTRAINT uq_products_master_asin UNIQUE (asin);


--
-- Name: daily_quota_usage uq_quota_day_store_action; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.daily_quota_usage
    ADD CONSTRAINT uq_quota_day_store_action UNIQUE (usage_date, store_id, action);


--
-- Name: walmart_returns uq_returns_store_rma; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_returns
    ADD CONSTRAINT uq_returns_store_rma UNIQUE (store_id, return_order_id);


--
-- Name: stores uq_stores_client_id; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.stores
    ADD CONSTRAINT uq_stores_client_id UNIQUE (client_id);


--
-- Name: stores uq_stores_name; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.stores
    ADD CONSTRAINT uq_stores_name UNIQUE (name);


--
-- Name: upc_pool uq_upc_pool_upc; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.upc_pool
    ADD CONSTRAINT uq_upc_pool_upc UNIQUE (upc);


--
-- Name: walmart_categories uq_walmart_cat_pt; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_categories
    ADD CONSTRAINT uq_walmart_cat_pt UNIQUE (category, product_type);


--
-- Name: walmart_prohibited_policy uq_walmart_prohibited_policy_category_en; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_prohibited_policy
    ADD CONSTRAINT uq_walmart_prohibited_policy_category_en UNIQUE (category_en);


--
-- Name: idx_bbs_precision; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_bbs_precision ON public.blacklist_brand_ip_stats USING btree (precision_pct DESC);


--
-- Name: idx_bbs_severity; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_bbs_severity ON public.blacklist_brand_ip_stats USING btree (COALESCE(override_severity, severity));


--
-- Name: idx_blacklist_brands_source; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_blacklist_brands_source ON public.blacklist_brands USING btree (source);


--
-- Name: idx_catmap_amazon_leaf; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_catmap_amazon_leaf ON public.walmart_category_map USING btree (amazon_leaf);


--
-- Name: idx_catmap_browse_node; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_catmap_browse_node ON public.walmart_category_map USING btree (browse_node_id);


--
-- Name: idx_catmap_cert; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_catmap_cert ON public.walmart_category_map USING btree (requires_certificate) WHERE (requires_certificate = true);


--
-- Name: idx_catmap_forbidden; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_catmap_forbidden ON public.walmart_category_map USING btree (zh_seller_forbidden) WHERE (zh_seller_forbidden = true);


--
-- Name: idx_catmap_pt; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_catmap_pt ON public.walmart_category_map USING btree (walmart_product_type);


--
-- Name: idx_gt_category; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_gt_category ON public.violation_groundtruth USING btree (reason_category);


--
-- Name: idx_gt_source; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_gt_source ON public.violation_groundtruth USING btree (source_sheet);


--
-- Name: idx_llm_cache_created; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_llm_cache_created ON public.llm_cache USING btree (created_at);


--
-- Name: idx_llm_usage_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_llm_usage_asin ON public.llm_usage USING btree (asin);


--
-- Name: idx_llm_usage_created; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_llm_usage_created ON public.llm_usage USING btree (created_at);


--
-- Name: idx_llm_usage_provider; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_llm_usage_provider ON public.llm_usage USING btree (provider);


--
-- Name: idx_llm_usage_stage; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_llm_usage_stage ON public.llm_usage USING btree (stage);


--
-- Name: idx_pt_meta_category; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pt_meta_category ON public.walmart_pt_meta USING btree (walmart_category);


--
-- Name: idx_pt_meta_forbidden; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pt_meta_forbidden ON public.walmart_pt_meta USING btree (zh_seller_forbidden) WHERE (zh_seller_forbidden = true);


--
-- Name: idx_pt_spec_cert; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pt_spec_cert ON public.walmart_pt_spec USING btree (has_real_cert) WHERE (has_real_cert = true);


--
-- Name: idx_werror_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_werror_asin ON public.walmart_error_records USING btree (asin);


--
-- Name: idx_werror_code; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_werror_code ON public.walmart_error_records USING btree (error_code);


--
-- Name: idx_werror_date; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_werror_date ON public.walmart_error_records USING btree (report_date);


--
-- Name: idx_werror_pt; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_werror_pt ON public.walmart_error_records USING btree (walmart_pt);


--
-- Name: idx_werror_src; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_werror_src ON public.walmart_error_records USING btree (source_sheet);


--
-- Name: idx_werror_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_werror_status ON public.walmart_error_records USING btree (status);


--
-- Name: ix_audit_hits_audit_run_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_audit_hits_audit_run_id ON public.audit_hits USING btree (audit_run_id);


--
-- Name: ix_audit_hits_rule_code; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_audit_hits_rule_code ON public.audit_hits USING btree (rule_code);


--
-- Name: ix_audit_hits_stage; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_audit_hits_stage ON public.audit_hits USING btree (stage);


--
-- Name: ix_audit_runs_pipeline_run_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_audit_runs_pipeline_run_id ON public.audit_runs USING btree (pipeline_run_id);


--
-- Name: ix_audit_runs_product_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_audit_runs_product_id ON public.audit_runs USING btree (product_id);


--
-- Name: ix_audit_runs_verdict; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_audit_runs_verdict ON public.audit_runs USING btree (verdict);


--
-- Name: ix_batch_events_batch_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_batch_events_batch_id ON public.batch_events USING btree (batch_id);


--
-- Name: ix_batch_jobs_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_batch_jobs_status ON public.batch_jobs USING btree (status);


--
-- Name: ix_batch_jobs_type; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_batch_jobs_type ON public.batch_jobs USING btree (type);


--
-- Name: ix_brand_company_graph_brand; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_brand_company_graph_brand ON public.brand_company_graph USING btree (brand);


--
-- Name: ix_brand_company_graph_brand_normalized; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_brand_company_graph_brand_normalized ON public.brand_company_graph USING btree (brand_normalized);


--
-- Name: ix_brand_company_graph_is_blacklist; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_brand_company_graph_is_blacklist ON public.brand_company_graph USING btree (is_blacklist);


--
-- Name: ix_brand_company_graph_real_company; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_brand_company_graph_real_company ON public.brand_company_graph USING btree (real_company);


--
-- Name: ix_brand_store_assignments_brand; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_brand_store_assignments_brand ON public.brand_store_assignments USING btree (brand);


--
-- Name: ix_brand_store_assignments_brand_normalized; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_brand_store_assignments_brand_normalized ON public.brand_store_assignments USING btree (brand_normalized);


--
-- Name: ix_brand_store_assignments_store_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_brand_store_assignments_store_id ON public.brand_store_assignments USING btree (store_id);


--
-- Name: ix_category_applications_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_category_applications_status ON public.category_applications USING btree (status);


--
-- Name: ix_category_applications_store_name; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_category_applications_store_name ON public.category_applications USING btree (store_name);


--
-- Name: ix_collection_failures_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_collection_failures_asin ON public.collection_failures USING btree (asin);


--
-- Name: ix_collection_failures_reason; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_collection_failures_reason ON public.collection_failures USING btree (reason);


--
-- Name: ix_collection_failures_resolved; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_collection_failures_resolved ON public.collection_failures USING btree (resolved);


--
-- Name: ix_collection_jobs_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_collection_jobs_status ON public.collection_jobs USING btree (status);


--
-- Name: ix_collection_results_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_collection_results_asin ON public.collection_results USING btree (asin);


--
-- Name: ix_collection_results_batch_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_collection_results_batch_id ON public.collection_results USING btree (batch_id);


--
-- Name: ix_compliance_rules_stage; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_compliance_rules_stage ON public.compliance_rules USING btree (stage);


--
-- Name: ix_feed_items_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_feed_items_asin ON public.feed_items USING btree (asin);


--
-- Name: ix_feed_items_feed_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_feed_items_feed_id ON public.feed_items USING btree (feed_id);


--
-- Name: ix_feed_items_listing_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_feed_items_listing_id ON public.feed_items USING btree (listing_id);


--
-- Name: ix_feed_items_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_feed_items_status ON public.feed_items USING btree (status);


--
-- Name: ix_feeds_feed_type; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_feeds_feed_type ON public.feeds USING btree (feed_type);


--
-- Name: ix_feeds_next_poll; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_feeds_next_poll ON public.feeds USING btree (next_poll_at) WHERE (status = ANY (ARRAY['submitted'::text, 'inprogress'::text]));


--
-- Name: ix_feeds_pipeline_run_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_feeds_pipeline_run_id ON public.feeds USING btree (pipeline_run_id);


--
-- Name: ix_feeds_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_feeds_status ON public.feeds USING btree (status);


--
-- Name: ix_feeds_store_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_feeds_store_id ON public.feeds USING btree (store_id);


--
-- Name: ix_listing_errors_listing_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listing_errors_listing_id ON public.listing_errors USING btree (listing_id);


--
-- Name: ix_listing_errors_product_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listing_errors_product_id ON public.listing_errors USING btree (product_id);


--
-- Name: ix_listing_errors_run_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listing_errors_run_id ON public.listing_errors USING btree (run_id);


--
-- Name: ix_listing_errors_walmart_error_code; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listing_errors_walmart_error_code ON public.listing_errors USING btree (walmart_error_code);


--
-- Name: ix_listing_specs_created_at; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listing_specs_created_at ON public.listing_specs USING btree (created_at);


--
-- Name: ix_listing_specs_feed_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listing_specs_feed_id ON public.listing_specs USING btree (feed_id);


--
-- Name: ix_listing_specs_listing_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listing_specs_listing_id ON public.listing_specs USING btree (listing_id);


--
-- Name: ix_listing_specs_product_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listing_specs_product_id ON public.listing_specs USING btree (product_id);


--
-- Name: ix_listing_specs_source; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listing_specs_source ON public.listing_specs USING btree (source);


--
-- Name: ix_listings_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listings_asin ON public.listings USING btree (asin);


--
-- Name: ix_listings_published_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listings_published_status ON public.listings USING btree (published_status);


--
-- Name: ix_listings_state; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listings_state ON public.listings USING btree (state);


--
-- Name: ix_listings_store_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listings_store_id ON public.listings USING btree (store_id);


--
-- Name: ix_listings_store_name; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listings_store_name ON public.listings USING btree (store_name);


--
-- Name: ix_listings_walmart_wpid; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_listings_walmart_wpid ON public.listings USING btree (walmart_wpid);


--
-- Name: ix_lp_end_at; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_lp_end_at ON public.listing_promotions USING btree (end_at);


--
-- Name: ix_lp_listing_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_lp_listing_id ON public.listing_promotions USING btree (listing_id);


--
-- Name: ix_lp_state; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_lp_state ON public.listing_promotions USING btree (state);


--
-- Name: ix_order_lines_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_order_lines_asin ON public.order_lines USING btree (asin);


--
-- Name: ix_order_lines_listing; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_order_lines_listing ON public.order_lines USING btree (listing_id);


--
-- Name: ix_order_lines_product; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_order_lines_product ON public.order_lines USING btree (product_id);


--
-- Name: ix_order_lines_sku; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_order_lines_sku ON public.order_lines USING btree (sku);


--
-- Name: ix_orders_customer; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_orders_customer ON public.walmart_orders USING btree (customer_order_id);


--
-- Name: ix_orders_purchase_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_orders_purchase_status ON public.walmart_orders USING btree (purchase_status);


--
-- Name: ix_orders_review_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_orders_review_status ON public.walmart_orders USING btree (review_status);


--
-- Name: ix_orders_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_orders_status ON public.walmart_orders USING btree (status);


--
-- Name: ix_orders_store_date; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_orders_store_date ON public.walmart_orders USING btree (store_id, order_date);


--
-- Name: ix_payout_accounts_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_payout_accounts_status ON public.payout_accounts USING btree (status);


--
-- Name: ix_pipeline_events_error_code; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pipeline_events_error_code ON public.pipeline_events USING btree (error_code);


--
-- Name: ix_pipeline_events_run_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pipeline_events_run_id ON public.pipeline_events USING btree (run_id);


--
-- Name: ix_pipeline_events_stage; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pipeline_events_stage ON public.pipeline_events USING btree (stage);


--
-- Name: ix_pipeline_events_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pipeline_events_status ON public.pipeline_events USING btree (status);


--
-- Name: ix_pipeline_runs_current_stage; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pipeline_runs_current_stage ON public.pipeline_runs USING btree (current_stage);


--
-- Name: ix_pipeline_runs_input_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pipeline_runs_input_asin ON public.pipeline_runs USING btree (input_asin);


--
-- Name: ix_pipeline_runs_input_store_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pipeline_runs_input_store_id ON public.pipeline_runs USING btree (input_store_id);


--
-- Name: ix_pipeline_runs_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pipeline_runs_status ON public.pipeline_runs USING btree (status);


--
-- Name: ix_pricing_store; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pricing_store ON public.store_pricing_rules USING btree (store_id);


--
-- Name: ix_pricing_store_fulfill; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_pricing_store_fulfill ON public.store_pricing_rules USING btree (store_id, fulfillment, is_active);


--
-- Name: ix_products_master_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_products_master_asin ON public.products_master USING btree (asin);


--
-- Name: ix_products_master_brand; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_products_master_brand ON public.products_master USING btree (brand);


--
-- Name: ix_products_master_lifecycle_state; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_products_master_lifecycle_state ON public.products_master USING btree (lifecycle_state);


--
-- Name: ix_products_master_walmart_product_type; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_products_master_walmart_product_type ON public.products_master USING btree (walmart_product_type);


--
-- Name: ix_products_raw_amazon_content_hash; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_products_raw_amazon_content_hash ON public.products_raw_amazon USING btree (content_hash);


--
-- Name: ix_products_raw_amazon_product_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_products_raw_amazon_product_id ON public.products_raw_amazon USING btree (product_id);


--
-- Name: ix_products_raw_amazon_run_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_products_raw_amazon_run_id ON public.products_raw_amazon USING btree (run_id);


--
-- Name: ix_products_raw_amazon_scraped_at; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_products_raw_amazon_scraped_at ON public.products_raw_amazon USING btree (scraped_at);


--
-- Name: ix_proxies_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_proxies_status ON public.proxies USING btree (status);


--
-- Name: ix_purchaser_configs_purchaser; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_purchaser_configs_purchaser ON public.purchaser_configs USING btree (purchaser_id);


--
-- Name: ix_quota_usage_date; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_quota_usage_date ON public.daily_quota_usage USING btree (usage_date);


--
-- Name: ix_returns_sku; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_returns_sku ON public.walmart_returns USING btree (sku);


--
-- Name: ix_returns_store_date; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_returns_store_date ON public.walmart_returns USING btree (store_id, created_at_walmart);


--
-- Name: ix_scheduled_tasks_next; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_scheduled_tasks_next ON public.scheduled_tasks USING btree (next_action_at) WHERE ((status)::text = ANY ((ARRAY['running'::character varying, 'pending'::character varying])::text[]));


--
-- Name: ix_scheduled_tasks_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_scheduled_tasks_status ON public.scheduled_tasks USING btree (status);


--
-- Name: ix_scheduled_tasks_store; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_scheduled_tasks_store ON public.scheduled_tasks USING btree (store_id);


--
-- Name: ix_store_incidents_kind; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_store_incidents_kind ON public.store_incidents USING btree (kind);


--
-- Name: ix_store_incidents_resolved; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_store_incidents_resolved ON public.store_incidents USING btree (resolved);


--
-- Name: ix_store_incidents_store_name; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_store_incidents_store_name ON public.store_incidents USING btree (store_name);


--
-- Name: ix_store_kpi_snapshots_snapshot_date; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_store_kpi_snapshots_snapshot_date ON public.store_kpi_snapshots USING btree (snapshot_date);


--
-- Name: ix_store_kpi_snapshots_store_name; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_store_kpi_snapshots_store_name ON public.store_kpi_snapshots USING btree (store_name);


--
-- Name: ix_store_rules_rule_type; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_store_rules_rule_type ON public.store_rules USING btree (rule_type);


--
-- Name: ix_store_rules_store_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_store_rules_store_id ON public.store_rules USING btree (store_id);


--
-- Name: ix_stores_client_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_stores_client_id ON public.stores USING btree (client_id);


--
-- Name: ix_stores_name; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_stores_name ON public.stores USING btree (name);


--
-- Name: ix_stores_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_stores_status ON public.stores USING btree (status);


--
-- Name: ix_system_orders_order; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_system_orders_order ON public.system_orders USING btree (order_id);


--
-- Name: ix_system_orders_tracking; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_system_orders_tracking ON public.system_orders USING btree (tracking_number);


--
-- Name: ix_tro_cases_handled; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_tro_cases_handled ON public.tro_cases USING btree (handled);


--
-- Name: ix_upc_pool_batch_label; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_upc_pool_batch_label ON public.upc_pool USING btree (batch_label);


--
-- Name: ix_upc_pool_claimed_by_run_id; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_upc_pool_claimed_by_run_id ON public.upc_pool USING btree (claimed_by_run_id);


--
-- Name: ix_upc_pool_claimed_by_sku; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_upc_pool_claimed_by_sku ON public.upc_pool USING btree (claimed_by_sku);


--
-- Name: ix_upc_pool_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_upc_pool_status ON public.upc_pool USING btree (status);


--
-- Name: ix_upc_pool_upc; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_upc_pool_upc ON public.upc_pool USING btree (upc);


--
-- Name: ix_walmart_categories_category; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_walmart_categories_category ON public.walmart_categories USING btree (category);


--
-- Name: ix_walmart_categories_product_type; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_walmart_categories_product_type ON public.walmart_categories USING btree (product_type);


--
-- Name: ix_walmart_categories_product_type_group; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX ix_walmart_categories_product_type_group ON public.walmart_categories USING btree (product_type_group);


--
-- Name: uniq_listing_errors_active; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE UNIQUE INDEX uniq_listing_errors_active ON public.listing_errors USING btree (listing_id, walmart_error_code) WHERE (resolved_at IS NULL);


--
-- Name: uq_collection_failures_active; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE UNIQUE INDEX uq_collection_failures_active ON public.collection_failures USING btree (batch_id, asin) WHERE (NOT resolved);


--
-- Name: uq_pricing_store_fulfill_range; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE UNIQUE INDEX uq_pricing_store_fulfill_range ON public.store_pricing_rules USING btree (store_id, fulfillment, price_low, price_high) WHERE (is_active = true);


--
-- Name: audit_hits fk_audit_hits_audit_run_id_audit_runs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.audit_hits
    ADD CONSTRAINT fk_audit_hits_audit_run_id_audit_runs FOREIGN KEY (audit_run_id) REFERENCES public.audit_runs(id) ON DELETE CASCADE;


--
-- Name: audit_runs fk_audit_runs_pipeline_run_id_pipeline_runs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.audit_runs
    ADD CONSTRAINT fk_audit_runs_pipeline_run_id_pipeline_runs FOREIGN KEY (pipeline_run_id) REFERENCES public.pipeline_runs(id) ON DELETE SET NULL;


--
-- Name: audit_runs fk_audit_runs_product_id_products_master; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.audit_runs
    ADD CONSTRAINT fk_audit_runs_product_id_products_master FOREIGN KEY (product_id) REFERENCES public.products_master(id);


--
-- Name: batch_events fk_batch_events_batch_id_batch_jobs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.batch_events
    ADD CONSTRAINT fk_batch_events_batch_id_batch_jobs FOREIGN KEY (batch_id) REFERENCES public.batch_jobs(id) ON DELETE CASCADE;


--
-- Name: brand_store_assignments fk_brand_store_assignments_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.brand_store_assignments
    ADD CONSTRAINT fk_brand_store_assignments_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: category_applications fk_category_applications_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.category_applications
    ADD CONSTRAINT fk_category_applications_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id);


--
-- Name: collection_failures fk_collection_failures_batch_id_collection_jobs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.collection_failures
    ADD CONSTRAINT fk_collection_failures_batch_id_collection_jobs FOREIGN KEY (batch_id) REFERENCES public.collection_jobs(id) ON DELETE SET NULL;


--
-- Name: collection_results fk_collection_results_batch_id_collection_jobs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.collection_results
    ADD CONSTRAINT fk_collection_results_batch_id_collection_jobs FOREIGN KEY (batch_id) REFERENCES public.collection_jobs(id) ON DELETE CASCADE;


--
-- Name: daily_quota_usage fk_daily_quota_usage_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.daily_quota_usage
    ADD CONSTRAINT fk_daily_quota_usage_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: feed_items fk_feed_items_feed_id_feeds; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.feed_items
    ADD CONSTRAINT fk_feed_items_feed_id_feeds FOREIGN KEY (feed_id) REFERENCES public.feeds(id) ON DELETE CASCADE;


--
-- Name: feed_items fk_feed_items_listing_id_listings; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.feed_items
    ADD CONSTRAINT fk_feed_items_listing_id_listings FOREIGN KEY (listing_id) REFERENCES public.listings(id) ON DELETE SET NULL;


--
-- Name: feeds fk_feeds_pipeline_run_id_pipeline_runs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.feeds
    ADD CONSTRAINT fk_feeds_pipeline_run_id_pipeline_runs FOREIGN KEY (pipeline_run_id) REFERENCES public.pipeline_runs(id) ON DELETE SET NULL;


--
-- Name: feeds fk_feeds_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.feeds
    ADD CONSTRAINT fk_feeds_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE SET NULL;


--
-- Name: listing_errors fk_listing_errors_listing_id_listings; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_errors
    ADD CONSTRAINT fk_listing_errors_listing_id_listings FOREIGN KEY (listing_id) REFERENCES public.listings(id) ON DELETE CASCADE;


--
-- Name: listing_errors fk_listing_errors_product_id_products_master; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_errors
    ADD CONSTRAINT fk_listing_errors_product_id_products_master FOREIGN KEY (product_id) REFERENCES public.products_master(id);


--
-- Name: listing_errors fk_listing_errors_run_id_pipeline_runs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_errors
    ADD CONSTRAINT fk_listing_errors_run_id_pipeline_runs FOREIGN KEY (run_id) REFERENCES public.pipeline_runs(id);


--
-- Name: listing_promotions fk_listing_promotions_feed_id_feeds; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_promotions
    ADD CONSTRAINT fk_listing_promotions_feed_id_feeds FOREIGN KEY (feed_id) REFERENCES public.feeds(id) ON DELETE SET NULL;


--
-- Name: listing_promotions fk_listing_promotions_listing_id_listings; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_promotions
    ADD CONSTRAINT fk_listing_promotions_listing_id_listings FOREIGN KEY (listing_id) REFERENCES public.listings(id) ON DELETE CASCADE;


--
-- Name: listing_specs fk_listing_specs_audit_run_id_audit_runs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_specs
    ADD CONSTRAINT fk_listing_specs_audit_run_id_audit_runs FOREIGN KEY (audit_run_id) REFERENCES public.audit_runs(id) ON DELETE SET NULL;


--
-- Name: listing_specs fk_listing_specs_feed_id_feeds; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_specs
    ADD CONSTRAINT fk_listing_specs_feed_id_feeds FOREIGN KEY (feed_id) REFERENCES public.feeds(id) ON DELETE SET NULL;


--
-- Name: listing_specs fk_listing_specs_listing_id_listings; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_specs
    ADD CONSTRAINT fk_listing_specs_listing_id_listings FOREIGN KEY (listing_id) REFERENCES public.listings(id) ON DELETE SET NULL;


--
-- Name: listing_specs fk_listing_specs_product_id_products_master; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listing_specs
    ADD CONSTRAINT fk_listing_specs_product_id_products_master FOREIGN KEY (product_id) REFERENCES public.products_master(id) ON DELETE CASCADE;


--
-- Name: listings fk_listings_last_feed_id_feeds; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listings
    ADD CONSTRAINT fk_listings_last_feed_id_feeds FOREIGN KEY (last_feed_id) REFERENCES public.feeds(id) ON DELETE SET NULL;


--
-- Name: listings fk_listings_pipeline_run_id_pipeline_runs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listings
    ADD CONSTRAINT fk_listings_pipeline_run_id_pipeline_runs FOREIGN KEY (pipeline_run_id) REFERENCES public.pipeline_runs(id) ON DELETE SET NULL;


--
-- Name: listings fk_listings_product_id_products_master; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listings
    ADD CONSTRAINT fk_listings_product_id_products_master FOREIGN KEY (product_id) REFERENCES public.products_master(id) ON DELETE CASCADE;


--
-- Name: listings fk_listings_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.listings
    ADD CONSTRAINT fk_listings_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id);


--
-- Name: order_lines fk_order_lines_listing_id_listings; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.order_lines
    ADD CONSTRAINT fk_order_lines_listing_id_listings FOREIGN KEY (listing_id) REFERENCES public.listings(id) ON DELETE SET NULL;


--
-- Name: order_lines fk_order_lines_order_id_walmart_orders; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.order_lines
    ADD CONSTRAINT fk_order_lines_order_id_walmart_orders FOREIGN KEY (order_id) REFERENCES public.walmart_orders(id) ON DELETE CASCADE;


--
-- Name: order_lines fk_order_lines_product_id_products_master; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.order_lines
    ADD CONSTRAINT fk_order_lines_product_id_products_master FOREIGN KEY (product_id) REFERENCES public.products_master(id) ON DELETE SET NULL;


--
-- Name: pipeline_events fk_pipeline_events_run_id_pipeline_runs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.pipeline_events
    ADD CONSTRAINT fk_pipeline_events_run_id_pipeline_runs FOREIGN KEY (run_id) REFERENCES public.pipeline_runs(id) ON DELETE CASCADE;


--
-- Name: pipeline_runs fk_pipeline_runs_input_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT fk_pipeline_runs_input_store_id_stores FOREIGN KEY (input_store_id) REFERENCES public.stores(id);


--
-- Name: pipeline_runs fk_pipeline_runs_product_id_products_master; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT fk_pipeline_runs_product_id_products_master FOREIGN KEY (product_id) REFERENCES public.products_master(id);


--
-- Name: products_raw_amazon fk_products_raw_amazon_product_id_products_master; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_raw_amazon
    ADD CONSTRAINT fk_products_raw_amazon_product_id_products_master FOREIGN KEY (product_id) REFERENCES public.products_master(id) ON DELETE CASCADE;


--
-- Name: products_raw_amazon fk_products_raw_amazon_run_id_pipeline_runs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_raw_amazon
    ADD CONSTRAINT fk_products_raw_amazon_run_id_pipeline_runs FOREIGN KEY (run_id) REFERENCES public.pipeline_runs(id) ON DELETE SET NULL;


--
-- Name: scheduled_tasks fk_scheduled_tasks_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.scheduled_tasks
    ADD CONSTRAINT fk_scheduled_tasks_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE SET NULL;


--
-- Name: store_incidents fk_store_incidents_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_incidents
    ADD CONSTRAINT fk_store_incidents_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id);


--
-- Name: store_kpi_snapshots fk_store_kpi_snapshots_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_kpi_snapshots
    ADD CONSTRAINT fk_store_kpi_snapshots_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: store_pricing_rules fk_store_pricing_rules_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_pricing_rules
    ADD CONSTRAINT fk_store_pricing_rules_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: store_quota_config fk_store_quota_config_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_quota_config
    ADD CONSTRAINT fk_store_quota_config_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: store_rules fk_store_rules_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.store_rules
    ADD CONSTRAINT fk_store_rules_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: upc_pool fk_upc_pool_claimed_by_run_id_pipeline_runs; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.upc_pool
    ADD CONSTRAINT fk_upc_pool_claimed_by_run_id_pipeline_runs FOREIGN KEY (claimed_by_run_id) REFERENCES public.pipeline_runs(id) ON DELETE SET NULL;


--
-- Name: upc_pool fk_upc_pool_claimed_by_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.upc_pool
    ADD CONSTRAINT fk_upc_pool_claimed_by_store_id_stores FOREIGN KEY (claimed_by_store_id) REFERENCES public.stores(id);


--
-- Name: walmart_orders fk_walmart_orders_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_orders
    ADD CONSTRAINT fk_walmart_orders_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: walmart_returns fk_walmart_returns_listing_id_listings; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_returns
    ADD CONSTRAINT fk_walmart_returns_listing_id_listings FOREIGN KEY (listing_id) REFERENCES public.listings(id) ON DELETE SET NULL;


--
-- Name: walmart_returns fk_walmart_returns_product_id_products_master; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_returns
    ADD CONSTRAINT fk_walmart_returns_product_id_products_master FOREIGN KEY (product_id) REFERENCES public.products_master(id) ON DELETE SET NULL;


--
-- Name: walmart_returns fk_walmart_returns_store_id_stores; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_returns
    ADD CONSTRAINT fk_walmart_returns_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: purchaser_configs purchaser_configs_purchaser_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.purchaser_configs
    ADD CONSTRAINT purchaser_configs_purchaser_id_fkey FOREIGN KEY (purchaser_id) REFERENCES public.purchasers(id) ON DELETE CASCADE;


--
-- Name: system_orders system_orders_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.system_orders
    ADD CONSTRAINT system_orders_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.walmart_orders(id) ON DELETE CASCADE;


--
-- Name: walmart_orders walmart_orders_purchaser_config_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_orders
    ADD CONSTRAINT walmart_orders_purchaser_config_id_fkey FOREIGN KEY (purchaser_config_id) REFERENCES public.purchaser_configs(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict SgNvWLXFulOw5qreyHCGA846HgrOWoZSgRugL6QdZNE2vc8VaKhlHOeSA481Qbh

