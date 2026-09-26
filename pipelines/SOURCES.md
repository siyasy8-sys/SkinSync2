# Data sources and licenses

Every source must be recorded here **before** it is ingested (see CLAUDE.md → Data and licensing).
Raw responses are cached under `data/raw/<source>/<snapshot-date>/` (git-ignored), so re-ingesting reads the cache instead of calling the source again.

| Source | License | Stored | Access | Snapshot |
| --- | --- | --- | --- | --- |
| EU CosIng | CC BY 4.0 (Commission reuse policy) | Ingredient metadata | CosIng site's search API, about 1 req/s | 2026-09-26 |
| Open Beauty Facts | ODbL 1.0 (database), DbCL 1.0 (contents) | Product metadata + ingredient text | Daily JSONL dump (streamed sample) | 2026-09-26 |
| PubMed Central OA subset | Per article; we keep CC BY and CC0 only | Metadata + abstract (full text later) | NCBI E-utilities | 2026-09-26 |

Not yet recorded, so not yet allowed: PubChem and the FDA OTC monographs.

---

## EU CosIng (cosmetic ingredients)

- **Publisher:** European Commission, DG GROWTH. <https://ec.europa.eu/growth/tools-databases/cosing/>
- **License:** The Commission's [legal notice](https://commission.europa.eu/legal-notice_en) says that EU-owned content on Commission websites is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) unless stated otherwise (Commission Decision 2011/833/EU on reuse of Commission documents). CosIng carries no separate notice.
- **Attribution:** "Contains data from the EU CosIng database, © European Union, CC BY 4.0. Modified: fields selected and normalized."
- **Legal status caveat:** CosIng "has informative purpose and no legal value". Being listed does not mean an ingredient is authorized; only Regulation (EC) No 1223/2009 and its annexes set regulatory status. Answers must not present CosIng data as regulatory advice.
- **Access method:** There's no bulk download (the site only exports single-ingredient PDFs). The loader calls the same JSON search API that the CosIng web app uses. Its endpoint and public API key are published in the site's `assets/env-json-config.json`.
  - This endpoint is **undocumented** and may change without notice. If it breaks, the fallback is a published mirror of the old CosIng CSV export, with its provenance recorded here.
  - Before using it (2026-09-26) we checked for restrictions on automated access:
    - The `ec.europa.eu/robots.txt` has no rules for `/growth/`.
    - The API hosts (`webgate.ec.europa.eu`, `api.tech.ec.europa.eu`) serve no robots.txt.
    - The legal notice doesn't restrict automated access.
  - Requests are rate-limited to about 1 per second and identify the client with a `SkinSync/0.1` User-Agent.
- **Stored:** substance ID, INCI name, CAS number, functions, and raw restriction fields (annex, max concentration, conditions).

## Open Beauty Facts (products)

- **Publisher:** Open Food Facts association. <https://world.openbeautyfacts.org/data>
- **License:**
  - Database: [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  - Individual contents: [Database Contents License (DbCL) 1.0](https://opendatacommons.org/licenses/dbcl/1-0/).
  - Product images are CC BY-SA, but **we don't use images**.
- **Attribution:** "Product data from Open Beauty Facts (openbeautyfacts.org), available under the Open Database License."
- **ODbL obligations:**
  - Attribute Open Beauty Facts wherever product data is shown.
  - If we **publicly distribute** a derived database (for example, a dump of our `products` table), it must be offered under ODbL. Share-alike applies to the database, not to the application code.
  - Showing query results through the API counts as a "produced work", which only needs attribution.
- **Access method:** OBF's rules say API calls must correspond to real user scans, and bulk scraping of the API is blocked. So we read the official daily dump (`https://static.openbeautyfacts.org/data/openbeautyfacts-products.jsonl.gz`) as a stream and stop after the sample limit, without downloading the whole file. Only the sampled records are cached.
- **Stored:** barcode, name, brand, category, raw ingredient text.

## PubMed Central open-access subset (papers)

- **Publisher:** NCBI / U.S. National Library of Medicine. <https://pmc.ncbi.nlm.nih.gov/tools/openftlist/>
- **License:** varies **per article**; each article's own license statement governs reuse.
  - We only ingest articles licensed **CC BY** (any version) or **CC0**. The search uses PMC's `"open access"`, `"cc by license"` and `"cc0 license"` filters.
  - The loader also re-checks each article's `<license>` element and skips anything else, including CC BY-NC, CC BY-ND and publisher-specific licenses.
- **Attribution:** every stored document keeps its title, URL (`https://pmc.ncbi.nlm.nih.gov/articles/PMC…/`) and license string. Answers that cite a document show its title and link.
- **Access method:** [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25497/), one of PMC's four approved retrieval services. Bulk retrieval by any other automated process is prohibited.
  - Requests send the `tool=skinsync` parameter, plus the `email` parameter taken from the `NCBI_EMAIL` env var.
  - They're paced at 2.5 req/s (8 with an `NCBI_API_KEY`), just under NCBI's 3 and 10 req/s limits, and 429/5xx responses are retried with backoff.
- **Stored:**
  - Week 1: metadata and abstract, with `documents.full_text` left NULL.
  - The full JATS XML is cached locally, so a later milestone can fill `full_text` for these same CC BY/CC0 articles without calling NCBI again.
- **PubMed abstracts outside PMC OA:** not ingested. These abstracts may be copyrighted by their publishers.
