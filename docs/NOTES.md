# Data notes

Findings about the source data that affect later milestones. Newest first within each section.

## Week 2 findings: entity resolution (2026-09-26)

### Resolution coverage (300 products, before gold labels exist)
6,649 mentions (1,963 distinct), resolved 6,047 (**90.9%**): 5,871 exact and 176 fuzzy. The remaining 602 are queued in `resolution_queue`: 347 no_match, 178 below_threshold (score 80–92), 47 suspected_noise, 30 ambiguous. These are coverage numbers, not accuracy; precision/recall/F1 need the hand labels in `eval/data/er_mentions.csv`.

Spot checks: 20 random fuzzy matches were all plausible typo fixes ("sodiunm carbomer", "Limoene", "Capylic/Capric Triglyceride"). The review band holds back near-misses that would be wrong: "citrus bergamota fruit oil" scores 80 against CITRUS BERGAMIA **LEAF** OIL and is queued, not accepted.

### The full CosIng inventory has quirks
- The API won't page past 10,000 hits, so the fetch splits the list by `substanceId` ranges (all 9 partitions are under 10k).
- Paging by the default relevance order is **not stable** for `text=*`: pages overlapped (16 IDs twice, 16 never seen). Sorting by `substanceId` fixes it.
- The index holds **16 exact duplicate documents** (same record and reference UUID): 33,656 hits represent 33,640 ingredients. Identical duplicates are deduped; conflicting ones would fail the run.
- Data error: KINETIN's INN field says "hyaluronidase". The INCI tie-break (below) sends "hyaluronidase" to HYALURONIDASE, not KINETIN.

### Name collisions between CosIng entries
CosIng often has two entries for one substance: an INCI name, plus another entry whose US name is the same text.
- "water": the WATER entry vs AQUA (US name "water")
- "titanium dioxide" vs CI 77891; "zinc oxide" vs CI 77947
- "mineral oil": two entries; "beeswax": BEESWAX vs CERA ALBA

Two rules handle this, both deterministic:
- **Corroboration:** when a label gives two names ("Titanium Dioxide (CI 77891)", "Water/Aqua"), the answer is the one ingredient both names point to.
- **INCI tie-break:** a bare shared name goes to the entry it is the official INCI name of. `er_inci_tiebreak` can be switched off for ablation.

Some pairs remain genuinely separate entries with no shared alias (CI 77007 vs ULTRAMARINES) and stay ambiguous.

### Gap: common English names aren't in CosIng
The biggest no_match groups are names CosIng doesn't carry: fragrance (18 mentions), purified water, perfume, vitamin e, shea butter, liquid paraffin, sunflower seed oil, deionized water. Closing this needs a common-name alias source. Options: PubChem synonyms (license to record first), or a small hand-curated alias list. **Decision pending.**

### Label formats the parser now handles
- `(and)` INCI blend notation, and `&` premixes (these share a position)
- dash-separated lists without commas
- commas inside chemical names (`1,2-Hexanediol`)
- CRLF line endings and line wraps inside names (`Sodium \r\nHyaluronate`)
- stacked may-contain markers (`[+/- MAY CONTAIN / PEUT CONTENIR`)
- backslash separators and HTML entities

Still unhandled, and left for the eval to measure:
- homoglyphs (a Cyrillic А in `АQUA`)
- OCR errors (`Stearoy!`)
- missing commas (`Phenoxyethanol. Propylparaben`)
- marketing prose pasted into the ingredient field (flagged as suspected noise)

### Salt/ester links
Rules generated 189 `salt_of` and 99 `ester_of` candidates, all `reviewed=false`, so nothing uses them yet. Examples: sodium hyaluronate → hyaluronic acid, tocopheryl acetate → tocopherol, retinyl palmitate → retinol, ascorbyl palmitate → ascorbic acid.

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
| CERAMIDE NP | 20 † | 11 |
| PANTHENOL | 12 | 43 |
| TOCOPHEROL | 20 | 104 |
| BAKUCHIOL | 11 | **7** ⚠ |

Totals: 241 unique papers (231 CC BY 4.0, 6 CC BY 3.0, 4 CC BY 2.0) and 300 products (295 with English names).

† Was 4 under the INCI name alone. Since the `ceramide-synonyms` branch, the PMC query also accepts `ceramide` / `ceramides` (`pmc_seed_synonyms` in `app/config.py`), which finds 108 hits instead of 4. **Caveat:** these papers cover ceramides as a class (barrier lipids, stratum corneum profiling, topical delivery), and none of the 20 titles names ceramide NP. Use them as evidence for ceramides in general. Week 2/3 must not treat them as specific to CERAMIDE NP. The same run pruned 3 of the original 4 ceramide NP papers, which fell outside the new top 20.

⚠ **Under 10:** these are source limits, not sampling bugs. The whole OBF dump has only 7 skincare products with azelaic acid, 2 with benzoyl peroxide and 7 with bakuchiol, and the sample takes all of them. Benzoyl peroxide and azelaic acid are mostly sold as OTC or prescription drugs, which OBF doesn't cover well. Expect weak product-suitability answers for these.

## Pipeline incident: NCBI error bodies with HTTP 200 (2026-09-26)
During the resample, NCBI answered one esearch (zinc oxide) with HTTP 200 and the body `{"esearchresult": {"ERROR": "Search Backend failed ... 502"}}`. The loader cached that response and read it as zero hits, and the `--prune` still ran. It deleted Week 1 zinc oxide papers that the rerun then re-inserted.

**Fix:** `RawCache.fetch` now takes a validator. A bad fresh payload is never cached, and a bad cached payload is refetched. esearch, efetch and CosIng responses are validated, and a failed esearch counts as a fetch error, which blocks `--prune`.
