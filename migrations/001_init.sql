-- 001_init.sql — foundation schema (Ticket 01).
-- Idempotent: safe to apply repeatedly (IF NOT EXISTS / ON CONFLICT DO NOTHING).
-- Requires the pgvector image (provides the `vector` extension); no vector
-- columns yet — the extension is enabled now so later lanes add them freely.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS sources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT UNIQUE NOT NULL,
  rss_url TEXT,
  hub_url TEXT,
  language TEXT,
  enabled BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id UUID REFERENCES sources(id),
  url TEXT UNIQUE NOT NULL,
  canonical_url TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NULL,
  published_at TIMESTAMPTZ NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL,
  language TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT UNIQUE NOT NULL,
  story_id UUID NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents (content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_url ON documents (url);
CREATE INDEX IF NOT EXISTS idx_documents_published_at ON documents (published_at);

-- Seed: first RSS source (Social Media Today).
INSERT INTO sources (name, rss_url, hub_url, language, enabled)
VALUES (
  'Social Media Today',
  'https://www.socialmediatoday.com/feeds/news/',
  'https://www.socialmediatoday.com/',
  'en',
  true
)
ON CONFLICT (name) DO NOTHING;
