--
-- PostgreSQL database dump
--

\restrict KxSmD0VQi0oKTkoHHPHUxRusc9xwFEGIMi1zr86nu1MbghaSCxkjha3i9WIzuVc

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

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ai_match_results; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.ai_match_results (
    id integer NOT NULL,
    case_number text NOT NULL,
    plaintiff_clean text,
    brand_clean text,
    real_company text,
    match_quality text,
    confidence double precision,
    match_reason text,
    brands_found text,
    agent_id integer,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.ai_match_results OWNER TO nextderboy;

--
-- Name: ai_match_results_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.ai_match_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ai_match_results_id_seq OWNER TO nextderboy;

--
-- Name: ai_match_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.ai_match_results_id_seq OWNED BY public.ai_match_results.id;


--
-- Name: amazon_walmart_category_map; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.amazon_walmart_category_map (
    id bigint NOT NULL,
    amazon_root_id text,
    amazon_path text,
    walmart_product_type text,
    confidence numeric(3,2),
    history_suspend_rate numeric(5,4),
    history_sample_count integer DEFAULT 0,
    source text,
    is_safe boolean DEFAULT false,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    walmart_category text,
    walmart_ptg text,
    recommended_amazon_l1 text,
    recommended_amazon_l2 text,
    recommended_amazon_l3 text,
    recommended_amazon_bsr text,
    recommended_amazon_path text,
    cert_required_text text,
    ip_risk_level text,
    compliance_notes text,
    sales_qualification text
);


ALTER TABLE public.amazon_walmart_category_map OWNER TO nextderboy;

--
-- Name: amazon_walmart_category_map_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.amazon_walmart_category_map_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.amazon_walmart_category_map_id_seq OWNER TO nextderboy;

--
-- Name: amazon_walmart_category_map_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.amazon_walmart_category_map_id_seq OWNED BY public.amazon_walmart_category_map.id;


--
-- Name: audit_flags; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.audit_flags (
    id bigint NOT NULL,
    audit_id bigint,
    flag_type text,
    flag_category text,
    flag_code text,
    description text,
    severity text,
    evidence jsonb
);


ALTER TABLE public.audit_flags OWNER TO nextderboy;

--
-- Name: audit_flags_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.audit_flags_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.audit_flags_id_seq OWNER TO nextderboy;

--
-- Name: audit_flags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.audit_flags_id_seq OWNED BY public.audit_flags.id;


--
-- Name: blacklist_brands; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.blacklist_brands (
    id integer NOT NULL,
    brand_name text NOT NULL,
    brand_upper text NOT NULL,
    source text,
    added_date date,
    is_multi_word boolean NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.blacklist_brands OWNER TO nextderboy;

--
-- Name: blacklist_brands_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.blacklist_brands_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.blacklist_brands_id_seq OWNER TO nextderboy;

--
-- Name: blacklist_brands_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.blacklist_brands_id_seq OWNED BY public.blacklist_brands.id;


--
-- Name: brand_nice_class; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.brand_nice_class (
    id integer NOT NULL,
    real_company text,
    brand_name text NOT NULL,
    brand_upper text NOT NULL,
    serial_number text,
    registration_number text,
    status text,
    is_live boolean DEFAULT true NOT NULL,
    nice_class text,
    nice_class_cn text,
    nice_class_en text,
    goods_services text,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.brand_nice_class OWNER TO nextderboy;

--
-- Name: brand_nice_class_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.brand_nice_class_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.brand_nice_class_id_seq OWNER TO nextderboy;

--
-- Name: brand_nice_class_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.brand_nice_class_id_seq OWNED BY public.brand_nice_class.id;


--
-- Name: company_brand_details; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.company_brand_details (
    id integer NOT NULL,
    real_company text,
    mark_identification text,
    serial_number bigint,
    registration_number text,
    status_code text,
    live_dead text,
    category text,
    international_code text,
    international_desc_cn text,
    international_desc_en text,
    goods_services text,
    first_use_date date
);


ALTER TABLE public.company_brand_details OWNER TO nextderboy;

--
-- Name: company_brand_details_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.company_brand_details_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.company_brand_details_id_seq OWNER TO nextderboy;

--
-- Name: company_brand_details_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.company_brand_details_id_seq OWNED BY public.company_brand_details.id;


--
-- Name: etl_errors; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.etl_errors (
    id bigint NOT NULL,
    data_type text NOT NULL,
    source_file text NOT NULL,
    record_id text,
    error_type text,
    error_message text,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.etl_errors OWNER TO nextderboy;

--
-- Name: etl_errors_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.etl_errors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.etl_errors_id_seq OWNER TO nextderboy;

--
-- Name: etl_errors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.etl_errors_id_seq OWNED BY public.etl_errors.id;


--
-- Name: etl_progress; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.etl_progress (
    id bigint NOT NULL,
    data_type text NOT NULL,
    source_file text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    records_total integer DEFAULT 0,
    records_inserted integer DEFAULT 0,
    records_skipped integer DEFAULT 0,
    records_errored integer DEFAULT 0,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    error_message text
);


ALTER TABLE public.etl_progress OWNER TO nextderboy;

--
-- Name: etl_progress_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.etl_progress_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.etl_progress_id_seq OWNER TO nextderboy;

--
-- Name: etl_progress_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.etl_progress_id_seq OWNED BY public.etl_progress.id;


--
-- Name: extra_brand_details; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.extra_brand_details (
    id integer NOT NULL,
    real_company text,
    mark_identification text,
    serial_number bigint,
    registration_number text,
    status_code text,
    live_dead text,
    category text,
    international_code text,
    international_desc_cn text,
    international_desc_en text,
    goods_services text,
    first_use_date date,
    created_at date DEFAULT CURRENT_DATE
);


ALTER TABLE public.extra_brand_details OWNER TO nextderboy;

--
-- Name: extra_brand_details_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.extra_brand_details_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.extra_brand_details_id_seq OWNER TO nextderboy;

--
-- Name: extra_brand_details_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.extra_brand_details_id_seq OWNED BY public.extra_brand_details.id;


--
-- Name: matched_companies; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.matched_companies (
    id integer NOT NULL,
    case_number text,
    plaintiff_clean text,
    brand_clean text,
    real_company text,
    match_quality text,
    needs_review boolean DEFAULT false,
    review_reason text
);


ALTER TABLE public.matched_companies OWNER TO nextderboy;

--
-- Name: matched_companies_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.matched_companies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.matched_companies_id_seq OWNER TO nextderboy;

--
-- Name: matched_companies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.matched_companies_id_seq OWNED BY public.matched_companies.id;


--
-- Name: nice_classification; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.nice_classification (
    code text NOT NULL,
    name_en text,
    name_cn text
);


ALTER TABLE public.nice_classification OWNER TO nextderboy;

--
-- Name: patent_grant_assignees; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.patent_grant_assignees (
    id bigint NOT NULL,
    patent_number text NOT NULL,
    orgname text,
    first_name text,
    last_name text,
    city text,
    state text,
    country text
);


ALTER TABLE public.patent_grant_assignees OWNER TO nextderboy;

--
-- Name: patent_grant_assignees_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.patent_grant_assignees_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.patent_grant_assignees_id_seq OWNER TO nextderboy;

--
-- Name: patent_grant_assignees_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.patent_grant_assignees_id_seq OWNED BY public.patent_grant_assignees.id;


--
-- Name: patent_grant_citations; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.patent_grant_citations (
    id bigint NOT NULL,
    patent_number text NOT NULL,
    cited_doc_number text,
    cited_country text,
    cited_kind text,
    category text
);


ALTER TABLE public.patent_grant_citations OWNER TO nextderboy;

--
-- Name: patent_grant_citations_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.patent_grant_citations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.patent_grant_citations_id_seq OWNER TO nextderboy;

--
-- Name: patent_grant_citations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.patent_grant_citations_id_seq OWNED BY public.patent_grant_citations.id;


--
-- Name: patent_grant_classifications; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.patent_grant_classifications (
    id bigint NOT NULL,
    patent_number text NOT NULL,
    system text NOT NULL,
    code text NOT NULL,
    is_main boolean DEFAULT false
);


ALTER TABLE public.patent_grant_classifications OWNER TO nextderboy;

--
-- Name: patent_grant_classifications_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.patent_grant_classifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.patent_grant_classifications_id_seq OWNER TO nextderboy;

--
-- Name: patent_grant_classifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.patent_grant_classifications_id_seq OWNED BY public.patent_grant_classifications.id;


--
-- Name: patent_grant_images; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.patent_grant_images (
    id bigint NOT NULL,
    patent_number text NOT NULL,
    image_name text NOT NULL,
    image_path text NOT NULL,
    embedding public.vector(512)
);


ALTER TABLE public.patent_grant_images OWNER TO nextderboy;

--
-- Name: patent_grant_images_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.patent_grant_images_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.patent_grant_images_id_seq OWNER TO nextderboy;

--
-- Name: patent_grant_images_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.patent_grant_images_id_seq OWNED BY public.patent_grant_images.id;


--
-- Name: patent_grant_inventors; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.patent_grant_inventors (
    id bigint NOT NULL,
    patent_number text NOT NULL,
    first_name text,
    last_name text,
    city text,
    state text,
    country text
);


ALTER TABLE public.patent_grant_inventors OWNER TO nextderboy;

--
-- Name: patent_grant_inventors_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.patent_grant_inventors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.patent_grant_inventors_id_seq OWNER TO nextderboy;

--
-- Name: patent_grant_inventors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.patent_grant_inventors_id_seq OWNED BY public.patent_grant_inventors.id;


--
-- Name: patent_grants; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.patent_grants (
    patent_number text NOT NULL,
    kind text,
    application_number text,
    appl_type text,
    title text,
    grant_date date,
    filing_date date,
    abstract text,
    num_claims integer,
    num_figures integer,
    num_drawing_sheets integer,
    term_years integer,
    term_extension_days integer,
    source_tar text,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.patent_grants OWNER TO nextderboy;

--
-- Name: path_a_results; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.path_a_results (
    id integer NOT NULL,
    plaintiff_clean text,
    match_type text,
    serial_number bigint,
    mark_identification text,
    owner_name text,
    status_code text,
    live_dead text
);


ALTER TABLE public.path_a_results OWNER TO nextderboy;

--
-- Name: path_a_results_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.path_a_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.path_a_results_id_seq OWNER TO nextderboy;

--
-- Name: path_a_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.path_a_results_id_seq OWNED BY public.path_a_results.id;


--
-- Name: path_b_results; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.path_b_results (
    id integer NOT NULL,
    brand_clean text,
    match_type text,
    serial_number bigint,
    mark_identification text,
    owner_name text,
    status_code text,
    live_dead text
);


ALTER TABLE public.path_b_results OWNER TO nextderboy;

--
-- Name: path_b_results_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.path_b_results_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.path_b_results_id_seq OWNER TO nextderboy;

--
-- Name: path_b_results_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.path_b_results_id_seq OWNED BY public.path_b_results.id;


--
-- Name: product_audits; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.product_audits (
    id bigint NOT NULL,
    stage_id bigint,
    asin text,
    batch_file text,
    verdict text,
    ip_risk integer,
    offensive_risk integer,
    regulatory_risk integer,
    counterfeit_risk integer,
    overall_risk integer,
    reason_summary text,
    recommended_walmart_product_type text,
    l1_triggered boolean DEFAULT false,
    l2_triggered boolean DEFAULT false,
    l2_vision_triggered boolean DEFAULT false,
    llm_raw_response jsonb,
    audited_at timestamp with time zone DEFAULT now(),
    walmart_pt_final text,
    walmart_category_final text,
    nice_class text,
    pt_confidence numeric(3,2),
    pt_classifier_reasoning text,
    violation_risk_category text
);


ALTER TABLE public.product_audits OWNER TO nextderboy;

--
-- Name: product_audits_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.product_audits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.product_audits_id_seq OWNER TO nextderboy;

--
-- Name: product_audits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.product_audits_id_seq OWNED BY public.product_audits.id;


--
-- Name: products_embeddings; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.products_embeddings (
    stage_id bigint NOT NULL,
    text_embedding public.vector(1024),
    embedding_model text DEFAULT 'qwen-text-embedding-v3'::text,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.products_embeddings OWNER TO nextderboy;

--
-- Name: products_stage; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.products_stage (
    id bigint NOT NULL,
    batch_file text,
    asin text,
    title text,
    brand text,
    product_type text,
    manufacturer text,
    model_number text,
    part_number text,
    origin_country text,
    bullet_points text,
    long_description text,
    image_urls text,
    upc text,
    ean text,
    root_category_id text,
    category_chain_ids text,
    category_path text,
    price numeric(10,2),
    bsr text,
    is_fba boolean,
    raw_data jsonb,
    collected_at timestamp with time zone,
    imported_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.products_stage OWNER TO nextderboy;

--
-- Name: products_stage_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.products_stage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.products_stage_id_seq OWNER TO nextderboy;

--
-- Name: products_stage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.products_stage_id_seq OWNED BY public.products_stage.id;


--
-- Name: status_code_mapping; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.status_code_mapping (
    status_code text NOT NULL,
    live_dead text NOT NULL,
    category text NOT NULL,
    description text
);


ALTER TABLE public.status_code_mapping OWNER TO nextderboy;

--
-- Name: tmp_fuzzy; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.tmp_fuzzy (
    word text
);


ALTER TABLE public.tmp_fuzzy OWNER TO nextderboy;

--
-- Name: tmp_prefixes; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.tmp_prefixes (
    prefix text
);


ALTER TABLE public.tmp_prefixes OWNER TO nextderboy;

--
-- Name: tmp_violations; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.tmp_violations (
    word text
);


ALTER TABLE public.tmp_violations OWNER TO nextderboy;

--
-- Name: trademark_classes; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.trademark_classes (
    id bigint NOT NULL,
    serial_number bigint NOT NULL,
    international_code text,
    us_code text,
    status_code text,
    first_use_date date,
    first_use_commerce_date date
);


ALTER TABLE public.trademark_classes OWNER TO nextderboy;

--
-- Name: trademark_classes_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.trademark_classes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.trademark_classes_id_seq OWNER TO nextderboy;

--
-- Name: trademark_classes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.trademark_classes_id_seq OWNED BY public.trademark_classes.id;


--
-- Name: trademark_design_codes; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.trademark_design_codes (
    id bigint NOT NULL,
    serial_number bigint NOT NULL,
    design_code text NOT NULL
);


ALTER TABLE public.trademark_design_codes OWNER TO nextderboy;

--
-- Name: trademark_design_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.trademark_design_codes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.trademark_design_codes_id_seq OWNER TO nextderboy;

--
-- Name: trademark_design_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.trademark_design_codes_id_seq OWNED BY public.trademark_design_codes.id;


--
-- Name: trademark_owners; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.trademark_owners (
    id bigint NOT NULL,
    serial_number bigint NOT NULL,
    entry_number text,
    party_type text,
    party_name text,
    entity_type text,
    nationality_state text,
    nationality_country text
);


ALTER TABLE public.trademark_owners OWNER TO nextderboy;

--
-- Name: trademark_owners_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.trademark_owners_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.trademark_owners_id_seq OWNER TO nextderboy;

--
-- Name: trademark_owners_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.trademark_owners_id_seq OWNED BY public.trademark_owners.id;


--
-- Name: trademark_statements; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.trademark_statements (
    id bigint NOT NULL,
    serial_number bigint NOT NULL,
    type_code text NOT NULL,
    text text
);


ALTER TABLE public.trademark_statements OWNER TO nextderboy;

--
-- Name: trademark_statements_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.trademark_statements_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.trademark_statements_id_seq OWNER TO nextderboy;

--
-- Name: trademark_statements_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.trademark_statements_id_seq OWNED BY public.trademark_statements.id;


--
-- Name: trademarks; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.trademarks (
    serial_number bigint NOT NULL,
    registration_number text,
    mark_identification text,
    status_code text,
    status_date date,
    filing_date date,
    registration_date date,
    cancellation_date date,
    abandonment_date date,
    mark_drawing_code text,
    attorney_name text,
    is_trademark boolean DEFAULT false,
    is_service_mark boolean DEFAULT false,
    is_collective boolean DEFAULT false,
    is_certification boolean DEFAULT false,
    is_intent_to_use boolean DEFAULT false,
    is_use_based boolean DEFAULT false,
    is_44d boolean DEFAULT false,
    is_44e boolean DEFAULT false,
    is_66a boolean DEFAULT false,
    standard_characters boolean DEFAULT false,
    color_drawing boolean DEFAULT false,
    drawing_3d boolean DEFAULT false,
    renewal_filed boolean DEFAULT false,
    section_8_accepted boolean DEFAULT false,
    section_15_acknowledged boolean DEFAULT false,
    source_file text,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.trademarks OWNER TO nextderboy;

--
-- Name: tro_cases; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.tro_cases (
    case_number text NOT NULL,
    date_filed date,
    nature_of_suit text,
    plaintiff_raw text,
    brand_raw text,
    plaintiff_clean text,
    brand_clean text,
    brand_eq_plaintiff boolean DEFAULT false
);


ALTER TABLE public.tro_cases OWNER TO nextderboy;

--
-- Name: unique_brands; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.unique_brands (
    id integer NOT NULL,
    brand_clean text,
    case_count integer,
    query_status text DEFAULT 'pending'::text
);


ALTER TABLE public.unique_brands OWNER TO nextderboy;

--
-- Name: unique_brands_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.unique_brands_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.unique_brands_id_seq OWNER TO nextderboy;

--
-- Name: unique_brands_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.unique_brands_id_seq OWNED BY public.unique_brands.id;


--
-- Name: unique_plaintiffs; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.unique_plaintiffs (
    id integer NOT NULL,
    plaintiff_clean text,
    case_count integer,
    query_status text DEFAULT 'pending'::text
);


ALTER TABLE public.unique_plaintiffs OWNER TO nextderboy;

--
-- Name: unique_plaintiffs_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.unique_plaintiffs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.unique_plaintiffs_id_seq OWNER TO nextderboy;

--
-- Name: unique_plaintiffs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.unique_plaintiffs_id_seq OWNED BY public.unique_plaintiffs.id;


--
-- Name: walmart_product_types; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_product_types (
    product_type text NOT NULL,
    sample_count integer DEFAULT 0,
    suspend_count integer DEFAULT 0,
    suspend_rate numeric(5,4),
    dominant_reason text,
    is_risky boolean DEFAULT false,
    notes text,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.walmart_product_types OWNER TO nextderboy;

--
-- Name: walmart_suspension_embeddings; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_suspension_embeddings (
    history_id bigint NOT NULL,
    text_embedding public.vector(1024),
    embedding_model text DEFAULT 'qwen-text-embedding-v3'::text,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.walmart_suspension_embeddings OWNER TO nextderboy;

--
-- Name: walmart_suspension_history; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.walmart_suspension_history (
    id bigint NOT NULL,
    shop text,
    amazon_asin text,
    walmart_sku text,
    feed_id text,
    title text,
    title_clean text,
    product_type text,
    price numeric(10,2),
    status text,
    status2 text,
    reason_raw text,
    reason_category text,
    reason_subcategory text,
    is_deleted boolean DEFAULT false,
    source_sheet_id text,
    source_date date,
    flagged_at timestamp with time zone,
    imported_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.walmart_suspension_history OWNER TO nextderboy;

--
-- Name: walmart_suspension_history_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.walmart_suspension_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.walmart_suspension_history_id_seq OWNER TO nextderboy;

--
-- Name: walmart_suspension_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.walmart_suspension_history_id_seq OWNED BY public.walmart_suspension_history.id;


--
-- Name: ai_match_results id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.ai_match_results ALTER COLUMN id SET DEFAULT nextval('public.ai_match_results_id_seq'::regclass);


--
-- Name: amazon_walmart_category_map id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.amazon_walmart_category_map ALTER COLUMN id SET DEFAULT nextval('public.amazon_walmart_category_map_id_seq'::regclass);


--
-- Name: audit_flags id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.audit_flags ALTER COLUMN id SET DEFAULT nextval('public.audit_flags_id_seq'::regclass);


--
-- Name: blacklist_brands id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.blacklist_brands ALTER COLUMN id SET DEFAULT nextval('public.blacklist_brands_id_seq'::regclass);


--
-- Name: brand_nice_class id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.brand_nice_class ALTER COLUMN id SET DEFAULT nextval('public.brand_nice_class_id_seq'::regclass);


--
-- Name: company_brand_details id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.company_brand_details ALTER COLUMN id SET DEFAULT nextval('public.company_brand_details_id_seq'::regclass);


--
-- Name: etl_errors id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.etl_errors ALTER COLUMN id SET DEFAULT nextval('public.etl_errors_id_seq'::regclass);


--
-- Name: etl_progress id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.etl_progress ALTER COLUMN id SET DEFAULT nextval('public.etl_progress_id_seq'::regclass);


--
-- Name: extra_brand_details id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.extra_brand_details ALTER COLUMN id SET DEFAULT nextval('public.extra_brand_details_id_seq'::regclass);


--
-- Name: matched_companies id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.matched_companies ALTER COLUMN id SET DEFAULT nextval('public.matched_companies_id_seq'::regclass);


--
-- Name: patent_grant_assignees id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_assignees ALTER COLUMN id SET DEFAULT nextval('public.patent_grant_assignees_id_seq'::regclass);


--
-- Name: patent_grant_citations id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_citations ALTER COLUMN id SET DEFAULT nextval('public.patent_grant_citations_id_seq'::regclass);


--
-- Name: patent_grant_classifications id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_classifications ALTER COLUMN id SET DEFAULT nextval('public.patent_grant_classifications_id_seq'::regclass);


--
-- Name: patent_grant_images id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_images ALTER COLUMN id SET DEFAULT nextval('public.patent_grant_images_id_seq'::regclass);


--
-- Name: patent_grant_inventors id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_inventors ALTER COLUMN id SET DEFAULT nextval('public.patent_grant_inventors_id_seq'::regclass);


--
-- Name: path_a_results id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.path_a_results ALTER COLUMN id SET DEFAULT nextval('public.path_a_results_id_seq'::regclass);


--
-- Name: path_b_results id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.path_b_results ALTER COLUMN id SET DEFAULT nextval('public.path_b_results_id_seq'::regclass);


--
-- Name: product_audits id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.product_audits ALTER COLUMN id SET DEFAULT nextval('public.product_audits_id_seq'::regclass);


--
-- Name: products_stage id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_stage ALTER COLUMN id SET DEFAULT nextval('public.products_stage_id_seq'::regclass);


--
-- Name: trademark_classes id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_classes ALTER COLUMN id SET DEFAULT nextval('public.trademark_classes_id_seq'::regclass);


--
-- Name: trademark_design_codes id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_design_codes ALTER COLUMN id SET DEFAULT nextval('public.trademark_design_codes_id_seq'::regclass);


--
-- Name: trademark_owners id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_owners ALTER COLUMN id SET DEFAULT nextval('public.trademark_owners_id_seq'::regclass);


--
-- Name: trademark_statements id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_statements ALTER COLUMN id SET DEFAULT nextval('public.trademark_statements_id_seq'::regclass);


--
-- Name: unique_brands id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.unique_brands ALTER COLUMN id SET DEFAULT nextval('public.unique_brands_id_seq'::regclass);


--
-- Name: unique_plaintiffs id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.unique_plaintiffs ALTER COLUMN id SET DEFAULT nextval('public.unique_plaintiffs_id_seq'::regclass);


--
-- Name: walmart_suspension_history id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_suspension_history ALTER COLUMN id SET DEFAULT nextval('public.walmart_suspension_history_id_seq'::regclass);


--
-- Name: ai_match_results ai_match_results_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.ai_match_results
    ADD CONSTRAINT ai_match_results_pkey PRIMARY KEY (id);


--
-- Name: amazon_walmart_category_map amazon_walmart_category_map_amazon_path_walmart_product_typ_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.amazon_walmart_category_map
    ADD CONSTRAINT amazon_walmart_category_map_amazon_path_walmart_product_typ_key UNIQUE (amazon_path, walmart_product_type);


--
-- Name: amazon_walmart_category_map amazon_walmart_category_map_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.amazon_walmart_category_map
    ADD CONSTRAINT amazon_walmart_category_map_pkey PRIMARY KEY (id);


--
-- Name: audit_flags audit_flags_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.audit_flags
    ADD CONSTRAINT audit_flags_pkey PRIMARY KEY (id);


--
-- Name: blacklist_brands blacklist_brands_brand_upper_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.blacklist_brands
    ADD CONSTRAINT blacklist_brands_brand_upper_key UNIQUE (brand_upper);


--
-- Name: blacklist_brands blacklist_brands_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.blacklist_brands
    ADD CONSTRAINT blacklist_brands_pkey PRIMARY KEY (id);


--
-- Name: brand_nice_class brand_nice_class_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.brand_nice_class
    ADD CONSTRAINT brand_nice_class_pkey PRIMARY KEY (id);


--
-- Name: company_brand_details company_brand_details_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.company_brand_details
    ADD CONSTRAINT company_brand_details_pkey PRIMARY KEY (id);


--
-- Name: etl_errors etl_errors_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.etl_errors
    ADD CONSTRAINT etl_errors_pkey PRIMARY KEY (id);


--
-- Name: etl_progress etl_progress_data_type_source_file_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.etl_progress
    ADD CONSTRAINT etl_progress_data_type_source_file_key UNIQUE (data_type, source_file);


--
-- Name: etl_progress etl_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.etl_progress
    ADD CONSTRAINT etl_progress_pkey PRIMARY KEY (id);


--
-- Name: extra_brand_details extra_brand_details_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.extra_brand_details
    ADD CONSTRAINT extra_brand_details_pkey PRIMARY KEY (id);


--
-- Name: matched_companies matched_companies_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.matched_companies
    ADD CONSTRAINT matched_companies_pkey PRIMARY KEY (id);


--
-- Name: nice_classification nice_classification_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.nice_classification
    ADD CONSTRAINT nice_classification_pkey PRIMARY KEY (code);


--
-- Name: patent_grant_assignees patent_grant_assignees_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_assignees
    ADD CONSTRAINT patent_grant_assignees_pkey PRIMARY KEY (id);


--
-- Name: patent_grant_citations patent_grant_citations_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_citations
    ADD CONSTRAINT patent_grant_citations_pkey PRIMARY KEY (id);


--
-- Name: patent_grant_classifications patent_grant_classifications_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_classifications
    ADD CONSTRAINT patent_grant_classifications_pkey PRIMARY KEY (id);


--
-- Name: patent_grant_images patent_grant_images_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_images
    ADD CONSTRAINT patent_grant_images_pkey PRIMARY KEY (id);


--
-- Name: patent_grant_inventors patent_grant_inventors_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_inventors
    ADD CONSTRAINT patent_grant_inventors_pkey PRIMARY KEY (id);


--
-- Name: patent_grants patent_grants_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grants
    ADD CONSTRAINT patent_grants_pkey PRIMARY KEY (patent_number);


--
-- Name: path_a_results path_a_results_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.path_a_results
    ADD CONSTRAINT path_a_results_pkey PRIMARY KEY (id);


--
-- Name: path_b_results path_b_results_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.path_b_results
    ADD CONSTRAINT path_b_results_pkey PRIMARY KEY (id);


--
-- Name: product_audits product_audits_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.product_audits
    ADD CONSTRAINT product_audits_pkey PRIMARY KEY (id);


--
-- Name: product_audits product_audits_stage_id_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.product_audits
    ADD CONSTRAINT product_audits_stage_id_key UNIQUE (stage_id);


--
-- Name: products_embeddings products_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_embeddings
    ADD CONSTRAINT products_embeddings_pkey PRIMARY KEY (stage_id);


--
-- Name: products_stage products_stage_batch_file_asin_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_stage
    ADD CONSTRAINT products_stage_batch_file_asin_key UNIQUE (batch_file, asin);


--
-- Name: products_stage products_stage_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_stage
    ADD CONSTRAINT products_stage_pkey PRIMARY KEY (id);


--
-- Name: status_code_mapping status_code_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.status_code_mapping
    ADD CONSTRAINT status_code_mapping_pkey PRIMARY KEY (status_code);


--
-- Name: trademark_classes trademark_classes_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_classes
    ADD CONSTRAINT trademark_classes_pkey PRIMARY KEY (id);


--
-- Name: trademark_design_codes trademark_design_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_design_codes
    ADD CONSTRAINT trademark_design_codes_pkey PRIMARY KEY (id);


--
-- Name: trademark_owners trademark_owners_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_owners
    ADD CONSTRAINT trademark_owners_pkey PRIMARY KEY (id);


--
-- Name: trademark_statements trademark_statements_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_statements
    ADD CONSTRAINT trademark_statements_pkey PRIMARY KEY (id);


--
-- Name: trademarks trademarks_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademarks
    ADD CONSTRAINT trademarks_pkey PRIMARY KEY (serial_number);


--
-- Name: tro_cases tro_cases_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.tro_cases
    ADD CONSTRAINT tro_cases_pkey PRIMARY KEY (case_number);


--
-- Name: unique_brands unique_brands_brand_clean_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.unique_brands
    ADD CONSTRAINT unique_brands_brand_clean_key UNIQUE (brand_clean);


--
-- Name: unique_brands unique_brands_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.unique_brands
    ADD CONSTRAINT unique_brands_pkey PRIMARY KEY (id);


--
-- Name: unique_plaintiffs unique_plaintiffs_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.unique_plaintiffs
    ADD CONSTRAINT unique_plaintiffs_pkey PRIMARY KEY (id);


--
-- Name: unique_plaintiffs unique_plaintiffs_plaintiff_clean_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.unique_plaintiffs
    ADD CONSTRAINT unique_plaintiffs_plaintiff_clean_key UNIQUE (plaintiff_clean);


--
-- Name: walmart_product_types walmart_product_types_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_product_types
    ADD CONSTRAINT walmart_product_types_pkey PRIMARY KEY (product_type);


--
-- Name: walmart_suspension_embeddings walmart_suspension_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_suspension_embeddings
    ADD CONSTRAINT walmart_suspension_embeddings_pkey PRIMARY KEY (history_id);


--
-- Name: walmart_suspension_history walmart_suspension_history_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_suspension_history
    ADD CONSTRAINT walmart_suspension_history_pkey PRIMARY KEY (id);


--
-- Name: walmart_suspension_history walmart_suspension_history_walmart_sku_source_date_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_suspension_history
    ADD CONSTRAINT walmart_suspension_history_walmart_sku_source_date_key UNIQUE (walmart_sku, source_date);


--
-- Name: idx_af_audit; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_af_audit ON public.audit_flags USING btree (audit_id);


--
-- Name: idx_af_category; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_af_category ON public.audit_flags USING btree (flag_category);


--
-- Name: idx_af_code; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_af_code ON public.audit_flags USING btree (flag_code);


--
-- Name: idx_awm_a_bsr; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_awm_a_bsr ON public.amazon_walmart_category_map USING btree (recommended_amazon_bsr);


--
-- Name: idx_awm_a_l1; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_awm_a_l1 ON public.amazon_walmart_category_map USING btree (recommended_amazon_l1);


--
-- Name: idx_awm_a_l3; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_awm_a_l3 ON public.amazon_walmart_category_map USING btree (recommended_amazon_l3);


--
-- Name: idx_awm_amazon; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_awm_amazon ON public.amazon_walmart_category_map USING btree (amazon_path);


--
-- Name: idx_awm_root; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_awm_root ON public.amazon_walmart_category_map USING btree (amazon_root_id);


--
-- Name: idx_awm_wmpt; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_awm_wmpt ON public.amazon_walmart_category_map USING btree (walmart_product_type);


--
-- Name: idx_bb_multi; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_bb_multi ON public.blacklist_brands USING btree (is_multi_word);


--
-- Name: idx_bb_single; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_bb_single ON public.blacklist_brands USING btree (brand_upper) WHERE (is_multi_word = false);


--
-- Name: idx_bb_upper; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_bb_upper ON public.blacklist_brands USING btree (brand_upper);


--
-- Name: idx_bnc_class; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_bnc_class ON public.brand_nice_class USING btree (nice_class);


--
-- Name: idx_bnc_class_live; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_bnc_class_live ON public.brand_nice_class USING btree (nice_class) WHERE is_live;


--
-- Name: idx_bnc_upper; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_bnc_upper ON public.brand_nice_class USING btree (brand_upper);


--
-- Name: idx_bnc_upper_live; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_bnc_upper_live ON public.brand_nice_class USING btree (brand_upper) WHERE is_live;


--
-- Name: idx_cbd_company; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_cbd_company ON public.company_brand_details USING btree (real_company);


--
-- Name: idx_ebd_company; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_ebd_company ON public.extra_brand_details USING btree (real_company);


--
-- Name: idx_etl_progress_lookup; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_etl_progress_lookup ON public.etl_progress USING btree (data_type, status);


--
-- Name: idx_matched_brand; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_matched_brand ON public.matched_companies USING btree (brand_clean);


--
-- Name: idx_matched_case; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_matched_case ON public.matched_companies USING btree (case_number);


--
-- Name: idx_matched_company; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_matched_company ON public.matched_companies USING btree (real_company);


--
-- Name: idx_matched_plaintiff; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_matched_plaintiff ON public.matched_companies USING btree (plaintiff_clean);


--
-- Name: idx_pa_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pa_asin ON public.product_audits USING btree (asin);


--
-- Name: idx_pa_batch; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pa_batch ON public.product_audits USING btree (batch_file);


--
-- Name: idx_pa_nice_class; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pa_nice_class ON public.product_audits USING btree (nice_class);


--
-- Name: idx_pa_pt_final; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pa_pt_final ON public.product_audits USING btree (walmart_pt_final);


--
-- Name: idx_pa_verdict; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pa_verdict ON public.product_audits USING btree (verdict);


--
-- Name: idx_pa_violation_cat; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pa_violation_cat ON public.product_audits USING btree (violation_risk_category);


--
-- Name: idx_path_a_plaintiff; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_path_a_plaintiff ON public.path_a_results USING btree (plaintiff_clean);


--
-- Name: idx_path_b_brand; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_path_b_brand ON public.path_b_results USING btree (brand_clean);


--
-- Name: idx_pg_app_number; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pg_app_number ON public.patent_grants USING btree (application_number);


--
-- Name: idx_pg_appl_type; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pg_appl_type ON public.patent_grants USING btree (appl_type);


--
-- Name: idx_pg_grant_date; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pg_grant_date ON public.patent_grants USING btree (grant_date);


--
-- Name: idx_pg_title_trgm; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pg_title_trgm ON public.patent_grants USING gin (title public.gin_trgm_ops);


--
-- Name: idx_pga_orgname_trgm; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pga_orgname_trgm ON public.patent_grant_assignees USING gin (orgname public.gin_trgm_ops);


--
-- Name: idx_pga_patent; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pga_patent ON public.patent_grant_assignees USING btree (patent_number);


--
-- Name: idx_pgc_code; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pgc_code ON public.patent_grant_classifications USING btree (code);


--
-- Name: idx_pgc_patent; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pgc_patent ON public.patent_grant_classifications USING btree (patent_number);


--
-- Name: idx_pgcit_patent; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pgcit_patent ON public.patent_grant_citations USING btree (patent_number);


--
-- Name: idx_pgi_patent; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pgi_patent ON public.patent_grant_inventors USING btree (patent_number);


--
-- Name: idx_pgimg_patent; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_pgimg_patent ON public.patent_grant_images USING btree (patent_number);


--
-- Name: idx_ps_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_ps_asin ON public.products_stage USING btree (asin);


--
-- Name: idx_ps_brand_trgm; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_ps_brand_trgm ON public.products_stage USING gin (brand public.gin_trgm_ops);


--
-- Name: idx_ps_root_cat; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_ps_root_cat ON public.products_stage USING btree (root_category_id);


--
-- Name: idx_ps_title_trgm; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_ps_title_trgm ON public.products_stage USING gin (title public.gin_trgm_ops);


--
-- Name: idx_status_mapping_live_dead; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_status_mapping_live_dead ON public.status_code_mapping USING btree (live_dead);


--
-- Name: idx_tm_classes_serial; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_tm_classes_serial ON public.trademark_classes USING btree (serial_number);


--
-- Name: idx_tm_design_codes_serial; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_tm_design_codes_serial ON public.trademark_design_codes USING btree (serial_number);


--
-- Name: idx_tm_owners_name_trgm; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_tm_owners_name_trgm ON public.trademark_owners USING gin (party_name public.gin_trgm_ops);


--
-- Name: idx_tm_owners_serial; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_tm_owners_serial ON public.trademark_owners USING btree (serial_number);


--
-- Name: idx_tm_statements_serial; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_tm_statements_serial ON public.trademark_statements USING btree (serial_number);


--
-- Name: idx_tm_statements_text_trgm; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_tm_statements_text_trgm ON public.trademark_statements USING gin (text public.gin_trgm_ops);


--
-- Name: idx_trademarks_filing_date; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_trademarks_filing_date ON public.trademarks USING btree (filing_date);


--
-- Name: idx_trademarks_mark_btree; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_trademarks_mark_btree ON public.trademarks USING btree (mark_identification) WHERE (length(mark_identification) <= 200);


--
-- Name: idx_trademarks_mark_trgm; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_trademarks_mark_trgm ON public.trademarks USING gin (mark_identification public.gin_trgm_ops);


--
-- Name: idx_trademarks_reg_number; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_trademarks_reg_number ON public.trademarks USING btree (registration_number);


--
-- Name: idx_trademarks_status; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_trademarks_status ON public.trademarks USING btree (status_code);


--
-- Name: idx_wsh_asin; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_wsh_asin ON public.walmart_suspension_history USING btree (amazon_asin);


--
-- Name: idx_wsh_date; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_wsh_date ON public.walmart_suspension_history USING btree (source_date);


--
-- Name: idx_wsh_deleted; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_wsh_deleted ON public.walmart_suspension_history USING btree (is_deleted);


--
-- Name: idx_wsh_ptype; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_wsh_ptype ON public.walmart_suspension_history USING btree (product_type);


--
-- Name: idx_wsh_reason; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_wsh_reason ON public.walmart_suspension_history USING btree (reason_category);


--
-- Name: idx_wsh_title_trgm; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_wsh_title_trgm ON public.walmart_suspension_history USING gin (title_clean public.gin_trgm_ops);


--
-- Name: audit_flags audit_flags_audit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.audit_flags
    ADD CONSTRAINT audit_flags_audit_id_fkey FOREIGN KEY (audit_id) REFERENCES public.product_audits(id) ON DELETE CASCADE;


--
-- Name: patent_grant_assignees patent_grant_assignees_patent_number_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_assignees
    ADD CONSTRAINT patent_grant_assignees_patent_number_fkey FOREIGN KEY (patent_number) REFERENCES public.patent_grants(patent_number);


--
-- Name: patent_grant_citations patent_grant_citations_patent_number_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_citations
    ADD CONSTRAINT patent_grant_citations_patent_number_fkey FOREIGN KEY (patent_number) REFERENCES public.patent_grants(patent_number);


--
-- Name: patent_grant_classifications patent_grant_classifications_patent_number_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_classifications
    ADD CONSTRAINT patent_grant_classifications_patent_number_fkey FOREIGN KEY (patent_number) REFERENCES public.patent_grants(patent_number);


--
-- Name: patent_grant_images patent_grant_images_patent_number_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_images
    ADD CONSTRAINT patent_grant_images_patent_number_fkey FOREIGN KEY (patent_number) REFERENCES public.patent_grants(patent_number);


--
-- Name: patent_grant_inventors patent_grant_inventors_patent_number_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.patent_grant_inventors
    ADD CONSTRAINT patent_grant_inventors_patent_number_fkey FOREIGN KEY (patent_number) REFERENCES public.patent_grants(patent_number);


--
-- Name: product_audits product_audits_stage_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.product_audits
    ADD CONSTRAINT product_audits_stage_id_fkey FOREIGN KEY (stage_id) REFERENCES public.products_stage(id) ON DELETE CASCADE;


--
-- Name: products_embeddings products_embeddings_stage_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.products_embeddings
    ADD CONSTRAINT products_embeddings_stage_id_fkey FOREIGN KEY (stage_id) REFERENCES public.products_stage(id) ON DELETE CASCADE;


--
-- Name: trademark_classes trademark_classes_serial_number_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_classes
    ADD CONSTRAINT trademark_classes_serial_number_fkey FOREIGN KEY (serial_number) REFERENCES public.trademarks(serial_number);


--
-- Name: trademark_design_codes trademark_design_codes_serial_number_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_design_codes
    ADD CONSTRAINT trademark_design_codes_serial_number_fkey FOREIGN KEY (serial_number) REFERENCES public.trademarks(serial_number);


--
-- Name: trademark_owners trademark_owners_serial_number_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_owners
    ADD CONSTRAINT trademark_owners_serial_number_fkey FOREIGN KEY (serial_number) REFERENCES public.trademarks(serial_number);


--
-- Name: trademark_statements trademark_statements_serial_number_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.trademark_statements
    ADD CONSTRAINT trademark_statements_serial_number_fkey FOREIGN KEY (serial_number) REFERENCES public.trademarks(serial_number);


--
-- Name: walmart_suspension_embeddings walmart_suspension_embeddings_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.walmart_suspension_embeddings
    ADD CONSTRAINT walmart_suspension_embeddings_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.walmart_suspension_history(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict KxSmD0VQi0oKTkoHHPHUxRusc9xwFEGIMi1zr86nu1MbghaSCxkjha3i9WIzuVc

