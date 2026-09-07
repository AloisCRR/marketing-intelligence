-- 007_deterministic_sources_and_not_null.sql — deterministic source identity + NULL-source quarantine.
-- Idempotent: safe to apply repeatedly (ON CONFLICT DO NOTHING / IF NOT EXISTS everywhere;
-- quarantine INSERT is ON CONFLICT (id) DO NOTHING; the trailing DELETE/SET NOT NULL are
-- no-ops once no NULL rows remain).
--
-- (i) Deterministic identity WITHOUT a new dependency: new source rows take ids from
-- stdlib uuid5(NAMESPACE_DNS, "trend-intelligence-brain:source:<name>") — see
-- brain.db.source_uuid(), which computes the same values (single stdlib `uuid` import,
-- no duplicate library). Rows seeded earlier by 001/006 with gen_random_uuid() ids are
-- preserved via ON CONFLICT (name) DO NOTHING: identity is the name UNIQUE column
-- (ingest.py resolves source_id by name), so id stability across old/new rows is
-- not required — only that every curated name exists exactly once.
-- Values below re-assert the full 20-source curated set
-- (.scratch/trend-intelligence-brain/curated-sources.json; language mapped to the
-- ISO codes the table already uses: en/pt/es).

INSERT INTO sources (id, name, rss_url, hub_url, language, enabled)
VALUES
  ('23d2dd3d-46df-533e-b929-190adf7d5200', 'Consumidor Moderno', NULL, 'https://consumidormoderno.com.br/', 'pt', true),
  ('8b81eb8c-9b37-546f-bd6f-6296f6e8d8e2', 'Exame', NULL, 'https://exame.com/', 'pt', true),
  ('9f560689-e109-54d7-a9af-7c6f569cbb1c', 'Forbes México', NULL, 'https://forbes.com.mx/', 'es', true),
  ('37e4368b-b17d-5ab4-9ea4-dea8d1dc211b', 'InfoMoney', 'https://www.infomoney.com.br/feed', 'https://www.infomoney.com.br/', 'pt', true),
  ('af9ea377-e0a2-51ca-9c96-36072e0eb5e5', 'Insider Latam', NULL, 'https://insiderlatam.com/', 'es', true),
  ('1fc898f5-d7af-5234-ba42-91993a6fab66', 'JCK Online', 'https://www.jckonline.com/feed/', 'https://www.jckonline.com/category/news-trends/retail/', 'en', true),
  ('2a65c136-efae-5720-a975-a532ba2f536e', 'Jing Daily', NULL, 'https://jingdaily.com/', 'en', true),
  ('09c9ed93-4c58-56a1-afbc-97d19caff587', 'LVMH Press Releases', NULL, 'https://www.lvmh.com/news-documents/press-releases/', 'en', true),
  ('c2ab2b37-65df-5f2d-8e70-9acb1cfe0bd5', 'MarTech', 'https://martech.org/feed/', 'https://martech.org/', 'en', true),
  ('470ce48f-1605-598f-a707-ecea1a5aaa02', 'Marketing Dive', NULL, 'https://www.marketingdive.com/', 'en', true),
  ('41e46aa1-a3ef-5c8d-a9b8-43c91388798d', 'MarketingDirecto', NULL, 'https://www.marketingdirecto.com/', 'es', true),
  ('e4f72bda-b707-5433-8f1c-f8556c5c61b8', 'Meio & Mensagem', NULL, 'https://www.meioemensagem.com.br/', 'pt', true),
  ('bc3fd36d-24c0-5417-9445-9b46e5d0d06c', 'Modaes', NULL, 'https://www.modaes.com/', 'es', true),
  ('dbe690fc-00c1-5fb4-a905-2b257d741313', 'National Jeweler', NULL, 'https://nationaljeweler.com/industry', 'en', true),
  ('d5792920-83ea-5148-83d4-450e7fbd82e0', 'Professional Jeweller', 'https://www.professionaljeweller.com/feed/', 'https://www.professionaljeweller.com/', 'en', true),
  ('3a5df9a2-efa5-500f-b42f-7f4db2ac94f8', 'Propmark', NULL, 'https://propmark.com.br/', 'pt', true),
  ('df995d8d-448d-5517-bd67-c0907400d4ed', 'Retail Dive', NULL, 'https://www.retaildive.com/topic/consumer-trends/', 'en', true),
  ('7583c00a-3532-5443-86cd-778e33750a91', 'Richemont Media', NULL, 'https://www.richemont.com/news-media/press-releases-news/', 'en', true),
  ('608e3e04-35f7-5b47-9dec-3ca95f61d8fe', 'Social Media Today', 'https://www.socialmediatoday.com/feeds/news/', 'https://www.socialmediatoday.com/', 'en', true),
  ('84e3dc65-d1b2-5f86-b8e5-4b604cec9dfd', 'Swarovski PR Newswire', 'https://www.prnewswire.com/rss/swarovski', 'https://www.prnewswire.com/news/swarovski/', 'en', true)
ON CONFLICT (name) DO NOTHING;

-- Join helper for source_id lookups (rerun-safe).
CREATE INDEX IF NOT EXISTS idx_documents_source_id ON documents (source_id);

-- (ii-a) Quarantine, NOT backfill: documents carries no source_name column, so a
-- NULL source_id row has no recoverable provenance — attributing it to any source
-- by guesswork would fabricate evidence. Preserve the rows explicitly instead.
CREATE TABLE IF NOT EXISTS quarantined_documents (
  id UUID PRIMARY KEY,
  source_id UUID NULL,
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NULL,
  published_at TIMESTAMPTZ NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL,
  language TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  quarantine_reason TEXT NOT NULL DEFAULT 'source_id IS NULL at 007 quarantine'
);

INSERT INTO quarantined_documents
  (id, source_id, url, canonical_url, title, author,
   published_at, retrieved_at, language, content, content_hash)
SELECT id, source_id, url, canonical_url, title, author,
       published_at, retrieved_at, language, content, content_hash
  FROM documents WHERE source_id IS NULL
ON CONFLICT (id) DO NOTHING;

DELETE FROM documents WHERE source_id IS NULL;

-- (ii-b) ingestion_runs.source_name is already NOT NULL (003_ingestion_runs.sql);
-- this removes only rows violating it on legacy DBs with schema drift. Valid run
-- history is never touched (no source_name-based deletion of attributed runs).
DELETE FROM ingestion_runs WHERE source_name IS NULL;

-- (iii) Enforce AFTER the quarantine above, so the guard holds on every DB this
-- migration touches (fresh or legacy). Future unknown-source upserts raise
-- ValueError in upsert_documents (fail-fast guard refusing the NULL source_id
-- insert); the leaf flow propagates and the batch flow records the explicit
-- per-source error — no silent unattributed rows. This DB-level NOT NULL is
-- the backstop for any other writer; no ingest.py change required.
ALTER TABLE documents ALTER COLUMN source_id SET NOT NULL;
