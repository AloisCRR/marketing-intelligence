-- 006_seed_all_sources.sql — seed the full 20-source curated set (Ticket 07).
-- Idempotent: safe to apply repeatedly (ON CONFLICT (name) DO NOTHING).
-- The 4 V1 sources already live in 001_init.sql; this file adds the other 16
-- (2 RSS extras + 14 no-RSS hub/sitemap sources, rss_url NULL). Retrieval
-- stanzas (discovery type, extractor family, pacing) live in
-- .scratch/trend-intelligence-brain/curated-sources.json and the
-- brain.sources registry — the table carries identity/provenance columns only.

INSERT INTO sources (name, rss_url, hub_url, language, enabled)
VALUES
  ('JCK Online', 'https://www.jckonline.com/feed/', 'https://www.jckonline.com/category/news-trends/retail/', 'en', true),
  ('Swarovski PR Newswire', 'https://www.prnewswire.com/rss/swarovski', 'https://www.prnewswire.com/news/swarovski/', 'en', true),
  ('National Jeweler', NULL, 'https://nationaljeweler.com/industry', 'en', true),
  ('Exame', NULL, 'https://exame.com/', 'pt', true),
  ('Modaes', NULL, 'https://www.modaes.com/', 'es', true),
  ('Retail Dive', NULL, 'https://www.retaildive.com/topic/consumer-trends/', 'en', true),
  ('Jing Daily', NULL, 'https://jingdaily.com/', 'en', true),
  ('Consumidor Moderno', NULL, 'https://consumidormoderno.com.br/', 'pt', true),
  ('Meio & Mensagem', NULL, 'https://www.meioemensagem.com.br/', 'pt', true),
  ('Marketing Dive', NULL, 'https://www.marketingdive.com/', 'en', true),
  ('MarketingDirecto', NULL, 'https://www.marketingdirecto.com/', 'es', true),
  ('Propmark', NULL, 'https://propmark.com.br/', 'pt', true),
  ('Insider Latam', NULL, 'https://insiderlatam.com/', 'es', true),
  ('LVMH Press Releases', NULL, 'https://www.lvmh.com/news-documents/press-releases/', 'en', true),
  ('Richemont Media', NULL, 'https://www.richemont.com/news-media/press-releases-news/', 'en', true),
  ('Forbes México', NULL, 'https://forbes.com.mx/', 'es', true)
ON CONFLICT (name) DO NOTHING;
