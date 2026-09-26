# Data notes

Findings about the source data that affect later milestones. Newest first within each section.

## Week 1 findings (2026-09-26)

### 1. Loose PMC matches: addressed in `week1-followups`
The Week 1 query (`"<seed>"[tiab] AND skin AND <license filter>`) matched "skin" anywhere in the article, so off-topic papers got in. Examples: corneal healing with D-panthenol, and skin flaps treated with azelaic acid.

**Fix:** the seed, a skin term (skin/cutaneous/dermal/epidermal/facial) and a dermatology/cosmetic term (dermatolog\*, cosmetic\*, topical\*, acne, photoaging, sunscreen\*, …) must all appear in the title or abstract. The term lists are `pmc_skin_terms` and `pmc_topic_terms` in `app/config.py`. CC BY/CC0 open-access hit counts before → after:

| Seed | Before | After |
| --- | --- | --- |
| niacinamide | 107 | 62 |
| retinol | 503 | 53 |
| ascorbic acid | 1533 | 103 |
| salicylic acid | 456 | 65 |
| glycolic acid | 766 | 32 |
| hyaluronic acid | 3001 | 393 |
| sodium hyaluronate | 174 | 12 |
| azelaic acid | 97 | 43 |
| benzoyl peroxide | 78 | 39 |
| zinc oxide | 982 | 66 |
| titanium dioxide | 603 | 46 |
| ceramide np | 8 | 4 |
| panthenol | 30 | 12 |
| tocopherol | 689 | 44 |
| bakuchiol | 35 | 11 |

A spot check of random titles after the change found them on-topic (acne, sunscreens, hyperpigmentation). A few are still loosely related, e.g. "Topical Nitric Acid Burns". Week 3 retrieval eval will show whether more tightening is needed.

### 2. CosIng CAS numbers are messy: parse them in Week 2
`ingredients.cas_number` is stored raw. It can hold several numbers with free-text notes. For example, TOCOPHEROL is `54-28-4 (gamma)/ 16698-35-4(beta) / 10191-41-0(DL) / 119-13-1 / …` (9 numbers), and CERAMIDE NP is `34354-88-6/100403-19-8 (generic)`. Separators vary (`/`, ` / `). Splitting and normalizing CAS numbers is part of entity resolution, and they're useful for matching against PubChem.

### 3. CosIng functions are thin
CosIng function tags are regulatory categories, not descriptions of what an ingredient does for skin. For example, NIACINAMIDE has only `SMOOTHING`, and AZELAIC ACID has `BUFFERING, FRAGRANCE`. "What does X do?" answers must come from the paper evidence, not from `ingredients.functions`.

### 4. Open Beauty Facts products are mostly non-English, with uneven seed coverage
In Week 1's first 300 skincare products, most were French. Seed coverage was uneven: 36 mentioned hyaluronic acid, 4 niacinamide and 0 retinol. INCI ingredient names are language-neutral, so this mostly affects product names, not ingredient parsing.

**Addressed in `week1-followups`:** OBF is now a seeded sample, with each seed in ≥10 products where available and English names first (see coverage below). Across the whole dump, 2,257 of 76,126 products pass the skincare filter, and 1,096 of those are English. "English" means `lang == "en"` or a non-empty `product_name_en`. That's a proxy, so a few French-named products still count as English (e.g. "lait après soleil").

## Sample coverage (2026-09-26)

Per seed active, after `pipelines.ingest pmc/obf --refresh --prune`. Papers are CC BY/CC0 PMC articles among each seed's top 20 relevance-ranked hits; a paper found by two seeds counts for both. Products are counted by whole-word match of the seed in the ingredient text. That's a sampling heuristic, not entity resolution.

| Seed active | Papers | Products |
| --- | --- | --- |
| NIACINAMIDE | 20 | 27 |
| RETINOL | 20 | 11 |
| ASCORBIC ACID | 20 | 12 |
| SALICYLIC ACID | 20 | 20 |
| GLYCOLIC ACID | 20 | 10 |
| HYALURONIC ACID | 20 | 12 |
| SODIUM HYALURONATE | 12 | 57 |
| AZELAIC ACID | 20 | **7** ⚠ |
| BENZOYL PEROXIDE | 20 | **2** ⚠ |
| ZINC OXIDE | 20 | 10 |
| TITANIUM DIOXIDE | 20 | 41 |
| CERAMIDE NP | **4** ⚠ | 11 |
| PANTHENOL | 12 | 43 |
| TOCOPHEROL | 20 | 104 |
| BAKUCHIOL | 11 | **7** ⚠ |

Totals: 228 unique papers (218 CC BY 4.0, 6 CC BY 3.0, 4 CC BY 2.0) and 300 products (295 with English names).

⚠ **Under 10:** these are source limits, not sampling bugs. Only 4 CC BY/CC0 papers match ceramide NP under the tightened query. The whole OBF dump has only 7 skincare products with azelaic acid, 2 with benzoyl peroxide and 7 with bakuchiol, and the sample takes all of them. Benzoyl peroxide and azelaic acid are mostly sold as OTC or prescription drugs, which OBF doesn't cover well. Expect weak product-suitability answers for these.

## Pipeline incident: NCBI error bodies with HTTP 200 (2026-09-26)
During the resample, NCBI answered one esearch (zinc oxide) with HTTP 200 and the body `{"esearchresult": {"ERROR": "Search Backend failed ... 502"}}`. The loader cached that response and read it as zero hits, and the `--prune` still ran. It deleted Week 1 zinc oxide papers that the rerun then re-inserted.

**Fix:** `RawCache.fetch` now takes a validator. A bad fresh payload is never cached, and a bad cached payload is refetched. esearch, efetch and CosIng responses are validated, and a failed esearch counts as a fetch error, which blocks `--prune`.
