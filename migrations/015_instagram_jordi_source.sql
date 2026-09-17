-- 015_instagram_jordi_source.sql — re-assert the ig:jordisanildefonso source row.
-- Idempotent: safe to apply repeatedly (ON CONFLICT (name) DO NOTHING).
--
-- Why a source-only migration: 007 is immutable and already applied, 012 owns
-- the ig:sabrikolod row — the second ADR-0013 premium account gets the same
-- re-assert pattern in its own file (deterministic stdlib uuid5 id, literal
-- embedded the same way). ON CONFLICT (name) DO NOTHING: identity is the name
-- UNIQUE column (ingest resolves source_id by name), so a legacy
-- gen_random_uuid() row for the same name is preserved and never re-keyed.
INSERT INTO sources (id, name, rss_url, hub_url, language, enabled)
VALUES ('73f59f5c-fecb-5220-bec8-93d28c2d9edb', 'ig:jordisanildefonso', NULL, 'https://www.instagram.com/jordisanildefonso/', 'es', true)
ON CONFLICT (name) DO NOTHING;
