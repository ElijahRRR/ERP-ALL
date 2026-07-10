--
-- PostgreSQL database dump
--

\restrict x4IgbtAaekoWKCw2MDpQQYWutwpbUOhp8Bes6o5sypYo6X0QdgaMlztE4DiNKjN

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
-- Name: error_items; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.error_items (
    id bigint NOT NULL,
    run_ts timestamp without time zone NOT NULL,
    store text NOT NULL,
    sku text NOT NULL,
    wpid text,
    gtin text,
    upc text,
    product_name text,
    product_type text,
    status text,
    lifecycle text,
    price numeric,
    inventory integer,
    last_updated text,
    reason text,
    category character(1),
    delete_time text,
    feed_id text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.error_items OWNER TO nextderboy;

--
-- Name: error_items_id_seq; Type: SEQUENCE; Schema: public; Owner: nextderboy
--

CREATE SEQUENCE public.error_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.error_items_id_seq OWNER TO nextderboy;

--
-- Name: error_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: nextderboy
--

ALTER SEQUENCE public.error_items_id_seq OWNED BY public.error_items.id;


--
-- Name: runs; Type: TABLE; Schema: public; Owner: nextderboy
--

CREATE TABLE public.runs (
    run_ts timestamp without time zone NOT NULL,
    source text DEFAULT 'live'::text NOT NULL,
    total_rows integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.runs OWNER TO nextderboy;

--
-- Name: error_items id; Type: DEFAULT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.error_items ALTER COLUMN id SET DEFAULT nextval('public.error_items_id_seq'::regclass);


--
-- Name: error_items error_items_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.error_items
    ADD CONSTRAINT error_items_pkey PRIMARY KEY (id);


--
-- Name: error_items error_items_run_ts_store_sku_key; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.error_items
    ADD CONSTRAINT error_items_run_ts_store_sku_key UNIQUE (run_ts, store, sku);


--
-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: public; Owner: nextderboy
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (run_ts);


--
-- Name: idx_error_items_category; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_error_items_category ON public.error_items USING btree (category);


--
-- Name: idx_error_items_sku; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_error_items_sku ON public.error_items USING btree (sku);


--
-- Name: idx_error_items_store; Type: INDEX; Schema: public; Owner: nextderboy
--

CREATE INDEX idx_error_items_store ON public.error_items USING btree (store);


--
-- PostgreSQL database dump complete
--

\unrestrict x4IgbtAaekoWKCw2MDpQQYWutwpbUOhp8Bes6o5sypYo6X0QdgaMlztE4DiNKjN

