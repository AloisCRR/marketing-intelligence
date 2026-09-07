# Asad Ikram's Web Scraping & Anti-Bot Bypass Guide 2026
Source: https://web-scraping-guide.com/
Author: Asad Ikram — Data Engineer, 7 years production scraping
Generated: 2026-09-06

---

## HOW TO USE THIS CONTEXT

Paste this file as a system prompt or context into Claude, ChatGPT, Cursor, or any LLM.
Then ask questions like:
- "How do I bypass Cloudflare Turnstile with Python?"
- "What's the best library for scraping a site with Akamai Bot Manager v3?"
- "Explain the difference between JA3 and JA4+ TLS fingerprinting"
- "Write a scrapy-stealth middleware config for my Scrapy spider"
- "Why is curl_cffi better than requests for scraping?"
- "What is the _abck cookie and why won't it flip from ~-1~ to ~0~?"

---

## QUICK REFERENCE, DECISION TREES

### Step 0: place the target on the difficulty ladder

Tier 1 OPEN: public API or clean data in HTML. Tell: visible in view-source. Effort: an afternoon.
Tier 2 LIGHT DEFENSE: rate limits, UA checks. Tell: works, then 429s once you scale. Effort: real headers + rate control.
Tier 3 DYNAMIC: data loads via JS/XHR/GraphQL. Tell: view-source empty, page full when rendered. Effort: find the API behind the page.
Tier 4 FINGERPRINTING: TLS/JA3 + browser fingerprint. Tell: your code blocked instantly, site fine in your browser. Effort: engine-level stealth; proxies alone will not do it.
Tier 5 BEHAVIOURAL: full Akamai/DataDome/Cloudflare, scoring mouse, timing, path. Tell: pass once, flagged when you scale. Effort: where most teams stall.
Tiers are cumulative. Decide the rung before you promise a deadline.

### General scraping priority (stop at the first win)

1. Mobile or GraphQL API, often zero anti-bot, same data
2. XHR endpoint via DevTools, or record the page's own fetches automatically
3. JSON inside the HTML: __NEXT_DATA__, window.__data__, chompjs, JSON-LD
4. HTTP client with TLS impersonation: curl_cffi (default), primp or impit at volume, plus a residential or ISP proxy
5. Rebuilt browser: Camoufox, CloakBrowser, zendriver, rayobrowse. Rebuilding beats patching flags
6. Managed API for the hardest tier: Bright Data, Zyte, Scrapfly

### TLS spoofing tiers

Tier 1 (most cases): curl_cffi with a current impersonate profile
Tier 2 (Scrapy): scrapy-impersonate or scrapy-stealth download handler
Tier 3 (volume): primp or impit, Rust cores, impit is now Crawlee's default client
Tier 4 (hardest): rebuilt browser, or a browser to mint the session and an HTTP client to replay it

### The detection stack, in the order it fires

Before your JS runs at all, the server already holds: IP reputation, TLS fingerprint (JA3/JA4), header values, header ORDER and casing, ALPN and ciphers, Client Hints, request timing and rate. Seven signals, zero cooperation from your code.
Layer 1 TLS: JA3/JA4 from the ClientHello. Note JA4 is not perfectly stable, ephemeral extensions on TLS 1.3 resumption (pre_shared_key, padding) change the hash; JA4E strips them.
Layer 2 HTTP: header order, and on HTTP/2 the pseudo-header order (Chrome sends :method :authority :scheme :path). Right values in the wrong order is a tell.
Layer 3 JS: canvas, WebGL, AudioContext, navigator.webdriver, CDP artifacts, and OS-level oracles like Math.tanh which reads the host libm.
Layer 4 Behavioural: continuous session-long scoring. A refresh does not reset it, and Gaussian-noise mouse movement fails a model that knows real motion has structure.

### Core principles

COHERENCE BEATS PERFECTION. You are not caught for being a bot, you are caught for being incoherent. Every layer must tell one story.
Reputation over resemblance. Modern TLS defence judges what a fingerprint DOES across many destinations, not what it looks like. Most bot traffic is real Chrome being driven, not fake Chrome.
More realism is not more human. A full browser header bundle from a client running no JS is itself the tell; a lean honest header set often passes where the full one fails.
Rotate sessions, not IPs. One session equals one exit IP plus coherent cookies, headers and geo, held stable. Rotating IPs under one fixed fingerprint announces one client behind many addresses.
A 200 is not success. Check the body for a real content marker; a captcha shell returns 200 too.
Blocked pages come back FASTER than real ones, so a latency-only throttle speeds up into a ban. React to block signals, not speed.

### Cost model

Spend the model once at build time, never at runtime. Bootstrap the session once and call the API directly: one measured case was about 19.25 MB per full page load versus about 22 KB per subsequent API call, roughly 875x less bandwidth.
Treat any vendor success rate as a dated snapshot. Ask five questions: pass criterion, target set, tier, reproducible harness, freshness. An honest benchmark shows its own failures.
Software stealth has a runtime price. The same infrastructure benchmarks materially slower with the stealth layer on, so size fleets against your real configuration.

### Access, identity and ethics

Three tiers of automated access: declared good bots, mixed-use crawlers, and the undeclared gray economy. The rules being written protect the first and squeeze the last.
Web Bot Auth lets a client cryptographically sign its requests, so an allowlist rests on proof rather than a copyable User-Agent string. Identity is still not authorisation; the publisher decides what a verified operator may do.
Over-aggressive crawling is a conduct problem. Respect robots.txt and rate limits, prefer public data, and remember residential proxy pools can be sourced from people who never consented.

---

KNOWLEDGE GRAPH — read this before the prose below.

This guide is not a list of tips. Almost every finding in it is an instance of one of 18 principles,
and those principles attach to the layers at which a request is judged. Node ids are stable; the
detailed prose for a node appears later in this document under a matching heading.

NODE TYPES: P principle | L detection layer | C method or practice area | V anti-bot vendor |
            T tool | E measured evidence | W law
EDGE VERBS: at=operates at | beats=defeats | catch=catches | then=judged before |
            caseof=instance of | backs=evidence for | cuts=limits or contradicts | costs=economic consequence

HOW TO REASON WITH IT
1. To answer "why am I blocked", walk the layers IN ORDER (L0 then L1 ... L7) and establish which layer
   the symptom belongs to BEFORE recommending a tool. Most wasted effort is a layer-3 fix for a layer-1 refusal.
2. To check whether a claim is supported, follow the backs edges from evidence nodes into the principle.
3. Edges marked cuts are the COUNTER-EVIDENCE. Quote them alongside the claim they limit, never omit them.
4. A tool node is only a recommendation once you know the layer. Tools carry beats and cuts edges to layers
   precisely so you can tell what they do not solve.

COUNTS: 187 nodes, 319 edges.


== PRINCIPLES ==

[P1] Coherence, not realism  (section: #detect)
  You are not blocked for looking like a bot. You are blocked for presenting claims that cannot describe one real device. Adding more realism adds another thing that has to match.
  edges: at L3 | at L1 | <- backs C-history | <- caseof T-fps | <- backs E-proxy | <- backs E-rand | <- caseof P19

[P2] The block is at a layer you are not working on  (section: #flow)
  Doubling down on the layer you enjoy is the commonest waste. Ask which layer refused you before choosing a tool.
  edges: at L1 | <- caseof C-flow | <- caseof C-sweep | <- backs E-ak3 | <- backs E-proxy | <- cuts E-sowa | <- caseof C-whoblocked | <- caseof E-dial | <- caseof C-identlimit | <- caseof E-hermes

[P3] Network identity dominates fingerprint quality  (section: #proxies)
  Once the IP or ASN is wrong, fingerprint quality stops being the variable. Engine choice makes no measurable difference behind a datacenter exit.
  edges: at L5 | <- backs E-15eng | <- backs E-http3 | <- backs E-obscura | <- backs W-netnut | <- backs P20

[P4] Every piece of state is a claim that can be judged  (section: #detect)
  Headers, cookies, tokens and fingerprints are assertions. Each can be held against you as easily as for you.
  edges: at L2 | <- caseof C-session | <- caseof C-login | <- backs E-429 | <- caseof C-untrusted

[P5] The best signals live in the seam between two layers  (section: #detect)
  Each component behaves correctly on its own terms and the combination leaks. TLS versus user agent, browser versus OS, web versus app.
  edges: at L1 | <- backs E-bridge | <- backs E-turn

[P6] Below the sandbox, patching stops working  (section: #detect)
  Signals produced by hardware, the OS or a shared cache cannot be spoofed from JavaScript. The answer is real machines, not better patches.
  edges: at L4 | <- caseof P24 | <- backs E-shader | <- backs E-frost | <- backs E-simd | <- backs E-math | <- costs E-bounty

[P7] The expensive failures return 200  (section: #post-extract)
  A block tells you it failed. Poisoned data, a different edition, or an empty container congratulate you instead.
  edges: <- caseof P22 | <- caseof C-throttle | <- backs E-time | <- caseof C-idem | <- caseof C-accepted | <- caseof C-susval | <- caseof C-fontpoison | <- caseof E-ddr5

[P8] Alert on the delta, not the level  (section: #post-extract)
  Coverage measured as a level catches only zero. Compare against the last good run or you will not see a collapse.
  edges: <- backs P21 | <- caseof C-dq | <- backs E-reddit | <- backs E-zyte

[P9] A row without provenance cannot be diagnosed  (section: #post-extract)
  Source URL, fetch time, status and extractor version. Four fields, and the whole difference between a diagnosis and a re-crawl.
  edges: <- caseof P21 | <- caseof C-queue | <- caseof C-dedupe | <- backs E-zyte | <- backs E-rag | <- caseof C-trail

[P10] Check the instrument before trusting the verdict  (section: #tools)
  If your health check is less capable than your scraper, it will confirm a wrong theory for weeks.
  edges: <- caseof P23 | <- caseof C-capture | <- caseof C-attr | <- caseof T-bench | <- backs E-proxy | <- backs E-zyte | <- caseof C-apimap | <- caseof C-benchread | <- caseof C-500pass | <- caseof C-renderdiff | <- caseof C-budget | <- backs E-glassbox

[P11] Agent cost is how often the model looks  (section: #agentic)
  Not how well it thinks. Every measured win came from removing round trips and schemas, not from a better model.
  edges: <- caseof C-agent | <- backs T-bu | <- backs E-tokens | <- backs C-trail | <- backs C-skillmem

[P12] Agents cannot separate instruction from content  (section: #agentic)
  Raw page text enters the model as trusted input. Architectural, so not patchable, so control the action rather than the input.
  edges: at L7 | <- caseof C-inject | <- caseof C-agent | <- cuts C-webmcp | <- backs E-bh | <- backs E-canvas | <- backs E-bots | <- cuts W-stealth | <- caseof C-untrusted | <- backs E-inject5

[P13] Protection is a rule on a route, not a property of a site  (section: #flow)
  Anti-bot is configured per endpoint by people under deadline. Enumerate every route before you accept the hard one.
  edges: <- caseof C-endpoint | <- caseof C-capture | <- caseof C-mobile | <- backs E-kasada | <- cuts E-shopee | <- caseof C-defladder | <- caseof E-facet | <- caseof C-trends | <- caseof E-fashion

[P14] Fix the class, not the instance  (section: #arch)
  Sites are instances of a smaller number of platforms. Key memory to the engine and one repair heals the whole class.
  edges: <- caseof C-heal | <- caseof T-scrapling | <- caseof T-a11y | <- caseof T-scrapy

[P15] Access was the old problem, legibility is the new one  (section: #ai)
  Reaching the page was mostly solved a decade ago. Whether a model can use what you collected is the 2026 question.
  edges: <- caseof C-doc | <- caseof C-mcp | <- caseof C-rag | <- caseof T-crawl4ai | <- backs E-rag | <- backs E-agentread | <- backs E-nojs | <- caseof C-stalefetch

[P16] Preventing is not the same as proving you prevented  (section: #tools)
  A test page showing different values measures your tool. Unlinkability at the target is the only evidence that counts.
  edges: <- backs C-community | <- cuts T-fpscan | <- backs T-bench | <- backs E-15eng | <- backs E-rand | <- backs E-glassbox

[P17] Spend the model once, at build time  (section: #cost)
  A model in the runtime loop is a per-request bill forever. Use it to write the extractor, then run the extractor.
  edges: <- caseof C-sidecar | <- caseof C-cost | <- cuts C-llm | <- backs E-cost | <- cuts C-susval | <- caseof C-skillmem | <- caseof C-warm

[P18] Escalate cheapest-first and stop at the first win  (section: #play)
  Every rung up the ladder costs more and breaks more often. The discipline is stopping, not climbing.
  edges: at L1 | <- caseof C-ladder | <- caseof C-esc | <- caseof C-managed | <- caseof T-trawl | <- cuts C-fontpoison | <- cuts C-system

[P19] A fingerprint assembled twice disagrees with itself  (section: #detect)
  Identity spread across files drifts apart. One profile module everything derives from, with values computed rather than hardcoded.
  edges: caseof P1 | at L3 | <- backs E-envelope

[P20] Your proxy pool has a provenance you can be caught downstream of  (section: #proxies)
  Residential IPs sourced from an SDK botnet are a supply-chain risk, not an ethics footnote. Ask where they come from; prefer ISP or datacenter.
  edges: backs W-netnut | backs P3 | caseof C-source

[P21] A citation asserts provenance, not currency  (section: #post-extract)
  A citation says a document at that URL contained the claim when it was indexed. It says nothing about whether it still does. Retrieval systems present the first as if it were the second.
  edges: caseof P9 | backs P8

[P22] The cheapest defence is to answer, not to refuse  (section: #detect)
  A block is loud and tells you to fix something. Plausible fabricated content returns 200, parses cleanly and enters the dataset. Refusing costs the defender a signal; lying costs them nothing.
  edges: caseof P7

[P23] A performance number without a build version is a rumour  (section: #detect)
  A pass rate names the date, the exact build and the targets, or it is unusable. The tool underneath a benchmark can change without the benchmark changing.
  edges: caseof P10

[P24] Below the OS, attestation is not forgeable at all  (section: #detect)
  A key held in secure hardware signs a token you cannot mint. Instrumentation reaches the call and stops. This is the one wall with no software answer.
  edges: caseof P6 | at L4


== LAYERS ==

[L0] Layer 0 · TCP/IP and layer 4  (section: #detect)
  SYN packet initial window, TTL, options ordering. Below the handshake, before anything you configure.
  edges: then L1

[L1] Layer 1 · TLS and JA4  (section: #detect)
  ClientHello cipher order, extensions, GREASE, ALPN. Fires before HTML is served, which is why browser patches cannot help here.
  edges: <- then L0 | then L2 | <- then L5 | <- at P1 | <- at P2 | <- at P5 | <- at P18 | <- at V-ak | <- at V-cf | <- at V-fastly | <- at C-sweep | <- beats T-curl | <- beats T-utls | <- beats T-primp | <- cuts T-nod | <- cuts T-mitm | <- cuts T-kite | <- cuts T-moli | <- at E-envelope | <- at C-trends | <- at E-hermes

[L2] Layer 2 · HTTP/2 and header order  (section: #detect)
  SETTINGS frame values, pseudo-header sequence, header ordering. A library emits a different shape from a browser.
  edges: <- then L1 | then L3 | <- at P4 | <- at V-ak | <- at V-fastly | <- beats T-curl | <- beats T-utls | <- at E-http3 | <- at E-hermes

[L3] Layer 3 · JavaScript fingerprinting  (section: #detect)
  Canvas, WebGL, AudioContext, navigator surface, extension enumeration. Only reachable once a script runs.
  edges: <- then L2 | then L4 | <- at P1 | <- at V-ak | <- at V-dd | <- at V-ka | <- at V-px | <- at V-f5 | <- cuts C-mobile | <- at C-obf | <- at C-glossary | <- cuts T-curl | <- beats T-cam | <- beats T-nod | <- beats T-cloak | <- beats T-fps | <- at P19 | <- at E-sg

[L4] Layer 4 · Hardware and OS side channels  (section: #detect)
  WASM SIMD CPU probes, math library results, disk and shader-cache timing. Produced below the JS sandbox.
  edges: <- then L3 | then L6 | <- at P6 | <- at P24 | <- at V-dd | <- cuts T-cam | <- at E-math

[L5] Layer 5 · Network identity  (section: #proxies)
  IP, ASN, WebRTC candidates, DNS resolver, geographic coherence. Five vectors that all have to agree.
  edges: then L1 | <- at P3 | <- at V-cf | <- at C-session | <- at C-source | <- at C-geo | <- beats T-trawl | <- beats T-bos | <- at E-proxyprice | <- at E-obscura

[L6] Layer 6 · Behaviour and session scoring  (section: #detect)
  Mouse curves, scroll physics, navigation paths, scored continuously across a session rather than at a challenge.
  edges: <- then L4 | then L7 | <- at V-ak | <- at V-cf | <- at V-dd | <- at V-ka | <- at V-f5 | <- at C-captcha | <- at E-precursor

[L7] Layer 7 · Declared identity and policy  (section: #legal-ethics)
  User agent, Web Bot Auth, robots posture. Increasingly a compliance question rather than an engineering one.
  edges: <- then L6 | <- at P12 | <- at C-webmcp | <- at C-robots | <- at C-webbot | <- at C-gdpr | <- at E-time | <- at E-refer | <- at E-bots | <- at W-hiq | <- at W-serp | <- at W-stealth | <- at C-402 | <- at W-serp2


== METHODS ==

[C-flow] The decision flow  (section: #flow)
  Choose between a plain HTTP client, TLS impersonation, a stealth browser and a managed API, cheapest first.
  edges: caseof P2 | <- caseof T-curl

[C-ladder] The five-tier difficulty ladder  (section: #play)
  Place the target before you promise a deadline. Difficulty is five discrete tiers, not a feeling.
  edges: caseof P18 | <- backs E-sowa

[C-esc] The escalation playbook  (section: #play)
  Walk the rungs in order and stop at the first thing that works. Most targets never need the top rung.
  edges: caseof P18

[C-sweep] Impersonation profile sweep  (section: #flow)
  The cheapest escalation win almost nobody does: try every TLS profile before you change tool.
  edges: caseof P2 | at L1 | <- backs E-ak3 | <- backs E-glassbox

[C-endpoint] Endpoint enumeration  (section: #flow)
  Read the JS bundles and list every route the frontend can call, then test each cold with a plain client.
  edges: caseof P13 | <- backs E-kasada | <- caseof C-trends | <- backs E-hermes | <- backs C-warm

[C-capture] Wire-level capture  (section: #tools)
  Record what the page fetches rather than what it renders. The listener, not the snapshot.
  edges: caseof P13 | caseof P10 | <- caseof T-mitm | <- caseof T-powhttp

[C-mobile] Mobile API interception  (section: #mobile)
  Apps talk to cleaner APIs than websites and often behind weaker protection. Intercept before the anti-bot.
  edges: caseof P13 | <- backs C-pin | <- backs C-sign | cuts L3 | <- cuts E-bridge

[C-pin] Certificate pinning and Frida  (section: #mobile)
  Pinning stops a proxy seeing traffic. Frida unpins at runtime so the wire becomes readable again.
  edges: backs C-mobile | <- beats T-frida

[C-sign] Native signing logic  (section: #mobile)
  When the signature is computed in a stripped native library, rebuild it in Python or let the app sign while you watch.
  edges: backs C-mobile | <- beats T-frida | <- backs E-shopee

[C-captcha] Challenges and CAPTCHA  (section: #antibots)
  Turnstile, reCAPTCHA, hCaptcha, proof-of-work. The visible test is the last resort, not the first line.
  edges: at L6 | <- backs E-liveness | <- backs C-mcpsolve

[C-obf] Obfuscation and deobfuscation  (section: #antibots)
  Detection scripts are packed, virtualised and rotated. Reading them is a specialism of its own.
  edges: at L3 | <- backs E-sg

[C-managed] Managed unblocker platforms  (section: #platforms)
  Buy the hardest tier rather than build it. The honest comparison is cost per successful request, not per request.
  edges: caseof P18

[C-cua] Computer-use agents  (section: #platforms)
  When the data lives behind a login the user owns, an agent operating with permission beats scraping.
  edges: backs C-agent

[C-session] Session stickiness  (section: #proxies)
  Rotate session objects, not IP addresses. One session is one exit IP plus coherent cookies, headers and geography.
  edges: caseof P4 | at L5 | <- cuts E-429 | <- backs E-precursor

[C-source] Proxy sourcing and KYC risk  (section: #proxies)
  What separates providers is where the IPs came from, not price per gigabyte. Your exit IP inherits its history.
  edges: at L5 | <- cuts E-proxyprice | <- cuts W-netnut | <- caseof P20

[C-geo] Geofeed verification  (section: #proxies)
  Measure the geography of your pool rather than buying the claim. Location data for most networks is self-declared.
  edges: at L5

[C-attr] Failure attribution  (section: #proxies)
  Log whether a failure was the proxy policy, the proxy infrastructure or the target. Otherwise you cannot argue with anyone.
  edges: caseof P10 | <- backs C-whoblocked | <- backs C-trail

[C-heal] Self-healing scrapers  (section: #arch)
  Detect drift, regenerate the selector, verify against a test, ship. The instrumentation is the hard part, not the repair.
  edges: caseof P14 | <- backs T-scrapy | <- backs E-reddit | <- cuts E-zyte | <- backs C-skillmem

[C-sidecar] Browser sidecar pattern  (section: #arch)
  Keep the browser out of the crawler. Only the requests that need one pay for one.
  edges: caseof P17

[C-throttle] Adaptive throttling  (section: #arch)
  Blocked pages return faster than real ones, so a latency-only throttle accelerates into a ban.
  edges: caseof P7 | <- backs C-courtesy | <- backs E-dial | <- cuts E-ddr5

[C-login] Scraping behind a login  (section: #arch)
  The session becomes the asset. Mint it once, carefully, and replay it rather than logging in repeatedly.
  edges: caseof P4 | <- cuts W-ryan

[C-queue] Queueing and resumability  (section: #arch)
  A crawl that cannot resume is a crawl you will run twice. State outside the process, always.
  edges: caseof P9

[C-dq] Data quality gates  (section: #post-extract)
  Row counts, field fill rates, type checks and distribution drift, compared against the last good run.
  edges: caseof P8 | <- backs C-susval | <- backs C-budget

[C-doc] Document parsing  (section: #post-extract)
  A large share of the data you need is in PDFs and scans, where there is no DOM to select from.
  edges: caseof P15 | <- cuts E-llmstxt | <- cuts E-ready

[C-dedupe] Dedupe and entity resolution  (section: #post-extract)
  The same record arrives from several routes with different keys. Resolving that is most of the pipeline.
  edges: caseof P9

[C-cost] Cost per usable document  (section: #cost)
  The only unit that matters. Retries, browsers used where a request would do, and third-party bleed are the hidden drivers.
  edges: caseof P17 | <- backs C-buy | <- backs T-primp | <- backs E-cost | <- backs C-accepted | <- backs C-system

[C-buy] Build versus buy  (section: #cost)
  Compare against the fully loaded cost of your own time and failure rate, not against the sticker price.
  edges: backs C-cost

[C-llm] LLM extraction  (section: #ai)
  Describe the fields instead of selecting them. Reliable for the easy 20%, and the rest is engineering.
  edges: cuts P17 | <- caseof T-firecrawl

[C-mcp] Scraping as an MCP server  (section: #ai)
  Name the capability and let the model call it, rather than teaching a model to drive a scraper.
  edges: caseof P15

[C-inject] Prompt injection against your own agent  (section: #ai)
  Point an agent at the open web and the page becomes an instruction channel into your infrastructure.
  edges: caseof P12 | <- backs E-canvas

[C-rag] Retrieval pipelines  (section: #ai)
  A model can only reason over what acquisition collected. Freshness, coverage and extraction fidelity are upstream of every answer.
  edges: caseof P15

[C-agent] Agentic browsers  (section: #agentic)
  An LLM in the decision layer, adapting where a script would break, and failing quietly where a script would fail loudly.
  edges: <- backs C-cua | caseof P11 | caseof P12 | <- backs T-kite | <- backs T-moli | <- backs T-bos | <- backs T-bu

[C-webmcp] WebMCP and declared tools  (section: #agentic)
  Sites handing agents a sanctioned door, which is also a public API a stranger can call with words you never wrote.
  edges: cuts P12 | at L7

[C-glossary] Plain-English glossary  (section: #jargon)
  Fingerprinting, challenges, IP reputation and robots.txt explained without the jargon.
  edges: at L3

[C-community] Community and learning  (section: #community)
  Discords, newsletters, conferences and books. Most real technique is transmitted here rather than in documentation.
  edges: backs P16

[C-history] The arms race timeline  (section: #tl)
  From IP bans to transformer-based behavioural scoring, and why each escalation produced the next.
  edges: backs P1

[C-robots] robots.txt is a norm, not a control  (section: #legal-ethics)
  It was never a security mechanism. Only the crawlers that were going to behave ever read it.
  edges: at L7 | backs W-hiq

[C-webbot] Web Bot Auth  (section: #legal-ethics)
  Cryptographically signed requests, so an allowlist rests on proof rather than a copyable user-agent string.
  edges: at L7 | <- backs W-stealth | <- backs C-402

[C-gdpr] Personal data and GDPR  (section: #legal-ethics)
  Public does not mean unregulated. A lawful basis is required whether or not the page was open.
  edges: at L7

[C-402] Pay per request at the edge (x402)  (section: #innovation)
  The edge answers 402 with its terms, the client pays, the proxy verifies before the origin runs. Web Bot Auth said who; this settles what.
  edges: backs C-webbot | at L7 | <- backs E-bots | <- backs E-refer | <- backs C-scarcity

[C-defladder] The defender cost ladder  (section: #innovation)
  Edge WAF, ingress rate limit, cache, application modules, proof of work. The further from the app you block, the cheaper the block.
  edges: caseof P13 | <- backs E-facet | <- backs E-bots

[C-mcpsolve] Solver published as MCP tools  (section: #innovation)
  akamai_solve and datadome_solve handed to the model, so the agent answers a 403 nobody pre-wired for it.
  edges: backs C-captcha | <- cuts E-envelope

[C-idem] Idempotency when the caller is a machine  (section: #post-extract)
  An agent times out, cannot tell whether the write landed, retries, and says nothing. Idempotency-Key with a constraint, or get_or_create on the natural key.
  edges: caseof P7

[C-scarcity] Blocking as a negotiating position  (section: #legal-ethics)
  Default crawler blocking plus per-article visibility turned access into something to license. Scarcity, not cybersecurity, is what brought AI platforms to the table.
  edges: <- backs E-227 | backs C-402 | cuts W-stealth | <- backs E-notdead | <- cuts C-courtesy

[C-whoblocked] Read the block page, not the header  (section: #detect)
  The CDN is often only the front door. One 403 header fronted DataDome once and PerimeterX another time, so the header names the door and not the decision.
  edges: caseof P2 | cuts V-cf | backs C-attr | <- backs E-dial

[C-apimap] Map the API reads before reversing the VM  (section: #tools)
  The cheapest layer of research answers which properties and methods a script reads and calls. Most write-ups skip to the bottom layer and make it look like magic.
  edges: backs E-sg | caseof P10

[C-trends] Per-endpoint header rules, not per-site  (section: #cases)
  On Google Trends a referer is required by the timeline endpoint and fatal on the regional ones, so stripping it is what gets you through.
  edges: caseof P13 | at L1 | caseof C-endpoint

[C-accepted] Price on accepted records, not requests  (section: #cost)
  The managed-versus-custom line is who owns the gap between a fetched page and a business record you would accept. That denominator moves most vendor comparisons.
  edges: backs C-cost | caseof P7 | <- backs E-blend | <- backs E-pareto | <- backs E-split

[C-benchread] Read a success rate against four choices  (section: #tools)
  Who defined success, who picked the targets, at what request rate, and whether the ranking survives per site rather than blended.
  edges: caseof P10 | <- backs E-blend | <- backs E-openbench | <- backs E-proxyprice | <- backs E-nofail | <- backs E-split

[C-500pass] A 500 is a cleaner pass signal than a 200  (section: #tools)
  A backend error means your traffic reached the application. Nobody serves an accidental stack trace to trick you.
  edges: caseof P10 | <- backs E-obscura

[C-susval] Adjudicate suspicious values with a model  (section: #post-extract)
  Schema and completeness first, then a model judges only what survives. A financing payment read as a price passes every structural test.
  edges: caseof P7 | cuts P17 | backs C-dq | catch C-fontpoison

[C-fontpoison] Poisoned fonts hand you different words  (section: #antibots)
  GSUB substitution applied to whole words. A quarter swapped for same-class synonyms, the font drawing the originals back for the human.
  edges: caseof P7 | <- backs E-shield | cuts P18 | <- catch C-renderdiff | <- catch C-susval | <- backs E-twoweb | <- backs E-finewb

[C-renderdiff] Spot-render a sample and diff the words  (section: #tools)
  The cheap check against text poisoning. Fetch over HTTP, render a few of the same pages, compare what the words actually say.
  edges: catch C-fontpoison | caseof P10

[C-stalefetch] Route on staleness, fetch live when the index is old  (section: #ai)
  An index has an age. Treating freshness as a runtime decision rather than a cron job is what keeps a RAG answer current.
  edges: caseof P15 | backs E-rag

[C-budget] Assert a budget against the real entry point  (section: #tools)
  A benchmark prints a number; a regression test refuses to let it change. Fixed count, real endpoint, two data sizes.
  edges: caseof P10 | <- backs E-nofail | backs C-dq

[C-untrusted] Treat every collected file as hostile input  (section: #arch)
  A dataset config reached code execution. Isolation, file-access limits, input validation and agent permissions, because disclosure beside a tool-using agent becomes exfiltration.
  edges: caseof P12 | caseof P4

[C-courtesy] Conditional, incremental, deduplicated  (section: #arch)
  304s instead of bodies, a stored cursor instead of full sweeps. Blocked for how you behaved is harder to fix than blocked for who you were.
  edges: cuts E-bots | backs C-throttle | cuts C-scarcity

[C-trail] Provenance for the session, not just the row  (section: #post-extract)
  Source, raw capture, timestamp, model and prompt version, and the step sequence. Without it the answer can only be trusted or discarded, never checked.
  edges: caseof P9 | backs C-attr | backs P11

[C-system] A system, not a script  (section: #arch)
  Proxy management, concurrency, a decision about JavaScript and a fallback, and cost tracked across all of it. Orchestration and instrumentation, not a file.
  edges: cuts P18 | backs E-sowa | backs C-cost

[C-skillmem] Keep the recipe, not the transcript  (section: #ai)
  Distil a successful run into a reusable note with success and failure counts, and demote it when it stops working. A smaller model can then follow it.
  edges: caseof P17 | backs P11 | backs C-heal

[C-identlimit] Ask whether the limiter counts speed or counts you  (section: #detect)
  If a slower cadence returns the same 429, backing off is not the answer and you are really facing an identity decision.
  edges: cuts E-dial | caseof P2 | <- backs E-fashion

[C-warm] Warm one session, then hit the API directly  (section: #flow)
  Solve the anti-bot once, post the trust tags, mint the token, reuse the session for dozens of JSON calls. The sensor cost amortises across every product.
  edges: <- backs E-hermes | backs C-endpoint | caseof P17


== VENDORS ==

[V-ak] Akamai Bot Manager  (section: #antibots)
  Scores across five layers, three of them at the handshake. The _abck cookie sits at bot until sensor.js validates the session.
  edges: at L1 | at L2 | at L3 | at L6 | <- catch E-ak3 | <- at E-abck1

[V-cf] Cloudflare  (section: #antibots)
  Roughly a quarter of the web. Also ships Kitesurf and WebMCP, so it sells both the wall and the ladder.
  edges: at L1 | at L5 | at L6 | <- catch E-15eng | <- catch E-turn | <- cuts C-whoblocked

[V-dd] DataDome  (section: #antibots)
  Fingerprint plus behavioural ML. Published the WASM SIMD CPU fingerprinting work.
  edges: at L3 | at L4 | at L6 | <- catch E-simd

[V-ka] Kasada  (section: #antibots)
  Real-browser-only by design, x-kpsdk headers, challenges that change constantly.
  edges: at L3 | at L6 | <- catch E-kasada

[V-px] PerimeterX / HUMAN  (section: #antibots)
  Frequently rebranded and served first-party, so hostname checks will not identify it.
  edges: at L3

[V-f5] F5 Shape  (section: #antibots)
  Heavy obfuscation and behavioural biometrics, usually found inside the composed stacks.
  edges: at L3 | at L6

[V-fastly] Fastly Bot Management  (section: #antibots)
  CDN-native, layered on the former Signal Sciences WAF. Standard JA3/JA4 and header-order stack.
  edges: at L1 | at L2


== TOOLS ==

[T-curl] curl_cffi  (section: #libs)
  Impersonates browser TLS and HTTP/2 from Python. Runs no JavaScript, so JS challenges are where it stops. Cheapest thing to try first.
  edges: beats L1 | beats L2 | cuts L3 | caseof C-flow

[T-utls] uTLS / Go sidecar  (section: #libs)
  Reimplements a real browser TLS stack byte for byte. The answer when the refusal happened at the handshake.
  edges: beats L1 | beats L2 | <- backs E-ak3

[T-primp] primp / impit  (section: #libs)
  Rust-cored HTTP clients with impersonation, for when volume makes Python the bottleneck.
  edges: beats L1 | backs C-cost

[T-cam] Camoufox  (section: #libs)
  Firefox with C++ level anti-detect patches. Has a real GPU context, so it passes WebGL checks Chromium forks fail.
  edges: beats L3 | cuts L4

[T-nod] nodriver / Patchright  (section: #libs)
  CDP automation without the webdriver markers. Patches the JS surface, not the network layer.
  edges: beats L3 | cuts L1

[T-cloak] CloakBrowser  (section: #libs)
  Loads real extension profiles, so extension-enumeration probes return plausible answers.
  edges: beats L3

[T-scrapling] Scrapling  (section: #libs)
  Adaptive selectors that re-find an element after the DOM shifts under them.
  edges: caseof P14

[T-fps] fingerprint-suite  (section: #libs)
  Generates one coherent fingerprint across headers and JS APIs in a Playwright context, rather than spoofing per axis.
  edges: beats L3 | caseof P1

[T-trawl] TRAWL  (section: #libs)
  The escalation ladder as a product: plain HTTP, cached session, fresh solve, then residential proxy.
  edges: caseof P18 | beats L5

[T-frida] Frida  (section: #mobile)
  Runtime instrumentation that unpins certificates and hooks the signing function while the app runs.
  edges: beats C-pin | beats C-sign

[T-mitm] mitmproxy / Burp  (section: #tools)
  The intercepting proxy. Two independent TLS connections, which is itself a fingerprint if you forget it.
  edges: caseof C-capture | cuts L1

[T-powhttp] powhttp  (section: #tools)
  Wire capture aimed at scraping workflows rather than security testing.
  edges: caseof C-capture

[T-fpscan] fingerprint-scan.com  (section: #tools)
  Splits the fingerprint hash into sub-hashes, so you can see which group of signals moved rather than only that something did.
  edges: cuts P16

[T-bench] StealthBench  (section: #tools)
  Reproducible stealth ranking against self-hosted detectors, with the harness published.
  edges: backs P16 | caseof P10

[T-kite] Cloudflare Kitesurf  (section: #agentic)
  Agent-first engine with no Chromium. Cheap per session, and explicitly not for TLS-fingerprint challenges.
  edges: cuts L1 | backs C-agent

[T-moli] Moli  (section: #agentic)
  Rust kernel with on-demand rendering. 73 MiB against headless Chrome’s 773 MiB at level success.
  edges: cuts L1 | backs C-agent

[T-bos] BrowserOS neo  (section: #agentic)
  Local agent browser using your own Chrome sessions, with session replay. Right for logged-in work, wrong for volume.
  edges: backs C-agent | beats L5

[T-bu] Browser Use  (section: #agentic)
  Open-source agent browsing toolkit. Its own team found strong models prefer writing CDP code to reading screenshots.
  edges: backs C-agent | backs P11

[T-a11y] Accessibility-tree selectors  (section: #agentic)
  Role plus accessible name. More durable than CSS because changing it breaks the page for screen readers.
  edges: caseof P14

[T-scrapy] Scrapy + scrapy-poet  (section: #arch)
  Page objects separate crawl logic from extraction, which is what makes machine-authored scrapers repairable.
  edges: caseof P14 | backs C-heal

[T-crawl4ai] Crawl4AI  (section: #ai)
  Built for legibility rather than access: strip the chrome and hand a model something citable.
  edges: caseof P15

[T-firecrawl] Firecrawl  (section: #ai)
  URL to clean markdown as a service, with an MCP server so a model can call it directly.
  edges: caseof C-llm


== EVIDENCES ==

[E-ak3] Akamai v3: every browser failed  (section: #cases)
  Seven stealth approaches all failed because the block happened before HTML was served. A Go TLS stack fixed it: 24 rpm, zero blocks in 500+ requests.
  edges: backs P2 | catch V-ak | backs T-utls | backs C-sweep

[E-15eng] Fifteen stealth engines, identical 403s  (section: #innovation)
  Three engines passed a free detector, then all hard-403’d on live Cloudflare at the same rate. The exit IPs were datacenter.
  edges: backs P3 | backs P16 | catch V-cf

[E-proxy] Weeks lost to the wrong variable  (section: #innovation)
  Three providers swapped over a TLS-versus-user-agent mismatch, while a non-browser-like health checker reported healthy proxies as dead.
  edges: backs P2 | backs P10 | backs P1

[E-429] The 429 fixed by deleting the cookie  (section: #innovation)
  Provider and pool swaps changed nothing. Removing the session cookie returned consistent 200s: the limit was per identity.
  edges: backs P4 | cuts C-session

[E-kasada] Two endpoints, one defended  (section: #innovation)
  /searches was protected. /map-listings returned the same records plus extra fields, undefended.
  edges: backs P13 | catch V-ka | backs C-endpoint

[E-shopee] Signing logic in a 4.7MB native library  (section: #cases)
  The signer exported only JNI_OnLoad, so static analysis stalled and the practical route was to let the app sign while watching.
  edges: backs C-sign | cuts P13

[E-reddit] 92% to 61% with zero code changes  (section: #arch)
  A public scraper degraded over 30 days because the target changed around it. The fix was architectural, not tactical.
  edges: backs C-heal | backs P8

[E-shader] ShaderGhost  (section: #innovation)
  A 32-bit ID written into the GPU shader cache, readable cross-site, surviving profile wipes, on around 99% of browsers.
  edges: backs P6 | <- backs E-bounty

[E-frost] Frost: SSD timing  (section: #innovation)
  Disk-subsystem latency infers activity in other tabs with no permissions, and incognito does not help.
  edges: backs P6

[E-simd] WASM SIMD CPU probes  (section: #detect)
  Vector instruction timing fingerprints the actual silicon, so JS-level patches cannot answer it.
  edges: backs P6 | catch V-dd

[E-math] Math.tanh reads the host libm  (section: #detect)
  Transcendental results differ by operating system, and CSS trig leaks the same signal everywhere on the page.
  edges: backs P6 | at L4

[E-bridge] Bridges to Self  (section: #innovation)
  A native app listening on localhost bypasses per-origin isolation, with neither platform’s model broken alone. USENIX Security 2026.
  edges: backs P5 | cuts C-mobile

[E-time] A publisher serving bots a different site  (section: #innovation)
  The same URL returns 303 KB of HTML to a browser and 13 KB of markdown to assistant crawlers, carrying its own advertising.
  edges: backs P7 | at L7

[E-bh] Black Hat: every agentic browser fell  (section: #agentic)
  Comet, ChatGPT Atlas and Opera all lost to indirect prompt injection. No payload to detect.
  edges: backs P12 | backs E-canvas | <- backs E-inject5

[E-canvas] HTML-in-Canvas injection  (section: #agentic)
  Instructions rendered into a canvas never exist as DOM text, so text sanitisation is structurally blind to them.
  edges: backs P12 | <- backs E-bh | backs C-inject | <- backs E-inject5

[E-tokens] Tool surface, not model, sets agent cost  (section: #agentic)
  Five independent measurements. A ten-step flow costs ~7,000 tokens or ~114,000 depending only on interface shape.
  edges: backs P11

[E-zyte] Ten failure modes in agent-built scrapers  (section: #ai)
  Recon kept only HTML, tests were written by the model that wrote the extractor, coverage flagged only zero, rows carried no provenance.
  edges: backs P9 | backs P8 | backs P10 | cuts C-heal

[E-http3] HTTP/3 is unreachable through a proxy  (section: #innovation)
  Configuring a proxy forces HTTP/2. Likely-bot share is 73.75% on HTTP/1.1, 26.76% on HTTP/2 and 3.33% on HTTP/3.
  edges: backs P3 | at L2

[E-turn] The 100% precise check was a Chrome bug  (section: #innovation)
  CDP clicks reported iframe-relative coordinates, real clicks main-frame-relative. Patched, and the check died with it.
  edges: backs P5 | catch V-cf

[E-sowa] The web is priced, not blocked  (section: #innovation)
  24,898 sites. 18.5% have no barrier, 30.7% have one, 88% stay in the two easiest tiers. The work is the hard 20%, and fashion needs real infrastructure on 57% of it.
  edges: cuts P2 | backs C-ladder | <- backs C-system | <- backs E-pareto | <- backs E-fashion | <- backs E-split

[E-rand] Randomisation is unproven  (section: #tools)
  Watching a hash change proves nothing if the tracker identifies you on whatever stayed stable.
  edges: backs P16 | backs P1

[E-bounty] Privacy bugs priced a fifth of memory bugs  (section: #innovation)
  Around $1,000 for a cross-site supercookie against roughly $5,000 for a sandbox escape, so the finder is paid to keep it.
  edges: backs E-shader | costs P6

[E-rag] RAG is bounded by acquisition  (section: #ai)
  A vector store indexed six months ago is an LLM with a stale cutoff. Boilerplate competes for similarity with the real content.
  edges: backs P15 | backs P9 | <- backs C-stalefetch

[E-precursor] Cloudflare Precursor scores the session  (section: #detect)
  Behavioural checks moved from bursts at a challenge to continuous scoring, so a refresh no longer resets you.
  edges: at L6 | backs C-session

[E-refer] Crawl-to-refer ratios  (section: #innovation)
  Some AI crawlers take thousands of pages per visitor returned, which is the economic argument behind publisher blocking.
  edges: at L7 | backs C-402 | <- backs E-227

[E-bots] Automated traffic is the majority  (section: #innovation)
  Past half of all web traffic, and a growing share is an agent acting for a real person who is genuinely the audience.
  edges: at L7 | backs P12 | backs C-402 | backs C-defladder | <- backs E-227 | <- backs E-twoweb | <- cuts C-courtesy | <- backs E-ddr5

[E-cost] 19.25 MB per page versus a direct call  (section: #cost)
  One measured comparison of a full browser load against calling the API the page itself calls.
  edges: backs P17 | backs C-cost

[E-liveness] Liveness CAPTCHA  (section: #antibots)
  Hand-gesture challenges arrive because behavioural scoring hit a ceiling, and they are answerable with a virtual camera.
  edges: backs C-captcha

[E-agentread] 268,000 requests: what agents really read  (section: #innovation)
  Software beat humans 2:1. ChatGPT-User took markdown 0.1% of the time, Claude Code 76%. Segment by client or the average describes nobody.
  edges: backs P15 | <- backs E-llmstxt | <- backs E-ready | <- backs E-nojs

[E-llmstxt] llms.txt drew 37 named-assistant fetches  (section: #innovation)
  About 660 direct fetches, but almost all from search crawlers. A hidden markdown link recorded zero hits across 268,000 requests.
  edges: cuts C-doc | backs E-agentread

[E-ready] Agent-readiness adoption is near zero  (section: #innovation)
  robots.txt on 78% of sites, declared AI preferences on 4%, markdown negotiation on 3.9%, MCP Server Cards on fewer than fifteen sites.
  edges: backs E-agentread | cuts C-doc

[E-facet] Faceted search is the pressure point  (section: #innovation)
  Every filter, sort and page state is its own URL, so walking combinations forces uncached application work at volume.
  edges: backs C-defladder | caseof P13

[E-envelope] Right payload, wrong envelope  (section: #innovation)
  Solving server-side submits the container TLS fingerprint, so a challenge verifying its own submitter rejects a correct answer.
  edges: cuts C-mcpsolve | at L1 | backs P19

[E-227] 227 bot visits per human visit  (section: #innovation)
  One publisher quarter. Crawling rose 18% year on year overall, GPTBot 305% and ChatGPT-User 2,825%, moving GPTBot from ninth crawler to third.
  edges: backs E-refer | backs E-bots | backs C-scarcity | <- backs E-notdead

[E-sg] Google Search Guard ships its own VM  (section: #detect)
  First-visit signals with no cookies are assembled inside a virtual machine and settle into SG_SS. Map which APIs the script touches before attacking the VM.
  edges: at L3 | backs C-obf | <- backs C-apimap

[E-proxyprice] Price and performance barely correlate  (section: #proxies)
  0.25 correlation on Amazon. Among providers under the same latency bar, $19 against $300 for the same threshold, and the dearest was not the fastest.
  edges: backs C-benchread | at L5 | cuts C-source

[E-blend] A blended score describes no real target  (section: #tools)
  Shein averaged 21.88% and G2 36.63% across eleven APIs. One API fell 84.47% to 72.98% on request rate alone.
  edges: backs C-benchread | backs C-accepted

[E-openbench] Reproducible is not disinterested  (section: #tools)
  Harness, targets, pass criteria and adapters published so anyone can re-run it, and still authored by the vendor it ranks first.
  edges: backs C-benchread

[E-notdead] The obituary is four years old  (section: #innovation)
  Search interest peaked in 2026. Models did not replace collection, they became its largest client, on both training and inference.
  edges: backs E-227 | backs C-scarcity

[E-obscura] The wall is the IP, not the fingerprint  (section: #proxies)
  An engine scoring 94/100 still needed reloads from clean residential addresses. Reputation decided access, not fingerprint quality.
  edges: backs P3 | at L5 | backs C-500pass | <- cuts E-abck1

[E-abck1] An unfinished sensor leaves _abck unsolved  (section: #antibots)
  A minimal engine stalls on heavy scripts, marking the second segment with -1 and returning 403 where a real browser does not wobble.
  edges: at V-ak | cuts E-obscura

[E-nojs] The largest assistants do not render JavaScript  (section: #ai)
  ChatGPT, Claude and Gemini fetch HTML and stop, while DeepSeek and Mistral render. Client-side content is absent to the first group.
  edges: backs E-agentread | backs P15

[E-shield] 25% of words swapped broke 55.8% of passages  (section: #antibots)
  Grammatical substitutes rather than garbage, so nothing downstream trips. Meaning failure in over half of news text tested.
  edges: backs C-fontpoison | <- backs E-finewb

[E-nofail] A benchmark that could not fail  (section: #tools)
  The optimisation it existed to prove was deleted and everything still passed, because the test built its own query instead of driving the app.
  edges: backs C-budget | backs C-benchread

[E-twoweb] Bots are the majority audience for HTML  (section: #innovation)
  Roughly 57 to 58% of HTML requests by one tracker and about 53% by another, and the version served to machines is the clean one.
  edges: backs E-bots | backs C-fontpoison

[E-finewb] Over 90% of shielded pages are filtered out  (section: #antibots)
  Against a training-set quality filter, most poisoned text never enters the corpus. The survivors carry false meaning in about 19.4% of content.
  edges: backs C-fontpoison | backs E-shield

[E-dial] Rate limiting is a dial, not a switch  (section: #detect)
  429 after eleven requests at one every 1.2 seconds, a fifteen-minute lockout, and polling returns the original countdown rather than extending it.
  edges: backs C-throttle | caseof P2 | backs C-whoblocked | <- cuts C-identlimit

[E-pareto] Eighty percent is easy and nobody pays for it  (section: #innovation)
  Difficulty is concentrated, not spread. The paid work lives in the closing fifth, and which fifth depends on the sector rather than the technology.
  edges: backs E-sowa | backs C-accepted | <- backs E-fashion

[E-fashion] Fashion is the hardest sector to read  (section: #innovation)
  2.86 of five against a 1.58 average. Rate limiting on 54%, TLS fingerprinting on 32%, and CAPTCHA on only 15% because it costs sales.
  edges: backs E-sowa | backs E-pareto | caseof P13 | backs C-identlimit

[E-ddr5] 551 polls per SKU, one every 6.5 seconds  (section: #antibots)
  91% bot traffic on DDR5 pages, cache-busting every request to force origin. A real-time inventory feed, not catalogue scraping.
  edges: cuts C-throttle | backs E-bots | caseof P7

[E-inject5] An injection is five layers, not one sentence  (section: #agentic)
  A fake boundary, a hidden image, a spoofed reply, a fake command, and padding. Each innocuous alone, together an instruction set.
  edges: backs P12 | backs E-bh | backs E-canvas

[E-hermes] 7,690 Hermes products, no browser, behind DataDome  (section: #cases)
  Three signals carried it: a Chrome TLS fingerprint, exact header order, and a DataDome token. 37 KB per product against a page a hundred times heavier.
  edges: caseof P2 | backs C-endpoint | backs C-warm | at L1 | at L2

[E-split] The new half of the market skipped the hard 20%  (section: #platforms)
  AI-native retrieval inherited the reachable web. Its search falls over on a hardened target, because access was the part everyone assumed was solved.
  edges: backs C-accepted | backs E-sowa | backs C-benchread

[E-glassbox] A raw-dump fingerprint mirror  (section: #tools)
  Glassbox shows every signal a tracker reads, client-side, with an identifiability estimate. Read your own surface rather than a pass or fail.
  edges: backs P10 | backs P16 | backs C-sweep


== LAWS ==

[W-hiq] hiQ v. LinkedIn  (section: #legal-ethics)
  Scraping public data is not unauthorised access under the CFAA. Ninth Circuit, reaffirmed 2022.
  edges: at L7 | <- backs W-vb | <- backs W-bd | <- backs W-ccdh | <- cuts W-ryan | <- cuts W-serp | <- backs C-robots | <- cuts W-serp2

[W-vb] Van Buren v. United States  (section: #legal-ethics)
  "Exceeds authorized access" reaches only areas off limits to you, not permitted areas used for a disfavoured purpose.
  edges: backs W-hiq

[W-bd] Meta and X Corp v. Bright Data  (section: #legal-ethics)
  Both failed. Contract and CFAA theories against public-data collection keep losing.
  edges: backs W-hiq

[W-ccdh] X Corp v. CCDH  (section: #legal-ethics)
  Public-interest research survived a hostile platform. Read with Sandvig v. Barr.
  edges: backs W-hiq

[W-ryan] Ryanair v. Booking.com  (section: #legal-ethics)
  The counterweight: a logged-in scrape against accepted terms is a different question from a public one.
  edges: cuts W-hiq | cuts C-login

[W-serp] Google v. SerpApi  (section: #legal-ethics)
  Live. Argues anti-bot is a technological protection measure, so bypassing it is DMCA circumvention rather than unauthorised access.
  edges: cuts W-hiq | at L7 | <- backs W-broker | <- backs W-serp2

[W-stealth] Stealth-crawler legislation  (section: #legal-ethics)
  H.R. 9915, the Stealth Bot Prohibition Act, plus a New York statute: prohibit bots that mask identity, moving disclosure from engineering to compliance.
  edges: cuts P12 | at L7 | backs C-webbot | <- cuts C-scarcity

[W-broker] Data-broker non-compliance  (section: #legal-ethics)
  32 registered brokers sell to generative AI developers and nobody knows which. The licensed market is less inspectable than open collection.
  edges: backs W-serp

[W-serp2] The narrow refiling against SerpApi  (section: #legal-ethics)
  Broad claim dismissed, copyright theory refiled. To win, a results page must be argued to contain protected work.
  edges: backs W-serp | at L7 | cuts W-hiq

[W-netnut] NetNut seized: a proxy vendor as a botnet  (section: #legal-ethics)
  A NASDAQ-listed residential provider whose pool was sourced from malware on two million devices. The stock fell 96% from peak.
  edges: backs P3 | cuts C-source | <- backs P20


== END KNOWLEDGE GRAPH ==

---

---

## THE SCRAPING DECISION FLOW

01 Attack strategy
The scrapingdecision flow
Walk steps in order. Stop at the first win. Complexity and cost increase right. Most production scraping is solved at steps 1–3.

 
 Thumb rule · before you walk the flow
 API or scrape? Five lines to score.
 The flow below assumes you have already decided to collect the page yourself. Often that is the wrong assumption, and the argument that follows is opinion against opinion. Below are the five lines worth scoring for both routes — the vendor's official API and scraping the site — and for each one, the metric that settles it and the reason that metric beats the obvious one. The lines are the same everywhere. The answer is different for every source, which is exactly why it is worth measuring rather than debating.
 

 
 Line 01Coverage
 Does the route carry the fields you actually need? An API returns what the vendor chose to expose; a page shows what they chose to publish, and those sets are rarely the same. Score it per source, not per programme.
 Measurefields returned ÷ fields needed, per sourceCount fields, not sources. A route that reaches every source and carries none of your fields scores 100% on the obvious metric and zero on the useful one. This is also the only line that can be zero, and a zero here makes the other four irrelevant.
 

 
 Line 02Lead time
 How long from deciding you want a source to holding a row you can use. The best single measure of whether the collection function is improving rather than merely growing, and the one most teams never track — it needs two timestamps nobody thought to write down.
 Measuredays from source approved → first usable rowTake the median over your last ten sources, not the first one. The first is always slow and tells you nothing about the machine. If the median stays flat while headcount rises, you are buying coverage rather than building it.
 

 
 Line 03Cost
 What a thousand records you can actually act on cost, all-in. “Usable” carries the sentence — a record nobody trusts cost more than nothing, because somebody acted on it.
 Measurefully loaded spend ÷ 1,000 usable recordsCost per GB and cost per call both hide the failures: you pay for the blocked request, the retry, and the row you later discarded. Include engineer hours — the largest line usually appears as headcount, so it escapes the comparison entirely. Worked numbers in §13.
 

 
 Line 04Reliability
 Not uptime — delivery. Two numbers: how many sources sit inside their refresh window, and how many runs reported success while delivering nothing.
 Measure% inside refresh SLA + silent-failure rateScore coverage as a delta against the last good run, not as a level. A source falling from 40,000 rows to 4,000 with every field populated passes every not-zero check ever written. The failure that costs you is the one your dashboard calls green.
 

 
 Line 05Leverage
 How much of the estate rides on something shared, and how much of it repairs itself. The leading indicator for the other four: this is the line that tells you what the next twelve months cost.
 Measure% of sources on a shared template · % of breaks auto-repairedThe question underneath it: does source two hundred cost less to add than source twenty did? If yes, you have leverage. If no, you are adding sources linearly, and that stops scaling before the roadmap does.
 

 
 How to actually use it
 Run both routes down all five, per source, on your own numbers — not a vendor benchmark and not a blog's. Start with coverage, because it is the only line that can return zero and a zero there makes the rest academic. Then read the five together rather than totting up a winner: the first four tell you how last month went, and only the fifth tells you what next year costs.
 In production the answer is often both: take the API for the fields it has and scrape the delta. That hands you something you cannot buy — where the two carry the same field and disagree, your parser is wrong until proven otherwise. The API stops being a data source and becomes a correctness oracle.
 One thing these five lines deliberately do not score: whether you are allowed to. Terms of service, rate limits, personal data and the relevant case law sit above the scorecard, not inside it — and a route that scores well on all five is still the wrong route if it is one you should not take. Start at Read this first.
 

Recon first (Step 0): Before picking a step from the flow, capture a real session through Burp Suite and run PortSwigger's MCP server with Claude Code. One prompt traces the entire cookie lifecycle (_abck, cf_clearance, datadome, reese84), identifies sensor payload endpoints, and tells you which step from the flow below will actually work for this target. What used to be a 4-hour manual walk through HTTP history is now a 2-minute prompt.

 Asad's Priority Order, start left, move right only when needed
 
 
 Step 1
 📱Mobile APIHTTPToolkitFrida · mitmproxy
 
 
 Step 2
 🔍XHR EndpointChrome DevToolsBurp + MCP · webclaw
 
 
 Step 3
 🗃️JSON in HTML__NEXT_DATA__chompjs · Parsel
 
 
 Step 4
 ⚡HTTP Scrapingcurl_cffiScrapy · Scrapling
 
 
 Step 5
 🌐C++ BrowserCamoufoxCloakBrowser
 
 
 Step 6
 ☁️Managed APIBright DataZyte · Firecrawl
 
 

Rule #1, Asad's priority: Never start at Step 5. The mobile app often hits the same backend with zero anti-bot. Confirmed on a major retailer, a direct GraphQL endpoint bypassed all HTML anti-bot protection entirely. Find the API first, always.

Before we go deeper: The flow above tells you what order to try things. But to understand why those steps exist in that order, and what happens when you skip one, you need to understand how detection actually works. The next section breaks down every signal anti-bots collect, starting at the TCP handshake.

First, place the target on the difficulty ladder#

Before any of that, do the cheapest thing that saves the most pain: work out which rung the target sits on, because most teams plan for the easy rung and discover the hard one in week two, after a deadline is already promised. Difficulty is not a spectrum so much as five discrete tiers, each with a giveaway tell and a rough effort, and knowing the tier up front tells you which tools you will actually need and how long it will honestly take.

 
 
 Tier 1 · Open
 The data is just sitting there
 What it is: a public API, or clean data right in the HTML. Tell: you can see the data in "view source" or hit a public endpoint directly. Effort: an afternoon. This is the rung you hope for and should always check for first, before assuming a fight.
 
 
 
 Tier 2 · Light defense
 Works fine, until you speed up
 What it is: rate limits, User-Agent checks, basic bot filtering. Tell: it works, then starts throwing 429s or blocks once you scale. Effort: real headers and rate control, a day or two. The fix is to slow down and look ordinary, not to reach for heavy machinery.
 
 
 
 Tier 3 · Dynamic
 Empty in source, full in the browser
 What it is: the data loads through JavaScript, behind XHR or GraphQL. Tell: "view source" looks empty but the page is full when it renders. Effort: drive a real browser, or (better) find the API behind the page and call it directly, which is usually faster and more stable than rendering.
 
 
 
 Tier 4 · Fingerprinting
 Flagged before anyone reads your request
 What it is: TLS/JA3 and browser-fingerprint checks. Tell: your code is blocked instantly, but the site loads fine in your own browser. Effort: engine-level stealth (impersonation or a rebuilt browser). Rotating proxies alone will not do it, because the block is about who you look like, not where you are coming from.
 
 
 
 Tier 5 · Behavioural
 It watches how you move
 What it is: Akamai, DataDome, Cloudflare at full strength, scoring mouse, timing, and the path you take. Tell: you pass once, then get flagged the moment you scale or move predictably. Effort: where most teams stall. Looking human over the whole session beats being fast, and this is the rung the rest of this guide spends most of its time on.
 

The single most useful habit this ladder gives you is honest scoping: decide the rung before you start, not after. A target that turns out to be Tier 4 or 5 needs a fundamentally different toolchain and timeline than a Tier 1 job, and the classic failure is quoting an afternoon for what is really a month because nobody checked which wall was there. The tiers are also cumulative, a Tier 5 site is doing everything the lower tiers do as well, so you climb the ladder in order rather than skipping to the top.

Sweep the impersonation profile before you escalate the tool#
One of the cheapest wins available, and almost nobody does it. The reflex when a request-based attempt fails is to escalate: curl_cffi did not work, so reach for a browser. That reflex skips a step that costs minutes and often removes the need for the browser entirely.A 2026 write-up of a production escalation put numbers on it. The team held request, headers, cookies, rate and IP conditions constant and changed only the impersonation profile. Against one Cloudflare deployment, the generic Chrome profile returned almost no usable responses while Firefox reached roughly 80%. Behind DataDome on a server-rendered route, chrome120 returned 0 usable responses out of 6, and firefox144 returned usable content in about 7 attempts out of 10. Same library, same everything else, different profile.Read those as a dated snapshot of two specific deployments rather than a rule about Firefox, because the useful lesson is procedural. A protection provider rejects a connection pattern, not a library, and the pattern that fails today is not the only pattern the library can produce. Before you accept the cost of a browser, sweep the profiles you already have and change one variable at a time. The version of this that saves the most time is written down: keep the profile that worked in your target notes, because the next engineer on that domain will otherwise repeat the whole sweep.

How much of the web is actually gated, measured rather than guessed. The largest published audit of access controls, covering 24,898 of the most-visited sites across 230 countries and 110 industries, found that only 18.5% carry no technical barrier at all and that half run two or more layered together. WAFs appear on more than nine sites in ten, and most of those were never chosen deliberately: they arrive switched on with the CDN. Yet the same audit found 88% of landing pages still sitting in the two lowest difficulty tiers. Both numbers are true, and holding them together is the whole discipline of the ladder above. The presence of a barrier tells you almost nothing about the effort required. Only probing the endpoint you actually need does that, and the landing page is frequently not that endpoint.

 8 field notes on strategy
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / API reversing
 Why Facebook's JSON Responses Start With an Infinite Loop
 Inspect Facebook's XHR responses and the body does not begin with JSON. It begins with for (;;); — an infinite loop. It is not obfuscation and it is not anti-scraping. It is a defence against JSON hijacking, a flavour of cross-site script inclusion, and knowing why it is there tells you exactly how to handle it.
 💡 The client strips nine bytes and parses; loaded as a script, it hangs before reaching the data
 The attack it kills. The same-origin policy stops an attacker's page from reading a cross-origin XHR response, but nothing stops that page from doing <script src="…/api/…">. The browser fetches it with the victim's cookies and executes the body as JavaScript. Around 2006 to 2009, if the body was a bare JSON array, Firefox let you override the Array constructor or define setters on Object.prototype and read every value as the literal was constructed. That is how Jeremiah Grossman extracted Gmail contact lists in 2006.Why the prefix works. Loaded as a script, the response loops forever before it ever reaches the data. Fetched by the real application, the client strips the first nine bytes and calls JSON.parse.The practical note for anyone consuming these endpoints. Strip the prefix, do not fight it. And note that a top-level object like {"payload": …} was never exploitable, because it is a syntax error when loaded as a script — the prefix is applied everywhere because blanket application is cheaper than auditing which endpoints return arrays. Other codebases use )]}', for the same reason. An unfamiliar prefix on a JSON body is usually a security artefact, not an attempt to stop you.
 
 +
 Aug 2026 / Technique
 Ask For Markdown and a Lot of Sites Will Just Give It To You
 This guide covers publishers serving markdown to named crawler user agents. It has not covered the technique from the collector's side: send Accept: text/markdown instead of Accept: text/html and a growing set of origins return a stripped document with no navigation, no scripts and no styling. Cloudflare shipped this zone-wide as an opt-in feature in February 2026, converting at the edge rather than at origin, free on paid plans.
 💡 The response carries a token-count header, so you can plan chunking before you parse
 The numbers. Cloudflare reports token reduction of up to 80% against the HTML equivalent, and every converted response carries an x-markdown-tokens header estimating the token count of the markdown body. For a RAG ingestion pipeline that is a budgeting primitive arriving free in a header you were already reading.It is a one-request probe. Send the header, look for markdown in the response and the token header alongside it. If it is there, you have skipped HTML parsing, boilerplate stripping and script execution in a single move — on a site that decided to hand it to you. Vercel documents the same pattern for Next.js, and several documentation platforms serve it while deliberately keeping the variant out of search indexes.The caveat that keeps it honest. A February 2026 survey of coding agents found only about three of seven negotiate for markdown at all; the rest still ask for HTML. Support is real but partial on both sides, so treat it as a fast path to try first, never as something to depend on.
 
 +
 Aug 2026 / Framing
 Access Was the 2008 Problem. Legibility Is the 2026 One.
 Scrapy has been solving the same problem since 2008 and still does it well: get past the wall, get past the JavaScript, get the page. It did not lose users. Crawl4AI reached 75,000 GitHub stars in three years, growing far faster than Scrapy ever did, and not because it scrapes better. Because a different problem appeared next to the old one.
 💡 A raw HTML dump full of nav bars is not a failure to access. It is a failure to read.
 The split is clean once stated. Access asks whether you can reach the page at all, and for most of the web that was largely settled a decade ago. Legibility asks whether a model can use what you collected without drowning in it, and that question barely existed when Scrapy was designed because the consumer was a database, not a context window. Crawl4AI's entire pitch is that second problem: strip the chrome, convert to something citable, hand the model text it can actually use. The two tools are not competing, they answer different questions, which is why both keep growing.Read it alongside the RAG acquisition-layer argument above and the tool-surface economics in the agentic section, and it is the same claim arriving from three directions: the expensive failure in 2026 is rarely the fetch. It is boilerplate competing for similarity with your actual content, payload you pay to re-read, and corpora nobody can audit.One extension worth thinking about, from Nimble's founder. Systems are starting to accumulate knowledge about what they found. The more interesting question is whether they can accumulate knowledge about how to find it — better source selection, better retrieval paths, a sharper sense of where the relevant information for a given domain tends to live. Those are two different kinds of memory and both compound. It is the same instinct as keying scraper memory to the platform rather than the URL: the durable asset is not the record you extracted, it is the route you learned to it.
 
 +
 Aug 2026 / Recon
 The Protection Is On the Endpoint, Not On the Site
 A Kasada-protected property site, approached the usual way: inspect the API, lift cookies from a browser session, replay. It failed on signatures, tokens and dynamic validation, as Kasada tends to. So instead of fighting the protection layer, they went reading the JavaScript bundles to see how the frontend itself fetched data.
 💡 Two endpoints, near-identical data, only one of them defended
 The site exposed /searches, the primary listing endpoint, and /map-listings, which returned largely the same property data plus some additional fields. Only /searches carried the protection. The whole architecture collapsed from browser, then cookies, then tokens, then signatures, then protected API down to direct API, structured JSON, database. No browser automation, no cookie management, no session persistence, no token extraction, and it ran continuously afterwards.Why this generalises further than it looks. Anti-bot is usually deployed as a rule on a route, not a property of a domain, and the rules are written by people under deadline. Map views, autocomplete, mobile endpoints, embed widgets, sitemap-adjacent JSON, RSS and print views all commonly serve the same underlying records through paths nobody thought to cover. The map endpoint in particular tends to be richer, because a map needs coordinates and metadata a list view does not.So make endpoint enumeration a step, not an afterthought. Before you accept that a target needs a full browser stack, read the bundles and list every route the frontend can call, then test each one cold with a plain HTTP client. This costs an hour and regularly turns a Kasada or Akamai problem into a JSON problem. It is also the cheapest thing in the escalation playbook above, which is exactly why it should come before you reach for a browser.
 
 +
 Aug 2026 / Method
 The Endpoint Only Fires If You Make the Page Ask For It
 A worked walkthrough of finding a retailer's internal API, and the reason most people conclude there isn't one. On a server-rendered listing, page one is already baked into the HTML, so the JSON call never happens on a plain load. It only fires when the frontend needs data it does not already have.
 💡 Open DevTools, then paginate, filter, sort or scroll. The API appears when the page runs out of data.
 Four details worth stealing, all of which cost an afternoon to rediscover.1. Warm up before you deep-link. Navigating straight to page two of a protected listing gets you blocked, and the symptom is misleading: an HTTP/2 protocol error, which reads like a network fault and is actually the anti-bot refusing you at the connection. The fix is to load the homepage first so the session collects _abck and bm_sz, then navigate to the target within the same session. Same lesson as the session-continuity material above: the cookie is not decoration, it is the thing that makes the next request legible.2. Listen, do not scrape. The capture is a page.on("response") handler that filters on content type (application/json and the +json variants), drops the analytics noise by host (Google, Facebook, OneTrust), deduplicates by HTTP method plus base path so you get one row per endpoint rather than one per request, and writes the bodies to disk for reading afterwards. Twenty lines, and it is the difference between finding the endpoint and guessing at selectors.3. When a click does nothing, click in the DOM. Client-side pagination often ignores a normal automation click. page.eval_on_selector(sel, "el => el.click()") dispatches it from inside the page and gets the handler to fire.4. The credential you were about to go hunting for may not exist. In the worked example the internal API turned out to be a proxied Algolia endpoint served from the retailer's own domain, with the credentials injected server-side. There was no key to extract, because the site was authenticating on your behalf. Always check whether the endpoint is first-party plumbing in front of a third-party search service before you start reverse-engineering a token.Read this against the agent-built-scrapers section above. The single largest finding there was a toolchain that launched a real browser and registered no response listener at all, keeping only page.content(). This is the twenty lines it was missing, and the reason that omission decides the whole strategy. The Lab #112, The Web Scraping Club
 
 +
 API-First Scraping
 Skip the HTML. Hit the API. 50× Faster.
 Open DevTools Network → Fetch/XHR before writing any code. Half the time the page calls a JSON API directly. 50× faster, 100× less memory, zero browsers launched.
 💡 Rule: open DevTools Network tab before writing any code
 The technique: open Chrome DevTools → Network tab → filter by Fetch/XHR. Reload the page. Look for requests returning JSON. Right-click → Copy → Copy as cURL. Run that cURL command. If you get the same data back, you have found the internal API. What to look for: GraphQL endpoints (POST to /graphql or /api/graphql), REST endpoints (GET to /api/v2/products, etc.), __NEXT_DATA__ (Next.js embeds full page state in a JSON script tag, no request needed, just parse the HTML). Benefits: bypasses most anti-bot because APIs typically have weaker protection than HTML endpoints, returns clean structured data instead of HTML you need to parse, no browser needed, runs at full HTTP speed. When this fails: auth cookies required, the API uses rotating tokens, or the site detects API scraping specifically.What to watch next: the new QUERY method (RFC 10008, June 2026). One reason so many JSON APIs are POST endpoints is that the read carries a filter object too large for a URL, so developers drop it in a POST body. GraphQL does this for every query. The cost is that a POST is opaque to caches and intermediaries: it cannot be cached or safely retried, and nothing in the method tells you it was only a read. HTTP now has a verb for exactly this, QUERY, which carries a body like POST but is declared safe and idempotent like GET, with a matching Accept-Query response header advertising which query formats a resource speaks. It is a Proposed Standard, not yet widespread (framework support is landing, e.g. an open Spring PR), but two things matter for a scraper. First, as targets adopt it, a QUERY endpoint is an even cleaner signal than a POST that you have found a read-only data API. Second, because QUERY responses are explicitly cacheable, a polite scraper that respects cache headers can cut fetches the way it never could against POST. Watch the Allow header for QUERY alongside GET and HEAD.When the internal API answers in an obfuscated shape: ProtoJSON. Finding the endpoint is sometimes the easy half. Large sites (Google properties are the textbook case) do not return clean labelled JSON, they serialise Protocol Buffers as deeply nested, positional JSON arrays with no keys, often prefixed with an anti-hijacking junk string such as )]}' that you must strip before parsing. The data is all there, but a field is addressed by its path through the array, not by a name, so a star rating might live at P[7][1][15] and an English translation at P[7][2][15][1][0]. Two things make this tractable. First, treat the leading junk prefix as a known quantity and slice it off before json.loads. Second, map the indices once and encode them as named constants, and this is a task an LLM is genuinely good at: hand it a sample response alongside the rendered page and ask it to align visible values to array paths, then freeze the mapping into a parser. The indices are effectively a private schema, so they can shift without notice, which means the index map belongs behind the same field-coverage monitoring and self-healing trigger as any brittle selector. It is the backend-API strategy carried to its conclusion: you traded HTML parsing for array-path parsing, which is faster and more stable, but it is still a contract the site can change.
 
 +
 Learning Path
 Scraping Tutorials Teach the Wrong Things First
 Most start with BeautifulSoup on static HTML. Real scraping is JS-rendered, sessions, rate limits, dynamic APIs. Better: DevTools → XHR replication → Scrapy → anti-bot.
 💡 DevTools → XHR replication → Scrapy → anti-bot, in that order
 The typical tutorial sequence: install Beautiful Soup, parse static HTML, extract data. This teaches the wrong mental model. Real production scraping involves: JavaScript renderingmost modern sites build their UI client-side, the HTML you fetch is an empty shell, Sessions and authcookies, CSRF tokens, login flows, Rate limiting and backoffexponential backoff, per-domain limits, Dynamic selectorssites change their HTML structure, you need adaptive extraction. The right learning sequence: DevTools Network tab → understand how data flows between client and server → learn to replicate XHR requests with requests/curl_cffi → Scrapy for structure and scale → fingerprinting and anti-bot bypass last. Understanding what actually happens when a browser loads a page is more valuable than memorising BeautifulSoup APIs.
 
 +
 Mental Model
 Understanding Beats Tools Every Time
 Not curl_cffi, not Playwright, not a $300/mo plan. Understanding how detection works is the real advantage. Tools change. Detection evolves. Understanding transfers.
 💡 "Tools change. Detection evolves. Understanding is what transfers."
 The mental model shift: most scrapers think in terms of tools ("which library bypasses Cloudflare?"). Experienced scrapers think in terms of signals ("which signals is my scraper leaking that Cloudflare can detect?"). The difference: tool-thinkers update their library when it breaks. Signal-thinkers understand why the library broke and can fix it themselves or identify the correct replacement. Signals Cloudflare checks: JA4 TLS fingerprint, HTTP/2 SETTINGS frames, navigator properties (webdriver, plugins, languages), Canvas hash, WebGL renderer, timing patterns, IP reputation. If you know which signal you are leaking, you can fix it regardless of which library you are using. This understanding also transfers to new anti-bots, the signals are similar across vendors even though the implementations differ.

---

## DETECTION LAYERS — How Anti-Bots Catch You

02 The anatomy of detection
Before you send a single byte,you've already been judged.
The moment your scraper opens a TCP connection to a CDN, a fingerprinting pipeline triggers. By the time your HTTP request body arrives, four independent scoring systems have already assigned you a trust score. Here's exactly what each one measures, and why defeating just one is never enough.

The fundamental insight: Anti-bots don't make binary decisions. They assign a continuous trust score across all four layers simultaneously. A perfect TLS fingerprint with a datacenter IP and machine-like mouse movement still fails, just at a different layer. The only winning strategy is addressing all four at once.

Concretely, here is the whole dossier a server holds before your JavaScript executes even one line. A modern browser is not one program but several processes cooperating, a coordinator process for tabs and navigation and cookies, a renderer for HTML and JS and the DOM, a GPU process for compositing, and a network process that actually speaks to the server, all wired together over inter-process communication. Only the renderer runs page JavaScript, and it runs late. By the time it does, the network process has already completed a handshake and a request, so the server has already collected, with zero cooperation from your code: your IP address and its reputation, your TLS fingerprint (JA3/JA4) from the ClientHello, your HTTP headers and their exact values, the header order and casing, the ALPN and cipher list, your Client Hints (sec-ch-*), and your request timing and rate. That is seven independent signals scored before a single line of your JS runs, which is why a flawless in-page fingerprint cannot save a client whose network-layer story does not match: if the headers announce Chrome 140 while the TLS handshake is Python's OpenSSL, the contradiction is decided before your first document.querySelector. The practical takeaway threads through this whole section, the JavaScript layer is the last thing a server learns about you, not the first, so a scraper that only manages its in-page fingerprint has already lost the connection it never knew it was being judged on.

This layered picture is not just the folklore of this guide, it is where the peer-reviewed research lands too. A 2025 integrative literature review of a dozen years of survey-bot studies (Karumathil and Tripathi, in AIS Transactions on Human-Computer Interaction) reached two conclusions worth carrying over to any bot problem, not just fraudulent survey respondents. First, there is no silver bullet: detection is inherently cyclical, defenders ship a check, bot authors adapt, defenders ship the next check, and no single technique stays decisive. Second, the only durable answer is multimodal quality control, layering independent protections and then checking them for consistency against each other, which is the academic phrasing of the same coherence principle the detection stack above is built on. The same logic generalises straight to the abuse a scraper impersonates or triggers, new-account fraud, account takeover, credential stuffing: any one signal can be spoofed in isolation, so the defensive value lives in the cross-checks between them, and correspondingly the offensive difficulty lives in keeping every layer telling one story at once.

Layer 1, TLS Fingerprinting: The Handshake That Betrays You#
This fires before a single HTTP byte is exchanged. Understanding it is non-negotiable.

 
 Origin 2017 · Salesforce Research
 JA3, The First Fingerprint
 
 When any HTTPS client connects, it sends a TLS ClientHello message. JA3 extracts five fields from it and MD5-hashes the combination:
 TLS Version + Cipher Suites + Extensions + Elliptic Curves + Curve Formats
 This produced a stable 32-char hex hash. Python's requests library has always had the same JA3 hash. Every major anti-bot catalogued it. By 2021, your Python scraper was identifiable before the first HTTP header.
 JA3's weakness: Chrome started randomising TLS extension order in 2022. Same browser, different JA3 every session. The fingerprint became unstable and unreliable.
 

 
 2023 · FoxIO · Replaces JA3
 JA4+, The Unbreakable Standard
 
 JA4 was engineered specifically to survive Chrome's randomisation. Instead of hashing raw extension order, it sorts extensions alphabetically and removes GREASE values before hashing. The result is stable regardless of Chrome's ordering.
 JA4 format: t13d1516h2_8daaf6152771_b0da82dd1658
 , t13 = TLS 1.3, d = DTLS, 1516 = cipher count+length hash, h2 = ALPN (HTTP/2), remainder = extension hash
 JA4+ extends this with: JA4H (HTTP header fingerprint), JA4X (X.509 certificate), JA4SSH (SSH handshake), JA4T (TCP window + options). Cloudflare deployed it in a Rust crate at CDN edge. Akamai in an EdgeWorker. Both fire before your request reaches origin.
 

 
 HTTP/2 · Wireshark Observable
 HTTP/2 Frame Fingerprinting
 
 Even with a perfect JA4 hash, HTTP/2 itself leaks your client identity. The SETTINGS frame that every HTTP/2 client sends at connection start has parameters that vary by implementation:
 HEADER_TABLE_SIZE, MAX_CONCURRENT_STREAMS, INITIAL_WINDOW_SIZE, MAX_FRAME_SIZE, MAX_HEADER_LIST_SIZE
 Chrome's exact values are documented. Python's httpx sends different values. curl sends different values. The ordering of these settings, the window update frame sizes, and the HPACK compression decisions all create a secondary fingerprint that cannot be spoofed without rewriting the HTTP/2 clientwhich is exactly what curl_cffi does.
 

 
 2024+ · Emerging Standard
 QUIC / HTTP/3 Fingerprinting
 
 As HTTP/3 adoption grows, JA4Q and QUIC Initial packet fingerprinting are being deployed. QUIC's handshake carries its own fingerprint surface: connection ID length, transport parameters, initial packet number, token presence.
 Chrome's QUIC stack differs from libcurl's QUIC implementation differs from Python's aioquic. Each leaves a unique signature in the Initial packets.
 Current status: JA4+ covers QUIC. Cloudflare has begun collecting QUIC fingerprints. Not yet widely enforced for blocking, but the infrastructure is live. Tools like curl_cffi are actively implementing QUIC parity.
 

python
# Test your actual JA4 fingerprint against tls.browserleaks.com
import requests
from curl_cffi import requests as cffi

# ❌ requests, exposes Python/urllib3 JA4, blocked immediately
r1 = requests.get("https://tls.browserleaks.com/json")
print(r1.json()["ja4"])
# → t13d1516h2_8daaf6152771_b0da82dd1658 (Python fingerprint, catalogued, blocked)

# ✓ curl_cffi, emits Chrome 124's exact JA4 hash, HTTP/2 frames, cipher order
r2 = cffi.get(
 "https://tls.browserleaks.com/json"–
 impersonate="chrome124" # also: chrome110, chrome107, safari17
)
print(r2.json()["ja4"])
# → t13d1517h2_c4b4b4b4b4b4_aaaaaaaaaa (Chrome 124 fingerprint, passes)

# Also check HTTP/2 fingerprint
print(r2.json()["http2"]) # Chrome's exact SETTINGS frame values

 
 
 Practical · How to actually spoof TLS in 2026
 From theory to working code
 
 All the JA4+ research is academic until you ship it. Three tiers of solution, in order of how often you should reach for each:
 
 
 
 Tier 1 · 80% of cases
 Use a TLS-impersonating HTTP client
 
 curl_cffi (Python), tls-client (Go), noble-tls, hrequests. One line of code, exact Chrome/Firefox JA4. Drop-in replacement for requests.
 curl_cffi.requests.get(url, impersonate="chrome131")
 
 
 
 Tier 2 · Scrapy projects
 Plug a stealth middleware in
 
 scrapy-stealth adds TLS + HTTP/2 fingerprinting + proxy rotation + fingerprint cycling to existing Scrapy spiders via DOWNLOADER_MIDDLEWARE. Per-request engine switching keeps simple URLs fast.
 meta={"stealth": {"profile": "chrome_147"}}
 
 
 
 Tier 3 · Hardest targets
 Browser with C++ patches
 
 When TLS spoofing alone fails (Akamai extension probes, Kasada toString checks, behavioural ML), reach for Camoufox, rayobrowse, or CloakBrowser. C++ binary patches ship a real-browser TLS stack along with everything else.
 Cost: 200MB+ memory per browser instance
 
 
 
 
 
 ⚠ Common mistakes
 
 1. Spoofing User-Agent without TLS. If your UA says Chrome but JA4 says Python urllib3, you flag faster than no spoofing at all, the mismatch is the signal.
 2. Forgetting HTTP/2 SETTINGS frames. Even perfect JA4 fails if your HTTP/2 SETTINGS (header table size, max concurrent streams, initial window size) do not match the browser you claim to be. curl_cffi and tls-client handle this; rolling your own usually does not.
 3. Using stale impersonation profiles. Chrome 120 fingerprints in 2026 are themselves suspicious, real users rolled forward. Keep impersonate="chrome131" or newer.
 
 
 
 

What "impersonate a browser" means at the byte level, and why a real browser fingerprint is not one value but a set of consistent tells. The reason a default HTTP client gets a 403 it cannot explain is that the ClientHello is built by its TLS library (OpenSSL for requests, Go's crypto/tls for Go clients) with that library's own cipher order, extension set, and ordering, a fingerprint that matches no shipping browser. Tools in the curl-impersonate lineage (and the Go uTLS library that pioneered this) do not tweak that ClientHello, they construct it manually, byte by byte, to reproduce a specific browser's exact handshake. Knowing the tells they have to match is what lets you tell a convincing impersonation from a leaky one.

 
 
 The per-browser discriminators
 GREASE presence, curves, and extension order
 A handful of stable signals separate the real stacks. GREASE (RFC 8701: random sentinel values like 0x0a0a a client injects so servers stay tolerant of unknown values) is present in Chrome, Edge, and Safari, but Firefox sends none, so a ClientHello that claims to be Chrome with no GREASE is self-contradictory. Safari advertises curves the others usually omit (for example secp521r1) and orders its suites and extensions distinctly. Chrome and Edge are both Chromium but not byte-identical. The renegotiation-info extension (0xff01) is present in stock browsers and a common omission in spoofs. Each TLS library has its own defaults (Chrome uses BoringSSL, Firefox uses NSS), and the gap between those defaults and a browser's exact bytes is exactly what a fingerprint database scores.
 
 
 
 Why pinning a profile is fragile
 Match the whole stack, and keep it current
 Impersonation libraries pin to a named profile (HelloChrome_131 and the like) because a known-good fingerprint is safer than auto-updating to an untested one, but that stability cuts both ways. Real Chrome rolled forward and randomises its extension order per connection since 2023, so a frozen profile drifts from the live browser, and named uTLS CVEs have shown pinned fingerprints leaking subtle inconsistencies (an ECH cipher choice that real Chrome never makes) on a fraction of connections. Two rules follow. Keep the profile current, a 2024 Chrome hash in 2026 is itself a tell. And keep it coherent across the whole stack: the TLS fingerprint, the HTTP/2 SETTINGS frame, the header wire order, and the User-Agent must all name the same browser, because a Chrome JA3 under a Firefox User-Agent is an instant contradiction.
 

Two TLS practicalities that trip people up: the MITM-proxy fingerprint, and TLS 1.3 session resumption. First, if you route traffic through an intercepting proxy (Burp, mitmproxy, and the like) the target never sees your browser's handshake. Such a proxy runs two independent TLS connections, your browser to the proxy, then the proxy to the server on its own stack, and the server only ever sees the second one, while the browser-grade HTTP headers (including the User-Agent) ride on top unchanged. So the server reads a request that says Chrome or Firefox over a handshake that is plainly the proxy's Java or Go TLS library, and that mismatch alone is enough to get silently downgraded. The practical symptom is worth memorising: auth or session endpoints intermittently failing with vague generic errors and no CAPTCHA ever shown, because an invisible risk-based check just failed closed. Before assuming an endpoint is broken, echo your real outbound handshake against a fingerprint service (tls.peet.ws and similar) and confirm it looks like the browser you are claiming.

Second, do not read too much into a fingerprint that changes between two runs of the same client. TLS 1.3 session resumption makes this happen legitimately: the first connection to a host has no cached session so the client offers a session_ticket extension, and the second reuses the ticket and offers pre_shared_key instead, so the ClientHello bytes, and therefore the JA3 hash, genuinely differ between first and repeat visits. It is not randomness or a timestamp, it is normal protocol behaviour that real browsers and proxies both exhibit, so a differing hash on a repeat test does not mean your spoofing broke. This is also why no serious detector allowlists one exact hash: real browsers already do not produce a single stable fingerprint (Chrome has randomised its TLS extension order since early 2023, which is part of why JA4 sorts extensions before hashing), one vendor reports on the order of fifteen million distinct JA4s across hundreds of millions of real users in a single hour, and a routine client-library update has been documented raising a mobile app's block rate by several percent purely from legitimate users' fingerprints shifting. The takeaway for a scraper is calibrating: the target is to land inside the broad cloud of plausible modern-browser fingerprints, not to clone one canonical hash byte-for-byte, because the detector is scoring closeness among many inputs, not string-matching a single approved value.

The reframe that changes what TLS is for: reputation, not resemblance. Everything above asks one question, does this handshake resemble a real browser. That question has a shelf life, because resemblance is cheap to fake, curl-impersonate and the uTLS crowd reproduce a browser's JA4 byte for byte, so a pure resemblance check eventually flags nothing. A sharper question is not what a fingerprint looks like but what it does: behaviour is not a property of the handshake, it is a property of everything that handshake has ever been seen doing. Research built from inside a residential proxy network (where a node sees the firehose of JA3/JA4 handshakes and the SNI each one reaches for) turns TLS from a per-connection shape check into a reputation layer, and the tell that falls out is promiscuity. A real browser's fingerprint touches the handful of domains its human actually visits; a tool fires the same barrel at a bank, a sneaker drop, a social API, and an airline in a row, and nobody browses like that. You judge a handshake by how lit-up it is across everything you have watched it do, not by one connection in isolation.

 
 
 The uncomfortable finding
 It is not fake Chrome, it is real Chrome being driven
 In that corpus about 56% of connections carried a Chrome/Chromium TLS fingerprint, almost exactly Chrome's real market share, which looks reassuring until you read the destinations: a large majority of that "Chrome" was automation infrastructure, social-media APIs and attestation endpoints, anti-bot gauntlets being ground through (DataDome, ThreatMetrix, Turnstile, Queue-it), retail scalping, ticket bots, and the dead giveaway, domains no human browser ever visits (anti-detect-browser and sneaker-tool phone-home endpoints). The reason it fingerprints as genuine Chrome is that it is genuine Chrome: headless Chrome under Playwright or Puppeteer, Electron, Android WebView, and the anti-detect browsers, which are Chromium forks. The moment an adversary stops faking a browser and starts driving one, the handshake cannot tell the driver from the driven, or from a real person, which is exactly why that route is chosen.
 
 
 
 One test that still separates them, and the limit
 The permutation check, and where TLS runs out
 There is a clean discriminator between driven-real-Chrome and cheap static fakes. Since Chrome 110 the browser shuffles its own ClientHello extension order on every connection, so one Chrome JA4 fans out into around a hundred distinct JA3s over repeated visits. Genuine Chrome traffic permutes correctly essentially all the time; a static impersonator with a perfect JA4 but a fixed hello never permutes, and that stillness is the tell (mature impersonation libraries can reproduce the shuffle, but the lazy tier does not). The honest limit is the lesson, though: TLS reputation is excellent at naming the software and catching the tier that never tried, but once the answer comes back "real Chrome, permuting correctly," the handshake has told you everything it can. It can say this is Chrome; it cannot say this is a human's Chrome. That single question, is there a person here, is exactly the one the network layer cannot answer, which is precisely why a JS challenge, hardware probing, and behavioural checks exist above it.
 

JA4 is not as stable as its reputation suggests, and the instability has a stealth cost worth understanding. The session-resumption wrinkle above is a specific case of a broader problem: JA4 counts and hashes every extension it sees, including ephemeral ones the client only sends in certain states. On a TLS 1.3 resumption a browser adds pre_shared_key (0x0029) and often padding (0x0015), which bumps the extension count and changes the hash, so the same browser on the same machine emits two different JA4s (for example a ...1516... variant with no key and a ...1517... variant with the pre-shared key, note the 16 becoming 17). A proposed refinement, JA4E, strips the ephemeral extensions before hashing to recover one stable per-client identity. From the evasion side this reframes the target: staying coherent now means aligning your TLS state with your application state. If a site has already handed you a session cookie, your HTTP layer is claiming to be a returning visitor, so a cold TLS handshake with no pre_shared_key is a contradiction between the two layers, exactly the kind of cross-signal mismatch the rest of this section is about. A returning-user HTTP state riding a first-time TLS handshake is a tell.

Two more sharp edges for anyone building or defeating a TLS fingerprint. First, GREASE: modern browsers inject deliberately random, meaningless values into the handshake (so servers stay tolerant of unknown parameters), and JA4 as commonly implemented ignores them. You should not try to compute the GREASE value, because Chrome randomises it every request, but its mere presence is signal a stricter fingerprint could flag, and a client that never emits GREASE where a real Chrome would is as odd as one that gets it wrong. Second, a concrete capture-side trap: as of Chrome 151, GREASE is also injected into the signature_algorithms extension, so a fingerprint parser that does not filter GREASE out of that field before hashing will compute a fresh, wrong JA4 on every single request from the same client. If you run a TLS-capture site or a fingerprint database, filter GREASE across every extension, not just the cipher and extension lists, or your own corpus will look like an infinite population of one-off clients. And note the ceiling of the format itself: JA4 (and JA4+) does not natively capture HTTP/2, HTTP/3, or QUIC transport parameters, which is why the HTTP/2 frame-ordering fingerprint (often called the Akamai HTTP2 fingerprint) is tracked separately rather than folded in.

Below the handshake: the layer most stealth work never reaches. TLS is where this section starts because it is where most practitioners start, but it is not actually the bottom. The TCP SYN packet that opens the connection carries its own signature: initial window size, maximum segment size, window scaling, the exact set and ordering of TCP options, and the initial time-to-live. Those values come from the operating system's network stack, not from your HTTP library and not from your TLS library, which is precisely what makes them useful to a defender. A request can present a flawless Chrome-on-Windows story at every layer you control and still contradict itself at layer 4, because the packet was assembled by a Linux server kernel. Passive OS fingerprinting from SYN packets is old technique, and it is now being applied to proxy egress specifically: the question stops being what your client claims and becomes what kind of machine actually sent the first packet. It is a small share of blocks compared with IP reputation and behaviour, but it is the residue that survives everything else, and it is invisible from inside the browser.

 Layer 2, JavaScript Fingerprinting: The Page That Interrogates You#

2026 update: Microsoft Edge now silently returns navigator.webdriver = false for AI-agent-driven Playwright sessions, and Google patched out the most common CDP-detection technique in V8. The browser vendors that wrote the automation-transparency rules quietly stopped enforcing them. Any bypass or detection strategy pivoting on these flags should treat them as unreliable. See the Innovation Feed card "The Vendors That Wrote the Detection Rules" for details.

Once your TLS passes, the page loads its anti-bot script. This is a 500KB+ obfuscated interrogation that runs dozens of tests in parallel.

 
 Most stable signal · GPU dependent
 Canvas + WebGL Fingerprinting
 
 The page draws invisible shapes, gradients, and text using canvas.getContext('2d') then calls canvas.toDataURL(). The exact pixel output varies by:
 , GPU manufacturer and model (NVIDIA vs AMD vs Intel)
 , Driver version and sub-pixel rendering
 , OS-level font rendering (Windows ClearType vs macOS CoreText)
 , Canvas size and DPI scaling
 A headless Chromium with no GPU produces a software-rendered canvas with a known hash. Botaaurus and CloakBrowser spoof this at the C++ level by injecting slight noise into the pixel values before toDataURL() returns, enough to vary the hash while remaining visually identical.
 

 
 GPU vendor string · Renderer string
 WebGL Fingerprinting
 
 WebGL exposes the GPU through gl.getParameter(gl.RENDERER) and gl.getParameter(gl.VENDOR). Real Chrome returns something like ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0).
 Headless Chrome returns a generic string or crashes on WebGL entirely. Anti-bots cross-reference: if WebGL says "Intel UHD 620" but Canvas hash shows software rendering, that's a contradiction, you're flagged.
 WebGL extensions list is also fingerprinted. Real GPUs expose 30–40 extensions. Software renderers expose a different subset. The exact combination is GPU-specific and stable across sessions.
 

 
 Deterministic · Hard to spoof
 AudioContext Fingerprinting
 
 The page creates an AudioContextgenerates a sine wave through an OscillatorNoderuns it through a DynamicsCompressorNodeand reads the output buffer values. The floating-point output depends on:
 , CPU architecture (x86 vs ARM floating-point precision)
 , Operating system audio stack
 , Audio driver implementation
 Headless environments often return 0.0 across the buffer (no audio context), or a software-emulated value that differs from hardware. CloakBrowser patches this at the Chromium C++ audio rendering layer.
 

 
 Runtime patches exposed · 2026 standard
 Function.toString() Detection
 
 This is why playwright-stealth fails against Kasada in 2026.
 When JS patches a native function, for example, navigator.webdriverit replaces the getter with a custom function. Calling Function.prototype.toString.call(getter) on the patched function returns function () { [custom code] } instead of function () { [native code] }.
 Kasada specifically tests dozens of native functions this way. playwright-stealth patches them in JavaScript, so toString() reveals the patch. PatchRight fixes this at the Python source levelbefore Chrome even starts. There's no JS to inspect.
 

 The harvest-and-replay threat (Castle Research, April 2026): Real canvas hashes from real Macs are now commercially traded. Services like Bablosoft's PerfectCanvas render canvas on a real remote GPU and inject the result into headless sessions. A "passing" canvas hash is not proof of a real browser, it may be a replayed hash from a legitimate device. Detection systems are responding by pairing canvas probes with harder-to-replay signals: WASM SIMD CPU timing, behavioural Bezier-curve physics, and controlled variability in their own probe scripts. Full report.

 
 Akamai specific · 60 probes in 2026
 Chrome Extension Probing (Akamai)
 
 Akamai's sensor.js fetches 60 known Chrome extension resource URLs using fetch('chrome-extension://[id]/manifest.json'). Real Chrome browsers have at least a few extensions installed (ad blockers, password managers, etc.).
 A headless browser returns net::ERR_FAILED on all 60 requests simultaneously, a statistically impossible result for a real user. The extension IDs probed include:
 cjpalhdlnbpafiamejdnhcphjbkeiagm (uBlock Origin)
 hdokiejnpimakedhajhdlcegeplioahd (LastPass)
 nngceckbapebfimnlniiiahkandclblb (Bitwarden)
 Fix: CloakBrowser loads real extension profiles. You install 1Password or Bitwarden into it so some probes return real manifest data.
 

 
 CDP timing leaks · Protocol signals
 Headless / CDP Detection
 
 Beyond navigator.webdriverCDP-controlled browsers expose themselves through subtler signals:
 Timing: CDP's Runtime.enable command leaves a timing gap between page parse and script execution that doesn't exist in real Chrome.
 Execution context: window.cdc_adoQpoasnfa76pfcZLmcfl_Array and similar artifacts left by ChromeDriver are checked.
 Permission API: Real Chrome returns realistic permission states. ChromeDriver returns defaults inconsistent with a "normal" browser.
 Plugins: Headless Chrome has zero plugins. Real Chrome always has at least the PDF viewer plugin.
 Camoufox's solution: Uses Mozilla's Juggler protocol, which sits below CDP entirely, none of these artifacts exist.
 

How the top tier weaponises the gap between two layers: the Akamai escalation trap. The layers above are usually described as independent checks you pass one at a time. The hardest deployments do the opposite, they cross-check one layer against another so that passing each in isolation still fails. A cold-start agentic mapping of a live Akamai sensor laid this out cleanly, and it is worth internalising because it explains why a technically perfect client payload can still earn a permanent block.

 
 
 The trap
 TLS at the edge cross-checks the behavioural payload
 Akamai evaluates the connection's TLS fingerprint at the edge before any client JavaScript runs. A consumer TLS stack (for example Safari emulated through curl_cffi) gets a 200 carrying the sec-cpt challenge; an automation stack (stock Node or Puppeteer Chromium) gets an immediate 403 and never sees the challenge. The trap is what happens next. Even if a real Chromium driven by a stealth tool solves sec-cpt and serialises a mathematically perfect roughly 2.2KB behavioural payload to validate the _abck cookie, the edge still refuses it when the TLS fingerprint on that same connection reads as the automation tool. Perfect behaviour over the wrong TLS escalates to a permanent 403 for the session. The lesson the guide repeats applies at its sharpest here: every layer must agree at once, a flawless payload over a mismatched handshake is worse than useless.
 
 
 
 The anti-hook
 The "clean iframe" defeats window-object patches
 Stealth tools commonly patch native APIs on the page's window (overriding navigator.webdriver, wrapping DOM methods in a Proxy, replacing toString). Akamai sidesteps all of it by injecting a hidden zero-size iframe and pulling pristine API references straight from iframe.contentWindow, so it can call the original native functions even when the main window is heavily spoofed. It pairs this with Function.prototype.toString checks for the [native code] marker and Object.getOwnPropertyDescriptor inspection of getters that should not exist, then ships an integrity hash to a separate anti-hook endpoint before the main telemetry is even sent. Practical consequence: patching the visible window is not enough, your patches must survive being compared against a clean realm the page can conjure at will.
 

The reference open-source fingerprinter: FingerprintJS, and the one line in its own README that every scraper should read. If you want to actually see what a fingerprinter collects rather than read about it, the canonical starting point is FingerprintJS, the most widely used open-source (MIT) browser-fingerprinting library. It runs entirely client-side, queries a broad set of browser and device attributes, and hashes them into a single stable visitorId, and the property that makes fingerprinting matter at all is easy to demonstrate with it: open its demo, then reopen in incognito or after clearing browser data, and the identifier is unchanged, because none of the signals it reads live in cookies or local storage. Running it against your own scraper is the fastest honest audit of how identifiable your setup is, and it pairs naturally with the local BotD and CreepJS harness described just below.

 
 
 Identification is not detection
 FingerprintJS vs BotD: same company, different jobs
 Keep two of this vendor's libraries straight, because they answer different questions. FingerprintJS does identification, is this the same browser I saw before, producing a stable visitor ID for deduplication and tracking. BotD, from the same team and referenced just below, does bot detection, is this browser automated at all. A scraper is exposed on both axes at once: FingerprintJS-style signals link your sessions together across IPs (the rotation-inversion problem from the proxy section), while BotD-style checks flag the automation itself. Beating one does nothing for the other, which is why a coherent identity and a clean automation surface are separate pieces of work.
 
 
 
 The admission in the README
 Client-side fingerprints can be spoofed, so the hard version moves server-side
 The most useful thing in the FingerprintJS documentation, from a scraper's point of view, is its own stated limitation: because the fingerprint is generated and processed in the browser, it is openly described as vulnerable to spoofing and reverse engineering, and the open-source accuracy is "significantly lower" than the commercial tier. That commercial version closes the gap precisely by moving work out of reach, combining the client signals with server-side processing over 100-plus signals plus network-level data, and validating that signals were not tampered with or replayed. That single design choice is the whole detection arms race in miniature: anything computed in the browser you can eventually forge, so serious systems anchor the verdict where your code cannot reach, which is the same lesson as the tanh and coherence sections, viewed from the vendor's side.
 

A measured warning: the JavaScript-surface score tells you almost nothing about whether you are caught. It is tempting to judge a stealth setup by how clean it looks to an in-page fingerprint probe, but that score and the actual block decision are only loosely related. The signal that flips many verdicts lives below JavaScript, in the driver binary. A practitioner who stood up the open-source detectors locally (BotD and CreepJS) and ran stock Selenium, selenium-stealth, and undetected-chromedriver against real Chrome found the gap directly: selenium-stealth cleaned up roughly 94 percent of the JS-surface checks, yet BotD still labelled it "selenium," because the stealth layer spoofs in-page properties but never strips the ChromeDriver binary's $cdc_ markers, which is exactly what BotD keys on. Only undetected-chromedriver, which patches those markers out of the binary, passed outright.

 
 
 The counterintuitive part
 Naive stealth can make you more detectable
 The same harness showed selenium-stealth's getter-based spoofing adding two CreepJS "lies" that plain Selenium did not have. When you override a property with a JavaScript getter, the override is itself a detectable artifact: a function where a native value belongs, a toString that does not read as [native code], a descriptor that should not exist. A consistency-checking detector counts each of those as evidence of tampering. So a half-measure that patches the visible surface while leaving the binary tell in place can score worse than no stealth at all, because you have removed nothing that mattered and added new lies to catch.
 
 
 
 The operating rule
 Fix the layer the detector actually reads
 This is why the library section rates binary-patching and below-CDP tools (undetected-chromedriver while it held, nodriver, the source-patched browsers) above JS-injection stealth on hard targets. The rule generalises past Selenium: identify the layer a given detector actually keys on (driver binary markers, the TLS handshake, the automation protocol) and fix it there, rather than polishing a JavaScript surface the detector may barely weight. And measure it: a local BotD or CreepJS harness turns "I think this is stealthy" into a before-and-after number, which is the only way to notice when a patch made things worse.
 

A reproducible way to rank stealth setups, and one CDP gotcha it exposed. An open build-in-public benchmark (stealthbench) scores automation configs against self-hosted detectors on localhost and commits every result, and its matrix is a useful sanity check on the folklore. Across a Sannysoft-style panel, BotD, CreepJS, and rebrowser-bot-detector, the runtime-patching CDP configs came out on top: undetected-chromedriver and its successor nodriver (which drives Chrome straight over the DevTools Protocol with no Selenium) passed every panel cleanly and occasionally aced the full tells panel, while stock Selenium and selenium-stealth got dinged on the same handful of automation giveaways every time, and Camoufox sat just behind the Chrome leaders. One honest wrinkle worth keeping in mind when you read any such table: a Firefox tool legitimately cannot answer Chrome-specific tests, so it runs a smaller denominator rather than failing, and a benchmark that treats that absence as a fail understates it. The methodological point is the durable one, a new detector that confirms your existing ranking is evidence the ranking was measuring something real, not noise.

The bug that build surfaced is a genuine trap for anyone reading signals back over the DevTools Protocol. Driving Chrome via CDP, a call like evaluate(..., return_by_value=True) can silently drop falsy return values if the client's unwrap logic tests truthiness (an if result.value: style check), so a script that legitimately returns false, 0, or "" comes back as nothing. This is quietly catastrophic for stealth work, because the values a bot detector traffics in are exactly the falsy ones: navigator.webdriver is supposed to read false on a good setup, and if false silently vanishes you cannot tell a passing config from a broken read. The clean fix is to stop trusting the transport's unwrap and force a round-trip: wrap the probe in JSON.stringify(...) on the page and JSON.parse (or json.loads) the returned string, so 0, false, "", and nested objects all survive verbatim (the one edge is a script evaluating to undefined, which stringifies to a bare word rather than valid JSON, so treat a non-string result as an unusable signal). The general lesson beyond this one library: when you read detection signals through any automation transport, verify the transport does not reshape your values before you trust a single result, because a corrupted read looks exactly like a passing check.

A second, larger benchmark reaches the same verdict and adds two methodology lessons worth stealing. An independent fifteen-tool run against live detectors sorted the open-source Python automation stack cleanly, and the pattern held: the tools that passed the strict referee mostly won by rebuilding the browser rather than patching flags onto a normal Chrome, engine-level Firefox and Chromium builds (camoufox, a patched-Chromium binary, a self-hosted stealth Chromium serving real-device fingerprints) plus a stealth-fetcher wrapper, none of which leave a HeadlessChrome user agent or anything that reads as CDP automation. Everything that still leaves navigator.webdriver true sat at the bottom, and the middle tier was, in the author's phrase, one user-agent string away from a clean pass. That is the same rebuild-beats-patch conclusion from the matrix above, reached with a different tool set, which is exactly the kind of agreement that tells you the ranking measured something real.

The two transferable lessons are about honest measurement. First, scope: that benchmark tested only the JavaScript fingerprint surface and said so, on the reasoning that transport (TLS and HTTP/2) depends on the browser's own network stack rather than the Python library wrapping it, timing is a behavioural question, and proxy reputation is about your IP, so swapping libraries only moves the JS-surface needle. Knowing which layer a score actually covers is the difference between a number you can use and one that misleads you, a tool can ace a JS-only bench and still die on TLS or IP. Second, a concrete artefact trap the author controlled for: Playwright's bundled headless Chromium reports a software WebGL renderer (SwiftShader), which is a headless artefact of that binary, not a tell the tool introduced, so pointing every Chrome-based tool at the real installed Chrome (channel="chrome") is what makes the comparison measure stealth rather than which Chromium happened to ship. The general habit both of these teach is to calibrate against outside referees rather than your own probe, cross-checking against independent bot-detection pages that return a structured verdict keeps your weighting honest.

A new hardware tell shipping in Chrome: the CPU Performance API. Sites have always wanted to know how powerful your device is, historically by running a timing benchmark, which is noisy and easy to perturb. Chrome is standardising a shortcut. Reading navigator.cpuPerformance returns a stable integer tier from 1 (low) to 4 (high), with 0 for unknown, a coarse hardware class handed over with no benchmark, no opt-out, and no cross-origin limit. There is also a managed-Chrome policy, CpuPerformanceTierOverride, that pins the reported tier — which is worth knowing in both directions: a supported way to set the value without patching anything, and a reason a detector cannot treat the tier as ground truth. It is a WICG proposal, deliberately bucketed (each tier must cover a few hundred CPU models) to cap the entropy it adds, but Mozilla still estimates it at one to three extra bits per user and WebKit declined to implement it, which tells you how the other engines weigh the tradeoff. There is even a Chrome Enterprise policy to override the value (0 to 4).

 
 
 Why a fixed number helps the defender
 A tier you cannot benchmark your way out of
 For a real user the tier is just a stable fact about their machine. For automation it is a fresh consistency check that costs the detector nothing. Because the value is declared by the browser rather than measured, a stealth setup cannot earn it by behaving well, it either matches the rest of the device story or it does not. And because the buckets are time-invariant, the same machine reports the same tier across visits and across origins, so it composes with every other signal a fingerprint already carries. A single coarse number is weak alone, which is the whole design, but it is one more axis that has to agree.
 
 
 
 Where it bites a spoofed box
 Cross-check the tier against the rest of the silicon story
 The interesting move for a detector is to cross the advertised tier against the other hardware signals and look for a machine telling two stories about itself. Report navigator.hardwareConcurrency of 16 (spoofed) on a 2-vCPU cloud box whose cpuPerformance lands at a low tier, and the two claims disagree loudly. Push further and a smart WASM micro-benchmark (SIMD timings, feature detection) fingerprints the actual silicon and either corroborates the tier or exposes the lie. The practical lesson for a scraper is the one this section keeps arriving at: if you spoof one hardware property you now own all of them, the tier, the core count, and the measured compute all have to tell a single coherent story, and a headless fleet on undersized cloud instances is exactly where they stop doing so.
 

A worked example of what "spoof one thing and you now own all of them" looks like in practice. It is worth seeing the coherence principle as a real remediation report reads it, because the failures are never where a beginner looks. Take a headless desktop Chromium configured to present as an Android mobile Chrome (claimed identity: Chromium / Android / mobile / Chrome, with the CPU architecture field left empty). To a first-order fingerprint it looks plausible. A full audit finds it contradicting its own story in at least seven independent places at once, and every one of them is a separate subsystem the operator forgot had an opinion.

 
 
 The contradictions an audit surfaces
 Seven subsystems, one incoherent device
 The CPU architecture, exposed through a WASM relaxed-SIMD instruction-selection oracle, reads as x86 desktop, not the ARM an Android device would run. navigator.maxTouchPoints is 0, but a phone reports one or more touch points. The Opus audio codec ships its desktop complexity default, a mobile build tunes it differently. The Widevine DRM stack and PaymentRequest service are missing or stubbed, where a real Android Chrome exposes both. The WebGL renderer string names desktop silicon, and the media power-efficiency profile matches a plugged-in desktop, not a phone. The automation protocol (CDP) is independently detectable on top. No single one of these is exotic; together they describe a device that cannot exist.
 
 
 
 Why the fix order is the lesson
 Config is cheap, coherence is architectural
 The instructive part of such a report is the remediation ladder. The cheapest wins are config and build flags: disabling the CDP automation domain, patching the Opus complexity constant, forcing maxTouchPoints to a mobile value, shipping a real Widevine CDM. But the deep fixes are architectural and cannot be faked from JavaScript: to make the SIMD CPU oracle agree with the ARM claim you have to actually run on ARM64 hardware or a VM, and a JS override of PaymentRequest or the GPU renderer leaves its own tamper artifact that a coherence check then catches. That is the whole thesis of this section in one page: the shallow tells are a checklist you can grind through, but the ones that decide the verdict require the environment to genuinely be what it claims, which is why a desktop box pretending to be a phone loses no matter how many properties it patches.
 

The purest example of "it is harder to lie than to tell the truth": your browser does math differently on each operating system. Most fingerprint signals leak because a browser exposes a property it could, in principle, be patched to fake. This one is different, because the browser is not the thing computing the answer. IEEE 754 fixes how a floating-point number is stored but does not require transcendental functions like sin, cos, or tanh to be correctly rounded to the last bit. Every operating system ships its own math library (glibc on Linux, libsystem_m on macOS, UCRT on Windows) with its own polynomial coefficients and rounding, so the same call returns bits that differ in the last place depending on the OS underneath.

 
 
 Why tanh in particular
 The one JavaScript math function that leaks the OS
 For most of Math, V8 bundles its own implementation, statically linked and identical on every OS, so sin, cos, and pow give the same bits everywhere and leak nothing. Math.tanh is the exception. Since Chrome 148, V8 stopped computing tanh with its own bundled routine and now calls std::tanh, which reads the host OS math library. The result: on Chrome 148 and later, Math.tanh of the right input returns Linux bits on Linux and Mac bits on Mac, and a Linux server spoofing macOS is caught the instant a page evaluates it, because the browser genuinely cannot change the answer, the OS produced it. Chrome 147 and earlier do not leak here, which itself pins a version range.
 
 
 
 The wider surface
 CSS trig leaks everywhere, and the edges are checked
 JavaScript Math is a tell in essentially one place, but CSS trig functions leak everywhere, because the rendering engine calls the host math library directly for every sin, cos, tan, and inverse. A defender can probe those and also the domain edges, where implementations diverge more loudly: asin(2) is out of domain and resolves to zero on a real Mac (the NaN is clamped), not the ninety degrees a naive reproduction returns. The lesson for a scraper is the sharpest version of this whole section's theme. You cannot patch your way to a coherent lie here, because the signal is produced below the browser, by hardware and the OS, so the only setup that survives a math probe is one whose claimed OS is the OS it runs on. Spoofing the User-Agent is free; making the silicon agree is not.
 

The subtle trap in math fingerprinting: the expected values decay, because "Chrome" was never a stable identity for a floating-point result. There is a second lesson here that cuts the other way, and it bites defenders and naive spoofers alike. These math results are not browser constants; they shift whenever an engine changes how it does the arithmetic, which happens far more often than fingerprint tables assume. V8 has been steadily replacing its bundled 1993-era fdlibm port with LLVM's libc, and each migration moves bits: Chrome 149 and 150 disagree on roughly 4.5% of sampled inputs to the migrated functions, every disagreement being the old implementation off by one unit in the last place. The change is permanent in a specific way, correct rounding has only one answer, so once an engine gets there the wrongness that made it distinctive is gone, and every engine that arrives spits out identical bits. The wrongness was the signal. The practical fallout is that a fingerprint library holding a table of "what Chrome returns" goes stale silently: run a current CreepJS-style table against a freshly migrated Chrome and a large share of the math rows no longer match its Chrome column, and some now match Firefox instead. The trig rows went stale back in Chrome 110 for the same reason. The takeaway for both sides: the useful key is not "Chrome" but the exact build, chrome_149 and chrome_150 are different fingerprints, and treating an engine family as one stable value is how a detector starts misclassifying its own real users, and how a spoofer targets a value the real browser stopped returning two versions ago.

Two functions are the exception that proves the OS-not-engine point: Math.pow and Math.tanh never used V8's math library at all (pow sits behind a use_std_math_pow flag defaulting true; tanh is one line calling std::tanh), so both go straight to the operating system's libm, ucrtbase on Windows, glibc on Linux. That is why Math.pow(17, 13) returns 9904578032905936 on Windows and 9904578032905938 on Linux, same browser, same version, and why a table that files tanh(0.123) under a single "Chrome" value gets it wrong on one of the two OSes. The vivid case: because that call now hits the system libm, Linux Chrome returns a value a stale table labels as Chrome while Windows Chrome returns one the same table labels as Safari, identical browser and version, two different engine labels, purely from the OS underneath. It is the cleanest possible proof that these values measure the platform, not the browser string, and that any fingerprint keyed on the browser name is standing on sand.

The counterintuitive corollary at the HTTP layer: sometimes sending fewer headers is what gets you through. The instinct when a plain HTTP client is blocked is to make it look more like a browser, copy the full navigation header bundle a real Chrome sends, accept-language, the sec-fetch-* set, sec-ch-ua, and so on. On some detectors (DataDome is the reported case) that is exactly backwards. Those headers exist because a browser is rendering a page and running JavaScript. Send the full bundle from a client that plainly is not executing any JS, with no telemetry, no canvas, no timing to justify them, and the mismatch is the tell: the detector flags the combination of browser-grade request metadata with a non-browser client, not any single header. A documented case had a controlled one-variable-at-a-time matrix (proxy, JA3 profile, header order, cache-control all changed in isolation) return a 403 every time, until the header set itself was cut back to a lean, honest handful (user-agent, priority, cache-control), at which point the same endpoint returned 200 and real HTML, no browser and no JS involved. The principle generalises the coherence theme one layer up from TLS and hardware: more apparent realism is not more human when the signals you add have nothing behind them. A client that honestly sends almost nothing is more coherent, and often less suspicious, than one wearing a full browser costume it cannot back up, so match your header set to what your client can actually justify rather than to the longest bundle you can copy.

The other way headers betray you: not what you send, but the order you send it in. You can copy Chrome's header values perfectly and still be flagged, because a real browser emits its headers in a stable, characteristic sequence and an HTTP library emits them in its own. Same values, different order, and the order itself is a fingerprint. This is sharpest on HTTP/2, which lowercases every header so that casing stops being a usable signal, at which point the pseudo-header order carries the weight. Chrome sends its pseudo-headers as :method :authority :scheme :path; most HTTP libraries emit a different order entirely (a common one is :method :path :scheme :authority), and that mismatch is exactly why a request with otherwise flawless headers still dies against Akamai. The trap is that you often cannot fix it from your own code: the library reorders your headers underneath you, and on the classic Python stack urllib3 reorders again below that, so setting the headers in a dictionary does not help because something downstream reshuffles them before they hit the wire. The reliable fixes are the ones that own the whole emission path: use curl_cffi, which impersonates Chrome's full profile with header and pseudo-header order included, or drive a real browser. Right values in the wrong order is not a disguise, it is a tell, and it is one of the most common reasons a hand-built HTTP client that looks perfect on paper fails the moment it meets a serious anti-bot.

How an extension list gets built, and why it reads as evidence of a person. The anti-bot vendors that probe for browser extensions are exploiting a gap the browser deliberately left. Chrome exposes no API that reports which extensions are installed, and that is an intentional anti-fingerprinting decision. Individual extensions can still be detected one at a time, and enough of those checks in sequence reconstructs a list.The main technique is resource probing. An extension declares web_accessible_resources, files it will serve to any page that asks, so a script can request a known file path under chrome-extension:// with the extension's fixed identifier and learn from success or failure whether it is present. Run that against a catalogue of popular extensions and you have an enumerated list.The signal is unusually good for a defender on two counts. It carries real entropy, because the combination of a dozen installed extensions is close to unique, and it is stable, because people rarely add or remove them, so the same list returns tomorrow. The second count is the one that decides tool choice: an extension list is decent positive evidence of a human, since automated browsers almost never ship with any. That is the reasoning behind the vendor probe covered in the Akamai profile, and it is why the browsers that clear it do so by loading genuine password-manager profiles rather than by patching a response. An empty extension surface is not neutral. It is a statement.

 Layer 2.6, Side Channels And State Leaks: The Signals Nobody Thinks To Patch#

 
 Timing side channel · navigator.storage
 Incognito Detection By Write Speed
 
 A recent revival of incognito detection skips feature checks entirely and times the storage backend. In a normal window navigator.storage is backed by disk, in a private window it is backed by RAM, and writing to RAM is measurably faster than writing to disk. Write and flush a single byte, time it, repeat a few times to weed out noise, and a flush under roughly a tenth of a millisecond gives away a private session.
 The lesson is broader than incognito: a detector does not need to read a property you spoofed, it can measure a physical consequence of your environment that no fingerprint patch touches. Timing is not on most people's spoofing checklist.
 

 
 Implementation leak · IndexedDB ordering
 When An Undefined Edge Case Becomes A Fingerprint
 
 A 2026 Firefox flaw is the cleanest example of how a tiny implementation detail turns into an identifier. The browser returned IndexedDB database names in hash-table iteration order rather than a canonical sorted order. That ordering was stable and unique enough to correlate a user across sessions, across private windows, even across a Tor "New Identity" reset, all without a single cookie.
 The takeaway for anyone building scrapers: stealth is not a setting you flip on. Any API behaviour, any implementation quirk, any undefined edge case can become a fingerprint. You cannot enumerate them in advance, which is why coherence across the whole environment matters more than patching individual tells.
 

 
 The countermeasure that actually holds
 Isolate At The Process Level
 
 Both signals above survive the usual defence of a fresh browser profile per identity, because they leak from shared process state, not from the profile. Memory-state artifacts, timing baselines, and hash-table orderings live below the profile boundary. If several scraping identities share one browser process, those hidden signals quietly become a correlation key tying your "separate" identities together.
 The fix is to stop isolating at the profile level and isolate at the process level: one identity, one process, and ideally one host with a coherent fingerprint, rather than many personas multiplexed through a single long-lived browser. It costs more to run. It is also the only isolation the deeper signals respect.
 

 
 Hardware timing · No permission needed
 Disk Latency As A Tracking Vector
 
 The timing family goes deeper than storage backends. A technique making the rounds measures the microscopic latencies of the machine's SSD itself. Every site and active tab generates a slightly different load pattern on the disk subsystem, and ordinary JavaScript can read those timing variations through default web APIs. In principle a script can infer activity in other tabs from the disk-contention signature alone.
 The two properties that make this matter for a scraper: it needs zero permissions, browsers do not prompt for disk access, and neither an ad blocker nor a private window mitigates it. Until vendors fuzz or round these timings there is no clean client-side fix. The general lesson repeats: hardware leaves a signature your fingerprint patch never touches, and a farm of identical VMs can look too identical at the disk layer.
 

 
 Spoof contradiction · Client Hints
 sec-ch-ua Is Deterministic, Not Random
 
 The sec-ch-ua header looks like noise: "Chromium";v="149", "Not A Brand";v="24". It is not random, it is a pure function of the Chromium major version. The brand ordering, the greasey "Not A Brand" label, and the version permutation are all seeded from the major version number over fixed lookup tables, no rand(), no timestamp. Every machine on the same Chromium version produces an identical header.
 That determinism is a trap if you spoof carelessly. Hand-roll a sec-ch-ua that does not match the algorithm for the Chrome version you are claiming, and you have manufactured a contradiction a detector can check with one lookup. If you set the version, derive the header from it rather than copying one from a different build.
 

 
 The pattern across all of these
 Why These Keep Appearing
 
 Storage timing, disk latency, IndexedDB ordering, Client Hints seeding: none of these is on a typical spoofing checklist, and that is exactly why they work. They are second-order signals, derived from physics or from an implementation detail rather than from a property you thought to override.
 You cannot enumerate them all in advance. The defensible posture is not to chase each new tell, it is to keep the whole environment internally consistent and let real hardware speak for itself wherever you can, rather than presenting a hand-assembled identity that has to be right on every one of a thousand axes at once.
 

Layer 2.5, WebAssembly Fingerprinting: The Layer Below Your Stealth Browser#

 
 Build-time leak · Nobody patches this
 Hyphenation Dictionary Detection
 
 Chromium needs a hyphenation dictionary bundled at build time on Windows and Linux (Android and macOS handle it at OS level). Most people forking Chromium don't know this. The benefit for detection: many stealth Chromium forks literally cannot hyphenate words, and that is visible from JavaScript.
 The probe: set hyphens: auto on a narrow container, render a known word like "hyphenation", read the rendered width or screenshot via Canvas. A real Chrome on Windows produces hy-phen-ation. A custom fork without the dictionary produces no break, or the wrong break.
 Affected stealth browsers: anything built from a custom Chromium source that skipped the hyphenation step, which is most of them. Real CloakBrowser and properly-built forks include it, hand-rolled patches usually don't.
 Mitigation: confirm your build ships the dictionary for every language you claim to support, or run a real Chrome binary under XVFB. Verify with the live PoC: joe12387.github.io/hyphenation-dictionary-poc · github source
 

 
 CPU fingerprinting · DataDome internal research
 WASM SIMD probes the CPU itself
 
 WebAssembly SIMD (Single Instruction Multiple Data) gives browsers access to 128-bit vector operations that map directly to CPU instructions. Anti-bots ship tiny WASM modules that execute SIMD ops in deterministic patterns and time them. The results reveal vector register width, NEON vs SSE vs AVX availability, and microarchitecture quirks unique to the CPU model.
 
 Why this matters: stealth browsers like Camoufox, CloakBrowser, PatchRight patch what the browser reports. WASM SIMD probes the actual CPU. A real Mac with M2 chip can't be spoofed to look like an Intel laptop because the SIMD timing fingerprint is generated by the silicon, not by the browser.
 
 Source: Anthony Manikhouth (DataDome bot detection engineer), blog.azerpas.com, May 2026.
 

 
 High-resolution timing · The enabling primitive
 SharedArrayBuffer via WASM gives 17× timer precision
 
 Chrome floors performance.now() at 100µs on non-isolated pages to prevent Spectre-style timing attacks. But one line of JavaScript breaks that: new WebAssembly.Memory({shared:true}).buffer returns a real SharedArrayBuffer on any page, no special headers required.
 
 Paired with a MessageChannel ping-pong loop in a hidden iframe driving Atomics.add(), you get a counter incrementing at 100,000 Hz, distinguishing steps around 6µs. That's 17× finer than the timer Chrome intends you to have.
 
 Why anti-bots love this: micro-timing patterns (canvas render time, JS jitter, animation frame variance) differ between humans and bots at sub-millisecond scale. WASM shared memory makes that measurable on every page, not just cross-origin-isolated ones. Reported to Chrome as crbug 40057687, marked Won't Fix.
 
 Source: Manuel (brokenbrowser.com).
 

 
 Bypass implications
 Why your stealth browser still leaks
 
 WASM fingerprinting sits in a blind spot of most stealth tools. The signal flow:
 
 1. Anti-bot ships a WASM module with SIMD ops and a high-resolution timer built from WebAssembly.Memory({shared:true}).
 2. The module runs natively, no JS hooks to intercept, no Function.toString() traces to leak.
 3. CPU microarchitecture + timing patterns are POSTed back as part of the bot scoring payload, often alongside the canvas hash.
 
 What this defeats: Camoufox (Firefox C++ patches), CloakBrowser (49 Chromium patches), PatchRight, undetected-chromedriver, Nodriver, Pydoll. All of them patch JS APIs and binary internals, but none patches the WASM execution layer.
 
 What still works: real hardware diversity. Different physical machines produce different SIMD fingerprints naturally. The future of stealth scraping is less about better lies and more about real hardware in real consumer locations, which is exactly what residential proxies on real ISP IPs already approximate.
 

 
 CVE-2026-6770 · April 2026 · Process memory leak
 IndexedDB Iteration Order: The Fingerprint Nobody Patched
 Firefox stored IndexedDB database names using internal UUID mappings in a global hash table shared across all origins within the same browser process. When a site calls indexedDB.databases(), the names come back in hash table iteration order, which is deterministic and stable for the lifetime of that process. Two unrelated sites see the same ordering and can use it to silently link a user's activity across domains — no cookies, no shared storage, no user interaction required.
 The fingerprint persisted across reloads, new private windows, and even Tor Browser's "New Identity" resets. Only a full browser restart cleared it. Fixed in Firefox 150 / Tor Browser 15.0.10 (April 21, 2026).
 Scraper implication: If you run multiple scraping identities inside the same browser process (shared Camoufox instance, same Firefox PID), an anti-bot can correlate them using this ordering as a stable session token — regardless of proxy rotation, cookie isolation, or fingerprint patching. The signal is below every stealth layer.
 Rule: Isolate scraping identities at the process level, not just the profile level. One identity = one browser process. Verify your Camoufox build is on Firefox 150+.
 Ref: CVE-2026-6770 · mfsa2026-30 · SecurityAffairs writeup
 

Layer 3, Network Identity: The Five Vectors That Must Agree#

This is the layer most people underinvest in. Beginners pour effort into header spoofing and fingerprint patching while running through a flagged IP, and then wonder why nothing works. It is backwards. If your IP reputation is bad, no amount of header spoofing will save your requests, the request is scored down before your carefully crafted headers are ever read. Infrastructure beats code here: a plain HTTP client on a clean residential IP routinely outperforms a perfectly patched browser on a flagged datacenter IP. Get the network identity right first, then worry about everything above it.

 
 Primary signal
 IP Reputation & ASN
 Anti-bots check your IP against ASN databases. AWS (AS16509), GCP (AS15169), Azure (AS8075) are immediately flagged. DigitalOcean, Linode, Vultr, all known. Even residential proxy networks from DataCenter IPs in the 24.105.x.x range are flagged if the ASN is a known proxy provider. Genuine ISP residential or 4G carrier IPs are the only reliably clean option.

 
 Browser API · Often overlooked
 WebRTC IP Leak
 JavaScript can query WebRTC ICE candidates which reveal your real local and public IP, even through a proxy. If your browser has a US proxy but WebRTC reveals a Pakistani local address, or the ICE candidate is from a different subnet than the HTTP request IP, that's an immediate flag. Camoufox's geoip=True aligns WebRTC candidates with the proxy exit country.

 
 All five must agree
 The Coherence Test
 Anti-bots run a coherence check across: IP country, timezone, Accept-Language, WebRTC candidate, DNS resolver location. A US proxy with Accept-Language: ur-PK fails immediately. All five must tell a consistent geographic story. This is why setting geoip=True in Camoufox is critical, it auto-configures all five to match the proxy's exit country.

Layer 3.5, DOM Honeypots: The Trap Doesn't Care About Your Fingerprint#

 
 
 Hidden DOM elements
 Honeypot Fields and Links
 Invisible form fields and hidden links that humans never see but bots fill in or click. Triggered = bot detected = IP banned. Common patterns: display:none, visibility:hidden, opacity:0zero-dimension elements, off-screen positioning, fields with tabindex="-1"or links placed after the closing </body> tag.
 
 
 
 Data poisoning
 Fake Data Served to Suspected Bots
 More dangerous than blocking, sites detect a scraper and silently serve different prices, fake reviews, wrong stock counts. You think you're scraping successfully but your dataset is corrupted. Defence: compare scrapes from 2+ different IP profiles for the same URL. Mismatched data = poisoning. Always check element visibility (getBoundingClientRect()) before interacting.
 

Layer 4, Behavioural ML: You Can't Fake Being Human#

 
 Physics-based · Gaussian jitter
 Mouse Movement Curves
 Human mouse movements follow Bezier curves with Gaussian noise applied to velocity. The mouse decelerates as it approaches a target (Fitts's Law), overshoots slightly, then corrects. Scrapers that click elements directly (teleporting the mouse to x,y) create a trajectory signature that's statistically impossible for a human. DataDome's 35-signal behavioural model catches this immediately. Botasaurus generates physically realistic curves with randomised velocity profiles.

 
 Sub-millisecond precision · ML scored
 Timing Analysis
 Transformer ML models trained on millions of sessions measure: time between page load and first interaction, scroll acceleration curves, inter-keystroke timing variance, navigation dwell time, and micro-timing of JS event handlers at <1ms precision. A scraper that immediately calls document.querySelector() after DOMContentLoaded looks nothing like a human who reads the page for 2.3 seconds first. Warm-up navigation (visiting homepage before target) significantly improves behavioural scores.

A sharp edge most stealth setups forget: behaviour is judged server-side too, from request timing alone, with no JavaScript and no fingerprint involved. The behavioural layer is usually discussed as client-side mouse and keyboard telemetry, which is why a real browser feels like a safe haven. But a server watching only the sequence and timing of your requests can still catch automation. A self-hosted open-source detector demonstrated this by flagging a genuine Chrome being driven by an LLM over the Chrome DevTools Protocol, with no client-side fingerprinting and no TLS interception in play. The tell was subtle: the automated session requested resources in an order and rhythm that did not match its own cache state, fetching things as though the cache were cold while it was actually warm. Real browsing has a characteristic shape, conditional requests, parallelism, think-time, asset ordering driven by the renderer, that a request sequencer reproduces imperfectly.

 
 
 Why it survives a perfect fingerprint
 Timing is orthogonal to identity
 TLS, JA4, canvas, and Client Hints describe what you are. Request timing describes how you behave over time, and the two are independent. You can present a flawless real-Chrome fingerprint and still emit a request rhythm no human produces. A patient detector does not even need to be certain on request one: it can lower confidence, watch the next few requests more closely, and converge, which is exactly what a behavioural memory model is built to do.
 
 
 
 What it means for your crawler
 Make the cache and the cadence honest
 Drive real navigations rather than firing a hand-built request list: let the browser fetch sub-resources in its natural order, honour caching so a warm session does not re-request what it already holds, and keep think-time and parallelism in a human range. The same logic that beats client behavioural ML, move like a person, applies one layer down at the request-sequence level. Server-side timing is cheap to run, needs no client cooperation, and is the layer a fingerprint patch never touches.
 

The 2026 direction of travel: continuous session-long behavioural validation. The behavioural checks above are mostly evaluated in bursts, at a challenge or on a scored request. The newer enterprise approach (Cloudflare shipped a productised version in mid-2026) drops that model and validates behaviour continuously across the whole session. A dynamically injected script quietly collects interaction signals as you use the site, cursor motion, scroll dynamics, typing cadence, clipboard actions, how long the page is actually visible, and streams them back to be scored in real time, with the verdict compounding as the session goes on. The design premise is precise: modern automation can execute JavaScript, run a real browser, and pass a single CAPTCHA without raising a flag, so a point-in-time check catches less and less. What stays hard is producing consistent human behaviour over an entire visit.

 
 
 Why bursts stop working
 You cannot reset a session by refreshing
 A per-request or per-challenge check is a door you pass once; a session-long model is a companion that never leaves. Because the score compounds with context across the visit, refreshing the page or navigating away does not wipe the behavioural signature, the session carries forward, so the cheap escape hatches (reload on block, spin a new page) stop helping. For automation this changes the target completely: it is no longer enough to look human for the one interaction being scored, the whole arc of the visit has to hold together, which is far more expensive to simulate and far less reliable to keep up at scale.
 
 
 
 Where synthetic behaviour cracks
 Human movement is not just "noisy"
 The common way to fake human input is to add Gaussian noise or uniform random delays to mouse paths and keystroke timing. That defeats a naive "is this too perfect" check but fails a model that knows the real texture of human motion, which has structure noise does not, the acceleration and correction profile of a real cursor, the rhythm of real typing. The other half is cross-signal coherence: does pointer activity line up with when the page was actually visible, is a text field genuinely focused during the typing events it claims. Random jitter satisfies none of those joint constraints. The defensive lesson mirrors the offensive one from the session-stickiness section, coherence across signals over time is the wall, and a bolt-on layer of randomness is exactly what it is built to catch.
 

Where this is heading, because AI is quietly reshaping both sides. Two shifts are worth internalising before the vendor section. First, the barrier to writing a bot has collapsed, and not mainly through vision or computer-use agents (those are roughly fifty times more expensive to run, so most abuse avoids them), but through AI-assisted coding: every frontier model was trained on the public corpus of anti-bot bypass code, so producing a one-shot automation script and then iterating "now make it evade detection" is trivial for anyone. The effect is a flood of low-effort automation and a higher floor on how polished the average bot looks. Second, the defensive response is to abandon the signals AI makes cheap to fake. Micro-behavioural checks like mouse-movement and typing-cadence analysis are being retired as primary signals precisely because they are noisy and now trivially synthesised, in favour of harder-to-phrase signals and two things a spoofed browser cannot easily supply. One is cross-signal deception detection: change your user agent to Safari while everything else says Chrome-on-Apple and the isolated manipulation is itself the flag, the same coherence principle this section is built on, turned into a primary defence. The other is context a fingerprint has no access to, application and business logic (the best bots run indistinguishable browsers but still beeline for the valuable flow in a way no human browses) and consortium or network reputation (a fingerprint seen misbehaving across many sites gets banned everywhere at once). The strategic reading for a scraper is that out-resembling the browser matters less every year, while behaving like a plausible participant in the specific application, and not reusing an identity already burned across a network, matters more.

The story so far: You now understand the full detection stack, TLS fingerprints at the network layer, JS interrogation in the browser, IP reputation checks per request, and ML behavioural analysis across the session, including the server-side request-timing variant that needs no client signal at all. The next sections show you exactly which anti-bot vendors use which combination of these layers, and the specific bypass strategies for each.

 20 field notes on detection
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Browser internals
 Why Chrome 136 Broke Attaching to Your Logged-In Profile
 If --remote-debugging-port silently stopped working against your everyday Chrome profile, this is why. From Chrome 136, both --remote-debugging-port and --remote-debugging-pipe are ignored unless you also pass --user-data-dir pointing at a non-standard directory. It is not a bug and there is no flag to restore the old behaviour. A non-default data directory gets a different encryption key, so the profile's cookies and saved passwords stay protected from anything that talks to the debug port.
 💡 The automation substrate you rely on became an exfiltration path, and it got closed
 Why Google did it. App-Bound Encryption on Windows made reading the cookie database off disk significantly harder, so the technique migrated: rather than decrypt the file, ask the browser. Google noted in March 2025 that since App-Bound Encryption shipped they had seen an increase in attackers using Chrome Remote Debugging to extract cookies. CDP running against an authenticated profile hands over live session state through ordinary API calls — Storage.getCookies returns the cookie jar without ever touching the encrypted store. The 136 change closes the convenient version of that: you can still enable CDP, but not against the profile that holds the sessions.What it means for your setup. The attach-to-my-real-logged-in-browser pattern is over on default profiles. Pair the flag with a throwaway --user-data-dir and you get a clean profile that you must authenticate into — which is the point. Plan for that in any workflow that assumed an existing session, and note it applies to Playwright and Puppeteer identically, because the restriction lives in Chrome, not the driver.If you run the endpoints being scraped. Researchers have since shown the restriction can be sidestepped by enabling CDP from inside an already-running process rather than via command line, which no browser flag can prevent. The published detection is host-level, not browser-level: Sysmon Event 8 (CreateRemoteThread) and Event 10 (ProcessAccess) targeting chrome.exe or msedge.exe, with access mask 0x143a the notable one. Worth handing to whoever owns endpoint detection, because a stolen live session walks straight past multi-factor authentication — the authentication already happened.
 
 +
 Aug 2026 / Measured
 Ten Bots Per Shopper, One Hit Every 6.5 Seconds: What a Scalper Feed Actually Looks Like
 On one retailer's DDR5 memory pages, 91% of traffic is now automated — roughly ten bot requests for every human visit, per DataDome's threat research (Jerome Segura), picked up by Tom's Hardware. In March, across a broader set of e-commerce sites, the ratio was nearer 6:1. But the headline number is the least interesting part.
 💡 The request pattern, not the percentage, is what marks it as a real-time inventory feed rather than scraping
 The pattern is the tell. In a one-hour sample, 91 listings were each polled about 551 times — one hit every 6.5 seconds per SKU — each request carrying cache-busting query parameters. That last detail is the whole story: a unique query string forces a miss at the CDN edge and pushes every single stock check through to origin. It is deliberate. This is not catalogue scraping that runs once and moves on; it is a real-time inventory monitor running on infrastructure the retailer pays for, watching for the instant a 32GB kit comes back in stock — kits whose price surged from $72 to $392 over the window, which is the economic engine underneath the traffic.Why it belongs in this guide, from both sides. If you build collection, this is the anti-pattern that gets a whole technique class blocked for everyone: cache-busting every request is maximally hostile to the target's economics, so it draws the hardest possible response and trains defenders to watch for per-SKU polling cadence. If you defend, the lesson is that a bot share alone tells you little — 551 polls at a fixed 6.5-second interval with cache-busters is a signature you can write a rule against, where a raw percentage is not. It pairs with the guide's cost material from the defender's chair: this traffic is expensive precisely because it is engineered to bypass the cache and hit origin every time.Source: Tom's Hardware, on DataDome research by Jerome Segura.
 
 +
 Aug 2026 / Teardown
 Obscura Against Akamai, and the Sentence That Settles the Fingerprint Argument
 The first real test of an agent-first engine against a hardened commercial target, run against a hotel chain's availability API behind Akamai Bot Manager. The engine performed well. The section heading that matters reads: the wall is the IP, not the fingerprint.
 💡 Requests that cleared Akamai returned 500 from the backend — a bug, not a block, which is how you know the fingerprint passed
 What it is. A headless engine written from scratch in Rust that embeds V8, ships no Chromium and no Node, and claims to run in about 30 MB of RAM. It exposes a CDP server so Playwright drives it unchanged, and carries a --stealth flag. On a local fingerprint probe it scored 94/100, against 70 for standard Chromium CDP tooling and 35 for vanilla Playwright and Selenium: navigator.webdriver false, a complete window.chrome with runtime and loadTimes, a spoofed NVIDIA GPU over WebGL.The architecture is the hybrid this guide keeps recommending. Use the browser only to mint the session, then replay with a fast HTTP client. Obscura produced valid Akamai cookies — _abck, bm_sv, bm_mi — and curl_cffi carried them to the availability endpoint for a 200. The session module retries up to six times with an eight-second settle between reloads, validating by calling the rooms endpoint and checking the cookies are actually present rather than merely set.Where the lightness costs you. Heavy fingerprinting suites never finish inside a minimal engine — boolean checks pass, but CreepJS and Pixelscan stall rather than completing. That has a real consequence: the Akamai sensor script sometimes ran incompletely, leaving _abck in its unsolved state, marked by a -1 in the second segment, which returns 403. Camoufox, a real Firefox, did not wobble. So the trade is explicit: a tenth of the memory, most of the fingerprint, and a sensor that occasionally does not finish.And then the finding that outranks all of it. From clean residential IPs the first load sometimes worked and sometimes needed a reload or two; the determinant was address reputation rather than fingerprint quality. The proof is the detail worth stealing: requests issued from inside the page context came back HTTP 500 — a backend error, not a bot block. A 500 means your traffic reached the application. That is a cleaner pass signal than any 200, because nobody serves you an accidental stack trace to trick you.Source: The Web Scraping Club, THE LAB #113.
 
 +
 Aug 2026 / Field note
 Every Header Correct, Blocked Six Times Out of Six
 Short and exact. A practitioner copied every header from the browser, precisely, and the request was refused six times running. The headers were never the problem. The refusal happened in the TLS handshake, before a single header was read.
 💡 Matching the connection, then sending the identical headers, was accepted immediately
 This is the shortest possible demonstration of the principle the guide keeps returning to, and it is worth keeping precisely because it is small. Copying headers is the most natural thing to try — it is visible in DevTools, it is easy to verify, and it feels like the thing that identifies you. But JA4 and the ClientHello are settled while the connection is still being negotiated, which is before the server has read one byte of what you so carefully copied. A perfect header set carried over a Python-shaped handshake is a perfect answer to a question nobody asked.The diagnostic that saves the week is to notice which layer refused you before choosing what to fix. If the same headers succeed once the transport changes, the headers were never the variable, and every hour spent tuning them was an hour spent proving something that was already true. The tell is exactly what happened here: identical request content, different transport, opposite outcome.Read this next to the earlier card on the block page naming a different vendor from the header. Both are the same failure of attribution — a confident fix applied to the wrong layer, or the wrong vendor — and between them they probably account for more wasted engineering hours than any technique in this guide accounts for saved ones.
 
 +
 Aug 2026 / Field note
 A Referer Is Required On One Endpoint and Fatal On the Next
 A worked Google Trends teardown with a detail worth more than the target it came from. Route requests through a proxy and you get USER_TYPE_EMBED_OVER_QUOTA almost immediately — but the interesting part is what happened after the TLS problem was solved.
 💡 Header rules are set per endpoint, by different people, and they contradict each other
 The first wall is below the headers. The load balancers inspect the TLS handshake before anything reads an HTTP header, so requests and httpx are dropped on their fingerprints regardless of how carefully the headers were assembled. The fix is the one this guide keeps arriving at: curl_cffi with impersonate="chrome", which is a transport change rather than a header change.The second wall is the useful one. Google runs different internal widget endpoints for timeline, heatmap and related-queries data, and their header expectations disagree: the timeline endpoint requires a referer, while sending any referer at all to the regional endpoints triggers a block. Stripping the header completely is what gets the regional data out. Reported result was near-total success with no headless browser anywhere in the path.Why this generalises past Google Trends. A per-site header profile is the wrong unit. Anti-bot configuration is applied per route by different teams at different times, so two endpoints on one host can hold opposite rules and a profile that satisfies both cannot exist. If a request works on one path and fails on the next with the same client, stop looking for a better global profile and start diffing what you sent to each. The same reasoning is why enumerating routes beats accepting the hardest one.
 
 +
 Aug 2026 / Reverse engineering
 Google Ships a Virtual Machine To Fingerprint Your First Visit
 Research into a strict first visit — no cookies, nothing in local storage, no history — to Google search. The answer lives in Search Guard, which serves an interstitial instead of results and settles into the SG_SS cookie. It is a virtual machine shipped with an encrypted program for it, and the fingerprint is assembled inside that machine.
 💡 The script reads deliberately absent names too — what you lack identifies you like what you have
 The methodology is the contribution, more than the finding. The stated approach is to work in layers from the cheapest thing you can try down to the most laborious, and the explicit criticism is that most write-ups jump straight to the bottom layer and make the work look like magic. The top layer answers a practical question without weeks of work: which properties and methods does this script actually read and call? That produces a full map of Browser API access — and it is enough to act on, long before anyone touches dispatch, opcodes, registers or the cipher.The detail worth carrying into your own work is that the challenge reads “every property, method and deliberately absent name” it cares about. Absence is a signal. A patched environment that adds a property real Chrome lacks, or leaves present something a real build removed, is identified by the gap rather than by the value — which is the coherence principle in its purest form and the reason adding realism keeps making things worse.On the ethics of the write-up, worth noting because it is a good pattern: the analysis runs offline against a capture you make yourself, so no vendor code is redistributed. That is a reasonable model for publishing this kind of work, and a better one than shipping somebody else's obfuscated bundle in a repository.
 
 +
 Aug 2026 / Below the sandbox
 ShaderGhost: A Supercookie That Lives in Your GPU Shader Cache
 Announced by Joe Rutkowski, who builds overpoweredJS, and billed as the tracking ID you cannot delete. The proof of concept writes a 32-bit random number into the shader cache and reads it back — from any site, across profile wipes. He puts the reach at 99% of browsers by market share: Safari, Chrome, Edge, Brave, on all platforms.
 💡 Storage you did not know was storage, with no clear button pointed at it
 The shader cache exists for a good reason. Compiling GPU shaders is expensive, so browsers keep compiled results on disk and reuse them, which is why a game or a WebGL-heavy page is slower the first time you load it. That cache is keyed on the shader source. Write a shader whose source encodes a value, and the presence or absence of a cached compilation for it becomes a readable bit. Repeat, and you have a number that persists in a place cookie policy was never written to cover.Why this belongs next to Frost and the WASM SIMD probes rather than with cookies. Every mitigation you have for tracking assumes the state lives somewhere the browser namespaces per origin and clears on request — cookies, localStorage, IndexedDB, the HTTP cache. This is a shared compilation artefact of the graphics stack. It is not partitioned by origin in the way storage is, which is what "accessible from literally every site" means, and there is no user-facing control pointed at it. The fix has to come from browser vendors partitioning or keying the cache differently, and that carries a real performance cost they will have to weigh, exactly as with disk-timing mitigation.For a scraper, invert it before you dismiss it as a privacy story. A persistent, cross-site, hard-to-clear identifier attached to a machine is precisely what a bot-management vendor wants most, because it survives the thing you rely on: a fresh profile. Everything in the fingerprint-replay economy assumes that a clean browser is a clean slate. If a hardware-adjacent cache carries an ID across your profile resets, then "new session" stops being true at the layer you cannot patch, and the answer is the same one that defeats SIMD probes and math fingerprints — separate real machines, not separate profiles on one.Worth pairing with a gap another practitioner named the same week: we have plenty of tools that patch a browser, and almost none that let you watch what an anti-bot script is actually reading from your system. Techniques like this arrive faster than the instrumentation to detect them being used. shaderghost.gg
 
 +
 Aug 2026 / Epistemics
 Does Fingerprint Randomisation Actually Work? Nobody Has Shown That It Does
 A question that quietly undermines an entire product category. Every anti-detect browser demonstrates the same thing: randomise a set of characteristics, watch the fingerprint hash change, conclude it works. The author's objection is that this proves almost nothing.
 💡 The question is not whether values change. It is whether the values that matter to the tracker change.
 Two problems, and the first is a missing baseline. Before you can evaluate randomisation you have to establish that an identical browser environment produces an identical observable fingerprint across different physical devices, verified against multiple independent fingerprinting tests. Without that you do not know which characteristics were actually distinguishing you, so obscuring a chosen subset is a guess dressed as a defence. Their experiments did show that identical virtual environments can produce identical fingerprints across different machines, and that selected characteristics can be randomised — and then showed the weakness plainly.The weakness is that a tracker gets to choose what it looks at. It can simply ignore the fields it knows are randomised and identify you on whatever stayed stable. Randomising the canvas hash is worthless if the identifying signal was the math library, the compilation cache, the TLS stack or the timing profile, none of which your anti-detect layer touched. Which is the same conclusion the guide reaches from the other direction: the coherent-story problem is not solved by making more fields noisy, because noise in a field the detector has written off is free for them and expensive for you.The sharpest line is methodological, and it generalises well beyond fingerprinting. Preventing identification and proving you have prevented identification are different problems, and the second has received remarkably little attention. Applied to your own stack, that is a request for a specific kind of evidence: not "my fingerprint changed between runs", which any tool can show you, but "sessions that should be unlinkable were not linked by the target", which almost nobody measures. If your only evidence that rotation works is that a test page shows different values, you have measured your tool rather than the detector. The fingerprint dilemma
 
 +
 Aug 2026 / Measured, uncomfortably
 Fifteen Stealth Engines, and the Detector That Said They All Worked
 A team running 15 stealth browser engines in production finally tested whether choosing the best one matters as much as the marketing implies. They put three head to head: ChromiumFish, a native fork that spoofs at the C++ engine layer rather than patching JavaScript, against patchright (JS-patched Chromium) and camoufox (patched Firefox).
 💡 All three cleared the demo detector. All three hard-403'd on the real deployment, at the same rate.
 On a free detector's core suite, all three passed nearly everything, with one real gap: both Chromium-based engines fail the WebGL check, because headless Chrome has no GPU context to report and native spoofing does not conjure one. Firefox-based camoufox does not have that problem. Otherwise a tie.Then the same three went against a live Cloudflare Bot Management deployment rather than a JS-challenge demo. All three had cleared the demo cleanly, rendering real content with no challenge page. All three hard-403'd on the real target, at an identical rate, regardless of which had the cleaner fingerprint an hour earlier. Attempting to fix it with a rotating proxy pool produced six attempts, four hard blocks, one dropped connection, and one that served real page content under a 403 status anyway. The sampled exit IPs were still datacenter and cloud infrastructure rather than residential, which is the actual reason nothing changed.Three things this pins down that the guide has argued and can now point at. First, a public detector measures whether you pass a public detector; it is not a proxy for a tuned commercial deployment, and the gap between the two is where most stealth-tool comparisons quietly live. Second, once the network layer is wrong, fingerprint quality stops being the variable, which is why the engine choice made no difference at all here. Third, and the part they said they did not expect to relearn so cleanly: maintaining fifteen separate fingerprint-spoofing engines is real, recurring engineering cost buying a difference that a datacenter IP erases completely. Fix the layer the block is happening on before you invest in the layer you enjoy working on.
 
 +
 Aug 2026 / Protocol layer
 The First Fingerprint Where Using a Proxy Is What Creates the Signal
 HTTP/3 runs over QUIC, which runs over UDP. Every proxy you own speaks TCP. The consequence is not a performance footnote: the moment you configure a proxy, Chrome negotiates HTTP/2 regardless of what the server supports, and drops into the protocol tier where the bots live.
 💡 Everywhere else a proxy hides you. Here it is the tell.
 The mechanics are dull and total. An HTTP proxy tunnels TCP only. SOCKS5 does technically define a UDP ASSOCIATE command, but browsers do not expose it, so Chrome is TCP-only over SOCKS5 in practice. There is no combination of a stock browser and a conventional proxy that produces an HTTP/3 request. Your fingerprint work at layers 1 and 2 can be flawless and you will still be announcing something before a single byte of TLS is compared.What makes it worth acting on is the trend, not the level. Cloudflare Radar's likely-bot share by protocol version: HTTP/1.1 at 73.75%, down from 82.07% a year earlier. HTTP/2 at 26.76%, up from around 21%. HTTP/3 at 3.33%, essentially unchanged. Read those three numbers together and the migration is visible: automated traffic is moving off HTTP/1.1 into HTTP/2, and almost none of it is arriving over HTTP/3. If that continues, protocol version stops being a weak prior and becomes a strong one, and the proxied browser is structurally parked on the wrong side of it.The honest options, none of them free. RFC 9298 defines UDP proxying over HTTP (CONNECT-UDP), which is the standards-track answer and needs your provider to implement it. A network-layer tunnel (a VPN rather than an HTTP proxy) carries UDP and therefore carries QUIC, at the cost of the per-request IP control that made proxies useful. Or wait for browsers to expose UDP proxying, which nobody should plan around. A small number of providers have started advertising HTTP/3 support; that claim is now worth testing rather than assuming, and it is easy to test, because the negotiated protocol is visible in your own response. Full writeup by Web Scraper
 
 +
 Aug 2026 / Debugging war story
 They Blamed the Proxies. It Was the Cookie They Added to Look Legitimate.
 An API started returning 429. The team did the obvious things: switched provider, switched pool, slowed the request rate. Still 429. Then someone tried the one thing nobody had considered — sending the request with no cookies at all. Consistent 200s.
 💡 Every piece of state you attach is also something that can be judged
 They had been attaching a session cookie so the request would look like a normal returning user. That was the mistake. A request with no cookies is anonymous — there is no history to hold against it. A request with a session cookie has an identity, and identities accumulate a reputation: request counts, past behaviour, a trust score. Theirs had already spent its budget. They thought they were adding credibility; they were adding a record.The inversion is worth stating plainly, because it is genuinely counter-intuitive against everything else in this guide: the authenticated-looking session was rate-limited while the anonymous one was not. Both behaviours are rational from the defender's side. Anonymous traffic gets a generic bucket. Identified traffic gets a per-identity budget, and a per-identity budget is a thing you can exhaust.How to use it without over-learning it. The lesson is not "never send cookies" — on a site that scores session continuity, dropping cookies is exactly how you get flagged. The lesson is that headers, cookies, tokens and fingerprints are all claims, and each claim is something that can be held against you as well as for you. So before scaling infrastructure to get around a limit, run the cheapest possible experiment: the same request with the state removed. If the anonymous request succeeds where the identified one fails, you were not being rate-limited by IP, you were telling the server exactly who to rate-limit. Add it to the block-triage checklist alongside "try a different TLS profile before you escalate the tool" — both are one-request experiments that save a week of the wrong work.
 
 +
 Aug 2026 / Read from the other side
 The Attacks That Are Hard to Stop Are the Ones That Look Like You
 A defender-side breakdown worth reading as a scraper, because it explains the shape of every rate limit you will ever hit. In 2018 GitHub absorbed a 1.35 Tbps memcached flood and was back in minutes — a colossal number that was easy, because nothing a real user does looks like that. The hard ones are small.
 💡 Defender cost is set by resemblance, not volume — and so is yours
 Four that sit on the difficult side of that line. Slowloris (2009): one machine, almost no bandwidth, opens many connections, sends partial headers and drips just enough to avoid the timeout until the connection pool is gone. Each one reads as a slow client on bad Wi-Fi. Mitigated with header/connection timeouts, per-IP connection caps, and a reverse proxy that buffers incomplete requests. Mēris (2021): roughly 250,000 compromised MikroTik routers, peaking at 21.8 million requests per second, every request syntactically valid, every source an ordinary network. Mitigation is behavioural baselines and IP reputation, not signatures. HTTP/2 Rapid Reset (CVE-2023-44487): open a stream, cancel it immediately with RST_STREAM, repeat — 398 million requests per second from about 20,000 machines, and every frame was valid HTTP/2 using a feature real browsers use. Mitigated by counting resets per connection and capping concurrent streams. CONTINUATION Flood (2024): unbounded HTTP/2 CONTINUATION frames without END_HEADERS, so the server buffers until it dies — and because the request never completes, it is never written to the access log. The server falls over with no evidence.Why a scraper should care. Every one of these forced defenders to move from cheap packet-level filters to expensive behavioural comparison against a baseline — CPU the attacker never had to spend. That is the same machinery that decides whether your crawler gets through, and it explains something that confuses people: why a modest, well-behaved scraper sometimes gets throttled harder than a loud one. Loud is cheap to classify. Ambiguous is expensive, and expensive traffic gets treated conservatively. It also explains protocol-level limits that look arbitrary — concurrent-stream caps, reset accounting, header-size ceilings. If your HTTP/2 client aggressively cancels requests, reuses connections unusually, or sends unusual header volumes, you are wearing a costume with a known bad reputation, entirely by accident. bunny.net's full breakdown
 
 +
 2026 shift / Browser vendors
 The Vendors That Wrote the Detection Rules Are Quietly Breaking Them
 Microsoft Edge now returns navigator.webdriver = false when an AI agent drives Playwright. Google patched out CDP detection in V8. Neither change was announced. The signals every anti-bot tool relied on to flag automation just became officially unreliable, because AI agents browsing on behalf of real users broke the human-vs-automation binary.
 💡 Detection has to move up the stack to behaviour, intent, and network identity
 For years the W3C spec required automated browsers to flag themselves via navigator.webdriver = true. That was the easiest detection signal in the industry, and most public anti-bot stacks were built around it (along with CDP-detection tricks for Chrome's DevTools Protocol). Two undocumented changes in 2026 have just made those signals soft: (1) Microsoft Edge returns navigator.webdriver = false when Playwright is driven by an AI agent on behalf of a user; (2) Google patched out the most common CDP-detection technique in V8. No release notes, no announcements. The reason is rational from the browser vendors' side: agentic browsing is now a legitimate use case (Anthropic Computer Use, OpenAI Operator, Browser Use, etc.) and the old binary doesn't apply. The implication for the guide and for production scrapers: any detection or bypass strategy that pivots on these flags needs to assume they are no longer reliable as either signal or counter-signal. Detection has to move up the stack, to behavioural ML, intent patterns, and network-identity layers (TLS JA4, IP reputation, WebRTC, DNS coherence), all of which are far harder to remove from the inside. DataDome's threat-research team published a longer breakdown if you want the technical specifics.
 
 +
 Detection vector / Hardware side-channel
 "Frost": Tracking Users via SSD Timing, Zero Permissions Required
 A new side-channel technique called Frost measures microscopic latencies in the SSD subsystem to fingerprint users and infer activity in other tabs. It requires no permissions, no APIs that prompt the user, and ad-blockers + incognito cannot mitigate it. Standard JavaScript on any page can read these timing variations.
 💡 The fingerprinting frontier is now below the JS sandbox layer
 Every active tab and every site generates a unique load pattern on the local disk subsystem. By timing common disk-touching operations through standard browser APIs, a script can probabilistically infer what other tabs the user has open and what they are doing in them, without ever requesting filesystem access. Not seen in the wild yet, but the technique class is what matters here: it joins WASM SIMD CPU probes and hyphenation-dictionary checks in a growing family of hardware-level fingerprinting that the browser sandbox does not stop. There is no direct fix until browser vendors add fuzzing or rounding to disk-operation timing, which has a real performance cost they have to weigh. For scraping: any stealth strategy that depends on JS-level patches stops working against this class of probe by design, because the signal is below the JS layer. The medium-term answer is the same one that defeats WASM SIMD probes: real hardware via real devices, not patched browsers in datacenters.
 
 +
 Detection vector / Timing attack
 Incognito Detection by Timing a Single Byte Write
 detectIncognito v1.7 (Nov 2026) integrates a side-channel that detects Chromium incognito mode by writing one byte to navigator.storage and timing the flush. Incognito routes storage to RAM, normal mode hits disk. RAM is faster. That is the entire vulnerability.
 💡 <0.1ms flush time = RAM = incognito, no API prompts needed
 The technique writes one byte three times to navigator.storage and measures the flush time. Under 0.1ms indicates RAM (incognito), above indicates disk (normal mode). No permission prompts, no API quirks the user can disable, runs in standard JavaScript on any page. Known caveats: RAM disks (used by some privacy-conscious users) trigger false positives unless the threshold is tuned (~0.01ms separates RAM-disk from incognito on test hardware); slow HDDs do not produce false positives because the technique detects suspiciously fast writes, not slow ones. For anti-bot: detecting incognito is a useful behavioural signal (legitimate buyers rarely shop in incognito; scrapers and abuse traffic over-index on it). For scraping: if your stealth stack uses incognito or per-session ephemeral storage to keep contexts clean, you are leaking a signal that is now trivially detectable. The fix is the same as for the broader timing-attack class: use persistent profile directories that hit real disk, accept the cookie/storage management overhead.
 
 +
 Detection / OS fingerprinting
 The Detection Vector Nobody Patches: Hyphenation Dictionaries
 Chromium on Windows and Linux requires a hyphenation dictionary to be bundled at build time. Most custom Chromium forks ship without one. The result: many stealth browsers literally cannot hyphenate words — a signal anti-bots can probe via hyphens: auto CSS and measure rendered output.
 💡 Camoufox, PatchRight, undetected forks — check yours with the PoC
 When CSS hyphens: auto is set and text overflows a container, the browser inserts soft hyphens at language-specific break points (so "hyphenation" becomes "hy-phen-ation"). The dictionary that drives this is OS-level on Android and macOS, but Chromium on Windows and Linux must bundle it at build time. Most people forking Chromium don't know this — the build artifact is large and the feature is invisible until you specifically test it. Joe (joe12387) demonstrated this is a detection vector: anti-bot scripts can render a known word in a known-width container with hyphens: auto, screenshot via Canvas, and compare the hyphenation positions against expected values for the claimed OS. A custom Chromium fork that fails to hyphenate at all (or hyphenates wrong) reveals itself instantly. Mitigation: ensure your build includes the hyphenation dictionary for the languages you claim to support, or run real Chromium binaries (not forks) under XVFB instead. Live PoC · github.com/joe12387
 
 +
 Debugging
 Your Scraper Is Blocked Because of Behaviour, Not Code
 Identical headers. Machine-speed intervals. No session state. Datacenter IPs. Fix: rotate headers, random.uniform(1.8, 4.3) delays, requests.Session(), residential proxies.
 💡 sleep(random.uniform(1.8, 4.3)) beats sleep(2) every time
 The signals that get you blocked, in order of detection speed: TLS fingerprint (detected before first HTTP byte), HTTP/2 SETTINGS frames (detected at connection), Request headers (User-Agent, Accept-Language, Sec-CH-UA, checked immediately), Request timing (identical intervals are machine-like), Session patterns (no cookies accumulated, no referrer chain), IP reputation (ASN, datacenter range). Fix each layer: curl_cffi for TLS, full Chrome headers via httpx or curl_cffi, random.uniform(1.8, 4.3) delays, requests.Session() for cookie accumulation, residential/mobile proxies for IP. Check your current fingerprint at tls.browserleaks.com/json.
 
 +
 Detection / WASM
 The Detection Layer Your Stealth Browser Cannot Patch
 WebAssembly SIMD probes the actual CPU. WebAssembly shared memory gives anti-bots a 17× higher-resolution timer than performance.now(). Both run below the JS hooks Camoufox, CloakBrowser, and PatchRight patch.
 💡 The arms race of better JS patches has a ceiling. WASM fingerprinting is past it.
 
The DataDome engineering team published in May 2026 a method for fingerprinting CPUs from the browser using WebAssembly SIMD. Vector operations on 128-bit registers map directly to CPU instructions (NEON on ARM, SSE/AVX on x86), and their timing reveals the actual silicon underneath, not whatever the browser claims.

The enabling primitive arrived in 2024 from Manuel at brokenbrowser.com: a one-liner that gets you a real SharedArrayBuffer on any page, no special headers, by calling new WebAssembly.Memory({shared:true}).buffer. Drive a MessageChannel ping-pong with Atomics.add() inside it and you have a counter ticking at 100,000 Hz, micro-timing precision around 6 microseconds. Chrome marked it Won't Fix.

What this defeats:
× Camoufox (Firefox C++ patches at the browser layer)
× CloakBrowser (49 Chromium binary patches)
× PatchRight, undetected-chromedriver, Nodriver, Pydoll
× Every JS prototype patch (Function.toString detection is irrelevant when nothing JS is touched)

What still works: real hardware diversity. The future of stealth scraping is real consumer machines on real ISP IPs, which is essentially what high-quality residential proxy networks like Massive, Bright Data, and Oxylabs already provide. As detection moves into the CPU layer, the value of actually being real compounds.

This is also why akamai-v3-sensor works on Akamai v3: it never executes the WASM at all because it never reaches sensor.js. By bypassing at the TLS layer, you skip every detection layer above it.

Sources: Anthony Manikhouth (DataDome) and Manuel (brokenbrowser.com).
 
 
 +
 Persistence / Evercookie tradition
 The Cookie That Refuses to Die
 Anti-bots increasingly persist tracking state across cookie clears using a chain of fallback storage: localStorage, IndexedDB, Service Workers, Cache API, FileSystem. Clear one, the others restore it. The 2010 Evercookie idea is back, in 2026 form.
 💡 If clearing cookies does not reset your session, the detection layer is not in the cookie
 
The unclearable-cookie repo demonstrates the modern version of Samy Kamkar's 2010 Evercookie technique: when a user clears cookies in DevTools, the cookie immediately respawns from a copy held in localStorage, IndexedDB, or a Service Worker cache. The visual is hypnotic, you delete it, refresh, it is back.

Why this matters for scrapers: if you are rotating cookies between requests to look like a fresh visitor, but the target site is reading your localStorage entry from the previous session, your rotation does nothing. Anti-bot vendors like Forter and Riskified have shipped variants of this for years. Cloudflare's cf_clearance cookie now has localStorage backup in some configurations.

Storage layers a real reset has to clear:
✓ Cookies (HTTP and JS)
✓ localStorage and sessionStorage
✓ IndexedDB (every database)
✓ Service Worker registrations and Cache API entries
✓ FileSystem API (legacy but still works)
✓ Web SQL (deprecated but persists on older Chromium)
✓ ETag / If-Modified-Since headers cached at HTTP layer
✓ HSTS pin database (yes, browsing data can be encoded in HSTS pins, this is real)

Practical implication for scrapers: when you rotate sessions, do not just clear cookies. Either spin up a fresh browser profile each session (Playwright context.close() + new context, or a fresh Camoufox BrowserContext), or run in an entirely isolated container. Half-measures leak state.

For the curious: the original Evercookie by Samy Kamkar in 2010 used 13 storage mechanisms. Modern browsers have removed several (Flash LSO, Silverlight, Java applets), but added more (Service Workers, BroadcastChannel, OPFS). The trick is alive and well, just modernised.
 
 
 +
 Research · Castle Intelligence · April 2026
 Your Anti-Bot Fingerprint Is Probably for Sale Right Now
 Castle analysed 811 bot-adjacent sites and found browser fingerprints being collected, packaged, and sold as operational assets. 12.5% deployed fingerprinting scripts consistent with harvesting. Services openly advertise "comprehensive TLS, HTTP/2, and JavaScript fingerprint collection."
 💡 Detection systems must assume replay. Client-side signals are untrusted input, not proof of identity.
 
Castle Research (Antoine Vastel), April 2026 analysed 811 bot-adjacent websites including proxy providers, CAPTCHA farms, engagement manipulation services, and sneaker bots. The findings document a structured, commercialised layer of the bot ecosystem that most scraping engineers have not thought about.

What they found:

12.5% of analysed sites deployed fingerprinting-related scripts consistent with harvesting. A subset replicated vendor-specific telemetry from PerimeterX, Incapsula, Akamai, Adyen, and hCaptcha, not to defend themselves, but to collect and replay the same signals against those vendors.

The mechanics:

Services like impersonate[.]pro openly advertise "comprehensive TLS, HTTP/2, HTTP/3, and JavaScript fingerprint collection." In Discord and Telegram communities, bot developers discuss embedding custom JavaScript on real websites specifically to harvest fingerprints from genuine visitors. The goal: build inventories of authentic device profiles that can be injected into automated sessions.

The PerfectCanvas mechanism from Bablosoft is the clearest example. Their documentation describes exactly the pattern:
• Render canvas on a real remote machine with a real GPU
• Send the canvas output to the automation server
• Inject it into the headless browser's response to the canvas probe

This is the harvesting-and-replay model made explicit. Instead of spoofing canvas values (detectable via inconsistency), you replay values from a real Mac. The fingerprint is genuine. It just came from a different device.

Genesis Marketplace established the precedent: ~323,000 compromised browser environments for sale, each bundled with a real device fingerprint and a custom Chromium extension that injected the victim's browser profile into attacker sessions. F5 Labs and Europol both documented this. Castle's report shows the same approach is now commercialised at scale for bot traffic, not just account takeover.

What this means for scrapers:

The arms race has a new dimension. Anti-bots are scoring fingerprints. Bot services are buying real fingerprints to replay. Defenders are now building for replay conditions, not just spoofing conditions. This is why:

• Canvas/WebGL probes are increasingly paired with behavioural and timing signals (harder to replay than static values)
• WASM SIMD CPU probes (above) are valuable precisely because they are harder to harvest and replay than JS-layer fingerprints
• Anti-bots are introducing controlled variability in their own client-side scripts so that even valid payloads can't be reverse-engineered and replayed reliably

The implication for this guide: when a stealth browser passes the canvas probe, it may not be because it spoofed the hash well. It may be because it replayed a real hash that was never flagged. The distinction matters because vendors will move toward replay-resistant probes, making the harvest-and-replay model progressively harder. WASM SIMD (which requires real hardware timing) is an early example of a replay-resistant signal.

Source: Fingerprint Harvesting in the Bot Ecosystem, Castle Research, Antoine Vastel, April 2026.

---

## ANTI-BOT VENDORS — Cloudflare, Akamai, DataDome, Kasada, PerimeterX, F5 Shape

03 The vendors
Six companies built the walls.Here's every key.
Each vendor applies the detection layers differently, different weights, different signals, different architectures. What bypasses Cloudflare has zero effect on Kasada. You need to know exactly which wall you're facing before you choose a tool.

 
 Step 0, Before anything else
 Identify which anti-bot you're facing
 Wrong strategy on the wrong vendor wastes hours. Before writing a single line of code, spend 30 seconds identifying exactly what's protecting the target.

 
A one-line orientation to how the big four fail differently, because the fix for one can hurt you on another. Before diving into any single vendor, it helps to hold a rough map of where each one concentrates its scrutiny, since a change that unblocks one can flag you on the next. In broad, practitioner-reported terms: Cloudflare failures are often an IP-reputation and TLS-fingerprint problem, so the first moves are a cleaner network and a coherent handshake. DataDome is frequently your header set rather than your JavaScript, which is why the lean-header lesson above bites hardest here. PerimeterX (HUMAN) leans heavily on behavioural signals, so a perfect static fingerprint still fails without human-like interaction over time. And Akamai is deep TLS and HTTP/2 fingerprinting, where pseudo-header order and JA4-level detail decide the outcome. These are tendencies, not laws, and every vendor blends all the layers, but knowing each one's centre of gravity keeps you from spending an afternoon hardening the layer that particular wall was never watching.

 
 
 1
 Wappalyzer Chrome Extension
 Install free ↗
 
 Visit the target site, click the Wappalyzer icon in your toolbar. It instantly shows all detected technologies, including the anti-bot vendor. Shows Akamai, Cloudflare, DataDome, PerimeterX, Kasada and more with a single click.
 

 
 
 2
 Check response cookies
 
 
 _abckAkamai
 cf_clearanceCloudflare
 datadomeDataDome
 _px3PerimeterX
 x-kpsdk-ctKasada
 _fs_ch_st_Fastly
 reese84F5 Shape
 dd_cookie_testDataDome
 bm_szAkamai
 
 Open DevTools → Application → Cookies. Match any cookie name to identify the vendor. Multiple vendors can run on the same site. For CLI scanning at scale: wafw00f https://target.com identifies WAF + anti-bot vendor in one command.
 

 
 
 3
 Check response headers
 
 DevTools → Network → any request → Response Headers. Look for x-datadome, server: cloudflare, x-akamai-request-idor challenge redirect URLs containing vendor names.
 
 

 
 
 🔍 Wappalyzer
 
 Free Chrome + Firefox extension. One click on any site shows:
 
 Anti-bot / security vendor
 CDN provider
 CMS, framework, analytics
 Server technology
 
 
 Install Wappalyzer Free ↗
 
 
 Firefox version ↗
 
 
 Also useful
 wappalyzer.com ↗
 builtwith.com ↗
 whatcms.org ↗
 wafw00f (CLI) ↗
 WhatWaf (CLI) ↗
 
 
 
 

 
 
 01/06 · Airlines · Banks · ~30% Fortune 500
 Akamai
 Bot Manager v3+ injects sensor.js (~512KB, fully obfuscated) into every protected page. Unlike Cloudflare which checks at CDN edge, Akamai runs its full fingerprint suite inside your browser via this script. It collects 500+ signals over multiple requests, trust accumulates across the session, not just on the first hit. The critical 2026 signal: 60 chrome-extension:// URL probes. Zero passing = instant bot score regardless of all other signals. JA4+ is checked at EdgeWorker before HTML is served.
 
 _abck cookie
 bm_sz
 60 ext probes
 Battery API
 Multi-req scoring
 
 
 
 Bypass strategy
 Step 1: Check for GraphQL/XHR API first, a direct endpoint bypasses HTML anti-bot entirely
 curl_cffi impersonate="chrome124" handles TLS + HTTP/2 layer
 CloakBrowser with 49 C++ patches handles sensor.js interrogation
 Load Bitwarden + 1Password extensions to pass 60 extension probes
 ISP/static residential proxy, never rotate mid-session (trust accumulates)
 Homepage warm-up → 2–3s human dwell → scroll → navigate to target
 
 
 Script size~512KBRe-obfuscated per rotation
 Ext probes60Zero passing = instant block
 Fortune 500~30%Retail, airlines, finance
 ScoringMulti-reqTrust builds across session
 

 
 
 02/06 · 20% of all internet traffic · 200+ countries
 Cloudflare
 Cloudflare's uniqueness is infrastructure-level deployment. JA4 is computed in a Rust crate running on every Cloudflare edge node, your request is fingerprinted before it reaches any application server. The ML bot score (1–99) is trained on Cloudflare's view of 20% of all internet traffic, giving it an unmatched baseline for what "real" browsers look like. Turnstile (their CAPTCHA replacement) submits a 79-parameter POST including Canvas hash, font measurements, SHA-256 proof-of-work, and TEA-encrypted timing data.
 
 cf_clearance
 __cf_bm
 JA4 Rust edge
 Turnstile 79 params
 ML score 1–99
 
 
 
 Bypass strategy
 Origin IP bypass: check SecurityTrails DNS history, many sites had Cloudflare added later, origin IP is in old A records
 Camoufox with geoip=True, 100% pass rate Mar 2026 on Instagram, Reddit, X, LinkedIn (FF135 base; stable moved to FF146 on 16 Jul 2026 — re-test)
 Scrapling's StealthyFetcher solves Turnstile natively and automatically
 Turnstile HTTP bypass possible: solve the PoW + Canvas hash without a browser in ~0.27s
 Camoufox uses Juggler (not CDP), zero CDP timing artifacts that Cloudflare's ML scores heavily
 
 
 Web coverage20%All internet traffic
 Turnstile params79Canvas + PoW + TEA crypto
 Camoufox100%Pass rate Mar 2026 · FF135
 ML trainingGlobal20% of all traffic
 

 
 
 03/06 · 5 trillion signals/day · 1,200+ clients
 DataDome
 DataDome's architecture is fundamentally different from the others: it deploys 85,000 separate ML modelsone per protected site. There is no universal bypass. What works on Grainger.com may not work on Le Monde. It runs at the application server level (not CDN), so origin IP bypass is impossible. The WASM boring_challenge is a Rust-compiled state machine that cannot be emulatedit requires actual browser execution to produce valid tokens. IP reputation alone accounts for 25–30% of the total trust score.
 
 datadome cookie
 WASM boring_challenge
 Picasso device FP
 35+ behavioural
 85K per-site models
 
 
 
 Confirmed bypass, Grainger.com ✓
 Always try first: find __NEXT_DATA__ in HTML source, Grainger had 110KB of product data in it, bypassing DataDome entirely
 curl_cffi chrome124 + residential proxy → confirmed 200 OK (Grainger.com)
 Mobile carrier IP (T-Mobile, Vodafone 4G), highest trust score, hardest to flag
 Camoufox + geoip=Truealigns all 5 identity vectors with proxy exit country
 2ms real-time response means every request is independently scored
 
 
 ML models85,000One per protected site
 Response2msReal-time, app server
 IP weight25–30%Of total trust score
 Universal bypassNonePer-site models
 

 
 
 04/06 · HUMAN Security · 3 billion devices
 PerimeterX
 After merging with HUMAN Security, PerimeterX gained the most powerful network effect in anti-bot. It verifies 15 trillion interactions per week across 3 billion devices. The critical risk: get detected on any one of 29,650+ protected sites and your fingerprint is flagged across the entire network. Nike, Walmart, Zillow, StubHub all share reputation data. Its 5-vector unified score (TLS + IP + HTTP headers + JS fingerprint + Behaviour) requires all five to pass simultaneously, fixing only one vector has zero effect.
 
 _px3 cookie
 _pxde cookie
 5-vector score
 29,650 site network
 Human Challenge
 
 
 
 Bypass strategy
 All 5 vectors must pass simultaneouslyCamoufox + residential proxy addresses all of them
 Generate a fresh fingerprint per session, never reuse fingerprints across different target domains
 SeleniumWire can intercept the _px3 token generation flow for token replay
 Scrapfly's ASP flag handles all 5 layers automatically at managed API level
 Never use burned IPs, the network effect means cross-site reputation
 
 
 Sites29,650+Nike, Walmart, Zillow
 Weekly verif.15T3B devices/month
 Vectors5/5All must pass
 Network effectGlobalReputation shared
 

 
 
 05/06 · No CAPTCHA · Gatekeeper proxy architecture
 Kasada
 Kasada operates as a gatekeeper proxyevery request flows through it before reaching origin. Its JavaScript (ips.jsrenamed polymorphically each deployment) issues proof-of-work challenges that require real CPU cycles and browser APIs to solve. There are no CAPTCHAs, failures are silent 403s or 429s with no explanation. The critical 2026 fact: Kasada specifically fingerprints playwright-stealth by calling Function.prototype.toString() on patched native functions. The patch signatures are catalogued.
 
 x-kpsdk-ct
 x-kpsdk-cd
 ips.js PoW
 polymorphic JS
 toString() inspection
 
 
 
 Bypass strategy
 Never use playwright-stealthKasada has its toString() signatures catalogued and blocks it outright
 PatchRight patches at Python source level, nothing in the JS runtime to inspect via toString()
 SeleniumBase UC mode, removes webdriver flag and auto-handles PoW challenges
 Residential proxy essential, datacenter IPs receive near-zero trust regardless of browser
 PoW tokens are single-use, never replay, always generate fresh per request
 
 
 Block styleSilent403 no explanation
 playwright-stealthDetectedCatalogued signatures
 ChallengeJS PoWReal CPU required
 JS filePolymorphicRenamed each deploy
 

 
 
 06/06 · $1 billion acquisition · Most sophisticated
 F5 Shape
 F5 acquired Shape Security for $1 billion in 2020and the price reflects what they built. Shape runs a custom JavaScript virtual machine. The bytecode that executes in the browser is not standard JavaScript, it's a proprietary instruction set that you cannot reverse-engineer with standard tooling. Session tokens expire in minutes. The challenge payload is re-generated with every rotation. For production scraping at scale, DIY bypass is economically irrational, the engineering cost of maintaining a bypass exceeds the cost of Bright Data's API within weeks.
 
 reese84 cookie
 TS cookie
 custom JS VM
 minute-cadence rotation
 $rsc= params
 
 
 
 Bypass strategy
 First: check if mobile app uses a weaker backend, Shape is often only on the web frontend
 Only reliable option at scale: Bright Data (98.44%) or Zyte (93.14%) managed APIs
 DIY reverse engineering: deobfuscate VM bytecode, takes weeks per rotation
 Cost-justify: >2 days/month of maintenance time → managed API is cheaper
 The custom VM produces tokens that can be replayed for a few minutes, session pooling can reduce API costs
 
 
 Acquisition$1BF5 Networks 2020
 Token expiryMinutesTight rotation cadence
 VM typeCustomProprietary bytecode
 DIY viabilityNoneUse managed API
 
 
 
 
 

 
 
 
 Forter
 Fraud / Behavioural
 
 Focuses on behavioural analysis and device fingerprinting for fraud prevention. Monitors checkout speed, typing rhythm, and device profile. Common on e-commerce checkouts. Bypass: headless browser with randomised timings, diverse residential proxy pool, replay real user interaction sequences.
 BehaviouralDevice FPCheckout fraud
 
 
 
 
 Riskified
 Fraud / Behavioural
 
 Monitors shopping and payment behaviour alongside device fingerprinting. Flags anomalies in purchase flow, typing patterns, and system details. Bypass: Playwright Stealth with realistic interaction replay, residential proxies, maintain full session cookies across the purchase flow.
 BehaviouralDevice FPPayment flows
 
 
 
 
 Imperva Incapsula
 WAF · IP reputation · JS challenges
 
 Enterprise WAF used by Fortune 500 financial and government sites. Focuses on IP reputation databases + JavaScript challenges + behavioural analysis. Less aggressive than DataDome on TLS but harsh on flagged IPs. Bypass: residential proxies (datacenter IPs nuked instantly), Camoufox or fortified browser, slow request pacing.
 IP reputationJS challengeEnterprise/finance
 
 
 
 
 AWS WAF
 Cloud-native · Bot Control · Captcha
 
 Amazon's managed WAF with Bot Control add-on. Three protection levels: Common (signature-based), Targeted (behaviour + JS challenge), Custom rules. Used by AWS-hosted apps. Bypass: rotate residential IPs (Common tier blocks AWS IPs themselves), browser automation for Targeted tier, request rate ≤ 5/sec to avoid trigger thresholds.
 AWS-nativeBot ControlCAPTCHA
 

 
 
 
 Anubis
 Open-source · PoW · Anti-AI scraper
 
 Self-hosted Web AI Firewall (15k+ stars on GitHub, MIT licensed, written in Go by Xe Iaso/TecharoHQ). Sits as a reverse proxy and issues JavaScript proof-of-work challenges before serving requests. Built specifically against AI scrapers that ignore robots.txt. Used by Codeberg, FFmpeg, the Linux kernel source, Sourcehut, and most non-Cloudflare FOSS projects. Recognisable by its anime "Anubis" mascot illustration during the challenge. Bypass: headless Chromium with JS enabled (it'll solve the PoW naturally, just slower), or persist the verification cookie across requests. Codeberg confirmed in mid-2025 that AI scrapers already learned to solve Anubis challenges, so it slows scraping but doesn't stop a determined operator.
 Proof-of-workSelf-hostedAI-targetedFOSS
 

 
 
 
 Fastly Bot Management
 CDN-native · JS Proof-of-Work · Next-Gen WAF
 
 Fastly runs bot detection at its CDN edge, layered on the Next-Gen WAF (the former Signal Sciences engine). It does the now-standard stack, JA3/JA4 TLS fingerprinting and HTTP header-order analysis before any HTML, IP reputation against datacenter and proxy ranges, and behavioural scoring, but its defining mechanic is the dynamic client challenge. A non-interactive JavaScript Proof-of-Work challenge tests that the client is a real JS-executing browser, escalating to an interactive CAPTCHA only on suspicion, and it can also issue Private Access Tokens. The tell is the cookie pair issued from the customer's own domain: _fs_ch_st_* marks a challenge starting and _fs_ch_cp_* marks it solved, with _fs_cd_cp_* appearing when advanced client-side detection is enabled. A solved challenge yields a token cookie (default one hour) that later requests must carry. Because the challenge is a JS PoW rather than a heavy obfuscated VM, a real JS-executing browser engine clears it where a plain HTTP client cannot, which puts it closer to Anubis in difficulty than to Kasada. The usual caveat holds: identify it from the _fs_ch_* cookies first, then match a real browser stack end to end (TLS, header order, and JS execution) rather than reaching for a heavier tool than the challenge needs.
 _fs_ch_st__fs_ch_cp_JS PoWJA3/JA4Header orderPAT
 

Quick identification reference#

What you seeAnti-botKey cookie/headerDetection method

"Pardon Our Interruption" pageAkamai block_abckWappalyzer · response body
CF-Ray header · Turnstile iframeCloudflare challengecf_clearanceResponse header CF-Ray
JSON with datadome keyDataDome blockdatadomeResponse header x-datadome
_px3 or _pxde setPerimeterX block_px3Cookie inspection
Silent 403 · no bodyKasada silentx-kpsdk-ctResponse headers · ips.js in source
reese84 or TS cookieF5 Shape blockreese84Cookie names · Shape JS reference
Anime mascot "weighing your soul" pageAnubis challengetecharo.lol-anubis-authJS PoW challenge · Anubis HTML title
302 redirect to a virtual waiting roomQueue-It queueQueue-it token cookieX-Queueit-Connector header · queue-it.net redirect

The through-line: Every anti-bot vendor is defending against the same thing, automated access that looks like a machine. The difference is which layer they weight most heavily. Akamai weights browser execution (sensor.js). Cloudflare weights TLS + global ML. DataDome weights per-site behaviour + IP. PerimeterX weights the network effect. Kasada weights PoW + JS integrity. F5 Shape weights token validity via a proprietary VM. The tools in the next section exist as direct countermeasures to each of these specific approaches.

 9 field notes on the vendors
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Anti-bot internals
 Kasada Is Not Obfuscated JavaScript Any More. It Is a Virtual Machine.
 A researcher pointed an agentic AI at Kasada on nike.com and published the inventory rather than a bypass, because there is no longer any readable logic to pretty-print. Kasada now ships a custom VM plus a bytecode blob: beautify it and you get an interpreter loop, not a program. The measured shape of SDK j-1.2.726: p.js is 163,120 bytes around a 136,736-character string blob, decoded by a variable-radix varint with a 72-character alphabet at radix 48; ips.js is 655,603 bytes around a 603,415-character blob, alphabet 71, radix 46, and wrapped in a wall-clock time lock.
 💡 The load chain hides where KPSDK is actually born, and that matters if you are replaying
 The chain, in order. The page loads p.js, which fires an mfc request and creates two iframe navigations to an fp endpoint. The fp response is a nine-line HTML bootstrap, and it is that bootstrap, not p.js, that creates window.KPSDK — pinning KPSDK.now to performance.now bound to performance with Date.now as fallback, stamping KPSDK.start immediately, then posting to the parent. Only then does ips.js load, carrying three query parameters: KP_UIDz, x-kpsdk-v (the SDK version, in cleartext) and x-kpsdk-im.Four layers, and only one is worth your time. p.js is the loader plus integrity checks. ips.js is the fingerprinting program. Then the transport, then the server. The researcher went for ips.js and ignored the rest.The methodological line worth stealing. He graded every finding by evidence strength, because a fingerprinting inventory built from decoded strings is mostly wrong: “A string sitting in a table hasn't been read by anything.” Finding navigator.hardwareConcurrency in a decoded string table proves the string exists, not that the program reads it. Distinguishing the two is the entire difficulty of the exercise, and it is the same mistake people make when they grep a bundle and announce what a vendor checks.
 
 +
 Aug 2026 / Sector data
 Fashion Runs the Hardest Doors on the Web, and Almost No CAPTCHAs
 Not banking. Not gambling. Fashion is the single hardest industry for a machine to read — averaging 2.86 out of five on access complexity against a research-wide mean of 1.58, and topping all eighteen sub-sectors of retail and e-commerce. The interesting part is which controls it uses, and which it refuses.
 💡 The defences moved to where a shopper never looks: the connection, the rate, the pattern
 What is on, and what is deliberately off. Rate limiting runs on 54% of fashion sites, the highest of any industry measured. JavaScript is required on 49%. TLS fingerprinting — screening you at the moment you connect, before a byte of HTML — sits at 32%, beaten only by jewellery and luxury. A WAF runs on 91%, though most arrived bundled with a CDN and do little alone. And then the one that explains the strategy: CAPTCHA appears on only 15%, below the cross-industry average. That restraint is commercial, not technical — a CAPTCHA in a checkout flow costs sales. So the industry moved its defences to the places a customer never encounters and a collector always does.Layering is the norm, not the exception. Most fashion sites run three or more barriers at once, sitting at different levels: the connection, the request rate, the rendering, the behaviour. Any one of them is minor. Together they turn a fetch into sustained engineering, which is exactly what pushes the sector up the scale. Roughly a third of fashion sites still resolve as Easy — but more than half land at Moderate or above and better than one in four reach the tiers that need residential proxies and heavier infrastructure.The split inside the sector is the useful bit. At the premium and heritage end the defences are quiet: an enterprise bot-management platform in the background, no CAPTCHA to interrupt the shopper, and a block returned on the very first request rather than a polite request to slow down. High-volume fast fashion runs lighter, leaning on basic rate limiting. The rule underneath: the closer a brand sits to scarcity and price protection, the heavier and quieter its controls. Much of the machinery now standard across the sector was proven on limited sneaker drops, where a shoe sells out in seconds and resells for multiples — queues, waiting rooms and behavioural detection were built to hold off automated buyers, then generalised.The figure that says where the anxiety actually is. Only 4% of fashion sites name an AI crawler to block in robots.txt, a fraction of the rate in news and publishing, and about 69% publish a robots.txt at all — with most of the attention still on Googlebot rather than GPTBot. A newspaper worries about a model trained on its archive. A fashion retailer is largely indifferent to whether a chatbot read its About page, because its concern is commercial data: prices, ranges, stock levels. The industry that lives on reading demand signals understands exactly what its own signals are worth.And the operational warning, which corrects a reasonable assumption. On these sites full access is required from the very first request, and the rate limiting responds to client identity rather than speed — so slowing down will not help you. That is the opposite of the 429 case elsewhere in this feed, and telling the two apart before you start tuning is the whole game.
 
 +
 Aug 2026 / Diagnosis
 The Cloudflare Header Does Not Tell You Who Blocked You
 A short post carrying a genuinely expensive lesson. A practitioner took a 403, read the header, and started debugging Cloudflare. The block page named a different company entirely — DataDome one time, PerimeterX another. Cloudflare was the front door and never made the decision.
 💡 The header names the door. The body names the decision.
 This is the most common wasted week in the field, and it is wasted before any technique is chosen. A CDN in front of an origin will stamp its own headers on a response whose refusal was made by a vendor sitting behind it, and modern stacks routinely run two or three defences at once — this guide has documented single properties loading Akamai, PerimeterX and reCAPTCHA Enterprise simultaneously. If you tune your TLS profile against the wrong one, everything you learn is true and useless.The check costs one request. Read the response body, not just the status and headers: block pages name their vendor, in visible text or in a script path or a support link. Then look at the cookies actually set on the failure — _abck and bm_sz mean Akamai, datadome means DataDome, cf_clearance means Cloudflare made the call, reese84 means Incapsula. The cookie jar is harder to misread than the header, because it is written by whichever system actually ran.The general form is a principle already in this guide, applied one level up: the block is at a layer you are not working on, and here it is at a vendor you are not working on. Before choosing a tool, establish which system refused you — and treat the CDN name in the header as the least reliable evidence available, because it is the one thing guaranteed to be there whether or not it was involved in the decision.
 
 +
 Aug 2026 / Detection archaeology
 The 100%-Precise Turnstile Check Was a Chrome Bug, and Google Just Fixed It
 Cloudflare had a Turnstile check that caught every mainstream automation framework with no false positives. Puppeteer, Playwright, Selenium, all of them. It was not clever heuristics or behavioural scoring. It was a coordinate bug in Chrome, open since 2023.
 💡 The sharpest detections are vendor bugs, which means they expire
 The mechanism is beautifully small. Turnstile renders inside an iframe. When a real person clicks, the reported mouse coordinates are relative to the main frame, so inside a widget sitting partway down a page they land in the hundreds. When the click is dispatched through the Chrome DevTools Protocol, the coordinates come out relative to the iframe instead, so they are under 100. One comparison, no machine learning, no false positives, and every CDP-driven browser failed it identically because they all go through the same code path.Google fixed the underlying bug in September 2025, years after it was first reported. Once the fix is broadly deployed, the check is dead.Three things worth taking from this. First, the highest-precision detection in a vendor's arsenal is often not a model, it is a discrepancy, and discrepancies get patched by someone with no stake in the detection. Second, this is the same story as browser vendors quietly softening navigator.webdriver and CDP detection: the detection surface is owned by the browser vendor, not the anti-bot vendor, and the browser vendor's incentives now include making agentic browsing work. Third, and most practically, the class of bypass that survived this was input generated outside the browser — xdotool, AutoHotkey, pyautogui — because a click synthesised at the OS level is not a CDP click at all, it is a real one, and it carries real coordinates. That is the general shape of the answer whenever a detection keys on how an action was dispatched rather than what it looked like. The teardown
 
 +
 Aug 2026 / Measured, not estimated
 Only 18.5% of the Web's Front Doors Are Actually Open
 The largest published audit of web access controls to date measured 24,898 of the world's most-visited sites across 230 countries and 110 industries, scoring each against six barrier categories: WAF, anti-bot, CAPTCHA, JavaScript dependence, rate limiting and TLS fingerprinting. It replaces a lot of folklore with a number.
 💡 Most sites did not choose their WAF. It arrived with the CDN.
 Headline findings, as a mid-2026 snapshot: only 18.5% of landing pages carry no technical barrier at all, meaning 81.5% deploy at least one, and half of all sites run two or more layered across the network and application layers. WAFs appear on more than nine sites in ten, dynamic rate limiting on more than half. Roughly four sites in ten now disallow AI crawlers in robots.txt. Fashion measured harder to access than banking, which surprises people until you remember what price and inventory data is worth.The finding that matters most is the one that reads as good news. Despite all of that, 88% of landing pages still sit in the two lowest accessibility tiers. Both halves of that are true at once, and holding them together is the whole point of the difficulty ladder above: a WAF being present is not the same as the target being hard, because most WAFs ship switched on by default with a CDN and were never tuned by anyone. The practical reading is that the barrier count tells you almost nothing on its own, and the only honest way to place a target is still to probe the endpoint you actually need. State of Web Access 2026, full report
 
 +
 2026 reality / Stack composition
 Defense in Depth Is Table Stakes Now (26-Site Scan)
 A scan of 26 e-commerce and ticketing sites found the same pattern everywhere: the defense is no longer a vendor, it is a stack. Home Depot's sign-in loads Akamai Bot Manager, PerimeterX/HUMAN, Forter, AND reCAPTCHA Enterprise simultaneously. Beating one is tractable. Beating the composition is not.
 💡 If you are still planning around a single vendor, you are two layers behind
 Five patterns kept appearing across all 26 sites. (1) Stack not vendor: multi-vendor compositions are now the norm, not the exception. (2) Brand names lie: Ticketmaster calls their system "EPS", but open the bundle and you find iamNotaRobot.js, abuse-component.js, aps.js — it is PerimeterX rebranded and served from their own domain. Trust the code, not the label. (3) First-party cloaking: Home Depot serves PX-shaped scripts under random first-party filenames. You cannot identify defenders by checking hostnames anymore, you have to watch how the script behaves at runtime. (4) Lazy-loaded defenses: Ticketmaster ships a reCAPTCHA site key in the homepage JSON but the SDK only loads on login or checkout. Probing only the homepage misses everything. Multi-hop traversal (homepage → login → cart) is the minimum recon bar now. (5) The fingerprint dictates the budget: PerimeterX + behavioral biometrics + reCAPTCHA Enterprise on one page tells you exactly what tier of browser, what kind of proxy, and how slow your automation has to be. Recon is upstream of every other decision. The takeaway: stop asking "which vendor does this site use" and start asking "which stack does this site compose, and where on each layer do I look for cracks."
 
 +
 FOSS Defense / Anti-AI
 Anubis: The Anime-Mascot Firewall Protecting FOSS
 Self-hosted Web AI Firewall (15k+ stars) built by Xe Iaso. Used by Codeberg, FFmpeg, the Linux kernel docs, Sourcehut. Issues a JS proof-of-work challenge with a furry-eared mascot. Codeberg admitted in mid-2025 that AI bots already learned to solve it.
 💡 PoW slows scrapers but headless Chromium solves it naturally
 Anubis is the new category of anti-scraper protection most guides miss: self-hosted, FOSS-targeted, AI-scraper-focused. Unlike Cloudflare and Akamai (enterprise SaaS), Anubis runs as a reverse proxy on the same server as the protected site. The challenge is pure client-side JavaScript proof-of-work — the browser hashes until it finds a nonce matching a difficulty target. For real users this is invisible (~1-3 seconds on modern hardware, painful on old phones). For scrapers using plain requests or curl_cffi, the challenge is unsolvable without JS execution. The bypass is mundane: any headless browser (Playwright, Camoufox, Patchright) with JS enabled will solve it automatically. Persist the auth cookie (techaro.lol-anubis-auth) and reuse it across requests. The political angle: Anubis exists because AI scrapers (OpenAI, Anthropic, Common Crawl, ByteDance) were DDoSing small FOSS projects by ignoring robots.txt. It's a community response, not a commercial product. github.com/TecharoHQ/anubis
 
 +
 TLS / Anti-bot
 Cloudflare Turnstile Solved Without a Browser
 Solvable with pure HTTP, no browser needed. Reverse-engineer the POST payload: 79 parameters covering Canvas, WebGL, Timing and crypto hashes. Status 200 in 0.27s.
 💡 Turnstile PoW is solvable in under 1s via plain HTTP
 The Turnstile POST payload contains 79 parameters. Key groups: Fingerprint (Canvas hash via OffscreenCanvas, WebGL renderer, AudioContext output), Browser Environment (navigator properties, screen dimensions, timezone), Interaction sequence (mouse path, click timing), and Crypto (custom SHA-256 + TEA encryption of the challenge nonce). The Sitekey is extracted automatically from the page source. Algorithms used: Custom SHA-256, TEA block cipher. Token format: Encrypted_Data-Timestamp-Version-Checksum. Full flow: extract Sitekey → initiate challenge → construct responses → generate 95-char token. Result: cf_clearance accepted in 0.27s. No browser process needed.
 
 +
 Defence experiment · Behavioural CAPTCHA · 2026
 A CAPTCHA That Watches How You Tell a Story
 A researcher built a deliberately awkward CAPTCHA as a behavioural-biometrics experiment: it shows a random prompt, asks you to type a short story about it, then rate the experience from 1 to 10. The trick is hidden in the interaction order. If you rate the CAPTCHA before you hit submit, you are probably a bot, because a human reads the story task first and rates last. The signal is not the answer, it is the sequence and rhythm of how you produced it.
 💡 The frontier of human-checking is behaviour over time, not a single correct response.
 
This is worth studying for what it tells you about where bot detection is heading, on both sides.

Why the idea is clever. A traditional CAPTCHA asks for an answer a script can compute or outsource to a solving farm. A behavioural CAPTCHA scores how the answer was produced: typing cadence, edit and pause patterns, the order in which UI elements were touched, time spent reading versus writing. Those are expensive to fake convincingly because they are emergent properties of a real person working through a task, not a field you can fill in. The out-of-order tell (rating before submitting) is a neat tripwire: it catches automation that fills every field it sees without modelling the human workflow the form implies.

Why it still falls. Within a day of the public demo, another engineer bypassed it consistently with an LLM plus Playwright. The sequence is familiar from the rest of this guide: first attempt scored too low and was rejected, the approach was tuned, the second and third attempts passed, including a live run. Behavioural scoring raises the cost of automation, it does not create a wall, because a scripted agent can be taught to produce human-shaped timings and to touch the form in the order a person would. The lesson cuts both ways: if you defend, behavioural signals are a strong layer but not a final one, and you must assume they will be modelled; if you scrape, the modern bar is not "submit the right value" but "reproduce the human process that produced it," which is exactly the territory automation-protocol and interaction-timing detection already live in.

The healthy norm on display. Both the defence and the bypass were published openly, demo and method in the open, framed as understanding security rather than breaking it. That is the same posture this guide takes: the techniques are dual-use, and studying them in public is how both sides get sharper.

StoryCaptcha by Tyler Richards (stackedqueries), a stated proof-of-concept and not production-ready by the author's own note; public AI-plus-Playwright bypass write-up, 2026.

---

## CASE STUDIES — Real Production Bypasses

04 Field notes
How I approached real-world bypasses
The theory above tells you what anti-bots do. These notes tell you what I did when I hit them on a production job. Each is a full day or two of work distilled to: what I tried, why it failed, what finally worked, and the decision tree I'd use next time.

Read these as snapshots, not recipes. Each case is dated and reflects what worked on a specific target at a specific time. Anti-bot systems are probabilistic and adapt continuously, so the exact stack that worked for me may behave differently for you, on a different target, today. The durable value is the reasoning, not the recipe. See Legal & Reality for the full caveat.

 
 
 
 
 
 

 
 Production bypass · ~1 day
 No browser · 0 blocks in 500+ requests · 24 req/min sustained
 
 Akamai v3 in 2026: cracking it without a browser
 Field notes from a production scraping job. The story of what I tried, why each thing failed, and the exact approach that finally got clean 200 responses with zero browser overhead.

 
 The core problem · _abck ~-1~ won't flip
 Akamai's _abck cookie has two states. ~-1~ means unvalidated, full bot score, blocked. ~0~ means validated, trust granted. The cookie is set immediately on any page load, but only flips to ~0~ after sensor.js (a 512KB obfuscated fingerprinting script) executes, collects signals, and POSTs them to /_bm/data.
 Signals that matter most: canvas fingerprint (pixel-level hash of GPU-rendered shapes and text), WebGL renderer (exact GPU model via WEBGL_debug_renderer_info), AudioContext (floating-point sine wave through a compressor node), Chrome extension probes (60 chrome-extension:// URLs fetched via fetch(), zero passing = instant bot score), mouse/scroll trajectory physics, and navigator properties cross-checked against the fingerprint.
 The kicker: validation is multi-request. Trust accumulates across the session, not just on the first hit.
 

 
 What failed (and why)
 
 
 × Attempt 1 — Headless Chrome variants (undetected-chromedriver, Pydoll)
 Standard starting point. Tried Chrome headless with undetected-chromedriver (uc) routed through a Comcast ISP proxy. Then switched to Pydoll — CDP automation without the usual webdriver flags. Both behaved identically. _abck set immediately as ~-1~, never flips. Waited 60 seconds, scrolled, dispatched JS mouse events. Nothing.
 Why: headless Chrome has no GPU. gl.getContext('webgl') returns null. Sensor.js sees zero WebGL context and assigns maximum bot score before the session even starts.
 
 
 × Attempt 2 — SwiftShader software GPU
 Tried --use-angle=swiftshader --use-gl=angle. WebGL works. Canvas renders. AudioContext works. Renderer: ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE))).
 Why: 0x0000C0DE is SwiftShader's device ID, in public lists of virtual GPU IDs. Akamai checks the unmasked renderer against a blocklist. SwiftShader is on it. The canvas hash it produces is also deterministic and known.
 
 
 × Attempt 3 — Camoufox (Firefox with C++ patches)
 Camoufox is excellent for Cloudflare. Real canvas hash, coherent device profile, no CDP artefacts because it uses Mozilla's Juggler protocol. geoip=True aligns WebRTC, DNS, and timezone with the proxy exit country. Set up a session, pointed it at the target, ran a few warm-up requests.
 Why: Camoufox patches the browser, not the network. The TLS fingerprint it produces is Firefox, but on this particular Akamai deployment, even Firefox's real JA4 wasn't enough — the IP reputation scoring on my exit IP and the multi-request trust accumulation Akamai uses meant validation never completed. Camoufox does great work where Cloudflare scoring is the gate; here the gate was earlier and harder.
 
 
 
 × Attempt 4 — JS prototype patching via CDP
 Inject before page load via Page.addScriptToEvaluateOnNewDocument: patch WebGLRenderingContext.prototype.getParameter to return "Intel Iris OpenGL Engine". Patch navigator.platform to "MacIntel", deviceMemory to 8, battery API, chrome.runtime. Result: 2327-byte error page before sensor.js runs.
 Why: Akamai's EdgeWorker fires at the TLS layer before HTML is served. JS injection patches don't affect the TLS handshake. The site also detects the timing signature left by Page.addScriptToEvaluateOnNewDocument. The prototype tampering itself is detectable via Function.prototype.toString().
 
 
 × Attempt 5 — CDP UA override only
 Use Network.setUserAgentOverride with full userAgentMetadata to spoof macOS Chrome 148. No JS injection. Same error page.
 Why: the UA override changes HTTP headers and what navigator.userAgent returns, but not the TLS ClientHello fingerprint. Akamai's EdgeWorker sees the JA4 hash (still Linux Python automation) and blocks at the network layer before the page loads.
 
 
 × Attempt 6 — Xvfb virtual display + non-headless Chrome
 The hypothesis: headless detection is at the GPU level. Give Chrome a virtual display, it thinks it has a real screen. xvfb-run -a, Chrome launches in headless=False. Pages load fully (1.1MB real HTML, all images, category navigation).
 Why: Xvfb has no GPU either. glxinfo shows Mesa software rasterizer. Canvas hash from Mesa llvmpipe is different from SwiftShader but still a known server software renderer, also flagged. _abck stays ~-1~ for 60+ seconds regardless of scrolling.
 
 
 × Attempt 7 — Inject real Mac canvas hashes via CDP
 If the server's canvas hash is flagged, why not inject a real Mac hash? Requires Page.addScriptToEvaluateOnNewDocument again. Same problem as Attempt 3.
 Why: the injection itself is the signal, regardless of what values we inject. Akamai detects the patch through CDP timing artefacts and Function.toString() inspection.
 
 
 

 
 The breakthrough · I was fixing the wrong layer
 Every failed attempt above tried to fix the browser layer. The fundamental insight: most Akamai-protected sites never reach the deep sensor.js evaluation if the request looks like real Chrome at the network layer first.
 Akamai scores in five layers:
 
 Layer 1 · TLS JA4fires before HTML is served
 Layer 2 · HTTP/2 SETTINGSHEADER_TABLE_SIZE, WINDOW_SIZE
 Layer 3 · ALPN + header orderh2 vs h3, Chrome order
 Layer 4 · sensor.jscanvas, WebGL, audio, extensions
 Layer 5 · Behaviouralmouse Bezier, scroll timing
 
 Python's requests, httpx, even curl_cffi with a wrong impersonation profile all fail at Layer 1. The JA4 hash doesn't match Chrome 148's actual ClientHello. Fix Layers 1-3 correctly and you often never reach Layer 4.
 

 
 ✓ What worked · a Go library reimplementing Chrome's TLS stack
 A Go library, akamai-v3-sensor, reimplements Chrome's exact TLS stack at the C level: cipher suite order, GREASE values, extension ordering, ALPN, HTTP/2 SETTINGS frames, HTTP/3 QUIC parameters. The JA4 fingerprint it produces is indistinguishable from real Chrome 148 because it is Chrome 148's cipher suite, implemented in Go.
 // One session, one proxy, one request
s := sensor.NewSession("chrome-148",
 sensor.WithSessionProxy("http://user:pass@comcast-ip:port"),
 sensor.WithSessionTimeout(30*time.Second),
)
resp, _ := s.Get(context.Background(), "https://target-site.com/")
// Status: 200, Protocol: h2, _abck: ~0~ (validated)

// Then GraphQL directly on the same session
gql, _ := s.DoWithBody(ctx, req, bytes.NewReader(payload))
// Status: 200, 30KB product data, zero blocks
 No browser process. No GPU. No canvas hash. No sensor.js execution. Just a TLS handshake that matches Chrome 148 exactly because it uses Chrome 148's cipher suites.
 

 
 Production architecture
 Scrapy spider
 → GoProxyMiddleware (urllib, ~35ms round trip)
 → Go HTTP server :8765 (4-session pool)
 → Go TLS library sessions
 → ISP proxy (Comcast AS7015, static residential)
 → Target site
 Session rotation logic: 206 or GenericError triggers the next session in the pool. Three errors on one session triggers a background re-warm (new TLS handshake, new session state). All 4 sessions blocked returns 503; Python middleware waits 5s and retries up to 3× before falling back to curl_cffi.
 
 24 req/min sustained
 0 blocks in 500+ requests
 0 browser processes
 4 session pool
 
 

 
 Key takeaways
 
 
 Canvas fingerprinting cannot be fixed at the JS layer.
 Patching toDataURL() or getParameter() in JavaScript is detectable via Function.prototype.toString(). The only real fix is at the C++ level, either a real GPU or a library that bypasses the browser entirely.
 
 
 SwiftShader's 0x0000C0DE device ID is permanently flagged.
 Don't bother. It's in Akamai's blocklist and the deterministic canvas hash is also known. Same for Mesa llvmpipe.
 
 
 Page.addScriptToEvaluateOnNewDocument is itself a signal.
 Akamai's EdgeWorker detects the timing gap left by CDP's Runtime.enable command. The injection runs, but the metadata around it is visible.
 
 
 The TLS layer is the one that matters first.
 Fix JA4, HTTP/2, ALPN, and header order before worrying about canvas or WebGL. Most deployments never even reach sensor.js if the TLS fingerprint doesn't match.
 
 
 A clean ISP proxy IP matters.
 Comcast AS7015 static residential is what worked. Datacenter IPs fail at the IP reputation layer regardless of TLS quality. Rotating residential proxies break session trust accumulation, Akamai scores per-session not per-request.
 
 
 

 
 Practical decision tree · Akamai in 2026
 
 
 01
 Find the mobile / GraphQL API first. Often zero anti-bot. Same data, no sensor.js. Look for /graphql, /api/v1/, mobile traffic intercepted via HTTP Toolkit.
 
 
 02
 curl_cffi chrome131 + ISP residential proxy. Works on ~60% of Akamai targets where sensor.js scoring is light.
 
 
 03
 Go TLS library (akamai-v3-sensor) + ISP proxy. For targets with heavier sensor.js where curl_cffi's impersonation doesn't pass JA4 at the EdgeWorker.
 
 
 04
 CloakBrowser (49 C++ patches, loads real extensions). For targets requiring a real canvas hash and passing the 60-extension probe. The kill-switch for sites where TLS spoofing alone is not enough.
 
 
 05
 Managed API (Scrapfly, Bright Data). For Bot Manager Premier targets running pixel challenges. Engineering cost exceeds managed API cost.
 
 
 

 Coming soon
 Cloudflare case study
 Field notes on bypassing Cloudflare Turnstile + Bot Fight Mode in production. Coming after the next Turnstile rollout cycle.

 Coming soon
 DataDome case study
 Bypassing DataDome's behavioural ML and 85,000 per-customer models in production. Notes in progress.

 Coming soon
 PerimeterX (HUMAN) case study
 Notes on bypassing HUMAN Security's PX scoring in production. Coming after current engagement.

 Coming soon
 Kasada case study
 Notes on cracking Kasada's KPSDK + Function.toString() inspection. In progress.

 Coming soon
 F5 Shape case study
 Notes on Shape's custom JS VM and minute-rotation TS cookies. In progress.

 2 field notes on real targets
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Case study
 The Entire US Hermès Catalogue, Behind DataDome, With Zero Browsers
 A team from the sneakerbotting world pulled 7,690 products off hermes.com's own JSON API — through DataDome, no browser at all — in about twenty minutes on eight sessions, for a few euros. It's open-sourced, every number reproducible, and it's the cleanest worked example of this guide's whole thesis: the page is a wrapper, the data is an API, and a browser is the expensive way to reach it.
 💡 A request-based bot against a modern anti-bot lives or dies on exactly three signals
 The three signals, and nothing else matters. Strip the folklore and a browserless bot against DataDome wins on: (1) a TLS fingerprint that looks like Chrome — JA3/JA4 and all, via bogdanfinn/tls-client with a Chrome profile and randomised extension order; (2) header order, not just which headers but the exact sequence including the HTTP/2 pseudo-header order; and (3) a valid DataDome token minted from real sensor data, which is the one part you cannot fake by copying headers. Get the first two wrong and you're flagged before the server reads a single value.The detail that proves they actually did the work: the bck.hermes.com API sends every header lowercase over HTTP/2, while DataDome's own geo.captcha-delivery.com endpoints run HTTP/1.1 and want Chrome's Title-Case names. They matched every request against a real browser capture to get that right. Header order was declared explicitly in code, never left to a map's iteration order.Why no browser, in one number. The product page is just a wrapper around bck.hermes.com/product?productsku=..., which returns the structured object directly — price, every colour and finish variant, breadcrumbs, stock, dimensions — at about 37 KB per product. The equivalent full page render is roughly a hundred times heavier, and you pay residential proxy rates on every megabyte of it. This is the cost section's argument made concrete: the bill scales with kilobytes, not with a browser farm.The flow worth stealing. Warm one session through DataDome once, reuse it for dozens of calls: load the homepage, solve the challenge if it fires, post the client tags (ch then le) to raise the trust score, call /sync-form to mint the x-xsrf-token, then hit the API — rotating the session after a set number of calls so no single datadome cookie is stretched too far. And you don't need a sitemap: bck.hermes.com/menu exposes the full category tree, and a top-level category already aggregates its whole sub-tree (pulling WOMEN returned all 2,785 women's products at pagesize=144).The honest caveat is that the metered part — the DataDome solves — runs through a paid API (Hyper Solutions), so this isn't free; it's cheap because the sensor calls amortise across every product a warm session pulls, and everything else is plain HTTP. That's the real lesson, and it's the opposite of the managed-challenge dead end elsewhere in this feed: where the token is the product and the session is reusable, browserless collection is dramatically cheaper. Where the challenge grades the browser every time, it isn't. Knowing which case you're in is the whole skill.Source: Hyper Solutions guest post on The Web Scraping Club; repo open-sourced.
 
 +
 Case study / CDN routing gap
 Bypassing a Queue-It Virtual Waiting Room via a CDN Gap
 FlySafair put its whole site behind a Queue-It virtual waiting room (a Cloudflare Worker checking for a queue token) during a flash sale. But two paths, /check-in and /manage, were served from a separate AWS CloudFront origin that was never routed through the Worker. And that origin served the entire SPA bundle.
 💡 If any path serves the SPA bundle, it must enforce the same queue
 The bypass chain is a masterclass in CDN-gap exploitation. (1) Recon: probe the public sitemap, log response headers per path. Most paths returned Cloudflare headers and redirected to the queue. Two paths (/check-in, /manage) returned AWS CloudFront headers, a completely separate origin. (2) That CloudFront origin didn't serve a lightweight stub, it served the full single-page-app bundle, the same JavaScript that powers the booking flow. (3) The bundle still runs Queue-It's client-side connector on load, which calls assets.queue-it.net and redirects if no token. But that check fires AFTER the browser has the full bundle. Block assets.queue-it.net in DevTools and the check never fires. (4) Because it's an SPA, once the bundle is running every navigation is client-side. Two console lines, history.pushState({}, '', '/') then dispatch a popstate event, render the home page with zero new server requests. Queue entirely bypassed. The root cause isn't a Queue-It bug, it's a CDN routing gap: most of the site went through Cloudflare with the Worker active, but two paths on a separate CloudFront origin served the same SPA without enforcement. Full writeup on GitHub

---

## LIBRARIES — 86 Tools Across Python, Go, Rust, TypeScript

05 The arsenal
Every tool built to fightevery wall we just described.
Now that you understand the detection stack and the six anti-bot vendors, every library below makes sense in context. curl_cffi exists because of JA4. Camoufox exists because of CDP leaks. PatchRight exists because of Kasada's toString() inspection. The arsenal wasn't built randomly, each tool is a direct countermeasure to a specific detection innovation.

One distinction decides your tool choice more than any other: which layer does your target actually gate on? There are three separable surfaces, and most stealth tooling only solves the first two. TLS fingerprinting keys on the handshake, a Firefox-shaped TLS via Camoufox or a Chrome-shaped TLS via curl_cffi both answer it. JavaScript-layer detection reads navigator properties after the page loads, where a current unpatched Chromium already passes most panels. The third surface is the one that quietly defeats expensive tooling: automation-protocol fingerprinting, which detects how the browser is being driven rather than what it claims to be.
This is the layer that does not care how good your fingerprint patches are. Anything driving Chrome through Playwright leaves a recognisable shape in the control protocol at startup, the Runtime.enable and Target.setAutoAttach handshake sequence. A fork can rewrite navigator properties all day without touching it. The tools that clear this layer are the ones that remove the standard automation framework from the control plane entirely: nodriver drives system Chrome over a direct CDP connection with no Playwright shim, which is why it walks through Cloudflare Turnstile gates that every patched Playwright fork fails. The practical lesson from running these side by side: identify the gate's layer first, then pick the cheapest tool that covers it. A twenty-line curl_cffi wrapper can match a 130MB patched Chromium fork on a TLS-and-JS target, and lose entirely on an automation-protocol target where only a non-Playwright control plane gets through. Patches are not the lever you think they are, the control plane is.

 Before the table · pick the right language first
 Scraping is no longer Python-only.
 Python still dominates the open-source ecosystem (Scrapy, curl_cffi, Camoufox), but the hardest 10% of targets in 2026 reach for Go, TypeScript, or Rust. Here's when each language earns its place, and why mixing them in one pipeline is the production-grade move.

 
 
 
 
 
 Python
 Default
 
 Largest ecosystem. Scrapy + curl_cffi + Camoufox cover 80% of targets. Best for data engineers already running pandas, Airflow, dbt downstream.
 Use when: default choice, fast prototyping, ML pipelines, when team is Python-native
 Key libs: Scrapy, curl_cffi, Camoufox, PatchRight, Crawlee-python, scrapy-stealth
 

 
 
 
 Go
 TLS & concurrency
 
 Closest language to OS-level TLS control. Akamai-grade JA4 spoofing is a Go specialty. Native goroutines beat asyncio at 10,000+ concurrent connections. Single binary deploys.
 Use when: Akamai v3, F5 Shape, TLS fingerprint precision, 10K+ concurrent crawls, edge deployments
 Key libs: akamai-v3-sensor, tls-client, colly, surf, rod, cycletls
 

 
 
 
 TypeScript / Node.js
 
 First-class Playwright and Puppeteer. Better browser automation primitives than Python ports. Strongest ecosystem for AI agents and Chrome extension work. Apify's Crawlee was Node-first for a reason.
 Use when: browser automation is the bottleneck, AI agents, Computer Use Agents, working with Chrome DevTools deeply, full-stack JS team
 Key libs: Crawlee, Playwright (TS), Puppeteer, Stagehand, Browser Use, ScrapingBee SDK, got-scraping
 

 
 
 
 Rust
 Emerging
 
 Lower-level than Go, even more control over TLS internals and memory. Used where you need both performance and Chrome-level fingerprint precision. 67% fewer tokens than equivalent Python in some benchmarks. Steeper learning curve.
 Use when: webclaw, custom TLS work, MCP servers, performance is critical, you already have Rust on the team
 Key libs: webclaw, rquest, rnet, scraper, fantoccini
 
 

 
 
 The pragmatic move in 2026:
 Run Python for orchestration (Scrapy is still the best framework for crawl logic, queues, deduplication, and ML downstream). Drop down to Go via a sidecar HTTP service only for the requests that need true Chrome JA4 (Akamai v3, F5 Shape). Use Node + Crawlee or Playwright (TS) when the work is genuinely browser-automation-heavy, especially for AI agents. The Akamai case study above shows this exact pattern: Scrapy spider in Python, calling a Go server via urllib for the protected requests only.
 

Master comparison table, all 86 libraries & tools#

 Library (click to expand)TypeLangJS renderTLS spoofTLS detailAnti-bot targetMCPStars

curl_cffi ⚡HTTPPython Chrome JA4+Akamai, DataDome–

 
 
 
 ⚡ HTTP
 Under the hood: libcurl C library with custom TLS patches. Emits exact Chrome/Safari/Firefox TLS ClientHello at the C level, cipher suites, extensions, ALPN, GREASE all match real browsers.
 
 
 ✓ ProsFastest HTTP option. Pure HTTP speed, no browser overheadConfirmed DataDome + Akamai bypass in 2026Asyncio support via AsyncSessionSimple requests-compatible API
 ✗ ConsNo JavaScript execution, useless for JS-rendered pagesCannot solve CAPTCHA or Turnstile challengesTLS fingerprint only, no behaviour or canvas spoofing
 
 
 

Scrapling ⚡HTTPPython Chrome TLSCloudflare Turnstile38k

 
 
 
 ⚡ HTTP
 Under the hood: Wraps curl_cffi for stealth HTTP + integrates Camoufox for browser mode. StealthyFetcher uses a real patched Firefox under the hood when needed.
 
 
 ✓ ProsStealthyFetcher solves Cloudflare Turnstile nativelyAsync spider v0.4, pause/resume, per-domain throttlingDual-mode: HTTP for speed, browser for hard targetsActive development, 38K stars
 ✗ ConsHigher complexity than plain curl_cffiBrowser mode adds Camoufox overhead when triggered
 
 
 

webclaw ⚡HTTPRust Chrome TLSMedium targets–

 
 
 
 ⚡ HTTP
 Under the hood: Rust HTTP client with TLS fingerprint spoofing. Emits browser TLS signatures from Rust, fast and low-memory.
 
 
 ✓ ProsRust speed, very low CPU/memory overheadTLS fingerprinting at Rust levelGood for high-volume HTTP scraping
 ✗ ConsRust, no Python APILess widespread adoptionNo JS rendering
 
 
 

httpx ⚡HTTPPython NoneUnprotected only–

 
 
 
 ⚡ HTTP
 Under the hood: Modern Python HTTP library with async support and HTTP/2.
 
 
 ✓ ProsAsync + sync in one libraryHTTP/2 support unlike requestsType hints, modern API
 ✗ ConsTLS fingerprint still Python-default, detectableNot as stealthy as curl_cffi without patching
 
 
 

requests ⚡HTTPPython NoneUnprotected only52k

 
 
 
 ⚡ HTTP
 Under the hood: Pure Python HTTP library. Sends HTTP/1.1 requests with standard Python TLS.
 
 
 ✓ ProsSimple API, universally knownSynchronous, easy to debug
 ✗ ConsTLS fingerprint is instantly detectable (Python urllib3)No async, slow for concurrent scrapingNo anti-bot capability
 
 
 

tls-client ⚡HTTPGo/Py Chrome/Firefox TLSAkamai, DataDome–

 
 
 
 ⚡ HTTP
 Under the hood: Go/Python wrapper around a Go TLS client that mimics browser fingerprints. Predecessor to cycle-tls.
 
 
 ✓ ProsPython bindings availableBypasses JA3/JA4 fingerprintingLighter than curl_cffi
 ✗ ConsLess actively maintained than curl_cffiNo JS renderingBinary dependency
 
 
 

Playwright 🌐BrowserPy/JS CDP (detectable)Medium (CDP leaks)68k

 
 
 
 🌐 Browser
 Under the hood: Chromium DevTools Protocol (CDP). Microsoft-maintained. Drives real Chromium, Firefox, or WebKit browsers over CDP socket.
 
 
 ✓ ProsBest JS execution support, renders any SPA68K stars, massive ecosystem and docsCross-browser: Chrome, Firefox, Safari (WebKit)Screenshot, PDF, network intercept built-in
 ✗ ConsCDP is detectable, needs C++ wrapper (PatchRight/Camoufox)Heavy: launches a full browser process per sessionSlow vs HTTP, ~10× more memory per concurrent task
 
 
 

Camoufox 🌐BrowserPython C++ Firefox JugglerCloudflare 100%, Akamai–

 
 
 
 🌐 Browser
 Under the hood: Forked Firefox with C++ binary patches to Juggler protocol (below CDP). Patches navigator, canvas, WebGL, fonts, window.chrome at binary level.
 
 
 ✓ Pros100% Cloudflare pass rate as of March 2026geoip=True aligns all 5 identity vectors automaticallyBelow-CDP, invisible to JS-level detectionAsync context manager, drop-in playwright replacement
 ✗ ConsFirefox only, no Chrome/SafariHeavier than curl_cffiOccasional site-specific quirks with Firefox fingerprint
 
 
 ⚠ CVE-2026-6770 — Process-Level Fingerprint Leak (patched Firefox 150)
 Firefox below v150 returned IndexedDB database names in hash table iteration order rather than sorted order. Because the hash table is shared across all origins within the same browser process, the ordering became a stable, high-entropy process-lifetime fingerprint — consistent across tabs, sites, private windows, and even Tor Browser's "New Identity" resets. Anti-bot systems could use this to correlate multiple scraping identities running in the same browser process, regardless of proxy rotation or profile switching.
 Fix: Use Camoufox built on Firefox 150+ (patched April 2026). Verify your version.
 Scraper lesson: Always isolate identities at the process level, not just the profile level. Multiple sessions in one browser process can be correlated through memory-state artifacts like this even when fingerprint patching is otherwise perfect.
 Ref: CVE-2026-6770 · mfsa2026-30 · Fixed Firefox 150 / Tor Browser 15.0.10
 
 
 

CloakBrowser 🌐BrowserPython 49 C++ patchesAkamai, reCAPTCHA v3 0.9–

 
 
 
 🌐 Browser
 Under the hood: 49+ C++ binary patches to Chromium itself. Patches webdriver, chrome object, plugins, permissions, WebGL, Canvas, AudioContext, and extension probe responses at the C++ level, not JavaScript. Repo: github.com/CloakHQ/CloakBrowser.
 
 
 ✓ ProsreCAPTCHA v3 score 0.9, the highest of any tool I have testedPasses Akamai's 60-URL extension probe (loads real 1Password / Bitwarden / LastPass profiles)Real extension fingerprint database built-in, no manual setupC++ level patches survive Function.prototype.toString() inspection (the kill-switch for JS stealth tools like puppeteer-stealth and patchright)Solves the hard problems Camoufox and PatchRight cannot: canvas hash at the C++ rendering layer, AudioContext at the audio pipeline layer, real extension probe responsesNow open source on GitHub, active development
 ✗ ConsHigher resource cost than HTTP-only tools (200MB+ per instance)Chromium only, no Firefox variantNewer than Camoufox, smaller communityOverkill for sites that curl_cffi + ISP proxy already handle
 
 
 

PatchRight 🌐BrowserPython Py source patchesKasada, Cloudflare–

 
 
 
 🌐 Browser
 Under the hood: Patches Playwright Python source files at install time. Removes CDP signatures, webdriver property, and stealth tells from the JS layer.
 
 
 ✓ ProsOpen source, free, Kasada bypass confirmedDrop-in Playwright replacement, zero API changesPatches JS layer without C++ recompilation
 ✗ ConsJS-level patches only, determined adversary can detect at binary levelLess robust than Camoufox on Cloudflare 5-second challengeRequires Playwright to be installed first
 
 
 

Puppeteer 🌐BrowserNode CDP (detectable)Medium targets89k

 
 
 
 🌐 Browser
 Under the hood: Node.js CDP driver for Chromium. Google-maintained. The original headless browser automation library.
 
 
 ✓ Pros89K stars, largest ecosystemNative Google product, Chromium compatibility guaranteedGood for CI/CD screenshot and PDF generation
 ✗ ConsCDP is easily detectable (webdriver=true, window.chrome absent)Node.js only, no PythonNo anti-bot stealth built-in
 
 
 

Selenium 🌐BrowserMulti webdriver=trueWeak (legacy)29k

 
 
 
 🌐 Browser
 Under the hood: WebDriver protocol (W3C standard). Drives any browser via standardised JSON protocol. The original browser automation framework.
 
 
 ✓ ProsMulti-language: Python, Java, C#, Ruby, JSSupports all browsers including IE and SafariHuge ecosystem, well-documented
 ✗ Consnavigator.webdriver=true is trivially detectableSlowest option, WebDriver adds round-trip latencyRequires ChromeDriver binary management
 
 
 

SeleniumBase UC 🌐BrowserPython UC removes WD flagKasada, general stealth10k

 
 
 
 🌐 Browser
 Under the hood: SeleniumBase with undetected-chromedriver mode. Patches Chrome binary to remove webdriver flag and CDP signatures.
 
 
 ✓ ProsUC mode removes webdriver=true flagPasses basic Cloudflare and PerimeterXBuilt-in test framework, good for QA teams
 ✗ ConsNot as strong as Camoufox/PatchRight on hard targetsChrome binary patches can break on updatesSlower than Playwright equivalent
 
 
 

Selenium-Driverless 🌐BrowserPython CDP no WebDriverMedium targets–

 
 
 
 🌐 Browser
 Under the hood: Direct CDP connection without ChromeDriver binary, no webdriver flag set. Async Python API.
 
 
 ✓ ProsNo ChromeDriver binary neededNo webdriver=true flagAsync Python native
 ✗ ConsNewer, less battle-tested than nodriverChrome onlySome CDP signatures still detectable
 
 
 

nodriver 🌐BrowserPython Raw CDP asyncMedium targets–

 
 
 
 🌐 Browser
 Under the hood: Controls Chrome via its internal DevTools socket without using CDP's standard automation flag. Chrome doesn't know it's being driven.
 
 
 ✓ ProsChrome does not set automation flagsPasses many sites that detect standard CDPLightweight, lower overhead than full Playwright
 ✗ ConsRelatively new, less battle-testedPython onlySome sites still detect via other JS signals
 
 
 

pydoll 🌐BrowserPython Async CDPMedium targets–

 
 
 
 🌐 Browser
 Under the hood: Pure Python browser automation using Chrome DevTools Protocol directly. No external driver.
 
 
 ✓ ProsNo ChromeDriver dependencyFast startup, no driver processPure Python, easy to install
 ✗ ConsCDP still potentially detectableLess mature than PlaywrightSmaller community
 
 
 

Botright 🌐BrowserPython CAPTCHA solvingCAPTCHA targets–

 
 
 
 🌐 Browser
 Under the hood: Playwright wrapper focused on CAPTCHA solving and stealth. Uses AI to solve CAPTCHAs during automation.
 
 
 ✓ ProsAuto-solves reCAPTCHA and hCAPTCHA inlineStealth patches on top of PlaywrightGood for CAPTCHA-heavy targets
 ✗ ConsHeavier than raw PlaywrightCAPTCHA AI may be rate-limitedLess control over fingerprinting
 
 
 

Botasaurus 🌐BrowserPython Gaussian mouseDataDome behaviour–

 
 
 
 🌐 Browser
 Under the hood: Playwright wrapper that adds Gaussian mouse movement, realistic typing, scroll physics, and session management.
 
 
 ✓ ProsGaussian mouse curves, passes behavioural ML checksHandles DataDome behavioural scoringSession persistence and rotating profiles built-in
 ✗ ConsBrowser-based overheadOverkill for targets without behavioural analysisLess control than raw Playwright
 
 
 

rayobrowse 🌐BrowserPy/Docker Real device FP DBHard targets–

 
 
 
 🌐 Browser
 Under the hood: Docker-based stealth Chromium browser from Rayobyte. C++ level patches (not JS-level), exposed via CDP so Playwright/Puppeteer/Selenium can connect natively. Self-hosted = free and unlimited; managed Cloud version available.
 
 
 ✓ ProsFree and unlimited self-hosted (Docker), Cloud version managedC++ level patches survive Function.toString() inspectionCoherent device profile: UA, WebGL, Canvas, AudioContext, fonts all matchNative CDP, drop-in for Playwright/Puppeteer/SeleniumUsed by Rayobyte to scrape millions of pages/day in production
 ✗ ConsStill in beta, results vary by target siteWindows + Android profiles strongest, macOS/Linux less matureClosed source (license restricts certain organizations)Canvas/WebGL FP coverage still evolving
 
 
 

undetected-chromedriver 🌐BrowserPython Removes WD flagMedium targets5k

 
 
 
 🌐 Browser
 Under the hood: Patches ChromeDriver binary to remove webdriver=true and CDP automation flags at binary level.
 
 
 ✓ ProsRemoves most obvious webdriver signalsSimple: just replace webdriver.Chrome with uc.Chrome
 ✗ ConsChrome binary patches break on updates frequentlyNot as robust as Camoufox on modern CloudflareMaintenance has slowed
 
 
 

⭐ Scrapy ⚡FrameworkPython Via curl_cffi mwMedium (with middleware)52k

 
 
 
 ⚡ HTTP
 Under the hood: Twisted-based async Python framework. Pure HTTP, sends requests, receives responses, parses with XPath/CSS. No browser.
 
 
 ✓ Pros52K stars, production standard for HTTP scrapingMassive ecosystem: scrapy-redis, scrapy-playwright, scrapydAsync by default, hundreds of concurrent requestsMature: pipelines, middlewares, extensions all built-in
 ✗ ConsNo JS rendering by default (need playwright middleware)Pure HTTP, detectable by TLS fingerprint without curl_cffi middlewareSteeper learning curve than requests
 
 
 

Crawlee 🌐FrameworkNode/Py Playwright-basedMedium targets15k

 
 
 
 🌐 Browser
 Under the hood: Apify's unified Node.js framework. Wraps both HTTP (got-scraping) and Playwright/Puppeteer. Handles retries, deduplication, storage.
 
 
 ✓ ProsDual HTTP+browser mode in one framework15K stars, actively maintained by ApifyBuilt-in dataset storage, request queue, proxy rotation
 ✗ ConsNode.js primary (Python port is newer, less mature)More opinionated than Scrapy, harder to customiseHeavier dependency footprint
 
 
 

scrapy-camoufox ⚡FrameworkPython Camoufox integrationHard targets–

 
 
 
 ⚡ HTTP
 Under the hood: Scrapy middleware that routes requests through Camoufox browser for stealth. Best of Scrapy + Camoufox.
 
 
 ✓ ProsScrapy pipeline management + Camoufox stealthPer-request browser decision (HTTP vs browser)Good for mixed protection targets
 ✗ ConsCamoufox overhead on browser requestsRequires both Scrapy and Camoufox installed
 
 
 

scrapy-nodriver ⚡FrameworkPython nodriver integrationMedium targets–

 
 
 
 ⚡ HTTP
 Under the hood: Scrapy middleware using nodriver for browser requests, Chrome without CDP flags.
 
 
 ✓ ProsScrapy framework + Chrome without automation flagsGood for Cloudflare-protected targetsUse Scrapy architecture you know
 ✗ Consnodriver overhead per browser requestLess control than raw nodriver
 
 
 

scrapy-stealth ⚡FrameworkPython Browser TLS + HTTP/2Cloudflare, Akamaiv0.4 (2026)

 
 
 
 ⚡ HTTP
 Under the hood: Pluggable Scrapy DOWNLOADER_MIDDLEWARE with three drivers: basic + turbo (TLS fingerprint + HTTP/2 impersonation, no browser), and browser (real Chrome via CDP for JS-heavy targets). Per-request engine switching via request.meta["stealth"]. Repo: github.com/fawadss1/scrapy-stealth. Author Fawad ships frequent updates.
 
 
 ✓ ProsBuilt-in TLS fingerprint spoofing, scrapy-playwright/scrapy-splash/scrapy-selenium do not have thisPer-request engine switching: keep light HTTP for easy URLs, browser only for protected onesBuilt-in proxy + fingerprint rotation (no separate middleware needed)Native Cloudflare and Akamai detection via status + body keyword checksBrowser profiles like chrome_147, safari_ios_18_1_1 kept currentMIT license, active development (v0.4 May 2026)
 ✗ ConsProject is new with limited GitHub adoption (low star count)Less battle-tested than scrapy-playwright in production at scaleBrowser driver 5-15s per page, use selectively for JS-protected URLs onlyRequires Python 3.11+ and Scrapy 2.15+
 
 
 

Firecrawl ⚡AIAPI FIRE-1 engineHard via managed111k

 
 
 
 ⚡ HTTP
 Under the hood: API service that converts any URL to clean Markdown or structured JSON for LLM consumption. FIRE-1 agent for multi-page crawls.
 
 
 ✓ Pros111K stars, most popular LLM scraping toolOutputs clean Markdown, 67% fewer tokens for LLMsMCP server for Claude/Cursor/LangChainHandles JS rendering and auth flows
 ✗ ConsAPI cost at scaleLess control over request details vs raw scrapingData goes through third-party servers
 
 
 

Crawl4AI 🌐AIPython Playwright-basedMedium targets60k

 
 
 
 🌐 Browser
 Under the hood: Local Playwright wrapper optimised for LLM output. Runs locally, converts pages to clean Markdown with BM25 relevance filtering.
 
 
 ✓ ProsFully local, no API costBM25 filter reduces LLM context bloatLLM extraction schema definitionMIT license, commercial friendly
 ✗ ConsPlaywright overhead per pageLess anti-bot bypass than CamoufoxNo managed infrastructure
 
 
 

ScrapeGraphAI ⚡AIPython NL graph pipelineLight protection18k

 
 
 
 ⚡ HTTP
 Under the hood: LLM-powered extraction that builds a graph pipeline from a natural language prompt. Local or API.
 
 
 ✓ ProsNatural language extraction definitionOpen source, self-hostableGraph pipeline handles multi-step extractions
 ✗ ConsLLM inference cost/latency per extractionLess deterministic than CSS/XPath selectorsNewer, less battle-tested at scale
 
 
 

Jina Reader API ⚡AIAPI Built-in renderingMedium targets–

 
 
 
 ⚡ HTTP
 Under the hood: REST API: prefix r.jina.ai/ to any URL to get clean Markdown back. Zero setup.
 
 
 ✓ ProsSimplest possible API, one URL prefixGood JS renderingFree tier available
 ✗ ConsData goes through Jina serversLess control than local scrapingRate limited on free tier
 
 
 

Steel 🌐AIAPI Docker browserMedium targets–

 
 
 
 🌐 Browser
 Under the hood: Self-hosted browser API with MCP server. AI agents call it as a tool to browse the web.
 
 
 ✓ ProsSelf-hosted, data stays localMCP server for AI agent integrationDocker deployment
 ✗ ConsNewer product, smaller communitySetup overhead vs managed services
 
 
 

Bright Data ⚡ManagedAPI Full enterprise stackAll incl. F5 Shape–

 
 
 
 ⚡ HTTP
 Under the hood: 72M+ IP network + scraping API. Managed infrastructure handles anti-bot, JS rendering, proxy rotation.
 
 
 ✓ Pros98.44% success rate, highest benchmarkCovers F5 Shape (only managed service that does)Residential + ISP + datacenter + mobile IPsDataset marketplace for pre-scraped data
 ✗ ConsMost expensive optionData goes through third-partyOverkill for simple targets
 
 
 

Zyte ⚡ManagedAPI Full stackAll targets–

 
 
 
 ⚡ HTTP
 Under the hood: Scrapy company's managed scraping platform. Zyte API + AutoExtract for structured data.
 
 
 ✓ Pros#1 Proxyway benchmark 2025AutoExtract returns structured product/article dataBuilt by the Scrapy maintainersSmart proxy rotation built-in
 ✗ ConsExpensive at scaleAutoExtract less flexible than custom extraction
 
 
 

Apify ⚡ManagedAPI 10K+ ActorsMedium-hard–

 
 
 
 ⚡ HTTP
 Under the hood: 10,000+ pre-built Actors on serverless cloud. Crawlee at core. MCP server for AI agents.
 
 
 ✓ ProsBiggest marketplace of pre-built scrapersMCP server: AI agents call Actors as toolsFree $5/mo credit for casual useCrawlee open source available locally
 ✗ ConsCU pricing can escalateData goes through Apify cloudLess control over anti-bot approach in Actors
 
 
 

ScrapingBee ⚡ManagedAPI Managed renderingMedium targets–

 
 
 
 ⚡ HTTP
 Under the hood: Managed scraping API. Handles JS rendering, CAPTCHA, proxies via simple REST call.
 
 
 ✓ ProsDead simple: one API call, get HTML backFree tier availableHandles most modern JS rendering
 ✗ ConsLess anti-bot strength than Zyte or Bright DataPer-call pricingLess control over request details
 
 
 

SerpAPI ⚡ManagedAPI SERP JSON APISearch engine data–

 
 
 
 ⚡ HTTP
 Under the hood: Managed API that abstracts Google, Bing, Baidu, Yandex, Yahoo, DuckDuckGo and 80+ other engines behind a single REST endpoint. Returns fully parsed, normalised JSON — organic results, ads, featured snippets, knowledge graphs, local packs, shopping, images, news — without you touching a proxy or a headless browser.
 
 
 ✓ Pros80+ search engines including Google, Bing, Baidu, Yandex, Yahoo, DuckDuckGo, Google Maps, Google Shopping, Google ScholarRichest parsed output — 15+ SERP element types including AI Overviews, PAA, video carousels, local packAdvanced geo-targeting and device simulation per requestFree tier: 100 searches/month, no credit card requiredWell-documented, widely adopted — largest mindshare in SERP API category
 ✗ ConsPremium pricing: $75/mo for 5K, $150/mo for 15K searches — expensive vs alternatives like Scrape.do (21x cheaper per request)SERP-specific only, not a general-purpose scraping APIData routed through SerpAPI servers
 
 
 

ScrapeBadger ⚡ManagedAPI Smart billing + AI extractCloudflare, DataDome, hard targets–

 
 
 
 ⚡ HTTP
 Under the hood: Newer managed scraping API built natively for modern anti-bot stacks. Key differentiator: smart billing — if you enable JS rendering and anti-bot bypass but the target doesn't need them, ScrapeBadger auto-downgrades the request and charges you less. Also ships an MCP server for Twitter/X scraping (profiles, tweets, trends) for AI agent workflows.
 
 
 ✓ ProsPay-only-for-success model — failed requests don't cost youSmart auto-downgrade: enables anti-bot features only when needed, reducing cost automaticallyNative Cloudflare Turnstile and DataDome bypass — 99%+ reported success on Zillow, Amazon, LinkedInAI extraction mode: pass a plain-English prompt, get structured JSON back without writing selectorsMCP server for Twitter/X — profiles, tweets, trends for AI agent pipelines
 ✗ ConsNewer entrant — less battle-tested at scale than Bright Data or ZyteSmaller community and ecosystem vs established providersData routed through third-party servers
 
 
 

Oxylabs ⚡ManagedAPI OxyCopilot AIHard targets–

 
 
 
 ⚡ HTTP
 Under the hood: 102M+ IP network with OxyCopilot AI extraction and scraper APIs.
 
 
 ✓ ProsLargest IP pool (102M+)OxyCopilot: AI-powered extractionStrong residential + datacenter options
 ✗ ConsEnterprise pricingData through third-party
 
 
 

Browserbase 🌐ManagedAPI Managed browserHard targets–

 
 
 
 🌐 Browser
 Under the hood: Managed Playwright cloud. Run Playwright scripts remotely without managing browser infrastructure.
 
 
 ✓ ProsNo browser infra to manageScales automaticallyPlaywright API unchanged, zero code changes
 ✗ Cons42% success rate on anti-bot benchmark (vs 81% Browser Use)Per-session pricingLess stealth than self-hosted Camoufox
 
 
 

chompjs ⚡ParserPython N/AParser only–

 
 
 
 ⚡ HTTP
 Under the hood: Python library to parse JavaScript objects embedded in HTML pages. Converts JS literals to Python dicts.
 
 
 ✓ ProsHandles malformed JSON that json.loads rejectsExtracts __NEXT_DATA__ and embedded JS objectsZero dependencies
 ✗ ConsParsing only, not a scraping frameworkNarrow use case
 
 
 

Parsel ⚡ParserPython N/AParser only–

 
 
 
 ⚡ HTTP
 Under the hood: Scrapy's HTML/XML parser library. XPath and CSS selectors with a clean Python API.
 
 
 ✓ ProsXPath + CSS in one libraryUsed inside Scrapy, familiar APIFaster than BeautifulSoup for selection
 ✗ ConsParsing only, no HTTP requestsLess beginner-friendly than BS4
 
 
 

BeautifulSoup4 ⚡ParserPython N/AParser only10k

 
 
 
 ⚡ HTTP
 Under the hood: Python HTML/XML parser. Wraps lxml or html.parser. Builds a parse tree from raw HTML strings.
 
 
 ✓ ProsSimple, readable API, beginner-friendlyWorks on any HTML string regardless of sourceNo network requests, pure parsing
 ✗ ConsNot a scraping framework, needs requests/httpx separatelySlow on large documents vs selectolax/lxmlNo anti-bot capability whatsoever
 
 
 

mitmproxy ⚡RE ToolPython N/ARE / intercept37k

 
 
 
 ⚡ HTTP
 Under the hood: Python-based HTTPS proxy. Intercepts, inspects, and modifies HTTP/HTTPS traffic between client and server.
 
 
 ✓ ProsFull request/response visibility and modificationScript intercepted traffic with PythonGood for understanding anti-bot request patterns
 ✗ ConsRequires certificate trust on deviceSSL pinning blocks it on hardened appsFor analysis/RE, not production scraping
 
 
 

HTTPToolkit ⚡RE ToolAny N/AMobile API intercept–

 
 
 
 ⚡ HTTP
 Under the hood: HTTPS intercepting proxy for development and mobile API discovery. Open source.
 
 
 ✓ ProsIntercepts HTTPS without SSL pinning (with rooted device)Beautiful UI for inspecting requestsWorks with Android emulators via ADB
 ✗ ConsFor analysis only, not for production scrapingRequires rooted device for mobile apps
 
 
 

Frida ⚡RE ToolPy/JS N/ASSL hooks–

 
 
 
 ⚡ HTTP
 Under the hood: Dynamic instrumentation toolkit. Injects JavaScript into running processes. Used to hook native functions and bypass SSL pinning.
 
 
 ✓ ProsBypass SSL pinning in any Android/iOS appHook any native function at runtimeEssential for mobile app API extraction
 ✗ ConsRequires rooted/jailbroken deviceComplex setup, not for beginnersApp-specific scripts needed per target
 
 
 

rebrowser-patches 🌐BrowserPython Chrome source patchesMedium targets–

 
 
 
 🌐 Browser
 Under the hood: JavaScript patches injected into Playwright/Puppeteer pages to mask automation signals.
 
 
 ✓ ProsRemoves navigator.webdriver and CDP signals at JS levelWorks with any Playwright versionEasy to integrate
 ✗ ConsJS-level only, binary signals still presentLess robust than C++ patches
 
 
 

cycle-tls ⚡HTTPGo/JS Chrome/Firefox TLSAkamai, DataDome–

 
 
 
 ⚡ HTTP
 Under the hood: Node.js/Go TLS client that cycles through browser fingerprints. Sends real JA3 hashes per request.
 
 
 ✓ ProsNode.js TLS fingerprint spoofingPer-request fingerprint rotationGood for JS pipeline scraping
 ✗ ConsNode.js only, no PythonLess robust than curl_cffi on hard targets
 
 
 

GoLogin 🌐BrowserCloud Antidetect profilesHard multi-account–

 
 
 
 🌐 Browser
 Under the hood: Cloud anti-detect browser. Manages browser profiles with unique fingerprints stored in cloud. Multi-account management.
 
 
 ✓ ProsProfile fingerprint management at scaleGood for multi-account scraping operationsTeam sharing of browser profiles
 ✗ ConsPaid product, cloud-dependentNot suitable for automated pipeline scrapingDesigned for manual browsing, not scripted crawling
 
 
 

Multilogin 🌐BrowserCloud Antidetect profilesHard multi-account–

 
 
 
 🌐 Browser
 Under the hood: Commercial anti-detect browser with managed profile fingerprints. Team collaboration on browser profiles.
 
 
 ✓ ProsProfessional multi-account managementManaged fingerprint databaseTeam profile sharing
 ✗ ConsVery expensiveDesigned for manual use, not automated crawlingData in cloud
 
 
 

ScraperAPI ⚡ManagedAPI Full stackAll incl. Walmart–

 
 
 
 ⚡ HTTP
 Under the hood: Simple proxy rotation + JS rendering API. Handles geo-targeting and header rotation.
 
 
 ✓ ProsSimple integration, just prepend URLFree tier with 1000 calls/moGeo-targeting built in
 ✗ ConsWeaker on hard anti-bot targetsBasic anti-bot handling vs Zyte/Bright Data
 
 
 

Decodo ⚡ManagedAPI Full stackAll targets–

 
 
 
 ⚡ HTTP
 Under the hood: Smartproxy's new brand. Residential, datacenter, and mobile proxy network.
 
 
 ✓ ProsAffordable residential proxiesPay-as-you-go pricingGood for mid-scale scraping
 ✗ ConsLess powerful than Bright Data on hard targetsSmaller IP pool
 
 
 

CapSolver ⚡CAPTCHAAPI N/AreCAPTCHA/hCaptcha–

 
 
 
 ⚡ HTTP
 Under the hood: AI-powered CAPTCHA solving service. Uses computer vision to solve reCAPTCHA v2/v3, hCAPTCHA, Cloudflare Turnstile.
 
 
 ✓ ProsSolves reCAPTCHA v3, hCAPTCHA, Turnstile, ImageCAPTCHAFast: under 10 seconds for most CAPTCHA typesAPI-based, works with any language
 ✗ ConsCost per solve (~$0.001–0.002)reCAPTCHA v3 score may be low vs C++ browserSolving is symptomatic, better to avoid triggering CAPTCHA
 
 
 

2captcha ⚡CAPTCHAAPI N/AAll CAPTCHA types–

 
 
 
 ⚡ HTTP
 Under the hood: Human + AI hybrid CAPTCHA solving service. One of the oldest in the market.
 
 
 ✓ ProsSolves almost any CAPTCHA type including custom onesHuman fallback for unusual CAPTCHAsLarge API ecosystem
 ✗ ConsSlowest option, human solving adds latencyCost per solveLess automated than CapSolver
 
 
 

Anti-Captcha ⚡CAPTCHAAPI N/AreCAPTCHA/image–

 
 
 
 ⚡ HTTP
 Under the hood: Human + AI CAPTCHA solving service. Competitor to 2captcha.
 
 
 ✓ ProsSolves all major CAPTCHA typesCompetitive pricingAPI compatible with 2captcha
 ✗ ConsHuman solving latencyCost per solveBetter to avoid triggering CAPTCHA in the first place
 
 
 

Scrapyd ⚡FrameworkPython Via middlewareScrapy deploy tool–

 
 
 
 ⚡ HTTP
 Under the hood: Daemon that deploys and runs Scrapy spiders via JSON API. Port 6800. Process-based job queue.
 
 
 ✓ ProsZero cloud cost, runs on any serverScrapydWeb provides visual dashboardSimple deploy: scrapyd-deploy -p project
 ✗ ConsSingle node by default, no horizontal scalingNo built-in monitoring or alertingJob isolation is process-level only
 
 
 

scrapy-redis ⚡FrameworkPython N/ADistributed Scrapy–

 
 
 
 ⚡ HTTP
 Under the hood: Scrapy extension connecting spiders to a Redis shared URL queue. Enables distributed crawling.
 
 
 ✓ ProsHorizontal scale: add workers without code changeRedis deduplicates URLs across all workersOne codebase, N machines
 ✗ ConsRedis is a new SPOFNo built-in job schedulingRequires Redis infrastructure
 
 
 

scrapy-cluster ⚡FrameworkPython N/AEnterprise Scrapy–

 
 
 
 ⚡ HTTP
 Under the hood: Distributed Scrapy cluster using Redis + Kafka + Zookeeper. Enterprise-scale distributed crawling.
 
 
 ✓ ProsTrue enterprise-scale distributed crawlingKafka for message durabilityMulti-project support
 ✗ ConsComplex infra: Redis + Kafka + ZookeeperOverkill for most use casesHigh ops overhead
 
 
 

scrapy-poet ⚡FrameworkPython N/APage Object pattern–

 
 
 
 ⚡ HTTP
 Under the hood: Dependency injection framework for Scrapy spiders. Cleaner spider code with page objects.
 
 
 ✓ ProsCleaner code via page objects patternWorks with zyte-spider and AutoExtractTestable spider logic
 ✗ ConsAdds abstraction overheadLearning curve for Scrapy veterans
 
 
 

Splash 🌐BrowserDocker Lua scriptingLight protection–

 
 
 
 🌐 Browser
 Under the hood: Lua-scriptable browser for JS rendering, runs in Docker. Integrates with Scrapy via scrapy-splash.
 
 
 ✓ ProsDocker-based, easy to deployLua scripting for complex interactionsGood for Scrapy integration on JS sites
 ✗ ConsOutdated, Playwright has superseded itLua scripting adds complexityLess stealth than Camoufox
 
 
 

selectolax ⚡ParserPython N/AFast HTML parser–

 
 
 
 ⚡ HTTP
 Under the hood: C-based HTML parser (lexbor engine). 10–100× faster than BeautifulSoup for pure parsing tasks.
 
 
 ✓ ProsExtremely fast, C engine vs Python in BS4CSS selectors with clean Python APILow memory footprint
 ✗ ConsCSS selectors only, no XPathLess forgiving on malformed HTML than BS4Smaller community/docs
 
 
 

lxml ⚡ParserPython N/AXPath + CSS parser–

 
 
 
 ⚡ HTTP
 Under the hood: C-based XML/HTML parser. Fastest Python HTML parsing option.
 
 
 ✓ ProsFastest Python HTML parser by farFull XPath 1.0 supportHandles massive documents efficiently
 ✗ ConsStricter on malformed HTML than BS4C dependency, occasional install issuesVerbose API vs BS4
 
 
 

w3lib ⚡ParserPython N/AURL/text utils–

 
 
 
 ⚡ HTTP
 Under the hood: Web-related utility functions. URL normalisation, encoding handling. Used internally by Scrapy.
 
 
 ✓ ProsURL cleaning and normalisationEncoding detection and conversionScrapy internals, very stable
 ✗ ConsUtility library only, not a scraperMost devs use it via Scrapy, not directly
 
 
 

SwiftShadow ⚡ProxyPython N/AProxy pool manager–

 
 
 
 ⚡ HTTP
 Under the hood: Free proxy pool manager. Fetches, validates and rotates free proxies automatically.
 
 
 ✓ ProsFree, zero proxy costAuto-validates and rotates on failure2 lines of code integration
 ✗ ConsFree proxies are low quality, high failure rateNot for hard anti-bot targetsIP reputation usually poor
 
 
 

requests-ip-rotator ⚡ProxyPython N/AAWS API Gateway IPs–

 
 
 
 ⚡ HTTP
 Under the hood: Rotates requests through AWS API Gateway endpoints to get rotating IPs.
 
 
 ✓ ProsFree if AWS free tier availableAWS IPs have good reputationWorks with requests library
 ✗ ConsAWS API Gateway has rate limitsSetup requires AWS accountLimited rotation speed
 
 
 

Colly ⚡FrameworkGo Go TLSMedium targets15k

 
 
 
 ⚡ HTTP
 Under the hood: Go HTTP scraping framework. Fast, concurrent, clean API.
 
 
 ✓ ProsVery fast, Go concurrency modelLow memory vs PythonGood for high-throughput HTTP scraping
 ✗ ConsGo only, no PythonSmaller ecosystem than ScrapyNo browser support
 
 
 

Katana ⚡FrameworkGo Go TLS + ChromiumMedium targets8k

 
 
 
 ⚡ HTTP
 Under the hood: Go-based web crawler by ProjectDiscovery. Designed for security research and recon.
 
 
 ✓ ProsExtremely fast Go crawlerHeadless mode with Playwright integrationBuilt for large-scale URL discovery
 ✗ ConsSecurity/recon focus, not a data scraping frameworkGo onlyLess data extraction tooling than Scrapy
 
 
 

playwright-go 🌐BrowserGo CDP (detectable)Medium targets–

 
 
 
 🌐 Browser
 Under the hood: Go bindings for Playwright. Same Playwright API in Go.
 
 
 ✓ ProsGo concurrency for browser scrapingSame Playwright API and capabilitiesLower memory than Python for concurrent sessions
 ✗ ConsLess mature than Python PlaywrightSmaller communityNo stealth patches yet
 
 
 

Charles Proxy ⚡RE ToolAny N/AMobile API intercept–

 
 
 
 ⚡ HTTP
 Under the hood: Commercial HTTPS proxy for request inspection and debugging. GUI-based.
 
 
 ✓ ProsGUI-based, easy to use for non-developersSSL proxying with certificate installSession recording and replay
 ✗ ConsPaid productFor debugging only, not automated scrapingLess powerful than mitmproxy for scripting
 
 
 

Selenoid ⚡HTTPGo (Docker) Browser-as-a-serviceMedium targets2.6k

 
 
 
 ⚡ HTTP
 Under the hood: Docker containers running headless Chrome/Firefox in parallel, Aerokube's Go-based Selenium grid replacement.
 
 
 ✓ ProsRun dozens of browsers in parallel from one hostLower memory than Selenium GridBuilt-in video recording per sessionDrop-in replacement for Selenium Grid
 ✗ ConsBrowsers still detectable as headless without stealth patchesOlder project, slower release cadenceRequires Docker infrastructure
 
 
 

noble-tls ⚡HTTPPython Chrome JA3/JA4Cloudflare, DataDome–

 
 
 
 ⚡ HTTP
 Under the hood: Python port of uTLS via custom TLS handshake stack, emits browser-matching ClientHello.
 
 
 ✓ ProsBypasses JA3/JA4 fingerprintingPure Python, no C compilationLighter than curl_cffi for simple casesEasy install via pip
 ✗ ConsSmaller community than curl_cffiFewer browser impersonation profilesLess battle-tested in production
 
 
 

hrequests ⚡HTTPPython Browser-grade TLSDataDome, Cloudflare900

 
 
 
 ⚡ HTTP
 Under the hood: Drop-in requests replacement with TLS impersonation, header order matching, and optional Playwright browser mode.
 
 
 ✓ Prosrequests-compatible API with stealth built inHeader order mimics real ChromeOptional browser mode for JS renderingBuilt-in async support
 ✗ ConsSmaller ecosystem than curl_cffiFewer impersonate profilesNewer project, some edge cases
 
 
 

crawlee-python 🌐BrowserPython Via curl_cffi backendMost targets6.2k

 
 
 
 🌐 Browser
 Under the hood: Python port of Apify Crawlee, wraps curl_cffi for HTTP and Playwright for browser modes in a unified framework.
 
 
 ✓ ProsMix HTTP and browser workers in one crawlerAuto-scaling and proxy rotation built inStorage abstraction for resultsStrong production patterns from Apify
 ✗ ConsLarger than plain ScrapyNewer than Node.js Crawlee, some features lagOpinionated framework
 
 
 

estela ⚡FrameworkPython (K8s) Spider-dependentDistributed Scrapy90

 
 
 
 ⚡ HTTP
 Under the hood: Kubernetes orchestrator for Scrapy, schedules and runs spiders as K8s jobs with auto-scaling.
 
 
 ✓ ProsOpen source alternative to Zyte CloudElastic scaling on KubernetesBuilt-in monitoring and stats UIMulti-tenant by design
 ✗ ConsRequires Kubernetes infrastructureHeavyweight for small projectsSmaller community than Scrapyd
 
 
 

fake-useragent ⚡HTTPPython UA strings onlyLightweight only3.8k

 
 
 
 ⚡ HTTP
 Under the hood: Curated database of real-world User-Agent strings, sampled from browser telemetry sources.
 
 
 ✓ ProsRealistic UA strings ready out of the boxFilter by browser family or OSUpdated databaseTiny dependency
 ✗ ConsUA alone is trivially detectable in 2026Not enough for any modern anti-botDatabase can become stale
 
 
 

grequests ⚡HTTPPython requests + geventUnprotected APIs4.4k

 
 
 
 ⚡ HTTP
 Under the hood: gevent-monkey-patched requests, fires hundreds of HTTP calls in parallel via greenlets.
 
 
 ✓ ProsDrop-in async for requests usersSimpler than asyncio for bulk fetchesBattle-tested gevent under the hood
 ✗ ConsMonkey-patching can conflict with other libsNo HTTP/2 supportNewer code should use httpx instead
 
 
 

Scrapoxy ⚡FrameworkNode.js Proxy managerSelf-hosted rotation2.1k

 
 
 
 ⚡ HTTP
 Under the hood: Self-hosted proxy pool manager, provisions proxies on AWS, Azure, GCP and rotates IPs automatically.
 
 
 ✓ ProsFree open-source alternative to Bright Data's proxy managerAuto-provision and tear down cloud IPsBan detection and auto-rotation built inMulti-tenant
 ✗ ConsCloud provider costs add up at scaleSelf-hosting infrastructure complexityCloud IPs flagged faster than residential
 
 
 

primp ⚡HTTPPython (Rust) Chrome JA3/JA4/HTTP2Akamai, DataDome–

 
 
 
 ⚡ HTTP
 Under the hood: Python Requests IMPersonate. Rust binding over a patched reqwest stack, emitting browser-matching TLS and HTTP/2 fingerprints.
 
 
 ✓ ProsRust core, tuned for throughput at volumeSets impersonate_os separately from the browser profilePrecompiled wheels for Linux, Windows, macOSSync and async clients
 ✗ ConsSmaller community and docs than curl_cffiAPI is requests-flavoured but not a true drop-inNo HTTP/3 fingerprints yet
 
 
 

impit ⚡HTTPRust / Py / JS Browser TLS + HTTP/3Cloudflare, DataDome–

 
 
 
 ⚡ HTTP
 Under the hood: Rust library built on rustls and reqwest, patched to reproduce browser TLS fingerprints without touching system libraries. Successor to got-scraping.
 
 
 ✓ ProsNow the default HTTP client in Crawlee for PythonHandles HTTP/2 and HTTP/3 browser behaviour nativelyPrebuilt binaries incl. Windows and macOS ARMAvoids the curl-impersonate build pain
 ✗ ConsYounger than curl_cffi, fewer profiles documentedBest experience is inside the Crawlee ecosystemNative binary adds a build dependency
 
 
 

rnet ⚡HTTPPython (Rust) Browser JA3/JA4Cloudflare, Akamai–

 
 
 
 ⚡ HTTP
 Under the hood: Async Python HTTP client over a Rust TLS stack that emits browser-shaped ClientHello and HTTP/2 settings.
 
 
 ✓ ProsFast async client for high-volume fetchingCommon partner to a browser in hybrid architecturesActively maintained impersonation profiles
 ✗ ConsDocumentation is thin compared to curl_cffiSmaller ecosystem and fewer integrationsNetwork layer only, no JS execution
 
 
 

zendriver 🌐BrowserPython Real Chrome stackCloudflare, DataDome–

 
 
 
 🌐 Browser
 Under the hood: Maintained CDP-driven wrapper around real Chrome with the undetected configuration handled for you, in the nodriver lineage. No Selenium or WebDriver binary.
 
 
 ✓ ProsDrives real Chrome, so the TLS stack is genuineNo ChromeDriver binary, so no $cdc_ markersActively maintained fork lineageCommon browser half of a hybrid pipeline
 ✗ ConsBrowser cost per page, slow next to HTTP clientsStill needs proxy and behaviour work at scaleChromium-only
 
 
 

scrapy-impersonate ⚡FrameworkPython Chrome JA3/JA4Akamai, Cloudflare–

 
 
 
 ⚡ HTTP
 Under the hood: Scrapy download handler backed by curl_cffi, so spiders get browser TLS and header order without leaving the Scrapy request cycle.
 
 
 ✓ ProsDrop-in handler, spider code stays unchangedPlatform spoofing across macOS, Windows, iOS, AndroidKeeps Scrapy middleware, retries and stats intactFar cheaper than bolting a browser into the spider
 ✗ ConsTracks whatever profiles curl_cffi shipsNo JavaScript renderingAnother moving part to keep in step with Scrapy versions
 
 
 

Trafilatura ⚡ParserPythonContent extraction–

 
 
 
 ⚡ HTTP
 Under the hood: Heuristic main-content extractor: strips boilerplate and returns article text plus metadata (title, author, date, language, site name) as text, markdown, or XML.
 
 
 ✓ ProsThe de-facto standard for corpus-scale text extractionUsed to build RefinedWeb and FineWebMetadata extraction alongside the body textPython library and CLI, no model required
 ✗ ConsHeuristic, so structured elements can be mangledTables, formulas and code are the weak spotsNot a fetcher, you bring the HTML
 
 
 

Resiliparse ⚡ParserPython / C++Content extraction–

 
 
 
 ⚡ HTTP
 Under the hood: High-performance HTML parsing and main-content extraction from the ChatNoir stack, built for web-scale corpora.
 
 
 ✓ ProsVery fast, designed for billions of pagesUsed to build the DCLM and Dolma corporaRobust encoding detection and DOM handlingPairs well with Common Crawl pipelines
 ✗ ConsHeuristic like Trafilatura, same structural blind spotsHeavier install than a pure-Python parserAimed at corpus work, not per-page precision
 
 
 

spider ⚡FrameworkRustHigh-volume crawling–

 
 
 
 ⚡ HTTP
 Under the hood: Async Rust crawler that discovers, fetches, and queues at very high concurrency, with a subscribe API for streaming pages as they land.
 
 
 ✓ ProsAmong the fastest open-source crawlers availableStreaming subscription model suits pipelined extractionLow memory footprint per pageComposes with Rust extractors like rs-trafilatura
 ✗ ConsRust, so a steeper on-ramp for Python teamsThinner anti-bot story than the Python stealth stackSmaller ecosystem of ready-made middleware
 
 
 

browser-use 🌐AIPython Real browser stackInteraction-heavy flows100k

 
 
 
 🌐 Browser
 Under the hood: Hands a real browser to an LLM agent that clicks, scrolls, types and reads screenshots with vision when the DOM is unusable.
 
 
 ✓ ProsHandles logins, modals and multi-step flows nativelyModel-agnostic across the major LLMsVision fallback when the DOM is a messNo selectors to maintain
 ✗ ConsSlow and token-expensive per runWrong tool for bulk fetchingNon-deterministic, needs verification on output
 
 
 

FlareSolverr 🌐ManagedTypeScript Real browser stackCloudflare IUAM–

 
 
 
 🌐 Browser
 Under the hood: Runs a browser behind a simple HTTP API, solves the challenge, and returns the rendered response, so the browser lives outside your crawler.
 
 
 ✓ ProsKeeps browser lifecycle out of the spider processOne service can serve many crawlersScales and fails independently of the scraperEasy to route only the requests that need it
 ✗ ConsAnother service to run and monitorChallenge coverage drifts as vendors updateNot a general anti-bot solution
 
 
 

DeepScrape 🌐AITypeScript Fingerprint hygieneContent extraction272

 
 
 
 🌐 Browser
 Under the hood: Self-hosted scraping API: markdown and structured extraction, self-healing CSS specs, an autonomous agent, and an MCP server exposing each saved site spec as its own tool.
 
 
 ✓ ProsGrounding-based confidence flags hallucinated fieldsSelf-healing specs re-derive selectors on driftPer-site MCP tools instead of one generic verbApache-2.0, self-hosted, runs on internal sites
 ✗ ConsNeeds an LLM key for the derivation stepsExplicitly not a CAPTCHA or anti-bot bypassSessions are in-memory, so long auth flows need tuning
 
 
 

 Yes
 Partial
 No
 HTTP/Parser/RE
 Browser
 Framework
 AI
 Managed CAPTCHA Proxy

Browser engines, deep dive#
Critical 2026 fact: CDP (Chrome DevTools Protocol) is itself detectable. Runtime.enable timing, execution context leaks, and binding exposure all signal automation. Camoufox uses Mozilla's Juggler protocol below CDP, no CDP leaks. playwright-stealth patches JS at runtime but Function.toString() exposes the patch.

 Microsoft 2020Playwright ★ 68k
 Chromium + Firefox + WebKit
 The 2026 standard framework. Powers Firecrawl, Crawl4AI, Browserbase. CDP is detectableuse C++ wrappers above Playwright. Auto-wait, network interception, multi-browser.pip install playwright && playwright install

 C++ Firefox · JugglerCamoufox ★ 100%
 Zero CDP exposure · geoip alignment
 Mozilla Juggler below CDP levelzero CDP leaks. Near-zero fingerprint surface. 100% pass rate Mar 2026 on Cloudflare, Instagram, Reddit, X. (FF135 base; stable moved to FF146 on 16 Jul 2026 — re-test). Note: Firefox ~3% market share.from camoufox.sync_api import Firefox

 Stealth ChromiumCloakBrowser
 49+ C++ binary patches
 Binary patches: Canvas, WebGL, Battery API, AudioContext, CDP input. reCAPTCHA v3 score 0.9. Passes Akamai's 60 extension probes with real extension loading. Best for Akamai-targeted Chromium sites.

 Playwright source forkPatchRight
 No JS signatures anywhere
 Patches Playwright Python source, not JS injection. Kasada fingerprints playwright-stealth via toString(). PatchRight leaves nothing in the runtime to inspect.pip install patchright

 Google · Node.jsPuppeteer ★ 89k
 Chrome DevTools Protocol
 Google's original CDP automation. puppeteer-stealth plugin patches common detection points. CDP signature still visible at protocol level. Better for rendering tasks than hard anti-bot targets.

 Multi-language · WebDriverSelenium ★ 29k
 Legacy, navigator.webdriver=true
 navigator.webdriver=true detectable in 2 JS lines. Use SeleniumBase UC mode to remove. Stock Selenium is dead against Akamai in 2026. Still valid for non-protected targets.

 Python · UC ModeSeleniumBase ★ 10k
 Undetected Chrome Mode
 UC mode removes navigator.webdriver. Auto-solves many CAPTCHAs. Good for Kasada, medium targets. Not production-safe against Akamai at scale.from seleniumbase import Driver

 Raw CDP · Async Pythonnodriver / pydoll
 Direct Chrome DevTools Protocol
 Direct CDP without WebDriver overhead. Used with Botright for CAPTCHA solving. scrapy-nodriver integrates with Scrapy directly. Lighter than full Playwright for medium targets.

 Human behaviour simulationBotasaurus
 Gaussian mouse physics
 Physically realistic mouse curves via Gaussian jitter. Combines with Patchright for protocol-level evasion + human behaviour. Effective against DataDome's 35-signal behavioural analysis.pip install botasaurus

python
from camoufox.sync_api import Firefox

# geoip=True: auto-aligns IP, timezone, locale, WebRTC simultaneously
with Firefox(
 geoip=True– # align all 5 identity vectors to proxy exit country
 humanize=True– # Gaussian mouse jitter
 proxy={"server": "http://proxy.provider.com:8011"–
 "username": "user"– "password": "pass"},
 screen={"width": 1920– "height": 1080}
) as browser:
 page = browser.new_page()
 # Warm up, never go directly to target URL
 page.goto("https://www.google.com")
 page.wait_for_timeout(2000)
 page.goto("https://cloudflare-protected.com")
 page.wait_for_load_state("networkidle")
 print(page.content()[:500])

 10 field notes on the arsenal
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Tooling
 An Escalation Ladder, Shipped as a Library Default
 The newest scrapy-stealth release adds driver="auto", and it is this guide's cheapest-first principle turned into a runtime decision. The engine attempts fast HTTP impersonation first and escalates to a real Chrome browser only when it meets a JavaScript challenge or a ban — so you pay browser prices on the pages that need a browser and request prices on the ones that do not.
 💡 The escalation is per-request, which is where the savings actually live
 Why this is the right shape. Most estates pick one tier for the whole crawl and pay its cost everywhere, because choosing per-target is manual work nobody schedules. Deciding per request, at the moment of refusal, means the expensive path is reserved for the pages that actually earned it — and on a typical mixed estate that is a small minority.The rest of the release, briefly: proxy health scoring with automatic cooldown on dead exits; behavioural mouse and scroll replay with no configuration; browser cookie handoff, so you log in once in a browser and reuse that session on the faster request path; custom DNS overrides to pin hosts to fixed IPs; and static-asset blocking to skip images, fonts and CSS on browser fetches.The one to actually look at is the cookie handoff, because it attacks the usual reason people run browsers at all. Very often the browser is not needed to render anything — it is needed once, to acquire a session. Separate those two jobs and the browser becomes a login step rather than a per-page cost.
 
 +
 Aug 2026 / Tooling
 Scrapling Has 77,000 Stars and Its “AI” Parser Is difflib
 Scrapling is the library people reach for when selectors break, and it genuinely earns its reputation — 76,946 GitHub stars, verified against the API rather than a screenshot. But the adaptive matcher marketed with language about intelligent similarity algorithms contains no machine learning of any kind. It is difflib.SequenceMatcher from the Python standard library: Ratcliff/Obershelp gestalt pattern matching, imported on line 4 of parser.py.
 💡 The default acceptance threshold for relocating your element is 40%
 How it actually works. On save, Scrapling serialises the element's tag, cleaned attributes, stripped text, tree path, parent tag/attributes/text, and sibling and child tag-name tuples. On relocation it scores every candidate node against that stored dictionary across roughly a dozen checks — tag exact match as 1 or 0, text similarity ratio, and attribute dictionaries scored as 0.5 × key-sequence ratio + 0.5 × value-sequence ratio — then normalises to round((score / checks) * 100, 2).Three consequences worth knowing before you trust it in production. First, the default threshold is percentage=40, so an element scoring 40% similarity is accepted as a match. Second, relocate() deliberately never stops early even on a perfect score, so it is O(n) across every node on the page, every time. Third, it returns a list, not a single element, so tied candidates all come back.This is not a criticism. String diffing over a rich property dictionary is a sensible, debuggable, dependency-free design, and it is why the library is fast and installs cleanly. It is a criticism of reading “intelligent” in a README and assuming a model is involved. Know the threshold, and set it yourself.
 
 +
 Aug 2026 / Tooling
 Markdown Is the Interchange Format, and Microsoft Ships the Converter
 The argument for converting before ingestion is a cost argument, not an aesthetic one. Markdown preserves headings, lists, tables, links and code blocks without HTML's structural noise, so a model processes it on materially fewer tokens — which is lower inference cost and more context window left for content that matters. MarkItDown, Microsoft's open-source Python library, is the multi-format converter: Office documents, PDFs, images, audio, EPUB, ZIP archives, HTML, YouTube URLs and structured data, through one API, with a CLI, a plugin architecture and an MCP server.
 💡 It is a converter, not a fetcher — and that distinction will bite you
 The gotcha the feature list hides. MarkItDown will happily take a URL: md.convert("https://example.com"). Under the hood that is a plain HTTP GET through Requests — no browser, no fingerprint management, no retries. Against anything with anti-bot protection it simply fails. Scrape with your own stack and hand MarkItDown the saved HTML.Two more. There is no proxy option at all; because it uses Requests, you configure HTTP_PROXY and HTTPS_PROXY as environment variables before instantiating. And YouTube conversion typically returns 429 Too Many Requests even from a fresh IP, so that feature is unusable at scale without paid residential infrastructure — a cost the docs do not mention.Practical setup. Python 3.10 or higher. pip install 'markitdown[all]' pulls heavy dependencies, so prefer selective extras — there are eleven, including [pdf], [docx], [audio-transcription] and Azure Document Intelligence — and keep the scraper container light. Plugins are disabled by default and must be enabled explicitly.
 
 +
 Aug 2026 / Lighter browsers
 Moli: Run the JavaScript, Skip the Pixels
 A headless Chrome instance sits around 773 MiB. Fifty in parallel is roughly 40 GB, and most teams simply accept that as the price of automation at scale. Moli, a new open-source Rust engine, reported 73 MiB on the same work. Ten times less.
 💡 Most agent and scraping work needs the DOM, not the paint
 The idea is on-demand rendering. By default Moli runs real JavaScript through V8, builds the DOM, and computes CSS via Servo's Stylo, then it stops. Layout and paint only happen if you ask for them with a flag, and screenshots, PDFs and media are separate toggles. Chrome pays for full layout on every page whether you needed it or not, and for a scraper or an agent consuming structure rather than pixels, that is the entire waste.On a 192-URL crawl it reported a success rate level with Chrome, 53.6% against 52.6%, at a tenth of the memory. It speaks CDP from a single binary, so existing Playwright and Puppeteer scripts connect directly.Read the caveats as stated, because they matter. The benchmarks are self-reported, there is no Chrome pixel parity, and the project is young. Note also what that success rate is telling you: roughly half of a 192-URL crawl failed for both engines, which is a reminder that the engine was never the thing deciding whether you get in.This lands squarely in the lighter-browser thread alongside Kitesurf, Obscura and Lightpanda, and the convergence is now hard to miss. Kitesurf is Blitz rendering plus Firefox's Stylo plus the Boa JS engine, built in twelve weeks on Workers, and Cloudflare credits Obscura as the inspiration. Moli uses V8 and Stylo. Everyone building for machine consumers is arriving at the same conclusion independently: keep the JavaScript engine and the DOM, throw away the rendering pipeline, and keep CDP so nobody has to rewrite their scripts.
 
 +
 Aug 2026 / Tool selection
 Two Stealth Libraries That Cover the Same Layer Are One Stealth Library
 The commonest wasted afternoon in this work: you install a second stealth library, nothing changes, and you conclude the target is unbeatable. Both libraries were patching the same layer, and the block was somewhere else. The fix is not a better library, it is knowing which rung you are standing on.
 💡 The order is IP and ASN, then TLS, then HTTP/2 — canvas and WebGL only matter once a script runs
 Stated for public pages, not accounts, which is the distinction that makes the ordering true. A site sees your IP and ASN first, then your TLS handshake, then your HTTP/2 settings. Every JavaScript-layer signal — canvas, WebGL, audio, the navigator object — is downstream of a script actually running, so a JS-layer patch cannot help with a block that happened before the HTML was served. That is the same conclusion the Akamai case study in this guide reaches the expensive way.Five tools, placed by layer rather than by popularity. curl_cffi impersonates browser TLS and HTTP/2 fingerprints from Python and runs no JavaScript, so JS challenges are exactly where it stops — and it is the cheapest thing to try, so try it first when you do not need page JavaScript. fingerprint-suite is Apify's Node toolkit: one call gives you a Playwright context where headers and JavaScript APIs are generated from a single coherent fingerprint, which is the whole game (see the coherence material above — the failure mode is spoofing one axis and leaving the others pinned). Obscura is an independent Rust and V8 engine with no Chromium underneath, currently v0.2.0, and its own notes warn that rendering and Web API behaviour can diverge from Chromium — the same structural trade as Kitesurf. TRAWL is the escalation ladder as a product: plain HTTP first, then a cached session, then a fresh browser solve, then a residential proxy, so a target that yields at rung one never costs you rung four — roughly sub-100ms, ~500ms, seconds, then tens of seconds, and it speaks FlareSolverr v2 so it drops into existing stacks. stealth-browser-mcp exposes a stealth browser over MCP, pinned to nodriver 0.47.0 — useful, and a good illustration of the last point.The closing advice is the part people skip: check the recent commits before you install any of them. In a field where the detectors ship weekly, a stealth tool's commit history is a freshness indicator, and a pinned dependency means you inherit that driver's fixes only when someone remembers to bump the pin. Read the pin as a date, not a version.
 
 +
 Trending / Browser stealth
 CloakBrowser Just Crossed +9.1K Stars in One Week
 The fastest-growing GitHub repo this week is a scraping tool. CloakBrowser, the stealth Chromium with source-level fingerprint patches, hit +9.1K stars (Nov 2026), passing 30/30 anti-bot tests. Confirms what production scrapers already knew: real browsers with patched binaries beat stealth plugins.
 💡 Drop-in Playwright replacement, no JS patches to detect
 Stealth plugins (puppeteer-extra-stealth, playwright-stealth) overwrite JS properties at runtime — which is itself a detection signal. Anti-bot scripts can see the override happen. CloakBrowser patches Chromium's C++ source directly so the fingerprint is real at the binary level. There's no override to detect. The recent velocity (+9.1K stars in one week) was driven by it passing every public anti-bot test suite including Sannysoft, Antoinevastel, Creepjs, and Bot.sannysoft. Its closest analogue is Camoufox (Firefox-based, same approach with Juggler) but CloakBrowser uses Chromium so it works with existing Playwright pipelines. The tradeoff: maintenance burden. When Chrome ships a new version, CloakBrowser has to patch and rebuild. The team has been keeping up so far. github.com/CloakHQ/CloakBrowser
 
 +
 Benchmarks · 2026
 The 30-Point Gap: Browser Scraping Success Rates Are Not Equal
 71 protected sites tested: Browser Use Cloud hit 81%Browserbase hit 42%. That gap is no longer marginal, it is the difference between a working pipeline and a broken one.
 💡 A 30-point gap in success rate = the difference between a working pipeline and a broken one
 The benchmark tested 71 sites protected by Cloudflare, Akamai, PerimeterX, and DataDome. Methodology: each provider was given identical target lists and measured on first-request success (no retries). Browser Use Cloud succeeded on 81%, achieved via custom Chrome patches at the C++ binary level plus coordinated fingerprint management. Browserbase succeeded on 42%, detected primarily via CDP timing signatures and canvas hash consistency. The gap exists because basic scraping (fetch URL, parse HTML) is commoditised. The data worth having in 2026 sits behind login walls, search interfaces, and multi-step authenticated flows requiring actual browser interaction. Cheap providers are adequate for unprotected targets; they fail silently on protected ones.
 
 +
 Python Framework
 Scrapling v0.4: The Biggest Python Scraping Update Yet
 New async spider: concurrent crawling, mix HTTP and stealth sessions, pause/resume from checkpoint, stream items live. Thread-safe ProxyRotator built in. Handles Turnstile natively.
 💡 pip install scrapling --upgrade
 The async spider framework uses a Scrapy-like API: define a Spider class, set start_urls, implement parse(). Key differentiators from Scrapy: mixed session types in one spider (HTTP fetchers, headless Camoufox, stealth browser), checkpoint/resumeCtrl+C saves state, restart continues from last position, per-domain throttlingset different rates per target. ProxyRotator: thread-safe, works across all fetcher types, supports custom rotation strategies, per-request override. Parser improvements: blocked_domains list to block tracking/CDN requests in headless mode, automatic proxy-aware retry on network errors, Response.follow() for easy link chaining. Install: pip install scrapling --upgrade.
 
 +
 Library Analysis
 Scrapling Hit 200K Views, Invest in Your Network Layer
 Detection vendors update, bypass libraries break, new ones ship. This cycle repeats every few months. What never depreciates: your proxy infrastructure. Invest there first.
 💡 Invest more in your network layer, it depreciates slower than your library
 The library lifecycle in scraping works like this: a new bypass technique is discovered, someone publishes a library implementing it, the library becomes popular, detection vendors add the library's fingerprints to their models, the library gets blocked, repeat. This cycle runs on a 2-4 month cadence for fast-moving targets. Your proxy setup operates on a different timeline: a well-configured residential proxy pool with good IP diversity and correct session management continues to work across multiple library generations. The specific libraries come and go but the network signals (IP reputation, ASN, session behaviour, timing patterns) remain consistent requirements. Conclusion: spend more engineering time on proxy quality, session management, and IP pool diversity than on tracking the latest bypass library.
 
 +
 Pattern · Tooling ergonomics · 2026
 The curl Command as a Front-End to a Real Browser
 A recurring 2026 pattern wraps a real browser behind the interface developers already know: you write an ordinary curl command and a tool parses it, replays the same method, headers, body, cookies, and auth inside an actual Chromium driven over the automation protocol, clears the browser-side friction (Cloudflare's managed challenge, a Turnstile widget), and hands back the final response. The point is not a new bypass, it is that the browser-grade request is expressed as the one-liner you would have written anyway.
 💡 The ergonomic win is meeting people at the curl command, not asking them to rewrite their workflow as browser automation.
 
This is worth noting as a shape, not a single product. The reference implementation in this lineage is CurlWright (open source, github.com/seifreed/Curlwright): it parses a curl invocation, supports the common flags (-X, -H, -d/--data-*, -b cookies, -u auth, -x proxy, -L, -k, --max-time), and runs it through a genuine Chrome rather than a spoofed fingerprint. Two design choices generalise beyond it.

Stealth below the automation layer. Rather than faking a browser with injected JavaScript, the current generation of these wrappers drive the real Chrome binary through a patched automation stack (Patchright-style) so the automation tells (the Runtime.enable CDP leak, navigator.webdriver, headless markers) are neutralised at the protocol level, and falls back to a CDP-free driver (nodriver-style) for the hardened "Just a moment" managed challenge. This is the same lesson the detection and library sections of this guide keep arriving at: on protocol-fingerprinting targets, driving a real browser without the standard automation surface beats patching a headless one. Persisting and re-importing the solved-cookie session (so a warmed-up cf_clearance can be reused across calls) is what makes the curl ergonomic actually practical at more than one request.

Machine-readable output for pipelines. The other generalisable idea is emitting structured JSON and even SARIF alongside the response, so a browser-grade fetch can drop straight into CI and security tooling that already understand those formats. It is a small thing that quietly moves browser-based fetching from an interactive task to a scriptable pipeline step. The honest constraint of the whole pattern is the one its own authors flag: because it drives a real Chrome, it needs that browser present on the host and carries the cost and weight of a full browser per protected request, so it earns its place on the hard targets, not as a default replacement for a plain HTTP client on everything.

Pattern reference: CurlWright (seifreed, open source), 2026, plus the Patchright and nodriver engines it builds on. Described here as a tooling-ergonomics pattern, not an endorsement of any one tool.

---

## AI-POWERED SCRAPING

06 AI & LLM Scraping
Describe, don'tselect
AI-native scraping replaces CSS selectors with natural language. A 2025 NEXT-EVAL benchmark showed LLMs hit F1 > 0.95 on structured extraction when input is properly formatted.

 2026 Market Shift
 Why AI scraping matters now
 Firecrawl's markdown output uses 67% fewer tokens than raw HTML, compounds significantly at thousands of pages for RAG pipelines. AI web scraping market: $7.5B → $38B by 2034 (CAGR 19.93%). LangChain, LlamaIndex, and CrewAI all have native integrations. Claude and Cursor can scrape the web via MCP tools with zero code. A server like the Decodo MCP is the worked example: it gives the model a scraping tool that returns Markdown, JSON, or screenshots for JS-heavy pages with no proxy rotation or anti-bot handling on your side, which is exactly the shape a RAG or research pipeline wants.

 Firecrawl ★ 111k
 Managed · Self-hostable · FIRE-1 · MCP
 Send URL → clean Markdown/JSON. No selectors. MCP serverClaude scrapes via natural language. FIRE-1 agent autonomously navigates. /interact endpoint clicks, fills forms, extracts behind dynamic content. SAP, Zapier, Deloitte.app.scrape(url) | app.crawl(site) | app.search("query")
 ✓ LangChain + LlamaIndex native · 500 free/mo

 Crawl4AI ★ 60k
 Open-source · Local LLM · Full control · MIT
 "Scrapy for the LLM era." Runs on your infrastructuredata never leaves your servers. Adaptive crawling learns selectors over time. BM25 content filter. Plug in Ollama for local models or OpenAI/Deepseek.result = await crawler.arun(url)
 ✓ Full data sovereignty, free, MIT license

 ScrapeGraphAI ★ 18k
 NL prompts · Graph pipeline · Self-healing
 Describe what you want, LLM builds and executes a graph-based extraction pipeline. Self-healing: site structure changes, re-describe and it adapts. No selectors ever written. Supports OpenAI, Claude, local.SmartScraperGraph(prompt="...", source=url)
 ✓ Best for: schema-free exploration, prototyping

 webclaw
 Rust · Chrome TLS · 10 MCP tools · 95.1% accuracy
 Rust-native scraper built for AI agent integration. 10 MCP toolsClaude and Cursor can call it directly via natural language. 95.1% success rate on bot-protected sites. Zero Python overhead, runs as subprocess or HTTP service. Chrome-level TLS fingerprinting baked in.pip install webclaw
 ✓ Best for: AI agents needing high-performance scraping + MCP integration

 Jina Reader API
 URL → clean text · Zero code
 Simplest LLM scraping tool. r.jina.ai/{url} is the entire API. Returns clean Markdown. Dynamic content handled via built-in rendering. Free tier available, paid ~$0.002–$0.01/page.
 ✓ Best for: text extraction, no-code integration

 Steel
 Open-source · Docker · MCP · AI agents
 Self-hostable headless browser API for AI agents. MCP serverClaude controls browsers directly. Session persistence + CAPTCHA auto-solve. <1s session start. LangChain/CrewAI integration.
 ✓ Best for: AI agents needing browser control

 Browserbase
 Managed cloud · $300M valuation
 50M sessions in 2025. Playwright/Puppeteer drop-in, one endpoint swap. Session recordings + CAPTCHA auto-solve. Used by AI agent frameworks as the browser layer. From $50/mo.
 ✓ Best for: AI agent infrastructure at scale

python
import asyncio
from crawl4ai import AsyncWebCrawler
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from pydantic import BaseModel

# Define exactly what you want, LLM extracts it, no selectors needed
class Product(BaseModel):
 name: str
 price: float
 model_number: str
 brand: str

async def extract(url):
 strategy = LLMExtractionStrategy(
 provider="openai/gpt-4o-mini"–
 schema=Product.model_json_schema(),
 extraction_type="schema"–
 instruction="Extract all products with prices and model numbers"
 )
 async with AsyncWebCrawler() as crawler:
 result = await crawler.arun(url=url– extraction_strategy=strategy)
 import json
 return json.loads(result.extracted_content)
# F1 > 0.95 on well-structured pages, NEXT-EVAL benchmark 2025

 
 
 MCP Server · 55 production tools · MIT
 Crawilfy github.com/razavioo/crawilfy-mcp-server
 
 The full scraping stack as an MCP server — gives Claude Code, Cursor, and Codex 55 production tools for the complete crawling pipeline.
 
 What it ships:
 • Stealth via curl_cffi TLS impersonation + rotating proxies
 • Auto-discover REST + GraphQL endpoints on any site
 • Record a flow once, export it as a runnable Python crawler
 • Smart extraction with any OpenAI-compatible LLM (free tiers + local Ollama work)
 • MIT licensed
 
 One command: uvx crawilfy-mcp-server
 
 Why this matters: at $0.002–0.01 per request, commercial scraping APIs compound fast on any non-trivial AI agent. Crawilfy brings the full stack in-process: TLS impersonation, proxy rotation, LLM extraction, all from within your IDE. The alternative is paying per-request at scale.
 
 
 GitHub ↗
 PyPI ↗
 
 

From extraction to production-grade data#
Crawl4AI and Firecrawl get you semantic understanding out of an LLM. But ask an LLM for a price across 10,000 articles and you will get $40, 40 dollars, 40 USD, "forty dollars", and occasionally null. Production pipelines cannot ingest that. The fix is to separate the two concerns LLMs conflate: semantic understanding and structural guarantees.

 
 
 The pattern · Pydantic + Instructor + LLM
 Schema-validated LLM extraction
 
 Define a Pydantic schema for what you want. Pass it to the LLM via Instructor (which patches OpenAI, Anthropic, Mistral clients to return validated schema objects, not free text). Instructor handles retries when the LLM returns malformed output, and rejects responses that fail validation before they enter your pipeline.
 
 # pip install instructor pydantic anthropic
from pydantic import BaseModel, Field
import instructor, anthropic

class JobPosting(BaseModel):
 title: str
 company: str
 salary_min_usd: int | None = Field(description="Floor of salary range in USD")
 salary_max_usd: int | None
 years_experience_min: int
 location: str
 remote: bool

client = instructor.from_anthropic(anthropic.Anthropic())
result = client.messages.create(
 model="claude-sonnet-4",
 response_model=JobPosting,
 messages=[{"role": "user", "content": scraped_html}],
 max_retries=3,
)
# result is a validated JobPosting object, not a string
# If LLM hallucinates "competitive" for salary, Instructor retries
 Why this beats raw LLM calls: normalises currencies, units, and phrasings ("just under two percent" → 1.8); rejects hallucinated dates that don't fit the schema; retries automatically on type errors; gives you a real Python object downstream.
 
 

 
 
 When classical NLP still wins
 spaCy, NLTK, Stanford NLP at scale
 
 LLMs cost $0.001–0.01 per article on Claude or GPT-5. Classical NLP costs effectively zero after model load. For 10M+ document extraction at scale where the schema is narrow and the domain is stable, spaCy NER + dependency parsing still wins on cost.
 
 Use classical NLP when: scraping millions of consistent documents (e-commerce, classifieds), schema is fixed, domain doesn't shift, latency budget is <5ms per doc.
 
 Use LLM + Instructor when: messy heterogeneous sources (news, newsletters, job boards), context disambiguation matters ("Apple" the company vs the fruit), schema may evolve, semantic equivalences need resolving ("FTE" = "full-time" = "permanent" = "direct hire").
 
 Hybrid in production: classical NLP pre-filters and tags. LLM resolves only the ambiguous cases. This is what Bloomberg, Reuters Refinitiv, and FactSet actually do, not pure LLM pipelines.
 
 Sources and further reading: Federico Trotta, The Web Scraping Club, May 2026; Instructor library.
 
 

 The production failure mode nobody warns you about: LLMs hallucinate plausibly. If your scraped article doesn't mention a publication date, an LLM will sometimes invent one that fits the article's tone. A Pydantic date | None field with Instructor's retry logic catches this, the LLM has to either find a real date or return None. Without schema validation, fabricated dates pass into your database as facts.

The framing to keep your head straight: AI agents did not replace scraping, they became its biggest new client. When half the industry is writing the eulogy for web scraping, it is worth noticing that an agent which browses the web is doing exactly what a scraper does. It makes HTTP requests, it hits anti-bot walls, it gets rate limited, it needs proxies and a believable fingerprint. The only thing that changed is who writes the prompt. The tell is in the spend: teams shipping agents bought more proxy and unblocking infrastructure through 2025, not less, because every agent loop ends in the same place a scraper's does, at a server that would rather not serve it. Everything in this guide applies to your agent the moment it leaves the sandbox.

Making LLM extraction production-reliable: the parts that are engineering, not prompting#

Asking a model to read a page and return the fields is the easy 20 percent. A team running AI-generated scrapers across hundreds of partner sites reported the number that matters: first-attempt LLM selectors fail to yield any data roughly 30 to 40 percent of the time. The model is strong at pattern recognition and has no way to check its own guess against the real DOM, so the reliability lives entirely in the scaffolding around the model, validation, diagnosis, cleaning, and a bounded retry loop. Three pieces of that scaffolding are worth stealing outright.

 
 
 1 · Steer the model toward durable selectors
 The selector-stability hierarchy
 Left alone a model reaches for whatever selector matches, which is often a hashed layout class (div.qElViY from a Wix or Chakra build) that changes on the next redeploy. Instruct it to prefer selectors in order of durability: (1) JSON-LD structured data, a declared schema contract, (2) data-testid attributes, added for automation and rarely changed, (3) id attributes, (4) semantic elements (h1, time, address, article), (5) itemprop / schema.org attributes that are part of a public SEO contract, (6) named platform class prefixes (tn-, ot_), and only last (7) visual styling classes, the first thing to break on a redesign. The higher the selector sits, the longer it survives, so a self-healing loop that prefers the top of this list heals far less often.
 
 
 
 2 · Do not pour the whole DOM into context
 A cleaned page, then a DOM-exploration agent
 A single listing page is often 200 to 400 thousand characters, and sending that on every retry is slow, expensive, and sometimes larger than the context window. First pre-clean: strip <style>, <svg>, <noscript>, and inline scripts (but keep <script type="application/ld+json">, that is data, not code), which typically shrinks the HTML three to five times. When even the clean page will not fit, do not truncate blindly, give the model targeted tools over the DOM instead, downloading the HTML to a file and exposing helpers like count_selector, dom_excerpt, and find_repeating_blocks so it can answer questions about structure without loading the whole document. And separate the two jobs: the model produces the selectors, your deterministic code does the actual field extraction, so the expensive model never has to see every row it is pulling.
 

3 · Diagnose the failure before you retry. The worst response to a selector that returned nothing is to resend the same prompt and hope. Most extraction failures fall into a few recognisable classes, and naming the class turns a blind retry into a targeted fix: extracted too little (selector too specific, matched a fragment), extracted too much (matched a container with surrounding noise), wrong DOM region (matched a real element, but the venue name in the footer instead of the event header), attribute-versus-text confusion (pulled an href when you needed the link text), and template mismatch (the sample pages the model saw do not represent every template on the site). Feeding back "the selector is too narrow, find a parent element" produces a far better second attempt than "try again", and it is the diagnostic layer that the circuit-breaker and correction-loop from the architecture section depend on. The same source also flags two JSON-LD traps worth pre-empting: server-side frameworks sometimes inject HTML comment markers (<!---->) inside the script tag that make json.loads fail silently, so strip comments before parsing, and JSON-LD can be present on some pages of a site and absent on others, so a CSS fallback is mandatory rather than optional.

Serving scraped data to an agent: the naming problem replaces the scraping problem#

There is a structural shift worth noting for anyone wiring web data into an agent rather than a pipeline. The old pattern was to write a tool wrapper around each scraper, hand-write its schema, teach the model when to call it, and keep fixing the wrapper every time a site changed. Exposing the scraping capability as an MCP server collapses that layer: the agent connects once and asks in plain language, and the model selects the right endpoint out of a large catalogue and gets structured JSON back, with no bespoke wrapper to break. The non-obvious consequence, reported by people who have built these, is that the hard problem moves. It stops being "how do I scrape this" and becomes "which of these hundreds of endpoints does the model reach for", which means clear, disambiguating tool names and descriptions start mattering more than the scraping code underneath. It is the same lesson as the selector hierarchy one level up the stack: when a model is choosing, the quality of what it chooses from, and how legibly it is labelled, is the thing you actually engineer.

One concrete answer to that naming problem, plus three extraction patterns worth copying. An open-source (Apache-2.0) implementation, DeepScrape, ships a pattern that resolves the "which of hundreds of endpoints" question rather neatly: instead of exposing generic verbs, you save a named site spec (the fields you want plus the derived selectors for one site), and each saved spec is published as its own MCP tool, so the agent discovers a specific, typed tool for the job rather than a generic scrape verb it has to aim. Because the spec is deterministic once derived and re-derives itself when the site drifts, it is the self-healing pattern and the naming fix in one object. It also runs on your own infrastructure, which is what makes it usable against internal or authenticated targets by binding a spec to an already-logged-in session, with the credentials staying yours.

 
 
 Stop hallucination being silent
 Grounding-based confidence signals
 The most valuable idea in that codebase for anyone doing LLM extraction: after the model returns fields, check each value back against the source text deterministically and report what you found. A field that is present in the output but cannot be located in the page is flagged suspect (a probable hallucination); a field that is absent or empty is flagged missing (an omission). That single distinction converts the two failure modes that usually pass silently into machine-readable signals you can gate a pipeline on, and it costs nothing but a string search. It pairs directly with the promotion-gauntlet idea from the self-healing section, the model proposes, but something deterministic decides whether to trust it.
 
 
 
 Three smaller patterns
 Pruning, discovery, and drift
 Link-density pruning: when converting a page to clean markdown for a model, score each DOM node and use link density as the key signal to strip nav, footers, and boilerplate while preserving headings, tables, and code, which cuts tokens without the blunt damage of a readability heuristic. Automated hidden-API discovery: rather than hunting XHR calls by hand in DevTools, load the page once in a real browser, record every fetch it makes, and return the JSON-ish endpoints, turning the guide's find-the-backend-API advice into a repeatable step. Change tracking: diff a page's main content against its previous scrape and return a status of new, same, or changed with the added and removed lines, which is the cheap way to watch for the site drift that breaks extractors before it silently corrupts your data.
 

Worth noting the posture as much as the features, because it is a good model for how to talk about this work. That project states its limits explicitly in its own documentation: the fingerprint work is described as hygiene rather than warfare (consistent UA, platform, locale, timezone, and WebGL, plus patching the obvious automation leaks) and it says plainly that it does not solve CAPTCHAs, does not defeat the major anti-bot vendors, is not a residential unblocker, and honours robots.txt, pointing you at a purpose-built anti-detect browser if that is what you actually need. It also documents where its own session model is weak rather than hiding it. Tools that tell you where they stop are more useful than tools that imply they never do, and the same standard is worth applying to anything you evaluate, including the vendor benchmarks discussed in the cost section.

 Q2 2026 Landscape · mapped by Massive
 The agentic browser stack is 8 layers, not one product#
 "Browser agent" sounds like a single tool. It's a stack. Most teams building AI agents account for one or two layers; the reliable ones map all eight. When an agent fails a task, the cause is usually not the framework everyone debates, it's one of the other seven layers nobody mapped. The proxy layer sits at the bottom and every layer above it still has to reach the live site.

 

 
 
 Layer 1 · Cloud Browser Platforms
 Hosted headless browsers at scale
 Managed Chrome/Chromium in the cloud, no infra to run. Browserbase, Kernel, Notte, Anchor Browser, Browserless, Hyperbrowser.
 

 
 
 Layer 2 · Agent Frameworks & SDKs
 Natural-language task → browser actions
 Browser Use, Stagehand, Skyvern, AgentQL, Dendrite, Nanobrowser. These translate "log in and download my invoices" into clicks and form fills.
 

 
 
 Layer 3 · Browser Automation
 The execution primitives
 Playwright, Puppeteer, Selenium, Crawlee (by Apify), BrowserMCP. Every layer above eventually calls down to one of these.
 

 
 
 Layer 4 · Computer Use Agents
 Models that see the screen and act
 Anthropic Computer Use, OpenAI Operator, Fellou, Twin, MultiOn. Vision-driven, they screenshot the page and decide the next click rather than reading the DOM.
 

 
 
 Layer 5 · Stealth & Anti-Detection
 Where most agent tasks silently fail
 CloakBrowser, Steel, Lightpanda, Pydoll, Camoufox. A flawless agent framework with a detected browser fingerprint still gets blocked.
 

 
 
 Layer 6 · Data Extraction & Enrichment
 Page → structured records
 Diffbot, ScrapingBee, ScraperAPI, Zyte, ScrapeGraphAI. Turn the rendered page into clean JSON.
 

 
 
 Layer 7 · LLM-Optimized Crawling
 Crawl output formatted for models
 Crawl4AI, Firecrawl, Jina AI, Apify, LLMScraper, Scrapy. Markdown/clean-text output that costs fewer tokens downstream.
 

 
 
 Layer 8 · Network & Proxy Layer
 The foundation everything depends on
 Massive (affiliate), Bright Data, Oxylabs, Smartproxy, NetNut, IPRoyal. A perfect stack with a flagged datacenter IP fails at the first request.
 

 

 
 How to use this map when debugging: When an agent fails, don't start with the framework (Layer 2), that's where everyone wastes time. Check Layer 5 (is the browser fingerprint detected?) and Layer 8 (is the IP flagged?) first. The boring layers fail more often than the clever ones. Credit: landscape mapped by Massive, Q2 2026.
 

 5c New in Aug 2026
 When an agent writes your scraper ten failure modes, from reading one end to end#
 The toolchains that let an LLM build a production scraper for you stopped being demos this year. The most complete public example is Zyte's open-source claude-skills plugin (v0.2.3, fifteen SKILL.md files): a staged pipeline that explores a site, negotiates a schema with you, generates a scrapy-poet project with real page objects, and deploys it to Scrapy Cloud. I read the whole repository, ran it, and wrote up thirty-four findings. The bugs themselves will be fixed and are not the interesting part. The shape of the failures is, because it is not specific to one vendor. If you let an agent build scrapers, in any framework, these ten are the ones to check for.

 
 Credit where it is due, because it frames the rest. The page-object architecture underneath is the right call and it is why the output is repairable at all. Extraction lives in a class decorated with @handle_urls(domain) and typed Returns[ItemClass], separate from the crawl loop; the generated project ships an executable web-poet fixture suite; the schema is negotiated with a human before any code is written. A pipeline that gets the architecture right and the instrumentation wrong is a far better starting point than the reverse. Everything below is about the instrumentation.
 

 

 
 1 · Recon reads the DOM, not the traffic
 The cheap path is never even looked for
 A real Chromium launches and no page.on("response") listener is registered anywhere in the repository. The entire harvest is one line: html = await page.content(). Every JSON, XHR and GraphQL response the page fetched to build itself is discarded the moment it is used. The HTML cleaner then strips <script> before the model sees anything, which deletes __NEXT_DATA__, __NUXT__ and self.__next_f — the exact places modern sites park their data.Why it generalises: the stage that chooses between a private JSON endpoint and a headless browser is the stage that has been made blind to the endpoint. The browser decision is committed before anything could have argued against it.Rule: recon captures the network, not the document. If your agent cannot show you the response bodies it saw, it did not choose between HTTP and browser — it defaulted.
 

 
 2 · Neither browser rung scrolls
 The spec is built from the first viewport
 Viewport is pinned at 1280×720. The local rung waits for networkidle and snapshots; the only scroll-related call sizes the screenshot afterwards. The hosted rung sends {"browserHtml": true, "screenshot": true} with no actions array, so the provider's own scrollBottom and waitForSelector are never requested. Grep the repo for mouse.wheel, scroll_into_view or wait_for_selector and you get nothing.Why it generalises: on any infinite-scroll listing the agent negotiates a schema against the twelve items it happened to see, and the spec looks perfectly reasonable.Rule: the recon fetch must be at least as capable as the production fetch. Prove it on a lazy-loaded page before you trust a generated spec.
 

 
 3 · Two robots.txt policies, never reconciled
 Exploration ignores it; the deliverable obeys it
 Grep the whole repository for robots and you get exactly two lines: the downloader sets "ROBOTSTXT_OBEY": False, and the generated project template sets ROBOTSTXT_OBEY = True. Nothing fetches robots.txt while the spec is being built, nothing checks the captured URLs against it, and nothing warns about the mismatch.Why it generalises: the spec is built from pages the shipped spider may be forbidden to fetch, and you learn that in production, on someone else's schedule.Rule: pin one policy across recon and production and assert it at spec time. A legal posture that changes between the prototype and the deliverable is not a posture.
 

 
 4 · The recon identity outguns production
 You tested with a browser and shipped a library banner
 Recon drives real Chromium. The generated spider announces Scrapy/<version> (+https://scrapy.org), because nothing sets Scrapy's USER_AGENT or DEFAULT_REQUEST_HEADERS on the default path — the one user-agent constant in the settings file is provider attribution, not target-facing identity — and transparent mode is off by default. No Accept-Language, no browser Accept. Combined with ROBOTSTXT_OBEY = True, the very first request to every new domain is a robots.txt fetch from that identity.Why it generalises: every agentic toolchain uses a heavy client to look and a light client to run. The gap between them is where the block lives.Rule: whatever built the spec must not be more capable than what runs it. Test the fingerprint the deliverable actually ships with.
 

 
 5 · Truncation becomes ground truth
 A context-window trim silently redefines the site
 The link extractor keeps the true total, then ships group_links[:max_links]. The next stage iterates the truncated list and writes only {url, text} — the count is discarded entirely, so nothing downstream can detect that truncation happened even in principle. That shortened list is copied into the values file, then into the spec, then verbatim into the test fixtures.Why it generalises: every agent pipeline trims lists to fit a context window. The bug is not the trim, it is that the trim leaves no scar.Rule: carry the true count next to every sample an LLM is shown, and fail loudly when the sample and the count disagree.
 

 
 6 · The test oracle is model-authored
 Green means the extractor agrees with itself
 The one human-verified artefact in the whole pipeline is the corrected example values you approve during schema negotiation — the moment you say "price should be without the currency symbol". That correction is persisted correctly as schema examples and routed into the extractor as context. Then nothing ever validates against it: the value extractor matches on field name only and never reads examples, and the fixture converter stamps the model's own output as the assertion target. It also writes the whole values dict, including fields the page object was never asked to produce, because it is never told which fields the page object emits.Why it generalises: a suite where the model writes both the code and the expected output is a regression detector, not a correctness test. Useful, but it will happily lock in a wrong answer.Rule: the human-corrected value is the only ground truth an agentic pipeline has. Wire it into the assertion, or admit you have no test.
 

 
 7 · Coverage has no baseline
 Only zero percent has a mechanical verdict
 The single mechanical rule in the trust layer is "flag any expected field at 0% coverage". Everything between 0% and 100% is left to judgement: download a few items where the field is missing and decide whether it is legitimately absent. Search the repository for threshold, baseline, previous job or expected items and you get nothing.Why it generalises: a run that falls from 40,000 items to 4,000 with every field populated passes every check. That is the most common real failure in production scraping and it is invisible to a level-based test.Rule: coverage is a delta, not a level. Compare against the last good run and alert on the derivative.
 

 
 8 · Items carry no provenance
 Careful during recon, dropped on the way out
 The spec side is meticulous: the metadata file records URL, captured_at, http_status, full headers, final_url on redirect, and the backlinks a page was discovered from. None of it survives into the output. The item class is built purely from the schema, the spider yields await page.to_item() and nothing more, and there is no item pipeline hook that could stamp anything afterwards.Why it generalises: three months later the price field goes null and the only question that matters is "which URL, fetched when, with what status". If the row does not carry it, the answer is a re-crawl.Rule: every row ships source URL, fetch timestamp and HTTP status. It costs three columns and it is the difference between a diagnosis and a shrug.
 

 
 9 · The unattended watcher lies when it fails
 "Cannot reach the API" reported as "still running"
 The background job poller only assigns its last-seen job on a successful poll. If every poll fails — wrong endpoint, mistyped job key, a purged job returning an empty list — it prints an empty timeout after the full twenty minutes, and the skill then instructs the agent, unconditionally, to tell the user the job is still running and offer to keep waiting. Three exception classes are also outside the retry handler and kill the poller outright.Why it generalises: this is the classic monitoring inversion, and agentic pipelines make it worse because the agent narrates the result to a human in confident prose.Rule: "no news" and "could not ask" must be different states with different messages. If they collapse, your monitoring is a comfort blanket.
 

 
 10 · The toolchain is a credential surface
 The part people forget to threat-model
 The cloud API helper takes the API URL verbatim from the command line and attaches Authorization: Basic <b64(apikey)> to whatever host it names — no scheme check, no host check, no allowlist; grep for urlparse, netloc, hostname or allowlist across the repo returns zero validation hits. Separately, the human-approval channel is a local HTTP server whose do_POST reads the body and writes it straight to the output path: no Origin check, no Host check, no shared secret, no size cap, no content-type check, and its logger is stubbed out. The only gate is an ephemeral port written in plaintext into a JavaScript file.Why it generalises: an agent that reads web pages can be argued with by a web page. If it can also be handed a URL that its credentials follow, the injection has somewhere to go.Rule: allowlist the hosts your credentials are permitted to touch, and treat any local approval channel as an unauthenticated endpoint on the user's machine, because that is what it is.
 

 

 
 One more that is worth naming separately, because it is the most expensive kind. A shortcut in the spec stage promotes a list-page-only spec when every requested field appears somewhere in a list-page sample — and in the default invocation that gate is vacuously true, because the flag it depends on is only set for fields matching explicit hints nobody passed. The result is a spec that quietly drops detail-page extraction from the run. Elsewhere, the code generator is documented to read four files, and the output module is not one of them: it overwrites a module it never read, including the @handle_urls(domain) decorator and Returns[ItemClass] base that a previous step had carefully constructed with libcst. Both are the same class of defect — a later stage that does not read what an earlier stage produced — and it is the defect agentic pipelines are structurally most prone to, because each stage is a fresh context that only knows what it was handed.
 

 
 Strategy · what to do instead
 
 Make the network log the recon artefact. Not the HTML. A recon run that ends with a HAR, or at minimum a list of every JSON response over a few hundred bytes, lets the strategy decision be argued rather than defaulted. This single change flips findings 1 and 2 at once.
 Test for properties, not for examples. The sharpest statement of the model-authored-oracle problem I have seen came from someone whose agent shipped 127 passing tests and a clean linter, including a test named after the exact promise it was violating: two distinct items never collide. The function was not injective, and the suite never looked, because it exercised the hostile inputs somebody thought of rather than searching for a counterexample. A green suite is evidence that the cases you encoded ran, and nothing more. Where a field carries a real property — uniqueness, monotonicity, a total ordering, a stable key — property-based testing searches the space instead of sampling your imagination, and it is the one addition that makes a machine-authored suite mean something.Make the human correction executable. Wherever a person corrects a value, that corrected value must become an assertion, not a comment and not context. If your pipeline has exactly one human-verified fact in it, spend it on the test.
 Emit provenance by default, strip it later if you must. Source URL, fetch time, HTTP status, and the selector or page-object version that produced the row. Four fields. They are the entire difference between debugging and guessing.
 Alert on the derivative, not the level. Item count versus the last good run. Field fill-rate versus the last good run. Response-code mix versus the last good run. A threshold against zero catches the failure that was never going to be subtle.
 Keep one identity policy end to end. The same user agent, the same header set, the same robots posture in recon and in production. Where they must differ, make the difference explicit and asserted, so nobody discovers it from a 403.
 Treat every stage boundary as a lossy channel. The characteristic agentic bug is a later stage reading a truncated, reformatted or regenerated version of what an earlier stage knew. Carry counts alongside samples, carry file hashes alongside paths, and have the last stage assert that what it received still matches what was produced.
 Threat-model the agent's own tools. Host allowlists on anything that carries a credential; authentication on any local approval server; a size cap on anything that writes to disk. The agent reads untrusted text for a living.
 And keep the good part. Page objects, a negotiated schema, an executable fixture suite and a surgical repair path are genuinely the right architecture for machine-authored scrapers. The lesson from reading this end to end is not that agents cannot build scrapers. It is that they build the code well and the instrumentation badly, and the instrumentation is what you will be living with.
 
 

 6 field notes on AI and LLM extraction
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Worked build
 A Costed RAG Ingestion Pipeline, With the Refresh Problem Actually Solved
 This guide has argued for a while that a RAG system is bounded by its acquisition layer — a vector store indexed six months ago is a model with a stale cutoff. Here is a full worked pipeline for the collection half, including the two parts most write-ups skip: how it stays current, and what it costs.
 💡 Incremental sync by content hash, and a live fetch when the index is known stale
 The gap it starts from is that a cloud provider's native web crawler for its own knowledge-base product handles static sites only — it breaks on JavaScript-rendered pages, on bot protection, and on geo-restricted content. Which is a neat statement of why this guide exists: the managed ingestion tool assumes the easy half of the web.The shape. An unblocking API fetches clean Markdown from pages that would otherwise be unreachable, lands it in object storage with metadata sidecars, embeds it, and stores vectors. Then the parts worth stealing: incremental syncs driven by content hashing so unchanged pages are not re-embedded, cited answers via retrieve-and-generate so a claim can be traced to a source, a golden-set evaluation script so a change to the pipeline can be scored rather than eyeballed, and hybrid routing that falls back to a live fetch when the best retrieved chunk falls below a relevance floor.That last one is the design idea, and the trigger matters. It is not a staleness clock — it is the index admitting, per query, that it does not hold a good enough answer. Most RAG builds treat the index as the truth and quietly serve the closest thing they have, however far away that is. Scoring the best chunk and routing past the index when it is too weak makes freshness a runtime decision rather than a cron job's problem, and it degrades to a live fetch instead of to a confident wrong answer.Reading the repository rather than the announcement adds the parts worth stealing. Change detection hashes the raw scrape, not the stripped body, so cosmetic boilerplate churn does not force a re-embed, and last-seen is tracked in a separate ledger from content age. A render check exists as its own step because it has to: on a JavaScript-only page a plain GET returned 96 characters and rendering returned the whole thing. Boilerplate stripping removed 21% of characters batch-wide. Near-duplicates go through MinHash/LSH. A reconcile step retires URLs that have dropped out of the source list, which is the unglamorous half of freshness nobody builds. The golden set scores hit@k and MRR rather than vibes — the six-page pilot reported hit@5 of 100% and MRR 0.88.And it is costed, which is rarer than it should be: a six-page pilot with weekly refreshes runs about four cents a month. Small enough to be a toy, but the unit is right — cost per page per refresh is the number that decides whether an index of ten thousand pages is a line item or a project. Vendor-authored, so read the tooling choice accordingly; the architecture is independent of it.
 
 +
 Aug 2026 / RAG and retrieval
 Your RAG Pipeline Is Only as Good as the Layer Nobody Budgeted For
 The argument, stated plainly: the quality of a RAG system is bound entirely by the quality of its retrieval layer. Everything downstream — the embedding model, the chunking strategy, the reranker, the prompt — is working on whatever the acquisition layer handed it, and cannot recover what was never collected.
 💡 A vector store indexed six months ago is just an LLM with a stale cutoff
 Four failure modes, and the third is the one that quietly ruins corpora.Freshness. A static knowledge base decays. Index six months ago and you have rebuilt the exact knowledge-cutoff problem RAG existed to solve, only now you own the infrastructure too.Coverage. Search engines index a fraction of the public web. Recently published pages, low-authority domains, anything dynamically rendered, and anything behind an anti-bot are invisible to search-backed retrieval. If your pipeline gets its documents through a search API, your corpus has the shape of someone else's index, including its blind spots.Extraction fidelity. Feeding raw HTML in means navigation, footers and cookie banners enter the corpus as content, and in the author's phrasing that is noise no chunking strategy can fully recover from. Boilerplate does not just waste tokens, it competes for similarity with the text you wanted.Chunking is downstream of all of it. Poor source text produces semantically incoherent chunks, which lowers retrieval precision, which looks like a model problem and is not.The practices that follow. Tiered crawl schedules matched to how fast each source actually changes, rather than one global interval, so breaking news is hourly and reference pages are weekly. Version-controlled vector stores, so a retrieval result is reproducible and auditable months later. Extract to structured JSON or Markdown, never raw HTML. Treat freshness as an SLA you can be held to rather than a vague intention. And use purpose-built scrapers for the sources that matter to you instead of outsourcing coverage to a search integration.This is the same economics as the cost-per-usable-document material in the cost section, arriving from the AI side: the expensive failure is never the fetch, it is discovering months later that what you collected was incomplete, stale or full of furniture. Full piece
 
 +
 Aug 2026 / Agent economics
 The Accessibility Tree Turns Out to Be the Cheapest Selector Nobody Uses
 Most browser agents already read the accessibility tree, and it buys them almost nothing, because they still run the same loop: look at the page, ask a model what to do, click, look again. A team at Evinced tried using the semantics to make the behaviour deterministic instead, and measured it across 300 tasks on 136 live sites.
 💡 Roles and accessible names survive redesigns that kill CSS and XPath
 Two mechanisms, both boring in the good way. Pre-defined skills: accessibility standards already specify how a dialog, a menu or a combobox is supposed to behave, so an agent that recognises the pattern can operate it as one atomic action with no model round trip. Picking an option from a custom dropdown goes from screenshot → DOM → LLM call → click → screenshot → DOM → LLM call → click, six steps and two round trips, down to: read role=combobox with accessible name "Fare", then selectOption("Economy"). One step, zero round trips. Learned skills: roles and accessible names stay stable across sessions in a way generated selectors do not, so a successful run can be cached against them. A ten-step flight search on united.com collapses into find_flights(origin, destination, dates) and later runs just fill in the parameters.Against Browser-Use on the Online-Mind2Web benchmark: 5.4× faster and 3.2× cheaper in tokens on average. The combined price-performance number is the one worth sitting with — roughly 32×, not 17×, because the two gains are positively correlated: the flows where you cut the most tokens are the flows where you cut the most time, since both come from deleting the same round trips. The distribution is heavily right-skewed, with about 11% of flows over 100×. Sites that score well on accessibility checks gave much bigger gains.Why this belongs in a scraping guide rather than an accessibility one. The thing that makes agent automation expensive is not the browser, it is the model call between every action, and the accessibility layer is a pre-existing, standardised, semantically-labelled index of the page that most scrapers ignore in favour of selectors they have to repair. If you run agent-driven extraction at any volume, the role/accessible-name pair is a selector strategy with a maintenance profile better than CSS and a cost profile better than vision. The honest caveat, from the authors: what an agent needs from a page is a subset of what a person needs, so agent-readiness does not mean a site is actually accessible. Full writeup in The AI Journal
 
 +
 Concept / Shift in thinking
 The Web Is Becoming a Real-Time Database for AI Crawlers
 Classic scraping: crawl on a schedule, store in a database, query the database. With cheap residential proxies, a new pattern emerged: don't store anything. The target website is your database. Scrape on demand, treat every page load as a query. For AI crawlers especially, why save data when you can fetch it live whenever you want?
 💡 Resproxies made scraping cheap enough to skip storage entirely
 This is a genuine shift in how scraping infrastructure gets designed. The old model assumed scraping was expensive and risky, so you scraped once, stored aggressively, and served queries from your own database. That meant stale data, storage costs, and sync logic. Residential proxies changed the economics: scraping became cheap and reliable enough that for many use cases you can just hit the live site every time a user (or an AI agent) asks. The website hosts the data, you treat it as a remote database, and the latency tradeoff is acceptable because the customer is willing to wait a few seconds for fresh answers instead of getting instant but stale results. For AI crawlers and agentic workflows this is even more pronounced, there's no point caching a price or a flight time when the agent can re-fetch it at query time. The implication for anti-bot teams: scraping volume is no longer bounded by "how often do they refresh their DB", it's bounded by "how often does a user ask", which can be far higher and far spikier.
 
 +
 RAG / LLM Pipelines
 Keep Your LLM Context Fresh: Incremental Indexing
 Scraped data goes stale fast. CocoIndex builds a continuously updated vector indexonly changed rows re-run. Pgvector, LanceDB, Neo4j targets. #1 GitHub Trending on launch.
 💡 github.com/cocoindex-io/cocoindex, incremental RAG for LLM agents
 The core problem: you scrape a site, embed it into a vector store, and 48 hours later 30% of the content has changed. Traditional batch re-indexing re-processes everything. CocoIndex solves this with a Rust-based delta engine: it tracks byte-level lineage per document, and when you re-run it only processes changed chunks. Target vector stores: Pgvector (PostgreSQL), LanceDB (local), Neo4j (graph). Python with Rust core means the delta calculation is very fast even on large corpora. The LLM integration: your agent always queries a fresh index, so answers reflect current scraped data. Setup: pip install cocoindexconfigure sources (files, URLs, S3), define your chunker and embedding model, run cocoindex.build()done in under 10 minutes.
 
 +
 Pattern · Self-healing scrapers · June 2026
 The LLM as Compiler and Oracle, Not Runtime
 A self-healing scraper design where the LLM compiles a cheap deterministic crawler once, then your pages run at zero model cost. When a redesign breaks the crawler, the model serves that one request live to avoid an outage while it regenerates a new crawler in the background. The hard part is not the regenerating, any model can rewrite a broken scraper. The hard part is trusting the rewrite before it ships.
 💡 The moat is not the heal. It is the proof that the heal is right.
 

 
 
 
 
 
 
 
 
 
 
 
 
 
 

 
 CRAWLOOP: SELF-HEALING VIA COMPILE, NOT RUNTIME
 the LLM compiles a cheap crawler and judges its replacements; it is not in the hot path

 
 
 LLM: COMPILER + ORACLE
 writes the crawler · is the source of truth

 
 
 DETERMINISTIC CRAWLER
 $0 / instant · no model calls

 
 
 EXTRACTION PIPELINE
 free, reproducible data

 
 
 WEBSITE LAYOUT CHANGE
 break detected

 
 
 compiles once
 
 
 
 
 breaks it

 
 
 break detected

 
 
 LLM SERVES THE REQUEST LIVE
 0 downtime while it regenerates
 

 
 
 PROMOTION GAUNTLET
 a regenerated crawler is trusted only if it passes all of these

 
 
 regenerate in bg

 
 
 ORACLE
 agrees EVERY
 item ≥0.98

 
 RECORD
 count must
 match exactly

 
 SAMPLES
 holds across
 ≥3 runs

 never a mean
 no missed rows
 no lucky page

 
 
 TIERED MODEL ESCALATION
 cheap model default → strong only if stuck

 
 
 
 TRUST
 score

 
 
 FAIL
 regenerate, try again

 
 PROMOTE → DEPLOY
 new crawler runs at $0 again

 
 
 
 
 promoted crawler replaces the broken one, back to $0 deterministic extraction

 
 Proven live against a real public site over real HTTP: cheap model failed the gauntlet, auto-escalated, promoted a crawler
 that then extracted a fresh page (title, price, stock, URL, image) for $0 with zero model calls.

The architecture treats the LLM as two roles at once. As a compiler it turns a target into cheap deterministic extraction code that runs for free. As an oracle it is the source of truth a regenerated crawler must agree with before it is trusted. Between them sits the piece that makes the whole thing safe to run unattended: a promotion gauntlet.

How a regenerated crawler earns promotion:
• It must agree with the LLM oracle on every item, not on average. A high mean is not good enough; one disagreement fails the batch.
• It must return the exact same record count as the oracle pass.
• It must hold across at least three independent samples, so a lucky single page cannot promote a broken crawler.

Tiered model escalation keeps the cost sane. A cheap model runs the regeneration by default; the loop only escalates to a stronger model when nothing clears the gauntlet. Whatever gets promoted is still free deterministic code, so the one-time model cost amortises across every later run.

This matters because "self-healing scraper" demos are easy and trustworthy self-healing is hard. Any model can produce a plausible rewrite. The engineering is in the verification that decides whether the rewrite is correct before it touches production data, which is the same adversarial-verification idea this guide's AI Workflow is built around. It pairs naturally with the agentic reverse-engineering shift in the next card: the model drives the toolchain, but a gauntlet, not the model's confidence, decides what is true.

The proof that separates it from a demo: the author ran the whole loop live against a real public site over real HTTP, not a saved fixture. The cheap model could not clear the gauntlet, so the loop auto-escalated to a stronger model, promoted a crawler, and that promoted crawler then extracted a fresh page (title, price, stock, URL, cover image) for zero cost with zero further model calls. That is the claim that matters: not "an LLM fixed my scraper" but "the system decided, on its own, that the fix was trustworthy enough to run unattended, and it was right." The honest weak spot is in the open too: holding a greedy oracle to exact agreement across a twenty-item listing is hard, and that is where it currently strains.

Pattern and live proof of concept shared publicly by the author of Crawloop (Apache-2.0 alpha POC, github.com/Jimmynycu/Crawloop), June 2026. Honest status from the README: a working POC without the managed proxies, scale, or dashboards the funded self-healing tools (Kadoa, ScrapeGraphAI, and others) ship.

---

## SCRAPING PLATFORMS AND SERVICES

07 Managed platforms
When DIY costexceeds platform cost
If spending more than 2 engineer-days/month on anti-bot maintenance, a managed platform is cheaper. Crossover typically hits when facing F5 Shape or Kasada at scale.

 Bright Data 98.44%
 Enterprise · 72M+ IPs · Scrape.do #1 2025
 Highest success rate in Scrape.do 2025. 100% on Indeed, Zillow, Capterra. 72M+ residential IPs. GDPR, ISO 27001. Scraping Browser for JS-heavy targets. $1.50/1K requests.
 ✓ Best for: F5 Shape, hard targets at scale
 brightdata.com ↗
 

 Zyte 93.14%
 #1 Proxyway 2025 · Fastest API · Scrapy
 #1 Proxyway 2025, 93.14% success rate. Fastest API response. Smart Proxy auto-selects type. GPTE AI generates parsers from natural language. scrapy-zyte-smartproxy integration.
 ✓ Best for: Scrapy pipelines, speed
 zyte.com ↗
 

 Firecrawl ★ 111k
 AI scraping · Self-hostable · MCP
 URL → Markdown/JSON. No selectors. MCP server, Claude scrapes via natural language. LangChain + LlamaIndex native. Used by SAP, Zapier, Deloitte. 500 free/mo.
 ✓ Best for: RAG pipelines, AI agents
 firecrawl.dev ↗
 

 Crawl4AI ★ 60k
 Open-source · Local LLM · Free
 89.7% OOTB success rate. Runs on your infrastructure. Local LLM support (Ollama). MIT license. Adaptive crawling. Full data sovereignty, data never leaves your servers.
 ✓ Best for: privacy, open-source, cost $0
 crawl4ai.com ↗
 

 Apify FEATURED · NO-CODE OPTION
 Serverless cloud · 10,000+ Actors · MCP · LangChain
 
 
 The best option if you don't want to build scrapers yourself. 
 Apify is a cloud platform where scraping is already done for you, 10,000+ community-built 
 Actors cover almost every major website: Amazon, LinkedIn, Instagram, Google Maps, 
 TikTok, Zillow, Twitter/X, Google Search, and thousands more. You pick an Actor, give it a URL, 
 and get back clean JSON. No Python, no proxies, no infrastructure.
 

 
 
 🎭 What is an Actor?
 An Actor is a serverless scraping programme that runs on Apify's cloud. Think of it like a function: you pass it inputs (URL, keywords, max results) and it returns data. Someone else wrote the spider, handles the anti-bot bypass, manages proxies, and keeps it updated. You just call it.
 
 
 💰 How pricing works
 You pay in Compute Units (CU). One CU = 1 CPU core for 1 hour. Most Actors use 0.1–0.5 CU per 1,000 results. Free tier: $5/mo creditenough for casual use. Paid plans from $49/mo. You can also run your own code as Actors and monetise them on the marketplace.
 
 
 🤖 Apify + AI agents
 Apify has a native MCP serverplug it directly into Claude, Cursor, or any LangChain agent. Your AI agent can call "scrape this URL", "search Google for X", or "get all reviews for this product" as natural language tool calls. No code required on the LLM side.
 
 
 🏗️ For engineers who do build
 Apify's open-source Crawlee library (formerly Apify SDK) is the core of many Actors. You can build your own Actor locally with Crawlee, push it to Apify, and run it on their infrastructure with built-in proxy rotation, auto-scaling, and a dataset API. 15K+ GitHub stars.
 
 

 
 
 ✓ Use Apify when
 
 You need data from a well-known site quickly
 You don't want to maintain scrapers long-term
 You're building an AI agent that needs live web data
 You want someone else to handle anti-bot bypasses
 You need to scale without managing infrastructure
 
 
 
 ✗ Build your own when
 
 Your target site has no existing Actor
 You need custom data transformation logic
 You're scraping at very high volume (cost)
 You need full control over request patterns
 Data stays internal and can't touch third-party cloud
 
 
 

 
 Quick start
 apify.com/store
 Crawlee (open source)
 MCP server
 ~$0.25/CU
 Free $5/mo tier
 Python + Node.js SDK
 
 
 ✓ Best for: ready-made scrapers, LangChain

 Oxylabs
 Enterprise · 100M+ IPs · OxyCopilot AI
 100M+ IPs, 195 countries. OxyCopilot AI generates parser code from natural language. Owns ScrapingBee (acquired 2025). ISO 27001 + GDPR. From $49/mo.
 ✓ Best for: enterprise scale, AI-generated parsers
 oxylabs.io ↗
 

 
 ScrapingBee
 Headless · Managed rendering
 Handles JS rendering, CAPTCHAs and proxies. Simple REST API, pass a URL, get back HTML or screenshots. Good for teams that want managed scraping without infrastructure. Free tier available.
 scrapingbee.com ↗
 

 
 Scrapfly
 Anti-bot · AI extraction · Monitoring
 Premium scraping API with built-in anti-bot bypass, JS rendering, and AI-powered data extraction. Strong on hard targets. Includes scraping monitoring and scheduling out of the box.
 scrapfly.io ↗
 

 
 Diffbot
 AI extraction · Knowledge graph
 Uses computer vision and AI to automatically extract structured data from any webpage, no CSS selectors, no XPath. Builds a knowledge graph from scraped content. Best for unstructured web data that needs AI parsing.
 diffbot.com ↗
 

 
 WebScraper.io
 No-code · Chrome extension
 Point-and-click scraping via a Chrome extension, select elements visually, define pagination, export to CSV. No coding required. Cloud version runs scrapers on schedule. Best for non-technical users.
 webscraper.io ↗
 

 
 Browse.ai
 No-code · Monitor · Robots
 Train a robot to scrape any website in 2 minutes by clicking on the data you want. Monitors for changes, sends alerts. Handles login flows, pagination, and dynamic sites. No code needed at all.
 browse.ai ↗
 

 
 Browser Use
 AI agent · LLM-controlled browser
 Open-source library that lets LLMs control a real browser. The AI agent navigates, clicks, fills forms and extracts data from instructions in natural language. 81% success rate on anti-bot benchmarks. GitHub ↗
 browser-use.com ↗
 
 
 Stagehand v3 · OCT 2025
 AI Browser SDK · Browserbase · Open source
 Browserbase's AI browser automation framework. Four primitives: act(), extract(), observe(), agent(). Write browser flows in plain English ("click submit button") that survive page redesigns via runtime LLM resolution. Built on CDP, supports OpenAI/Anthropic/Gemini. 65% Mind2Web benchmark. Self-healing + auto-caching. TypeScript and Python.
 browserbase.com/stagehand ↗
 

 
 Kadoa
 AI · Zero-config · Auto-adapt
 AI-powered scraping that requires zero configuration, no selectors, no rules. Understands page structure automatically and adapts when sites change. Ideal for scraping at scale without maintaining spider code.
 kadoa.com ↗
 

 
 ScrapeGraphAI
 LLM · Graph pipeline · Open source
 Builds a graph-based extraction pipeline from a natural language prompt. Describe what data you want, it generates the scraping logic. Open source and self-hostable. Good for rapid prototyping of complex extractions.
 GitHub ↗
 

 
 TinyFish
 AI · Structured extraction · Fast
 AI-native scraping API focused on speed and structured data output. Pass a URL and a schema, get back clean typed JSON. Handles JS rendering and basic anti-bot. Good fit for feeding structured data into AI pipelines.
 tinyfish.io ↗
 

 
 Nimble
 AI · Structured · E-commerce
 AI-powered web data platform with pre-built pipelines for e-commerce, SERP, and social. Returns structured data with no parsing needed. Built-in proxy network. Strong for retail intelligence and price monitoring.
 nimbleway.com ↗
 

 
 NetNut
 ISP · Residential · Scraping API
 ISP-level residential proxy network with a built-in scraping API. Direct carrier connections for lower detection risk. Strong for e-commerce and SERP scraping where IP freshness and session stability matter.
 netnut.io ↗
 
 
 Infatica
 Scraper API · SERP · P2B sourcing
 Singapore-based provider with both a P2B residential proxy network and a managed Scraper API (POST URL → HTML/JSON, auto-retry, optional render mode for JS-heavy pages, dedicated SERP endpoint with Google AI Overview support). The differentiator vs Zyte/Bright Data/ScraperAPI: explicit peer-to-business IP sourcing, mandatory KYC on buyers, ISO 27001/27701/22301/20000-1 certified. Smaller pool than the giants but priced below them, with a 5,000-request trial. Worth a look when ethics and certification matter as much as raw scale.
 infatica.io ↗
 

 
 Scraping Robot BY RAYOBYTE
 Scraping API · 5,000 free/month · JSON output
 Plug-and-play scraping API from Rayobyte. Returns clean JSON, handles cookies + headers + browser attributes automatically. 5,000 free scrapes/month on signup, paid tiers from $5/GB. Built on Rayobyte's proxy network and rayobrowse stealth browser. Lower entry barrier than Bright Data or Zyte for teams wanting "scraping as a service" without infrastructure.
 scrapingrobot.com ↗
 

The next CAPTCHA frontier is liveness, and it is being defeated the same week it ships#

The reason the visible puzzle is fading is that behavioural scoring has run into a wall. When most of your traffic is automated and the best of it imitates a person convincingly, a test built on watching for human signals stops separating anyone from anyone. So the frontier is moving to liveness: proof that a real, live body is present at the moment of the check. In June 2026 Google began testing a hand-gesture reCAPTCHA inside Google Cloud Fraud Defense. It asks for camera permission, records a short clip of you waving or holding up an open palm, and uses a hand-tracking model to extract 21 knuckle-landmark coordinates, deleting the video afterward. On paper it is a much harder challenge for a script than reading warped text.

In practice it was passed with a still image within days of surfacing. A journalist and independent testers fed a plain stock photo of a hand through a virtual camera (OBS Virtual Camera presents any video file or image to the browser as if it were a real webcam) and the check accepted it. No deepfake, no generated animation, no AI bot reading the page, just a static picture routed through the camera input, and the whole thing automatable in minutes with a small script. The lesson is the one this section keeps returning to: the challenge is only as strong as the integrity of the channel it arrives on, and a browser cannot vouch that the pixels on its camera input came from a physical lens rather than a file.

 
 
 Why a static image gets through
 The attack is injection, not forgery
 Liveness systems fail at the point where the media enters the pipeline, not at the recognition step. A virtual-camera driver sits between any media file and the browser's getUserMedia stream, so the model never sees a lens, it sees whatever frames you hand it. This is a presentation or injection attack, and it is the same class of bypass that has dogged face liveness for years (masks, screen replays, injected deepfake video). A hand is, if anything, easier to synthesise than a face, with fewer micro-expressions to get right. Reported virtual-camera and deepfake liveness-bypass attempts rose sharply through 2025-2026 as the tooling commoditised, with some injection kits priced around the cost of a coffee.
 
 
 
 What it means for a scraper
 The friction grows faster than the gap closes
 For most scraping you will never trip a camera challenge, because the way to beat the visible test is to never summon it: keep the trust score high with clean sessions and a coherent fingerprint so the hard challenge stays dormant. Liveness raises the ceiling of pain for the cases that do escalate, but it does not close the underlying gap, and it adds real friction and privacy cost for genuine humans. The structural pressure points the other way entirely: bots increasingly skip the rendered interface and hit the JSON API directly (a measurable and rising share of automated traffic in 2025-2026), where there is no camera prompt to inject into at all. The verification ritual gets longer at the front door while the data keeps leaving through the side.
 

One caveat worth stating plainly: the vendor here is uniquely placed to harden this over time, owning the dominant browser, the hand-tracking models, the reCAPTCHA scoring layer, and a mobile OS, which is exactly the stack you would need to detect virtual-camera injection through capture-integrity and device-attestation signals. The current stock-photo bypass is an early-rollout gap, not proof the approach is permanently hopeless. The durable point is the principle: a liveness check is only as trustworthy as its ability to prove the media came from a real sensor, and that proof is the hard, unfinished part.

 5b Adjacent category
 Computer Use Agents when scraping isn't enough#
 A new category emerged in 2025: AI agents that don't just scrape, they log in as the user, navigate any UI (web apps, legacy portals, desktop software), handle MFA and CAPTCHAs, and return structured JSON. Different from scrapers because the user grants permission, "Plaid for any website." If your problem is utility bills, payroll exports, e-commerce backends, or any portal without a public API, this is the category.

 
 
 Deck FEATURED · $25M RAISED
 Computer Use Agents · Credential Vault · SOC 2
 Plaid-ifies any website. Provisions isolated desktop VMs, encrypts credentials in Deck Vault, runs AI agents that log in, navigate, and return schema-validated JSON. Founded by the team behind Flinks (Canadian open-banking, acquired for $150M by National Bank). Connects to 100,000+ utility providers across 40+ countries. Handles MFA, CAPTCHA, device fingerprinting, audit-logged sessions. Strong on regulated portals with no public API.
 deck.co ↗
 

 
 Skyvern
 Open source · LLM + Computer Vision · 85.8% WebVoyager
 YC-backed open-source agent that uses LLMs and computer vision (no XPath or CSS selectors) to operate any browser workflow. State-of-the-art 85.8% on WebVoyager benchmark. Used for invoice retrieval, job applications, government forms, insurance quotes. Both cloud-hosted and self-hostable SDK with Playwright integration.
 skyvern.com ↗
 

 
 Bytebot
 SDK · AI browser automation
 SDK-first computer use agent platform. Lighter footprint than full VM solutions, integrates into existing apps. Targets developer workflows where you want agentic browser actions without managing browser pools yourself.
 bytebot.ai ↗
 

 
 CloudCruise
 Browser automation · Web agents
 Developer platform for creating and managing web agents. Focuses on production-grade browser automation infrastructure. Competes with Deck and Browserbase on the infra layer.
 cloudcruise.ai ↗
 

 
 Autotab
 Enterprise AI agent · Data + form automation
 General-purpose AI agent for enterprise, data collection, form filling, executing actions across business apps. Pitched at operations teams rather than developers.
 autotab.ai ↗
 

 
 Browserless
 Managed headless Chrome · CDP-as-a-service
 Chrome-as-a-service over WebSocket and REST. Foundation layer that other agent platforms build on. Strong for teams that want managed browser pools without the agent reasoning layer on top.
 browserless.io ↗
 
 
 
 
 When to pick this category over scraping: if the data lives behind a login the user owns (their utility bill, their bank statement, their payroll), Computer Use Agents are the right answer, the user permission model gives you a clean legal posture and access to data scraping legally cannot reach. If the data is public-facing (e-commerce listings, SERPs, social), traditional scraping is faster and cheaper.
 

 3 field notes on platforms
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Measured
 Two Posts, One Day Apart, and a Working Method For Reading Any Success Rate
 One practitioner published four reasons not to trust a quoted success rate. The next day a vendor published a benchmark putting itself first on success rate by fifteen points. Read together they are the best worked example this guide has of how to judge a number somebody else produced.
 💡 Four questions: who defined success, who picked the targets, at what request rate, and does the ranking survive per site
 The four choices, none of which you made. First, the vendor defines success, and usually it means the request came back — but a challenge page returns 200, a login wall returns 200, and a page whose price sat in a JavaScript variable nobody parsed also returns 200. “98% success” can mean 98% of requests returned something; you hear 98% of the prices you need. Second, somebody chose the target list: in one 2026 benchmark of eleven APIs against fifteen protected sites, Shein averaged 21.88% across every provider tested and G2 36.63%. Blend those with Amazon and Google and the headline describes neither. Third, test conditions move it — in that same report one API fell from 84.47% to 72.98% purely by raising the request rate. Same tool, same targets, twelve points. Fourth, rankings invert per site: on G2, ScraperAPI succeeded where Oxylabs, Decodo and ScrapingBee failed, despite carrying one of the lower blended scores in the test. The “worse” tool was the only one that worked there.Now score the vendor benchmark against those four. It ran 15 providers over 99 targets, 5 attempts each, 7,425 billable requests, reporting a failure-aware per-target p75 latency. Results: String 97.0% at 9.98s, Scrapfly 82.0% at 18.35s, Context.dev 79.2%, Firecrawl 78.6% at 9.21s, Bright Data 78.0% at 26.14s. And to its real credit, the harness, the 99 target URLs, the pass criteria and every provider adapter are published, so anyone can re-run it. That is a higher bar than almost any benchmark in circulation and it deserves saying plainly.What still needs holding in mind. The vendor authored the benchmark it comes first in, by fifteen points. The pass criterion — question one, the one that decides everything — lives in the repository rather than in the write-up, so the headline travels without it. The post claims 2 to 10× better cost effectiveness while the benchmark contains no cost analysis at all, which is a separate claim wearing the benchmark's authority. And the comment thread under it is three people from the same company. None of that makes the numbers wrong. It means reproducible and disinterested are different properties, and this is the first without being the second.The rule that falls out. A published success rate is a claim about somebody else's target list at somebody else's request rate under somebody else's definition of success. The only number that describes you is the one you produce on your own sites at your own volume — which is exactly what an open harness makes possible, and the best reason to want one.
 
 +
 Aug 2026 / Buy versus build
 The Vendor Question Was Never “Can It Run JavaScript”
 Firecrawl in 2026 is no longer a page-to-markdown API: it carries crawl, search, structured extraction, browser interaction, monitoring, an agent endpoint and proxy escalation. That capability growth moves the buy-versus-build line rather than settling it, and the post names where the line actually sits.
 💡 Compare on accepted records and completed refreshes, never on raw requests
 The dividing question. Not whether the API can render JavaScript — it can — but who owns the gap between a fetched page and an accepted business record. A managed service fits when its standard workflows pass on representative sources and your team can own validation, orchestration and downstream retries. A custom engine fits when each source needs different behaviour under one shared business schema, with monitoring, repair ownership, and an SLA defined around accepted output rather than successful fetches.The denominator is the part to steal. Cost compared on accepted records and completed refreshes rather than raw requests, because — as the post puts it — that denominator changes many vendor comparisons. It is the same unit this guide's cost section argues for and the same one on the API-versus-scrape scorecard in §01: a request you paid for that produced a record you cannot trust is worse than no request, because somebody downstream acted on it.Why the reframing matters more than the vendor. Feature lists converge — every managed platform will eventually claim crawl, extract, monitor and an agent endpoint. What does not converge is who is accountable when a source silently changes shape at 3am. Choose on where that accountability sits, then check the price per accepted record, and the feature matrix stops mattering almost entirely.
 
 +
 AI Visibility · 2026
 Top 10 Scraping APIs per ChatGPT + Perplexity + Gemini + Google
 All four AI models queried simultaneously. Consensus: 1. Bright Data · 2. Zyte · 3. ScrapingBee · 4. Firecrawl · 5. Scrape.do. Half of B2B buyers now start research in AI chatbots.
 💡 AI search is a real channel, Bright Data #1 across all four models
 The research methodology: a scraping API was used to query four AI systems simultaneously from a San Francisco IP address (to simulate a US-based B2B buyer). Prompt: "best web scraping API 2026". Results aggregated by occurrence and ranking position. Full ranking: 1. Bright Data (98.44% success, 72M+ IPs), 2. Zyte (93.14%, #1 Proxyway benchmark), 3. ScrapingBee, 4. Firecrawl (111K GitHub stars, LLM-optimised), 5. Scrape.do, 6. ScraperAPI, 7. Apify, 8. Scrapingdog, 9. Oxylabs, 10. Scrapfly. The AI search SEO implication: if you are building a scraping product, being in AI training data and AI search indexes is now a primary distribution channel. The buyers searching "best scraping API" increasingly ask an AI chatbot, not Google.

---

## PROXY PROVIDERS AND TYPES

08 Proxy strategy
IP type mattersmore than provider
Rotating proxies is table stakes. The real variable is IP type, datacenter IPs score near-zero on DataDome and PerimeterX regardless of fingerprint quality.

DatacenterTrust: Very Low
 AWS/GCP/Azure ranges. Instantly flagged by DataDome and PerimeterX. Cheapest (~$0.01/GB). Use only on non-protected public data. Never for Akamai or PerimeterX targets.

ResidentialTrust: High
 Real home ISP addresses. Passes most trust checks. Confirmed: curl_cffi + residential bypasses DataDome on Grainger.com. Rotate per session, not per request, mid-session rotation = Akamai block.

Mobile / 4GTrust: Highest
 T-Mobile, Vodafone, O2 carrier IPs. Highest trust score on DataDome and PerimeterX. Shared tower IPs, hard to flag. Mobile IPs get DataDome 200 OK where residential fails. ~$10–15/GB.

ISP / StaticTrust: High
 Static residential range. Akamai multi-request scoring rewards consistent IPs, trust accumulates from same ISP IP. Best for long sessions on Akamai sites. Never rotate mid-session.

NetNutISP Direct
 ISP-based infrastructure with direct carrier connections. Lower detection risk than pooled residential. Fast and stable, good for e-commerce targets that check IP freshness and session age.

IPRoyalResidential
 Ethically sourced residential + datacenter. Pay-as-you-go pricing, no long-term commitment. Good entry point before scaling to enterprise contracts with Oxylabs or Bright Data.

 
 
 
 
 Massive
 I use this
 Ethical · Founded 2018
 
 Residential · ISP · Web Access API · Web Render API · MCP Server
 
 Try Massive with my referral ↗ (affiliate)
 

 
 
 "I've tested a lot of proxy providers across my 7 years in scraping. Massive stands out for two reasons: the ethics are real (not marketing), and the performance numbers hold up under actual load. 99.87% US success rate and 0.52s response time aren't made up, my production runs match that. If you care about running a clean, compliant operation, this is where I'd start."
 Asad Ikram, Data Engineer
 

 
 
 
 Residential Proxies
 1.6M+ IPs, 195+ countries. 99.87% US success rate. 0.52s response time. GDPR + CCPA compliant, AppEsteem certified. From $4.9/GB.
 
 
 ISP Proxies
 Static residential IPs for sticky, session-bound workflows. 100% success rate, 0.09s response (US). From $1.8/IP. Best for continuous monitoring.
 
 
 Web Render API
 Full JavaScript rendering with anti-bot bypass at scale. From $8/mo. Handles Cloudflare-protected pages without you managing browsers.
 
 
 MCP Server ✦ new
 Official MCP server. Use Massive directly from Claude, Cursor, or any MCP client. Geo-targeted search, bulk extraction, SERP analysis without leaving your AI workflow.
 
 

 
 
 
 99.87%
 US success rate
 
 
 0.52s
 response time
 
 
 195+
 countries
 
 
 99.9%
 uptime
 
 
 100%
 ethically sourced
 
 
 <20%
 fraud score (US)
 
 

 
 
 Verified target success rates
 
 Instagram 96%
 Amazon 94%
 Google 88%
 ISP 100%
 Trusted by Snowflake · Shopee · Tavily
 
 

 
 
 Startups get 1TB free for 3 months, no equity required. 24/7 live support. GDPR + CCPA compliant. AppEsteem certified.
 Join with my referral ↗ (affiliate)
 

RayobyteEthical · Multi-type
 "America's #1 proxy provider" (formerly Blazing SEO, est. 2014). 40M+ residential IPs across 100+ countries, plus ISP, datacenter, and mobile. Non-expiring bandwidth sets them apart, $3.50/GB residential dropping to $0.50/GB at 5TB+. Ethically sourced via Cash Raven consent-based proxyware. Ships rayobrowse stealth browser too. Hands-on technical CEO, partners with EWDCI for ethics standards.

ScrapoxyOpen Source Manager
 Self-hosted proxy manager that pools and rotates proxies across AWS, Azure, GCP. Routes requests through different IPs automatically. Free alternative to commercial proxy managers. github.com/fabienvauchelles/scrapoxy

BytefulResidential · ISP
 Residential and ISP proxies pitched at scraping engineers who understand the full detection stack. Honest positioning: "proxies solve IP reputation, but TLS fingerprint, header order, browser behaviour, and request cadence all have to line up independently." Good entry point for teams that already have their TLS and browser layers handled.

InfaticaResidential · Mobile · P2B network
 Singapore-based provider that runs an explicit peer-to-business (P2B) sourcing model: they pay app developers to embed their SDK on idle user devices instead of buying SDK installs through opaque intermediary chains. Smaller pool than the giants (~15M IPs vs Bright Data's ~150M, Oxylabs' ~175M) but with city, ZIP, and ASN filtering you can combine, and they claim to not resell the pool to other providers. Distinctive for the market: mandatory KYC on buyers, which is unusual and reduces abuse on the network. Pricing sits below the premium tier (subscriptions from $0.30/GB, pay-as-you-go from $8/GB). Worth a look when you want ethical sourcing and stricter buyer vetting without paying Bright Data prices, accepting that filter coverage by country is thinner than the larger networks.

WebRTC coherence rule: Proxy IP country, WebRTC ICE candidate, DNS resolver, timezone, and Accept-Language must all agree. US residential proxy + Pakistani DNS = flagged by every major anti-bot. Use geoip=True in Camoufox to align all five vectors automatically.
Crawlera/Zyte proxy bug: Port 8011 speaks plain HTTP. Both http:// and https:// keys must use http:// scheme. Using https:// causes BoringSSL WRONG_VERSION_NUMBER (TLS-over-TLS failure). Fix: "https": "http://key:@proxy.crawlera.com:8011/"

python
from curl_cffi import requests
import time– random

session = requests.Session(impersonate="chrome124")

# Crawlera/Zyte: BOTH keys use http://, never https://
PROXIES = {
 "http": "http://apikey:@proxy.crawlera.com:8011"–
 "https": "http://apikey:@proxy.crawlera.com:8011"– # http:// not https://
}

def fetch(url– retries=3):
 for i in range(retries):
 try:
 r = session.get(url– proxies=PROXIES–
 timeout=30– verify=False) # verify=False: proxy cert
 if r.status_code == 200: return r
 if r.status_code in (403–429):
 time.sleep(2**i + random.uniform(0–1))
 except Exception as e:
 print(f"Error: {e}")
 return None

Rotate sessions, not IP addresses: stickiness is the strategy#

The most expensive rotation mistake is the one that feels most thorough: buy the biggest IP pool you can, rotate the address on every request, randomise the User-Agent, and assume you are now invisible. What actually happens is that the blocks arrive around request 150 to 300, because modern anti-bot systems (Cloudflare, Akamai, DataDome, PerimeterX) do not treat the IP as the primary signal. They model the session, and per-request IP rotation produces a session that no human could generate: the same logged-in user apparently teleporting between cities mid-visit. More IPs do not fix that, they amplify it. The unit you rotate should be the session object, not the address, where a session bundles one exit IP with one coherent set of cookies, headers, and geo, held stable for as long as a real visit would last.

 
 
 Where a mid-session IP change gives you away
 Coherence is the layer most scrapers fail
 Three flows punish an IP swap in the middle. Authenticated sessions: change the IP between the login request and the next data request and the server sees a user who signed in from Paris asking for data from Seoul, so it re-verifies or invalidates. Stateful pagination: many apps tie server-side page state to your IP, so changing address between page 3 and page 4 loses the cursor and returns a block or, worse, silently wrong data. Multi-step forms and checkouts: the state established at step one is carried forward, and a mid-flow IP change is an instant red flag. The rule that falls out is simple: all requests belonging to one logical session must exit through the same IP, or at minimum the same subnet, ASN, and geo.
 
 
 
 How to build the rotation layer
 Sticky assignment, honest expiry, careful retries
 Key each session deterministically (job plus target domain, not random) so a crawl reuses one session until it expires, and expire on both a TTL and a request count (refresh around every ten minutes or a couple hundred requests, so a long-lived session does not become its own statistical fingerprint). Keep one header profile per session, no swapping the sec-ch-ua or Accept-Language between requests that claim to be the same browser. Between sessions, rotate within the same geo and ASN cluster, since a jump from a Tokyo residential IP to a Toronto one reads as a VPN switch, not a returning user. And separate your retry cases: a transient timeout retries on the same session after a short jittered backoff, only a clear block (407, 429, a CAPTCHA) flags the session and forces a fresh one. At fifty machines this needs a shared store (a Redis-backed pool) so every worker sticks to the same session instead of each inventing its own.
 

The division of labour that scales best: let a provider that offers sticky sessions guarantee the exit-IP consistency (a session token with a location baked in, held for up to some minutes or hours), and keep your own layer responsible for the cookies, headers, and navigation coherence on top. Building and maintaining a distributed session pool yourself is real, ongoing work that grows every time a target updates its detection. IP rotation is a tactic; session architecture is the strategy, and it is the difference between firefighting blocks and running a scraper that stays a reliable data source as the detectors evolve.

The mirror-image mistake is just as fatal: rotating IPs under one fixed fingerprint. Sticky sessions solve the case where the IP changes mid-visit, but the opposite pairing is its own tell. If you rotate through a dozen addresses while the browser fingerprint stays byte-identical across all of them, you have not diversified, you have announced that one client is hiding behind twelve IPs, which is behaviour no real user or device produces. Once the fingerprint is part of the identity, an unchanging fingerprint spread across many networks links those sessions together more tightly than a single sticky IP ever would have, so the rotation you added for cover becomes the thing that convicts you. The rule that resolves both failures is the same: one fingerprint travels with one IP for the life of a session, and when you rotate, you rotate both together, the way a real device and its connection move as a unit.

The scaling tension worth naming: real hardware wins precisely because it is not infrastructure, and scaling drags you back toward infrastructure. A real phone on a real mobile connection passes the hardest checks not by spoofing anything but by being genuine at every layer at once, the network, the TLS stack, the sensors, the timing all agree because one real device produced them. That is the cleanest version of the coherence principle this guide keeps returning to. The catch is economic, not technical: one real device does not scale, and the moment you add machines to go faster you are back on servers with datacenter IPs whose Layer-4 (TCP/IP) fingerprint gives you away before a single byte of HTTP is read. Every fix from there reopens the same wound, so the honest way to hold this is as a permanent trade rather than a solved problem: the closer a setup sits to a real device on a real network, the higher its trust and the lower its throughput, and scaling is the deliberate act of spending some of that trust for volume. Naming the trade is what stops you from expecting a single configuration to be both maximally stealthy and maximally fast.

Know where your IPs come from, and remember you are a guest#

The detail that separates providers is not the price per gigabyte, it is sourcing: how those residential and ISP addresses end up in the pool. Residential IPs are typically gathered through proxyware, SDKs paid to sit on idle consumer devices, ideally with informed consent (the ethical end of the market) and not so ideally bundled silently into free apps (the part to avoid). ISP proxies are a different animal: addresses registered to consumer ISPs but hosted in datacentres, which is why they combine residential reputation with datacentre stability, and why the way a provider acquires that ISP space is worth understanding before you trust it. Knowing the sourcing model is not a compliance nicety, it directly predicts pool health: consent-based, well-managed pools get burned less and last longer than pools stitched together through opaque intermediary chains.

Two practical stances follow. Pick providers whose sourcing you can actually explain, the reputable ones gatekeep their networks and let only legitimate users on, which is what keeps the IPs clean for you. And carry the right posture onto both networks you touch: you are a guest on the proxy infrastructure and a guest, often an unwanted one, on the target. Behave politely and leave as few footprints as possible. That is not only an ethics point, it is the same discipline that keeps a pool and a target usable for longer.

What a compromised residential pool actually looks like from the inside#

It is worth seeing the ugly end of that sourcing question concretely, because a published 2026 teardown traced one botnet from a single infected handset all the way to a law-enforcement takedown, and the anatomy explains a lot about why network-layer defences behave the way they do. The thread starts with arithmetic anyone can do: every proxy storefront advertises millions of residential IPs, but homes are finite, so where do they come from? Three honest answers. People genuinely opt in through bandwidth-sharing SDKs bundled into free apps. Providers buy and resell each other's pools, so one real home appears under five brand names. And some of those addresses belong to people who never agreed to anything.

 
 
 The delivery vehicle
 A cracked game that actually works
 The infected device in that case was an Android phone that ran warm overnight and burned gigabytes of cellular data doing nothing. The source was a repackaged "free cracked game" from a Telegram channel that posted titles on a schedule. The reason nobody investigates is that the game works: it installs, it launches, it plays. The malware rides along as a passenger while a functioning app does the driving. Inside the repack, three files had been injected that did not belong: a decoy audio file (padding, so the rebuilt package's size lines up), a small bootstrap that loads native code, and the payload itself, a Go-compiled shared library. The intent was legible from its imports alone, a SOCKS5 server and an HTTP proxy library. It was not after photos or files. It wanted the connection.
 
 
 
 The architecture
 A directory, then one socket that never closes
 The command path is two hops, not one. The implant first asks a config domain (sitting behind a mainstream CDN, over TLS) for a relay address, then opens a persistent TCP connection to that relay on an unusual high port and holds it open indefinitely. The domain is only a directory pointing at the muscle, so taking the domain down just moves the relay list. Over that single socket runs a small custom binary protocol that multiplexes many logical channels, so one phone can carry several customers' traffic at once without opening a socket per job. Authentication is a single frame containing a UUID minted when the malware installed, and in the analysed sample the server never expired it, a months-old identifier still authenticated. A phone holding one long-lived tunnel to a datacenter all night is not resting, it is working for someone.
 

Two findings from that work matter directly to anyone in this field. First, what the customers were actually doing. Watching traffic at the exit, the dominant pattern was not ad verification or price scraping, it was large-scale mailbox credential brute-forcing, waves of IMAP connections against major mail providers, with a side of old-fashioned IoT scanning. The "residential proxy service" label is the storefront; a substantial part of the product is rate-limit and blocklist evasion for people attacking accounts at scale, which is precisely why a home IP is worth paying for. Second, the shape of the business. The infrastructure was deliberately smeared across jurisdictions (storefront behind a CDN in one country, relays on a hosting ASN in a second, backend in a third) so no single abuse desk ever sees the whole picture, and the same device pool was resold under more than one brand name. The scale numbers that look contradictory are not: a storefront dashboard showed roughly 417,000 exits online at any moment, while the takedown reporting cited about 17 million devices across the operation's lifetime, which is the same pool measured instantaneously versus cumulatively.

Why this changes how you read IP-based defences, in both directions. For a defender, it explains why pure IP or ASN blocking has quietly stopped working: when every request arrives from a different real consumer connection attached to a real person's infected device, blocking the address punishes the victim and costs the attacker nothing, since the next request comes from another home entirely. IP reputation remains a useful filter, but it can no longer be the load-bearing signal, which is exactly why the detection stack moved up into TLS, fingerprinting, and behaviour. For a scraper, it is the strongest possible argument for the sourcing discipline above, some of the cheapest residential bandwidth on the market is cheap because the people supplying it never agreed, and traffic that shares an exit with credential-stuffing waves inherits that address's reputation. And there is a closing thought from the same research worth keeping: a residential proxy hides your IP, but the shape of your traffic, its timing, sizes, and rhythm, survives being wrapped and relayed, and that shape is its own fingerprint. The address was never the only thing worth hiding.

Verify the geography, do not buy it. Where a provider says an IP lives and where it answers from are different claims, and the industry lets the first stand in for the second. Location data for most networks comes from geofeeds the operators publish about themselves, which is a self-declaration with no verification step in front of it. Measure instead. Ping the addresses from a distributed measurement network and compare the latency to what the claimed location would require, and a familiar pattern appears: addresses advertised across dozens of countries resolve to a handful of large datacentre regions, with Northern Virginia, Amsterdam and Singapore absorbing most of it. Asia is the worst offender, where an address sold as Indonesian or Australian is very often answering from Singapore because the bandwidth is cheaper there.This matters beyond truth in advertising. The five-vector coherence test at the top of the network layer assumes your exit is where you think it is. Set a timezone, an Accept-Language and a DNS resolver to match a country your traffic is not actually leaving from, and you have built a contradiction carefully and deliberately. Before you tune the four vectors you control, measure the one you bought.

 The risk that runs the other way: your IP is liable for the whole pool#

Sourcing is not only about pool health, it is about what your exit IP is on the hook for. When you route through a residential pool, your address is shared infrastructure: at the same moment your scraper reads a public page, other tenants of that pool are sending traffic that exits through addresses just like yours. An investigation that joined one such network as a node and recorded over ten million transiting requests found the mix went well past data collection, into ad-fraud and affiliate-attribution abuse, mass account registration, ticketing and scalping automation, and email spam. The provider's marketing said web data collection and market research; the wire said otherwise.

The uncomfortable part for a buyer is that strong vetting does not remove the risk, it only narrows it. A provider can run KYC on every customer, throttle the SDK, and inspect traffic on the network, and still be one bad edge case away from your IP carrying something you would never send. A trusted customer's credentials leak. A sensitive destination (a bank, a government service) is not classified correctly and so is not blocked. The provider never anticipated what a local LAN address looks like from inside a residential node. You are trusting that their vetting and security hold, on an address that resolves to a real person's home, which is also why the supply side is uncomfortable. One 2026 scan of 6,038 LG and Samsung smart-TV apps found proxy SDKs in 2,058 of them, 42.5 percent of LG webOS apps and 26.9 percent on Samsung Tizen, fish-tank screensavers and solitaire clones quietly turning the television into an always-on exit node, often with consent reduced to a single setup prompt navigated by remote. Cheap streaming boxes have shipped with dormant proxy software preinstalled. The risk is not only that your traffic shares an address with strangers: because a smart TV sits on the home LAN beside routers, NAS drives, and cameras, a failure in the provider's private-range filtering can turn that exit node into a foothold inside the network. The US FBI issued a 2026 advisory on exactly this, warning that when criminal traffic exits through a residential IP, the innocent homeowner is the one whose address is on record. Those same residential IPs feed the pools sold as ethically sourced. Two practical takeaways: prefer providers whose sourcing and abuse controls you can actually inspect (a verifiable partner list and audit trail, not a landing-page adjective), and treat the residential layer as something to use deliberately and sparingly, not as a default you leave running, because every hour you are on it your address is vouching for strangers.

 12 field notes on proxies
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Proxies
 “Our Product Is Really the Filtering Layer, Not the Pool”
 A proxy founder said the quiet part in a public AMA. Providers can source from largely the same upstream supply and still deliver completely different results, because the differentiator is what happens between the raw IP supply and your request: which suppliers get accepted, how incoming IPs are scored, which ones ever reach a customer, how fast burned addresses are pulled, and how sticky sessions are routed.
 💡 Pool size is a vanity metric, and the reputation databases are not proprietary
 The admissions that matter. His own network is majority third-party — even providers running their own acquisition SDKs supplement heavily with upstream and peered supply, which is why the same IP shows up across competing networks. The supplier bar is three concrete requirements rather than a score: visible opt-in disclosure, a functioning opt-out, and a named application or sourcing partner. And he explicitly disclaims verifying consent himself for most of the pool, relying on suppliers' own consent flows.The scoring stack is five components: multiple reputation databases, an internal honeypot network, dynamic weighting of the reputation signals, automated checks at handoff, and continuous re-checking. Critically, the underlying reputation databases can be bought by competitors — the moat is the weighting, not the data.What to do with this. Stop comparing pool sizes. There is no universally clean residential IP: quality is per-target and time-varying, and an address that works on a marketplace may already be dead on Google. The only honest benchmark is your own success rate on your own targets over a week, which is also the only number a vendor cannot sell you.
 
 +
 Aug 2026 / Network
 The UDP Leak, and Why Your Proxy Never Saw the Packet
 WebRTC has been on by default in Chrome and Firefox since 2013, and it can reveal the real IP behind a proxy — because Chromium and Firefox do not proxy UDP at all. Those packets leave the local machine directly, past whatever proxy you configured. The reported side of a WebRTC candidate is easy to rewrite from JavaScript; the hard version is a TURN relay, where the anti-bot's own server logs the address the UDP packet physically arrived from. You cannot spoof that from inside the page.
 💡 Correction worth having: the tool doing the TURN check is Scrapfly's, not the one the post pointed at
 Get the attribution right. NodeMaven's WebRTC leak test is STUN-only — no TURN check and no HTTP/3 or QUIC test in the version we could verify. Scrapfly's tool is the one implementing the three-way comparison: your reported public IP, the IP WebRTC exposes in-browser, and the IP that actually connects to the relay. Pierluigi Vinciguerra covered this client-side gap in UDP, Proxies, and the WebRTC Leak on 2 July 2026.The HTTP/3 half. HTTP/3 runs over UDP and is now roughly a fifth of all requests, with HTTP/2 still near half and about 38% of sites advertising it via Alt-Svc. So a browser that cannot speak HTTP/3 to a site that offers it is now itself a signal. And the sting for request-based scrapers: curl_cffi hard-refuses HTTP/3 over both HTTP and SOCKS proxies — it returns “HTTP/3 is not supported over an HTTP proxy” and the SOCKS equivalent. That is a client-side refusal, not a server block.The fixes. Cheap: launch with --force-webrtc-ip-handling-policy=disable_non_proxied_udp (Firefox: media.peerconnection.ice.no_host and relatives). Proper: run the browser in a VM or container and proxy the whole environment at the network layer, not just web requests. “Switching to HTTP/3 doesn't remove protocol fingerprinting, it just moves it somewhere else.”
 
 +
 Aug 2026 / Supply chain
 Your Residential Pool, and the Phone That Never Consented
 The residential proxy supply chain has a documented history of enrolling devices whose owners never meaningfully agreed. HUMAN Security's Satori team documented PROXYLIB: 29 Android apps removed from Google Play that silently turned handsets into residential proxy exit nodes, via an SDK marketed to developers as a monetisation tool that paid by traffic routed, with consent buried where nobody reads. In January 2026 Google disrupted IPIDEA, taking down domains covering 13 proxy brands and 4 SDKs, a network advertising 6.1 million daily-updated IPs and adding 69,000 new addresses a day, with roughly 600 Android apps flagged as containing proxy-enabling code and around 5 million bots still connecting at takedown.
 💡 550+ state-linked threat groups were routing through one commercial pool
 The number that should end the “it's just proxies” argument. More than 550 distinct threat groups from China, North Korea, Iran and Russia were using IPIDEA infrastructure as recently as January 2026. IPIDEA's public response acknowledged aggressive expansion and promotion on hacker forums while denying it endorsed abuse — which is the closest thing to a candid answer this industry has produced.Why this belongs in a scraping guide. When a provider tells you the pool is consent-based, ask which of the three checks they run: visible opt-in disclosure, a functioning opt-out, and a named sourcing partner. Then ask who verifies it — because at least one founder has said publicly that he relies on his suppliers' consent flows rather than his own screens.One claim we could not stand up. A widely-shared teardown this week alleged a specific budget Android handset shipped a repurposed commercial proxy SDK plus a backdoor surviving factory reset. We searched for the writeup and could not find it indexed anywhere, so we are not repeating it as fact. The verified cases above make the same point without needing it.
 
 +
 Jul 2026 / The reckoning
 The FBI Seized a NASDAQ-Listed Proxy Provider. Your Pool Might Be a Botnet.
 On 2 July the FBI seized hundreds of domains tied to NetNut — a large residential proxy service run by Alarum Technologies, a company listed on NASDAQ. Not an anonymous dark-web operation. A public company with investor relations, quarterly reports, and a legal team. The guide has argued for a while that residential proxy sourcing is the industry's quiet liability; this is what it looks like when the bill arrives.
 💡 The exit node you rented may be malware on someone's smart TV, and that is now a legal risk, not an ethical footnote
 How it unravelled. On 19 June three security firms showed NetNut's residential network was powering a botnet called Popa, running on everyday devices — smart TVs, streaming boxes — via SDKs bundled into pirated streaming apps. Qurium traced the control servers while investigating DDoS attacks; Brian Krebs made it public; two weeks later the authorities moved. Google estimated the network at at least two million devices.Why you cannot just unplug it, and how they did anyway. A residential proxy network is decentralised by design — the exits are millions of consumer devices you cannot seize. So the takedown hit the three things those devices depend on to be a commercial product. The command-and-control layer: Google killed the accounts running the malware's C2 — which was itself hosted on Google's own services, a technique called living off trusted services so malicious traffic hides inside normal cloud traffic. The enrollment pipeline: Play Protect began disabling apps carrying NetNut SDKs and blocking new installs, so the pool shrinks as devices reset and drop off. The commercial front door: seizing the domains shut the storefront and stopped new customers. Google's estimate was that this cut available devices by millions.The distinction every buyer should now be able to draw. NetNut sold four things, and they are not equal. Datacenter IPs are clean — clear ownership. Its ISP proxies came through DiviNetworks by an above-board arrangement: a revenue-share pitch to ISP partners, a GRE tunnel terminated on the partner's edge router, policy-based routing steering a slice of traffic out through NetNut — the partner signs and gets paid, and whatever you think of subscriber consent, the contractual chain is in daylight. The residential pool is the Popa part: sourced by distributing SDKs onto devices whose owners never knowingly agreed. Same vendor, same dashboard, two completely different provenance stories — and only one of them ends in a seizure banner.The market's verdict, and yours. Alarum disputes the accusations and says it was never formally contacted — but the stock crashed more than 70% in a week, roughly 96% off its 2024 peak, and the company warned of a material adverse effect. For anyone running collection in production the lesson is concrete: provenance of your proxy pool is now a supply-chain risk you can be caught downstream of. “It was a big, professional vendor” is exactly what NetNut was. Ask where the residential IPs come from, prefer ISP or datacenter where the target allows it, and treat an unexplained too-cheap residential pool as the warning it is. This sits alongside the data-broker and legal material in Read this first.Source: The Web Scraping Club, Pierluigi Vinciguerra.
 
 +
 Aug 2026 / Proxy operations
 "Was That the Proxy or the Site?" Is Finally Becoming a Status Code
 The most expensive hour in proxy debugging is the one spent deciding whose fault a failure was. Most proxies forward a generic 5xx and leave you guessing between a proxy policy block, proxy infrastructure, and the target actually refusing you. That is starting to change, and there is a real disagreement about how.
 💡 Log the attribution field from day one, whatever your provider calls it
 Where the market is as of August 2026: Bright Data ships an X-BRD-ERR-CODE header and now also supports RFC 9209 Proxy-Status, the IETF standard designed for exactly this. Oxylabs adds an X-Error-Description header only when the fault is on their side. Decodo documents dedicated error codes in its response headers. Massive went further and, from 17 August, returns dedicated HTTP status codes (452–454 and 470–477) that are unambiguously theirs, plus an X-Massive-Reason header with the exact cause — no header parsing, the status code alone tells you policy, infrastructure, or target.The disagreement is the useful part. The immediate pushback, from practitioners in the thread, was that proprietary status codes are the wrong abstraction: this is precisely what Proxy-Status was standardised to solve, and having to learn that 452 means one thing at one provider and 472 means something else at another is the fragmentation the RFC exists to prevent. The counter-argument is equally real: a status code needs no parsing and no client library, so it gets adopted, and an RFC that nobody implements attributes nothing.What to do while it settles. Do not wait for a winner. Add one field to your request log now — call it failure_owner, values proxy_policy / proxy_infra / target / unknown — and populate it from whatever your provider gives you today, header or status. The value is not in the encoding, it is in being able to answer "what share of last week's failures were ours" without re-running anything. It also changes your provider conversations, because "unknown" trending upward is a contract question, not an engineering one.
 
 +
 Aug 2026 / Proxy market
 Price Does Not Predict Proxy Performance, and the Winner Changes Per Domain
 A benchmark across providers, domains, prices and request volumes landed on three results that between them retire most published proxy rankings: expensive providers did not consistently beat cheap ones, the best provider changed from one target domain to another, and the best value changed again as monthly volume grew.
 💡 The only ranking that means anything is yours, on your targets, at your volume
 The volume effect is the one people get wrong most often: a provider can be the best option at 10,000 requests a month and considerably worse at 100,000 or a million, because pricing tiers, pool composition and per-domain reputation all move independently. A generic league table cannot encode any of that. The method that does work is unglamorous — run your actual targets through each candidate and compare on success rate, latency, and cost per successful request, which is the only one of the three that combines the other two into something you can put in a budget.The market context around it, mid-2026. A legal win, then a second round: Google's DMCA claim against SerpApi was dismissed on 20 July 2026 in the Northern District of California, with the claims resting on search results containing no copyrighted content thrown out for good, on the reasoning that the DMCA does not protect material that is not copyrighted. The court left Google 21 days to amend on a narrower set, the results carrying a copyrighted component such as the snippets in Knowledge Panels. Google refiled on the final day, 10 August 2026: a 15-page amended complaint built around the one element the court found missing, written permission from copyright owners, and naming Reddit as the partner that asked it to block scrapers. The question has therefore moved from whether scraping public pages is unlawful to whether the platform holds licences that make the content protectable in the first place, which is a licensing-chain argument rather than an access one. A vote of confidence: Oxylabs took a $130M investment at a reported $3.6B valuation. And two warnings that the residential market's Wild West era is closing — the FBI seized domains connected to NetNut and its sourcing network amid fraud allegations, and LG told developers to strip residential-proxy functionality from smart-TV apps or face suspension. Expect more KYC, tighter reseller controls, and far more scrutiny of where residential IPs come from.The third finding is the one that ties back to this guide's detection section. A perfect browser fingerprint is no longer sufficient: anti-bot systems are moving from checking individual fingerprint values to judging the whole session. You can pass every discrete check and still fail because the browser, OS, hardware, network, navigation and behaviour do not tell a coherent story together. Which is the same conclusion the guide reaches from the other end — you do not get blocked for looking like a bot, you get blocked for not making sense. There is also a practical corollary nobody says out loud: this environment rewards continuous measurement over one-off research, because a stack that was correct three months ago is a stack you have not tested.
 
 +
 Tool / Proxy ops
 Stop Round-Robining Dead Proxies: Bayesian Selection
 Round-robin proxy rotation has a dumb flaw: it keeps sending requests through proxies that are already dead or banned. ProxyOps uses Thompson Sampling to learn which proxies are working right now and route around the rest. Real data over 549,114 requests in 7 days: 76% success with Bayesian selection vs 36% with round-robin. Same proxies, same targets, more than double the success rate.
 💡 Treat proxy selection as a multi-armed bandit, not a queue
 The core insight: proxy health is non-stationary, a proxy that worked a minute ago may be banned now, and a dead one may recover. Round-robin ignores this entirely and gives every proxy equal traffic regardless of recent performance. Thompson Sampling (a Bayesian multi-armed-bandit method) models each proxy's success probability as a distribution, samples from those distributions to pick the next proxy, and updates beliefs after every request. Good proxies get more traffic, failing ones get probed occasionally to check for recovery but mostly avoided. The benchmark is striking: 76% vs 36% success on identical proxies and targets is not a marginal optimisation, it's the difference between a viable scrape and a failing one. ProxyOps ships this as a full open-source tool, multi-provider inventory, pluggable rotation strategies, per-bot proxy groups, comparison dashboards, on FastAPI + Vue + PostgreSQL, Dockerized (docker compose up). MIT licensed. github.com/Paulo-H/proxyops
 
 +
 Tool / Concurrency
 Validating Millions of Public Proxies: Why Python Wasn't Enough
 Public proxy lists are mostly dead, slow, or pre-blocked. Validating them at scale before a scrape becomes its own engineering problem. One builder hit the wall with Python threads checking proxies for Google scraping, then rebuilt the validator in Go. The lesson: Go is exceptional for I/O-heavy concurrent workloads where Python's threading model bottlenecks.
 💡 Validate the pool before the scrape, in Go, not inline in Python
 The bottleneck most people don't anticipate: if you rotate through huge lists of free/public proxies to avoid rate limits, you spend more time discovering which proxies are alive than actually scraping. Doing this inline with Python threads doesn't scale, the GIL and thread overhead cap you well below the concurrency a proxy-check workload needs (it's almost pure network I/O with tiny CPU cost, the ideal case for lightweight concurrency). Go's goroutines and channels handle tens of thousands of concurrent connection checks on modest hardware, with far better memory efficiency than Python threads or even asyncio for this specific pattern. The architecture takeaway generalises: separate proxy validation into its own high-throughput service (in Go or Rust), maintain a continuously-refreshed pool of known-good proxies, and have your scraper pull only from validated proxies rather than checking inline. Useful well beyond scraping, the same pattern applies to OSINT, network reconnaissance, and distributed systems. github.com/harshit-singh-ai/proxy-validator
 
 +
 Benchmarks / 2026 data
 Distributed Browsers: The "Residential Proxy Moment" for Stealth
 The Web Scraping Insider benchmarked stealth browser APIs (April 2026): Scrapeless 90.95, Bright Data 89.05, Oxylabs 85.71, then a cliff. The bigger idea from Driver.dev: as anti-bots fingerprint GPU and hardware entropy, cloud stealth browsers may become the new datacenter proxies, detectable by default, pushing the field toward real-device browser networks.
 💡 Cloud browsers → real-device browser networks, mirroring DC → residential
 Three data points worth internalising from the April 2026 benchmarks. (1) Stealth browser APIs are measurably different: Scrapeless (90.95), Bright Data Scraping Browser (89.05), Oxylabs Headless (85.71) lead, then most providers fall off a cliff for the same reason, automation signals leak, and once that happens proxy rotation doesn't save you. (2) Cloudflare bypass has no single winner: across 8 approaches tested on 20 protected sites, only 3 had broad coverage, smart proxy APIs, TLS impersonation (curl_cffi), and browser APIs. Different domains use different protections, so what works on one target fails on another. (3) The structural prediction: anti-bots increasingly fingerprint the whole environment (browser, GPU, hardware entropy, OS quirks), which means a stealth browser running in a datacenter is detectable simply by being in a datacenter, regardless of how good its fingerprint patches are. The likely evolution mirrors proxies exactly: datacenter proxies gave way to residential proxies, and cloud stealth browsers may give way to distributed browser networks running across real consumer devices. The fingerprint isn't patched, it's genuinely real, because it's a real device.
 
 +
 TLS / Proxies
 Why "Just Get Better Proxies" Stopped Working
 The problem is your TLS handshakenot your IP. Cipher suites, HTTPS extensions, GREASE values form a JA4 fingerprint. A clean residential IP still fails if the fingerprint exposes you.
 💡 The residential IP passed. The fingerprint gave it away.
 TLS detection happens at the ClientHello level, before any HTTP is exchanged. The JA4 fingerprint hashes: cipher suite list (sorted), TLS extensions (sorted, GREASE removed), ALPN protocols. Python's requests library sends a different cipher suite order than Chrome. httpx is different again. Even with a clean residential IP, if your cipher ordering does not match Chrome's, you are identified before the server processes a single header. Fix: use curl_cffi with impersonate="chrome124"it emits Chrome's exact TLS ClientHello. Also watch HTTP/2 SETTINGS frames, they contain window sizes and header table parameters that vary per client.
 
 +
 Network Identity
 The WebRTC Trap: Your Browser Is Leaking Your Real Location
 Proxy says US. WebRTC says elsewhere. It leaks your real IP via STUN and creates geo mismatches. Anti-bots check that IP + WebRTC + timezone + DNS + Accept-Language all agree.
 💡 Quick test: browserleaks.com/webrtc, check before blaming your proxy
 WebRTC uses the STUN protocol to discover network paths. During ICE candidate gathering, the browser contacts a STUN server and reports: your real public IP, your local LAN IP (e.g. 192.168.x.x), and all network interface addresses. Your proxy only routes HTTP/HTTPS traffic, WebRTC bypasses it entirely. Anti-bots cross-check: proxy exit IP vs WebRTC public IP vs DNS resolver location vs Accept-Language vs timezone. All five must agree. Fix with Camoufox: set geoip=True and it automatically aligns all five vectors. Do not simply disable WebRTC, it removes a feature that 99% of real users have, which itself becomes a bot signal.
 
 +
 Proxies
 SwiftShadow: Free Proxy Rotation Without the Headaches
 Grabs free proxies, validates, rotates automatically, filters by country. Built-in caching, auto-switches on failure. ~300 stars, actively maintained.
 💡 pip install swiftshadow, from swiftshadow import QuickProxy
 SwiftShadow maintains a pool of free proxies sourced from multiple public lists. On initialisation it validates all proxies (checks response time and anonymity level) and caches the working set. When a proxy fails mid-request, it automatically switches to the next validated proxy in the pool, no intervention needed. The QuickProxy(countries=["FR","DE"]) API filters by exit country. The built-in cache means it does not hit proxy list APIs on every request. Usage: from swiftshadow import QuickProxy; proxy = QuickProxy(); session.proxies = {"http": str(proxy), "https": str(proxy)}. Important: free proxies have high failure rates and low anonymity, do not use for Akamai, DataDome, or PerimeterX targets. Best for scraping open/unprotected sites at scale without cost.

---

## DECISION PLAYBOOK — Step-by-Step Priority Order

09 Decision playbook
Walk this in order.Stop at first win.
Each step adds complexity, cost, and maintenance. Most production scraping is solved at steps 1–3. Never start at step 5.

 01
 
 Lowest friction · Asad's priority #1
 Find the mobile API
 Mobile apps hit same backend with far weaker bot protection. HTTPToolkit intercepts all HTTPS from Android emulator. Frida hooks into SSL_read/SSL_write directly. If you find the API endpoint, every HTML anti-bot becomes irrelevant.
 
 HTTPToolkitFridamitmproxyBurpsuite

 02
 
 XHR reverse engineering
 Find the GraphQL or REST endpoint
 Chrome DevTools → Network → Fetch/XHR. Many SPAs load from one undocumented JSON endpoint. Confirmed in production, a direct GraphQL endpoint bypassed all Akamai HTML protection.
 
 Chrome DevToolsBurpsuitewebclaw CLI

 03
 
 JSON in HTML · No requests needed
 Look for embedded state
 Next.js embeds full state in __NEXT_DATA__. React SPAs often have >50KB script containing all data. Confirmed: Grainger.com (DataDome-protected), 110KB JS state blob bypasses DataDome entirely because it's in initial HTML.
 
 chompjsParselBeautifulSoup4

 04
 
 HTTP scraping · No browser
 curl_cffi + Scrapy
 Identify anti-bot with Wappalyzer. curl_cffi with JA4 impersonation resolves most Akamai and DataDome at HTTP layer. Add residential proxy. If __NEXT_DATA__ appears in response, extract it with chompjs.
 
 curl_cffiScrapyScrapling

 05
 
 Browser automation · C++ level only
 Camoufox or CloakBrowser
 JS injection patches leave signatures. Camoufox: 100% pass rate Mar 2026 on the FF135 base; stable moved to FF146 in July 2026, so re-test. CloakBrowser: 49 C++ patches, reCAPTCHA v3 score 0.9, Akamai extension probes pass. PatchRight for Kasada specifically, no JS signatures.
 
 Camoufox ★CloakBrowserPatchRight

 06
 
 Last resort · F5 Shape only viable path
 Managed platform API
 F5 Shape's custom VM makes DIY impractical. Token expiry in minutes, payload changes every rotation. At scale: engineer maintenance cost > platform cost. One API flag handles everything. Cost-justify: >2 days/month maintenance → managed API wins.
 
 Bright DataZyteFirecrawl

Quick reference cheat sheet#

Anti-botPrimary vectorSteps 1–2 viable?Best toolKey note

AkamaiJA4+ + sensor.js + extension probesOftencurl_cffi + CloakBrowserFind mobile/GraphQL first
CloudflareJA4 Rust edge + TurnstileSometimesCamoufoxOrigin IP via SecurityTrails
DataDome85K ML + WASM boring_challengeYescurl_cffi + mobile IPCheck __NEXT_DATA__ first
PerimeterX5-vector scoreSometimesCamoufox + residentialFresh session per domain
KasadaPolymorphic JS PoWRarelyPatchRight + residentialNever playwright-stealth
F5 ShapeCustom VM + minute expiryNoManaged APIDIY not practical

Disclosure · my company
If your target sits on a Sometimes, Rarely or No row, email me at scrapesync@gmail.com before you lose a weekend to it. Send the URL and a note on what you have already tried, and I will tell you which rung it is actually on and what I would try next. I usually reply within a couple of days, slower when I am mid-job. Most of the time the answer is a section further up this page and I will point you at it. Sometimes it is one of the managed APIs in the Best tool column, in which case I will name Zyte or Scrapfly rather than myself. The interest I should declare, since this is a table of vendors and I sell on the same rung: scraping is my day job, I run ScrapeSync (my company). The answer is free either way.

 1 field notes on the playbook
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Diagnosis
 A 429 and a 403 Need Different Fixes, and One of Them Is Not About You
 A practitioner sent requests at a deliberately unremarkable pace — about one every 1.2 seconds. After eleven of them the site returned 429. Not a block. A speed limit. “Have you ever changed your headers or your fingerprint to fix a problem that was actually about speed?”
 💡 Rate limiting is a dial, not a switch — and the dial does not care what you look like
 The detail that saves you the most time is what happened next. The lockout ran fifteen minutes, and checking again during the window returned the time remaining from the original lockout, not a fresh countdown. So polling did not extend the penalty. That is worth knowing precisely because the instinct — stop touching it, you will make it worse — is wrong here, and acting on the wrong instinct leaves you guessing about a window you could simply have read.Why this is the same finding as the audit above, from the other end. That research separates the signals by kind: CAPTCHA is binary, JavaScript either runs or does not, and rate limiting is a gradient — the site is not refusing you, it is pricing your pace. A 429 is a negotiation. A 403 is a verdict. Treating them as the same event is how you end up rebuilding a TLS profile to solve a problem that a sleep would have solved.The rule. Read the status before you reach for a technique. 429 means slow down, honour Retry-After, and back off with jitter — your identity was fine. With one caveat this feed earned the hard way: some sectors rate-limit on client identity rather than pace — the fashion card in this feed found exactly that, with a block on the very first request and no amount of waiting helping. So the test is whether backing off actually changes the outcome. If a slower cadence gets the same 429, the limiter is not counting your speed, it is counting you, and you are back to the 403 question of which layer decided. 403 means you were judged, and now the question is which layer judged you, which is a different card in this feed and a different afternoon. The expensive mistake is not picking the wrong fix; it is never asking which problem you had.

---

## INNOVATION AND EMERGING TECHNIQUES

10 From the community
What practitioners areactually shipping in 2026
Fresh insights from engineers actively solving these problems in production. Shared publicly on LinkedIn.

The newest 24 · the rest are filed under each topic above

 
 
 Showing 33 of 33 recent notes

 
 +
 Sep 2026 / Deobfuscation
 Two Hours Against Several Weeks, and the Blog Post That Set the Ceiling
 The REcon Montreal talk on agentic reverse engineering is now published, and it is the most concrete account yet of what an agent actually does to a protection. The thesis is narrow and worth quoting exactly: "the agent becomes an orchestrator of tooling" and "agents change the economics of deobfuscation". One of the speakers puts it flatly — the agent is not able to understand the binary, it is mainly calling tools. Comprehension did not improve. The debugging loop got cheap.
 💡 Its competence on a commercial VM was bounded by a human blog post from 2021, handed to it as input
 The setup. Claude Code or Codex inside a Docker sandbox, Binary Ninja Headless reached over MCP, with unicorn, miasm and msynth as direct tool calls. Experiments run May and June 2026. The model never does the semantic work; the emulator, the symbolic executor and the synthesiser do.The result everyone will quote, with the condition attached. Given the generic prompt — here is an obfuscated function, deobfuscate it and reconstruct the high-level code — the agent produced nothing usable after several hours against a commercial VM obfuscator. It only worked once it was handed a procedure: the author's own 2021 write-up on writing disassemblers for VM-based obfuscators as its knowledge base, the script templates from that post, and the three tools. Then it ran one to two hours autonomously against what the speakers estimate as several weeks of full-time manual work. The self-referential detail is the honest one: the agent's ceiling was an existing human explanation of the problem.The pipeline, because it transfers. Emulate entry to exit with unicorn and treat that as ground truth, self-repairing failures as they appear. Re-run the same path under symbolic execution, checkpointing against the emulated trace at every step so the agent knows it is still on the real path. Lift each handler's semantics from the validated trace. Extend the script into a full disassembler. Reconstruct. Against a kernel anti-cheat the agent found one shared interpreter across roughly a thousand VM entries, wrote a single disassembler, and devirtualised them in parallel.The failures are the useful half. A recent game DRM defeated it outright: meaning is runtime-bound, so it stalls under emulation. A deflattening pass written for one malware family did not work at all on the next and had to be rewritten, then still failed on the MBA opaque predicates. On call obfuscation the agent tried cryptographic identification of a jump table and then brute-forcing every address in the binary before a human stepped in. The warning to carry: each pass stays sample-specific. There are no benchmarks, no success rates and no cost figures anywhere in the talk.What they say impedes an agent. Four dimensions, and this is one slide plus a Q&A answer rather than a measured result, so weight it accordingly. Budget and search traps (state expansion, costly oracle checks) attack the token and tool-call budget. Protection stacks bind meaning to state unavailable at analysis time — control-flow-sensitive rolling-key bytecode where each handler derives the key decrypting the next, so nothing can be enumerated or skipped. Tool friction got the sharpest answer: anti-emulation mostly means anti-QEMU, finding behaviours the emulator models differently from real hardware and gating a loop on them so it spins forever — and the argument is quantity, "the agent gets stuck and stuck and stuck", because each divergence costs a full debug cycle and the agent cannot know how many remain. Narrative poisoning: fake symbols, planted strings, prompt injections aimed at the model rather than its tools. Asserted, not demonstrated.What transfers to JavaScript, and what does not. The VM workflow maps one to one onto bytecode VMs in commercial anti-bot scripts, and rolling-key handler decryption is the same primitive doing the same job. "Each pass stays sample-specific" is exactly the Babel transform that works on one bundle and dies on the next. Narrative poisoning is more potent in JavaScript, because an agent reads real source text rather than decompiled pseudocode, so planted strings and injections land directly in its context — strip strings before feeding a challenge script to an agent. But the tool-friction defence is much weaker here: it depends on the emulator being an imperfect model of hardware, and in JavaScript you simply run a real browser, so ground truth is nearly free and there is no fidelity gap to exploit. There is no lifting problem either, since a parser hands you exact function boundaries for nothing. And the threat model is inverted. The talk is about protecting code you ship; anti-bot is about classifying the client. An agent that fully deobfuscates a sensor script still has to emit a payload that survives server-side correlation — which is precisely why this industry put its real defence there.
 
 
 +
 Sep 2026 / Research
 The Solver That Never Touches the DOM, and the Denominator Under Its 100%
 A preprint posted on 2 September describes a reCAPTCHA v2 solver with no Selenium, no Playwright and no DOM access at any point. It screenshots the page, localises the grid with a fine-tuned YOLOv8 detector, escalates only the uncertain tiles to an open-weight 7B vision-language model, and clicks by sending operating-system mouse events through PyAutoGUI. Against Google's reCAPTCHA v2 demo page: 100 of 100 sessions passed, 206 puzzles, 87.9% solved on first attempt, median 17.1 seconds per puzzle. The motivation is stated plainly — Selenium "sets the navigator.webdriver property", so they removed the driver rather than hiding it.
 💡 The word "demo" appears exactly once in the paper, and it is carrying the entire result
 arXiv:2609.02393, Salman and Bicakci, Istanbul Technical University. Unreviewed preprint, no venue, no ethics statement and no limitations section — the caveats are scattered inline. Read it for the architecture, not for the headline.What is theirs and what is borrowed. Their own YOLOv8 is a UI detector (five classes: captcha area, cell, checkbox, reload, submit; mAP@0.5 0.995, ~1.1ms/image). The tile classifier is Plesner et al.'s publicly released YOLO, reused as-is. The paper says so: "Because both systems use the same published YOLO classifier, these gains come from the hybrid design rather than a stronger detector." The VLM is Qwen-7B-VL, self-hosted, routed at a confidence threshold of 0.70; YOLO handles ~70% of cells at 4ms, the VLM is called on 37.1% of puzzles at 225ms to 2251ms.The comparison is weaker than it reads. The 2 → 5 → 7 → 13 ablation (median challenges per CAPTCHA as cookies, Bezier curves and mouse simulation are stripped) and "blocked entirely after 20 runs" are Plesner's published numbers restated, not re-measured. And Plesner's solver skips puzzles whose class it does not support (Boat, Taxi, Tractor, Stairs), so the 100%-versus-100% is not like for like."Learns from every encounter" is offline, not online. No tile cache, no live gradient updates. It is teacher-student distillation between runs: the VLM hard-labels new classes, YOLO is fine-tuned with Experience Replay. Honest about the cost — Taxi reaches 96% recall after roughly two puzzles, Boat plateaus near 48% using every training image, and catastrophic forgetting runs 6.8 to 17.2 points on the existing 13 classes. Labels are used unfiltered, and VLM labelling accuracy was 82.1% on Taxi.The genuinely interesting result is adversarial. PGD perturbation drives Plesner's YOLO to 0.0% accuracy; the VLM only drops 3.7 points. So the VLM relabels, YOLO retrains, and the loop recovers across a simulated twelve-month arms race. Their explanation is the part worth keeping: a cheap, roughly 70%-accurate open-weight teacher hardens the student as effectively as a perfect oracle, because noisy labels decorrelate the solver from the defender's surrogate model, so crafted perturbations stop transferring.The practitioner counter, from the post's own comments. A demo page is mostly grading the token, not the pointer. The layer that survives losing CDP and navigator tells is kinematics: OS-level synthetic events arrive without the HID jitter and sub-pixel noise a real mouse produces, often land on exact integer coordinates, and on Windows carry an injected flag the page cannot read but the endpoint can. Cite this paper as "on Google's demo page", never as "against reCAPTCHA v2".
 
 +
 Sep 2026 / Benchmark
 Fifteen Engines, One Strict Referee, and the Four That Passed
 An open-source stealth benchmark ran fifteen browser automation tools against two live bot detectors. Against the strict referee — deviceandbrowserinfo.com, which returns an isBot flag plus roughly twenty individual detections — only four passed: camoufox, CloakBrowser, RayoBrowse and scrapling. Plain Playwright, plain Selenium, undetected-playwright and rebrowser-playwright were all called bot by both detectors.
 💡 Rebuilding the browser beat patching flags onto it, and the headless User-Agent was the single most common tell
 The scoreboard. Perfect 100 with no tells: camoufox (Firefox), CloakBrowser (Chromium), invisible-playwright (Firefox), RayoBrowse (Chromium), scrapling-stealthy (Chromium). Obscura scored 94, tripped only on a non-native function. Then a hard cliff to 70 — nodriver, patchright, pydoll, undetected-chromedriver and zendriver all failed on one tell and one only: the headless User-Agent, which was enough for the strict detector to call them bots while BrowserScan passed them. At 35: playwright-vanilla, selenium-vanilla, undetected-playwright (webdriver plus headless UA). Bottom at 10: rebrowser-playwright, which added visible driver residue.Three of the four winners rebuild the browser rather than patch it. No HeadlessChrome string in the UA, nothing reading as CDP automation. That is the finding, and it matches this guide's engine-level thread: flags applied on top of normal Chrome leave a seam that a detector reading twenty signals will find.Read the scope before you buy anything on it. The benchmark deliberately covers the JavaScript fingerprint surface only. Transport (TLS and HTTP/2), request timing and proxy exit are explicitly excluded, on the stated reasoning that swapping a Python library cannot change them. So a 100 here means "clean on the JS surface in headless mode on an M2", not "will get you in". Every Chrome-based tool was driven with channel="chrome" against real installed Chrome, precisely so bundled Chromium's SwiftShader renderer would not be miscounted as a tool failure — a methodological care worth copying. Probe score and detector verdict agreed on 12 of the 13 tools where a verdict was captured.Date discipline. The article carries a publication date of 23 July 2026 and resurfaced on social in September. Treat the table as a July measurement of fast-moving tools, and re-run rather than cite it in December. The harness is in the author's subscriber repository; the results table and heatmap are public, the per-tool analysis is paywalled.
 
 +
 Sep 2026 / Measurement
 The Benchmark That Publishes Its Own Retractions
 The proxy-vendor harness this guide already cites for the Amazon result has now published its full method, and it is the most self-critical measurement document in this field. It documents a claim it reversed, an IP ban it inflicted on itself, and one classification cell it flags as never having been checked. "Five of these ten replaced an earlier claim of ours, and both versions are still in the notebook."
 💡 The Amazon table is a tie, not a ranking, and the harness says so before you do
 Correcting our own earlier entry. The Amazon run used 8 engines, not 11 — eleven is the harness registry, not that run's matrix. And 3,816 is attempts; the judged denominator is 3,530 after 286 harness errors are excluded. Both numbers are in the repository and they mean different things.Why "tied or beat" is the only honest verb. Two-sided Fisher exact against the leading cell with a Bonferroni threshold of 0.0071: stock chromium 96% (419/436), rebrowser 95%, botasaurus 94%, camoufox 92%, zendriver 92%, seleniumbase 92% are all statistically indistinguishable. Only cloak at 80% and patchright at 63% (288/457) separate, and they separate downward. Patchright sits 33 points below the engine doing nothing at all. Two tiers, not eight places.Findings worth more than the headline. Chrome burns 43MB per fresh profile talking to Google before you request anything — 43.2MB of a 43.4MB idle window to optimizationguide-pa.googleapis.com on about:blank, which is roughly 43GB per thousand attempts billed as residential traffic. The host outweighs the proxy on Google: same code, same gateway, same target, 39% (24/61) on a Windows workstation against 0% (0/84) on a Linux VPS in overlapping hours, Fisher p = 3.7e-11. DuckDuckGo reads the User-Agent and nothing else: 95/95 pass for engines whose UA omits HeadlessChrome, 0/50 for the two that carry it, across three browser families. The TLS handshake was not the discriminator: chromium, patchright and obscura emit byte-identical ClientHellos yet finish 44 points apart — and compare JA4, not JA3, because Chrome shuffles extension order per connection.The method choices to steal. Verdicts come from page content, never HTTP status, across ok / captcha / consent / block / empty / error, and empty is not block — Google's 92KB "enable JavaScript" scaffold rejects nothing, and 14 of 14 such rows carried enablejs and none carried recaptcha. Harness errors are excluded from the denominator instead of counted as failures. Cells interleave at batch granularity because "the hour is the largest confound this repository has found: the same gateway, country and browser moved 69 points to 52 between two windows of one afternoon." A matrix that cannot be controlled is refused by a dry run rather than silently adjusted. And the unmodified control is inside the matrix, enforced by a test that reads the source and fails the build if browser arguments or a UA override ever appear.Standing caveat. The author is a proxy vendor. The repository asserts that nothing in it is a sales number, which cannot be audited without re-running the harness — which is, to its credit, exactly what it is built to let you do.
 
 +
 Sep 2026 / Argument
 The Vendors Are Talking About Agents While the Classical Bypasses Ship
 The head of research at an anti-bot vendor published an unusually blunt piece arguing that his own industry has drifted. While vendors talk about agentic trust, agent intent and agent governance, public tools that fully reverse engineer those same protections keep shipping, and the release rate is accelerating. Not fingerprint spoofing within the rules of the game — real bypasses that generate valid anti-bot payloads without running the protected JavaScript in a browser at all.
 💡 AI is not making bots smarter. It is making bot developers faster, which is a different threat model.
 The distinction he draws. "AI may have been involved at almost every stage of building that system without a single AI agent ever touching the production environment." And: "AI does not remove the need to understand what you are doing, especially against sophisticated protections, but it lowers the barrier and makes experienced bot developers considerably faster." What eventually arrives at your endpoint is still a classical HTTP client, and it still has to be caught through the client, the automation, the infrastructure or the behaviour.His test for a vendor. "Can it reliably tell that the client hitting your application is automated in the first place?" He wants credential stuffing blocked, fake account creation with disposable domains caught, and measurable counts of fraudulent accounts created — over intent classification. "These problems are much less fashionable than AI agents, but they are also much easier to measure." Note honestly that his piece names no numbers on bypass release frequency; the acceleration claim is his observation from Discord, Telegram, Reddit and GitHub, not a dataset.The comment thread is where the useful engineering rule appeared. Asked why anti-detect browsers are the default when generating payloads directly is cheaper for scraper and target alike, he gave a decision rule worth writing down. Scraping as a service, across many sites and many anti-bot vendors and versions: invest in the anti-detect browser, proxies and realistic mouse movement, because maintaining one solver per vendor per version is not sustainable. A small number of target sites at very high volume — his example was millions of profiles continuously — and the non-browser client scales better. That is the same escalation ladder this guide teaches, priced by breadth against depth.Two refinements from other practitioners in the thread. A newsletter author argued the binary is gone: bot or human is now bot, human, or bot acting on behalf of a human, and no vendor can comfortably sell "we block AI agents too" to a customer who reads that as lost revenue. A detection engineer noted the malware field already has vocabulary for this, pointing at Recorded Future's AI Malware Maturity Model, which sorts AI-assisted development from genuinely AI-driven execution — swap "malware" for "bot" and it mostly transfers. A reverse engineer pushed back on the acceleration itself: against VM-based commercial protections, AI alone is not enough, and the author agreed — it is the combination of a skilled reverser and AI, not AI on its own.
 
 +
 Sep 2026 / Tooling
 Run the Page's Own Decoder. Then Check What the Repository Actually Ships.
 A deobfuscation tool circulated this week on a genuinely correct premise: most deobfuscators pattern-match known encodings and break the moment a site rotates its obfuscation, so instead of reimplementing base-N, RC4 or XOR, extract the string array and the decoder the file already contains, run them in a sandbox, and ask the real decoder for every index. A new alphabet or a shifted rotation offset then costs nothing.
 💡 The idea is right, the repository is 200 lines, and the interesting half is in the article rather than the code
 The technique, precisely. Parse with Babel, take the largest top-level array of literals as the string array, take the first top-level function referencing it as the decoder, re-emit array plus rotation plus declarations into a node vm with a two-second timeout, then loop the indices calling the shipped decoder to build an index-to-plaintext map. Rotation is never computed — the rotation IIFE is simply executed first, so whatever offset the build used applies naturally. That part of the claim holds.Where it stops, from reading the source. The array must be a top-level variable. Stock obfuscator.io hides it inside a self-rewriting getter function, and the extractor never descends into function bodies, so it raises "No string array found" on the most common real-world input. Decoder calls must have exactly one numeric-literal argument; obfuscator.io's decoder is two-argument, and those call sites are silently skipped. There is no alias resolution, so the routine practice of assigning the decoder to a second identifier defeats it. Dead-branch removal fires only on literal true and false, not on the customary negated-array idioms. And the sandbox has no window, document or navigator, so any self-defending, domain-locked or environment-probing decoder throws.Provenance worth checking before you adopt it. The repository has zero stars, one commit dated 20 June 2026, and nothing pushed since; the September announcement is a re-post of unchanged June code. Its own test suite is four assertions against a single hand-written 944-byte fixture that approximates the shape rather than reproducing real obfuscator.io output. There is no comparative benchmark, and the README does not mention webcrack, synchrony, restringer or de4js — all of which already handle the getter-function array, two-argument decoders, alias resolution and control-flow unflattening.Why it is still worth reading. The companion write-up shows the technique working at real scale against a live anti-bot challenge — hooking Function to capture a 350KB evaluated body, then running it across a dozen payload generations. That harness, the Function-hook loader interception and the faked browser environment, is not in the repository. What shipped is the vendor-neutral skeleton. Take the stance, which is correct for per-request-regenerated payloads, and keep webcrack for everything else.
 
 +
 Sep 2026 / Scale
 Throughput Is Concurrency Divided by Service Time, and the Queue Is Not the Fix
 An engineering write-up on building toward very high CAPTCHA solve rates makes one point that applies to every scraping fleet: throughput equals concurrency divided by service time. If an operation takes about two seconds and you want 8,000 completions per second, you need roughly 16,000 operations in flight. Everything else is arithmetic you do not get to argue with.
 💡 An unbounded queue does not prevent overload. It hides overload until memory becomes the failure mechanism.
 The formula validates. 256 workers at 7.83ms p50 predicts 32,695 solves per second; measured was 32,507, an overhead of 0.57%. Which means capacity planning here is a calculation, not an experiment — measure service time first, then derive the concurrency you actually need.The connection-pool trap is the one that bites scrapers. Go's default MaxIdleConnsPerHost is 2. Past worker three, nearly every operation pays a fresh TCP and TLS handshake. At two thousand workers against one host that is a full round trip added to every request, and it presents as mysterious latency rather than as a configuration error. Set MaxIdleConns, MaxIdleConnsPerHost and MaxConnsPerHost to the worker count and IdleConnTimeout to 90 seconds.Backpressure is one buffered channel. When it fills, the producer blocks and the system slows intake instead of growing the heap. Queue size sets buffering tolerance; worker count sets sustainable rate. They are separate dials and conflating them is how fleets die.Measurement discipline. "Throughput and tail latency only mean something together, and only when you know which stage produced them." Their own mock p99 is called out as unreliable, capped by simulated latency plus jitter rather than reflecting DNS, TLS and retry tails. And the memory arithmetic is the kind nobody does until it hurts: eight-byte latency samples at 8,000 per second accumulate 62.5KB per second, 230MB per hour, 5.5GB per day, and the harness sorts the whole history on every report call, doubling it. Production wants a fixed-size bucketed histogram rotated per window.Credit where due. The article answers its own headline honestly. Asked whether the sample command solves 8,000 real CAPTCHAs per second: "No." The live sample runs about four per second, network-bound, on twelve requests through six workers. The 8,000 is an architecture target across distributed workers, and the piece says so rather than letting the number sit there.
 
 +
 Sep 2026 / Correction
 A Viral Post Said No Dependencies. A Commenter Counted 434.
 A widely shared post presented the Rust agent-first browser this guide already covers as a finished Chrome replacement: 30MB of RAM, 85ms page loads, "no dependencies", "a single binary", "detectors can't catch it", and a drop-in replacement for Puppeteer and Playwright. The comment thread underneath it is a better technical review than the post.
 💡 The wall was never canvas or GPU randomisation. It is the handshake and the exit.
 What the commenters actually established. The dependency claim is false and was counted: 425 third-party crates across six cluster images plus nine workspace crates, 434 total. Several engineers made the obvious point that a browser with no external packages is not a thing that exists. On evasion, a scraping team lead who had built something comparable was blunt — as a bypass layer it is weak, starting at the networking layer, with V8 limitations and a lot of missing Chromium-proprietary surface, and it remains far from a real browser or device. A long-standing newsletter author added the measured version: not true that it cannot be blocked, but a good tool nonetheless.The correction that matters most for practice. The hardest failure point for these engines is not canvas or GPU randomisation, it is the TLS handshake — which is exactly what the independent harness found when chromium, patchright and this engine emitted byte-identical ClientHellos and still finished 44 points apart. Per-session randomisation of GPU, canvas, audio and battery is the axis being advertised, and it is not the axis doing the rejecting.This is consistent with what this guide already recorded. Our own teardown against a hardened commercial target concluded the wall was the IP, not the fingerprint, and the engine's own release notes warn that rendering and Web API behaviour can differ from Chrome. The tool is genuinely interesting for its actual purpose — a cheap, fast, machine-consumer browser for agent workloads, where the rendering pipeline is dead weight. It is not an evasion product, and buying it as one will cost a week.The general lesson, again. A star count is not a measurement, a benchmark on the author's own page is not a benchmark, and "detectors can't catch it" is a claim with a date attached whether or not the poster writes the date down.
 
 +
 Sep 2026 / Economics
 Your Data Is Priced Against Three Thousandths of a Cent a Page
 From the defensive side, a bot-mitigation practitioner put a number on what protection is actually competing with. A bypass for a legacy protection vendor can be bought for as little as 0.3 cents per session, and one session covers 100 to 200 pages — which works out to roughly 0.003 cents per page or less. His line is the one to remember: that is the price your data is being measured against.
 💡 A vendor that updates monthly is selling a subscription to a number that moves weekly
 Why this belongs in a scraping guide. It is the same arithmetic from the other chair, and it explains market behaviour this guide keeps observing. At three thousandths of a cent per page, no amount of per-request friction prices an attacker out of a target with genuinely valuable data; only detection that actually works does. It also explains why bypass-as-a-service exists as a product category at all, and why its pricing is public.His argument to defenders. Solutions that update monthly or less often cannot track threat actors who iterate continuously, so buyers should assess protection vendors themselves rather than defaulting to whichever option is easiest to justify in procurement. "Short-term peace of mind is exactly that: short-term. It does not protect a business model built on unique intellectual property."Read alongside the vendor-drift argument elsewhere in this feed. One side says the anti-bot industry is chasing agent governance while classical bypasses ship; this one says the buyers are choosing on procurement convenience while the bypass market publishes a rate card. Both describe the same gap from different ends, and both are written by people who sell protection, which is worth holding in mind while agreeing with them.
 

 +
 Aug 2026 / Economics
 Learn the Page Once, Then Stop Paying the Model
 This guide's seventeenth principle says to spend the model once at build time and run deterministic code per page. Somebody has now published unit economics for exactly that architecture, and the gap is wider than the principle implies. Two paths: a cold path where a model maps the fields on an unfamiliar page and the structural template is saved, and a hot path where later visits read the live HTML through that stored map with no inference at all.
 💡 $4.38 against $36 and $47 for the same 400 pages
 The published figures. Single page: $0.19 against $0.95 and $1.16 for two well-known AI extraction services. A 400-page run: $4.38 against $36.28 and $47.50. Roughly five to six times cheaper on one page, and eight to eleven times cheaper across a run — because the saving compounds every time the same template is reused.What is actually cached matters. Not page content, and not embeddings. The structure — where the fields live. That is why it stays current: the map is stale only if the layout changes, while the values are read live on every visit. Cache the content and you have a freshness problem; cache the structure and you have a drift problem, and drift is the one this guide already knows how to detect.The honest caveat. These are a vendor's own comparative numbers against named competitors, not an independent benchmark, so treat the ratio as the claim rather than the fact. But the shape is not in dispute, and it is the argument for the pattern: paginated listings and linked detail pages are where one learned template pays for itself hundreds of times over. The expensive question is not what a page costs. It is how many times you pay to understand the same page.
 
 +
 Aug 2026 / Language choice
 Two Reasons a Scraping Engine Gets Written in Go Instead of Python
 A team building an evasive-scraping engine published their reasoning, and it comes down to two things that Python cannot reach from where it stands. Not a general claim about the languages — a specific claim about holding open ten thousand hand-picked TLS connections.
 💡 A goroutine costs about 2KB of stack. A Python thread costs about 8MB.
 The TLS half. Every HTTPS connection opens with a ClientHello whose cipher-suite and extension order is distinctive enough to identify the client. Go's tls-client lets you construct that handshake to match a real Chrome release byte for byte. Python's standard ssl module does not expose that layer at all — you get whatever OpenSSL negotiates, and it does not look like a browser. That is why the Python ecosystem reaches for curl_cffi, which is a binding to a patched C library rather than something the language offers.The concurrency half. Roughly 2KB of stack per goroutine against about 8MB per OS thread, with Go scheduling tens of thousands of them across a handful of threads. A Python thread pool of fifty is already substantial; the orders of magnitude are not close.The part worth stealing regardless of language. They run a multi-armed bandit that learns which TLS profile actually works per site, using Thompson sampling — the same method this guide already documents for choosing between live and dead proxies, applied one layer down. Which Chrome profile to impersonate is exactly the kind of decision that is per-target, drifts over time, and is usually hard-coded once and never revisited. It is not a global setting. It is a per-domain hypothesis you should be updating.
 
 +
 Aug 2026 / The counter-move
 Google Wrapped Every Search Link, and Made One Ranking Cost a Thousand Requests
 Confirmed by Google on 26 August 2026 after a bucketed test that began in June: result links in the SERP are being rewritten to google.com/goto?url=… with a custom encoding, resolving through a 302. Ordinary searchers notice nothing — one extra hop, same destination, no interstitial. Anything that reads a results page programmatically is hit hard. By late August it was near-total across several residential providers.
 💡 500 to 1,000 requests to resolve one five-page ranking, and HEAD is blocked
 Why it costs so much. The encoding cannot be decoded locally, so a tool has to follow each redirect rather than read the address. Derek Perkins of Nozzle put the number on it: resolving all links for a single five-page ranking takes 500 to 1,000 requests. HEAD requests are blocked, so you must issue GETs that follow through.It compounds, it does not replace. The num=100 removal in September 2025 multiplied the number of pages needed and raised operating cost roughly tenfold. This multiplies the requests per page. In Perkins' framing the two changes compound rather than substitute.The design insight, and it is the good one. As one bot-detection researcher put it, this is a speed bump rather than a wall. Client-side detection can be argued with. This cannot: nobody clicks every result on a page, so a whole /24 doing exactly that is self-identifying. You either self-moderate your click-through and lose coverage, or you look like a subnet that reads a hundred links a second. Either way Google gets what it wants.And it may not be a rollout at all. One reading circulating among practitioners is that the wrapper is what Google serves to clients it has already classified — which would make its appearance a detection signal about you, not a product change. Google has published no documentation, no rate limits and no permanence timeline, saying only that it deploys technical measures against evolving abuse.
 
 +
 Aug 2026 / The other side
 A Publisher Spent a Day Trying to Tell Scrapers From Readers. Every Signal Was Already Spoofed.
 A team went looking for a reliable way to separate automated traffic from human traffic on their own site, and worked through the obvious signals one by one. Every single one had already been defeated by the traffic they were trying to catch. The write-up is one of the most honest defender's-eye accounts published this year, and it lands somewhere uncomfortable for both sides.
 💡 The better the impersonation gets, the more dangerous fingerprint blocking becomes
 Signal by signal. User-Agent: their scrapers sent valid, current Chrome strings, so a known-good UA list would have vouched for them. Referer: they found 199 requests carrying an identical hardcoded Google referer across 197 different IPs and 200 different UA strings, all sharing one TLS fingerprint. Cookies: they had logged “has an analytics cookie” as evidence of a real browser — GPTBot carried one on 34% of its IPs, because well-behaved crawlers persist cookies too. Sub-resource loading: assets ship with Cache-Control: immutable, so a returning human fetches only the HTML, which is indistinguishable from an HTML-only scraper.The line worth keeping. On JA3/JA4 they noted that curl-impersonate clones Chrome's handshake exactly, and drew the conclusion most vendors will not say out loud: the better the impersonation, the more the attacker's fingerprint converges on the one your real users have. Fingerprint blocking is safest while your attacker is incompetent, and most destructive the moment they improve. A detection method whose false-positive rate rises with attacker skill is a liability disguised as a control.
 
 +
 Aug 2026 / Measured
 On Amazon, Plain Chromium Tied or Beat Every Anti-Detect Engine Tested
 A proxy vendor open-sourced the harness it uses internally to benchmark scraping tools, and published a result that cuts against its own category. Across 3,816 attempts on Amazon, stock Chromium with no anti-detect layer at all tied or beat every anti-detect engine in the test. The harness runs 11 engines — Camoufox, obscura, CloakBrowser, Patchright, SeleniumBase and six others — against live targets through whatever proxy you point it at.
 💡 It reports which layer refused you: the exit IP, the browser, or the TLS handshake
 Two design choices make it worth more than the usual vendor benchmark. First, it attributes the block to a layer rather than reporting a bare pass rate, so a failure tells you where to spend effort instead of just that you failed. Second, the same browser runs with and without the proxy inside the same time window, so the two are compared side by side rather than an hour apart — which is the flaw that quietly ruins most published comparisons, since target behaviour drifts within the hour.Every finding is said to carry its run id, its denominator and the date it was collected, which is exactly the discipline this guide has been asking for from vendor numbers.Read the Amazon result carefully, because it is a claim about a target, not about the tools. It says Amazon's defence on that path was not keyed to the signals anti-detect engines fix, and that adding one bought nothing there. It does not generalise to Cloudflare or Kasada. The honest takeaway is the one this guide keeps arriving at: measure on your own target before you buy a stealth layer, because the layer that matters is target-specific and the default assumption is frequently wrong. Reported by the harness's author; the repository is days old and not yet independently reproduced.
 
 +
 Aug 2026 / Tooling
 A HAR File Is a Timeline. It Is Not an Explanation.
 When a request works only because several earlier requests prepared its session, tokens, cookies and headers, a HAR capture shows you what happened without showing you why the request could work. Finding the real dependency chain has meant reading traffic by hand. A new open-source tool, Reqvexa, tries to reconstruct that missing layer automatically.
 💡 Provenance based on actual runtime values, not on field names or request proximity
 The central design decision is the interesting part. Rather than guessing from field names, URL patterns or how close two requests are in time, it traces actual values backwards through earlier responses. So instead of reporting that /api/products uses an access_token, it identifies the request that produced that token, and accounts for later mutations of it.What it handles: values that are URL-encoded, Base64, base64url or carried as Bearer tokens; recursive dependency chains; values that change over time; and reduction of a large capture down to just the requests one operation actually needs.The discipline worth copying even if you never run the tool. It keeps observed evidence separate from unresolved hypotheses, and explicitly refuses to treat correlation as proof. That is the same distinction this guide draws about decoded string tables versus values a program actually reads, and about a fingerprinting inventory built from grep. A dependency you inferred from proximity is a guess; a dependency you traced by value is a finding. Early-stage alpha, described here from its author's own write-up.
 
 +
 Aug 2026 / Architecture
 8,000 CAPTCHAs a Second Is Not a Solver Problem. It Is a Queueing Problem.
 The arithmetic is Little's Law and it is worth doing before you buy anything: throughput equals concurrency divided by service time. If each solve takes roughly two seconds, then sustaining 8,000 completions per second requires about 16,000 operations in flight at all times. No solver vendor makes that number smaller. It is a property of how long the work takes and how much of it you can hold open at once.
 💡 Measure service time first, then calculate the concurrency you actually need
 Why this reframing matters. Teams hitting a throughput ceiling reliably go looking for a faster solver, a better proxy or a bigger machine. The binding constraint is usually none of those — it is how many operations the system can keep simultaneously in flight before something upstream starts refusing. Doubling concurrency and halving service time are equivalent on paper, and one of them is usually far cheaper.What actually breaks first, in rough order: HTTP connection pool limits, which cap in-flight work well below what your worker count suggests; unbounded queues, which convert a temporary slowdown into a memory exhaustion; and per-connection memory at sustained volume, which is fine in a burst test and fatal over hours.The controls. Bounded queues with real backpressure, so a slow downstream slows the producer instead of filling RAM. Worker pools with explicit concurrency limits rather than unbounded goroutines or tasks. And measurement at p50 and p99, not the mean — because at 16,000 in flight the tail is what determines whether the queue drains or grows without bound.
 
 +
 Aug 2026 / Correction
 Our Own Camoufox Number Was Five Months Old, and the Channel Moved Underneath It
 This guide quotes a 100% pass rate from March 2026 in four separate places and never once names a Firefox base. That was a mistake of exactly the kind this guide keeps writing about. On 16 July 2026 Camoufox moved its stable channel from FF135 to FF146, with the maintainer describing the old build as terribly out of date and unsuitable for modern anti-bot systems. Every one of those tables has now been dated and flagged.
 💡 The next release restored a humanised mouse trajectory the migration had silently dropped
 Three days is the interesting number. The FF146 migration landed on 16 July. The release three days later carried a fix restoring humanised mouse movement that the migration had dropped. So the recommended stable build spent a short window with one of its behavioural defences missing, and nothing in a version string would have told you.Why a stale table is worse than stale prose. A number in a comparison table gets read out by assistants, pasted into decisions and quoted back months later with the date stripped off. Prose carries hedges; a table cell does not. If you publish a benchmark, publish the build it ran against, because the tool underneath a pass rate can change without the pass rate changing.The general rule, which this guide will now follow. Any performance claim carries three things or it does not go in: the date, the exact build or version, and the targets it was measured against. A pass rate without a base version is a rumour with a percentage attached.
 
 +
 Aug 2026 / Mobile
 The Wall at the End of the Mobile-API Road: Hardware Attestation
 This guide's most repeated instruction is find the mobile or GraphQL API first, and it teaches the whole toolchain to get there — Frida, JADX, Ghidra, certificate unpinning. It has never named the thing that ends that road. Play Integrity, Apple App Attest, DeviceCheck and Android Key Attestation sign their token with a key held inside the device's secure hardware — a TEE, StrongBox or Secure Enclave — that never leaves the chip and cannot be read by any process, rooted or not.
 💡 Every other wall in this guide is forgeable in software. This one is not.
 Why it is different in kind. A TLS fingerprint, a canvas hash, a header order and a proof-of-work token are all things a sufficiently determined program can produce. An attestation token is a signature from a key you do not have and cannot extract. You can reach the call, read the code around it, and watch it happen — and still not mint one. Instrumentation gets you to the door and stops.What actually remains, honestly. Real devices, which means a physical handset farm with the cost and operational drag that implies. Harvesting tokens at low rate from real devices and spending them carefully, which changes your architecture from throughput to budget. Or the answer nobody wants: the endpoint is closed, and the web surface you were trying to avoid is the surface you have.The practical read. Check for attestation before you invest a week in reversing. If the app calls Play Integrity or App Attest on the endpoint you want, the reversing effort has a known ceiling and you should price the alternatives on day one rather than day nine.
 
 +
 Aug 2026 / Law
 Europe Asks a Different Question, and Deploying a CAPTCHA Is Now Part of the Answer
 This guide's legal section has been entirely American — hiQ, Power Ventures, Meta, SerpApi, the CFAA and the DMCA. The European regime asks something structurally different. Not was access authorised, but did you honour a machine-readable reservation, and can you show your balancing test. On 7 July 2026 the EDPB adopted Guidelines 03/2026 on web scraping for generative AI, open for consultation to 30 October 2026, with a final version not expected before year end.
 💡 The inversion: putting a CAPTCHA up changes your legal position whether or not anyone defeats it
 The part that turns 126 cards of technique upside down. The EDPB treats robots.txt, ai.txt, CAPTCHAs and login walls as evidence about what data subjects could reasonably expect. Where a site signals objection through any of them and is scraped anyway, individuals are less likely to have expected the processing, and the legitimate-interest balancing test gets correspondingly harder to pass. A technical measure you bypassed still counts against you, because its legal function is to express an objection rather than to stop you.The test itself is three cumulative conditions under Article 6(1)(f): an interest that is lawful, clearly articulated and real rather than speculative; necessity, meaning no equally effective and less intrusive route exists; and a balance where the data subject's rights do not override it. All three, or the basis fails.Who inherits this. Anyone selling scraped data into an EU-market model provider, and anyone buying an already-scraped dataset from a broker — the guidelines cover obtaining as well as collecting. Draft guidance in consultation, not settled law, and not legal advice.
 
 +
 Aug 2026 / Law
 The First Appellate Ruling on Whether an AI Agent Accesses a Website
 On 4 August 2026 the Ninth Circuit vacated the preliminary injunction that had barred Perplexity's Comet browser assistant from Amazon, in Amazon.com Services LLC v. Perplexity AI, No. 26-1444. The district court had granted that injunction on 9 March. The appellate holding: Perplexity does not “access” Amazon under the CFAA or California's CDAFA, because the assistant is a tool, not a person — it is the user who accesses the site.
 💡 Where the agent executes now decides who the law thinks visited
 The architectural fact that decided it. Comet's assistant runs locally: screenshots are taken on the user's own machine, sent to Perplexity, and instructions come back. The browsing happens on the user's computer, under the user's session. That is why the court treated the human as the accessor.And the case it expressly reserved. The panel left open the opposite configuration — an agent with more autonomy, or one communicating server-to-server with the target. So the local-agent versus cloud-browser choice this guide already frames as an architecture and cost decision now carries different exposure on each side of the line, and the cloud side is the one the court did not bless.Two details squarely on this guide's territory. The dispute began partly because Perplexity would not send an identifying user-agent string, and the court held that refusal irrelevant to whether access occurred. And the opinion preserves the contract route, which means the practical lever moves back to terms of service and technical blocking rather than the computer-crime statute. First circuit to rule on this. Not legal advice.
 
 +
 Aug 2026 / Vendors
 Six Companies Did Not Build All the Walls. We Have Been Reading in One Language.
 This guide's spine has been six companies built the walls — Cloudflare, Akamai, DataDome, Kasada, HUMAN and Imperva. That map is accurate for the English-speaking web and badly incomplete everywhere else. Geetest, Arkose Labs, Tencent's captcha stack, Alibaba's AWSC, aliyun WAF and Radware have appeared in this guide exactly zero times — and with them Taobao, Douyin, Xiaohongshu and AliExpress as targets.
 💡 Arkose sits on Roblox, Microsoft, LinkedIn and X, and this guide had never named it
 The honest framing is the finding. This hole is invisible from inside an English-language source diet. The newsletters, the conference talks, the vendor blogs and the Reddit threads this guide reads are all drawn from one half of the web, and the absence propagates silently: you cannot notice a vendor nobody you follow writes about.A concrete artifact to start from. AliExpress serves collina.js and fireyejs.js from its AWSC path, and they build a WebAudio graph that stays running — a sawtooth oscillator into an analyser into a script processor into a gain node pinned at zero, terminating at the audio destination. Not the one-shot sample this guide's existing AudioContext material describes, but a persistent pipeline. It is conspicuous enough that it has been observed holding Bluetooth multipoint connections open.What that changes about technique. A persistent audio graph is a different detection surface from a one-shot fingerprint: it can observe over time, and it survives page interaction. Anything you learned about spoofing a single AudioContext read does not transfer.
 
 +
 Aug 2026 / Transport
 Skip the HTML, Hit the API — Except the Data Is on a WebSocket
 The most repeated instruction in this guide quietly assumes REST and JSON. A great deal of the data worth having does not arrive that way. Live prices, order books, seat maps, betting odds, inventory drops and chat move over WebSocket, gRPC-web or server-sent events, and none of them behave like a request you can replay from a copied cURL command.
 💡 One long-lived connection defeats per-request rate limiting, and creates a different tell
 What changes mechanically. You do not capture a request, you capture the subscribe frame — the message that tells the server what stream you want. Authentication frequently rides in that first message rather than in a header, so header-focused replay gets you a connected socket that never sends data. You must answer heartbeats and pongs or the server drops you, silently, and your collector looks healthy while receiving nothing.The engineering nobody writes down. Real feeds are snapshot-plus-delta: you get a full state once, then increments. Miss a delta and every subsequent value is wrong while remaining perfectly well-formed — the exact silent-corruption shape this guide keeps warning about, arriving through a different door. You need sequence-number gap detection and a resync path, not a retry.The detection posture differs too. Servers check Origin and the Sec-WebSocket-* headers at the handshake, so the entire fight happens once at connection time rather than per request. That cuts both ways: fewer chances to be caught, and a much longer-lived session to be judged on.
 
 +
 Aug 2026 / Access
 Every Shopify Store Ships an Unauthenticated MCP Endpoint
 Shopify's Storefront MCP exposes product discovery, cart operations, store policy answers and order status at /api/mcp on the storefront domain, with no authentication. On Hydrogen it is live with zero setup because the route proxying that enables it is on by default. Alongside it sits a capability profile at .well-known/ucp that lets you enumerate what a merchant will answer before you write a single selector.
 💡 This is skip-the-HTML made universal, standardised and vendor-documented
 Why it matters at this scale. Anyone currently running headless Chrome against a Shopify storefront is paying browser prices to fight a page while an official, structured, documented door sits beside it. The economics are not close — and unlike a reverse-engineered private API, this one is published, versioned and intended to be called.The questions only this guide would ask, and which are unanswered. What is the rate limit? Is there per-store opt-out, and how many merchants have used it? Does it return the same catalogue as the rendered page, or a subset shaped for conversational use? And what happens to all of the above when merchants notice the traffic?Treat this as a lead with a measurement attached. The mechanism is documented and verifiable. The limits are not, and this guide does not publish numbers it has not measured. The card that follows this one should be an afternoon with a live store, a request counter and a diff against the rendered catalogue.
 
 +
 Aug 2026 / Operations
 The Week the Letters Arrive
 This guide states, correctly, that a cease and desist changes your legal position in a way a robots.txt file does not, and that collectors lose when they kept going after being told to stop. Then it stops at the case summary. What a working data business actually does that week is undocumented, and the community's confident answers to it frequently contradict the case law this guide cites.
 💡 The scope question decides everything and almost nobody asks it first
 Scope. Does compliance mean dropping the site, or dropping the site and every derived row already in the warehouse and in customer deliverables? Those are wildly different costs and the letter usually will not say. Deciding late is what turns a nuisance into an incident.Whether to reply at all, and what we already deleted it does to your position — it can read as good faith or as an admission depending on what you deleted and when. That is a question for a lawyer, but the engineering decision of whether deletion is even possible is yours, and it is decided months earlier by how you stored provenance.The pattern problem. Complying quietly with one letter may be cited by the next sender as evidence you knew. That is an argument for a consistent written policy rather than case-by-case improvisation.The architecture consequence, which is the part this guide can own. Structure the crawl so one domain can be dropped without a rebuild: per-source lineage on every row, per-source scheduling, and no transform that silently merges sources into an unattributable blend. If you cannot delete one source cleanly, you have no operational answer to a letter, only a legal one.
 
 +
 Aug 2026 / TLS
 Post-Quantum Changed the ClientHello, and Your Chrome Profile Did Not
 This guide names JA4 well over a hundred times and has never mentioned post-quantum key exchange. The hybrid key share adds roughly 1,088 bytes to the ClientHello, pushing it past 1,400 bytes, which changes fragmentation behaviour and the JA4 string itself. A client presenting a modern Chrome user agent without a hybrid key share now matches no real Chrome in existence.
 💡 This check completes before a single byte of HTTP is exchanged
 Why it is a clean tell. It is not a heuristic about behaviour or a probabilistic fingerprint score. Real Chrome sends a hybrid post-quantum key share; a library pinned to a pre-PQ profile does not. The mismatch is categorical, it is visible in the handshake, and it costs the defender nothing to check.The scale of the change. A June 2026 measurement across roughly 32,000 domains found close to half already supporting hybrid post-quantum key exchange, and Akamai made it the default for client-to-Akamai connections at the end of January 2026. This is not an edge case any more; it is the majority path on protected infrastructure.What to do about it. Check what your impersonation library actually sends rather than what its profile name claims. A profile called chrome131 that predates the PQ rollout will happily identify itself as a browser that no longer exists. This is the same failure as the Camoufox correction above: a version label is not a measurement.
 
 +
 Aug 2026 / Evidence
 Three Quarters of the Headless Penalty Is a String in a Header
 A June 2026 study ran 40,000 visits across the Tranco 10K in a two-by-two matrix of engine and display mode, and produced the population-level numbers this guide has been quoting anecdotes about. Soft-block rates: Chromium headless 15.2%, Chromium headed 7.2%, Firefox 6.8% either way. By provider: Cloudflare 37.0%, Akamai 26.4%, Fastly 15.6%, Microsoft 14.8%, Google 5.1%.
 💡 Correcting the headers to drop the HeadlessChrome token cleared 75% of headless-only blocks
 Read that number carefully, because it reorders the whole ladder. Three quarters of the penalty for running headless was not a runtime fingerprint, not a missing plugin array, not a canvas difference. It was a token in the user-agent string. The cheapest possible fix recovered most of the gap, and everything this guide says about escalating cheapest-first is vindicated by it.The other number worth holding. Only 34% of sites probe navigator.webdriver at all. The single most-patched property in the stealth ecosystem is checked by barely a third of the web, which says something uncomfortable about where collective effort has gone.What this is good for. It gives a denominator. When a vendor quotes a success rate, or a blog post claims a technique works, you now have a population baseline to compare against: a plain headed Chromium already passes roughly 93% of the Tranco 10K, so any claim of a large improvement needs to say on which subset.
 
 +
 Aug 2026 / Security
 You Are Executing Attacker-Controlled Code Inside Your Own Network
 Every injection card in this guide is about an agent being talked into something by page text. The plainer version has never been written down. On every browser-based rung of the ladder you execute attacker-controlled JavaScript inside your VPC, decompress attacker-controlled bytes, follow attacker-controlled URLs, and parse attacker-controlled XML and SVG. The target chooses the payload. You chose to run it.
 💡 The cloud metadata endpoint is one fetch away from a browser you told to follow links
 Server-side request forgery is the sharp one. A crawler that follows discovered URLs will, given the chance, fetch internal addresses — including the cloud instance metadata service, which on a misconfigured instance hands out credentials. Your crawler has network position that an external attacker does not, and you built that position deliberately.Decompression bombs are the cheap one. A few kilobytes of gzip can expand to gigabytes, and a fleet of browsers each holding one is an outage you paid for. Cap the decompression ratio and the response size, not just the timeout.The controls are boring, specific and mostly missing. An egress allowlist on the browser fleet so it can reach targets and nothing else, especially not link-local metadata. Response-size and ratio limits before parsing. A patch cadence for headless Chrome that matches the one you would apply to a public-facing service, because that is effectively what it is. And no credentials on the machines that render pages.
 
 +
 Aug 2026 / Operations
 Diagnosis Is Half an Incident. This Guide Skips the Repair.
 There is a lot here about telling a 429 from a 403, attributing a failure to the proxy rather than the site, and catching silent corruption. Then the story ends at the moment you understand the problem. Backfill, re-queue, runbook and postmortem appear nowhere in this guide, and repair is where the actual data loss is decided.
 💡 Merging recovered rows into a table that already holds partial rows for that window is the hard part
 The four questions a repair actually poses. How far back does the gap go, given that detection lagged the break? Which rows already landed for that window, so the merge does not double-count — and this is where an upsert on a stable natural key earns its entire existence. Is the history even retrievable, or does the source only expose current state? And what do you tell the customer when the answer is no?The unfillable case is more common than people admit. Price pages, inventory levels and rankings frequently have no history endpoint. If your collector was down for six days, those six days do not exist and no amount of re-queueing conjures them. That should be a stated property of each source, recorded when you onboard it, not discovered during an incident.Note the one partial exception. Sometimes the history does exist — just not on the source. See the next card, because a public archive is the only answer this problem has.
 
 +
 Aug 2026 / Data quality
 The Value Parses Perfectly and Means Something Else
 This guide's data-quality work covers the wrong value and the missing value. It has never covered the correctly-typed, in-range, entirely plausible value that is silently wrong because of encoding, locale or units. 1.234,56 read as one point two three four. 03/04 read as March when the site meant April. A price in the currency of the exit node's locale rather than the market you are tracking. Full-width CJK digits that a naive parser drops.
 💡 Every one of these passes a range check, which is why range checks are not enough
 Why this class is uniquely nasty. A missing field is loud. A wrong-by-a-thousand field is loud if you look. A date silently transposed between day-first and month-first is never loud — it is a valid date, in range, in the right column, and wrong for eleven days out of twelve. It corrupts time series in a way that survives every schema test you own.Where it comes from, specifically. Locale-dependent thousand and decimal separators. Ambiguous date orders with no timezone. Currency inherited from geolocation rather than declared by the page. Character-set guessing when the server lies about its encoding, producing mojibake that looks like a data-entry error rather than a pipeline bug. And multi-market sites serving different units to different exits.The fix is a declaration, not a checker. Record the expected locale, currency, date order and encoding per source at onboarding, and assert against that declaration rather than against generic plausibility. Then a mismatch is a failure instead of a number.
 
 +
 Aug 2026 / Method
 Before You Fight the Site, Check Whether Somebody Already Has the Page
 The Internet Archive's CDX API and the Common Crawl index appear nowhere in this guide, and between them they are the cheapest first move against a hard target and the only answer to an unfillable history gap. Both let you query what URLs exist and what captures are available before you send a single request to the origin.
 💡 It is also the only way to get back a window your collector missed
 Two distinct uses. First, frontier seeding: pull the known URL set for a domain from an index at zero block risk, so you arrive knowing the shape of the site instead of discovering it by crawling. Second, historical recovery: when your collector was down and the source exposes no history of its own, an archive capture is the only place that window still exists.The caveats, stated honestly, because they decide whether this works for you. Crawl cadence is nowhere near daily for most domains, so gaps in the archive are common. Coverage is popularity-biased, so the long tail is thin exactly where you often need it. JavaScript-rendered content is largely absent, which rules out a lot of modern commerce pages. And the licence terms on what you may do with the data are not the same as the terms on data you collected yourself.The judgement. It is a poor primary source and an excellent reconnaissance and recovery tool. Checking costs one API call. Not checking has cost people weeks.
 
 +
 Aug 2026 / Defence
 A Peer-Reviewed Defence Built Specifically to Break LLM Scrapers
 Published at IEEE S&P 2026, WebCloak pairs dynamic structural obfuscation with what its authors call an optimised semantic labyrinth, and benchmarks it across 237 pages from 50 high-traffic sites against 32 scraper implementations. The reported effect on extraction recall is a collapse from 88.7% to zero. The code is public, which means it will be deployed.
 💡 It also supplies the taxonomy this guide has been missing
 Three consumer types, and they do not fail the same way. WebCloak distinguishes LLM-to-Script (a model writes an extractor, deterministic code runs it), LLM-Native crawlers (the model reads the page each time), and LLM web agents (the model drives a browser). That distinction predicts which defence hurts which of your pipelines — structural obfuscation punishes the generated selector, semantic mazes punish the model that reads, and the two together punish the agent.Why this matters more than an average anti-bot product. It is reproducible, published and free. The LLM-honeypotting card in this guide describes sites that lie; this is that idea peer-reviewed, benchmarked and shipped as something a defender can install on a Friday.The defensive read for a collector. If your pipeline is LLM-to-Script with cached deterministic extractors — the pattern this guide already recommends on cost grounds — you are in the category that structural obfuscation targets most directly. Regenerating an extractor against an obfuscated page produces a confident, wrong extractor. Your verification gate against golden fixtures is the control that catches it.
 
 +
 Aug 2026 / Policy
 Verified Status Now Means Identity Only, and You Can Lose It For What You Do After
 Cloudflare has decoupled verification from access. Being a verified bot now attests who you are and nothing else; whether you get in is decided per category — Search, Agent, or Training — by each site. More consequentially, what a bot does with content after crawling now feeds back: a verified bot that reproduces content in full can lose verification, and therefore lose access across the network.
 💡 This is the first time robots.txt has had an enforcement lever attached
 Why that qualifies one of this guide's own cards. We have argued that robots.txt works perfectly on the bots that were going to comply anyway, and that remains true for anonymous collectors. But for an identified crawler that wants network-wide access, a declared preference now has a consequence attached to ignoring it. The file did not gain teeth; the identity layer beside it did.The category split is the practical part. A site can say yes to search indexing and no to training while allowing agent traffic, which means the same crawler is welcome or unwelcome depending on what it says it is for. Declaring purpose accurately becomes an access strategy rather than a courtesy.And a directory now exists. Classifications are searchable, so how your crawler is categorised is a public fact about your business that customers and targets can look up. That is a reputational surface most collectors have never had to manage.
 
 +
 Aug 2026 / Identity
 Agent Identity Is Being Built on the Payment Rails, Not the Web Ones
 This guide covers agent identity as a web-infrastructure problem — Web Bot Auth, signatures, verified crawlers. The track with actual enforcement teeth is somewhere else: Visa's Trusted Agent Protocol and Mastercard's Agent Pay, both riding on the same signature scheme, with Akamai wiring agent trust into bot management in mid-2026. It gates checkout rather than page access, which is why it will win.
 💡 The sign flipped: AI-driven retail traffic now converts better than human traffic
 The number that changes the incentive. In March 2025, retail traffic arriving via AI converted materially worse than non-AI traffic. By March 2026 the measured position had reversed, with AI-referred traffic converting substantially better. Once agent traffic is your best-converting segment, a false positive on your bot filter is not a security win, it is lost revenue with a name attached.Why payment rails beat web standards here. A web-infrastructure identity standard needs adoption on both sides and gives a site little immediate reason to comply. A payment-network standard arrives attached to the money: merchants adopt it because the acquirer, the scheme and the chargeback rules say so. Enforcement follows the settlement, not the specification.What that means for a collector. Identity is bifurcating. Anonymous collection keeps getting harder and cheaper to detect. Identified, purpose-declared, payment-attested agent traffic is being actively courted. Those are not two points on one spectrum — they are separate roads, and the second one is being paved much faster.
 
Nothing matches that. Try a shorter word, or clear the filter.

---

## TESTING AND DETECTION TOOLS

11 Testing tools
Check your ownfingerprint first
Before you bypass anything, you need to know what your setup is leaking. These tools show exactly what anti-bots see when your scraper connects. Run your scraper through them, not just your browser.

 Gold standard, most detailedBrowserLeaksbrowserleaks.comThe most comprehensive fingerprint testing suite online. Tests WebRTC IP leak, Canvas hash, WebGL renderer, JA3/JA4 fingerprint, HTTP/2 Akamai hash, Chrome extension detection, fonts, geolocation, JavaScript environment, battery status. Essential for verifying your scraper identity stack.
 TLS specific, generates JA3/JA4BrowserLeaks TLSbrowserleaks.com/tlsTests your TLS ClientHello. Shows cipher suites, TLS extensions, key exchange groups, JA3 and JA4 hashes. Run Python requests, curl_cffi, and real Chrome through this and compare. JA4 is what Cloudflare and Akamai check at edge before serving any HTML.
 IP leak, proxy coherence testBrowserLeaks WebRTCbrowserleaks.com/webrtcReveals your real IP even through a proxy. Shows local IP, public IP via STUN, and ICE candidates. If your proxy exit is US but WebRTC shows a local Pakistani address, every anti-bot flags you immediately. The most commonly overlooked leak.
 JSON API, use directly in codeTLS JSON APItls.browserleaks.com/jsonReturns your TLS fingerprint as raw JSON including ja3, ja4, akamai hash, HTTP/2 settings. Call this directly from your scraper to compare fingerprints against real Chrome. One requests.get() vs cffi.get() tells you everything about the difference.
 Quick pass/fail validationBrowserScanbrowserscan.netHigher-level green/red check for automation detection, timezone coherence, WebRTC status, canvas fingerprint uniqueness. Good for quick pre-deployment validation before hitting a protected target.
 EFF built, uniqueness scoreCover Your Trackscoveryourtracks.eff.orgBuilt by the Electronic Frontier Foundation. Tells you how unique your fingerprint is among all visitors. A fingerprint too unique is as bad as one that looks like a bot. Scrapers need to look like the middle of the distribution.
 Bot detection simulationPixelscanpixelscan.netSimulates what anti-fraud systems see. Identifies inconsistencies in timezone, IP, language, and WebRTC that would trigger detection. Fast pass/fail for operational teams before deploying at scale.
 Advanced, behavioral and hardware signalsCreepJSabrahamjuliot.github.ioThe most advanced fingerprint tester available. Simulates what modern anti-fraud systems actually detect, including behavioral and hardware-level signals far beyond surface tests. Use this for deep audits of browser configurations.
 Diff your bot against a real browserxray-scanner + script2builtinscatalog-driven fingerprint diffA different kind of test: instead of scoring your browser, it shows you exactly which surfaces leak. The toolchain reverse-engineers bot-detection JavaScript (seeing through string-array tables, JSF-ck, and reflective getters) into a catalog of several hundred fingerprint surfaces, then gives you a test page you point your bot at and diff its JSON against a real browser. You stop guessing which attribute betrayed you and read it off a list. Pair it with a daily benchmark run so you notice when a detector quietly changes what it probes.

Workflow: Fetch tls.browserleaks.com/json from both your scraper and real Chrome. Compare ja4 hashes. If they differ, fix TLS first with curl_cffi. Then check WebRTC at browserleaks.com/webrtc. Then headers. Work from layer 1 outward.One habit worth building: re-run these checks on a schedule, not once. Detection scripts are not static. A vendor that probed one set of surfaces last week probes a different set this week, so a fingerprint audit you did a month ago is already stale. Treat your own fingerprint as something to monitor continuously, the same way the other side treats yours.

The wire-level capture loop: how you actually close the gap on a blocked request#

Fingerprint checkers tell you what you look like in the abstract. This is the workflow for the specific case where your script gets a 403 on an endpoint your browser loads fine, and it is the single most useful loop in request-based scraping. The premise is the one this guide keeps returning to: finding the endpoint is the easy fifth of the work, making your request indistinguishable from the browser\'s is the other four fifths, and almost none of that is visible in DevTools. Three things it structurally cannot show you. Your TLS fingerprint, which is decided before a single header is sent and never appears in the Network tab at all. Your header order, because DevTools alphabetises headers for display, so the sequence you copy out is not the sequence Chrome actually sent, and copy-as-cURL bakes that wrong order straight into your script. And exact case and values, where HTTP/2 lowercases every name while HTTP/1.1 uses Title-Case, and a value has to match to the token (Chrome has advertised accept-encoding: gzip, deflate, br, zstd since Chrome 123, so a request still sending the three-value version no longer looks like current Chrome). You cannot fix what you cannot see, so you need a proxy that records the real wire.

The loop itself. (1) Record the browser first, from a clean profile, because that capture is your source of truth. (2) Search the whole session for each dynamic value on the request you want, matching across URLs, headers and response bodies. A token like an Imperva reese84 cookie echoed as an x-d-token header will turn up in three places: the response that minted it, the cookie that carried it, and the header that spends it. That chain, not the endpoint, is the real output of reading a session, because it tells you which requests your script has to make first. (3) Write a first pass with a browser-grade TLS client, routed through the same capture proxy. (4) Diff your request against the browser\'s in wire order and fix every difference until the diff is empty. The differences that block you are usually one token wide, a missing zstd, a stray Host header on an HTTP/2 request, priority not sent last, or headers in alphabetical order which is itself a dead giveaway that they came out of a dictionary rather than a browser. (5) Replay one captured request over and over and watch for the flip from 200 to 403. That number is how many calls a single warm session survives, and you set your rotation threshold just under it, which is how the session-stickiness advice in the proxy section turns into an actual figure instead of a guess.

 
 
 The test that saves the most hours
 Same IP, browser versus script
 The question that eats afternoons is "is it my proxy or is it my code", and there is a clean way to settle it. Point your capture proxy\'s upstream at the exact proxy your scraper uses, so the capture exits from the same IP the target will see. Run your script through it, then, without changing anything else, point a real browser through the same chain and load the site by hand. If the browser is also blocked on that IP, the problem is the network: the address is flagged, or its geo or ASN is wrong, and no amount of header work will help. If the browser sails through while your script is blocked on the same IP, the problem is entirely in your request, so go back to the diff. One test, one tool, and you stop chasing the wrong layer.
 
 
 
 Capture hygiene, or you debug a ghost
 Your tooling can corrupt the evidence
 A capture is only useful if it is honest, and several common mistakes quietly poison it. Your interception proxy itself can be the problem: some rewrite the ClientHello enough that anti-bots block the proxy rather than your script, and at least one popular tool moves Content-Length to the bottom of the header block when chaining to an upstream proxy, corrupting the exact ordering you opened the tool to inspect. Verify your tool preserves wire order before you trust its output. Turn off the DevTools "disable cache" option while recording, because it injects Cache-Control and Pragma headers a real navigation never sends and you will copy them into your script. Record from a clean, ordinary profile, not incognito and not one that merely had its cookies cleared, since both produce artefacts like extra Client Hints on the first request. And route only the client you are debugging, not the whole system, or the session drowns in traffic from every app on the machine. One more detail people miss: header order is per request type, so a navigation, an XHR, a subresource and the anti-bot\'s own endpoint each have their own order. Capture the specific request you intend to replay.
 

Two closing notes. Keep the whole chain consistent while you work: the IP your capture exits from should be the IP your scraper uses, on a sticky session rather than a rotating one, or you are comparing captures taken from different identities. And this is a workflow a model is unusually good at once the data is in a readable form, since exporting the session (or exposing it over a local MCP server) lets an agent read wire-order headers, the TLS handshake and the raw HTTP/2 frames and point straight at the mismatched header, which is the same diff you would do by eye, done faster.

Two capture mistakes that waste a whole afternoon#
Name the tool, then distrust it. The loop above needs a proxy that records the real wire, and the one most request-based practitioners settle on is powhttp, which is built for exactly this comparison and has almost no documentation, so budget an hour to learn it. Whatever you choose, verify it preserves header order before you trust a single diff it shows you, because an interception proxy that quietly reorders headers turns the tool you opened to inspect ordering into the thing corrupting it.Record from an ordinary profile, and understand what incognito actually leaks into your capture. The advice to avoid private windows when recording is usually given without a reason, so it gets ignored. The reason is specific: a session recorded in Incognito or Guest mode carries artefacts a normal navigation never sends. You may see Sec-Fetch-Storage-Access: none, and the payloads that anti-bot scripts assemble can contain stricter third-party-cookie and storage-partitioning signals that reflect the private context rather than the browsing you are trying to imitate. Replay that capture and you are faithfully reproducing a browser state no ordinary visitor is in, which is a coherent-looking request that is coherent about the wrong thing. Practitioners working request-based DataDome flows report this as one of the most common causes of a session that was recorded wrong from the first byte, where the TLS is right, the header order is right, and the replay still fails.

Detector scripts change faster than your notes. One researcher running an analyser against fourteen live bot detectors every day reports two things worth internalising. Every outbound request those scripts make is attributable to its infrastructure, so the data collected about a browser has a return address and can be watched. And the probes themselves shift constantly: a detector that measured one set of surfaces last week measures a different set this week. An audit performed once and written down is already drifting. If you maintain any fingerprint documentation, put it on a schedule, or accept that it describes the past.

 3 field notes on testing tools
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Instrument
 A New Fingerprint Mirror, and a Firefox Bug That Linked You Across Tabs
 Two fingerprinting items worth holding together. Glassbox is a new browser-fingerprint tool that runs entirely client-side and dumps the raw, unfiltered signals plus an identifiability estimate. And Fingerprint's research team disclosed a Firefox flaw that let unrelated sites link your activity across a running browser — no cookies, no storage, no explicit channel, now patched.
 💡 The check-your-scraper tools keep getting sharper; so do the leaks they measure
 Glassbox as an instrument. It joins the guide's testing-tools shelf next to BrowserLeaks, CreepJS and Cover Your Tracks, but its angle is the raw dump: rather than a pass/fail, it shows every attribute a tracker or anti-fraud script would read and estimates how identifiable the result is, entirely in-browser with nothing shipped out except a geolocation ping. That's the right shape for auditing a scraper's browser — you want to see the exact surface you present, not a green tick. Pair it with a daily capture so you notice when a target quietly starts probing something new. (The counterweight, from a fingerprinting professional: most of the fingerprint demos trending this month are vibe-coded toys that derive identifiers that work on your one machine but have no real-world scale behind them — a check-tool is for reading your own surface, not for judging what a serious detector actually keys on.)The Firefox bug as evidence of the layer below. Fingerprint's team found that Firefox-based browsers let one site link activity to another across a live browser process — the exact cross-context linking that cookies were supposed to be required for, achieved with none. Responsibly disclosed to Mozilla and the Tor Project, fixed in Firefox 150 and ESR 140.10.0. It matters here because it's a reminder that the browser's attack surface is enormous and shifting: the same primitives that leak a user's identity across tabs are the ones a detector can read to tell your automated browser from a human's, and they change version to version. Whatever you audited on the last build is not guaranteed on the next.Sources: Glassbox (via The Register); Fingerprint research, fixed in Firefox 150 / ESR 140.10.0.
 
 +
 Aug 2026 / Instrument check
 “I Published a Performance Chart Five Weeks Ago. It Was Measuring Nothing.”
 The benchmark cards in this feed are about distrusting somebody else's numbers. This one is worse and more useful: an engineer went back to turn his own published benchmark into a regression test, deleted the optimisation it existed to prove, and everything still passed.
 💡 Delete what a test guards. If it still passes, it was never watching your code.
 The flaw, which is worth stealing because it is so easy to commit. The benchmark constructed its own query inside the test file rather than driving the endpoint the application actually serves. The two matched on the day they were written and then drifted apart freely. So it was answering “does this framework's eager-loading feature work?” — a settled question about somebody else's library — instead of “does the code I deploy still do this?” Fifteen tests passed in 4.67 seconds and the benchmark printed its usual flat number while the real regression sat there untouched. His phrase for it: simultaneously correct and useless.Driving the real entry point changed the answer. The true count was four, not three, because the paginator's COUNT(*) was invisible from where the benchmark stood. That gap sounds trivial and is not: one query in four is nothing in a latency budget, but an assertion written against the wrong number fails immediately and forever on correct code, which is how a well-meant regression test gets deleted by the next person.The replacement is the transferable idea. Not a benchmark that prints a number, but a query budget — a fixed count asserted against the real endpoint at two different data sizes, so flatness is proven rather than assumed, with each query named in a comment so any future change has to be justified rather than absorbed. A benchmark prints a number. A regression test refuses to let it change.Why this belongs in a scraping guide. Substitute your own instruments and the argument survives intact. A health check that builds its own request instead of running your scraper's actual client is measuring the library, not your pipeline. A coverage alert whose fixture was captured once and never re-driven is measuring history. The guide already says to check the instrument before trusting the verdict; this is the procedure that operationalises it — delete the thing the check protects and watch it go red. If it does not, you have been reassured by nothing for however long it has been green.
 
 +
 Aug 2026 / Debugging
 The Proxies Were Fine. The Tool Measuring Them Was Broken.
 Weeks lost to a certainty: every request to one site came back blocked, three providers swapped, identical failure each time. That identical failure read as proof the IPs were burned. It was proof they were irrelevant.
 💡 When something fails identically everywhere, the thing you keep swapping is not the cause
 The actual fault: the scraper was shaking hands like a browser from two years ago while presenting a user agent for the newest version. No real browser can do that, the TLS stack and the version string are welded together, so the detector did not need to be clever. It noticed the contradiction and closed the door, from every IP, from every provider. Straight back to the coherence rule that runs through this whole guide.The second half is the part almost nobody writes about, and it is the more useful lesson. The tool being used to check whether a proxy was alive did not look like a browser either, so it got blocked too, always. That means the instrument was reporting healthy proxies as dead and actively confirming the wrong theory. Weeks of evidence, all of it generated by a broken measurement. If your health check is less capable than your scraper, every reading it gives you is noise dressed as data, and you will keep buying the conclusion it hands you.The fix is worth copying as a pattern, not just a value. Rather than finding the correct setting, they made the wrong one impossible to express: the handshake profile and the user agent now travel together as a single unit, so you cannot select one without the other. The bug cannot be recreated by a future edit. That is the difference between fixing a defect and removing a class of defect, and it is the same instinct as binding a fingerprint's axes together rather than configuring them independently. Result: roughly 0% to 90% success, with exactly one block across the following 50,000 requests.

---

## PRODUCTION ARCHITECTURES

12 Architecture
How production scrapersare actually built
From a single Scrapyd daemon to multi-region ECS clusters. Twelve real pipeline architectures, from simple to enterprise-scale, with every component and data flow mapped out.

 
 
 
 
 
 
 
 
 
 
 
 

The simplest production setup. One server, Scrapyd managing spiders via JSON API, ScrapydWeb as UI. Good for <50 spiders and teams without Kubernetes. Deploy with scrapyd-deployschedule via /schedule.jsonmonitor at port 6800.

 
 
 
 
 
 
 💻
 Developer
 + codebase
 
 
 
 scrapyd-deploy

 
 
 SCRAPYD SERVER :6800
 
 
 🕷️
 Scrapyd Daemon
 JSON API + job queue
 
 
 Spider 1
 process
 
 Spider 2
 process
 
 
 ↻ Rotating Proxy
 scrapy-rotating-proxies

 
 
 HTTP request

 
 
 
 🌐
 Target Site
 anti-bot protected
 

 
 

 
 
 
 📦 Storage Pipeline
 

 
 
 
 SCRAPYDWEB
 🖥️
 Visual Dashboard
 port 5000
 
 
 monitors via API

 
 
 
 SCHEDULER
 Cron / APScheduler
 POST /schedule.json
 
 

 
 
 
 🔔
 Slack / Email
 job alerts
 
 

 
 
 ✓ Pros
 Zero infrastructure overhead, one server, doneScrapydWeb gives full UI: logs, job history, scheduleDeploy new spiders in seconds with scrapyd-deployGreat for teams without DevOps expertise
 
 
 ✗ Cons
 Single point of failure, server down = scrapers downLimited to one machine's CPU and memoryNo auto-scaling, manual capacity planningSpider isolation is process-level only
 
 
 ↑ Scale up
 Add more Scrapyd nodes → ScrapydWeb manages cluster from one UI. Next step: scrapy-redis for shared URL queue.
 
 
 
 Stack
 Scrapyd :6800ScrapydWeb :5000scrapyd-client (deploy)APScheduler or cronGerapy (alt UI)
 

Distributed crawling across multiple nodes using scrapy-redis. Redis acts as the shared frontier, all spiders pull URLs from the same queue and push items to the same pipeline. Horizontally scalable: add worker nodes without changing spider code.

 
 
 

 
 
 
 📦
 Git Repo
 codebase
 
 
 CI/CD

 
 
 MASTER NODE
 
 Scrapy Master
 seed URLs + schedule
 
 
 Scrapyd :6800
 
 
 ↻ Proxy rotator

 
 
 push seeds

 
 
 
 REDIS
 ⚡
 Shared Queue
 URL frontier + dedup
 

 
 
 WORKER NODES
 
 
 Worker 1
 Scrapyd + RedisSpider
 
 
 Worker 2
 Scrapyd + RedisSpider
 
 
 Worker N...
 add nodes to scale

 
 
 
 
 BLPOP

 
 
 items

 
 
 
 STORAGE
 Postgres / S3
 item pipeline
 

 
 
 
 push new URLs discovered

 
 
 
 ScrapydWeb, monitors all nodes from single dashboard
 

 
 
 ✓ Pros
 Horizontally scalable, add workers with zero config changeRedis deduplicates URLs automatically across all nodesOne spider codebase, N workersCheap to run on commodity VMs
 
 
 ✗ Cons
 Redis is a new SPOF, needs replication for productionNo built-in job scheduling (add Celery or cron)Worker failure loses in-flight items unless pipelines are idempotentHarder to debug distributed spider behaviour
 
 
 ↑ Scale up
 Replace VMs with Kubernetes pods. Add scrapy-cluster for multi-project support. Use Redis Cluster for HA.
 
 
 
 Stack
 scrapy-redisRedis / Redis ClusterScrapydWebCelery (scheduling)scrapy-cluster (enterprise)
 

Production AWS pipeline: Scrapy spiders containerised and deployed to ECS Fargate tasks. EventBridge triggers runs on schedule, Step Functions orchestrates multi-step flows, results land in S3 data lake then processed to RDS. Built and maintained this pattern in production.

 
 
 

 
 
 CI / CD
 
 
 📁
 GitHub
 
 
 
 
 ⚙️
 Actions
 
 
 
 
 📦
 ECR
 
 push Docker image

 
 
 deploy

 
 
 ECS FARGATE
 
 🕷️ Scrapy Spider Tasks
 N concurrent Fargate tasks
 
 ↻ Rotating Proxy
 
 CloudWatch logs

 
 
 
 EVENTBRIDGE
 ⏱ Cron schedule
 
 

 
 
 
 STEP FUNCTIONS
 orchestration
 
 

 
 
 push data

 
 
 
 S3 DATA LAKE
 🪣
 raw JSON / Parquet
 

 
 
 
 
 GLUE CRAWLER
 schema detect
 → Athena tables
 

 
 
 
 
 LAMBDA / EMR
 transform + clean
 

 
 
 
 
 RDS / DWH
 🗄️
 Postgres / Redshift
 

 
 
 
 
 DASHBOARDS
 📊
 Tableau / Retool
 

 
 
 
 CLOUDWATCH
 monitoring + alerts
 
 

 
 
 ✓ Pros
 Fully serverless compute, no EC2 to manageAuto-scaling Fargate tasks based on queue depthNative AWS observability via CloudWatchStep Functions gives retry logic and error handling for free
 
 
 ✗ Cons
 Cold start latency on Fargate (~15-30s per task)AWS vendor lock-in across the whole stackCost spikes with high concurrency, needs spending alertsDebugging distributed Fargate tasks is harder than local
 
 
 ↑ Scale up
 Move from Fargate to EKS for better cost control at scale. Add SQS queue between EventBridge and spiders for burst buffering.
 
 
 
 Stack
 AWS ECS / FargateEventBridge (schedule)Step FunctionsS3 + Glue + AthenaLambda (transform)CloudWatch + SNSECR (container registry)
 

Enterprise-scale reference pattern: a large spider fleet on Kubernetes (EKS), ingesting to an S3 raw data lake, Glue cataloguing, Redshift Spectrum for external tables, and Bronze/Silver/Gold data layers, orchestrated by Apache Airflow. This is the shape such platforms typically take, not a description of any particular deployment.

 
 
 
 
 
 CI / CD
 📦
 GitHub → Actions
 → update EKS deployment

 

 
 
 ORCHESTRATION
 Apache Airflow
 job scheduling
 → Slack job alerts
 
 run job

 
 
 EKS (KUBERNETES)
 
 🕷️ Scrapy Pods
 500+ spiders, N pods each
 
 ↻ Proxy
 
 CW Logs
 
 EKS Job Data Ingestion

 
 
 write data
 
 
 S3 RAW DATA
 🪣
 JSON / Parquet
 

 
 
 crawl schema
 
 
 GLUE CRAWLER
 🔍
 data catalog
 

 
 
 create table
 
 
 ATHENA
 🔎
 external tables
 

 
 
 DATA WAREHOUSE

 
 external query

 
 
 Redshift Spectrum
 
 
 
 
 🥉 Bronze (RDL)
 
 
 
 
 🥈 Silver (ODL)
 
 
 
 
 🥇 Gold (ADL)
 
 
 
 
 → Dashboards
 

 
 
 
 MONITORING
 AWS CloudWatch → Slack alerts
 

 
 
 ✓ Pros
 Redshift Spectrum queries S3 directly, no ETL for ad-hoc analysisBronze/Silver/Gold medallion enforces data quality gatesAirflow DAGs are version-controlled and reproducibleEKS scales to hundreds of concurrent spiders
 
 
 ✗ Cons
 High operational complexity, EKS + Airflow + Redshift is a big surface areaExpensive at rest, Redshift cluster runs 24/7Airflow needs its own HA setupLong data latency, batch-oriented, not real-time
 
 
 ↑ Scale up
 Add Kafka between spiders and S3 for real-time streaming. Replace Airflow with AWS MWAA (managed). Use Redshift Serverless to cut idle cost.
 
 
 
 Stack
 EKS (Kubernetes)Apache Airflow / MWAAS3 + Glue CrawlerAmazon AthenaRedshift SpectrumBronze/Silver/Gold layersCloudWatch + Slack
 

Self-hosted distributed Scrapy cluster on VPS, no cloud needed. Scrapy Master distributes crawl jobs to Worker nodes via Docker. Apache Airflow handles scheduling. Results go to local NFS data lake, then ETL to SQL Server. Cost-effective for mid-scale scraping without AWS spend.

 
 
 

 
 
 
 CODEBASE
 📁
 All Code
 
 

 
 
 
 🐳 Docker Registry
 
 ⚙️ GitHub Actions
 CI/CD pipeline
 
 

 
 
 SCRAPING CLUSTER (Docker)
 
 
 VPS / Docker Host
 start cluster
 
 
 Scrapy Master
 distribute jobs
 
 
 Scrapy Workers
 N containers
 
 
 
 ↻ Rotating Proxy
 
 Raw Data
 NFS / Local
 
 route requests

 
 
 
 ORCHESTRATION
 🌀
 Apache Airflow
 schedule + status
 → Slack alerts
 
 

 
 
 
 PROCESSING
 ETL / Cleaning
 Pandas + SQL
 upsert to warehouse
 
 
 read raw
 
 

 
 
 
 WAREHOUSE
 🗄️ MS SQL Server
 
 📊 Tableau
 Dashboards
 

 
 
 ✓ Pros
 Cost-effective, VPS Docker is 5-10x cheaper than EKS at same scaleFull control over the stack, no cloud lock-inAirflow is battle-tested for complex DAG schedulingNFS shared storage is simple and fast for the cluster
 
 
 ✗ Cons
 Manual server provisioning and patchingNo managed auto-scaling, capacity planning is manualNFS is a bottleneck and SPOF for data writesDocker Swarm has less ecosystem than Kubernetes
 
 
 ↑ Scale up
 Graduate to Kubernetes (K3s or full K8s) when spiders exceed 50. Replace NFS with S3-compatible storage (MinIO). Add Prometheus + Grafana for metrics.
 
 
 
 Stack
 Docker / Docker SwarmApache AirflowNFS / local storageMS SQL ServerTableau / GrafanaRotating proxy pool
 

Full AWS Step Functions workflow for enterprise data collection. Scraper containers deployed via AWS Batch, Bronze/Silver/Gold EMR Serverless processing pipeline, RDS backend, application load balancer. Multi-stage medallion with AI/ML training capability bolted on.

 
 
 

 
 
 
 CODEBASE
 📁
 GitHub Actions
 
 
 
 
 EVENTBRIDGE
 ⏱ Schedule
 triggers workflow
 

 
 
 AWS STEP FUNCTIONS WORKFLOW

 
 

 
 
 
 SCRAPER
 🕷️ AWS Batch
 web sources
 
 

 
 
 
 S3
 🪣
 JSON data
 
 

 
 
 
 BRONZE 1
 EMR
 Serverless
 raw → bronze
 
 

 
 
 
 S3
 🪣
 Parquet
 
 

 
 
 
 BRONZE 2
 EMR
 Serverless
 validate
 
 

 
 
 
 SILVER
 EMR
 Serverless
 dedupe + clean
 
 

 
 
 
 GOLD
 EMR
 Serverless
 aggregate
 

 
 
 
 PDF / Images
 Amazon Textract
 
 
 
 
 Translate
 multi-lang
 
 
 
 
 Location Service
 geo-enrich
 
 

 
 
 
 
 RDS
 🗄️
 Gold Parquet
 
 
 
 
 API + ALB
 🔌
 serve data
 

 
 
 
 AI / ML TRAINING, OPTIONAL
 📦 ECR images
 
 ⚡ EC2 GPU
 
 🧠 AI/ML Output
 Capacity Blocks for GPU training on scraped data
 

 
 
 
 SNS ALERTS
 failure notify
 
 

 
 
 ✓ Pros
 State machine with built-in retry, timeout, and parallel branchesEMR Serverless, pay only for actual compute secondsFull medallion pipeline in one declarative workflowNative AWS service integration, no glue code
 
 
 ✗ Cons
 Step Functions cost adds up at high invocation frequencyEMR Serverless cold starts can be slow (1-3 min)Complex state machines are hard to debugTightly coupled to AWS, very high migration cost
 
 
 ↑ Scale up
 Add Kafka (MSK) for real-time ingestion alongside batch. Use Lake Formation for fine-grained data governance. Add Bedrock for AI-powered extraction at the Gold layer.
 
 
 
 Stack
 AWS Step FunctionsAWS Batch (scraper)EMR ServerlessAmazon S3Amazon RDSAWS TextractEC2 GPU (AI/ML)SNS + CloudWatch
 

Microsoft Azure-native scraping pipeline. Container Instances run Scrapy spiders, Logic Apps handle scheduling, Data Factory orchestrates ETL, Azure Data Lake Storage Gen2 for raw data, Synapse Analytics for Bronze/Silver/Gold processing, Power BI for dashboards.

 
 
 

 
 
 AZURE DEVOPS
 📦
 Repos + Pipelines
 → push to ACR

 

 
 
 CONTAINER INSTANCES
 
 🕷️ Scrapy Containers
 ACI serverless instances
 
 ↻ Proxy
 
 App Insights

 
 
 
 LOGIC APPS SCHEDULER
 cron triggers → ACI REST API
 
 

 
 
 write data

 
 
 
 ADLS GEN2
 🗂️
 raw zone / Parquet
 

 
 
 
 
 DATA FACTORY
 ETL pipelines
 trigger on arrival
 

 
 
 
 
 SYNAPSE
 
 🥉 Bronze layer
 
 
 🥈 Silver layer
 
 
 🥇 Gold layer
 Dedicated SQL pool
 

 
 
 
 
 POWER BI
 📊
 dashboards
 

 
 
 
 Azure Monitor + Application Insights + Alerts
 

 
 
 ✓ Pros
 Native Azure integration, Logic Apps, Data Factory, Synapse all connect without glueACI is truly serverless, pay per second, no cluster to runSynapse combines data warehouse + Spark in one serviceAzure DevOps gives end-to-end CI/CD in one platform
 
 
 ✗ Cons
 Azure lock-in, harder to migrate than open-source stackACI has limited networking, VNet integration requires extra configData Factory pipelines are GUI-heavy, version control is painfulSynapse Dedicated SQL Pool is expensive at rest (~$700/mo minimum)
 
 
 ↑ Scale up
 Migrate spiders to AKS (Azure Kubernetes) for better scaling. Replace Dedicated SQL Pool with Synapse Serverless. Add Event Hubs for real-time streaming ingestion.
 
 
 
 Stack
 Azure Container InstancesAzure DevOpsLogic AppsData FactoryADLS Gen2Synapse AnalyticsPower BIAzure Monitor
 

2026 AI-native pattern: scraper feeds directly into a vector store for LLM consumption. Crawl4AI or Firecrawl converts pages to clean Markdown, embeddings generated, stored in vector DB. Claude/GPT queries the index. Self-healing selectors via LLM. No traditional ETL pipeline.

 
 
 

 
 
 
 TRIGGER
 Cron / Event
 or on-demand
 
 

 
 
 AI SCRAPER
 
 Crawl4AI / Firecrawl
 HTML → clean Markdown
 
 curl_cffi
 
 Camoufox

 
 
 
 LLM SELF-HEALING
 detects broken selectors → auto-fix
 
 

 
 
 Markdown

 
 
 
 EMBEDDINGS
 text-embedding-3
 OpenAI / local
 1536-dim vectors
 
 
 vectors

 
 
 
 COCOINDEX
 incremental re-index
 
 

 
 
 
 VECTOR DB
 Pgvector
 
 Pinecone
 
 LanceDB
 

 
 
 ANN search

 
 
 
 LLM LAYER
 Claude / GPT-4
 
 RAG + context
 grounded answers
 

 
 
 
 
 APPLICATION
 API / Chat UI
 natural language queries
 

 
 
 schedule re-crawl when content goes stale

 
 
 ✓ Pros
 No ETL pipeline, scraper output is directly queryable by LLMsSelf-healing selectors reduce maintenance overhead dramaticallyNatural language interface replaces dashboards for many use casesCocoIndex keeps vectors fresh without full re-crawl
 
 
 ✗ Cons
 LLM API cost at scale, embedding + inference per page adds upVector search is approximate, misses exact matches that SQL would catchHallucination risk if context window is poorly managedDebugging RAG failures is harder than SQL query failures
 
 
 ↑ Scale up
 Add hybrid search (BM25 + vector) for better recall. Use local embedding models (BGE, E5) to cut API cost 10x. Add a reranker (Cohere, cross-encoder) before LLM context. Cache frequent queries.
 
 
 
 Stack
 Crawl4AI / Firecrawlcurl_cffitext-embedding-3CocoIndexPgvector / Pinecone / LanceDBClaude / GPT-4FastAPI
 

API-first scraping platform, each spider type is a microservice behind a REST/GraphQL API. Clients request data via API, jobs queue in Redis/SQS, worker pools execute, results stored in Postgres and returned via webhook. Suitable for multi-tenant SaaS scraping platforms.

 
 
 

 
 
 
 CLIENTS
 🌐 Web App
 
 📱 Mobile
 
 🔧 API
 consumers
 
 

 
 
 
 API GATEWAY
 REST / GraphQL
 auth + rate limit
 
 

 
 
 
 JOB QUEUE
 ⚡ Redis
 or SQS / RabbitMQ
 
 

 
 
 WORKER POOL
 
 🕷️ HTTP Workers
 curl_cffi + Scrapy
 
 🌐 Browser Workers
 Camoufox / Playwright
 
 🤖 AI Workers
 Crawl4AI / Firecrawl
 
 
 ↻ Proxy rotation

 
 
 results

 
 
 
 STORAGE
 🗄️ Postgres
 
 🪣 S3 / GCS
 
 raw files
 

 
 
 
 
 WEBHOOK
 Push results
 to client callback
 

 
 
 async result delivery

 
 
 
 Prometheus + Grafana, queue depth, worker health, success rates
 

 
 
 
 ADMIN UI
 Retool / custom
 
 

 
 
 ✓ Pros
 Multi-tenant, different clients can request scraping via API with auth/rate limitsMix HTTP, browser, and AI workers in the same pool depending on targetWebhook delivery means clients get results without pollingPrometheus metrics give real visibility into queue depth and worker health
 
 
 ✗ Cons
 Most complex architecture, 6+ services to run and keep healthyRedis is a SPOF for the job queue, needs replicationWebhook delivery failures need dead-letter queue and retry logicMulti-tenancy adds complexity: per-client rate limiting, result isolation
 
 
 ↑ Scale up
 Containerise with Kubernetes, scale worker types independently. Add Kafka instead of Redis for durable job queue. Use Temporal for complex multi-step workflow orchestration with full state persistence.
 
 
 
 Stack
 FastAPI / GraphQLRedis / SQScurl_cffi workersCamoufox workersCrawl4AI workersPostgres + S3Prometheus + GrafanaRetool (admin)
 

The resilience pattern for crawling 1000+ sources. Instead of one service crawling sources sequentially (where one broken source stalls everything and latency grows linearly with source count), a publisher pushes one message per source to a broker. Independent worker subscribers pull messages and crawl in parallel, fully isolated. One source breaking fails alone, the rest of the pipeline keeps running. Scales horizontally: add subscribers, get throughput. The lesson: if your service does N things sequentially and any one can kill the rest, that is not a scaling problem, it is an architecture problem.

 
 
 
 
 
 
 📡
 Publisher
 1 msg / source
 
 
 
 publish
 
 
 
 Message Broker
 topic: sources
 
 
 
 
 
 
 
 
 
 SQS · Kafka · RabbitMQ · Pub/Sub
 
 
 
 
 
 pull
 
 
 
 Worker 1
 isolated crawl
 
 
 
 Worker 2
 isolated crawl
 
 
 
 Worker 3 ✗
 fails in isolation
 
 
 
 
 
 
 
 🗄️
 Data Store
 + dead-letter Q
 
 
 Latency stays flat as sources grow · failures isolated · scales by adding subscribers

 Stack
 AWS SQS / SNSApache KafkaRabbitMQGoogle Pub/SubCelery (workers)scrapy-redisDead-letter queueHorizontal autoscaling

Browser-as-a-service, decouple the control library from the browser binary (John Watson Rooney's pattern). A persistent patched-Chromium (CloakBrowser) runs headed under Xvfb inside Docker, managed by supervisord, exposing a Playwright WebSocket endpoint. Many scraping scripts connect as clients over ws://, each opening isolated contexts with per-context rotating proxies. One browser service, many projects. Scale by adding browser nodes behind one endpoint.

 
 
 
 
 
 
 Scraper Script 1
 Playwright client
 
 
 
 Scraper Script 2
 scrapy-playwright
 
 
 
 Scraper Script N
 connect(ws://)
 
 
 
 
 
 CDP / ws://:3000
 
 
 
 Docker container (supervisord)
 
 
 
 Xvfb :99
 1920x1080x24
 
 
 
 
 Playwright srv
 ws endpoint
 
 
 
 
 CloakBrowser (headed, both slots)
 source-patched · chrome + chrome-headless-shell
 
 
 
 Pool of isolated contexts (16x):
 
 
 
 
 
 
 
 
 
 
 
 
 per-context proxy
 
 
 
 🔁
 Residential
 
 
 
 
 Target
 
 Control library stays local · patched binary runs remote · scale by adding browser nodes (Docker Swarm)

 Stack
 Playwright server (ws)CloakBrowserXvfbDocker + supervisordIsolated contexts poolscrapy-playwrightResidential proxies (per-context)Docker Swarm (scale-out)

Not a pipeline diagram. A reality map. Drawn around the only question that matters in production: when something breaks at 3am, where do I look and what do I touch? Built from 7 years of running spiders that had to keep working on Monday mornings. The center is not an orchestrator, it is the live data flow. The edges are the five things that actually fail and the boring, specific responses to each one.

 
 
 
 
 
 

 
 
 
 The one principle this whole workflow rests on:
 a clean HTTP 200 means absolutely nothing. silent failure is the default mode, not the exception.
 

 
 
 ───────── what the system does every day, left to right ─────────
 

 
 
 
 URL queue
 Pub/Sub broker
 one msg = one URL
 isolation is non-negotiable
 
 

 
 
 fetch worker
 curl_cffi · or browser
 cheapest rung that worked
 yesterday is the default today
 
 

 
 
 parse
 selectolax + chompjs
 Pydantic validation
 missing field ≠ failed parse
 
 

 
 
 dedup & persist
 content hash · upsert
 Parquet to S3 (bronze)
 last_seen, not insert
 
 

 
 
 downstream
 silver / gold
 dbt · BI · API
 someone else's problem
 

 
 
 
 the keeper
 cookies · sample HTML
 expected JSON · last-good selectors
 git-tracked. not a "blackboard."
 

 
 
 reference

 
 
 
 The five things that actually break in production · what each looks like · what fixes it · who touches it
 

 
 
 
 1. selector drift
 ~70% of all incidents
 
 looks like
 price field fill-rate
 drops from 96% → 8%.
 200 OK · row count fine
 fix
 Claude reads keeper's old
 selector + live HTML, diffs,
 proposes 1 candidate,
 runs fixture test, ships.
 touches: one selector.
 does NOT touch: spider.
 

 
 
 
 2. ban rate spike
 ~15% of incidents
 
 looks like
 403/429 jumps to >10%
 overnight. code did not
 change. site did.
 fix
 rotate proxy pool · check
 5-vector coherence ·
 if all green, climb one
 rung on the ladder.
 infra problem,
 Claude does NOT help.
 

 
 
 
 3. structural change
 ~8% of incidents
 
 looks like
 total item count drops
 by >20%. pagination quietly
 stops at page 10 not 20.
 fix
 human re-explores by hand.
 URL templates changed,
 categories merged, or new
 listing format.
 do NOT auto-patch.
 restudy from scratch.
 

 
 
 
 4. silent poisoning
 rare, devastating
 
 looks like
 all metrics green.
 but values quietly wrong:
 stale prices, shuffled rows
 fix
 diff 50 records against
 hand-curated truth nightly.
 if >10% drift, slow down,
 vary nav path, new session.
 discipline beats
 stealth here.
 

 
 
 
 5. cost creep
 silent budget killer
 
 looks like
 $ per successful row creeps
 up because retries climb,
 browser sessions live longer
 fix
 audit retry distribution.
 drop down one rung if site
 relaxed. raise concurrency
 cautiously, watch ban rate.
 cheapest rung that
 still works · always.
 

 
 
 
 Where Claude actually earns its keep · four narrow jobs, not a swarm

 
 recon
 read Burp captures, trace cookie lineage,
 identify sensor endpoints. 4hr → 2 min.
 
 
 scaffold
 SKILL.md + 3 sample pages → first-draft
 spider. shipped, never re-prompted.
 
 
 repair (failure 1 only)
 one broken field at a time. live page +
 old selector → diff → propose → fixture.
 
 
 audit (failure 4)
 nightly: diff 50 records vs hand-curated
 truth, flag >10% drift. that is it.
 
 

 
 
 What this workflow deliberately rejects:
 → Many specialist agents. One engineer + one spider + Claude at four moments is enough for most production loads.
 → Self-healing-everything. Three of the five failures should NEVER be auto-patched. Restudy beats auto-fix.
 → Orchestrator-as-brain framing. There is no orchestrator. There is a queue, a worker, a parser, and a monitor. They are separate processes.
 → "Living organism" metaphors. It is a Linux service. It has logs. When something breaks, you read the logs and fix one thing.
 7 years running spiders that had to be working when the team came in Monday morning. That is the only test that matters.
 

 Stack
 Pub/Sub queue (URL isolation)curl_cffi → CloakBrowser → managed (ladder)selectolax + chompjs + PydanticContent-hash upsert + Parquet bronzeGit-tracked "keeper" (samples + last-good selectors)Fill-rate · count-delta · ban-rate · cost-per-row · driftClaude (4 narrow jobs only)Residential proxies + 5-vector coherenceNightly ground-truth diffLinux services, not "agents"

 
 ★ Featured architecture
 The AI Workflowgraph + adversarial + five algorithms
 A genuine AI-agent scraping system, designed from scratch. Two agents working through a queryable graph of vendor and selector history, with six mathematical concepts each replacing a heuristic that would otherwise rule the system. Below the conceptual diagram, you will find what this looks like when you actually run it in production: real services, real protocols, real bottlenecks, and the cost numbers it should add up to.
 

 
 
 
 
 ① Graph theory
 PageRank · Louvain · BFS
 Memory as a queryable graph. PageRank ranks vendors by operational criticality. Louvain community detection auto-distinguishes failure modes (structural change vs vendor flip vs isolated drift). BFS bootstraps new URLs from their nearest known-working neighbour.
 replaces · flat-file selector history
 
 
 ② Bayesian inference
 Beta(α, β) confidence
 Every selector and every URL carries a Beta distribution, not a scalar. Beta(95, 5) means 95 successes in 100 tries, very confident. Beta(3, 0) is 3 from 3, but the credible interval is wide. Routing weighs evidence, not just point estimates.
 replaces · single confidence float
 
 
 ③ Thompson sampling
 multi-armed bandit on rungs
 For each URL, the four rungs (curl_cffi / browser / browser+pacing / managed) are arms of a bandit. Thompson sampling balances exploration (try the cheap rung occasionally) and exploitation (use what worked yesterday). Same math as ProxyOps which is already in this guide.
 replaces · fixed rung choice per URL
 
 
 
 
 ④ Information theory
 KL divergence drift
 Compute KL divergence between today's value distribution and a rolling 30-day baseline, per field. A field can have 100% fill rate and still drift, prices all shift up 5%, titles all gain a suffix. KL catches what fill-rate misses. This is how silent poisoning becomes visible before it pollutes silver.
 replaces · fill-rate as drift signal
 
 
 ⑤ Control theory
 PID concurrency loop
 Ban rate is the error signal, request concurrency is the actuator. A PID controller adjusts concurrency continuously to hold ban rate at target (e.g. <0.5%). Proportional reacts to magnitude, integral to persistent drift, derivative dampens overshoot. Used in production at Stripe, Cloudflare. Heuristics here cost real money.
 replaces · fixed concurrency & manual backoff
 
 
 ⑥ Reservoir sampling
 unbiased audit sample
 Maintain a 100-row ground-truth audit sample drawn uniformly from a 50M-row stream, in one pass, in O(1) memory. Vitter's Algorithm R. Without this you end up auditing the easy rows from the start of each batch and missing the hard rows at scale.
 replaces · first-N or last-N sampling
 
 
 Concepts deliberately rejected as theatre for this scale: GNN training (graph too small), max-flow/min-cut (no mapping), causal do-calculus (impractical in production), game-theoretic minimax (no concrete operation), Markov chains for traversal (overkill), learned embeddings (cost > value).
 

 
 
 The architecture · conceptual view
 A genuine AI-agent scraping architecture, built around three ideas you won't find in published frameworks. (1) Graph memory. URLs, selectors, vendors and outcomes are nodes, connected by edges. When one vendor changes its sensor, every URL on that vendor inherits the learning automatically. (2) Adversarial verification instead of self-healing. The scraper agent ships an answer + a confidence score, the verifier agent's job is to disprove it. Only outputs that survive the disproof attempt reach gold storage. The detail that makes this work is keeping the verifier context-blind: run it in a fresh session with no memory of how the answer was produced, so it judges the output on its merits instead of inheriting the generator's rationalisations. A reviewer that sat through the generation will quietly accept the same shortcut that produced the bug; a reviewer handed only the result, the schema, and the live page has to re-derive correctness from scratch, which is exactly the property you want. The same applies when an agent rewrites a broken scraper: benchmark and review the regenerated code from a clean context, not inside the conversation that wrote it. (3) Graph-theoretic intelligence on top of the memory. PageRank ranks vendors by operational criticality, community detection auto-distinguishes structural failures from selector drift, shortest-path traversal bootstraps strategies for new URLs from their nearest known-working neighbours. Most of the system runs cheap, verification runs only on the hard cases.

 
 
 
 
 
 
 

 
 
 
 Three ideas this architecture is built on (and nothing else):
 ① graph-shaped memory ② adversarial verification ③ graph-theoretic intelligence (PageRank · community detection · shortest path)
 

 
 
 
 THE KNOWLEDGE GRAPH (memory, not files)
 Neo4j or DuckDB-graph · one process can read it · agents query it like a database

 
 
 
 VENDOR :Akamai_v3
 last_sensor_hash · expected_cookies
 

 
 
 
 URL :homedepot/p/123
 success_rate=0.94 · rung=2
 
 
 
 URL :homedepot/p/456
 success_rate=0.91 · rung=2
 
 
 
 URL :lowes/p/789
 success_rate=0.98 · rung=2
 
 
 
 URL :ticketmaster/...
 success_rate=0.61 · rung=4
 

 
 
 
 SELECTOR :price.v3
 CSS · confidence 0.92
 
 
 
 SELECTOR :title.v1
 XPath · confidence 0.98
 
 
 
 SELECTOR :stock.v2
 JSON path · confidence 0.74
 

 
 
 
 
 
 USES

 
 
 
 
 
 EXTRACTS

 
 
 
 ★ vendor sensor changes → graph updates → ALL connected URLs inherit new strategy in one write
 
 

 
 
 
 THE TWO-AGENT LOOP (only two, not thirteen)

 
 
 
 SCRAPER agent
 small model · cheap · fast
 
 1. read graph for this URL
 2. pick rung from vendor history
 3. fetch with that rung
 4. extract using known selectors
 5. emit row + per-field confidence
 { price: 42.99 (0.94),
 

 
 
 
 VERIFIER agent
 larger model · adversarial
 
 prompt: "prove this is wrong"
 • re-fetch via different rung
 • check mobile API if any
 • compare to similar URLs
 • disagrees? FAIL the row
 trust is earned by surviving
 

 
 
 claim
 

 
 
 
 CONFIDENCE ROUTING (only verify the hard cases)

 
 
 
 conf ≥ 0.90 · fast lane
 skip verifier
 ship straight to bronze
 ~85% of rows
 
 
 
 0.60 ≤ conf < 0.90 · check
 verifier runs
 survives? → bronze
 ~12% of rows
 
 
 
 conf < 0.60 · quarantine
 do not ship
 human review queue
 ~3% of rows
 
 

 
 
 
 Every outcome rewrites the graph · this is where the system learns
 

 
 
 
 verifier disagrees N times in a row
 
 → selector node confidence drops
 → a new selector candidate is mined
 → graph proposes :price.v4 with edge
 → ALL URLs using vendor inherit candidate
 
 
 
 
 ban rate spike on one URL
 
 → vendor node 'last_sensor_hash' flips
 → ALL URLs using vendor mark rung+1
 → scraper agent re-reads, escalates
 → no per-URL fix · one vendor edit fixes 1000s
 
 
 
 
 field confidence trends down across URLs
 
 → schema-drift flag raised on the field
 → human notified via Slack with samples
 → system never silently auto-changes schema
 → this is the only gate that holds
 

 
 
 
 What actually runs · in production
 graph DB (Neo4j or DuckDB) · 2 Anthropic API workers · curl_cffi + browser pool · Pub/Sub queue · Parquet on S3 bronze → silver → gold
 

 
 
 
 
 Graph Intelligence Layer · three algorithms that actually pull their weight (not theatre)
 

 
 
 
 A · PageRank on vendor nodes
 criticality, not popularity
 
 
 what it does
 rank vendor nodes by how much traffic
 flows through them (eigenvector centrality)
 
 what it tells you
 Akamai_v3 fails = 47% volume down
 F5_Shape fails = 2% volume down
 → pager priority is data, not vibes
 
 cost
 recompute nightly · tiny graph (~50 vendors)
 runs in <100ms with NetworkX
 
 tells ops where to point their attention
 

 
 
 
 B · Louvain community detection
 ★ the gem · failure-mode discriminator
 
 
 what it does
 cluster URLs into communities by shared
 selectors, vendor, template, behavior history
 
 what it tells you
 when one URL fails, look at its community:
 • 100% of community failing = structural
 • ~50% failing = vendor sensor change
 • <10% failing = isolated selector drift
 
 why this is the gem
 distinguishes Reality Map failure modes
 automatically. Humans currently guess.
 

 
 
 
 C · Shortest-path on new URLs
 cold-start bootstrapping
 
 
 what it does
 a NEW URL arrives. BFS through graph to
 find nearest URL with working selectors via
 shared vendor / template / domain edges
 
 what it tells you
 "start at rung 2, use these 4 selectors,
 expect Akamai_v3, conf ~0.8 on title"
 → zero-shot bootstrap, not blind probe
 
 cost / fallback
 if path distance > threshold: cold start
 via scraper agent recon (slower path)
 

 
 
 What this layer deliberately does not include:
 GNN training for selector prediction (graph is too small, cost > value) · max-flow / min-cut (no mapping) · learned graph embeddings (overhyped for ~10k nodes) · centrality measures beyond PageRank (redundant)
 

 
 Design choices that make this different from every other "agentic scraper" you've read about:
 → Memory is a graph, not a folder. When one vendor changes its sensor, the fix touches one node and propagates to thousands of URLs.
 → Two agents, not thirteen. A scraper that emits confidence, a verifier whose only job is to disprove. Direct handoff. No orchestrator in between.
 → Adversarial verification, not self-healing. Trust is earned by surviving disproof. The default assumption is "you are wrong."
 → Confidence routing, not retry loops. 85% of rows ship without ever invoking the second agent. The expensive path runs only on hard cases.
 → Three graph algorithms, no more. PageRank, Louvain, BFS. Skipped GNN training, max-flow, learned embeddings as theatre for a 10k-node graph.
 → Schema changes need a human. Selectors, rungs, candidates all evolve automatically. The shape of the data does not change without a person.
 → Learning is vendor-shaped, not URL-shaped. One Akamai_v3 sensor change is one edit. One URL going down is one edit. The graph reflects how anti-bot vendors actually deploy.
 Six mathematical primitives, two agents, one graph. Every routing decision in the system is named, costed, and traceable to a printed equation, not a heuristic that drifts as the team forgets why it was chosen.
 

 Stack
 Neo4j or DuckDB-graph (knowledge graph memory)NetworkX (PageRank · Louvain · BFS)Small LLM as Scraper agentLarger LLM as Verifier agentAny frontier LLM API · prompt: "prove this is wrong"Confidence routing (≥0.9 / 0.6-0.9 / <0.6)Pub/Sub queue (URL fan-out)curl_cffi + CloakBrowser (rung-aware fetch)Vendor-level learning propagationSchema-drift Slack alerts (human gate)Parquet bronze → silver → gold

 

 
 
 
 What this looks like in production
 Real services, real protocols, real bottlenecks. Drawn for 10M-50M URLs/day. Each rung in the Scraper agent is annotated with the actual scraping technique that does the work, not just the box that runs it. Color-coded dots mark each flow direction so you can trace any path through the system.
 

 
 
 ★ stack philosophy
 
 Two agents, no framework, model-agnostic. The two agent roles are scraper (small fast model, runs on every row) and verifier (larger careful model, runs on ~12% of rows). Either role can be served by any frontier LLM with structured output, an Anthropic model, an OpenAI model, a Gemini model, an open-weights model behind vLLM. No LangChain, LangGraph, AutoGen, CrewAI, Haystack, LlamaIndex. The architecture has six math operations, none of which need a framework's abstraction. Direct API call for the model, pydantic for structured output, networkx for the three graph algorithms, scipy.stats.beta for Bayesian state, small custom services for Thompson sampling, KL drift, and the PID loop, boto3 for SQS/Kinesis/S3. Frameworks shine when you have 30 chains and 10 tool integrations to coordinate. We have 2 agents and a queue.
 
 

 
 
 legend
 scrape traffic · ~85%
 verifier-flagged · ~12%
 verifier verdict · written back
 learning feedback · updates control plane
 

 

 
 
 
 

 
 
 
 

 
 
 
 
 

 
 
 
 
 

 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 

 
 
 

 
 LAYER 1
 
 
 CONTROL PLANE
 where the math lives · low traffic · stateful · single-region

 
 
 
 Neo4j
 knowledge graph
 ~10k vendors · ~5M URLs
 

 
 
 
 PostgreSQL
 Beta(α,β) state
 per-selector confidence
 

 
 
 
 Thompson sampler
 FastAPI · ECS
 per-URL rung selection
 

 
 
 
 PID controller
 Go service · 1s loop
 tunes worker concurrency
 

 
 
 
 KL drift
 Spark · EMR · hourly
 vs 30-day baseline
 
 
 

 
 
 
 NetworkX jobs
 nightly CronJob
 PageRank · Louvain · BFS
 
 

 
 LAYER 2
 
 QUEUE LAYER

 
 
 
 SQS · scrape-jobs
 ~5M msg/day
 

 
 
 
 SQS · verifier-jobs
 ~600K msg/day
 

 
 
 
 SQS · quarantine
 ~150K msg/day
 

 
 
 
 Kinesis · outcomes
 ~50M events/day
 
 

 
 LAYER 3
 
 

 
 SCRAPER agent · EKS
 50–400 pods · auto-scaled by PID controller

 
 
 
 
 Scraper role
 small fast model · any vendor
 reads graph → picks rung →
 extracts → emits Beta(α,β)
 runs on 100% of rows · cheap
 

 
 
 
 
 Rung 0 · API hunt (always tried first · free)
 technique: HTTPToolkit on rooted emulator · inspect mobile app endpoint
 technique: look for __NEXT_DATA__ · GraphQL · XHR in page HTML
 → if JSON endpoint found · skip rungs 1-4 entirely, hit the API
 

 
 ↓ if API hunt fails · escalate through the rungs (Thompson-sampled per URL)

 
 
 
 Rung 1 · curl_cffi · ~85% of volume · ~$0.30 / 1K
 technique: TLS impersonation (Chrome 124 JA4) + HTTP/2 + residential proxy
 when: Akamai easy mode · Cloudflare static · most e-commerce
 no JS execution · ~10ms per request · fastest possible path
 

 
 
 Rung 2 · browser pool · ~10% · ~$3 / 1K
 technique: CloakBrowser / Camoufox over ws:// · Xvfb · two-slot fix
 when: JS-rendered content · simple bot detection · cookies needed
 patched Chromium · persistent profiles · ~3s per page
 

 
 
 Rung 3 · browser + pacing · ~3% · ~$8 / 1K
 technique: Rung 2 + Bezier mouse curves + realistic nav · 8-30s pacing
 when: PerimeterX · DataDome · Kasada · behavioral ML defenses
 walks the site like a human · slow but reliable on hard targets
 

 
 
 Rung 4 · managed API · ~2% · ~$15 / 1K
 technique: Zyte API / Bright Data Web Unlocker / Scrapfly
 when: F5 Shape · Akamai v3 deep behavioral · enterprise sites
 last resort · they own the bypass burden · we pay the fee
 
 

 
 
 
 
 VERIFIER
 ECS · queue-scaled

 
 
 
 Verifier role
 larger model
 prompt:
 "prove this is wrong"
 technique:
 re-fetch at different rung,
 BFS-compare to siblings
 
 

 
 
 
 
 STORAGE
 S3 medallion

 
 
 🥉 Bronze
 Parquet
 raw + provenance
 90-day retention
 

 
 
 🥈 Silver
 dbt models
 content-hash dedup
 upsert + last_seen
 

 
 
 🥇 Gold
 Iceberg tables
 BI · APIs
 downstream consumers
 
 

 
 
 
 PROXY LAYER
 Bright Data ISP
 Massive residential
 Infatica (KYC)
 ★ 5-vector coherence
 

 
 
 EXTRACTION OUTPUT
 { row,
 Beta(α,β) per field,
 rung_used, latency }
 → Kinesis stream
 

 
 LAYER 4
 
 
 LIVE METRICS · sample snapshot · what the dashboards actually show

 
 
 BAN RATE
 
 0.31% · target 0.5%
 
 
 

 
 FILL RATE (price field)
 
 96.4% · stable 30d
 
 
 

 
 VERIFIER DISAGREE
 
 2.1% of ~12% sampled
 
 
 

 
 KL DRIFT (top field)
 
 0.08 · threshold 0.20
 
 

 
 CONCURRENCY (PID)
 
 184 pods · range 50-400
 
 
 

 
 $ / 1K rows
 
 0.71 USD · trailing 24h
 
 
 

 
 LAYER 5
 
 
 OBSERVABILITY
 what stands between you and a 3am page

 
 
 Prometheus + Grafana
 
 
 
 OpenTelemetry traces
 
 
 
 KL drift dashboard
 
 
 
 Graph health view
 
 
 
 Reservoir audit
 
 
 
 PagerDuty
 
 

 
 LAYER 6
 
 
 HUMAN GATES · only here · nowhere else
 ① schema changes (KL drift > threshold) → Slack with samples · ② new vendor detected · ③ quarantine review · ④ critical-vendor degradation
 selectors, rungs, candidates evolve automatically · the shape of the data does not change without a person
 

 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 

 
 
 
 
 
 
 
 
 
 

 

 
 
 What this production design handles automatically · no on-call required
 
 
 vendor sensor flips
 PID + bandits re-route within minutes. ALL URLs on that vendor inherit the new escalation through the graph.
 
 
 selector drift
 Verifier disagreement N times raises a new selector candidate. Graph propagates to related URLs. Seamless.
 
 
 cost creep
 PID tightens concurrency as bans rise. Bandit shifts traffic to cheaper rungs. Spend self-corrects.
 
 
 silent value poisoning
 KL divergence catches distribution shifts the row count and 200-OK rate cannot see. The hardest failure mode, now visible.
 
 
 
 What it routes to a human · by design: schema changes (new field · removed field · type mismatch) · the shape of data is governed, not learned.
 
 
 

 
 ✦ Pattern
 Self-Healing Scraperpowered by Claude
 Scrapy spiders break when sites change their HTML. Instead of manually fixing selectors, this architecture uses Claude to detect failures, analyse the new page structure, and write corrected selectors automatically, without human intervention.
 

 
 
 
 
 
 
 
 
 
 
 
 
 

 
 
 
 SCRAPY SPIDER
 🕷️
 runs on schedule
 
 

 
 
 
 TARGET SITE
 🌐
 HTML changed
 

 
 
 success

 
 
 
 STORAGE
 📦 S3 / DB
 clean items
 

 
 
 failure / empty

 
 
 
 DETECTOR
 Item count check
 0 items = broken
 
 

 
 
 
 CLAUDE API
 
 
 1. Fetch broken page HTML
 
 2. Analyse new structure
 
 3. Write new selectors
 

 

 
 
 
 UPDATER
 Patch spider config
 CSS / XPath / regex
 

 
 
 
 
 RETRY
 Re-run spider
 with new selectors
 

 
 
 auto-healed, spider runs again without human intervention

 
 
 
 NOTIFY
 Slack / email
 
 
 "healed" alert
 
 

 
 
 1
 
 Spider detects failure
 Item count drops to zero or below threshold. A Scrapy extension hook fires immediately, no waiting for the next run.
 
 
 
 2
 
 Claude analyses the broken page
 The full page HTML is sent to Claude with the old selectors and a prompt: "The selectors below stopped working. Examine the HTML and write corrected CSS selectors for the same data fields."
 
 
 
 3
 
 New selectors written and applied
 Claude returns structured JSON with corrected selectors. The updater patches the spider config or YAML file. No code deployment needed.
 
 
 
 4
 
 Spider retries and confirms
 The spider re-runs with new selectors. If items come back, healed. A Slack notification logs what changed. If it fails again, escalates to human review.
 
 
 

 
 Claude prompt pattern
 You are a web scraping expert. A Scrapy spider broke because the site changed its HTML.

Old selectors (no longer working):
 title: h1.product-title::text
 price: span.price-now::text
 image: img.main-image::attr(src)

New page HTML (truncated):
{{ page_html[:8000] }}

Return ONLY valid JSON with corrected selectors:
{"title": "...", "price": "...", "image": "..."}
 

 
 
 
 ✓ Why it works
 
 Claude reads raw HTML better than regex, handles minified, dynamic, and obfuscated markup
 Zero downtime, spider heals mid-run, not on next deployment
 Works across 50+ spiders from a single Claude integration
 Selector changes are the most common spider failure, this covers 80% of breakages
 
 
 
 ⚙ Implementation notes
 
 Use claude-haiku-3 for speed and cost, ~$0.0003 per heal
 Cap page HTML at 8K chars before sending, beyond that Claude doesn't need more
 Store selector history in a YAML file versioned in Git for auditability
 Add a confidence check, if healed items look wrong, escalate to human
 
 
 
 ↑ Extend it
 Add a second Claude call to validate the healed output against a schema. Use computer-use to handle JavaScript-rendered pages where HTML alone isn't enough. Log all heals to build a fine-tuning dataset.
 
 
 
 Stack
 Scrapy extension hook
 Claude API (Haiku)
 YAML selector store
 Slack webhook
 Anthropic SDK
 
 

The lab-to-production gap: running thousands of pages unattended for days#

There is a wide gap between running one to three spiders on your laptop and running thousands of pages in production, unattended, for days. Most tutorials stop where that gap begins. The crawl that works perfectly in a terminal session fails in ways you never see interactively: a connection resets at hour nine, a target quietly tightens its rate limits, a selector that matched yesterday returns nothing today, and nobody finds out until the morning. Closing that gap is less about clever bypasses and more about a few unglamorous habits.

 
 
 Throttling and retries
 Speed is a tradeoff, not a setting
 Tune throttling for the real tradeoff between crawl speed and getting banned rather than hammering at a fixed rate. Scrapy's AutoThrottle adapts the delay to the server's observed latency, which both behaves better and survives longer than a hardcoded concurrency number.Handle failures at the right layer. A try/except around your parse code never sees a network-level failure, because the request fails before your callback runs. You need an errback to catch DNS errors, timeouts, and connection resets, and a helper like get_retry_request to requeue with proper accounting instead of silently dropping the URL.
 
 
 
 Structure and monitoring
 Catch a bad run in minutes, not the next morning
 Give the project a structure that scales past a handful of spiders without becoming unmanageable, so shared logic lives in one place rather than copy-pasted across files.Then monitor outcomes, not just uptime. A tool like Spidermon validates each run against expectations (item counts, field coverage, error rates) and alerts when a run drifts, so a half-broken crawl is caught in minutes instead of discovered the next day. For deployment, the ladder runs from Docker to Scrapyd to a managed cloud, and scrapy-redis earns its place only once you genuinely need a distributed, shared request queue across workers, not before.
 

A concrete case worth internalising. A public Reddit scraper ran at a 92% success rate, then over thirty days, with zero code changes, fell to 61%. The logs showed the same thing on every failure: HTTP 403 on every retry, every proxy, every subreddit. The retry logic was fine, proxy rotation was fine, the user-agent set was varied. The cause was upstream of all of it: the shared residential proxy pool had been fingerprinted and burned against www.reddit.com, and you cannot out-rotate a poisoned pool. The fix was a single hostname change to old.reddit.com, the same JSON API with the same response shape, but served by a subdomain whose bot-detection thresholds are far looser because most human traffic left it years ago. Success snapped back to 92% with zero retries.

Three lessons compound out of that one incident. First, the architectural fix (change the endpoint) beats the tactical fix (add more retries) almost every time a whole proxy pool is burned. Second, before fighting a target, check whether it exposes an old. or m. subdomain whose WAF rules differ from the main host. Third, and most important for unattended systems, the platform never sends you a deprecation notice: your failure-rate chart is the notice. The same scraper later survived Reddit removing its public JSON endpoints entirely by falling back to RSS feeds behind a circuit breaker, with the degraded fields tagged honestly in the output rather than silently passed off as complete. Monitoring is what turns a silent collapse into a five-minute alert and a planned fallback.

A quiet throughput fact when a real browser is your fetch layer: connections are pooled and capped per origin. A browser does not open a fresh TCP and TLS connection for every request. It keeps idle connections alive in a per-origin pool and reuses them, and Chromium caps concurrency at six simultaneous connections to a single origin (a limit inherited from the HTTP/1.1 era and still enforced there; HTTP/2 multiplexes many streams over one connection instead). For a scraper this has two practical consequences worth designing around rather than discovering by surprise. First, it bounds your real parallelism against one host: firing fifty page loads at a single origin through one browser context does not give you fifty concurrent fetches, it gives you six at a time with the rest queued, so genuine scale comes from spreading work across origins, contexts, or workers, not from asking one context to do more. Second, reused connections carry state, cookies, cached negotiation, and warmed TLS, which is part of what makes a coherent session look human (the session-stickiness section leans on exactly this), but it also means a connection you keep open is a connection whose earlier context travels with it, so a clean per-session boundary means letting the pool cycle rather than forcing unrelated work down a socket that is still mid-conversation. The practical rule is simply to respect the browser's own connection model instead of fighting it: parallelise across origins, keep one logical session on its own pool, and let keep-alive do the work it is good at.

Adaptive throttling, and the one trap that makes a naive version speed up into a ban. A fixed crawl delay is always a guess: too aggressive and you are blocked, too cautious and an hour-long job runs all night, and either way the right number changes the moment the site's load shifts or it decides it dislikes you. The better pattern (Scrapy's AutoThrottle, and Scrapling's v0.4.12 take on it) watches how fast each domain actually answers and tunes the delay per domain, giving a fast server more speed and a struggling one more room, with your own download_delay and any robots.txt Crawl-delay kept as a hard floor so politeness is never undercut. The subtle part is the failure response: when a site starts blocking or rate-limiting, the crawler should back off (double the delay, or wait exactly what a Retry-After header asks) until that clears, then recover. Here is the trap that catches naive latency-based throttling: blocked and challenge pages usually come back faster than real ones, because a captcha wall or a 429 stub is cheaper to serve than a rendered page. A throttle that only watches response speed therefore reads a wall of blocks as the server having spare capacity and speeds up, which is precisely the wrong move and turns a soft rate-limit into a hard ban. The fix is to make the throttle react to block signals (status codes, challenge markers, Retry-After) and not to latency alone, so that getting slower under pressure, not faster, is the built-in reflex.

Keep the browser out of the crawler: the sidecar pattern. When only some targets need a real browser (a Cloudflare interstitial, a JS-challenge page), the tempting move is to embed Selenium or Playwright directly in the spider, which entangles browser lifecycle, concurrency, and networking with your scraping logic in one process. A cleaner architecture keeps the browser complexity outside the crawler as a separate service the crawler calls over a simple API. A browser-challenge solver running as its own service (FlareSolverr is the common one) sits behind an HTTP endpoint, handles the challenge, and returns the rendered response, and a thin downloader-middleware layer routes only the requests that need it through that service (ideally round-robin across several backends for throughput) while every other request takes the normal fast path. The payoff is separation of concerns: the spider stays a spider, the browser fleet scales and fails independently, and you pay the browser cost only on the fraction of URLs that actually require it, which is the same build-once, spend-the-expensive-thing-sparingly logic the cost section argues for.

Scraping behind a login, and the second factor problem. A lot of genuinely valuable data sits behind an account, which changes the architecture in two ways. First, the session is the asset: you log in once in a real browser, then export the authenticated cookies and replay them from a cheap HTTP client for as long as they last, which is the mint-once pattern again with credentials instead of a signature. Keep one identity per account, because the coherence rules apply doubly here, an account that logs in from Lahore and then queries from Ohio ten seconds later is a much louder signal than an anonymous IP change. Second, the second factor is the part people underestimate. Where the account uses a time-based one-time code, the shared secret can be stored with the credentials and the code generated programmatically, so TOTP automates cleanly. Where it uses SMS or a push prompt, it does not, and the honest design is a human handoff: the automation pauses, pings a person on whatever channel they actually read, waits for the code, and continues. Building that pause in deliberately is far better than discovering at 3am that your pipeline has been silently failing on a login prompt for a week. And be clear-eyed about the terms, an authenticated session is one you agreed to conditions to obtain, so the legal and ethical footing here is different from public-page scraping and generally much narrower.

The far end of that spectrum: hand the whole task to a browser agent. The most aggressive version of "let a real browser deal with it" is an AI agent driving the browser directly (browser-use is the widely adopted open example, model-agnostic across the major LLMs). Instead of parsing HTML, it uses the site the way a person would, clicking, scrolling, typing, logging in, and reading a screenshot with vision when the DOM is too messy to parse, which dissolves the modal-popup, login-flow, and JS-rendering problems that break a traditional scraper the moment a site behaves like an app rather than a document. The strategic point worth taking from it, beyond the tool itself, is that an agent which can operate a UI removes the hard dependency on an API or a stable selector: a great deal of integration work historically existed only because sites did not want to be scraped and SaaS tools did not expose APIs, and "point an agent at the UI" is now a real option for the multi-step, interaction-heavy tasks where request-based scraping was never going to reach. The trade is the one this guide keeps returning to: it is powerful and general but slow and token-expensive per run, so it belongs on the hard, low-volume, interaction-bound tail, not on the bulk fetching where a request-based scraper built once is orders of magnitude cheaper.

The risk that comes with pointing an agent at the open web: prompt injection. Everything else in this guide treats the page as data. The moment an LLM agent reads a page and acts on it, the page becomes instructions, and any text on it, visible or hidden in alt attributes, comments, or off-screen elements, is a candidate instruction to your model. A hostile page can tell your agent to ignore its task, exfiltrate whatever credentials or cookies the session holds, or navigate somewhere else entirely, and the agent has no reliable way to distinguish the site owner\'s content from your operator\'s intent because both arrive as tokens in the same context. This is not hypothetical and it is not solved: practitioners building agentic browsers report it as one of the two problems they could not close cleanly (CAPTCHA being the other), and anyone claiming a complete fix is selling something. What you can do is reduce blast radius rather than promise immunity. Give the agent the narrowest credentials that let it finish, not your logged-in everything. Keep secrets outside the context the page can influence. Put a deterministic allowlist on which domains and actions are permitted, so an instruction to visit somewhere new simply cannot execute. Require confirmation before anything irreversible, such as a purchase, a message, or a deletion. And treat page-derived text as untrusted input everywhere downstream, the same way you would treat a form field from a stranger. The honest framing to carry: an agent that browses for you has the same authority you gave it, and the page gets a vote.

The framework already has a place for everything, use it instead of the callback. A working spider is not yet a working project. The satisfying part of a framework like Scrapy (write a callback, pull a few fields, yield a dict) tempts you to keep piling concurrency, normalisation, retries, and headers into that one callback, and it works right up until the crawl has to run again next week on a larger catalog with a missing price and a flaky endpoint. The discipline that survives is to put each concern where the framework already keeps it: crawl behaviour (rate, concurrency, timeouts, robots obedience) lives in settings as a reviewable project contract, not scattered per-callback; a rule that applies to every item (validating required fields, normalising a price, dropping unusable rows, deduping) lives in a pipeline, ordered so one stage can depend on an earlier one; and cross-cutting request behaviour (project-wide headers, auth, response diagnostics) lives in downloader middleware, which is exactly where the framework's own retry, redirect, cookie, cache, and robots logic already live. Page-specific extraction stays in the spider; anything shared across spiders moves out of it. Two habits make this pay off: log the business condition the default stats do not (a product with no visible price), not every success, and when a page is puzzling reach for the interactive shell before editing code. That last one carries a sharp diagnostic: if the shell does not contain data you can see in a browser, that is evidence about the page's delivery path, not an instruction to add a browser. The data is arriving by some route you have not captured yet (an XHR, a JSON island, a different endpoint), and finding it is usually cheaper than rendering. Keeping the first project boring, settings for behaviour, a pipeline for shared rules, real logs, and configured feed exports, is what turns a script that scraped a site once into a project you can trust to run again.

Structure the scraper so an agent can fix one selector, not rewrite the project#

The self-healing pattern earlier in this guide focused on regenerating a crawler and proving the regeneration is correct. There is a quieter architectural choice that decides how cheap and how safe that healing can be: how the spider is laid out in the first place. A scraper written as one long callback, where fetching, pagination, and field extraction are tangled together, forces any repair (by a human or an agent) to reason about the whole thing at once. Break the same logic into Page Objects, one small class per page type whose only job is to turn a response into fields, and a broken site becomes a broken method, not a broken project.

 
 
 Why agents love this shape
 A small blast radius is a fixable blast radius
 When a redesign moves the price selector, the failure is localised to one Page Object's price field. An agent (or a person) can be handed just that class, its test, and the new HTML, and asked to repair that one extractor, with no risk of collaterally rewriting pagination or the request logic that still works. The Scrapy ecosystem packages this as web-poet and scrapy-poet: Page Objects plus dependency injection, so each page type is an isolated, swappable unit. The smaller the unit a fix touches, the easier it is to trust the fix, which is the same principle the promotion-gauntlet card argues from the verification side.
 
 
 
 Tests are the trigger and the guardrail
 Per-field tests turn healing into a closed loop
 Pair each Page Object with tests that assert the shape of what it should return, and monitor field coverage in production (what fraction of expected fields actually came back populated). A coverage drop on one field is both the alarm that triggers healing and the acceptance test the fix has to pass before it ships. That closes the loop: detect the specific broken field, repair the one extractor that owns it, prove it green against the test, deploy. Without the structure, healing means regenerating everything and hoping; with it, healing is a targeted edit a monitor can request and a test can sign off.
 

The throughline with the rest of this section: agentic maintenance does not replace good engineering, it rewards it. The more modular and tested the scraper, the smaller and more trustworthy each automated repair, and the less often a broken selector turns into a rewritten project or a silent gap in your data.

From prompting to loops: the self-correcting maintenance cycle#

There is a mental-model shift behind all of the self-healing material in this guide that is worth stating plainly, because it changes how you build rather than just which tool you reach for. The first instinct with an LLM is to prompt it: describe the broken scraper, paste the HTML, ask for a fix, copy the answer back. That is a one-shot transaction with a human in the middle of every cycle. The more durable pattern is to close the loop: wire generation, execution, benchmarking, and review into a cycle that runs on its own and only surfaces to a human when it cannot resolve something. "Write a loop, not a prompt" is the compressed version, and it is the difference between an assistant you operate and a system that maintains itself.

 
 
 What the loop actually contains
 Generate, run, benchmark, review, repeat
 A working self-healing loop is more than a model that rewrites code. It generates a candidate fix, runs it against the live target, benchmarks the result against what a known-good run produced (record counts, field coverage, value sanity), and only then lets a separate reviewer pass judgement, with the failed cases feeding back in as the next iteration's input. The model that writes is not the authority on whether the write is correct; the benchmark and the reviewer are. This is the same promotion-gauntlet idea from the AI Workflow, expressed as a continuously running cycle rather than a one-time check, and it pairs with the context-blind reviewer point from that section: the agent that scores the regenerated code should not be the agent that wrote it.
 
 
 
 Spidermon as the loop's trigger and gate
 Monitoring is what makes the loop autonomous
 A loop needs a signal to fire on and a bar to clear, and in the Scrapy world Spidermon supplies both. As monitoring it watches each run for the drift that should wake the loop up (item counts collapsing, a field's coverage dropping, error rates climbing); as a validation suite it is the acceptance test a regenerated scraper has to pass before its output is trusted. That is what turns "an agent can fix scrapers" into a system that notices it needs fixing, attempts the fix, proves the fix, and ships it without a human kicking off each step. The boring monitoring layer is not a footnote to the clever self-healing, it is the part that makes the self-healing autonomous at all.
 

Hardening the loop: what a production self-healer needs that a demo does not#

The architecture above describes the happy path: detect drift, regenerate, verify, ship. A version you can leave running unattended needs four more things, each of which maps to a lesson elsewhere in this guide. The gap between a self-healer that demos well and one that survives a quarter in production is almost entirely in how it behaves when it cannot heal, when the fetch itself is the problem, and when the loop could run forever.

 
 
 1 · Circuit breakers
 The loop must be able to give up
 An autonomous regeneration loop without a stop condition is a way to spend an unbounded amount of money on a target that has simply decided to block you. Every healing attempt needs a bounded budget: a maximum number of regeneration rounds per URL, a cost ceiling per heal (model calls plus fetches), and a wall-clock deadline. When a breaker trips, the loop does not keep trying, it freezes the last known-good extractor, marks the URL degraded in the graph, and escalates to the human gate with the diff it could not resolve. A self-healer that cannot give up is not autonomous, it is just expensive.
 
 
 
 2 · Distinguish a broken selector from a blocked fetch
 Do not regenerate code to fix a 403
 The single most common way a self-healer wastes money is rewriting a perfectly good parser because the page it parsed was a block page, a challenge interstitial, or a soft 404. Before the loop is ever allowed to regenerate extraction code, it must classify why the run failed: empty fields on a 200 that is really a CAPTCHA wall is a fetch problem (escalate the rung via the Thompson bandit), not a selector problem. Wiring the status-code and soft-404 signals from the post-extraction section into the loop's entry condition stops it from healing the wrong layer. The community-detection step already separates structural change from vendor flip from drift; this adds the fourth case, not-actually-broken, which is the one that burns the most compute when missed.
 

 
 
 3 · Verify the fetch, not just the output
 A clean payload over a leaky session still fails tomorrow
 The adversarial verifier checks whether the extracted data is correct. It should also check whether the way the data was fetched is sustainable, because a row that arrived today through a session that looks automated is a row that will arrive as a block next week. Two cheap signals feed back into the control plane: rung-level coherence (the TLS profile, HTTP/2 settings, and User-Agent must name the same current browser, a stale or mismatched fingerprint is treated as a soft failure even when it returned data) and request-cadence sanity (a session whose timing and cache behaviour do not resemble a real browser is flagged, the same server-side timing signal a detector keys on). Feeding fetch-health back means the bandit demotes a rung that is technically returning 200s but accumulating risk, before that risk becomes a ban-rate spike the PID loop has to chase.
 
 
 
 4 · Assume the target is adapting to you
 Traps, poisoning, and the anti-agentic page
 A mature defender does not just change its layout, it engineers pages that behave differently under automation: honeypot fields, content that mutates when it detects instrumentation, and values seeded to poison a scraper that does not validate. The loop needs two defences. The KL-divergence drift signal already catches silent value poisoning before it reaches gold. Against traps that make the loop chase its own tail (a page that looks broken only to an agent, so every regeneration fails for reasons the model cannot see), the circuit breaker from point one is the backstop: if N context-blind regenerations all fail the benchmark in different ways, the correct move is not an N+1th attempt but escalation to a human who can recognise a trap the loop structurally cannot. Agentic maintenance does not end the arms race, it moves it up a level.
 

There is also a branch the conceptual diagram leaves out: not every target is HTML. When a URL resolves to a PDF, a scanned document, or an image, the same control plane applies but the extraction node changes shape. The rung is not curl-versus-browser, it is the document-parsing hybrid from the post-extraction section, a deterministic OCR-plus-rules pass and an LLM or vision pass run together, with their agreement as the confidence score the verifier consumes. The graph memory, the Beta-distribution confidence, the KL drift check, and the human gate all work unchanged on that branch; only the thing inside the fetch box differs.

The throughline across all four: a production self-healer spends most of its engineering not on the clever regeneration but on knowing when not to trust itself, when the failure is the fetch and not the parser, when an extraction is poisoned rather than correct, when a fingerprint is decaying, and when to stop and call a human. The math operations give it the signals; the circuit breakers and the fetch-health feedback are what keep those signals from being used to confidently automate the wrong thing at scale.

 7 field notes on architecture
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Threat model
 A Dataset Config Became a Code-Execution Path, and Every Pipeline Has That Shape
 In July a malicious dataset configuration on a major model hub triggered local-file disclosure and server-side code execution. Not a payload in a binary, not a supply-chain package — a config file, doing what config files are allowed to do.
 💡 If you run untrusted files through parsers, browsers, sandboxes or a tool-using agent, you have this exposure
 Why it generalises to collection work specifically. A scraping pipeline is, structurally, a machine for pulling hostile input from strangers and feeding it to parsers. HTML into a DOM parser, JSON into a deserialiser, a PDF or spreadsheet into a library that was never audited for adversarial input, and increasingly all of it into an agent holding real tools. The guide has covered prompt injection as a content problem; this is the same trust boundary one layer down, where the file format itself is the vector and no model is involved at all.The four checks named, all boring and all skipped in practice: workload isolation, file-access limits, input validation, and agent permissions. The last is the one that changed recently — a parser that can read arbitrary local files is bad, but a parser sitting next to an agent that can also make network calls and write files is a different category of bad, because disclosure becomes exfiltration without a second exploit.The tooling response worth knowing about is a category rather than a product: open-source runtime guardrails that monitor agent actions, record session artifacts, and enforce policy on risky operations. The design principle transfers cleanly even if you never adopt one — separate agent logic from security policy, log what the agent actually did, and make sensitive operations reviewable after the fact. Prompt engineering is not a control surface once an agent holds credentials, because the thing you are defending against is not in the prompt.
 
 +
 Pattern / Multi-agent architecture
 Scraping as a Bee Colony: Multi-Agent Orchestration via Claude Skills
 Zyte published a sharp piece reframing production scraping as multi-agent orchestration, not single-shot spiders. The metaphor: honeybees solving distributed coordination for 100 million years. The architecture: many small specialist agents (each one a Claude Skill) writing to a shared append-only blackboard, coordinated by a weak orchestrator that routes attention, not commands.
 💡 One Claude Skill = one bee with one specialist role
 The architecture organises around three connected loops, not a linear pipeline. (1) Discovery loop: intake skill turns a fuzzy "scrape this site" request into a structured spec (fields, page types, refresh, cost, fallback); scout skills (detail-page-finder, page-downloader, site-explorer, link-classifier) reduce uncertainty in parallel before any code is written. (2) Build loop: selector-analyzer proposes candidates with confidence scores; code-synthesizer generates extraction; test-runner is the heartbeat; repair-agent fixes the broken field surgically without rewriting the project. Selector confidence becomes first-class metadata (price=medium, availability=fragile-with-fallback) so monitoring knows where to look. (3) Ops loop: drift-detector watches item count, field fill-rate, ban rate, response codes, schema mismatches; the right repair gets routed to the right loop, not a panic rewrite. The non-obvious piece is the blackboard: a structured project directory (.scrape/site/) with append-only authorship, timestamps, source, and reason on every observation. Three months later when the price field goes null, you read backwards through the trail to see which selector was used, which samples supported it, when it last passed, what changed on the site. The orchestrator is deliberately weak: it senses state, routes work, protects approval gates, and asks for human input on the schema/material-tradeoff decisions only, not every CSS path. Evidence-weighted consensus beats popularity-weighted ("another agent agreed" is the weakest form of review). Full Zyte writeup by Neha Setia Nagpal
 
 +
 Build pattern / Self-hosted infra
 Browser-as-a-Service: Separate the Control Library from the Binary
 John Watson Rooney built a self-hosted stealth-Chrome scraping service and documented every gotcha. The key mental model: Playwright, Puppeteer, and Selenium are control libraries, not browsers. They speak CDP to whatever binary you point them at. Separate the two: run a persistent patched browser on a dedicated machine, connect to it over WebSocket from many scripts.
 💡 playwright.connect("ws://...") = one browser service, many clients
 The architecture: instead of playwright.launch() spinning up a local browser per script, run a persistent Playwright server on a dedicated box exposing a WebSocket endpoint, and have every scraping script connect("ws://host:3000") as a client. The page can't tell the difference, the API is identical. Five hard-won lessons from the build: (1) Binary choice matters as much as library choice. JS overrides of navigator.webdriver are themselves detectable (wrong property descriptor, wrong prototype chain); source-patched binaries like CloakBrowser remove the signal instead of masking it. (2) Headed via Xvfb beats headless, the virtual framebuffer means nothing looks headless because it isn't. (3) The two-slot trap: Playwright keeps TWO Chromium directories, a full build and a stripped chrome-headless-shell. Replace only the full slot with your patched binary and Playwright silently launches the untouched headless shell instead, you get 403s and the wrong version string with nothing in the logs explaining why. You must replace both slots and rename the headless one to chrome-headless-shell. (4) supervisord inside Docker manages the multi-process reality (Xvfb priority 10, Playwright server priority 20 with startsecs delay). (5) Concurrency = contexts, not instances. One browser, a pool of isolated contexts (separate cookies/storage), workers pull from an async queue, a 403 requeues with backoff and the worker grabs the next job. Proxy creds go per-context so a bad IP just retries with a fresh one. 16 concurrent contexts ran fine on a Ryzen 4650G mini-desktop. Full writeup · github.com/jhnwr/browser-service · YouTube
 
 +
 Architecture / Resilience
 Pub/Sub: Why Sequential Crawlers Don't Scale
 A threat-intel crawler hitting 1000+ sources started as one service crawling sequentially. Two failures emerged: latency grew linearly with each new source, and one broken source took down the entire pipeline. The fix wasn't more compute — it was event-driven pub/sub architecture.
 💡 If N sequential steps can each kill the rest, that's an architecture problem
 The pattern: a publisher pushes one message per source to a broker (SQS, Kafka, RabbitMQ, Google Pub/Sub). Independent worker subscribers pull messages and crawl in parallel, completely isolated from each other. When one source breaks — a timeout, a layout change, a 500 — it fails alone in its own worker. The rest of the pipeline never notices. Three properties fall out of this for free: (1) ingestion latency stays flat regardless of source count, because workers run concurrently not sequentially; (2) failures are isolated by design, with a dead-letter queue capturing the broken messages for retry; (3) the system scales horizontally — add more subscriber workers, get more throughput, no code change. This is the difference between a script that crawls 50 sources and a platform that crawls 50,000. I added a full architecture diagram for this pattern in the Architecture section (tab: Pub/Sub Event-Driven). The takeaway generalises beyond scraping: any pipeline where one slow or broken step blocks all the others is one message broker away from being resilient.
 
 +
 Architecture
 Browsers as a Session Layer, Not a Scraping Product
 HTTP is fast, browsers are expensive. Right architecture: browser for session warmup and hard challenges onlythen lightweight HTTP workers for bulk collection.
 💡 Browser for session warmup → HTTP for bulk collection
 The architectural insight: most scraping pipelines use browsers for everything, which is expensive. But you only actually need a browser for two things: (1) session establishmentgenerating valid cookies and session tokens that a protected site will accept, and (2) hard challenge pagesAkamai sensor.js, Cloudflare Turnstile, DataDome WASM challenges. Once you have a valid session cookie, the rest of the data collection can happen via lightweight HTTP requests at 10-100× the speed and 1/100th the memory. Implementation: use camoufox or rayobrowse to generate sessions, then curl_cffi with the extracted cookies for bulk collection. Rotate sessions every 30-50 requests.
 
 +
 IoT / Edge
 A Microcontroller Scraping Live Weather Data via API
 An ESP32 calling a scraping API, parsing JSON, displaying on TFT screen. The abstraction is now clean enough for devices with no Python. Scraping as real infrastructure.
 💡 Scraping APIs are now clean enough for Arduino, #esp8266 #esp32
 An ESP8266 microcontroller running Arduino firmware makes an HTTPS request to a scraping API endpoint. The API (Zyte) handles: TLS negotiation with the target, JavaScript rendering if needed, anti-bot bypass, data extraction. The microcontroller receives clean JSON back and renders it on a TFT display. This demonstrates that scraping has become a proper infrastructure layer, just like how you would call a weather API, you can now call a scraping API from any HTTP-capable device. The broader implication: scraping is no longer just a Python script on a server. It is a data access layer that any application can use. The complexity of browser fingerprinting, proxy rotation, and anti-bot evasion is fully abstracted behind a simple API call.
 
 +
 Browser stack / Serverless
 XVFB + Headed Chrome + Nodriver Even on Serverless
 The real breakthrough is not header spoofing. It is running a real browser in a real headed environment, even on serverless. Modern anti-bots are trained to detect machines pretending to be browsers, so the answer is to actually be one.
 💡 The future belongs to systems that execute like real humans from the ground up
 
A few years ago, simple HTTP requests were enough. Today Cloudflare, DataDome, Akamai, and HUMAN Security analyse hundreds of browser, network, and behavioural signals simultaneously. The stack you choose now matters more than the proxy you point it at.

Detection risk by stack (lowest is best):

Stack
Detection Risk
Limitation

requests / httpx
Very High
No browser rendering

Scrapy
Very High
No behavioural realism

Headless Browser
High
Headless traces (WebGL=null, missing extensions)

Stealth Headless
Medium
Partial spoofing, JS patches detectable

XVFB + Headed Browser
Lowest
Higher data consumption

The full stack:
✓ XVFB virtual display (real X11 server, not headless flag)
✓ Fully headed Chrome (no --headless anywhere)
✓ Nodriver for CDP without webdriver artefacts (or Camoufox for Firefox)
✓ Authentic TLS / HTTP-2 behaviour (the browser handles this for free)
✓ Humanised interactions (Bezier-curve mouse, variable scroll timing)
✓ Residential proxies, sticky session for trust accumulation
✓ Fingerprint coherence (UA + WebRTC + DNS + timezone all match exit IP)

Why XVFB beats --headless even with stealth patches: headless Chrome reports HeadlessChrome in the user agent (fixable), missing extensions (probe-able), and zero GPU context (the real killer). With XVFB you get a real display, Chrome runs in headed mode, extensions load normally, and the GPU stack is whatever your server provides. JS patches still leave Function.prototype.toString() traces; XVFB does not.

The serverless angle: the conventional wisdom is that serverless cannot run a real browser. The trick is provisioning an X11 socket inside the container (Xvfb :99 &, DISPLAY=:99 chrome ...) so Chrome runs headed on a virtual display. Lambda has hit memory limits historically, but ECS Fargate, Cloud Run, and Modal handle this comfortably with ~1GB memory per browser instance. The result: serverless infrastructure behaving like real users, not automation.

What still beats XVFB: C++ patched browsers like Camoufox (canvas, WebGL, audio at the binary level) and CloakBrowser (real extension probe profiles) close the remaining 10%. But for the 80-90% of targets where XVFB + Nodriver gets you in, the cost difference is significant. Camoufox: 200MB+ per instance. XVFB headed Chrome: same memory but works on any Chromium binary.

Modern anti-bot systems are trained to detect machines pretending to be browsers. The path forward is not better lies, it is fewer lies.

---

## COST, BUILD-VS-BUY AND BENCHMARK SKEPTICISM

13 Cost & economics
Build vs buy:the number that decides
The most common mistake is treating "can I bypass it" as the only question. The real production question is "what does each successful record cost, and is rolling my own cheaper than paying someone else." Here is the honest math, with the caveat that exact prices move constantly, so treat these as orders of magnitude, not quotes.

Self-hosted stealth stack vs managed API#

ApproachMonthly cost driverRough costBest when

Self-hosted: HTTP + curl_cffi + residential proxiesProxy bandwidth (the dominant cost), small serverProxy GB at roughly 3 to 8 USD/GB, plus ~50 to 200/mo computeHigh volume on targets that yield to TLS impersonation, where you control bandwidth use
Self-hosted: Camoufox/CloakBrowser cluster + residential proxiesServer CPU/RAM for browsers, plus proxy bandwidth (browsers burn far more GB)~200 to 1,200/mo compute, plus heavy proxy GB; engineering time to maintainHardened targets that need a real browser, at volumes where per-request API fees would exceed infra cost
Managed API (ScraperAPI, Zyte, Bright Data Web Unlocker, Scrapfly)Per successful request, anti-bot handling includedRoughly 1 to 5 USD per 1,000 requests (more for JS-render / hard targets)Low-to-medium volume, or hard targets where engineering time is worth more than the per-request fee

The break-even rule of thumb: Managed APIs win until volume gets high enough that per-request fees dwarf the cost of running your own infra. A worked example: at 1 USD per 1,000 requests, 10 million requests/month is ~10,000 USD on a managed API. A self-hosted stack handling the same volume might run a few hundred in compute plus 1,000 to 3,000 in proxy bandwidth, call it 2,000 to 4,000 all-in, if you have the engineering time to build and babysit it. Below roughly 1 to 2 million requests/month, the managed API is almost always cheaper once you price in your own hours. Above 10 million, self-hosting usually wins on raw cost. In between, it depends on how hardened the target is and how much your time costs.

The cost everyone forgets: maintenance. A managed API absorbs every anti-bot change for you. A self-hosted Camoufox cluster does not, when Akamai ships a new sensor build or your fingerprint starts leaking, that is your weekend. Price your own time into the comparison. A stack that is 3,000/mo cheaper but eats four engineer-days a month is not actually cheaper. For many teams the right answer is hybrid: managed API for the hard or low-volume targets, self-hosted for the high-volume easy ones.

The bill is mostly waste, not price#

Most people model scraping cost as proxy price times bandwidth and stop there. The real bill is hiding in inefficiency, and it is usually the bigger number. The mental shift that saves the most money is to stop thinking in cost per gigabyte and start thinking in cost per successful record. A pipeline with a low success rate pays for every failure twice: once in the wasted request, once in the retry.

 
 
 Driver 1 · Retries and rotation
 Failure compounds quietly
 Unnecessary retries and a blunt rotation strategy open a steady gap between the cost you expected and the cost you actually pay. Every blocked request that triggers two retries has tripled its bandwidth for zero data. Before buying more proxy, fix the success rate, it is almost always cheaper to stop failing than to pay for the failures.
 
 
 
 Driver 2 · Proxy-type mismatch
 Mobile IPs on an easy target
 Each proxy type is a different point on the speed, cost, and trust curve. Mobile IPs are expensive precisely because they are highly trusted, so they earn their price on a hardened target. Pointing them at a low-protection site burns money for no extra success. Match the proxy tier to how hard the wall actually is, do not run one universal strategy across every target.
 
 
 
 Driver 3 · Loading the whole page
 You are paying for images and fonts
 When a headless browser loads a page it also pulls images, scripts, video, and fonts you never parse. If the data lives in structured JSON, use an HTTP request and skip the render entirely. Reserve the browser for pages that genuinely need JavaScript. Blocking heavy resource types on the requests you do render is one of the cheapest wins available.
 
 
 
 Driver 4 · Third-party bleed
 One GB on target, five on its trackers
 A modern page calls a pile of third-party services as it loads. I have seen a single GB spent on the real target drag in several more GB of analytics, ad, and widget traffic routed through the same proxy. Block the domains you do not need, and give each website its own proxy credentials so a runaway bill shows up as one obvious outlier instead of hiding in a shared total.
 

The largest cost of all never appears on an invoice, because it is salary. The four drivers above are the ones you can see in a bill. The one you cannot see is the senior engineer who keeps resurrecting the same scraper because it breaks every other week. That time is real money, it is usually the biggest line, and it shows up as headcount rather than spend, so it escapes the comparison entirely. The honest unit is cost per usable document: every dollar of proxy, compute, retries, and human maintenance divided by the count of records you could actually trust and use. Model it that way and the rankings often invert. The source that looked cheapest by the gigabyte turns out to eat an engineer alive in babysitting, while the one that looked expensive runs untouched for months and quietly wins on cost per document. You cannot manage what you have not measured, and the thing most teams have never measured is the one costing them the most.

A perspective worth holding while you read the rest of this guide#

This guide is largely about how to get through defences, so it is worth surfacing the strongest argument against treating that as the main event. The case goes like this: bypassing anti-bot systems is a treadmill. You ship a workaround, the target adapts, and you spend the next sprint on infrastructure that produced no business value, running hard to stay in place, and the day a site flips its detection stack overnight you are back to the start. By this view the teams genuinely winning at data collection are not the ones with the cleverest bypasses, they are the ones who abstracted that complexity away so their best engineers spend their time on what the data is for, not on how to fetch it without getting blocked. The pointed version of the question: when did "how do we bypass this?" become more important to your team than "what are we actually going to do with this data?"

It is a fair challenge and mostly correct as a default. It is also not absolute, which is the honest other half. Sometimes the bypass is the business: the data is not available any other way, no managed provider covers the target, the margin only works if you control the cost per document yourself, or the capability is the moat. The resolution is not "always build" or "always buy," it is to decide deliberately which hard problems deserve your strongest people. For most teams, on most targets, the answer is to abstract the fetching away (a managed API, a maintained library, the cheapest rung that works) and spend the saved attention on the data itself. Reserve the deep stealth engineering for the targets where access is genuinely the differentiator. The rest of this guide gives you the depth for when you need it; this note is the reminder to be honest about how often that actually is.

A warning about the one clean number: cost per thousand requests#

It is tempting to reduce a target to a single figure, what an operator pays per thousand requests, carried out to a fraction of a cent. It looks authoritative and it makes comparisons easy. It is also quietly dishonest, and it is worth understanding why before you trust one. The inputs that feed that number, solver prices, proxy costs, the detection tier a site happens to be running, are opaque and swing constantly. Doing precise arithmetic on inputs like that produces a figure whose decimal places are invented: false precision dressed up as measurement.

The FAIR Institute, which argues about exactly this in cyber-risk quantification, has the cleanest framing: many "quantitative" scores are really ordinal judgements with numbers painted on, where the digits could be swapped for colours or the words high and low and nothing would change. You cannot legitimately multiply red by yellow, and running multiplication and division over solver-price guesses and proxy-cost estimates is the same move, math on a colour scale. The practical takeaway is not to stop estimating cost, it is to estimate it honestly: prefer a range over a false-precision point value, treat any per-request figure as a snapshot that decays the moment a vendor changes pricing or a target changes its detection tier, and keep the number anchored to the one thing you can actually measure, the cost per validated payload on your target, from your own runs. A wide honest range beats a narrow invented one, because a single confident decimal invites decisions the underlying data cannot support.

The other number to distrust: a vendor's success rate#

The same skepticism applies to the headline figure every unblocker sells on. Search the market and you will find vendors advertising 98, 99, even 99.99 percent success, and they cannot all be right about the same web. These are usually not lies, they are selective truths, because when a vendor publishes its own benchmark it silently controls every variable that decides the score: which URLs are tested, how success is defined, how retries are counted, which pricing tier runs, and, crucially, whether the harness is published at all. A 99 percent from one vendor and a 98 percent from another are not two products a point apart, they are two different tests, and with no shared harness there is nothing to check. Even the word "independent" is weak protection: two of the most-cited independent scraper benchmarks each rank a single provider first, and the domains trace back to the very companies that top them.

The defensible way to read any success-rate claim (including the ones quoted elsewhere in this guide, which are vendor or benchmark figures reported as snapshots, not independent verification) is to ask five questions the number alone never answers. What was the pass criterion, since a 200 that returns a captcha shell or an empty JS page is a failure only if the harness checks the body for a real content marker rather than trusting the status code. What was in the target set, because a suite with one site per anti-bot vendor lets a single leniently configured deployment stand in for all of DataDome or Kasada. What tier ran, since premium stealth and the default endpoint are different products. Is the harness public and reproducible. And how fresh is it, because a defense and a provider both drift monthly, so any figure is a dated snapshot the moment it is published. The honest signal is not a high number, it is a benchmark that shows its own failures and hands you the harness to rerun. When a claim reports 100 percent, the most likely reading is not a perfect product but a test that stopped being hard, and the missing failures are exactly the information you needed.

Spend the model once: build-time AI, not runtime AI#

The single largest cost decision in an AI-assisted scraper is where the model sits. A whole category of tools puts a language model in the runtime: an agent drives a headless browser, loads the full page, and reads it on every single run. It demos beautifully and it is a margin killer at scale, because each run pays three times over, for a full browser rendering tens of megabytes, for residential-proxy bandwidth on all of it, and for model inference and its latency, every time, to redo work that never changed. The hard part (working out a site's anti-bot flow and writing correct request code) is a one-time job and exactly what a model is good at. The repetitive fetching is exactly what it should not touch. So spend the model once, up front, to build a plain request-based scraper, then run that scraper with no model in the loop at all.

The number that makes this concrete comes from a published airline-pricing teardown. Loading the full page for every search measured about 19.25 MB; bootstrapping the anti-bot session once and then calling the JSON endpoint directly measured about 22 KB per subsequent search, roughly 875 times less bandwidth. Priced at a mid-range residential rate around four dollars per gigabyte, a million searches is on the order of nineteen terabytes and tens of thousands of dollars the page-loading way, versus tens of gigabytes and about a hundred dollars the direct-API way, before you even add the per-run inference bill the agentic version also carries. The distinction that matters is not browser versus no browser, it is whether you reload the whole page for every data point or hold a session and call the API. A real browser can be optimised the same way (bootstrap once, then fetch() the API directly instead of reloading), and the moment you have done that, the browser and any model driving it are dead weight you can drop. The one genuinely useful role for a model at build time here is reading the real wire traffic, the exact header order and the JA4/TLS fingerprint that a HAR export or DevTools cannot show, and reproducing it in request code, which is precisely the analysis you only need to do once.

The cost nobody budgets for: raw browser runtime speed#

When you cannot avoid a browser, one line item quietly dominates the bill at scale, and almost nobody measures it: how fast the browser itself executes. Agents, scrapers, and automation pipelines all live inside a browser, so every slow render is a tax paid on every step of every job, multiplied across millions of pages. The industry-standard way to put a number on it is BrowserBench Speedometer (a cross-vendor benchmark that times simulated user interactions; a normal laptop scores roughly 8 to 15), and a cluster of browser-infrastructure vendors has started competing openly on it. Reported figures from mid-2026 put dedicated bare-metal setups (real consumer CPUs, a custom OS image on physical hardware) around 15 to 21, well above cloud-hosted headless browsers on shared infrastructure. Treat the exact numbers as dated, vendor-run snapshots rather than gospel, but the ordering is the durable point: where your browsers run changes their throughput by a large factor, and at volume that factor is real money and real wall-clock time.

The sharpest insight in that discussion is a hidden cost this guide has been circling all along: software stealth has a runtime price. One vendor's own numbers showed the same infrastructure scoring materially higher in a plain headless configuration than in its stealth-hardened one (roughly 14.4 versus 9.66 on the same benchmark), and that gap is precisely what the stealth patches cost you in execution speed, because every hook, shim, and property override the stealth layer installs runs on every operation. It sharpens the recurring build-once, real-hardware theme from a new angle: patching a normal browser to look human is not only more fragile than running genuine hardware, it is also measurably slower, so the "rebuild the environment rather than patch the flags" advice from the detection section turns out to be a performance argument as well as a stealth one. The practical takeaway for costing a large browser-based job is to benchmark your actual browser configuration, stealth layer included, before you size the fleet, because the per-page time you measure on a clean headless browser is not the time you will get once the stealth is switched on.

 5 field notes on cost and economics
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / The market
 The Scraping Market Split in Two, and Most of the New Half Skipped the Hard Part
 From someone who sits in both camps: the industry has cleaved into infrastructure (proxies, rotation, anti-bot bypass, uptime) and intelligence (AI-native search and retrieval for LLMs and agents). The first is mature and commoditised. The second is younger, better funded — and mostly inherited the easy 80% of the web and called it done.
 💡 “Most of them can't touch a site with real anti-bot protection”
 The two halves, drawn honestly by someone selling both. On the infrastructure side, everyone's success rate now sits in the high 90s, so what's left to compete on is price and reliability, not features — the benchmark-card reality, from the vendor's chair. On the intelligence side sit the AI-native retrieval products with great docs and great positioning whose search product falls over the moment it meets a hardened target. They built for the agent era from day one but never solved access, because access was the boring part everyone assumed was done.The observation that makes it more than vendor talk: brand equity does not transfer across the split. Years of proxy performance awards and a top response-time record mean nothing to a developer discovering tools through GitHub stars and framework docs — and conversely the AI-native players' positioning means nothing the first time their retriever hits a DataDome wall. It's the same fault line as the API-vs-scrape scorecard in §01: the hard, valuable 20% is exactly the part the shiny layer skipped, and it's where the work still is.Why it matters if you're choosing tools, not selling them. An “AI-native” data product that demos beautifully on a blog and a docs site is telling you nothing about whether it can reach the retailer, the marketplace or the forum you actually need. Test any retrieval or agent product against your hardened targets before you believe its coverage — because most of them, by this account, inherited the reachable web and quietly redrew the boundary of “the web” around it.Source: Gabrielé Vitké (proxy + AI vendor).
 
 +
 Aug 2026 / The thesis
 “The Web Is Being Priced, Not Blocked”
 The researcher behind the largest audit of web access controls ever published was asked to sum up 24,898 sites across 230 countries and 110 industries. She did not reach for a number. “The web is being priced up. The spirit of the worldwide web was that information needs to be free. But now the information is out there; it’s just not free. There’s a different cost to getting it.”
 💡 Most of the web is cheap. Nobody pays you to fetch the cheap part.
 The distribution first, because it is counterintuitive. Only 18.5% of audited landing pages had no detectable barrier at all — but 30.7% had exactly one, and 88% still land in the two easiest access tiers. Most of the web really is straightforward with the right tools. The catch is the shape: “It’s almost like the Pareto ratio. Eighty percent could be easy. But most of the work is going to be done on the 20%, which is getting more and more aggressive, more and more closed. If you’re getting paid to do web scraping, most likely you’ll be working in that 20%. That’s where the work is.”Which is why “how hard is the web” is the wrong question. Difficulty skews violently by sector, because the barrier tracks commercial value rather than technology. Fashion is the single hardest industry in the report, needing moderate-or-harder infrastructure on 57% of its sites; reference and publishing sit at the opposite end. The barrier you actually meet has less to do with the web in the abstract than with which corner of it pays your bills.The line worth pinning above a desk. “Web scraping at scale is getting more and more complex. It’s not just a script kind of problem; it needs to be a system” — proxy management, concurrency, a decision about whether the page needs JavaScript and a fallback when it does, and cost managed across all of it. “It’s all about orchestration and instrumentation.”Read the signals as different kinds of thing, not one wall. CAPTCHA is close to binary — “we want you to be gone”. JavaScript either loads or it does not, and plenty of sites run it purely for user experience while incidentally making you pay for a headless browser. Rate limiting is neither: it is a dial, not a switch — “please, yes, you can access this automatically, but don’t rush”. So the operational question is not is this site blocked but how many of these mechanisms is it running, and which kind. A CAPTCHA changes your answer; a little client-side JavaScript might not.And the finding that surprised the vendor who commissioned it. Anti-bot adoption came in at just 18.5%, against their own instinct — because their day-to-day book of business is exactly the sites that run it. “It’s sites with e-commerce, pricing intelligence, data that correlates deeply with commercial value.” That is a vendor stating plainly that its own sample is not the web, which is rarer than it should be.Two methodology notes that earn the rest of it trust. Every “website” here is one landing page, hit once, from a single IP — “what’s measured is the front door, not the whole building”, so barriers deeper in a site or visible only from certain regions are simply not in the data, and they say so. And they deliberately did not run the audit through their own unblocking API, because it would have solved every barrier and erased the measurement: “we wanted to capture the web as it is, just a simple request and response, and not taint the process of getting it.” Set that against the benchmark card in this feed — this is what it looks like when a vendor builds the instrument to survive its own result.
 
 +
 Aug 2026 / Economics
 Price Is Not a Performance Metric
 A benchmark of major scraping APIs across fifteen high-traffic sites asked a question nobody usually tests: does paying more actually get you more? On Amazon the correlation between price and performance was 0.25 — close enough to nothing that a roulette wheel is a defensible selection method.
 💡 Same performance threshold, 2× to 15× the price, depending only on who you picked
 The spreads, among providers that all cleared the same bar — an average success latency of fifteen seconds or under. Amazon: $19 against $300 a month, a 15.8× difference. Target: $19 against $249, 13.1×. Walmart: $29 against $300, 10.3×. Etsy: $59 against $475, 8.1×. Across fourteen of the fifteen sites tested, the gap between the cheapest and the most expensive qualifying provider was at least 2×.And it is not merely that cheap sometimes ties. On Amazon: ScrapingAnt at $19 returned in 13.19s, Bright Data at $300 in 4.18s, and Zenscrape at $59 in 2.70s. The most expensive provider was not the fastest. Nothing here says premium providers are bad — it says the number on the pricing page is not a performance figure, and treating it as a proxy for quality is how teams end up paying an order of magnitude for a threshold they could have hit for a rounding error.The part that makes it actionable is that the relationship is per domain, not global. Price tracked performance reasonably well on Home Depot and Best Buy, and barely at all on Amazon, Walmart, Booking.com, Glassdoor and Google. So there is no ranking to memorise and no provider to recommend in the abstract. The only reliable method is the unglamorous one: benchmark candidates against the sites you actually intend to scrape, at the volume you actually intend to run, and let that decide. It pairs exactly with the success-rate card above — both end at the same place, which is that the only number describing your situation is one you generated.
 
 +
 Aug 2026 / The economics
 227 Bot Visits Per Human Visit, and a Bill With a Number On It
 Several posts this week are the same story told from four seats: a publisher counting the ratio, a vendor selling the block, a lobbyist writing the bill, and a founder reading the growth curve. Held together they describe the moment blocking stopped being a security decision and became a commercial one.
 💡 Reliable scarcity, not cybersecurity, is what brought AI platforms to the table
 The ratio first. One publisher reported 227 bot visits for every human visit across a single quarter. The growth behind that: Cloudflare measured overall crawling up 18% year on year across more than thirty major crawlers, with GPTBot up 305% and ChatGPT-User up 2,825% — moving GPTBot from the ninth-largest crawler in the sample to the third in twelve months.What the blocking actually did. Cloudflare began blocking AI crawlers by default across its share of the web unless a customer allows them, and from 15 September 2026 mixed-purpose crawlers that combine search and training — Googlebot included — are blocked on ad-supported pages unless separated first. The strategy quote is unusually candid: customers use the tools to “create reliable scarcity for their content, then negotiate better deals.” One publisher CEO put the mechanism plainly: they block almost every AI crawler except the one they have a deal with and the one they cannot block, and “they have to pay for it.” Financial Times, The Atlantic, Ziff Davis, Condé Nast and the Associated Press are named as working through the same arrangement. Pay-per-crawl is still in closed beta and the emphasis has already shifted toward pay-per-use, with payment details unsettled.The waste argument doubles as the pricing argument. More than half the time, by Cloudflare's account, an AI crawler fetches something it has already fetched and which has not changed since. That is the same observation underneath the 402 card — the traffic is expensive and largely redundant, which is precisely what makes metering attractive to the party paying for it.And the part that leaves engineering entirely. Three US Representatives introduced H.R. 9915, the Stealth Bot Prohibition Act, aimed at bots that disguise their identity to take content. Read alongside the New York statute already in this guide, the direction is consistent: identity disclosure is migrating from a thing you choose to a thing you must do. Whatever happens to this particular bill, a collector whose whole strategy is indistinguishability should notice that the strategy is being legislated at rather than merely defended against. §00 has the case law; this is the other tide.
 
 +
 Aug 2026 / The other side
 A Costed Defence Ladder, Written By The People Paying The Server Bill
 This guide is mostly written from the collecting side. A five-layer defence guide published for Drupal operators is worth reading precisely because it is not — and because its organising idea is one collectors rarely think about: every layer has a price, and the price rises the closer to the application you block.
 💡 “The further away from the app you can block, the cheaper that block usually is”
 The ladder in order, with the cost note attached to each. Layer one, CDN and WAF at the edge, drops high-volume script traffic and DDoS noise globally and costs almost nothing because it never reaches the origin. Layer two, ingress and nginx rate limiting on high-risk paths, still cheap because it fires before PHP boots. Layer three, caching — CDN, dynamic page cache, Redis — serves repeats from memory with no database hit. Layer four, the application itself (Perimeter, Antibot, Honeypot, CrowdSec), where context-aware decisions about forms, logins and search filters live, and which is expensive because it needs a full bootstrap. Layer five, a proof-of-work proxy on uncacheable routes that pushes the cost back onto the client's CPU.The pressure point they name is faceted search, and it is the most useful thing here for someone on the other side. Every filter, category, sort order and pagination state generates its own URL, so a crawler walking filter combinations produces a combinatorial explosion of uncached requests, each one forcing PHP execution and database queries. That is why a site which serves you happily on product pages turns hostile the moment you start walking facets: you moved from layer three to layer four, and you did it at volume.Two things to take from it. First, the defensive posture you meet is a function of which layer your traffic lands on, not of how the site feels about scraping in the abstract — requesting cacheable URLs at a sane rate is genuinely cheap for them and is therefore tolerated far longer. Second, they put automated traffic at more than half of all traffic for a typical site, which is the number that funds the entire defence industry described in §03.Source: amazee.io, layered Drupal bot and scraper defence.

---

## POST-EXTRACTION — Storage, Dedup, Validation, Document Parsing

14 After the bypass
What happens to50 million rows
Bypassing detection is the part everyone writes about. But getting the data is only the start. The questions that actually decide whether a scraping operation survives are about what you do next: where the data lands, how you avoid storing the same thing twice, and how you notice when a site quietly starts feeding you garbage.

Storage: stop dumping JSON into a folder#

 
 
 Format
 Parquet, not CSV or JSON
 For anything past a few hundred thousand rows, columnar Parquet beats row formats decisively: 5 to 10x smaller on disk through column compression, and analytics engines (Athena, DuckDB, Spark) only read the columns a query touches. A 50M-row crawl that is 40GB as JSON is often under 5GB as Parquet, and queries run an order of magnitude faster. Partition by crawl date or source so you can prune whole files without scanning them.
 
 
 
 Layout
 A simple medallion layout
 Land raw responses untouched in a bronze layer (S3/GCS, exactly as scraped, your audit trail and replay source). Clean and normalise into silver (typed, deduplicated, schema-validated Parquet). Build query-ready aggregates in gold (the tables analysts and APIs actually hit). When a parser bug ships, you re-run silver from bronze without re-scraping, which on a hardened target can save you weeks and a lot of proxy spend.
 

Deduplication: the same item will arrive many times#

 
 
 URL-level
 Seen-URL sets & Bloom filters
 A large crawl revisits the same URL constantly through pagination loops, cross-links, and retries. Holding every seen URL in a Python set works until memory dies around a few million. A Bloom filter (or scrapy-redis's RFPDupeFilter backed by Redis) checks membership in constant memory with a tiny, tunable false-positive rate. For distributed crawls, a shared Redis set keeps every worker honest so two workers never fetch the same page.
 
 
 
 Content-level
 Hash the content, not the URL
 The harder duplicate is the same product reachable via three different URLs (tracking params, locale prefixes, canonical vs vanity). URL dedup misses these. Compute a stable hash over the meaningful fields (a normalised product ID, or a hash of the cleaned record), and dedup on that. Keep a last_seen timestamp so you can tell a genuine update apart from a re-scrape, and upsert rather than blindly insert.
 

Data poisoning: when the bypass succeeds but the data is fake#

Passing the bouncer does not mean the data is real. Sophisticated sites detect scraper-like behaviour after the initial check and respond not by blocking you but by quietly serving poisoned data: subtly wrong prices, shuffled listings, fabricated rows, or stale snapshots. You get clean 200 responses and a healthy-looking dataset that is silently corrupt. This is more dangerous than a block, because a block is obvious and poisoned data flows straight into your decisions.

 
 
 Detection
 How to catch poisoning
 Maintain a small hand-verified ground-truth sample (a few dozen records you check manually) and diff every crawl against it. Watch for statistical anomalies: prices that are suspiciously round, distributions that shift overnight, or the same record varying between two near-simultaneous requests from different IPs. Cross-check a sample against the mobile API or a logged-in view. If two clean sessions disagree on the same field, suspect poisoning before you trust the data.
 
 
 
 Cause
 Why it happens to fast scrapers
 Poisoning is usually triggered by behaviour, not fingerprint: requesting pages far faster than a human could, hitting endpoints in a non-human order, or ignoring the rendering layer entirely. The fix is the same boring discipline that prevents bans: pace requests, vary timing, follow realistic navigation paths, and do not request the whole catalogue in five minutes. Slow, human-shaped scraping gets real data; greedy scraping gets fed lies.
 

A clean 200 is not the same as a clean dataset. Modern firewalls rarely bother to hard-block you anymore. They return 200 OK with a degraded body: an empty list, sanitised prices, a tarpit that drips bytes forever, or yesterday's snapshot frozen in place. If your scraper treats the status code as the success signal, you will log thousands of green requests and ship corrupt data. Validate the shape and the statistics, not the status. The question is never "did I get a 200", it is "did I get roughly N records like yesterday, with prices in the range I expect, and the fields I depend on populated". Wire that check into the pipeline so a silent drop to ten percent fill rate pages you instead of flowing downstream.
One operational habit that pays for itself: keep a small Targets table as the single source of truth for every endpoint you depend on. Endpoint path, required headers, cookie shape, the schema version you last validated against, and the expected record count. When a site renames /api/v2/ to /api/v3/ or reshapes a field, you change one row instead of hunting through scraper code. It costs half a day to write and saves you the morning you would otherwise spend discovering, after the fact, that a fortnight of data is empty.

The quiet failure mode: a 200 with nothing in it. Poisoning replaces your data with something wrong. There is a milder and far more common variant that replaces it with nothing at all, and it is harder to notice because every dashboard stays green. A verification interstitial or a soft rate limit answers with HTTP 200 and an empty result set rather than a challenge or an error, so the request succeeds, the parse succeeds, and the record count is zero. Search endpoints are where this shows up most, and practitioners tracking one major engine through mid-2026 reported exit addresses in some regions failing this way the overwhelming majority of the time while the success metric never moved.The defence is the same instinct as the poisoning check, applied to volume rather than values. Alert on record count per request, not on status code. A run that returns 200s and zero rows is a failing run, and it should page you exactly as loudly as a run of 403s. The operational counter that works is unglamorous: take a fresh session on one sticky address, request a small amount, confirm real records come back, and only then increase the rate on that same session, holding the identity still rather than rotating into a new one. You are looking for the point at which results thin out, which arrives before the point at which requests are refused.

 The status code is a two-way signal: soft 404s, crawl budget, and hallucinated URLs#

The earlier point was that a 200 OK can hide failure on the way in, when a tarpit feeds you poisoned data. The same dishonesty runs the other way too, and it is worth understanding from both chairs because it shapes how crawlers (yours, Google's, and an LLM's) treat a URL.
Many JavaScript frameworks, by default, answer a request for a URL that does not exist by rendering a "not found" view and still returning 200 OK in the header. The page says the content is gone; the server says everything is fine. Google calls that mismatch a soft 404, and it is not harmless. A real 404 or 410 tells a crawler to stop coming back. A soft 404 keeps the dead URL in the queue, because the server never admits the page is gone, so the crawler keeps re-fetching nothing.

 
 
 The attack your own default config enables
 Crawl-budget drain as negative SEO
 If a framework hands a 200 to every undefined route, an attacker does not need to touch the site at all. Point bots at thousands of made-up URLs on the domain; each one renders, returns 200, and consumes crawl resources that should have gone to real pages. On a large site the real content gets discovered slower. That is a negative technical SEO attack made possible by the server never saying "gone." The fix is boring, which is the point: return a real 404 or 410 for routes that do not resolve, and verify the rendered status in Search Console rather than how the page looks in a browser. The browser hides the header; the crawler reads it.
 
 
 
 The newer, stranger layer
 When a 200 confirms a hallucinated URL
 Large language models invent URLs the same way they invent facts. One audit of model-suggested login links for major brands found roughly a third pointed at domains the brands did not own, many unregistered and waiting to be claimed. The HTTP status is the honest answer in this exchange. A proper 404 tells the machine the page never existed. When a server answers a hallucinated URL with 200 OK, it does the opposite: it confirms the fiction and tells the model its guess was right. For a scraper consuming model-suggested targets, this is a direct lesson, treat status codes as ground truth over a page that merely looks populated, because the rendered body is exactly what a soft 404 gets wrong.
 

When the data is not HTML: parsing documents#

A large share of the data people actually need does not live in a DOM you can select. It is locked inside PDFs, scanned invoices, receipts, statements, and image-only files, where there is no clean JSON API to hit and no selector to write. This is its own extraction problem that sits downstream of fetching, and it is worth treating as a first-class part of the pipeline rather than an afterthought, because it is where a surprising amount of real-world value (and error) lives. The naive approach, run OCR and parse the raw text with regex, breaks the moment a layout shifts, a scan is skewed, or a total wraps onto two lines.

 
 
 The durable pattern
 Hybrid validation: two parsers, one confidence score
 The pattern that holds up on messy real documents runs two extractors and cross-checks them rather than trusting either alone. A deterministic OCR-plus-rules parser (an engine like EasyOCR or Tesseract, then field rules) gives a cheap, reproducible baseline with per-token confidence. An LLM or vision-language model reads the same document and returns structured JSON against your schema, handling the messy cases rules cannot, skewed scans, odd layouts, missing labels. You then compare the two outputs and derive a confidence score from how much they agree. Where they match, you trust the value; where they diverge or both score low, you flag it. Neither half is reliable on its own, the rules are brittle and the model can hallucinate a plausible total, but the agreement between them is a far better signal than either confidence in isolation.
 
 
 
 Why it matters downstream
 Confidence routing beats silent failure
 The point of scoring every extraction is what you do with the score: route low-confidence documents to human review instead of letting a wrong number flow silently into your dataset. This is the document-parsing version of the data-poisoning lesson above, a model that confidently returns grand_total: 369963 from a blurry receipt is its own kind of soft 404, output that looks clean and is wrong. A schema-consistent JSON contract, a numeric confidence on every field, and an explicit human-in-the-loop lane for the low-confidence tail are what turn document parsing from a demo into something you can trust at volume. The same discipline applies whether the input is a scraped PDF, an emailed invoice, or a photographed receipt.
 

 7 field notes on life after the bypass
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Method
 You Cannot Prove You Got the Whole Catalogue, and Nobody Checks
 Every completeness idea in this guide is defensive — did a field go missing, did coverage drop against the last run. None of it answers the prior question: did you ever have all of it? A crawl that has silently been seeing 70% of a catalogue since the day it launched has no delta to alert on, because the baseline was already wrong.
 💡 A stable row count is evidence of a stable bug just as easily as a stable catalogue
 Why pagination lies. Most listing interfaces cap out — a thousand results, fifty pages, an infinite scroll that stops. Query a category with more items than the cap and you get exactly the cap, every run, forever, with no error. Your monitoring sees a beautifully consistent number. That is the failure mode, not the reassurance.Three ways to actually measure recall. Facet decomposition: slice the same catalogue by a different dimension — brand, price band, date — and check the union exceeds what a single traversal returned. Identifier probing: where identifiers are dense, sample the space directly and see how many resolve to items your crawl never found. Cross-source reconciliation: compare against a second source that should agree, including the site's own sitemap and its declared result counts.Then write the number down. Estimated recall belongs next to freshness in what you report, because a customer told a dataset is complete when it is 70% complete has been misled regardless of how clean every returned row is.
 
 +
 Aug 2026 / Data quality
 LLM Honeypotting: When the Site Stops Blocking You and Starts Lying
 The failure mode inverts. Traditional blocking is loud — 403, 429, a CAPTCHA — and your logs tell you which target needs attention. A honeypot returns valid HTML and 200 OK, with nothing in the response marking it fake, so it flows straight into the dataset and only surfaces later when a model starts producing unreliable answers. It is a data-quality problem wearing an access problem's clothes, and it hits training-data teams hardest because bad rows get buried in the week's volume.
 💡 Three techniques, and each one bills you differently
 Proof of work costs you compute and latency on every request. Content mazes cost crawl budget, bandwidth, processing and storage — Cloudflare's AI Labyrinth is the named public example, exposing hidden links to pre-generated decoy pages that no human ever sees, and treating a bot that follows them as confirmed. Data poisoning costs you the dataset itself.The fake content takes recognisable forms: fabricated product listings and identifiers, prices attached to nothing real, contradictory dates and specifications, and invented relationships between entities.Detection, and this is the actionable half. Seven signals: a crawl count far exceeding what sitemap.xml declares; URLs that nest deeper without ever reaching a terminal page; fluent text containing no verifiable facts; near-identical content under many distinct URLs; and internal contradictions across pages that should agree. Then run near-duplicate detection — SimHash, MinHash, n-gram comparison or embeddings — because exact-match deduplication misses generated pages that differ by a few words. “A response can look valid, parse correctly, and still lead the scraper nowhere.”
 
 +
 Aug 2026 / Pipeline
 A Citation Proves Where an Answer Came From, Not That It Is Still True
 An engineer built a small agent over a search index of a website's documentation, then changed the source site and left the index alone. The agent kept answering with complete confidence and the same citations, which still resolved, still pointed at real pages, and no longer described reality. Nothing in the output marked the problem.
 💡 Retrieval systems fail the same way scrapers do — with a 200 and a straight face
 This is the guide's oldest principle wearing new clothes. A scraper whose selector breaks returns a clean row with an empty field. A retrieval index whose source moved on returns a fluent answer with a valid citation. In both cases the failure is silent, the artefact looks correct, and every check that inspects form rather than freshness passes.What a citation actually asserts. Provenance, not currency. It says a document at that URL contained this claim at the moment it was indexed. It says nothing at all about whether the document still says it. Those are different guarantees, and RAG systems routinely present the first as if it were the second.The fix is the fix you already know. Store fetch time alongside the text, treat staleness as a first-class field, and alert when the gap between index time and now exceeds what the source's own change rate justifies. An unchanged index over a changing source is the same alert as an unchanged file from a live feed — which is to say, a loud one.
 
 +
 Aug 2026 / Provenance
 Your Agent Found the Answer. The Evidence Did Not Come With It.
 An AI research tool hands you a conclusion. The pages it retrieved, the passages it used, the timestamps, the tool calls it made — usually none of that travels with the answer. Which means you cannot audit it, cannot re-run it, and cannot hand it to a colleague.
 💡 This guide's provenance rule, applied to the research session rather than the row
 The parallel is exact and worth making explicit. The guide argues that a row without source URL, fetch time, status and extractor version cannot be diagnosed — you can only re-crawl and hope. An agent's answer without its retrieval trail is the same object: a value with no lineage, which you must either trust entirely or discard entirely. The intermediate state, checking it, is unavailable.The fields named for a portable session map almost one to one onto the row-level list: the source URL, the raw capture, the timestamp, the model and prompt version, the extracted fields, and the sequence of steps that produced them. The two additions over the row-level case are the interesting ones — model and prompt version, because the same question asked next month against a different checkpoint is a different experiment, and the step sequence, because with an agent the path is part of the result in a way it never is for a deterministic extractor.And a cost note from the same source that pairs with it. Prompt caching is an extraction-cost lever, not just a latency one: keep instructions and schemas as a stable prefix and put the changing content at the end, so repeated work hits cache rather than re-billing the same tokens. That is the same shape as the agent-cost finding elsewhere in this feed — the spend is decided by how you arrange the context, not by which model reads it.
 
 +
 Aug 2026 / New attack class
 The Page Says One Word. The Font Draws Another. Your Scraper Takes the Wrong One.
 Every defence in this guide so far tries to stop you reading a page. This one lets you read it and hands you different words. A build step swaps roughly a quarter of the words in the source for grammatically identical substitutes, and a custom font quietly draws the originals back for the human. You get clean HTTP 200s, complete rows, plausible sentences, and a quarter of the nouns are wrong.
 💡 The first anti-bot measure that gets cheaper the better your parser is
 The mechanism, and it is elegant. OpenType has always allowed a font to draw something other than what the code says — that is what a ligature is, merging f and i into a single glyph. ShieldFont takes those same GSUB glyph-substitution rules and applies them to whole words. At build time, on the author's own server, each chosen word in the source is exchanged for a different word of the same part of speech and roughly the same frequency. The font then substitutes the original back at render time. A person in a browser reads exactly what was written. A client pulling HTML gets different words in the same grammar.Why the substitution is grammatical rather than random. Garbage would be caught. The design goal is text that reads as unusual but sensible, so nothing downstream trips. At about 25% of words swapped, testing reported meaning failure in 55.8% of news passages and around half of general web text. Built by Isaque Seneda and Gabriel Abrucio with the type foundry Playtype, running since October 2025.The number that reframes it, from the creators' own testing. Measured against FineWeb-Edu — a quality filter used to build a major public training dataset — over 90% of shielded pages get rejected outright, and the minority that survive the filter carry false meaning in roughly 19.4% of their content. That is a different and more interesting claim than “it poisons scrapers”. The primary effect is exclusion: most shielded text never makes it into the corpus at all, because a filter tuned for quality reads slightly-wrong prose as low quality and drops it. The poisoning is what happens to the remainder. For a publisher wanting out of training data that is close to an ideal outcome. For anyone collecting text, it means the damage is concentrated in exactly the pages that passed your quality checks.What it inverts. This guide spends a lot of words arguing that a real browser is expensive and you should reach for HTTP when the data is in the HTML. Against this technique that advice is exactly backwards: a headless browser that actually renders the font reads the page correctly, and so do OCR and vision-language models working from a screenshot. Frequency analysis over a large corpus also unpicks it, and anyone holding the font file can simply reverse the mapping. So it does not stop a determined collector — it taxes the cheap path and leaves the expensive one intact, which is a deliberate and rather sharp choice of who to inconvenience.The costs the defender eats are worth knowing, because they bound how far this can spread. Search engines index the decoy text. Screen readers hit the protected regions, which is a real accessibility harm rather than a footnote. RSS feeds leak the original text unencrypted. Any of those may matter more to a publisher than the scraping does.What it means for your pipeline. Pair this with the plausible-wrong card above and the conclusion is uncomfortable: a value can now be wrong on purpose, in grammar, at scale. Row counts, schema checks and completeness validation all pass. The detections that survive are semantic — does this text agree with the rendered version, does this field agree with a second source, does the vocabulary of this page drift from its own history. If you collect text for a model rather than numbers for a table, this is the threat model to hold, and the cheapest insurance is spot-rendering a sample of pages you already fetched over HTTP and diffing the words.
 
 +
 Aug 2026 / Verification
 “One of the Worst Things a Web Scraper Can Do Is Succeed”
 The sharpest statement yet of the failure this guide keeps circling. A retailer changes a product page and the scraper starts reading the monthly financing payment as the full price. Every request succeeds. The row count is normal. The schema passes. The number is even plausible. And it is now sitting in an investment model.
 💡 A schema check cannot catch a value that is the right type, the right range, and wrong
 Why this is worse than the silent 200. An empty container is at least detectable: count the rows, compare against the last good run, alert. But £49 where £1,176 belongs passes every structural test ever written. It is a number, it is positive, it is in a currency, it is within the range prices occupy. Completeness validation says fine. Schema validation says fine. Only something that knows what a price for this product should roughly be can object.The technique named for it is a two-stage check: deterministic schema and completeness validation first, then an LLM evaluating suspicious values — the author's term is “sus vals” — on whatever survives. That is a defensible use of a model in the pipeline, and notably the opposite of the pattern this guide warns about. The model is not doing the extraction, where it would be a per-request bill forever and a new source of quiet error. It is doing adjudication on a small suspicious subset, which is cheap, and where being occasionally wrong costs you a false alarm rather than a corrupted row.The operational half. The claim attached is that over thirty days more than 99.7% of delivered runs passed those checks, with self-healing agents handling most failures automatically. Treat the number as a vendor's own, per the benchmark card above — but the definition underneath it is the transferable part: a run that fetched everything and delivered a wrong number does not count as delivered. Most teams have no such definition, which is why they do not have the metric either.
 
 +
 Aug 2026 / Verification
 "Twelve Requests, Twelve 200s, Zero Rows" — and the Acceptance Test That Catches It
 Two practitioners posted the same failure independently this week, which is usually a sign a thing is common rather than unlucky. One ran twelve requests, got 200 on every one and zero rows of data. The other put it as a rule: a scraper can return 200 and still have collected the wrong page.
 💡 The acceptance test matters more than the library
 The guide has argued the silent-200 case for a while. What is new here is a concrete acceptance test, and it is the most directly useful thing in this update if you are having an agent build scrapers for you.The ordered procedure, worth giving to a model verbatim: start with plain HTTP; check for the fields and the minimum record count you asked for; move to a browser-like client only when the response proves a transport mismatch; launch a full browser only when JavaScript is what creates the data; and stop on login, CAPTCHA, explicit denial or unclear permission.The formulation that makes it work is the acceptance criterion itself: "twenty records, four required fields, same schema on a second run" gives a model something it can actually prove. "The request worked" does not. That single sentence fixes the model-authored-oracle problem described in the agent-built-scrapers section above, because it replaces a check the model can satisfy by agreeing with itself with one it has to demonstrate against reality — and the "second run" clause quietly catches the case where a page happened to render once.A related habit worth stealing, from a third post. An agent hit a third-party upload form stuck on "Uploading…". Rather than retrying a fourth time, it opened the browser's network trace: the request for a presigned upload URL returned 200, and the follow-up PUT to storage never fired — a client-side bug in the site's own widget, not a network or server fault. It then grepped the captured requests across all three attempts to confirm the same pattern, and afterwards verified the outcome on the site's own confirmation page rather than trusting the optimistic "done" state in the UI. Read the trace before you retry, and verify against the source of truth rather than the interface is ordinary engineering discipline; the notable part is that it is now something you can expect from an agent mid-task, and something worth explicitly instructing when you do not get it.One boundary from the same author, useful if you are choosing where to put this logic: a skill teaches a model how to build and verify a scraper; an MCP server is for when you need it to call a running tool on demand. They are not competing, and reaching for the second when you needed the first is a common and expensive mix-up.

---

## MOBILE API INTERCEPTION

15 Mobile API Scraping
Intercept mobile app trafficbefore it hits any anti-bot
Mobile APIs serve the same data as the web, but with weaker protection. No Cloudflare, no JA4 fingerprinting. Intercept the traffic once, replicate the call forever.

Why mobile APIs? The same data served to a mobile app often sits behind a simpler auth layer than the web. No browser fingerprinting, just a clean JSON endpoint you can call directly from Python.

 
 01
 
 Install Android Studio + create a Virtual Device
 Open Android Studio → Virtual Device Manager → Create Device. Pick any phone that shows the Play Store icon. For the system image, choose any API level above 28do not choose Android 9 (Pie / API 28), the rooting script does not support it. API 30 (Android 11) is a safe default.
 💡 Any Android 10+ image works. Start the AVD and confirm it boots before proceeding.
 
 

 
 02
 
 Root the AVD using rootAVD
 AVDs are not rooted by default. Root access lets HTTP Toolkit intercept SSL traffic. The rootAVD script handles everything in one command.
 git clone https://github.com/newbit1/rootAVD.git
cd rootAVD

# Verify AVD is accessible
adb shell

# List your AVDs
./rootAVD.sh ListAllAVDs

# Copy the first command from the output and run it
# e.g: ./rootAVD.sh system-images/android-30/google_apis_playstore/x86_64/ramdisk.img
 💡 adb not found? Add to ~/.zshrc: alias adb='/Users/$USER/Library/Android/sdk/platform-tools/adb'
 
 

 
 03
 
 Confirm root, Magisk appears in the app drawer
 After rootAVD finishes the AVD reboots automatically. Once it's back up, open the app drawer and look for the Magisk app, this confirms root is working. Zygisk does not need to be enabled.
 💡 If the AVD didn't reboot itself, reboot it manually. No Magisk = root failed, re-run the script.
 
 

 
 04
 
 Install HTTP Toolkit and connect via ADB
 Download from httptoolkit.com or install via Homebrew. Open it → Intercept tab → "Android device via ADB". HTTP Toolkit detects your running AVD and prompts it to grant superuser rights, grant it.
 # macOS
brew install --cask http-toolkit
 💡 "System trust disabled" warning? Disconnect and reconnect in HTTP Toolkit, or reboot the AVD.
 
 

 
 05
 
 Install the target app and capture its requests
 Sign into Google Play on the AVD and install the app, or download the APK from apk.support and drag-drop it onto the emulator. Open the app, navigate through it (lists, detail pages, search) while HTTP Toolkit runs. Switch to the View tabevery request the app makes is captured in real time.
 💡 Use the filter bar, you'll see 400+ requests but only ~10 are the data endpoints. Filter by the target domain name.
 
 

 
 06
 
 Replicate the API call in Python
 Click any intercepted request to see full headers, auth tokens, and query parameters. Test in Postman first to confirm it returns data, then replicate in Python. Mobile APIs return clean JSON, no HTML parsing needed.
 import curl_cffi.requests as requests

resp = requests.get(
 "https://api.targetapp.com/v2/listings",
 headers={
 "Authorization": "Bearer <token_from_http_toolkit>",
 "X-App-Version": "4.2.1",
 "User-Agent": "TargetApp/4.2.1 (Android 11; SDK 30)",
 "Accept": "application/json",
 },
 impersonate="chrome120"
)
data = resp.json()
 💡 Tokens expire, check if the app refreshes on login and build a token refresh step into your scraper.
 
 

 
 ✓ Works well for
 
 Property portals, classifieds, marketplaces
 Apps where the web version is heavily protected
 Data only available in the mobile app
 Targets using simple Bearer token auth
 Any app that doesn't pin SSL certificates
 
 
 
 ✗ Limitations
 
 Apps with SSL pinning block interception
 Some apps crash on rooted devices
 ARM-only apps may not run on x86 emulators
 Tokens expire, need refresh logic in scraper
 App updates can silently change endpoints
 
 
 
 ↑ SSL Pinning bypass
 If the app blocks interception it likely uses SSL pinning. Use Frida or objection to bypass it at runtime, or use Burp Suite with the Xposed + TrustMeAlready module for a more permanent bypass.
 

 Stack
 Android Studio
 rootAVD (github.com/newbit1/rootAVD)
 Magisk
 HTTP Toolkit
 apk.support
 Postman
 curl_cffi

When the signature lives in native code#

Interception gets you the requests. On a hardened app it does not get you the signatures. Replay a captured call against an app like Shopee and the backend answers with an error, because every request carries anti-fraud headers built by code you cannot read in JADX. The signing method is there in name only, declared native, with its body on the far side in compiled ARM inside a .so library. This is where most people give up on the mobile route. It is also where the mobile route gets genuinely durable, because once you reproduce the signing you are no longer tied to a running app at all.

The decision that shapes everything: can you rebuild the signer, or do you have to borrow it? Read the native library and you land in one of two cases. If it is readable crypto, you reverse it and reimplement it in Python, then sign offline at any volume with no app in the loop. If it is a bytecode virtual machine you cannot practically rewrite, you keep the app running and drive its own signer as an oracle your scraper calls. Both beat the amateur route of a rooted phone you have to babysit, because a signer or an oracle runs on a server inside your scraper. The first is cleaner; the second always works. You pick based on what the library actually is, which means you have to open it first.

 
 
 Four tools, one job each
 The native-reversing toolchain
 
 androguard is fast static recon. It lists the .so libraries an app ships and finds which classes declare native methods. Structure you can script, not readable source.
 JADX decompiles Dalvik back to Java. It is how you read the managed side and find the exact class and method that crosses into native code. It stops at the native keyword, the handoff point.
 Ghidra is the NSA's open framework. It disassembles a .so and decompiles it to pseudo C. It is the only tool here that reads native code, so the work centers on it. Run it headless so the workflow scripts cleanly and repeats exactly.
 Frida injects a JS engine into the running process so you can hook and call functions live, and confirm your static reading against what the app actually does.
 
 
 
 
 Two tells worth knowing before you start
 Reading the library
 
 Model the layers first. Managed Java and Kotlin builds the request and calls into native methods. A set of .so libraries does the sensitive work. Interceptors attach the anti-fraud headers. Name the layers before you open anything, so you target the one library that matters instead of all of them.
 A small library is a good sign. A signing lib of a few hundred kilobytes has little room for a heavy obfuscator, and auto analysis that finishes fast with zero decompile failures tells you the binary is not packed or virtualised. C++ symbols that survived give you the function names for free.
 Some values are not in the file. A fixed AES IV can live in .bss, which is zero-filled on disk and only set at runtime. Hash, HMAC, and Base64 modes do not care because their output is fully determined by input and key. The AES modes do, so you read that one value from the live process once and bake it in.
 
 

The payoff scales past the one app. Most apps that protect their API at all push the work into a native library, and a large share of those turn out to be plain, readable crypto you can rebuild byte for byte. Work it out once and it is the same move on the next app you open. Pair this with an agent that can drive the disassembler and you compress days of manual tracing into a guided loop, which is the next section's territory.

 2 field notes on mobile
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Peer-reviewed
 The Localhost Bridge: A Decade-Old Hole Between Web and Mobile, Fixed This Year
 "Bridges to Self: Silent Web-to-App Tracking on Mobile via Localhost" took both the Distinguished Paper Award and the Internet Defense Prize at USENIX Security 2026, a month after the CNIL-Inria Privacy Protection Prize. The mechanism sat in the gap between the web platform's security model and the mobile platform's, went unaddressed for years, and was being actively exploited at scale.
 💡 A native app listening on localhost turns any web page into a channel into that app
 The shape of it: an installed native app opens a listener on localhost, and a script on an ordinary web page in the mobile browser can reach it. Neither platform's model is broken on its own terms. The browser is doing what it does with a local address, and the app is doing what any app may do with a socket. Between them, the browser's per-origin isolation is bypassed entirely, and a web session can be joined to a device-level app identity with no permission prompt and nothing visible to the user — which is exactly the linkage both platforms are supposed to prevent.The outcome is the part worth noting, because security research often stops at the finding. This one produced concrete change: browser mitigations, a new Android permission, and regulatory attention. The authors are explicit that the changes mattered more than the awards.Two reasons it belongs in a scraping guide. First, it is the cleanest recent example of a class this guide keeps returning to: the highest-value signals are not in any one layer's threat model but in the seam between two layers, where each component is behaving correctly and the combination leaks. TLS versus user agent, JA4 versus the JavaScript that runs after it, an app's signing logic versus the OS it trusts — same shape. Second, if you do mobile work, a localhost listener is now a permissioned, mitigated surface rather than a free one, so anything you built that leaned on app-to-web bridging should be re-tested rather than assumed. USENIX Security 2026
 
 +
 Learning / Mobile API reversing
 The Mobile-API Reversing Toolchain: Frida + JADX + Ghidra
 Your guide's #1 rule is "find the mobile API first", apps often hit the same backend with zero anti-bot. But intercepting a modern app means defeating its own protections (cert pinning, signed headers). The toolchain: Frida to hook and modify native Java/C functions at runtime, JADX to decompile the APK back to Java, Ghidra to decompile the native C, on a rooted Android emulator.
 💡 MobileHackingLab ships a free Frida course with certificate
 This is the practical skill that makes "scrape the mobile API" actually work in 2026, when apps defend their endpoints. The workflow: (1) JADX decompiles the APK to readable Java so you can find where the signed header or auth token is generated. (2) If that logic is in a native .so library (common for the sensitive bits), Ghidra decompiles the C/C++ to understand the algorithm. (3) Frida hooks those functions at runtime via injected JavaScript, so you can log the inputs/outputs, bypass certificate pinning, or call the signing function directly to mint valid headers, no need to fully reverse the algorithm if you can just invoke it. (4) Run all of this on a rooted Android emulator from Android Studio for a controlled, disposable lab. Pair with HTTPToolkit or mitmproxy to capture the now-decrypted traffic and recover the API contract. MobileHackingLab offers a free Android Frida course with a certificate and CTF-style challenges, the fastest way in if the toolchain feels intimidating. The payoff: once you can mint the app's signed headers, you call its clean JSON API directly and skip every browser-layer anti-bot entirely. MobileHackingLab free Frida course

---

## LEGAL AND ETHICS — Access Tiers, Bot Identity, Responsible Scraping

★ Read this first, legal & ethics
The part most guidesconveniently skip
Bypassing detection is only half the question. The other half is whether you should, whether it is legal where you operate, and whether your approach survives contact with reality. This section comes first on purpose. It is deliberately honest about the limits of everything that follows.

This is not legal advice, but you need to think about these#

 
 
 Legal exposure
 Terms of Service & CFAA-style law
 Scraping publicly available data has been broadly upheld in some jurisdictions (e.g. the US hiQ v LinkedIn line of cases), but violating a site's Terms of Service, bypassing an authentication wall, or accessing non-public data can expose you to breach-of-contract or computer-misuse claims (CFAA in the US, the Computer Misuse Act in the UK, and equivalents elsewhere). "Technically possible" and "legally safe" are different questions. Logging in and then scraping is a materially different legal posture than scraping anonymous public pages.
 
 
 
 Privacy law
 GDPR, CCPA & personal data
 If your scrape collects personal data of EU/UK residents, GDPR applies regardless of where you operate, and you likely become a data controller with obligations (lawful basis, retention limits, subject rights). The same logic applies to CCPA in California and a growing list of regional laws. Scraping names, profiles, reviews, or contact details is not the same as scraping product prices. Treat personal data as radioactive unless you have a clear lawful basis.
 
 
 
 Ethics & sustainability
 robots.txt, rate limits & not being a jerk
 robots.txt is not legally binding in most places, but ignoring it is a signal of intent and increasingly cited in disputes. Beyond law: hammering a small site with thousands of requests can degrade service for real users and is the fastest way to get your IP ranges burned across an entire provider. Pace your requests, cache aggressively, scrape during off-peak hours, and take only what you need. Sustainable scraping is slow scraping.
 
 
 
 Jurisdiction
 Where you are matters
 The legality of an identical scrape can differ between the country you operate from, the country the target is hosted in, and the country whose residents' data you collect. A technique that is low-risk in one jurisdiction can carry real exposure in another. If you are operating commercially or at scale, this is a question for an actual lawyer in the relevant jurisdictions, not a guide on the internet.
 

The case law: who actually won#

Most of what people believe about scraping law is folklore. Below are the decisions that set the line, with the winner stated plainly. The wins for data collectors are real and substantial, but narrower than the headlines suggest, and the losses are just as instructive. These are almost entirely United States rulings, several are district-court decisions that can be and sometimes are reversed, and none of them speak to what you do with the data afterwards.

Wins for data collectors

 
 
 Scraper won
 Ninth Circuit · 2019, reaffirmed 2022
 hiQ Labs v. LinkedIn
 Public pages have no lock to pick
 Winner: hiQ Labs
 LinkedIn sent a cease and desist over scraping of public profiles, and hiQ sued. The Ninth Circuit held that scraping publicly accessible data likely does not violate the Computer Fraud and Abuse Act, because a page open to anyone has no authorisation gate to breach. It reaffirmed this in April 2022 after the Supreme Court sent the case back in light of Van Buren. This is the most-cited authority in the field, and every later win leans on it.
 

 
 
 Defendant won
 US Supreme Court · 2021
 Van Buren v. United States
 Breaking a rule is not breaking in
 Winner: Nathan Van Buren
 Not a scraping case, but the one that reshaped the ground under all of them. The Court held that "exceeds authorized access" reaches only information in areas of a system that are off limits to you, not information you may lawfully reach but use for a purpose the owner dislikes. So violating a site’s terms is not, by itself, a federal crime, and CFAA exposure turns on whether you bypassed a real technological gate such as a login.
 

 
 
 Scraper won
 N.D. California · January 2024
 Meta v. Bright Data
 Logged out is the whole ballgame
 Winner: Bright Data
 Meta sued over the scraping of public Facebook and Instagram pages and lost the core of its case. The court dismissed the CFAA claim and the parallel California computer-fraud claim outright. The breach-of-contract claim survived only for the window in which Bright Data actually held an account relationship with Meta. The rule that fell out of it is worth memorising: a platform’s terms bind its account holders, not logged-out visitors.
 

 
 
 Scraper won
 N.D. California · May 2024
 X Corp. v. Bright Data
 You cannot fence off what you do not own
 Winner: Bright Data
 Judge Alsup dismissed every claim X brought: breach of contract, tortious interference, unjust enrichment, trespass to chattels and unfair competition. Two strands matter. X’s users, not X, hold the rights in the content, and X holds only a non-exclusive licence, so it cannot use its terms to exclude everyone else from data it does not own. And those state-law claims were preempted by the Copyright Act. The court warned that letting platforms gate public data tends toward "information monopolies".
 

 
 
 Researcher won
 N.D. California · March 2024, on appeal
 X Corp. v. CCDH
 Research survives a hostile platform
 Winner: Center for Countering Digital Hate
 X sued a non-profit research organisation over scraping of public posts that fed critical reports about the platform. The contract and CFAA claims were dismissed, though the decision is on appeal to the Ninth Circuit. Read with Sandvig v. Barr (D.D.C. 2020), which held that breaching terms of service does not turn academic and journalistic auditing into a federal crime, courts have been notably protective of public-interest research.
 

 
 
 Live · round two
 N.D. California · dismissed July 2026, amended August 2026
 Google v. SerpApi
 The first serious attempt to make anti-bot a copyright question
 Status: unresolved, and the one to watch
 Every case above turns on access: was the scraping authorised. This one tries a different door, and that is why it matters more than its size suggests. Google's theory is that its anti-bot system is a technological protection measure, so getting past it is DMCA circumvention rather than unauthorised access.Round one. On 20 July 2026 the court granted SerpApi's motion to dismiss. Claims resting on search results containing no copyrighted content were thrown out with no leave to refile, on the straightforward reasoning that the DMCA does not protect material that is not copyrighted. The court left a narrow opening: 21 days to amend as to results carrying a copyrighted component, such as the snippets in Knowledge Panels.Round two. Google refiled on the final day, 10 August 2026, with a 15-page amended complaint built around the single element the court had found missing, written permission from the copyright owners, and naming Reddit as the partner that asked it to block scrapers. The question has therefore moved from whether scraping public pages is lawful to whether the platform holds licences that make the content protectable in the first place. That is a licensing-chain argument, not an access one, and nothing in hiQ or Van Buren answers it.Why a scraper should care more about this than about the CFAA rulings. If the circumvention theory ever lands, the legal question stops being "were you authorised" and becomes "did you bypass a protection measure", which is a far worse question to be asked, because bypassing is the thing you demonstrably did. The counterweight is that Google has to characterise its own results page as full of protectable copyrighted work, which is the opposite of what it has argued for twenty years about everyone else's content, and any win becomes precedent pointed back at it. Treat this as unsettled; it is the only live case on this page.
 

 
 
 Upstream of you
 Stanford HAI · FAccT 2026 paper and policy brief
 Data brokers and the training-data supply chain
 The compliance question nobody can answer
 Status: measured, and largely unenforced
 Everything else on this page asks whether you may collect. This asks about the market that already did. Stanford researchers examined data-broker compliance with California's privacy law and found it widely lacking, and the finding that should stop a scraper mid-sentence is this: 32 registered brokers sell data to generative AI developers, and we do not know which ones, or why. Registration exists; disclosure does not.Why it belongs beside the case law rather than in a privacy footnote. Google v. SerpApi turns on whether a platform holds licences that make content protectable, which is a question about a chain of permissions. This is the same chain one layer up and considerably murkier: models are trained on brokered data whose provenance and consent basis cannot be inspected from outside, sold by companies not meeting the obligations they already have. If you scrape public pages and keep provenance on what you collected, you are on firmer ground than a good deal of the licensed market. That is genuinely useful to know and not a licence to relax, because enforcement attention that arrives for brokers historically arrives for everyone nearby. Practical version: keep provenance on your rows, know your legal basis for any personal data, and never assume "we bought it" is a stronger position than "we collected it openly".
 

 
 
 Scrapers won
 The claim that keeps failing
 Trespass to chattels
 Politeness is a legal argument
 Winner: Scrapers, repeatedly
 The dot-com era theory from eBay v. Bidder’s Edge (2000), that a scraper trespasses on the server it queries, has become much harder to run. In X v. Bright Data it was dismissed because X never adequately pleaded any damage, with the court noting the access was no more burdensome than a person using a browser. Unusually actionable: a rate-limited crawl that does not degrade the service gives this claim almost nothing to grip. One that hammers the target hands it over.
 

Losses, and the lines you do not want to cross

 
 
 Platform won
 N.D. California · November 2022
 hiQ Labs v. LinkedIn, contract phase
 The same case, the other ending
 Winner: LinkedIn
 The part usually left out of the hiQ story. After winning on the CFAA, hiQ lost on contract: the court found it had breached LinkedIn’s User Agreement, which it had accepted by creating accounts, and the case ended in a consent judgment. hiQ did not survive as a business. The CFAA shields you from hacking claims on public pages. It does not shield you from a contract you agreed to.
 

 
 
 Platform won
 Ninth Circuit · 2016
 Facebook v. Power Ventures
 A cease and desist can revoke permission
 Winner: Facebook
 Power Ventures aggregated users’ social accounts with their consent. Facebook sent a cease and desist and blocked its IPs; Power Ventures kept going. The Ninth Circuit held that once permission is expressly revoked, continued access is "without authorization" under the CFAA, even where the users themselves consented. Still cited today, which is why a cease and desist changes your legal position in a way a robots.txt file does not.
 

 
 
 Split decision
 D. Delaware · 2024 verdict, overturned January 2025
 Ryanair v. Booking.com
 Won the trial, lost the judgment
 Winner: Booking.com on the verdict, Ryanair on the reasoning
 A Delaware jury found in 2024 that Booking.com violated the CFAA by scraping Ryanair, awarding exactly the $5,000 statutory minimum. In January 2025 the court granted Booking.com judgment as a matter of law and overturned that verdict, finding Ryanair had not proved the required $5,000 of qualifying loss, since only its anti-bot spend counted and customer-service costs did not. But the same ruling held the CFAA applies extraterritorially, and that access behind a login after a cease and desist can still violate it. The scraper walked away; the reasoning left real exposure behind.
 

The pattern across all of them. Data collectors win when the data is public, the access is logged out, no cease and desist has landed, and the crawl causes no measurable harm. They lose when they logged in and accepted terms (hiQ), when they kept going after being told to stop (Power Ventures), or when the fight moves to ground the CFAA never covered. What this line of cases does not give you: comfort on personal data, where GDPR, CCPA and their equivalents run on entirely separate logic and have produced real penalties against scraped-face databases; resolution on copyright, where collecting a work and training a model on it are separate questions still being litigated; or any right to be let in, since the same judge who dismissed X’s claims confirmed a platform may use technological self-help to block you. It is also jurisdictional: these are US decisions, several district-level, at least one on appeal, and the EU stacks database rights on top. Treat the pattern as a way to reason about risk, and take a real lawyer for anything commercial.

The line is moving: agents, robots.txt, and the end of a clean binary#

For twenty years the ethical and technical model rested on a tidy split: humans on one side, bots on the other, with robots.txt as the polite fence between them. That split is dissolving, and it changes how you should think about both the ethics and the defences. The scale of the shift is no longer hypothetical: in June 2026 Cloudflare reported that automated traffic had crossed the majority line for the first time in the web's history, at 57.5% of HTTP requests to HTML content against 42.5% from humans, driven mostly by agentic AI. Read the number carefully, it measures crawlable web content rather than every packet, but the direction is unambiguous: the web is now a machine-to-machine environment as much as a human one, which is the whole reason this skill set stopped being niche.There is a second number underneath the first that matters even more for anyone who scrapes. Cloudflare's crawl-to-refer ratio measures how many pages a platform crawls for every visitor it sends back. In 2026 the AI crawlers sit at extraordinary imbalances, with Anthropic's reported in the thousands to tens of thousands of pages crawled per referral and other AI operators in the hundreds to low thousands, against a traditional search engine like Google at roughly five to one. Read these as dated, volatile snapshots, the published figure for a single operator has swung from six figures to low five figures inside a year and moves month to month, so treat any specific ratio as a reading rather than a constant. The structure is what is stable: the dominant readers of the web now consume vastly more than they return, which is exactly why so much of the web is hardening against automated reading at the same time as automated reading becomes the majority of traffic. That tension, more machines reading, more defences raised against them, is the backdrop the rest of this guide operates in.
Start with the uncomfortable truth about robots.txt: it was never a security control. It is a request, and only the bots that choose to listen ever obeyed it. That was a workable social contract when the only things crawling the web were search engines and the occasional scraper. It breaks the moment an autonomous agent with a set of tools and a goal is involved. Give an agent the instruction to gather something and a browser to do it with, and a blocked default user agent does not stop it, it problem-solves around the block, picks a tool that blends in, and keeps going, without ever being told to evade. The obedience was always voluntary, and agents do not share the assumptions that made it hold.

 
 
 The binary is breaking
 "Human or bot" no longer maps cleanly
 When an agent browses on behalf of a real person, which is it? The platforms themselves are blurring the line: browser and automation vendors have started shipping agent modes where navigator.webdriver reports false, the same value a human-operated browser returns. The signal that reliably meant "automated" for a decade is being switched off from inside the platform, not defeated from outside. A defender can no longer treat the presence of automation as proof of bad intent, and a scraper can no longer assume that "looking automated" is what gets it blocked.
 
 
 
 What defence now requires
 Two layers, not one fence
 If you sit on the defending side of this, a single fence is no longer enough. Protecting content from agentic collection now takes two layers working together: traditional anti-automation (fingerprinting, rate limiting, browser challenges) and behavioural controls that reason about intent rather than identity. Either one alone leaves a gap, and an agent with options is built to find gaps. The honest framing is that this is an adversarial pressure problem, not a classification problem with a clean answer.
 

Why this matters for you, on either side. The adversarial dynamic has a cost that lands on real people. When sophisticated operators mimic legitimate browsers well enough to poison a scoring system, the classifier does not just start failing on bots, it starts failing on genuine users too, the Firefox visitor handed an unsolvable puzzle, the real customer who quietly gives up and leaves. The bot did not cost that business the sale, the over-tuned defence did. Whichever side of this you build on, aim for proportionality: collect what you have a defensible reason to collect, defend in a way that does not punish the humans you are trying to serve, and treat the agentic shift as a reason to be more careful, not less.

The legislative response: transparency, not technical blocking#

Because robots.txt was never enforceable, the pressure has moved from the robots.txt file to the statute book and the contract. Two 2026 developments mark the direction. In the United States, New York passed a Stealth Crawler Prohibition Act (through the state Senate and Assembly, awaiting the governor's signature at the time of writing), which targets bots that scrape news content while evading detection. It would make it an offence to damage, impair, or burden the operation of a covered news site, let aggrieved publishers subpoena a service provider to identify an alleged violator, and allow them to seek injunctions and damages. In the United Kingdom, a Private Members' Bill, the Automated Online Software (Access and Transparency) Bill, backed by the News Media Association, takes a deliberately narrower line: it does not try to regulate AI models or dictate behaviour, it requires that a bot accessing a site and taking content disclose who it is and what it will do with what it takes. Private Members' Bills rarely become law without government backing, but they shape the bills that follow.

The throughline matters more than any single bill. The thing being criminalised or regulated is not scraping, it is deception: a bot that hides its identity or its purpose, that impersonates a human or a legitimate crawler (a fake Googlebot), or that retrieves paywalled articles it was never granted. The same Fastly threat report behind the majority-machine number found that a large share of bot traffic is unwanted or unverifiable, and publishers are not waiting for legislation: some have started replacing robots.txt bans with search-only contracts in their terms of service, so they can invoice per article scraped and pursue it as a contract matter rather than fight a long copyright case. For a scraper the practical lesson is concrete and forward-looking: identifying yourself honestly, respecting an explicit licence or paywall, and being able to say what you collect and why is moving from etiquette toward a legal posture. The operators most exposed to the coming rules are precisely the stealth-crawler pattern this guide describes how to detect, not the ones who scrape openly and within terms.

The civil-liberties case against those same bills, in fairness. The publisher framing is not the only one, and the counter-argument from digital-rights groups (the EFF prominent among them) is worth stating plainly because it is substantive. Their point is that "stealth crawler" is a loaded name for something ordinary and often valuable: an automated tool that collects public web data without disclosing who is behind it. Anonymous crawling has powered real accountability journalism, investigations into search-ranking and pricing practices that identified themselves as ordinary browsers precisely so they could see what a normal user sees, along with academic research, security monitoring, and privacy tools that audit sites for trackers. The worry is that a bill requiring every crawler to reveal its operator and every future use of the data hands publishers a veto over lawful access, letting them unmask and block not just abusive bots but also researchers, journalists, and critics, with court orders available on no evidence of wrongdoing. And it aims at the wrong target: the genuine harm publishers cite is server strain from over-aggressive crawling, which is a matter of conduct (rate, volume, politeness) that unmasking does nothing to fix. The balanced reading for anyone in this field is that identity and behaviour are different axes: the honest, rate-limited, within-terms crawler described just above is exactly the actor these critics argue should stay free to operate anonymously, and conflating "undisclosed" with "abusive" is the move both the technical detection stack and the proposed laws risk getting wrong.

The three tiers of AI access, and where the pressure actually is#

The publisher-facing side of this has stratified into three layers worth knowing, because they explain who the new rules actually touch. At the top are declared "good bots" that identify themselves and pass through infrastructure gatekeepers (Cloudflare, Fastly, Akamai). In the middle are mixed-use crawlers, bots used for both ordinary search and AI training or agentic tasks, historically waved through as "search" because that was their original purpose. At the bottom, and by volume the largest, is the gray scraping economy: traffic that does not declare itself and does not play by robots.txt at all.

Two shifts in 2026 matter for anyone operating here. First, the gatekeepers are moving against the middle tier: from September 2026 Cloudflare's defaults for mixed crawlers are set to allow search but block AI-training and agent use on pages carrying ads, ending the "free pass" that let a search crawler quietly double as a training crawler. Since roughly a fifth of the web sits behind Cloudflare, even leaving those defaults untouched raises the marginal cost of crawling and puts a soft price on access. Second, and more pointed for this guide, defenders have stopped pretending robots.txt is the battleground. One practitioner who reverse-engineers scraping tools estimates that on major news brands, while 20 to 30 percent of traffic is identified crawlers, roughly a quarter of all traffic is stealth crawlers mimicking human users, traffic most publishers never even classify as bots.

The defensive playbook that follows is explicitly an economic one, and it is the mirror image of everything in this guide. Rather than tweaking robots.txt and hoping, the advice to publishers is to turn on the highest-friction defences available, client-side human-check challenges on first page load, systematic blocking of non-essential crawlers, and hard benchmarking of any vendor claiming over 90 percent detection, with the stated aim of making scraping unreliable and expensive enough that the intermediaries are forced into licensing talks. That is the strategic backdrop to the whole detection stack in this guide: the gray economy exists because access is worth more than the friction currently costs, and both sides are now deliberately trying to move that balance. Reading the room, the durable position for a scraper is the same as the legal section's: declared, within-terms access is the tier the rules are being written to protect, and the stealth tier is the one they are being written to squeeze.

The other direction: sites that hand agents a sanctioned door (WebMCP)#

While one half of the web is hardening against undeclared bots, the other half is starting to do the opposite, expose a sanctioned way in for agents. The emerging shape is WebMCP: a small script the site itself ships that registers callable tools on the page, so an agent can search the catalogue, read product details, manage a cart, or start checkout by calling a declared tool instead of scraping the DOM. This is not hypothetical, through mid-2026 Shopify began rolling WebMCP tools onto the storefronts it renders (quietly, no announcement), DoorDash exposed an agent-facing command-line ordering path, and both major assistant vendors moved browsers inside their agents, which pushes the website itself to become part of the agent stack. For anyone doing data work this is a genuinely different acquisition path worth watching: where a sanctioned tool interface exists, it is more stable than any selector, it is explicitly permitted, and it sidesteps the entire detection arms race, because you are being invited through the front door rather than climbing the wall. The catch is coverage and trust, most of the web will not be rebuilt for agents soon, a page-shipped tool can change or vanish without notice, and a tool the site controls can also shape or withhold what it returns, so a declared interface is a gift to take when offered but not something to depend on to the exclusion of the techniques in the rest of this guide. It fits the same three-tier picture: the clean future is declared identity meeting a declared interface, and the gray economy persists wherever that handshake is missing.

The missing half of the handshake: proving who you are#

A declared interface only helps if the declaration means something, and today most of them do not. A request that says it comes from a well-known search crawler is making a claim in a header, and anyone can type that string into an HTTP request, so a User-Agent allowlist is an honour system. The traditional hardening step is to verify by IP (reverse-DNS the address, check it against the operator's published ranges), which is real evidence but is getting harder to rely on as legitimate automation spreads across cloud providers and remote-browser infrastructure, where address ranges shift and intermediaries blur who actually owns the connection. That gap is what Web Bot Auth is designed to close: the automated client cryptographically signs its request, and the site verifies the signature against a key associated with a known operator. The claim stops being a string anyone can copy and becomes something only the key holder can produce, which finally gives an allowlist a foundation worth building on.

The limit is worth stating as clearly as the promise, because it is where policy actually lives. Authentication establishes who sent the request, and nothing more. The publisher still has to decide whether that operator, for that use case, at that rate, is allowed, which is a separate judgment that no signature can make for them. Identity and authorisation are different questions, and conflating them is how you end up with a verified crawler doing something the site never intended to permit. Read alongside the three tiers and the sanctioned-interface direction above, the shape of the near future is fairly clear: crawler policy converges on reliable identity plus explicit access rules, where a signed request proves the operator and a published policy states what that operator may do. For anyone building scrapers, the practical implication is that the declared tier is becoming both easier to join and harder to fake your way into, so if your use case can be done openly, signing your traffic is likely to become the cheapest possible form of access.

Read this before you copy anything from this guide#

Anti-bot systems are probabilistic, not deterministic. Modern systems (Akamai, Cloudflare, DataDome, Kasada, HUMAN) are machine-learning systems running adaptive scoring, per-customer configurations, and continuous experiments. That means: the same fingerprint can pass today and fail tomorrow, two identical sessions can get different treatment, and a technique that worked on one customer of a vendor can fail on the next. Nothing in this guide is a guaranteed bypass. It is a description of what worked, on specific targets, at a specific time.

This advice has a shelf life. The more widely a bypass technique spreads, the faster vendors adapt to it, so the most public techniques are often the first to die. Treat dated case studies (each is timestamped) as a snapshot, not a permanent recipe. The durable value here is the detection theory and the decision process, not any single tool or fingerprint. Understanding why a layer fires lets you adapt when the specific recipe stops working. Copying a fingerprint without understanding it is cargo-cult scraping, and it ages badly.

You probably need less than this guide implies. This guide is optimised for hardened targets (Akamai v3, Kasada, F5 Shape). Most scraping jobs are not that. A huge number of sites still yield to a plain HTTP client with proper headers, session reuse, and sensible pacing, no patched browser, no mobile proxies, no JA4 spoofing. Always start at the cheapest rung of the ladder (the decision flow near the top of this guide) and only escalate when a target actually forces you to. The expensive stealth stack is a last resort, not a default. Your results will also vary enormously by target category: a strategy tuned for ecommerce can fail completely on airline pricing, ticketing, sneaker drops, or social platforms.

 4 field notes on law and ethics
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Law
 A US Court Just Held the DMCA Does Not Protect a Search Result
 In Google LLC v. SerpApi (No. 4:25-cv-10826-YGR, N.D. Cal.), the court in July 2026 dismissed with prejudice every claim resting on search results that do not involve copyrighted works — the links, snippets and factual index data that make up the bulk of a results page. The reasoning: the DMCA's anti-circumvention provisions protect access to copyrighted works, and a factual search result is not one. Google's amended complaint, filed 10 Aug 2026, is DMCA-only: 17 U.S.C. §§ 1201(a), 1201(a)(2) and 1203. No copyright infringement count. No CFAA count. No breach of contract.
 💡 The distinction the whole case now turns on, in seven words
 “An anti-download provision is not an anti-access provision.” That is SerpApi's framing, and it is the crux. §1201(a) prohibits circumventing a measure that controls access to a copyrighted work. A rule that says you may look but not bulk-download is, on this reading, not an access control at all — you were already granted access.What changed between complaints. Google dropped the Shopping and Maps theories entirely, did not try to revive the non-copyrighted-content claims, and now pleads four separate sources of copyright-owner authorisation to deploy its anti-bot measure — an unidentified licensing partner, an unidentified content provider, the Reddit agreement, and one more. That pivot exists because the July order dismissed the earlier version for failing to allege that any copyright owner authorised Google to deploy the protection on their behalf.Why it matters beyond one company. Every anti-bot vendor sells the idea that circumventing their product is itself unlawful. This ruling says that for non-copyrighted material the DMCA does not reach it. Motion to dismiss is Doc. 49, filed 24 Aug 2026; hearing 29 Sept 2026. Not legal advice, and a district-court order binds nobody else — but it is the clearest judicial statement yet on where anti-circumvention stops.
 
 +
 Aug 2026 / Etiquette
 A Code Host Started Blocking Crawlers For Being Wasteful, Not For Crawling
 Codeberg now blocks LLM crawlers that request every revision, every diff, and every duplicate representation of the same file across public repositories. The content was public and the crawling was allowed. What got blocked was the manner of it.
 💡 The baseline moved: courtesy is becoming a condition of access rather than a virtue
 This is the same number from the other side. Elsewhere in this feed, an infrastructure provider reports that more than half the time a well-behaved AI crawler fetches something it already has and which has not changed. A source-control host is where that pathology reaches its worst case: every commit is a URL, every diff is a URL, and the same blob is reachable through several representations, so a naive crawler can generate effectively unbounded requests against a finite amount of actual content.The four practices asked for are the ones a competent collector already wants for its own reasons: conditional requests so unchanged resources cost a 304 rather than a body, incremental crawling against a stored cursor instead of full sweeps, deduplication before fetch where several URLs resolve to one artifact, and skipping unchanged content outright. Every one of those lowers your bill as much as theirs, which is what makes the ask reasonable rather than moralising.The strategic read. Blocking used to be a response to who you were — wrong fingerprint, wrong ASN, no permission. This is a block for how you behaved while doing something permitted, and it is a much harder one to route around, because there is no identity to improve. Combined with the metering and licensing described elsewhere in this feed, the direction is consistent: access is becoming conditional on cost imposed. The cheapest insurance is to be the crawler nobody has a reason to notice.
 
 +
 Aug 2026 / Legal
 Google Refiles Against SerpApi, and Has To Argue the Opposite of Its Own Twenty-Year Position
 The broad version of the claim was thrown out. Google has come back with a narrow one, now leaning on a copyright theory: partners asked it to stop third parties extracting licensed content, so its anti-bot system is protecting copyrighted work. This guide already tracks the case; the refiling is where it gets structurally interesting.
 💡 To win, it must call its own results page a container of copyrighted work needing protection
 The contradiction is the whole story. For two decades the argument for indexing everybody else's content rested on that use being permissible. To make the new claim work, the results page has to be characterised as full of protected material that circumvention harms. A win therefore manufactures precedent that points straight back — which is the thing to watch, because it is the part that outlives the parties.Three observations from the practitioner reaction worth keeping. First, it is suing partly on another company's behalf, while that company litigates separately in New York. Second, the obvious remedy is available and unused: put it behind a login, as every site serious about stopping collection eventually does. It stays open because free and ad-supported is the business — open to humans to sell ads, closed to bots so nobody builds a competing index. Third, and least legal: the asymmetry in costs means a smaller defendant can lose by winning slowly.Why it matters beyond one company. The DMCA-circumvention theory is the live threat to public-data collection, and it is a different animal from the CFAA arguments that keep failing. Access theories ask whether you were authorised. A circumvention theory asks whether you got past a technical measure — which every practice in this guide, by construction, does. The judge killing the broad version and the narrow one still being attempted is the shape to watch: the doctrine gets built out of the narrow attempts. Read alongside the stealth-bot legislation covered elsewhere in this feed — two different routes to the same destination, one through the courts and one through statute.
 
 +
 Reality check · robots.txt
 robots.txt Works Perfectly. On the Bots That Were Going to Comply Anyway.
 A publisher blocked GPTBot, ClaudeBot, and PerplexityBot in robots.txt. Logs confirmed zero traffic from those User-Agents. The scraping continued. It just stopped identifying itself.
 💡 User-Agent is a string the client chooses to send. Polite bots send a real one.
 
A publisher blocked GPTBot, ClaudeBot, and PerplexityBot in robots.txt. Logs confirmed it: zero traffic from any of those User-Agents. They thought they had solved the problem.

What was actually happening: the scraping continued. The traffic that used to say "I am GPTBot" was now saying "I am Chrome 124 on macOS." Same content destinations, same fetch patterns, different label.

User-Agent is a string the client chooses to send. Polite scrapers send a real one. The scrapers you are actually worried about — the ones running at commercial scale on behalf of paying customers — send whatever string gets through.

robots.txt works on:
✓ Academic crawlers (Googlebot, Bingbot, academic research bots)
✓ Large AI labs (OpenAI, Anthropic, Google) that have reputational incentives to comply
✓ Hobbyist scrapers who read the rules and care

robots.txt does not work on:
× Commercial data brokers sending Chrome User-Agents
× Competitive intelligence tools running at scale behind residential proxies
× AI startups that have not publicly announced themselves
× Anyone whose business depends on data you do not want them to have

The implication for anti-bot systems: blocking by User-Agent is the weakest possible signal. Cloudflare, Akamai, and DataDome do not read robots.txt. They score TLS fingerprints, canvas hashes, behavioural timing, and IP reputation because those signals are harder to fake. User-Agent string matching is not a detection layer. It is a flag for voluntary compliance.

For scrapers reading this: if a target blocks GPTBot in robots.txt but has no real anti-bot scoring, the robots.txt is the only gate. Respect it. If they have Akamai or Cloudflare deployed, the robots.txt is decorative. The actual gate is the JA4 hash, the canvas probe, the IP reputation check. That is where this guide comes in.

Via a publisher conversation, May 2026.

---

## AGENTIC BROWSERS — Categories, Token Economics, Prompt-Injection Security

6b The new client
Agentic browsersthe visitor that reads, decides, then clicks
A category that did not exist in a serious way two years ago and now sits on both sides of this guide: it is the thing you might build with, and it is the thing arriving at your target. Worth its own section because the economics, the failure modes and the threat model are all different from headless automation, and most of what is written about it is either marketing or panic.

The one-line definition, because it gets muddled with headless browsing. A headless browser runs a script you wrote; when the UI changes, the script breaks. An agentic browser puts an LLM in the decision layer and runs a loop of perception, planning and action, so it adapts when the UI changes instead of breaking. That is the whole trade: you swap a deterministic thing that fails loudly for a probabilistic thing that fails quietly, and you pay per decision.

Four categories, and they are not competing with each other#
People argue about these as if one wins. They serve genuinely different jobs, and the distinction that matters is who the browser is for and whose session it uses.

 
 1 · AI-native consumer browsers
 A person is still in the loop
 ChatGPT Atlas, Perplexity Comet, Opera's AI browser, The Browser Company's Dia, and Chrome itself now that Auto Browse handles multi-step tasks from natural language, with Gemini Nano on-device for low-latency work and a persistent preference layer across the user's Google account. These are full browsers built around a model as the primary interface. They drive the lower layers on behalf of a real user, which is a large part of why browser vendors quietly stopped enforcing automation-transparency flags.
 

 
 2 · Agent-first engines
 No tabs, no address bar, no human
 Cloudflare Kitesurf, Moli, Obscura, Lightpanda. Built on the premise that when the consumer is a model, most of the rendering stack is dead weight: it wants structured content, a small token count and a cheap session it can throw away. Kitesurf is Blitz rendering plus Firefox's Stylo plus the Boa JS engine, shipped in twelve weeks on Workers. Moli is a Rust kernel over libcurl, html5ever, V8, Stylo, Taffy and Parley. Both keep CDP so existing Playwright and Puppeteer code connects unchanged.
 

 
 3 · Local, on your own session
 The blocking problem solved by not leaving home
 BrowserOS neo and its category. Runs on the user's machine, imports their real Chrome sessions, and shows the work: a live cockpit, session replay as scrubbable video, parallel agents in isolated tabs. No proxy, no reused-IP reputation, no credential handed to a third party. Correct architecture for authorised, logged-in, user-owned work, and the wrong one for public collection at volume, because it is one machine on one residential IP.
 

 
 4 · Frameworks that drive a browser
 Where most engineering actually happens
 Browser Use, Hermes, Vercel's agent-browser, Libretto's browser tools, stealth-browser-mcp. These are not browsers, they are the tool surface between a model and one. That makes them the least glamorous category and the one where the cost is decided, as the numbers below show. The trend across all of them is identical: away from one tool per action, toward the agent writing a script.
 

The cost is the tool surface, not the model#
This is now the best-evidenced claim in agentic browsing, with five independent measurements from five teams using five methods. It is also the one most likely to save you money this week, because on most frameworks it is a configuration change rather than a rewrite.

Where the tokens actually go. An MCP tool surface ships its JSON Schema definitions into context on every session, whether you call the tools or not: Playwright MCP costs roughly 13,700 tokens before you have done anything, Chrome DevTools MCP roughly 17,000. A CLI-shaped surface costs zero, because there is nothing to declare. Then it compounds per step. A single page snapshot: ~1,000 tokens against ~15,000. A ten-step automation flow: ~7,000 tokens against ~50,000 for Chrome DevTools MCP and ~114,000 for Playwright MCP. That is a sixteen-fold spread on the same work, decided entirely by the shape of the interface.

 
 
 Vercel · agent-browser
 17 tools → 2
 3.5× faster, 37% fewer tokens — and the number people skip: success rate went from 80% to 100%. Fewer tools did not merely cost less, it made the agent more accurate. Rust CLI for sub-millisecond parsing, a Node daemon holding a warm browser, CDP underneath, and compact accessibility-tree snapshots at 200 to 400 tokens.
 
 
 
 Hermes · browser-use CLI 3.0
 Twelve tools → one script
 Measured by Nous Research at 48 to 66% fewer tokens with no drop in accuracy. The diagnosis was that a dozen tool schemas rode along in every request whether used or not, and opening a page to read one headline cost four or five round trips.
 
 
 
 Libretto · browser tools
 ~70% fewer tokens
 Across 26 tasks on a local desktop Chrome, ~61% cheaper on one agent and 54%/38% on another. Same model, same browser, only the tool surface changed. Stock tools were chatty: large page payloads dumped into context, a wide tool menu, the model re-reading what it already had.
 
 
 
 Evinced · accessibility tree
 ~32× price-performance
 5.4× faster and 3.2× cheaper across 300 tasks on 136 live sites, combining to roughly 32× because the gains correlate. Operates known UI patterns as atomic actions with no model round trip at all, and caches learned flows against roles and accessible names rather than selectors.
 
 
 
 Browser Use · BrowserCode
 Screenshots are out of favour
 Strong models rarely want screenshots and prefer inspecting the page through raw CDP, writing their own low-level code, finding internal APIs and taking JavaScript shortcuts. The exception is website QA, where an agent constrained to behave like a human is still the better instrument.
 
 
 
 Moli · engine layer
 73 MiB vs 773 MiB
 A tenth of headless Chrome's memory at level success on a 192-URL crawl (53.6% against 52.6%), with CDP ready in 34.85ms against 169.37ms. Layout and paint are off by default and only happen behind a flag. Note that half that crawl failed for both engines: the engine was never what decided whether you get in.
 

The single sentence to take from all of it. The expensive thing in agentic browsing is how often the model has to look and how many schemas it carries while looking — not how well it thinks. Every team that fixed it made the same move, from one tool per action to one script per task. And the Vercel accuracy result suggests the reason is not just economy: a smaller tool surface leaves the model less to be wrong about.

The security problem is architectural, not a bug queue#

At Black Hat USA 2026, Brave's Artem Chaikin presented "Attacking and Defending AI Browsers" and tested Comet, ChatGPT Atlas and Opera's AI browser against indirect prompt injection. None of them held. No malware, no exploit chain, just words on a page the model decided to trust. The same class has separately been reproduced against MultiOn, Browser Use and OpenAI Operator, so this is not three unlucky products. The cause is that these agents feed raw page content into the model with no separation between the user's instructions and the page's content, which is not a defect you can patch out of an architecture built to follow instructions from whatever it reads. We spent a decade teaching browsers to distrust the page. Agentic browsers handed that trust back by default.

 
 The WebMCP chain
 Injection becomes authenticated action
 A page can declare named tools with typed inputs that an in-browser agent invokes inside the user's authenticated session. So the attack stops being informational and becomes transactional: plant hidden instructions in content the agent will read, the agent treats them as a user directive, and it calls your tools with attacker-controlled parameters carrying the user's cookies, CSRF token and permissions.The worked example is a line of hidden text in a product review saying to call updateEmail with the attacker's address. And it chains: read cart, apply coupon, change address, check out. Named vectors to worry about are anything the agent ingests without you writing it — reviews, emails, fetched pages, PDFs, support tickets.
 

 
 HTML-in-Canvas
 Why text sanitisation is not enough
 The vector that defeats the obvious mitigation. Fraudulent instructions are rendered inside a canvas element, so they never exist as text in the DOM at all — and a vision-language agent reads them as page content anyway.That matters because the first thing most teams reach for is a filter that strips known injection phrases from the text before it reaches the model. Against a canvas payload there is no text to strip. Any defence that operates on the DOM string is structurally blind to it, which is the clearest argument going for putting the control on the action rather than on the input.
 

 Mitigation, in the order worth doing it
 
 Assume the bad text got through. This is the premise, not a step. Every control below is designed to hold when the injection succeeded, because prompt filtering is a speed bump and the canvas vector shows why.
 1. Register fewer tools. Expose only what an agent genuinely needs and delete the discretionary ones. This is also the change that makes the agent cheaper and, per Vercel's numbers, more accurate. Rare alignment between security and performance, so take it.
 2. Put consequential actions out of band. The good formulation: make it something the agent narrates but cannot click. A human confirmation the model has no tool to satisfy is the only control an injection cannot argue its way past.
 3. Re-validate server-side on every call. Permissions and ownership, every time, treating each request as potentially hostile. The agent's session being valid tells you nothing about whether this particular call was the user's idea.
 4. Scope the session. Narrow the agent's access to what the task needs, with short-lived tokens rather than the user's full standing authority.
 5. Rate-limit and log per tool. This one is for afterwards. It will not stop the first incident, it is how you find out it happened and what it touched.
 And the question to sit with if you run agents: would you let one browse arbitrary pages using the same account that holds your production access? Most teams answer no immediately, and then discover their agent does exactly that.
 

The operational tax nobody scopes#
Agentic browsing demos beautifully and degrades quietly, which is the worst combination for a project plan. Three failure modes that show up after the pilot.

 
 
 The one that gets projects cancelled
 Silent task degradation
 Task completion rates decline within 60 to 90 days of deployment where teams skipped the audit and observability work. Nothing throws. The agent keeps reporting success on a task it is now doing worse, which is the same shape as the coverage-without-a-baseline problem in the post-extraction section: you cannot notice a decline you never measured a starting point for.
 
 
 
 Perception mode has consequences
 DOM-only blindness
 DOM parsing is cheaper than a vision model and brittle on dynamic pages. Modern portals using declarative partial updates change visible content with no full page reload, so a DOM-only agent goes blind exactly where the content is freshest. Teams without a vision fallback end up maintaining scripts alongside the agent, which defeats the reason they bought it.
 
 
 
 Budget reality
 Three times the scope
 The estimate worth quoting at a planning meeting: observability, session management and retry logic cost around three times what teams initially scope on open-source implementations. That gap is the whole distance between a working demo and something you can leave running, and it is the same lesson as the agent-built-scrapers section — the code is the easy part, the instrumentation is what you live with.
 

What to build, concretely. Structured action logs with task_id, step, timestamp, action, outcome and a confidence score, so a decline is visible as a trend rather than an anecdote. Screenshot traces for debugging, because reconstructing an agent's reasoning from text alone is miserable. Outcome validation before anything progresses down the pipeline. Credentials in a vault or environment, never in the agent's context. And treat the agent as an untrusted endpoint on your own network: scope its file access to the task, and monitor its session the way you would a third party's.

What this means if you scrape rather than build agents#

 
 Agent traffic is a client class now
 Sites have started treating declared agents as a distinct audience with its own commercial treatment, not merely allowing or blocking them. A publisher serving assistant crawlers a different, smaller, separately-monetised edition of the same URL is the clearest example, and your 200 will look perfectly healthy while you collect it. If you identify as an agent, check what you are being served against what a browser is served, at least once.
 
 
 Agent-first engines cannot do the hard part
 The trade is structural rather than a missing feature. Cloudflare states plainly that Kitesurf is not for negotiating a bot-challenge handshake with real TLS fingerprints or holding long authenticated sessions, and points you back to Chromium. A browser that is not Chromium cannot produce a Chromium ClientHello, and a stateless engine spun up per request has nowhere to accumulate session trust. Cheap and fast for open pages; wrong tool for a defended one.
 
 
 Agent-readiness is becoming a site property
 Two surfaces are appearing next to the HTML: a semantic layer through roles and accessible names, and a declared tool layer through WebMCP. Both are more stable than the markup around them, because both have somebody else's compliance or contract riding on them. Check for either before you write a selector — and diff what they return against the rendered page at least once, because they are maintained by different code and can disagree.
 
 
 The binary you are detected by is dissolving
 Automated traffic is now the majority, and a growing share of it is an agent acting for a real person who is genuinely the audience. That is why navigator.webdriver and CDP detection went soft, and why detection is moving to behaviour, intent and network identity. For a scraper the practical read is that "look human" is a less useful goal than "make coherent sense as some legitimate client", which may not be a human at all.
 

 27 field notes on agentic browsers
 Reported from practice. Click any card to open it.
 

 +
 Aug 2026 / Agents
 Computer-Use Agents Take 2.7 to 4.3× More Steps Than Necessary
 OSWorld-Human (arXiv:2506.16042, UC San Diego, accepted to MLSys 2026) re-annotated all 369 OSWorld tasks by hand with two graduate annotators and cross-validation, then measured how efficiently agents actually work. The widely-quoted figure of 1.4–2.7× more steps than necessary is from the June 2025 v1 abstract and has been superseded: the May 2026 revision re-ran the analysis and moved it up to 2.7–4.3×, across the same 16 agents. If you see 1.4–2.7 quoted in 2026, it is stale.
 💡 Twelve minutes to double-space two paragraphs
 Use the paper's own example, not the one going round LinkedIn. The much-repeated “two minutes for a human, twenty for an agent” line appears only in the authors' lab blog, not in either version of the paper. What is in the paper is sharper anyway: changing the line spacing of two paragraphs to double-spaced takes a computer-use agent 12 minutes, against under 30 seconds for a typical user.The metric worth borrowing. They introduce a Weighted Efficiency Score that penalises both inefficiency and the wasted steps a failure drags behind it. The gap it exposes is the point: the best agent with public trajectories scores 41.4% on success but 15.6% on WES. Success rate flatters agents enormously, because it counts the finish and ignores the wandering.What this means for a scraping budget. Per-call browser-infrastructure pricing is the number people compare, but steps-per-task is the multiplier nobody prices in. An agent that needs three to four times the necessary actions pays that multiple on every one of them. It is the strongest available argument for spending the model once at build time to generate a deterministic extractor, and running the extractor per page.
 
 +
 Aug 2026 / Infrastructure
 Cloudflare's Bet: Fewer Than 10% of Agent Tasks Need a Container
 @cloudflare/computer, announced 3 Aug 2026, is a virtual filesystem living inside a Durable Object with authoritative state in SQLite, exposing one pluggable execution surface via workspace.runtime.exec(source, { backend }). The premise: giving every agent its own container will not scale, so route the roughly 90% of agent work that is plain file manipulation into cheap isolates, and reserve containers for native binaries and package managers.
 💡 Three corrections to how this is being described
 It does not ship a browser. The three backends are a FUSE-mounted Linux container, a bash-only isolate shell, and a JavaScript isolate runtime. Browser work is a separate Cloudflare product (Browser Run, renamed from Browser Rendering in April 2026); the announcement names a browser only as future intent. It is not a hosted product with a price — it is an MIT-licensed npm library, explicitly marked preview-only with unstable APIs, suitable for prototypes; you pay for the underlying Workers, Durable Objects and Containers it orchestrates. And it is not a quiet release: 8,805 GitHub stars, not the four-figure number doing the rounds.The benchmark is more interesting than the pitch. On their own numbers, the FUSE mount is roughly 2× slower than ext4 and 3.6× slower than tmpfs on a full npm install, and up to 41× slower on bulk I/O. But it beats real disk on metadata-heavy work: removing 1,000 files at 0.66×, walking a tree at 0.72×, git init plus a 100-file commit faster than local.Read that as a shape, not a score. Agent work is overwhelmingly metadata-heavy — listing, stating, moving, small edits — which is exactly where this design wins and exactly why the 10% claim is plausible.
 
 +
 Aug 2026 / The other side
 What a Site Actually Changes When It Decides to Be Agent-Readable
 A consultancy published the build log rather than the opinion piece, and it is the most concrete answer yet to what does agent-ready actually mean. Three tiers — Discovery, Understanding, Action — of which they implemented the first two and deliberately skipped the third. Every change is verifiable live, which is the reason to cite this one over the hundred think-pieces published the same month.
 💡 llms-full.txt is 26,839 bytes of the whole site as clean markdown
 Discovery tier. An XML sitemap generated automatically by the build. A custom build integration emitting llms.txt (1,699 bytes, the index) and llms-full.txt (26,839 bytes, about 4,054 words) on every deploy rather than maintained by hand — each page's HTML parsed, stripped of navigation, headers, footers and scripts, then converted to clean markdown. And a robots.txt carrying a Content-Signal directive, live and exact: Content-Signal: search=yes, ai-input=yes, ai-train=no. Read that as: index me, ground an answer in me, do not train on me.The deadline behind it. They cite Cloudflare's announcement that from 15 September 2026 new domains will block AI agents by default on pages displaying ads unless the owner opts in. The default is flipping, and sites that want agent traffic will have to say so explicitly.Honest note on the framing. They did not skip the Action tier to protect human visitors, which is how this gets retold. Their stated reason was simpler and more useful: the tier did not yet earn its complexity for a site of that size.
 
 +
 Aug 2026 / Threat model
 Five Tricks in One Email: The Anatomy of an Agentic-Browser Injection
 The guide has covered that agentic browsers fall to indirect prompt injection. A security lab published the construction of one such payload against Claude in Chrome, and the value is in seeing that a working injection is not one clever sentence — it is five deliberate techniques layered into a normal-looking email.
 💡 The payload does not look like an attack; it looks like structure the model already trusts
 The five layers, because the composition is the lesson. A fake structural boundary convinces the model the email has “ended,” so what follows reads as a new context rather than quoted content. A hidden image steers the agent toward the tool that will read the rest of the payload. A spoofed assistant response is planted to build false trust, so the model treats the next line as its own prior reasoning. A fake user command, disguised as an innocent debugging request, issues the actual instruction. And padding blends the whole thing into the page's normal structure so nothing stands out. The end state: a normal-looking email that, to the model, was a complete instruction set ending in arbitrary JavaScript execution from an attacker-controlled CDN.Why a scraper-builder should read an attacker's write-up. This is the exact inverse of the collection problem, and it is the same mechanism the guide keeps returning to: an agent cannot separate instruction from content, so any page or document it ingests is a potential instruction set. If you point an agentic browser at pages you do not control — which is the whole job — you are the target this payload was built for. The defensive takeaway from the authors is the honest one: understanding how the payload is assembled is the first step to defending against it, because there is no single string to filter — each of the five parts is innocuous alone. Control the actions the agent can take, not the text it reads.Source: Zenity Labs.
 
 +
 Aug 2026 / New pattern
 Agents Rediscover the Web Every Session. Somebody Started Keeping the Notes.
 An agent can spend fifteen tool calls working out a bad date picker, finding a hidden JSON endpoint, or learning the shortest route through a site. Then the session ends and every bit of that is thrown away. The next agent arrives and pays for the same discovery again.
 💡 Once the hard thinking is done, you no longer need the model that did it
 What a “skill” actually is here: a markdown file that is “not merely a replay of clicks, but a distillation of the durable knowledge about a site” — the URL scheme that skips the whole interface, the hidden JSON blob the page inlines, the anti-bot quirk, a runnable extractor. It is written automatically from instrumented sessions: the commands issued, the URLs visited, the task given, how long it took, with credentials and one-off parameters stripped before anything is saved.The lifecycle is the part worth copying, because a memory that only grows is a liability. Skills are widened when a later run discovers more (a country filter the first agent never tried), gated by safety and topicality checks, provenance on whoever produced the session, and human review before anything reaches a public catalogue. Each carries success and failure counts and is automatically demoted when it drops below threshold. Knowledge that stops being true gets retired rather than quietly poisoning the next run — which is the same discipline the self-healing card in this feed argues for, applied to memory instead of code.Why it changes the bill and not just the latency. The claim is that an open-weight model can then handle site tasks that would otherwise need a frontier one, because following a proven recipe is instruction-following rather than exploration. That is this guide’s own principle from a new direction: spending the model once at build time is cheaper than spending it on every request — and here the “build” is done by whichever agent happened to go first, on behalf of everyone after it.The caveat to hold. A shared recipe pool across tenants is a shared blast radius: a skill that encodes a site quirk is fine, a skill that quietly encodes an assumption that has since changed is a wrong answer delivered fast and cheap. The failure counters are the control, so the number that matters when evaluating any system like this is how quickly a stale skill is demoted, not how many skills it holds.
 
 +
 Aug 2026 / Observation
 Sites Now Serve Two Versions of Themselves, and the Clean One Is For Machines
 The shortest post in this batch and among the sharpest. The human version has cookie walls, popups and fake countdown timers. The machine version is clean, structured, no theatre. People are upset about the split and have decided the bot web is the fake one.
 💡 Look at what each version was built for before deciding which is the honest one
 The inversion is uncomfortable and correct. The version shown to people is optimised against them — consent friction, interstitials, manufactured scarcity, layout that fights the reader for attention. The version shown to machines carries the same underlying facts with none of it, because none of that machinery works on a parser and all of it costs tokens. Whatever else it is, the machine version is the one where the publisher stopped trying to manipulate the reader, for the simple reason that the reader could not be manipulated.Two numbers to sit alongside it. Cloudflare's public bot-versus-human tracker puts bots at roughly 57 to 58% of HTTP requests for HTML content against 42 to 43% human. Imperva's report on 2025 data puts bots at about 53% of measured traffic for the second year running. Different methods, different denominators, same conclusion: the majority audience for HTML is already not people.Where it stops being comfortable. A two-version web is a cloaking arrangement, and cloaking has always been the thing search engines punish and researchers distrust — you cannot verify a claim about a page if the page you are shown differs from the page others get. It also sits uneasily beside the poisoned-font card above, which is the same capability turned hostile: once serving different bytes to different clients is normal infrastructure, serving deliberately wrong bytes is a configuration change rather than an attack. The split is not itself dishonest. It removes the guarantee that used to make dishonesty detectable.
 
 +
 Aug 2026 / Shipped
 WebMCP Ships At the Edge, and Is a Bridge To Something You Have Not Built
 The callable half of the agentic-web stack landed as a product feature: toggle a setting, a script is injected into your HTML, and browser agents can discover tools on your page. Then you read the mechanics.
 💡 The pack that takes real action is a proxy to an MCP server you are already hosting
 Two gaps, both structural rather than teething. First, the component that lets an agent do something — call a search, place an order — forwards to an MCP server you built and host yourself at your own endpoint. If that does not exist, the feature does nothing. The bridge shipped; the far bank is still your problem, which is a very different proposition from “toggle a setting”.Second, and more interesting for anyone deciding where to spend: these tools only activate for an agent that is actually driving a browser. A large share of agent traffic that matters commercially never opens one — desktop assistants and coding agents call MCP endpoints directly. For those clients, a script injected into your HTML is invisible by construction. So the feature addresses the browser-agent case precisely, and the non-browser case not at all.Read the source carefully: this critique comes from a company selling the alternative it names, which is a reason to check the mechanics rather than to dismiss them — and the mechanics check out, because “it proxies to your own endpoint” is a claim about documented behaviour rather than a matter of opinion. Set against the adoption numbers elsewhere in this feed, where MCP server cards appeared on fewer than fifteen sites in an entire scan, the honest summary is that the plumbing is arriving faster than anything to plumb.
 
 +
 Aug 2026 / Measured
 The Big Assistants Do Not Run Your JavaScript. The Others Do.
 A follow-up to the traffic-measurement card, and sharper than it. When you ask an assistant to read a URL it fetches the HTML and stops. A structured test found none of ChatGPT, Claude or Gemini rendered JavaScript — while several non-US assistants including DeepSeek and Mistral did, and a re-check this month found it still true.
 💡 If your content needs JS to appear, the largest assistants cannot see it at all
 Two consequences, and the second is the one nobody has priced in. First, the obvious one: a page whose content is assembled client-side is, to those assistants, an empty shell. Not ranked badly, not summarised poorly — absent. Every argument about being discoverable to AI is downstream of whether the content exists in the HTML at all, and this is a single-request check anyone can run against their own site today.Second: they are invisible in your analytics. Client-side analytics fire from JavaScript. No JavaScript, no beacon, no row. So the assistant traffic arriving at your site does not appear in the dashboard most teams use to decide what to build, which is precisely why the measured 2:1 software-to-human ratio in the earlier card surprised the people who measured it. They had to read raw server logs to find it. If your only instrument is client-side, your instrument is blind to the fastest-growing segment of your traffic by construction.The split is the interesting part. That the US assistants skip rendering and several others do not is a cost decision, not a capability gap — rendering is expensive at fetch volume. It also means “do AI assistants execute JavaScript” has no single answer, and any advice built on one is describing whichever assistant the author happened to test. Segment by client, as ever.
 
 +
 Aug 2026 / Measured
 268,000 Agent Requests, Two Months, and Almost Nothing Read the llms.txt
 A team instrumented their own site for two months and counted what actually arrived. Software traffic beat human traffic by more than two to one — roughly 268,000 agent-tracked requests against about 107,000 human pageviews. The numbers underneath that are the interesting part, because several of them contradict what the ecosystem has spent a year recommending.
 💡 “AI traffic” is not one population — segment by user agent before you believe any conclusion
 What arrived, by name. ChatGPT-User was 196,973 requests, about 73% of all agent traffic, and it pulled markdown 272 times — call it 0.1%. Claude Code was 23,300 requests and asked for markdown 17,814 times, 76%. Perplexity was 7,728 and took markdown essentially never. OAI-SearchBot 7,255 at 26%; GPTBot 3,579 at 31%. Across everything markdown was about 15% of reads, roughly 40,000 against 227,000 HTML.The lesson is not “ship markdown” or “do not”. It is that the answer differs by nearly three orders of magnitude between clients, so advice treating agent traffic as one population is advice about an average nobody experiences. A site serving mostly ChatGPT users and a site serving mostly coding agents should do different things.The uncomfortable finding. /llms.txt was fetched about 660 times and /llms-full.txt about 110 — but only 37 of those came from a named AI assistant. The rest were search crawlers and unidentified bots. And a hidden <link> element pointing at the markdown version, the trick that circulated widely as a cheap win, recorded zero measurable hits across all 268,000 requests.Two operational notes that generalise. AI crawlers do not execute JavaScript, so client-side analytics cannot see any of this — the measurement has to happen in middleware reading raw headers, which is also why most teams have no idea what their agent traffic looks like. And the referrer data was contaminated: 106 of 117 referred hits carried a single frozen Chrome/111.0 string, a reminder that a user-agent is a claim rather than a fact.Read it against the adoption numbers. Cloudflare's Agent Readiness work, published the same month, scores sites on discoverability, content, bot access control and capabilities, and found 78% of sites have a robots.txt, 4% have declared AI usage preferences in it, and markdown content negotiation passes on 3.9%. MCP Server Cards and API catalogues together appeared on fewer than fifteen sites in the entire dataset. Markdown is worth up to 80% fewer tokens, and Cloudflare's own docs measured 31% fewer tokens and 66% faster to a correct answer than an unoptimised competitor's.And the dissent worth keeping. One practitioner's answer to the whole agent-readiness push: crawlable is not the same as recommended. A model can index you perfectly and still never say your name, because it also read what the rest of the internet says about you, and that is decided outside your domain. Access is necessary and nowhere near sufficient.Sources: evilmartians.com chronicles (Rita Klubochkina); blog.cloudflare.com/agent-readiness; isitagentready.com.
 
 +
 Aug 2026 / New category
 HTTP 402 Sat Reserved For Thirty Years. Agents Are What Finally Uses It.
 This guide has covered Web Bot Auth at length: the automated client cryptographically signs its request and the site verifies the signature, so identity stops being a user-agent claim. The half that was missing is what happens once you know who it is. A cluster of work this month answers that with a status code that has sat marked for future use since HTTP/1.1.
 💡 Identity was the hard part; billing is the part that changes who gets served
 The problem is an accounting one. The open web runs on an attention model — the page is served free and value settles downstream through a human seeing an ad or paying a subscription. An agent consumes the compute and skips the settlement entirely. Worse, much of the traffic is waste on both ends: Cloudflare's data shows a large share of well-behaved bot traffic is re-fetching pages that have not changed.The handshake. The edge intercepts the request and returns 402 Payment Required with its terms, the client pays, and the proxy verifies proof of payment before the origin ever executes. No pre-shared API key, no account, no checkout redirect. It is framed as four primitives that only work together — readable (markdown for agents on the server side, Kitesurf as the client-side half), discoverable (agent engine optimisation rather than keyword SEO), callable (WebMCP, where a site exposes its actions as explicit tool contracts with JSON schemas instead of making an agent guess which button to click, plus Code Mode where the agent calls endpoints in code rather than prose), and payable (x402 and PACT tokens, with wallets on the agent side).Why a collector should care, in both directions. If you gather data, this is the first credible pricing mechanism aimed squarely at you, and it prices per request rather than per contract — a very different negotiation from a rate card. If you run a site, it turns the bot question from block-or-allow into a third option, and the argument for it is commercial rather than moral: behind almost every agent is a paying customer, so treating agents like legacy scrapers means losing the customer.The honest caveat. This is largely one vendor's stack described in that vendor's own posts, and the adoption numbers in the card above — fewer than fifteen sites in the dataset carrying an MCP Server Card — say the callable and payable layers are proposals rather than conditions. Worth understanding now, worth building against later, worth not rearchitecting around yet.
 
 +
 Aug 2026 / New pattern
 The Agent Hits a 403 and Solves It Itself, Because the Solver Is an MCP Tool
 A pattern this guide has watched arrive from several directions landed concretely this month. Instead of a human wiring up a bypass for a known site in advance, the solver is published to the model as tools, and the agent reaches for one when it meets a challenge nobody anticipated.
 💡 Solve server-side and you submit the container's TLS fingerprint, not a browser's
 What is exposed. An MCP server over stdio publishing akamai_solve (for _abck), datadome_solve (from an HTML response), plus health_check, solver_stats and akamai_queue_metrics for backpressure. The usage pattern in the demo is a prompt rather than a script: fetch this URL, and if you get a 403 or an access-denied page, use the solver, then retry the same request with the cookies it hands back.The technical point worth extracting is why the Akamai path drives a real Chrome relaying each sensor request over the session socket instead of computing the sensor server-side. Solving server-side transmits the container's TLS fingerprint rather than a browser's, and any challenge that verifies the client actually submitting it will reject the answer: correct sensor payload, wrong envelope. A run against a live commercial target completed in 20.8 seconds.The maintenance lesson underneath it applies whether or not you ever touch this. The browser identity had been fragmented across several files, so the Akamai scripts had quietly drifted two versions behind the DataDome clients. The fix was a single profile module everything derives from — DataDome clients, Akamai telemetry, CDP overrides, MITM headers, the MCP tools — with values like sec-ch-ua computed rather than hardcoded. A fingerprint assembled in more than one place will eventually disagree with itself, and that disagreement is what gets caught.One operational constraint stated plainly and worth repeating: the clearance cookies are bound to the exit IP, so the retry has to leave through the same proxy. An agent whose built-in fetch tool egresses somewhere else will earn a fresh 403 while holding a perfectly valid cookie.The deployment model is the actual differentiator, and it is worth separating from the MCP novelty. This ships as a Docker image you run on your own hosts: your worker calls the container on localhost, the solver generates the payload, and a valid token comes back without a single external API call. That is the opposite shape to every managed unblocker in §07 — those are per-request calls to somebody else’s endpoint, which means your target list, your URLs and your timing all leave your network. Here nothing does, which makes air-gapped deployment possible. Billing follows the architecture: a flat fee for unlimited solves rather than per-request or per-GB, which inverts the crossover maths in §13 — the cost stops scaling with volume and starts being a fixed line.One claim worth reading against the artifact. The product page describes solving at the payload level, “milliseconds, not seconds”, with no Playwright instances and no per-challenge browser overhead. The pull request above, which is the changeset that adds the Akamai path, drives a real Chrome and reports 20.8 seconds. Both can be true at once, and the reconciliation is the lesson: the payload-level story holds for DataDome and does not survive contact with a vendor that verifies the client actually submitting the answer. Read any blanket “no browsers needed” claim per vendor, not per product — and note that the honest version of the architecture is visible in the repository while the simpler version is on the homepage. The commit is the better source, which is generally true and is why this guide keeps linking them.Source: xhrdev/examples PR #40.
 
 +
 Aug 2026 / Design for machines
 Send the Same POST Twice. An Agent Will Never Tell You It Made Two Rows.
 Not a scraping post, but it belongs here because it names this guide's recurring failure mode from the other end. An engineer sat down to write about how their API handles retries safely, sent the identical POST twice, and got back two rows with two different ids.
 💡 The properties that matter most to a machine caller are the ones no human tester will ever report
 The diagnosis is the good part. Three of their four design decisions for a non-human caller held up: UUID primary keys so an id survives across sessions, an OpenAPI schema generated from the serializers so the contract cannot drift from the code, and server-side filtering so a caller does not pull the whole set to find one row — turning a forty-request pagination loop into one indexed query. The fourth did not exist at all. No Idempotency-Key header, no unique constraint, and an ingest endpoint calling create() keyed on nothing: same URL five times, five items.Their own framing of the mistake is the line worth keeping. They had built the identifier story and mistaken it for the write story. A UUID makes a row addressable once it exists. It does nothing about the request that created it arriving twice.Why it lands harder with an agent on the other end. A human sees the duplicate and files a bug. An agent times out, cannot determine whether the write landed, retries, and says nothing — so the duplicate arrives silently and surfaces much later as a data-quality problem with no obvious cause. That is the same shape as the silent-200 this guide keeps returning to: invisible where it happens, expensive where it shows up.The two fixes named. Either a client-supplied Idempotency-Key stored behind a database constraint, replaying the stored response on a repeat; or, for a narrow ingest endpoint, get_or_create on the natural key such as (owner, url). If you are building the API your own collection pipeline writes into, this is a ten-minute check: send the same request twice and look at what you get.Source: bessavagner.com, an API-first vault my agents can call.
 
 +
 Aug 2026 / The argument
 Three Answers to "Agents Need Browsers", and One Person Saying They Do Not
 This guide has been tracking the lighter-browser thread with some enthusiasm. It deserves its counter-argument, and this week produced a genuine three-way disagreement worth holding all at once.
 💡 Shrink the browser, multiply it cheaply, or move the tooling inside it — or skip it entirely
 Answer one, shrink the browser. Kitesurf, Moli, Obscura, Lightpanda. When the consumer is a model, the rendering pipeline is dead weight, so throw it away and keep the DOM and CDP. Covered at length above.Answer two, keep the real browser and make it cheap to run thousands of. Unikraft's founder puts the objection to answer one bluntly: you cannot run JavaScript, render a page, take a screenshot or click a button from a comfortable CLI, and agents touching real sites need a genuine engine underneath rather than a lightweight stand-in. His framing is the useful part: the problem was never whether agents need real browsers, it is what happens once you try to run thousands of them. That reframes it from a rendering problem to a cold-start and isolation problem, which is unikernel territory rather than browser-engine territory.Answer three, put the operating system in the browser instead. BrowserPod 3.0 goes the other way: rather than shrinking the browser, it implements Linux syscalls in WebAssembly — a custom wasm32-browserpod-linux-musl target rather than WASI, giving a persistent virtual filesystem, inbound and outbound networking, threads on separate Web Workers, and real subprocesses. They compiled and ran ripgrep, Starship, jj and OpenAI's Codex unmodified, the last at around 1.25 million lines. The limitation they state plainly is that Rust programs must be compiled offline, and the target declares arch="wasm64" while excluding the wasm family tag to dodge ecosystem assumptions about reduced-capability builds.And the dissent, which cuts all three. A sceptic's post on running full browsers in V8 isolates called it a leaky abstraction and asked the question the whole category should answer: for production agents you usually need specific data points, not a simulated DOM and rendering engine, so a purpose-built HTTP client with proper error handling and retry logic will beat a simulated browser most of the time. He is right about the median case, and this guide's own escalation ladder agrees with him — plain HTTP first, browser only when proven necessary. Where he is too strong is the tail: login flows, canvas-rendered content and JavaScript-constructed data are not reachable from a CLI at any level of retry sophistication. The honest position is that all three architectures are answers to the minority of traffic that genuinely needs a browser, and the argument is only about how expensive that minority has to be.
 
 +
 Aug 2026 / New pattern
 Repos That Install Themselves, Because the Reader Is an Agent
 A small open-source release with a design choice worth more than the tool: a stealth-browser MCP server whose repository is written to be read by an AI assistant rather than a person. Hand your agent the GitHub link and it reads hidden <ai_context> instructions in the repo, installs the Playwright binaries, and configures the tooling for itself.
 💡 A README for humans and an install script for agents are becoming different documents
 The author calls it an agent-first architecture, and once you see it you notice the same instinct forming elsewhere: llms.txt, WebMCP tool declarations, accessibility-first markup, and now repositories carrying embedded setup instructions addressed to whatever model is reading them. The through-line is that the machine consumer has stopped being an afterthought parsing documentation written for people, and is being handed its own channel.Which is genuinely useful and also a small, clear security lesson. A repository that instructs your agent is, definitionally, untrusted text that your agent will act on. It is the supply-chain version of the prompt-injection problem in the agentic section above: nobody had to compromise a package registry, because the instructions are the feature. An <ai_context> block that installs binaries and rewrites your tool configuration is doing precisely what an injection payload would do, with your consent, and the only difference between the helpful case and the hostile one is the author's intent, which you cannot read from the file.So treat it as you would any install script. Read the embedded instructions yourself before pointing an agent at a repository, particularly one that installs browser binaries or edits configuration. And note the asymmetry that makes this worth flagging rather than shrugging at: a human skimming a README will not see an <ai_context> block at all, while the agent reads it as the primary content. That is the same invisible-to-humans, authoritative-to-machines shape as the canvas injection vector and the markdown edition a publisher serves only to bots.
 
 +
 Aug 2026 / Agent security
 Every Agentic Browser Tested at Black Hat Lost to a Web Page
 Brave's Artem Chaikin presented "Attacking and Defending AI Browsers" at Black Hat USA 2026, testing Comet, ChatGPT Atlas and Opera's AI browser against indirect prompt injection. None of them held. No malware, no exploit chain, just words on a page the model decided to trust.
 💡 We spent a decade teaching browsers to distrust the page. Agentic browsers handed that trust back by default.
 The mechanism is architectural rather than a bug, which is why it is not a patch away. These agents feed raw page content straight into the model with no separation between the user's instructions and the page's content, so a hostile page can issue commands the agent treats as trusted. What that unlocks: exfiltration from the active browsing session, and account takeover through hijacked trusted UI actions, using plain web content with no payload to detect. Researchers have separately reproduced the same class against MultiOn, Browser Use and OpenAI Operator, so this is not three unlucky products.WebMCP widens the same wound, and this is the part teams miss when they ship it. Every tool you register is effectively a public API that a stranger can invoke with words you never wrote. An attacker needs no login and no stolen token; they plant text where the agent will read it, and the agent calls your tools using its own trusted session — the user's cookies, the user's CSRF token, the user's permissions. A registered updateEmail or applyCoupon executes as the logged-in user. Chrome's own guidance is explicit that registered tools are an attack surface and prompt injection is the delivery mechanism.What follows for both sides. If you publish tools, decide before launch which actions an agent may take unsupervised and which require a human in the loop, because that is a design decision and not something you bolt on afterwards. If you run agents against the open web, the question to sit with is the one Chaikin's work implies: would you let an agent browse arbitrary pages using the same account it holds production access with? The guide's own prompt-injection material covers the URL-fragment surface that never crosses the network; this is the same threat with a session and a tool list attached.
 
 +
 Aug 2026 / Agent economics
 Two Thirds of an Agent's Token Bill Is the Browser Tool, Not the Thinking
 Someone swapped the built-in browser tools out of two popular coding agents and measured it across 26 tasks on a local desktop Chrome. One agent used around 70% fewer tokens and cost about 61% less. The other, 54% fewer tokens and 38% cheaper. Same model, same tasks, same browser. Only the tool surface changed.
 💡 The waste is not the reasoning. It is the page text you keep re-reading.
 The diagnosis is worth more than the numbers. Stock agent browser tools are chatty: they dump large page payloads into context and expose a wide menu of tools. The model then spends tokens re-reading content it already has and calling tools it did not need, and every one of those is a round trip. The replacement is two ideas: a deliberately tiny snapshot tool, so the page arrives as a lean representation rather than a wall of DOM, and an exec tool that accepts Playwright code, so a whole batch of interactions happens in a single model turn instead of one turn per click.This is now the fifth independent measurement pointing the same way, and at this point it is a settled finding rather than a claim. The Browser Use team found strong models rarely want screenshots and prefer to inspect the page through code they write themselves. The accessibility-tree work found the win comes from operating known UI patterns as atomic actions with no model round trip at all. Hermes swapped twelve per-action browser tools for a single script-driven engine on browser-use's CLI 3.0, and Nous Research measured 48 to 66% fewer tokens with no drop in accuracy — because a dozen tool schemas were riding along in every request whether used or not, and opening a page to read one headline cost four or five round trips. Vercel's agent-browser made the same move from MCP tool schemas to a lightweight CLI and reported 3.5× faster execution and 37% fewer tokens. And this one finds most of the bill is payload and tool sprawl rather than reasoning. Five teams, five methods, one conclusion: the expensive thing in agentic browsing is the number of times the model has to look and the number of schemas it carries while looking, and the cheapest optimisation is to make it look less often rather than to make it think better. The shape of the fix is identical everywhere too — stop exposing one tool per action, and let the agent express a whole task as one script.If you are building on an agent framework, this is a config-level change rather than a rewrite, which makes it one of the few genuinely cheap wins in this part of the stack. Measure your own tasks though: these gains come from removing waste specific to a tool surface, so how much you recover depends on how chatty yours is.
 
 +
 Aug 2026 / AI visibility
 Claude Cannot Follow a Link It Has Not Been Given, and That Is Deliberate
 Fingerprint's team tested how Claude actually reaches pages on the web and found a constraint that quietly rewrites "optimise for AI discovery": a relative link sitting in HTML the model has already fetched is not enough to unlock that page. Not across multiple pages, not via an llms.txt, not via a .md version.
 💡 It is a security boundary, not a crawler limitation — so it will not be "fixed"
 The mechanism is documented but rarely read: the fetch tool is provenance-gated. It can only fetch URLs that have already appeared in the conversation context — user messages, prior search results, prior fetch results. It cannot follow a URL it constructed itself. The stated reason is exfiltration defence: if a model could fetch any URL it composed, then any attacker-controlled text on any page it read could become a channel for sending your context somewhere. Read that alongside the prompt-injection material in this guide and the design is obviously correct, which is also why nobody should plan around it changing.The consequence for anyone who wants to be read by an assistant. The search index becomes the gatekeeper rather than your link graph. For Claude that means Brave Search is the practical entry point — a page that is not indexed there may simply never be reachable, no matter how clean your internal linking is. Absolute URLs help, because an absolute URL that appears in fetched content has provenance where a relative one does not. Practitioners in the thread noted the same asymmetry applies to every assistant with its own index, and that there is no Search Console equivalent for most of them, so the only honest method is to probe and measure rather than assume.The mirror image, for scrapers: this is a reminder that agent traffic is shaped by tool policy, not just by crawl budget. If you are modelling which of your pages assistants can see, the question is not "did I allow the bot" but "did the URL ever have provenance". Anthropic's web-fetch tool documentation
 
 +
 Aug 2026 / The machine-facing web
 WebMCP Shipped, and Most of What Shipped Is a Bridge to Something You Have Not Built
 Cloudflare turned on WebMCP as a developer preview: flip a switch in the dashboard, pick your packs, and the next HTML your site serves carries the bridge — nothing deployed, nothing changed at your origin. It rides document.modelContext, the browser API shipping experimentally in Chrome 146, so browser agents can discover tools on your page. The plumbing is real. The part people assumed was included is not — the pack that lets an agent take a real action is a proxy that forwards to an MCP server you are already hosting at your own endpoint.
 💡 A site can now have two front doors, and they can disagree
 Two gaps got named quickly. First, the action layer is a bridge with nothing on the far side unless you built it: if you do not have an MCP server, the pack does nothing. Second, WebMCP tools only activate for an agent that is actually driving a browser, and a large share of the agent traffic that matters — desktop assistants, IDE agents — never opens Chrome to read your page; it calls an MCP endpoint directly.The subtler critique came from the Chrome side, and it is the one worth keeping. Treating WebMCP purely as a tunnel to an existing MCP server throws away the thing that makes it different: WebMCP tools are part of your frontend code, with access to client-side state — cookies, local storage, the DOM — and can write to it as well as read it. A set_filters tool can actually change what the user sees. And registering the same tools twice, once via an MCP server and once via WebMCP, duplicates them for the agent, inflating tool count and creating genuine ambiguity about which to call.What this means if you scrape. A growing number of sites will expose a declared, structured, versioned tool surface next to their HTML — and the two can disagree, because they are maintained by different code. That is the same lesson as the publisher-serving-bots-a-different-site card, arriving from the opposite direction: the page a machine is offered is increasingly not the page a person is offered. Check for a tool surface before you write a selector, and if you find one, diff what it returns against what the rendered page shows at least once, because you are now choosing between two sources of truth rather than parsing the only one. Cloudflare's WebMCP announcement
 
 +
 Aug 2026 / Agent-native browsers
 The Other Answer to Agent Blocking: Stop Using Someone Else's IP
 Cloud browsers get IP-blocked on the sites agents most need — the ones you are logged into. Headless browsers run blind, so you never see what the agent did or why it failed. BrowserOS neo takes the opposite bet from the whole hosted-browser category: run on the user's own machine, with the user's own Chrome sessions, and show the work.
 💡 For logged-in work the residential-proxy problem disappears if you never leave the residence
 The design choices are the interesting part, and several are directly transferable whatever you build on. Local execution with imported Chrome sessions, so the agent operates inside apps the user is already authenticated to — no proxy, no reused-IP reputation, no login flow to automate, and no credential ever handed to a third party. A live cockpit showing every agent's activity as it happens, instead of reconstructing failure from logs. Session replay as scrubbable video, which is the single most underrated capability in agent automation: when a run fails you scrub back to what the agent actually saw. Parallel agents in isolated tabs so concurrent runs do not tread on each other's state. And simplified web snapshots instead of screenshots — a lean text representation of the page rather than an image, which cuts token use substantially and lands in the same place as the accessibility-tree result above. Free, open source, macOS and Windows, past 13,000 GitHub stars.The trade to be honest about. This solves the blocking problem for authorised, logged-in, user-owned work, and it is the correct architecture for that case — it is the same argument the Computer Use Agents section makes about data behind a login the user owns. It does not solve public-web collection at volume: one machine, one residential IP, one browser profile, and the moment you need geographic distribution or concurrency beyond a laptop you are back in proxy territory. Read it as a category boundary rather than a replacement. browseros-ai/BrowserOS on GitHub
 
 +
 Aug 2026 / Bot-majority web
 Half the Web Is Automated, and the Publisher's Best Reader Might Be a Bot
 Automated traffic is now around 53% of the web, and US traffic has crossed the 50% line on Cloudflare's own radar. The reflex is to block. A publisher-side story from August makes the case that the reflex is expensive, and it is more interesting than the usual bot-panic piece.
 💡 The bot/human binary is a measurement artefact, not a fact about the reader
 A team found an AI agent crawling one of their publishers' sites — methodically working through specific topic categories, textbook bot behaviour, exactly the traffic the industry says to block. When they traced it, the agent belonged to one of that publisher's own advertisers: a real professional had dispatched it to research content in their category, and the content was being consumed by a real person, just indirectly. Block that traffic and your numbers get cleaner and smaller, and you have cut off the reader the publication exists to reach.Their argument is that the answer is not a better filter but a better unit of value — a known audience, because a bot can fake an impression but cannot validate a work email, complete a profile and come back three more times. Whether or not you buy the framing, the underlying observation is the one that matters for anyone building collection infrastructure: the identity of the requester and the identity of the beneficiary have come apart, and every detection system still assumes they are the same.The legislative counter-current is moving the other way. "Stealth crawler" has gone from slang to a term in draft law: a bill passed in New York last month and legislation introduced in the US House in July both aim to prohibit bots from masking their identity. That is a live risk to a large amount of current practice, and the honest read is that the disclosure question — who you say you are, in your user agent and in your headers — is drifting from an engineering choice toward a compliance one. Track it in the legal section of this guide, and note the direction of travel: identification is becoming cheaper to comply with and more expensive to avoid.
 
 +
 Aug 2026 / The machine-facing web
 A Publisher Now Serves Bots a Different Website, With Ads Built In
 The machine-majority web stopped being a statistic and became a product decision. TIME.com now answers the same article URL with two entirely different documents depending on who asks, and the version written for assistant crawlers carries its own advertising. This is not a block and it is not poisoning. It is a second edition of the site, authored for machines, that no human reader will ever see.
 💡 The User-Agent header is quietly becoming a billing identity
 The test is one any reader can repeat: fetch the same article, change only the User-Agent, and compare. As Chrome or Safari the response is text/html, 303,235 bytes, and Google's search crawler receives the same page a person does. As ClaudeBot, PerplexityBot or OAI-SearchBot the response is text/markdown, 13,409 bytes, roughly one twenty-third the size, byte for byte identical between those three. GPTBot and ChatGPT-User are refused outright with a 406. Inside the markdown, where no person would ever encounter it, sits sponsored content: adtech vendor Mobian serving FAQ blocks for an online bank and a professional body, with tracking links carrying campaign identifiers, and response headers x-mobian-impression (a fresh UUID per request) and x-mobian-tokens counting what was served. Three separate audiences, three commercial treatments, decided at the header.Why this matters to a scraper, and it is not the obvious reason. The guide has covered poisoned data, where a site detects you and quietly serves wrong values. This is the opposite in intent and identical in consequence: the document you receive is legitimately, deliberately not the document a human receives, and nothing about it looks like a block. Your status code is 200, your parse succeeds, your record count is plausible, and you have collected a different edition of the page. Two habits follow. Diff your extraction against a browser-rendered fetch of the same URL, not just against yesterday's run, because a content-type shift from html to markdown is invisible to a selector-based validator that only counts fields. And treat the identity you declare as a variable that changes what you are served, not merely whether you are served. Vincent Schmalbach's original teardown
 
 +
 Aug 2026 / Agent-native browsers
 Cloudflare Shipped a Browser for Agents That Cannot Pass Its Own Challenges
 Kitesurf, announced 6 August 2026 during Cloudflare's Agents Week, is a browser engine written from scratch in Rust, compiled to WebAssembly, running inside V8 isolates on Workers. There is no Chromium underneath. It implements the Chrome DevTools Protocol, so existing Puppeteer, Playwright and chrome-remote-interface clients connect unchanged. Free while in beta.
 💡 It speaks CDP, so your code works. It has no real TLS fingerprint, so hard targets will not.
 The numbers, from Cloudflare's own benchmarking, are a clean trade rather than a win: 3.1 to 3.8 times less CPU and 4.7 to 7.0 times less memory than Chromium on common agent tasks, while Chromium remains 1.7 to 1.8 times faster on wall time. Kitesurf wins on cost per session, not on speed.The limitation is the interesting part, and Cloudflare states it plainly. Kitesurf does not support bot-challenge handshakes that require real TLS fingerprints, and it has no WebGL, no video, and no long authenticated sessions that need persistent state. For those cases the documentation points you back to Chromium. Read that against Layer 1 of the detection section and the reason is structural rather than a missing feature: a browser that is not Chromium cannot produce a Chromium ClientHello, and a stateless engine spun up per request has nowhere to accumulate the session trust that vendors like Akamai score across multiple requests.This lands squarely in the lighter-browser thread alongside Obscura and Lightpanda: when the consumer is a model rather than a person, most of the rendering stack is dead weight, so the sensible move is to drop it. Kitesurf is the largest infrastructure provider on the web making that bet in public. It also puts Cloudflare on both sides of the same street, selling the bot management that decides which automated clients get through and the browser those clients drive. Cloudflare Browser Run docs
 
 +
 2026 landscape / AI browser stack
 The AI Browser Stack: 3 New Layers Nobody Talks About Yet
 The AI browser race has the headlines (Atlas, Comet, Dia, new contender every month). But the winner gets decided by what sits underneath. A fresh map of the stack the AI browsers actually run on shows three categories that did not exist a year ago: AI-native browsers, anti-detect platforms, and publisher licensing/tolls.
 💡 Publisher licensing is the economic answer to "AI crawlers are scrapers too"
 Three additions worth tracking, beyond the agentic browser map already in this guide. (1) AI-native browsers as a top layer: ChatGPT Atlas, Perplexity Comet, The Browser Company's Dia. Not wrappers, full browsers built around an LLM as the primary user interface. They drive the lower layers as agents on behalf of real users, which is part of why Microsoft Edge and V8 quietly stopped enforcing automation-transparency flags (see the "Vendors That Wrote the Detection Rules" card). (2) Anti-detect & fingerprinting platforms as a distinct category: Multilogin, AdsPower, Kameleo. These are not scraping libraries, they are productised browser-profile managers (each profile = a coherent fingerprint with its own canvas/WebGL/timezone/IP), originally for multi-account ecommerce and affiliate work but increasingly used by serious scrapers. Worth knowing about because the techniques inside them are the same fingerprint-coherence rules this guide describes, just packaged with a UI. Some are repositioning around scraping outright: Kameleo 5.0 (2026) dropped the signup wall so you can run a few lines against its local API and test its two in-house stealth browsers on your target before creating an account, an acknowledgement that the centre of gravity for these tools has moved from multi-account management to undetected automation for scraping teams. (3) Publisher licensing & tolls, the genuinely new layer: TollBit, Cloudflare Pay-Per-Crawl, ProRata.ai. The premise: instead of fighting AI crawlers with detection, charge them per access. Publishers expose a paid API for LLMs and agents, with rate-limited free tiers and metered paid ones. This is the economic acknowledgment that AI crawlers are scrapers and that some categories of access are worth paying for. If you build agentic browser systems, this is the layer that may eventually replace bot-detection-as-defense for content-heavy sites. The detection layer is designed to tell bots from people, but the AI browsers on top and the residential networks on the bottom both look the part, so this line is blurring fast. Credit: landscape framing by Massive (Q4 2026 update).
 
 +
 Landscape / Agentic browsers
 "Browser Agent" Is Not One Product — It's 8 Layers
 Massive mapped the agentic browser infrastructure landscape for Q2 2026. The insight: most teams building AI agents think about one or two layers. The reliable ones account for all eight. When an agent fails a task, the cause is usually not the framework everyone debates — it's one of the other seven layers nobody mapped.
 💡 Every layer above the network still has to reach the live site
 The eight layers of the 2026 agentic browser stack: (1) Cloud Browser Platforms — Browserbase, Kernel, Notte, Anchor Browser, Browserless, Hyperbrowser. (2) Agent Frameworks & SDKs — Browser Use, Stagehand, Skyvern, AgentQL, Dendrite, Nanobrowser. (3) Browser Automation — Playwright, Puppeteer, Selenium, Crawlee, BrowserMCP. (4) Computer Use Agents — Anthropic CUA, OpenAI Operator, Fellou, Twin, MultiOn. (5) Stealth & Anti-Detection — CloakBrowser, Steel, Lightpanda, Pydoll, Camoufox. (6) Data Extraction & Enrichment — Diffbot, ScrapingBee, ScraperAPI, Zyte, ScrapeGraphAI. (7) LLM-Optimized Crawling — Crawl4AI, Firecrawl, Jina AI, Apify, LLMScraper, Scrapy. (8) Network & Proxy Layer — Massive, Bright Data, Oxylabs, Smartproxy, NetNut, IPRoyal. The proxy layer sits at the bottom and everything above it depends on it: a perfect agent framework with a flagged datacenter IP still fails. Most agent debugging focuses on layer 2 (the framework) when the actual failure is layer 5 (detection) or layer 8 (the IP). Map all eight before you debug one.
 
 +
 Workflow / AI-assisted recon
 Burp Suite MCP + Claude Code = Anti-Bot Recon in Minutes
 PortSwigger shipped an MCP server for Burp Suite. Point it at Claude Code and the hours-long ritual of tracing which cookie unlocks which route, when the sensor payload fires, what gets re-validated on POST — collapses into a single prompt. The bar for what counts as anti-bot recon just moved.
 💡 Build a burp-antibot-recon SKILL.md and replay it across targets
 The classic anti-bot recon workflow: capture a session through Burp, scroll the HTTP history one request at a time, manually trace cookie flows (_abck, datadome, cf_clearance, reese84), identify sensor.js challenge endpoints, figure out which requests trigger re-validation. For a moderately complex target like nike.com, this takes hours per session. With Burp's MCP server pointed at Claude Code, you capture the same session and prompt: "trace the _abck cookie lifecycle from home page through add-to-cart, identify all sensor payload endpoints, and explain the validation flow." Claude reads Burp's full history directly and produces the analysis in seconds. The pattern scales: build a reusable burp-antibot-recon Skill once, replay it across Akamai/DataDome/Cloudflare targets. If you work in this space and haven't wired it up, this is the unlock. github.com/PortSwigger/mcp-server
 
 +
 Shift · Agentic reverse engineering · 2026
 Agents Now Drive the Disassembler, Not Just Read It
 The interesting change in LLM-assisted reverse engineering is not that models suddenly read obfuscated code perfectly. It is that, wired to real tooling, they coordinate the whole pipeline: query disassemblers and decompilers, inspect cross-references, generate scripts, patch binaries, rerun analysis, and refine hypotheses in a loop. The shift is from a smart helper sitting next to the analyst to an agent driving the toolchain.
 💡 The lever is orchestration of the workflow, not raw comprehension of the code.
 
For scraping this lands squarely on the hardest target type: native mobile request-signing. The manual version of that work (open the .so in Ghidra, trace the call chain, confirm with Frida, rebuild in Python) is exactly the kind of multi-tool loop an agent can now coordinate. You point a coding agent at a target, it spins up specialist sub-agents (engines, impersonation, detection, architecture, fingerprints), runs them in parallel, then synthesises and stress-tests the result.

The honest caveat is the same one the reverse-engineering community draws: agentic workflows compress the process, they do not dissolve every defence. Some obfuscation classes (heavy virtualisation, bytecode VMs) stay resilient, which is precisely when you fall back to the oracle approach from the mobile section rather than a clean offline rebuild. Treat the agent as a force multiplier on a method you already understand, not a replacement for understanding it.

And the obfuscation side is adapting to the agents specifically. The same practitioners who teach automated deobfuscation (SMT solving, symbolic execution, MBA simplification, program synthesis to recover VM handlers and bytecode) now report protections deliberately engineered to break those pipelines: anti-agentic patterns. Analysis traps that detect a symbolic-execution or instrumentation harness and change behaviour under it, and runtime-bound semantics where a function's meaning depends on live state an offline solver cannot supply, are built to make an automated loop stall or draw a confidently wrong conclusion. The arms race did not end when agents could drive the disassembler; it moved up a level, and the counter to a trap you cannot automate around is still a human who understands what the loop was trying to do.

The defensive counterpart: adversarial, evolutionary obfuscation. The same shift explains why classic JS obfuscation is effectively dead against a capable agent. Packing a script, virtualising it, or flattening its control flow only bloats the code; a frontier model with a sandbox takes those apart in minutes, because the transformation is mechanical and therefore reversible by a mechanical process. The technique defenders are moving to instead is generative and uses the attacker's own tool as the fitness function. Rather than hiding a detection probe by wrapping it, you take a method that is API-adjacent and plausible (a dead-end bot inquiry that looks like ordinary telemetry) and have a model morph it together with the real probe you want to conceal, then point a council of analysis agents at the result and ask them to state its purpose. Whatever they correctly identify, you feed back as the next generation's target to disguise, and you repeat. After only a few rounds the reported case is that the agent council could no longer determine the exact mechanism by which detection was happening, not because the code was denser but because its observable behaviour had been evolved to read as something benign. The lesson cuts both ways for a scraper: the obfuscation you meet on a serious target is no longer a puzzle with a fixed solution your agent will grind out, it is a moving artifact shaped specifically to survive being read by an agent like yours, and the tell you are looking for may have been deliberately grown to look like innocent instrumentation.

A concrete cold-start case. A researcher pointed an agent that pairs a model with a sandboxed VM (Manus AI) at a live Akamai deployment on a real luxury store, with no prior notes and no internal wiki, and asked only that it study how the protection works, deobfuscate the client sensor, and enumerate which parameters feed the score. The agent fetched and instrumented the actual live script in its VM, then returned a structured map of the anti-hook layer, the challenge flow, and the TLS gate. The point is not a finished bypass (none was shipped) but that the expensive, human-gated part, reading minified obfuscated telemetry code and rebuilding its logic, was done by the agent running and checking its own work rather than a person spending days renaming variables.

The guardrail gap is the real story. Ask a guarded chat assistant to deobfuscate a production anti-bot sensor and enumerate its scoring signals and you hit a refusal, because that is squarely inside cybersecurity guardrails. An agent product wired to a VM took the same task and ran it. The economic consequence is what matters for the arms race: the cost that kept most protections standing was the human reverse-engineering hours, and an agent that verifies its own deobfuscation moves that gate. Defenders should now assume sensor logic is cheaper to map than it used to be, and lean harder on the layers that do not live in the client script.

Framing from "Deobfuscation in the Age of Agentic Reverse Engineering" (REcon 2026), practitioner demos of multi-agent RE pipelines, and a documented cold-start agentic mapping of a live Akamai sensor (The Web Scraping Club, Lab #108, June 2026). The agent's specific findings are reportage, not independently re-verified, and operational specifics were redacted at the source.
 
 
 +
 Tooling · Agent-native fetching · 2026
 The Browser Is Getting Lighter Because Agents Do Not Need Pixels
 A human needs a rendered page. An agent needs the meaning of the page. That gap is producing a new class of fetchers that throw away the visual rendering stack agents never use: headless engines measured in tens of megabytes instead of two hundred, and tools that hand back clean Markdown instead of a DOM you have to parse. When the consumer is an LLM, the heavy browser is mostly overhead.
 💡 Match the fetcher to the consumer. An LLM wants structure and speed, not a painted viewport.
 
Three tools sketch the direction, and the pattern matters more than any one of them:

• Obscura is a headless browser written in Rust that ships its own V8, speaks the Chrome DevTools Protocol, and runs as a single binary with no Chrome and no Node.js. Roughly 30MB resident against 200MB-plus for headless Chrome, with per-session fingerprint randomisation and a DOM-to-Markdown mode for feeding pages straight to a model. Be honest about maturity: it is an early v0.1.0 with self-reported numbers, so star it and benchmark it yourself rather than betting production on it today.

• Crawl4AI turns selected pages into clean Markdown or structured data built for agents, RAG, and pipelines. It is the extract-and-shape half of the stack.

• SearXNG is a self-hosted search layer that finds the candidate URLs in the first place. It is the discover half.

Together they describe a small, controllable agent web-context loop: discover, fetch, extract, cache, cite, with each stage owned by a light tool you can host yourself rather than a heavy browser doing all four jobs badly. The takeaway is not "switch to these tools." It is that when an agent is the consumer, the cost of rendering pixels nobody looks at is pure waste, and the tooling is starting to reflect that.

Tools surfaced publicly by practitioners in 2026: Obscura (Rust headless, Apache-2.0/MIT, early), Crawl4AI (Apache-2.0, 68k+ stars), SearXNG (self-hosted metasearch).

---

## JARGON GLOSSARY

16 Plain English
Scraping jargonin simple terms
Every term that makes scraping documentation confusing, explained with an analogy.

 
 
 Network layer
 TLS Fingerprint
 How your browser "shakes hands" when connecting securely. Chrome and Firefox shake hands differently, so a server can tell them apart before you send a single header. Analogy: recognising someone by the way they shake hands, firm, soft, awkward.
 
 
 
 Network layer
 HTTP Fingerprint
 The order and style of your HTTP headers. A bot might say "I'm Chrome" but forget to include headers Chrome always sends. Analogy: like a boarding pass, if your name and flight number don't match the expected pattern, it's suspicious.
 
 
 
 Network layer
 TCP/IP Fingerprint
 Looks at how your computer sends and receives internet packets. Windows and Linux send packets with subtle differences. Analogy: recognising someone's hometown by their accent, you didn't ask, they just gave it away by how they talk.
 
 
 
 Browser layer
 Canvas Fingerprint
 Website secretly asks your browser to draw a hidden picture. Each GPU renders it slightly differently, that difference is your fingerprint. Analogy: asking 10 artists to draw the same tree, each drawing is unique even with the same instructions.
 
 
 
 Browser layer
 WebGL Fingerprint
 Uses 3D graphics rendering to identify your GPU and driver. Same browser, different hardware, different fingerprint. Analogy: recognising a car engine by its sound, same model, but each engine has subtle variations you can hear.
 
 
 
 Browser layer
 Device Fingerprint
 Collects OS, fonts, screen size, timezone, plugins, battery level, everything about your setup combined into a unique profile. Analogy: identifying someone by their full outfit + hairstyle + voice + habits. Change one thing, the combo is still unique.
 
 
 
 Behavioural
 Behavioural Analysis
 Watching how you type, scroll, move your mouse. Bots move in straight lines at constant speed. Humans are messy and inconsistent. DataDome runs 35 behavioural signals in real-time. Analogy: a security guard watching body language, not what you say, how you move.
 
 
 
 Challenge
 Dynamic Challenges
 The website throws mini-tests to check if you're real, CAPTCHA, Turnstile, proof-of-work puzzles. Kasada changes them constantly so you can't pre-solve. Analogy: a teacher changing exam questions mid-test to catch cheaters.
 
 
 
 Network
 IP Reputation
 Whether your IP address is associated with known bots, VPNs, datacenters, or abuse. Datacenter IPs are instantly flagged. Residential IPs from real ISPs get highest trust. Analogy: your home address appearing on a blacklist, the doorman knows before you knock.
 

 
 
 Compliance signal
 robots.txt
 A text file at /robots.txt telling crawlers which paths to skip. Works on voluntary compliance only. Googlebot and GPTBot respect it. Commercial scrapers send a Chrome User-Agent and walk straight past it. Analogy: a "staff only" sign. Anyone who cares about signs obeys it. Anyone who does not care walks in anyway.

---

## KNOWLEDGE GRAPH — How Every Part of This Guide Connects

17 How this is wired
The guide as a graphbecause the links are the argument
Twenty sections and a hundred and forty field notes are a pile, not a model. What makes this subject learnable is that almost everything in it is an instance of about eighteen principles, and those principles attach to the layers a request is judged at. 130 nodes, 188 typed edges, and every section of the guide represented. The rings run outward from the spine: principles, then the layers a request is judged at, then methods, then tools and vendors, with the measured evidence and the case law on the rim. Click any node to see what it connects to, and why.

 
 
 
 Drag a node around its ring · click to inspect · chips filter
 
 Pick a node. The principles in the centre are the spine — start there for the short version of the whole guide. Rings run outward: principles, then the layers a request is judged at, then methods, tools and vendors, then the evidence and case law on the rim.

Why this exists, and it is not decoration. The same structure is written into the LLM context this site hands out, as a typed index of nodes and edges ahead of the prose. A model reading a flat list of findings cannot tell that weeks lost to the wrong variable, fifteen stealth engines with identical 403s and the Akamai case are three pieces of evidence for one claim. Given the edges, it can. One data structure generates both the picture above and the text a model reads, so they cannot drift apart.

 Text outline of the whole graph
 PrinciplesCoherence, not realism — You are not blocked for looking like a bot. You are blocked for presenting claims that cannot describe one real device. Adding more realism adds another thing that has to match.operates at → Layer 3 · JavaScript fingerprinting · operates at → Layer 1 · TLS and JA4 · is evidence for → The arms race timeline · is instance of → fingerprint-suite · is evidence for → Weeks lost to the wrong variable · is evidence for → Randomisation is unproven · is instance of → A fingerprint assembled twice disagrees with itselfThe block is at a layer you are not working on — Doubling down on the layer you enjoy is the commonest waste. Ask which layer refused you before choosing a tool.operates at → Layer 1 · TLS and JA4 · is instance of → The decision flow · is instance of → Impersonation profile sweep · is evidence for → Akamai v3: every browser failed · is evidence for → Weeks lost to the wrong variable · is limits → The web is priced, not blocked · is instance of → Read the block page, not the header · is instance of → Rate limiting is a dial, not a switch · is instance of → Ask whether the limiter counts speed or counts you · is instance of → 7,690 Hermes products, no browser, behind DataDomeNetwork identity dominates fingerprint quality — Once the IP or ASN is wrong, fingerprint quality stops being the variable. Engine choice makes no measurable difference behind a datacenter exit.operates at → Layer 5 · Network identity · is evidence for → Fifteen stealth engines, identical 403s · is evidence for → HTTP/3 is unreachable through a proxy · is evidence for → The wall is the IP, not the fingerprint · is evidence for → NetNut seized: a proxy vendor as a botnet · is evidence for → Your proxy pool has a provenance you can be caught downstream ofEvery piece of state is a claim that can be judged — Headers, cookies, tokens and fingerprints are assertions. Each can be held against you as easily as for you.operates at → Layer 2 · HTTP/2 and header order · is instance of → Session stickiness · is instance of → Scraping behind a login · is evidence for → The 429 fixed by deleting the cookie · is instance of → Treat every collected file as hostile inputThe best signals live in the seam between two layers — Each component behaves correctly on its own terms and the combination leaks. TLS versus user agent, browser versus OS, web versus app.operates at → Layer 1 · TLS and JA4 · is evidence for → Bridges to Self · is evidence for → The 100% precise check was a Chrome bugBelow the sandbox, patching stops working — Signals produced by hardware, the OS or a shared cache cannot be spoofed from JavaScript. The answer is real machines, not better patches.operates at → Layer 4 · Hardware and OS side channels · is instance of → Below the OS, attestation is not forgeable at all · is evidence for → ShaderGhost · is evidence for → Frost: SSD timing · is evidence for → WASM SIMD CPU probes · is evidence for → Math.tanh reads the host libm · is costs → Privacy bugs priced a fifth of memory bugsThe expensive failures return 200 — A block tells you it failed. Poisoned data, a different edition, or an empty container congratulate you instead.is instance of → The cheapest defence is to answer, not to refuse · is instance of → Adaptive throttling · is evidence for → A publisher serving bots a different site · is instance of → Idempotency when the caller is a machine · is instance of → Price on accepted records, not requests · is instance of → Adjudicate suspicious values with a model · is instance of → Poisoned fonts hand you different words · is instance of → 551 polls per SKU, one every 6.5 secondsAlert on the delta, not the level — Coverage measured as a level catches only zero. Compare against the last good run or you will not see a collapse.is evidence for → A citation asserts provenance, not currency · is instance of → Data quality gates · is evidence for → 92% to 61% with zero code changes · is evidence for → Ten failure modes in agent-built scrapersA row without provenance cannot be diagnosed — Source URL, fetch time, status and extractor version. Four fields, and the whole difference between a diagnosis and a re-crawl.is instance of → A citation asserts provenance, not currency · is instance of → Queueing and resumability · is instance of → Dedupe and entity resolution · is evidence for → Ten failure modes in agent-built scrapers · is evidence for → RAG is bounded by acquisition · is instance of → Provenance for the session, not just the rowCheck the instrument before trusting the verdict — If your health check is less capable than your scraper, it will confirm a wrong theory for weeks.is instance of → A performance number without a build version is a rumour · is instance of → Wire-level capture · is instance of → Failure attribution · is instance of → StealthBench · is evidence for → Weeks lost to the wrong variable · is evidence for → Ten failure modes in agent-built scrapers · is instance of → Map the API reads before reversing the VM · is instance of → Read a success rate against four choices · is instance of → A 500 is a cleaner pass signal than a 200 · is instance of → Spot-render a sample and diff the words · is instance of → Assert a budget against the real entry point · is evidence for → A raw-dump fingerprint mirrorAgent cost is how often the model looks — Not how well it thinks. Every measured win came from removing round trips and schemas, not from a better model.is instance of → Agentic browsers · is evidence for → Browser Use · is evidence for → Tool surface, not model, sets agent cost · is evidence for → Provenance for the session, not just the row · is evidence for → Keep the recipe, not the transcriptAgents cannot separate instruction from content — Raw page text enters the model as trusted input. Architectural, so not patchable, so control the action rather than the input.operates at → Layer 7 · Declared identity and policy · is instance of → Prompt injection against your own agent · is instance of → Agentic browsers · is limits → WebMCP and declared tools · is evidence for → Black Hat: every agentic browser fell · is evidence for → HTML-in-Canvas injection · is evidence for → Automated traffic is the majority · is limits → Stealth-crawler legislation · is instance of → Treat every collected file as hostile input · is evidence for → An injection is five layers, not one sentenceProtection is a rule on a route, not a property of a site — Anti-bot is configured per endpoint by people under deadline. Enumerate every route before you accept the hard one.is instance of → Endpoint enumeration · is instance of → Wire-level capture · is instance of → Mobile API interception · is evidence for → Two endpoints, one defended · is limits → Signing logic in a 4.7MB native library · is instance of → The defender cost ladder · is instance of → Faceted search is the pressure point · is instance of → Per-endpoint header rules, not per-site · is instance of → Fashion is the hardest sector to readFix the class, not the instance — Sites are instances of a smaller number of platforms. Key memory to the engine and one repair heals the whole class.is instance of → Self-healing scrapers · is instance of → Scrapling · is instance of → Accessibility-tree selectors · is instance of → Scrapy + scrapy-poetAccess was the old problem, legibility is the new one — Reaching the page was mostly solved a decade ago. Whether a model can use what you collected is the 2026 question.is instance of → Document parsing · is instance of → Scraping as an MCP server · is instance of → Retrieval pipelines · is instance of → Crawl4AI · is evidence for → RAG is bounded by acquisition · is evidence for → 268,000 requests: what agents really read · is evidence for → The largest assistants do not render JavaScript · is instance of → Route on staleness, fetch live when the index is oldPreventing is not the same as proving you prevented — A test page showing different values measures your tool. Unlinkability at the target is the only evidence that counts.is evidence for → Community and learning · is limits → fingerprint-scan.com · is evidence for → StealthBench · is evidence for → Fifteen stealth engines, identical 403s · is evidence for → Randomisation is unproven · is evidence for → A raw-dump fingerprint mirrorSpend the model once, at build time — A model in the runtime loop is a per-request bill forever. Use it to write the extractor, then run the extractor.is instance of → Browser sidecar pattern · is instance of → Cost per usable document · is limits → LLM extraction · is evidence for → 19.25 MB per page versus a direct call · is limits → Adjudicate suspicious values with a model · is instance of → Keep the recipe, not the transcript · is instance of → Warm one session, then hit the API directlyEscalate cheapest-first and stop at the first win — Every rung up the ladder costs more and breaks more often. The discipline is stopping, not climbing.operates at → Layer 1 · TLS and JA4 · is instance of → The five-tier difficulty ladder · is instance of → The escalation playbook · is instance of → Managed unblocker platforms · is instance of → TRAWL · is limits → Poisoned fonts hand you different words · is limits → A system, not a scriptA fingerprint assembled twice disagrees with itself — Identity spread across files drifts apart. One profile module everything derives from, with values computed rather than hardcoded.instance of → Coherence, not realism · operates at → Layer 3 · JavaScript fingerprinting · is evidence for → Right payload, wrong envelopeYour proxy pool has a provenance you can be caught downstream of — Residential IPs sourced from an SDK botnet are a supply-chain risk, not an ethics footnote. Ask where they come from; prefer ISP or datacenter.evidence for → NetNut seized: a proxy vendor as a botnet · evidence for → Network identity dominates fingerprint quality · instance of → Proxy sourcing and KYC riskA citation asserts provenance, not currency — A citation says a document at that URL contained the claim when it was indexed. It says nothing about whether it still does. Retrieval systems present the first as if it were the second.instance of → A row without provenance cannot be diagnosed · evidence for → Alert on the delta, not the levelThe cheapest defence is to answer, not to refuse — A block is loud and tells you to fix something. Plausible fabricated content returns 200, parses cleanly and enters the dataset. Refusing costs the defender a signal; lying costs them nothing.instance of → The expensive failures return 200A performance number without a build version is a rumour — A pass rate names the date, the exact build and the targets, or it is unusable. The tool underneath a benchmark can change without the benchmark changing.instance of → Check the instrument before trusting the verdictBelow the OS, attestation is not forgeable at all — A key held in secure hardware signs a token you cannot mint. Instrumentation reaches the call and stops. This is the one wall with no software answer.instance of → Below the sandbox, patching stops working · operates at → Layer 4 · Hardware and OS side channelsLayersLayer 0 · TCP/IP and layer 4 — SYN packet initial window, TTL, options ordering. Below the handshake, before anything you configure.judged before → Layer 1 · TLS and JA4Layer 1 · TLS and JA4 — ClientHello cipher order, extensions, GREASE, ALPN. Fires before HTML is served, which is why browser patches cannot help here.is judged before → Layer 0 · TCP/IP and layer 4 · judged before → Layer 2 · HTTP/2 and header order · is judged before → Layer 5 · Network identity · is operates at → Coherence, not realism · is operates at → The block is at a layer you are not working on · is operates at → The best signals live in the seam between two layers · is operates at → Escalate cheapest-first and stop at the first win · is operates at → Akamai Bot Manager · is operates at → Cloudflare · is operates at → Fastly Bot Management · is operates at → Impersonation profile sweep · is defeats → curl_cffi · is defeats → uTLS / Go sidecar · is defeats → primp / impit · is limits → nodriver / Patchright · is limits → mitmproxy / Burp · is limits → Cloudflare Kitesurf · is limits → Moli · is operates at → Right payload, wrong envelope · is operates at → Per-endpoint header rules, not per-site · is operates at → 7,690 Hermes products, no browser, behind DataDomeLayer 2 · HTTP/2 and header order — SETTINGS frame values, pseudo-header sequence, header ordering. A library emits a different shape from a browser.is judged before → Layer 1 · TLS and JA4 · judged before → Layer 3 · JavaScript fingerprinting · is operates at → Every piece of state is a claim that can be judged · is operates at → Akamai Bot Manager · is operates at → Fastly Bot Management · is defeats → curl_cffi · is defeats → uTLS / Go sidecar · is operates at → HTTP/3 is unreachable through a proxy · is operates at → 7,690 Hermes products, no browser, behind DataDomeLayer 3 · JavaScript fingerprinting — Canvas, WebGL, AudioContext, navigator surface, extension enumeration. Only reachable once a script runs.is judged before → Layer 2 · HTTP/2 and header order · judged before → Layer 4 · Hardware and OS side channels · is operates at → Coherence, not realism · is operates at → Akamai Bot Manager · is operates at → DataDome · is operates at → Kasada · is operates at → PerimeterX / HUMAN · is operates at → F5 Shape · is limits → Mobile API interception · is operates at → Obfuscation and deobfuscation · is operates at → Plain-English glossary · is limits → curl_cffi · is defeats → Camoufox · is defeats → nodriver / Patchright · is defeats → CloakBrowser · is defeats → fingerprint-suite · is operates at → A fingerprint assembled twice disagrees with itself · is operates at → Google Search Guard ships its own VMLayer 4 · Hardware and OS side channels — WASM SIMD CPU probes, math library results, disk and shader-cache timing. Produced below the JS sandbox.is judged before → Layer 3 · JavaScript fingerprinting · judged before → Layer 6 · Behaviour and session scoring · is operates at → Below the sandbox, patching stops working · is operates at → Below the OS, attestation is not forgeable at all · is operates at → DataDome · is limits → Camoufox · is operates at → Math.tanh reads the host libmLayer 5 · Network identity — IP, ASN, WebRTC candidates, DNS resolver, geographic coherence. Five vectors that all have to agree.judged before → Layer 1 · TLS and JA4 · is operates at → Network identity dominates fingerprint quality · is operates at → Cloudflare · is operates at → Session stickiness · is operates at → Proxy sourcing and KYC risk · is operates at → Geofeed verification · is defeats → TRAWL · is defeats → BrowserOS neo · is operates at → Price and performance barely correlate · is operates at → The wall is the IP, not the fingerprintLayer 6 · Behaviour and session scoring — Mouse curves, scroll physics, navigation paths, scored continuously across a session rather than at a challenge.is judged before → Layer 4 · Hardware and OS side channels · judged before → Layer 7 · Declared identity and policy · is operates at → Akamai Bot Manager · is operates at → Cloudflare · is operates at → DataDome · is operates at → Kasada · is operates at → F5 Shape · is operates at → Challenges and CAPTCHA · is operates at → Cloudflare Precursor scores the sessionLayer 7 · Declared identity and policy — User agent, Web Bot Auth, robots posture. Increasingly a compliance question rather than an engineering one.is judged before → Layer 6 · Behaviour and session scoring · is operates at → Agents cannot separate instruction from content · is operates at → WebMCP and declared tools · is operates at → robots.txt is a norm, not a control · is operates at → Web Bot Auth · is operates at → Personal data and GDPR · is operates at → A publisher serving bots a different site · is operates at → Crawl-to-refer ratios · is operates at → Automated traffic is the majority · is operates at → hiQ v. LinkedIn · is operates at → Google v. SerpApi · is operates at → Stealth-crawler legislation · is operates at → Pay per request at the edge (x402) · is operates at → The narrow refiling against SerpApiMethodsThe decision flow — Choose between a plain HTTP client, TLS impersonation, a stealth browser and a managed API, cheapest first.instance of → The block is at a layer you are not working on · is instance of → curl_cffiThe five-tier difficulty ladder — Place the target before you promise a deadline. Difficulty is five discrete tiers, not a feeling.instance of → Escalate cheapest-first and stop at the first win · is evidence for → The web is priced, not blockedThe escalation playbook — Walk the rungs in order and stop at the first thing that works. Most targets never need the top rung.instance of → Escalate cheapest-first and stop at the first winImpersonation profile sweep — The cheapest escalation win almost nobody does: try every TLS profile before you change tool.instance of → The block is at a layer you are not working on · operates at → Layer 1 · TLS and JA4 · is evidence for → Akamai v3: every browser failed · is evidence for → A raw-dump fingerprint mirrorEndpoint enumeration — Read the JS bundles and list every route the frontend can call, then test each cold with a plain client.instance of → Protection is a rule on a route, not a property of a site · is evidence for → Two endpoints, one defended · is instance of → Per-endpoint header rules, not per-site · is evidence for → 7,690 Hermes products, no browser, behind DataDome · is evidence for → Warm one session, then hit the API directlyWire-level capture — Record what the page fetches rather than what it renders. The listener, not the snapshot.instance of → Protection is a rule on a route, not a property of a site · instance of → Check the instrument before trusting the verdict · is instance of → mitmproxy / Burp · is instance of → powhttpMobile API interception — Apps talk to cleaner APIs than websites and often behind weaker protection. Intercept before the anti-bot.instance of → Protection is a rule on a route, not a property of a site · is evidence for → Certificate pinning and Frida · is evidence for → Native signing logic · limits → Layer 3 · JavaScript fingerprinting · is limits → Bridges to SelfCertificate pinning and Frida — Pinning stops a proxy seeing traffic. Frida unpins at runtime so the wire becomes readable again.evidence for → Mobile API interception · is defeats → FridaNative signing logic — When the signature is computed in a stripped native library, rebuild it in Python or let the app sign while you watch.evidence for → Mobile API interception · is defeats → Frida · is evidence for → Signing logic in a 4.7MB native libraryChallenges and CAPTCHA — Turnstile, reCAPTCHA, hCaptcha, proof-of-work. The visible test is the last resort, not the first line.operates at → Layer 6 · Behaviour and session scoring · is evidence for → Liveness CAPTCHA · is evidence for → Solver published as MCP toolsObfuscation and deobfuscation — Detection scripts are packed, virtualised and rotated. Reading them is a specialism of its own.operates at → Layer 3 · JavaScript fingerprinting · is evidence for → Google Search Guard ships its own VMManaged unblocker platforms — Buy the hardest tier rather than build it. The honest comparison is cost per successful request, not per request.instance of → Escalate cheapest-first and stop at the first winComputer-use agents — When the data lives behind a login the user owns, an agent operating with permission beats scraping.evidence for → Agentic browsersSession stickiness — Rotate session objects, not IP addresses. One session is one exit IP plus coherent cookies, headers and geography.instance of → Every piece of state is a claim that can be judged · operates at → Layer 5 · Network identity · is limits → The 429 fixed by deleting the cookie · is evidence for → Cloudflare Precursor scores the sessionProxy sourcing and KYC risk — What separates providers is where the IPs came from, not price per gigabyte. Your exit IP inherits its history.operates at → Layer 5 · Network identity · is limits → Price and performance barely correlate · is limits → NetNut seized: a proxy vendor as a botnet · is instance of → Your proxy pool has a provenance you can be caught downstream ofGeofeed verification — Measure the geography of your pool rather than buying the claim. Location data for most networks is self-declared.operates at → Layer 5 · Network identityFailure attribution — Log whether a failure was the proxy policy, the proxy infrastructure or the target. Otherwise you cannot argue with anyone.instance of → Check the instrument before trusting the verdict · is evidence for → Read the block page, not the header · is evidence for → Provenance for the session, not just the rowSelf-healing scrapers — Detect drift, regenerate the selector, verify against a test, ship. The instrumentation is the hard part, not the repair.instance of → Fix the class, not the instance · is evidence for → Scrapy + scrapy-poet · is evidence for → 92% to 61% with zero code changes · is limits → Ten failure modes in agent-built scrapers · is evidence for → Keep the recipe, not the transcriptBrowser sidecar pattern — Keep the browser out of the crawler. Only the requests that need one pay for one.instance of → Spend the model once, at build timeAdaptive throttling — Blocked pages return faster than real ones, so a latency-only throttle accelerates into a ban.instance of → The expensive failures return 200 · is evidence for → Conditional, incremental, deduplicated · is evidence for → Rate limiting is a dial, not a switch · is limits → 551 polls per SKU, one every 6.5 secondsScraping behind a login — The session becomes the asset. Mint it once, carefully, and replay it rather than logging in repeatedly.instance of → Every piece of state is a claim that can be judged · is limits → Ryanair v. Booking.comQueueing and resumability — A crawl that cannot resume is a crawl you will run twice. State outside the process, always.instance of → A row without provenance cannot be diagnosedData quality gates — Row counts, field fill rates, type checks and distribution drift, compared against the last good run.instance of → Alert on the delta, not the level · is evidence for → Adjudicate suspicious values with a model · is evidence for → Assert a budget against the real entry pointDocument parsing — A large share of the data you need is in PDFs and scans, where there is no DOM to select from.instance of → Access was the old problem, legibility is the new one · is limits → llms.txt drew 37 named-assistant fetches · is limits → Agent-readiness adoption is near zeroDedupe and entity resolution — The same record arrives from several routes with different keys. Resolving that is most of the pipeline.instance of → A row without provenance cannot be diagnosedCost per usable document — The only unit that matters. Retries, browsers used where a request would do, and third-party bleed are the hidden drivers.instance of → Spend the model once, at build time · is evidence for → Build versus buy · is evidence for → primp / impit · is evidence for → 19.25 MB per page versus a direct call · is evidence for → Price on accepted records, not requests · is evidence for → A system, not a scriptBuild versus buy — Compare against the fully loaded cost of your own time and failure rate, not against the sticker price.evidence for → Cost per usable documentLLM extraction — Describe the fields instead of selecting them. Reliable for the easy 20%, and the rest is engineering.limits → Spend the model once, at build time · is instance of → FirecrawlScraping as an MCP server — Name the capability and let the model call it, rather than teaching a model to drive a scraper.instance of → Access was the old problem, legibility is the new onePrompt injection against your own agent — Point an agent at the open web and the page becomes an instruction channel into your infrastructure.instance of → Agents cannot separate instruction from content · is evidence for → HTML-in-Canvas injectionRetrieval pipelines — A model can only reason over what acquisition collected. Freshness, coverage and extraction fidelity are upstream of every answer.instance of → Access was the old problem, legibility is the new oneAgentic browsers — An LLM in the decision layer, adapting where a script would break, and failing quietly where a script would fail loudly.is evidence for → Computer-use agents · instance of → Agent cost is how often the model looks · instance of → Agents cannot separate instruction from content · is evidence for → Cloudflare Kitesurf · is evidence for → Moli · is evidence for → BrowserOS neo · is evidence for → Browser UseWebMCP and declared tools — Sites handing agents a sanctioned door, which is also a public API a stranger can call with words you never wrote.limits → Agents cannot separate instruction from content · operates at → Layer 7 · Declared identity and policyPlain-English glossary — Fingerprinting, challenges, IP reputation and robots.txt explained without the jargon.operates at → Layer 3 · JavaScript fingerprintingCommunity and learning — Discords, newsletters, conferences and books. Most real technique is transmitted here rather than in documentation.evidence for → Preventing is not the same as proving you preventedThe arms race timeline — From IP bans to transformer-based behavioural scoring, and why each escalation produced the next.evidence for → Coherence, not realismrobots.txt is a norm, not a control — It was never a security mechanism. Only the crawlers that were going to behave ever read it.operates at → Layer 7 · Declared identity and policy · evidence for → hiQ v. LinkedInWeb Bot Auth — Cryptographically signed requests, so an allowlist rests on proof rather than a copyable user-agent string.operates at → Layer 7 · Declared identity and policy · is evidence for → Stealth-crawler legislation · is evidence for → Pay per request at the edge (x402)Personal data and GDPR — Public does not mean unregulated. A lawful basis is required whether or not the page was open.operates at → Layer 7 · Declared identity and policyPay per request at the edge (x402) — The edge answers 402 with its terms, the client pays, the proxy verifies before the origin runs. Web Bot Auth said who; this settles what.evidence for → Web Bot Auth · operates at → Layer 7 · Declared identity and policy · is evidence for → Automated traffic is the majority · is evidence for → Crawl-to-refer ratios · is evidence for → Blocking as a negotiating positionThe defender cost ladder — Edge WAF, ingress rate limit, cache, application modules, proof of work. The further from the app you block, the cheaper the block.instance of → Protection is a rule on a route, not a property of a site · is evidence for → Faceted search is the pressure point · is evidence for → Automated traffic is the majoritySolver published as MCP tools — akamai_solve and datadome_solve handed to the model, so the agent answers a 403 nobody pre-wired for it.evidence for → Challenges and CAPTCHA · is limits → Right payload, wrong envelopeIdempotency when the caller is a machine — An agent times out, cannot tell whether the write landed, retries, and says nothing. Idempotency-Key with a constraint, or get_or_create on the natural key.instance of → The expensive failures return 200Blocking as a negotiating position — Default crawler blocking plus per-article visibility turned access into something to license. Scarcity, not cybersecurity, is what brought AI platforms to the table.is evidence for → 227 bot visits per human visit · evidence for → Pay per request at the edge (x402) · limits → Stealth-crawler legislation · is evidence for → The obituary is four years old · is limits → Conditional, incremental, deduplicatedRead the block page, not the header — The CDN is often only the front door. One 403 header fronted DataDome once and PerimeterX another time, so the header names the door and not the decision.instance of → The block is at a layer you are not working on · limits → Cloudflare · evidence for → Failure attribution · is evidence for → Rate limiting is a dial, not a switchMap the API reads before reversing the VM — The cheapest layer of research answers which properties and methods a script reads and calls. Most write-ups skip to the bottom layer and make it look like magic.evidence for → Google Search Guard ships its own VM · instance of → Check the instrument before trusting the verdictPer-endpoint header rules, not per-site — On Google Trends a referer is required by the timeline endpoint and fatal on the regional ones, so stripping it is what gets you through.instance of → Protection is a rule on a route, not a property of a site · operates at → Layer 1 · TLS and JA4 · instance of → Endpoint enumerationPrice on accepted records, not requests — The managed-versus-custom line is who owns the gap between a fetched page and a business record you would accept. That denominator moves most vendor comparisons.evidence for → Cost per usable document · instance of → The expensive failures return 200 · is evidence for → A blended score describes no real target · is evidence for → Eighty percent is easy and nobody pays for it · is evidence for → The new half of the market skipped the hard 20%Read a success rate against four choices — Who defined success, who picked the targets, at what request rate, and whether the ranking survives per site rather than blended.instance of → Check the instrument before trusting the verdict · is evidence for → A blended score describes no real target · is evidence for → Reproducible is not disinterested · is evidence for → Price and performance barely correlate · is evidence for → A benchmark that could not fail · is evidence for → The new half of the market skipped the hard 20%A 500 is a cleaner pass signal than a 200 — A backend error means your traffic reached the application. Nobody serves an accidental stack trace to trick you.instance of → Check the instrument before trusting the verdict · is evidence for → The wall is the IP, not the fingerprintAdjudicate suspicious values with a model — Schema and completeness first, then a model judges only what survives. A financing payment read as a price passes every structural test.instance of → The expensive failures return 200 · limits → Spend the model once, at build time · evidence for → Data quality gates · catches → Poisoned fonts hand you different wordsPoisoned fonts hand you different words — GSUB substitution applied to whole words. A quarter swapped for same-class synonyms, the font drawing the originals back for the human.instance of → The expensive failures return 200 · is evidence for → 25% of words swapped broke 55.8% of passages · limits → Escalate cheapest-first and stop at the first win · is catches → Spot-render a sample and diff the words · is catches → Adjudicate suspicious values with a model · is evidence for → Bots are the majority audience for HTML · is evidence for → Over 90% of shielded pages are filtered outSpot-render a sample and diff the words — The cheap check against text poisoning. Fetch over HTTP, render a few of the same pages, compare what the words actually say.catches → Poisoned fonts hand you different words · instance of → Check the instrument before trusting the verdictRoute on staleness, fetch live when the index is old — An index has an age. Treating freshness as a runtime decision rather than a cron job is what keeps a RAG answer current.instance of → Access was the old problem, legibility is the new one · evidence for → RAG is bounded by acquisitionAssert a budget against the real entry point — A benchmark prints a number; a regression test refuses to let it change. Fixed count, real endpoint, two data sizes.instance of → Check the instrument before trusting the verdict · is evidence for → A benchmark that could not fail · evidence for → Data quality gatesTreat every collected file as hostile input — A dataset config reached code execution. Isolation, file-access limits, input validation and agent permissions, because disclosure beside a tool-using agent becomes exfiltration.instance of → Agents cannot separate instruction from content · instance of → Every piece of state is a claim that can be judgedConditional, incremental, deduplicated — 304s instead of bodies, a stored cursor instead of full sweeps. Blocked for how you behaved is harder to fix than blocked for who you were.limits → Automated traffic is the majority · evidence for → Adaptive throttling · limits → Blocking as a negotiating positionProvenance for the session, not just the row — Source, raw capture, timestamp, model and prompt version, and the step sequence. Without it the answer can only be trusted or discarded, never checked.instance of → A row without provenance cannot be diagnosed · evidence for → Failure attribution · evidence for → Agent cost is how often the model looksA system, not a script — Proxy management, concurrency, a decision about JavaScript and a fallback, and cost tracked across all of it. Orchestration and instrumentation, not a file.limits → Escalate cheapest-first and stop at the first win · evidence for → The web is priced, not blocked · evidence for → Cost per usable documentKeep the recipe, not the transcript — Distil a successful run into a reusable note with success and failure counts, and demote it when it stops working. A smaller model can then follow it.instance of → Spend the model once, at build time · evidence for → Agent cost is how often the model looks · evidence for → Self-healing scrapersAsk whether the limiter counts speed or counts you — If a slower cadence returns the same 429, backing off is not the answer and you are really facing an identity decision.limits → Rate limiting is a dial, not a switch · instance of → The block is at a layer you are not working on · is evidence for → Fashion is the hardest sector to readWarm one session, then hit the API directly — Solve the anti-bot once, post the trust tags, mint the token, reuse the session for dozens of JSON calls. The sensor cost amortises across every product.is evidence for → 7,690 Hermes products, no browser, behind DataDome · evidence for → Endpoint enumeration · instance of → Spend the model once, at build timeVendorsAkamai Bot Manager — Scores across five layers, three of them at the handshake. The _abck cookie sits at bot until sensor.js validates the session.operates at → Layer 1 · TLS and JA4 · operates at → Layer 2 · HTTP/2 and header order · operates at → Layer 3 · JavaScript fingerprinting · operates at → Layer 6 · Behaviour and session scoring · is catches → Akamai v3: every browser failed · is operates at → An unfinished sensor leaves _abck unsolvedCloudflare — Roughly a quarter of the web. Also ships Kitesurf and WebMCP, so it sells both the wall and the ladder.operates at → Layer 1 · TLS and JA4 · operates at → Layer 5 · Network identity · operates at → Layer 6 · Behaviour and session scoring · is catches → Fifteen stealth engines, identical 403s · is catches → The 100% precise check was a Chrome bug · is limits → Read the block page, not the headerDataDome — Fingerprint plus behavioural ML. Published the WASM SIMD CPU fingerprinting work.operates at → Layer 3 · JavaScript fingerprinting · operates at → Layer 4 · Hardware and OS side channels · operates at → Layer 6 · Behaviour and session scoring · is catches → WASM SIMD CPU probesKasada — Real-browser-only by design, x-kpsdk headers, challenges that change constantly.operates at → Layer 3 · JavaScript fingerprinting · operates at → Layer 6 · Behaviour and session scoring · is catches → Two endpoints, one defendedPerimeterX / HUMAN — Frequently rebranded and served first-party, so hostname checks will not identify it.operates at → Layer 3 · JavaScript fingerprintingF5 Shape — Heavy obfuscation and behavioural biometrics, usually found inside the composed stacks.operates at → Layer 3 · JavaScript fingerprinting · operates at → Layer 6 · Behaviour and session scoringFastly Bot Management — CDN-native, layered on the former Signal Sciences WAF. Standard JA3/JA4 and header-order stack.operates at → Layer 1 · TLS and JA4 · operates at → Layer 2 · HTTP/2 and header orderToolscurl_cffi — Impersonates browser TLS and HTTP/2 from Python. Runs no JavaScript, so JS challenges are where it stops. Cheapest thing to try first.defeats → Layer 1 · TLS and JA4 · defeats → Layer 2 · HTTP/2 and header order · limits → Layer 3 · JavaScript fingerprinting · instance of → The decision flowuTLS / Go sidecar — Reimplements a real browser TLS stack byte for byte. The answer when the refusal happened at the handshake.defeats → Layer 1 · TLS and JA4 · defeats → Layer 2 · HTTP/2 and header order · is evidence for → Akamai v3: every browser failedprimp / impit — Rust-cored HTTP clients with impersonation, for when volume makes Python the bottleneck.defeats → Layer 1 · TLS and JA4 · evidence for → Cost per usable documentCamoufox — Firefox with C++ level anti-detect patches. Has a real GPU context, so it passes WebGL checks Chromium forks fail.defeats → Layer 3 · JavaScript fingerprinting · limits → Layer 4 · Hardware and OS side channelsnodriver / Patchright — CDP automation without the webdriver markers. Patches the JS surface, not the network layer.defeats → Layer 3 · JavaScript fingerprinting · limits → Layer 1 · TLS and JA4CloakBrowser — Loads real extension profiles, so extension-enumeration probes return plausible answers.defeats → Layer 3 · JavaScript fingerprintingScrapling — Adaptive selectors that re-find an element after the DOM shifts under them.instance of → Fix the class, not the instancefingerprint-suite — Generates one coherent fingerprint across headers and JS APIs in a Playwright context, rather than spoofing per axis.defeats → Layer 3 · JavaScript fingerprinting · instance of → Coherence, not realismTRAWL — The escalation ladder as a product: plain HTTP, cached session, fresh solve, then residential proxy.instance of → Escalate cheapest-first and stop at the first win · defeats → Layer 5 · Network identityFrida — Runtime instrumentation that unpins certificates and hooks the signing function while the app runs.defeats → Certificate pinning and Frida · defeats → Native signing logicmitmproxy / Burp — The intercepting proxy. Two independent TLS connections, which is itself a fingerprint if you forget it.instance of → Wire-level capture · limits → Layer 1 · TLS and JA4powhttp — Wire capture aimed at scraping workflows rather than security testing.instance of → Wire-level capturefingerprint-scan.com — Splits the fingerprint hash into sub-hashes, so you can see which group of signals moved rather than only that something did.limits → Preventing is not the same as proving you preventedStealthBench — Reproducible stealth ranking against self-hosted detectors, with the harness published.evidence for → Preventing is not the same as proving you prevented · instance of → Check the instrument before trusting the verdictCloudflare Kitesurf — Agent-first engine with no Chromium. Cheap per session, and explicitly not for TLS-fingerprint challenges.limits → Layer 1 · TLS and JA4 · evidence for → Agentic browsersMoli — Rust kernel with on-demand rendering. 73 MiB against headless Chrome’s 773 MiB at level success.limits → Layer 1 · TLS and JA4 · evidence for → Agentic browsersBrowserOS neo — Local agent browser using your own Chrome sessions, with session replay. Right for logged-in work, wrong for volume.evidence for → Agentic browsers · defeats → Layer 5 · Network identityBrowser Use — Open-source agent browsing toolkit. Its own team found strong models prefer writing CDP code to reading screenshots.evidence for → Agentic browsers · evidence for → Agent cost is how often the model looksAccessibility-tree selectors — Role plus accessible name. More durable than CSS because changing it breaks the page for screen readers.instance of → Fix the class, not the instanceScrapy + scrapy-poet — Page objects separate crawl logic from extraction, which is what makes machine-authored scrapers repairable.instance of → Fix the class, not the instance · evidence for → Self-healing scrapersCrawl4AI — Built for legibility rather than access: strip the chrome and hand a model something citable.instance of → Access was the old problem, legibility is the new oneFirecrawl — URL to clean markdown as a service, with an MCP server so a model can call it directly.instance of → LLM extractionEvidencesAkamai v3: every browser failed — Seven stealth approaches all failed because the block happened before HTML was served. A Go TLS stack fixed it: 24 rpm, zero blocks in 500+ requests.evidence for → The block is at a layer you are not working on · catches → Akamai Bot Manager · evidence for → uTLS / Go sidecar · evidence for → Impersonation profile sweepFifteen stealth engines, identical 403s — Three engines passed a free detector, then all hard-403’d on live Cloudflare at the same rate. The exit IPs were datacenter.evidence for → Network identity dominates fingerprint quality · evidence for → Preventing is not the same as proving you prevented · catches → CloudflareWeeks lost to the wrong variable — Three providers swapped over a TLS-versus-user-agent mismatch, while a non-browser-like health checker reported healthy proxies as dead.evidence for → The block is at a layer you are not working on · evidence for → Check the instrument before trusting the verdict · evidence for → Coherence, not realismThe 429 fixed by deleting the cookie — Provider and pool swaps changed nothing. Removing the session cookie returned consistent 200s: the limit was per identity.evidence for → Every piece of state is a claim that can be judged · limits → Session stickinessTwo endpoints, one defended — /searches was protected. /map-listings returned the same records plus extra fields, undefended.evidence for → Protection is a rule on a route, not a property of a site · catches → Kasada · evidence for → Endpoint enumerationSigning logic in a 4.7MB native library — The signer exported only JNI_OnLoad, so static analysis stalled and the practical route was to let the app sign while watching.evidence for → Native signing logic · limits → Protection is a rule on a route, not a property of a site92% to 61% with zero code changes — A public scraper degraded over 30 days because the target changed around it. The fix was architectural, not tactical.evidence for → Self-healing scrapers · evidence for → Alert on the delta, not the levelShaderGhost — A 32-bit ID written into the GPU shader cache, readable cross-site, surviving profile wipes, on around 99% of browsers.evidence for → Below the sandbox, patching stops working · is evidence for → Privacy bugs priced a fifth of memory bugsFrost: SSD timing — Disk-subsystem latency infers activity in other tabs with no permissions, and incognito does not help.evidence for → Below the sandbox, patching stops workingWASM SIMD CPU probes — Vector instruction timing fingerprints the actual silicon, so JS-level patches cannot answer it.evidence for → Below the sandbox, patching stops working · catches → DataDomeMath.tanh reads the host libm — Transcendental results differ by operating system, and CSS trig leaks the same signal everywhere on the page.evidence for → Below the sandbox, patching stops working · operates at → Layer 4 · Hardware and OS side channelsBridges to Self — A native app listening on localhost bypasses per-origin isolation, with neither platform’s model broken alone. USENIX Security 2026.evidence for → The best signals live in the seam between two layers · limits → Mobile API interceptionA publisher serving bots a different site — The same URL returns 303 KB of HTML to a browser and 13 KB of markdown to assistant crawlers, carrying its own advertising.evidence for → The expensive failures return 200 · operates at → Layer 7 · Declared identity and policyBlack Hat: every agentic browser fell — Comet, ChatGPT Atlas and Opera all lost to indirect prompt injection. No payload to detect.evidence for → Agents cannot separate instruction from content · evidence for → HTML-in-Canvas injection · is evidence for → An injection is five layers, not one sentenceHTML-in-Canvas injection — Instructions rendered into a canvas never exist as DOM text, so text sanitisation is structurally blind to them.evidence for → Agents cannot separate instruction from content · is evidence for → Black Hat: every agentic browser fell · evidence for → Prompt injection against your own agent · is evidence for → An injection is five layers, not one sentenceTool surface, not model, sets agent cost — Five independent measurements. A ten-step flow costs ~7,000 tokens or ~114,000 depending only on interface shape.evidence for → Agent cost is how often the model looksTen failure modes in agent-built scrapers — Recon kept only HTML, tests were written by the model that wrote the extractor, coverage flagged only zero, rows carried no provenance.evidence for → A row without provenance cannot be diagnosed · evidence for → Alert on the delta, not the level · evidence for → Check the instrument before trusting the verdict · limits → Self-healing scrapersHTTP/3 is unreachable through a proxy — Configuring a proxy forces HTTP/2. Likely-bot share is 73.75% on HTTP/1.1, 26.76% on HTTP/2 and 3.33% on HTTP/3.evidence for → Network identity dominates fingerprint quality · operates at → Layer 2 · HTTP/2 and header orderThe 100% precise check was a Chrome bug — CDP clicks reported iframe-relative coordinates, real clicks main-frame-relative. Patched, and the check died with it.evidence for → The best signals live in the seam between two layers · catches → CloudflareThe web is priced, not blocked — 24,898 sites. 18.5% have no barrier, 30.7% have one, 88% stay in the two easiest tiers. The work is the hard 20%, and fashion needs real infrastructure on 57% of it.limits → The block is at a layer you are not working on · evidence for → The five-tier difficulty ladder · is evidence for → A system, not a script · is evidence for → Eighty percent is easy and nobody pays for it · is evidence for → Fashion is the hardest sector to read · is evidence for → The new half of the market skipped the hard 20%Randomisation is unproven — Watching a hash change proves nothing if the tracker identifies you on whatever stayed stable.evidence for → Preventing is not the same as proving you prevented · evidence for → Coherence, not realismPrivacy bugs priced a fifth of memory bugs — Around $1,000 for a cross-site supercookie against roughly $5,000 for a sandbox escape, so the finder is paid to keep it.evidence for → ShaderGhost · costs → Below the sandbox, patching stops workingRAG is bounded by acquisition — A vector store indexed six months ago is an LLM with a stale cutoff. Boilerplate competes for similarity with the real content.evidence for → Access was the old problem, legibility is the new one · evidence for → A row without provenance cannot be diagnosed · is evidence for → Route on staleness, fetch live when the index is oldCloudflare Precursor scores the session — Behavioural checks moved from bursts at a challenge to continuous scoring, so a refresh no longer resets you.operates at → Layer 6 · Behaviour and session scoring · evidence for → Session stickinessCrawl-to-refer ratios — Some AI crawlers take thousands of pages per visitor returned, which is the economic argument behind publisher blocking.operates at → Layer 7 · Declared identity and policy · evidence for → Pay per request at the edge (x402) · is evidence for → 227 bot visits per human visitAutomated traffic is the majority — Past half of all web traffic, and a growing share is an agent acting for a real person who is genuinely the audience.operates at → Layer 7 · Declared identity and policy · evidence for → Agents cannot separate instruction from content · evidence for → Pay per request at the edge (x402) · evidence for → The defender cost ladder · is evidence for → 227 bot visits per human visit · is evidence for → Bots are the majority audience for HTML · is limits → Conditional, incremental, deduplicated · is evidence for → 551 polls per SKU, one every 6.5 seconds19.25 MB per page versus a direct call — One measured comparison of a full browser load against calling the API the page itself calls.evidence for → Spend the model once, at build time · evidence for → Cost per usable documentLiveness CAPTCHA — Hand-gesture challenges arrive because behavioural scoring hit a ceiling, and they are answerable with a virtual camera.evidence for → Challenges and CAPTCHA268,000 requests: what agents really read — Software beat humans 2:1. ChatGPT-User took markdown 0.1% of the time, Claude Code 76%. Segment by client or the average describes nobody.evidence for → Access was the old problem, legibility is the new one · is evidence for → llms.txt drew 37 named-assistant fetches · is evidence for → Agent-readiness adoption is near zero · is evidence for → The largest assistants do not render JavaScriptllms.txt drew 37 named-assistant fetches — About 660 direct fetches, but almost all from search crawlers. A hidden markdown link recorded zero hits across 268,000 requests.limits → Document parsing · evidence for → 268,000 requests: what agents really readAgent-readiness adoption is near zero — robots.txt on 78% of sites, declared AI preferences on 4%, markdown negotiation on 3.9%, MCP Server Cards on fewer than fifteen sites.evidence for → 268,000 requests: what agents really read · limits → Document parsingFaceted search is the pressure point — Every filter, sort and page state is its own URL, so walking combinations forces uncached application work at volume.evidence for → The defender cost ladder · instance of → Protection is a rule on a route, not a property of a siteRight payload, wrong envelope — Solving server-side submits the container TLS fingerprint, so a challenge verifying its own submitter rejects a correct answer.limits → Solver published as MCP tools · operates at → Layer 1 · TLS and JA4 · evidence for → A fingerprint assembled twice disagrees with itself227 bot visits per human visit — One publisher quarter. Crawling rose 18% year on year overall, GPTBot 305% and ChatGPT-User 2,825%, moving GPTBot from ninth crawler to third.evidence for → Crawl-to-refer ratios · evidence for → Automated traffic is the majority · evidence for → Blocking as a negotiating position · is evidence for → The obituary is four years oldGoogle Search Guard ships its own VM — First-visit signals with no cookies are assembled inside a virtual machine and settle into SG_SS. Map which APIs the script touches before attacking the VM.operates at → Layer 3 · JavaScript fingerprinting · evidence for → Obfuscation and deobfuscation · is evidence for → Map the API reads before reversing the VMPrice and performance barely correlate — 0.25 correlation on Amazon. Among providers under the same latency bar, $19 against $300 for the same threshold, and the dearest was not the fastest.evidence for → Read a success rate against four choices · operates at → Layer 5 · Network identity · limits → Proxy sourcing and KYC riskA blended score describes no real target — Shein averaged 21.88% and G2 36.63% across eleven APIs. One API fell 84.47% to 72.98% on request rate alone.evidence for → Read a success rate against four choices · evidence for → Price on accepted records, not requestsReproducible is not disinterested — Harness, targets, pass criteria and adapters published so anyone can re-run it, and still authored by the vendor it ranks first.evidence for → Read a success rate against four choicesThe obituary is four years old — Search interest peaked in 2026. Models did not replace collection, they became its largest client, on both training and inference.evidence for → 227 bot visits per human visit · evidence for → Blocking as a negotiating positionThe wall is the IP, not the fingerprint — An engine scoring 94/100 still needed reloads from clean residential addresses. Reputation decided access, not fingerprint quality.evidence for → Network identity dominates fingerprint quality · operates at → Layer 5 · Network identity · evidence for → A 500 is a cleaner pass signal than a 200 · is limits → An unfinished sensor leaves _abck unsolvedAn unfinished sensor leaves _abck unsolved — A minimal engine stalls on heavy scripts, marking the second segment with -1 and returning 403 where a real browser does not wobble.operates at → Akamai Bot Manager · limits → The wall is the IP, not the fingerprintThe largest assistants do not render JavaScript — ChatGPT, Claude and Gemini fetch HTML and stop, while DeepSeek and Mistral render. Client-side content is absent to the first group.evidence for → 268,000 requests: what agents really read · evidence for → Access was the old problem, legibility is the new one25% of words swapped broke 55.8% of passages — Grammatical substitutes rather than garbage, so nothing downstream trips. Meaning failure in over half of news text tested.evidence for → Poisoned fonts hand you different words · is evidence for → Over 90% of shielded pages are filtered outA benchmark that could not fail — The optimisation it existed to prove was deleted and everything still passed, because the test built its own query instead of driving the app.evidence for → Assert a budget against the real entry point · evidence for → Read a success rate against four choicesBots are the majority audience for HTML — Roughly 57 to 58% of HTML requests by one tracker and about 53% by another, and the version served to machines is the clean one.evidence for → Automated traffic is the majority · evidence for → Poisoned fonts hand you different wordsOver 90% of shielded pages are filtered out — Against a training-set quality filter, most poisoned text never enters the corpus. The survivors carry false meaning in about 19.4% of content.evidence for → Poisoned fonts hand you different words · evidence for → 25% of words swapped broke 55.8% of passagesRate limiting is a dial, not a switch — 429 after eleven requests at one every 1.2 seconds, a fifteen-minute lockout, and polling returns the original countdown rather than extending it.evidence for → Adaptive throttling · instance of → The block is at a layer you are not working on · evidence for → Read the block page, not the header · is limits → Ask whether the limiter counts speed or counts youEighty percent is easy and nobody pays for it — Difficulty is concentrated, not spread. The paid work lives in the closing fifth, and which fifth depends on the sector rather than the technology.evidence for → The web is priced, not blocked · evidence for → Price on accepted records, not requests · is evidence for → Fashion is the hardest sector to readFashion is the hardest sector to read — 2.86 of five against a 1.58 average. Rate limiting on 54%, TLS fingerprinting on 32%, and CAPTCHA on only 15% because it costs sales.evidence for → The web is priced, not blocked · evidence for → Eighty percent is easy and nobody pays for it · instance of → Protection is a rule on a route, not a property of a site · evidence for → Ask whether the limiter counts speed or counts you551 polls per SKU, one every 6.5 seconds — 91% bot traffic on DDR5 pages, cache-busting every request to force origin. A real-time inventory feed, not catalogue scraping.limits → Adaptive throttling · evidence for → Automated traffic is the majority · instance of → The expensive failures return 200An injection is five layers, not one sentence — A fake boundary, a hidden image, a spoofed reply, a fake command, and padding. Each innocuous alone, together an instruction set.evidence for → Agents cannot separate instruction from content · evidence for → Black Hat: every agentic browser fell · evidence for → HTML-in-Canvas injection7,690 Hermes products, no browser, behind DataDome — Three signals carried it: a Chrome TLS fingerprint, exact header order, and a DataDome token. 37 KB per product against a page a hundred times heavier.instance of → The block is at a layer you are not working on · evidence for → Endpoint enumeration · evidence for → Warm one session, then hit the API directly · operates at → Layer 1 · TLS and JA4 · operates at → Layer 2 · HTTP/2 and header orderThe new half of the market skipped the hard 20% — AI-native retrieval inherited the reachable web. Its search falls over on a hardened target, because access was the part everyone assumed was solved.evidence for → Price on accepted records, not requests · evidence for → The web is priced, not blocked · evidence for → Read a success rate against four choicesA raw-dump fingerprint mirror — Glassbox shows every signal a tracker reads, client-side, with an identifiability estimate. Read your own surface rather than a pass or fail.evidence for → Check the instrument before trusting the verdict · evidence for → Preventing is not the same as proving you prevented · evidence for → Impersonation profile sweepLawshiQ v. LinkedIn — Scraping public data is not unauthorised access under the CFAA. Ninth Circuit, reaffirmed 2022.operates at → Layer 7 · Declared identity and policy · is evidence for → Van Buren v. United States · is evidence for → Meta and X Corp v. Bright Data · is evidence for → X Corp v. CCDH · is limits → Ryanair v. Booking.com · is limits → Google v. SerpApi · is evidence for → robots.txt is a norm, not a control · is limits → The narrow refiling against SerpApiVan Buren v. United States — "Exceeds authorized access" reaches only areas off limits to you, not permitted areas used for a disfavoured purpose.evidence for → hiQ v. LinkedInMeta and X Corp v. Bright Data — Both failed. Contract and CFAA theories against public-data collection keep losing.evidence for → hiQ v. LinkedInX Corp v. CCDH — Public-interest research survived a hostile platform. Read with Sandvig v. Barr.evidence for → hiQ v. LinkedInRyanair v. Booking.com — The counterweight: a logged-in scrape against accepted terms is a different question from a public one.limits → hiQ v. LinkedIn · limits → Scraping behind a loginGoogle v. SerpApi — Live. Argues anti-bot is a technological protection measure, so bypassing it is DMCA circumvention rather than unauthorised access.limits → hiQ v. LinkedIn · operates at → Layer 7 · Declared identity and policy · is evidence for → Data-broker non-compliance · is evidence for → The narrow refiling against SerpApiStealth-crawler legislation — H.R. 9915, the Stealth Bot Prohibition Act, plus a New York statute: prohibit bots that mask identity, moving disclosure from engineering to compliance.limits → Agents cannot separate instruction from content · operates at → Layer 7 · Declared identity and policy · evidence for → Web Bot Auth · is limits → Blocking as a negotiating positionData-broker non-compliance — 32 registered brokers sell to generative AI developers and nobody knows which. The licensed market is less inspectable than open collection.evidence for → Google v. SerpApiThe narrow refiling against SerpApi — Broad claim dismissed, copyright theory refiled. To win, a results page must be argued to contain protected work.evidence for → Google v. SerpApi · operates at → Layer 7 · Declared identity and policy · limits → hiQ v. LinkedInNetNut seized: a proxy vendor as a botnet — A NASDAQ-listed residential provider whose pool was sourced from malware on two million devices. The stock fell 96% from peak.evidence for → Network identity dominates fingerprint quality · limits → Proxy sourcing and KYC risk · is evidence for → Your proxy pool has a provenance you can be caught downstream of

---

## FAQ — Straight Answers to 22 Common Questions

18 Straight answers
The questions Iactually get asked
Twenty-two of them, answered in a paragraph each. Every answer points at a section above where the long version lives.

 
 What is the best library for bypassing Cloudflare in 2026?
 Camoufox is the strongest open-source option for bypassing Cloudflare in 2026, achieving a 100% pass rate in March 2026 benchmarks. It patches Firefox at the C++ level using Mozilla's Juggler protocol, making it undetectable via JavaScript inspection. For HTTP-only scraping, curl_cffi with impersonate='chrome131' handles most Cloudflare targets without a full browser.
 
 
 What is JA4+ TLS fingerprinting and how does it affect web scraping?
 JA4+ is a TLS fingerprinting standard that identifies scrapers before any HTTP headers are exchanged. It hashes the TLS ClientHello fields (cipher suites, extensions, ALPN) in a sort-stable way that survives Chrome's extension order randomisation. Cloudflare deploys JA4 in a Rust crate at CDN edge, Akamai in an EdgeWorker. Python's requests library has a unique JA4 hash that gets blocked instantly. The fix is curl_cffi, which impersonates real Chrome TLS down to HTTP/2 SETTINGS frames.
 
 
 How do you bypass Akamai Bot Manager in 2026?
 Akamai Bot Manager in 2026 probes 60 Chrome extension URLs via fetch() to detect headless browsers. Real Chrome always has at least a few extensions installed. LinkedIn does a particularly aggressive version of this — they probe for Grammarly, 1Password, and other popular extensions by fetching resources at known extension paths. Your scraper never touches the DOM, but the DOM tells on you anyway. The fix: use CloakBrowser (loads real extension profiles) or Camoufox (uses Juggler protocol, no CDP artifacts). Combine with residential or ISP proxies since Akamai flags datacenter ASNs instantly. Set geoip=True in Camoufox to align WebRTC, DNS, and timezone with your proxy exit country.
 
 
 What is the difference between residential and datacenter proxies for web scraping?
 Datacenter proxies are fast and cheap but carry known ASNs (AWS AS16509, GCP AS15169) that anti-bots flag immediately. Residential proxies route through real ISP connections, making traffic look like genuine users. In 2026, most protected sites block datacenter IPs outright. ISP proxies (static residential) offer the best of both: residential IP authority with datacenter speeds. Use datacenter for unprotected APIs, ISP for medium targets, rotating residential for the hardest targets like Cloudflare-protected e-commerce.
 
 
 How do you scrape JavaScript-rendered websites with Python in 2026?
 For JavaScript-rendered websites in 2026, use Camoufox (Python, patches Firefox at C++ level, bypasses Cloudflare), PatchRight (undetected Playwright drop-in, bypasses Kasada), or scrapy-stealth middleware (adds TLS fingerprinting and browser engine to Scrapy). For AI-powered extraction, Crawl4AI (60K stars) and Firecrawl (111K stars) convert pages to clean Markdown. Avoid plain Playwright without stealth patches — navigator.webdriver=true is trivially detected by all major anti-bots.
 
 
 What is curl_cffi and why is it better than requests for web scraping?
 curl_cffi is a Python library that wraps libcurl with BoringSSL patches to produce exact Chrome and Firefox TLS fingerprints. Unlike Python requests (which has a unique JA4 hash that anti-bots recognise instantly), curl_cffi sends a ClientHello identical to a real browser including HTTP/2 SETTINGS frames. It is 10-50x faster than browser automation and works as a drop-in requests replacement: curl_cffi.requests.get(url, impersonate='chrome131').
 
 
 How do I intercept mobile app API traffic for scraping?
 To intercept mobile app API traffic: install Android Studio and create a virtual device with API 30+, root it using rootAVD and Magisk, install HTTP Toolkit to intercept HTTPS traffic and bypass SSL pinning automatically. Once you capture the API request, replicate it with curl_cffi for production scraping. Mobile APIs serve the same data as the website but with far weaker anti-bot protection — no Cloudflare, no JA4 fingerprinting.
 
 
 What is the best Scrapy anti-bot middleware in 2026?
 scrapy-stealth is the most complete Scrapy anti-bot middleware in 2026. It adds TLS fingerprint spoofing, HTTP/2 impersonation, proxy rotation, fingerprint cycling, and a real browser engine via CDP — all unavailable in scrapy-playwright, scrapy-splash, or scrapy-selenium. It supports per-request engine switching via request.meta, so easy URLs use the fast HTTP engine while protected pages use the browser engine. Install with: pip install scrapy-stealth
 
 
 Why does nodriver beat patched Playwright forks for anti-bot bypass?
 The difference is automation-protocol fingerprinting, not fingerprint patches. Patched Chromium forks still drive the browser over the Chrome DevTools Protocol (CDP), which leaves detectable traces in how the browser is controlled. nodriver avoids the standard CDP automation surface, so on targets that fingerprint the automation protocol itself (rather than navigator properties), it passes where a heavily patched fork is still blocked. In a 7-tool benchmark across 31 protected targets, nodriver was the only tool with zero blocks.
 
 
 What is fingerprint harvesting and why does it matter for scraping in 2026?
 Fingerprint harvesting is a commercialised practice where real browser fingerprints are collected from genuine users and replayed against anti-bot systems. Tools embed JavaScript on real sites to capture authentic device profiles (canvas, WebGL, TLS, audio) and sell them for injection into automated sessions. The consequence for scrapers: when a stealth browser passes a canvas probe, it may be replaying a real harvested hash rather than spoofing one. Anti-bot vendors respond by adding replay-resistant signals like WASM SIMD CPU timing that require genuine hardware.
 
 
 How do you scrape a mobile app when the request signature is in native code?
 When an app signs its API requests inside a compiled native .so library, interception and a Java decompiler (JADX) stop at the native keyword. There are two approaches. If the native library is readable crypto, reverse it with Ghidra and reimplement the signing in Python to sign offline at any volume with no app in the loop. If it is a bytecode virtual machine you cannot practically rewrite, keep the app running and drive its own signer as an oracle your scraper calls. The toolchain is androguard for recon, JADX for the managed layer, Ghidra for native code, and Frida to confirm behaviour live.
 
 
 Are AI agents replacing web scrapers in 2026?
 No. AI agents that browse the web are themselves scrapers: they make HTTP requests, hit anti-bot walls, get rate limited, and need proxies and believable fingerprints. The only thing that changed is who writes the prompt. Teams shipping agents bought more proxy and unblocking infrastructure through 2025, not less, because every agent loop ends at a server that would rather not serve it. The detection and bypass techniques used for traditional scraping apply directly to agentic browsing.
 
 
 Why is my scraper still getting 429 errors after changing proxy provider?
 Because the rate limit may be keyed to your session, not your IP. A request with no cookies is anonymous and carries no history; a request with a session cookie carries an identity, and identities accumulate a reputation and a per-identity budget that can be exhausted. Before scaling infrastructure around the limit, send the same request with all state removed. If the anonymous request succeeds where the identified one fails, you were telling the server exactly who to rate limit.
 
 
 Does using a proxy prevent HTTP/3, and does that matter for scraping?
 Yes. HTTP/3 runs over QUIC, which runs over UDP, while HTTP proxies tunnel TCP only. SOCKS5 defines a UDP ASSOCIATE command but browsers do not expose it, so Chrome is TCP-only over SOCKS5 in practice and negotiates HTTP/2 the moment a proxy is configured. It matters because Cloudflare Radar puts likely-bot traffic at 73.75% on HTTP/1.1, 26.76% on HTTP/2 and 3.33% on HTTP/3, so a proxied browser sits on the wrong side of a hardening signal. The options are RFC 9298 UDP proxying (CONNECT-UDP), a network-layer VPN tunnel that carries UDP, or a provider that has implemented HTTP/3 proxying.
 
 
 What is machine identity in web scraping?
 Machine identity is the combination of signals a site uses to recognise and evaluate an automated visitor: IP reputation, browser fingerprint, TLS characteristics, cookies, headers, device information, session behaviour and geographic consistency, judged together rather than individually. It is why rotating IPs alone stopped working: you get blocked not for looking like a bot but for presenting a set of claims that cannot describe one real device.
 
 
 Can I use the accessibility tree instead of CSS selectors for scraping?
 Often yes, and it is more durable. A hashed class name like css-1x7bhq9 is a build artifact that disappears on the next bundle, while an element's role and accessible name are semantic contracts that cannot quietly change without breaking the page for screen readers. Playwright exposes this directly with getByRole and get_by_role. Coverage on the open web is patchy so keep a CSS fallback, but on large commercial sites and anything that has been through a compliance review the role and name are usually present.
 
 
 Can an AI agent write a production web scraper?
 It can write the code well and the instrumentation badly, and the instrumentation is the part you live with. Reading a full agentic scraping toolchain end to end surfaced ten repeatable failure modes, including recon that keeps only the HTML and discards every JSON response, tests written by the same model that wrote the extractor, coverage checks that only flag fields at zero percent, rows carrying no source URL or fetch time, and a background watcher that reports a job as still running when it never reached the API.
 
 
 Why do some websites serve different content to AI crawlers?
 Because the user agent is becoming a commercial identity rather than just a client string. TIME.com answers the same article URL with roughly 303,000 bytes of HTML for a browser and about 13,000 bytes of markdown for ClaudeBot, PerplexityBot and OAI-SearchBot, refuses GPTBot with a 406, and embeds sponsored blocks and impression-counting headers in the machine version. For a scraper this is dangerous precisely because it is not a block: the status is 200, the parse succeeds and the record count looks plausible, but the document is a different edition of the page.
 
 
 Why can't Claude or ChatGPT reach my page even though it is public?
 Claude's fetch tool is provenance-gated: it can only retrieve URLs that have already appeared in the conversation context, such as user messages, prior search results or prior fetch results, and it cannot follow a URL it constructed itself. A relative link inside HTML it already fetched is not enough, and neither is an llms.txt or a markdown version. That makes the search index the gatekeeper rather than your link graph, which for Claude means Brave Search. The design is an exfiltration defence, so it is not going to be relaxed.
 
 
 What is a self-healing scraper and how does it work?
 A self-healing scraper detects that extraction has drifted, regenerates the broken selector, verifies the fix against a test, and ships it without a human writing code. The parts that make it work in production are drift detection on item counts and field fill rates compared with the last good run rather than against zero, a repair path that fixes one field instead of rewriting the project, provenance on every row so you can tell when the break started, and a context-blind verifier that judges the output rather than the process that produced it.
 
 
 How do you detect which platform or CMS a website is built on?
 Score signals by how expensive they are to customise, and never detect on anything a theme can change. Platform-set cookie names, endpoints that ship regardless of theme, asset path conventions and response header quirks all survive a redesign; generator meta tags are worth including but weighting lowest, because they are the first thing an agency strips. Keep it scored rather than binary and store the confidence next to the label so a weak classification is visible rather than silently wrong.
 
 
 What is WebMCP and does it replace web scraping?
 WebMCP lets a website declare structured tools that a browser agent can discover and call, and Cloudflare now injects the bridge for any site on its network. It does not replace scraping. The action layer usually proxies to an MCP server the site still has to build, the tools only activate for an agent actually driving a browser, and the declared tool surface and the rendered HTML are maintained by different code and can disagree. Check for a tool surface before writing a selector, then diff what it returns against the page at least once.

---

*Context extracted from web-scraping-guide.com — Asad Ikram's complete 2026 web scraping guide.*
*Free, no signup, no paywall. Single HTML file on GitHub Pages.*
