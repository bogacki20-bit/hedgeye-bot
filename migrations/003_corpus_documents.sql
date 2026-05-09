-- ============================================================================
-- Migration 003 — corpus_documents
--
-- Stores text documents captured for the bot's corpus: Macro Show summaries
-- and slide-deck text, Hedgeye U lessons + quizzes, VolSignals YouTube
-- transcripts, SpotGamma daily captures, and historical emails. The bot's
-- classifier and recommender pull RAG context from this table when reasoning
-- about live signals.
--
-- Apply via apply_migration.py (existing helper that uses DATABASE_PUBLIC_URL).
-- All statements idempotent — safe to re-run.
-- ============================================================================

CREATE TABLE IF NOT EXISTS corpus_documents (
    id              BIGSERIAL PRIMARY KEY,

    -- Where this content came from. Examples:
    --   'macro_show_dashboard', 'macro_show_summary_notes', 'macro_show_deck',
    --   'macro_themes_update', 'hedgeye_u_lesson', 'hedgeye_u_quiz',
    --   'volsignals_youtube', 'spotgamma_founders_note', 'spotgamma_flowpatrol',
    --   'spotgamma_equityhub', 'risk_range_email', 'rta_email', 'email'
    source          TEXT NOT NULL,

    -- The original publish date of the content (NOT capture date).
    -- Critical for time-aware RAG (e.g. "what did Keith say about energy in March?").
    source_date     DATE NOT NULL,

    -- Stable identifier within source. For YouTube videos, the 11-char video ID.
    -- For Macro Show decks, the date string (e.g. '5.8.2026'). For lessons,
    -- the lesson slug. NULL if not applicable.
    source_ref      TEXT,

    -- Document shape - constrains how the bot uses the text.
    --   'transcript', 'slide_deck_text', 'webpage_text', 'email_text', 'note'
    document_type   TEXT NOT NULL,

    -- Page number for decks, segment index for chunked transcripts. NULL for
    -- single-blob documents.
    page_or_segment INTEGER,

    title           TEXT NOT NULL,
    full_text       TEXT NOT NULL,

    -- Free-form metadata: tickers mentioned, quad regime referenced, sentiment,
    -- timestamps for video segments, slide labels, etc.
    metadata        JSONB DEFAULT '{}'::jsonb,

    captured_at     TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Most queries will filter by source + date range, then by source_ref.
CREATE INDEX IF NOT EXISTS corpus_documents_source_date
    ON corpus_documents (source, source_date DESC);

CREATE INDEX IF NOT EXISTS corpus_documents_source_ref
    ON corpus_documents (source, source_ref);

-- For full-text search until we add pgvector embeddings.
CREATE INDEX IF NOT EXISTS corpus_documents_fts
    ON corpus_documents
    USING gin (to_tsvector('english', full_text));

-- Prevent duplicate inserts when the ingestor runs against the same files
-- multiple times. (source, source_ref, source_date, page_or_segment) is unique.
-- Use COALESCE to make NULL == NULL for page_or_segment.
CREATE UNIQUE INDEX IF NOT EXISTS corpus_documents_unique
    ON corpus_documents (source, COALESCE(source_ref, ''), source_date, COALESCE(page_or_segment, -1));
