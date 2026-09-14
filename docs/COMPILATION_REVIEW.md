# Compilation extraction review — September 11, 2026

The dense extraction path is implemented, but the requested real-reel acceptance benchmark is **not yet passed**. The reported TikTok remains blocked at media download. Synthetic controls must not be confused with successful extraction of that reel.

## Reported reel

The public player shows a 17.18-second video, not a 60–90-second video. Its opening card supplies the expected count (7), New York City, restaurant kind and rooftop attribute. The seven manually observed labels are preserved in `compilation-evidence/reported-reel-labels.json`: Lost in Paradise Rooftop, Westlight NYC, elNico, Alma, One40 Rooftop, Electric Lemon and Creatures Rooftop.

A fresh deeper scan on September 11 still returned `extraction_empty`, expected 7, extracted 0. Metadata is accessible, but the worker's video request receives HTTP 403. Consequently there is no real scene-cut count, sampled-frame timeline or seven automatically resolved entries to report. See `compilation-evidence/reported-reel-sep11.json`. Manually observed names have not been injected into the extraction cache or presented as automated output.

The previous untouched, unverified “Unidentified place” stub was retired, with its original row archived in the owner's save diagnostics for recovery. Real saved places, notes and custom folder memberships were preserved.

## Implementation and limits

Compilation detection runs before metadata can end the fetch ladder. Explicit counts, list language, guide creators and scene boundaries select the dense path. Ordinary single-venue captions retain the cheap path. Shared city and restaurant/rooftop context follows each segment through extraction and provider classification; an explicit user category edit takes precedence.

The first scan uses scene threshold 0.30 plus 1 fps coverage. A count shortfall permits one 0.20 / 2 fps scan. Across both scans combined, hard limits are 120 sampled frames, 25 OCR calls and 15 vision calls. The initial pass reserves 30 frames, 8 OCR calls and 5 vision calls for escalation. A 180-second reel cannot retain every 1 fps sample plus scene samples under the 120-frame cap: bounded temporal coverage takes precedence.

Local text gating, white-band-aware temporal hashes, OCR and low-confidence vision feed timestamped segments. Intro/outro cards are excluded; each segment yields at most one place; normalized duplicates collapse. Partial results report `found_X_of_N`; zero places produce an empty extraction with deeper retry, original-reel and manual-entry actions. Global cache identity uses platform video ID, while failed/incomplete evidence and private source corrections do not poison shared results.

Provider failures preserve partial evidence, stop queued extraction calls after the bounded in-flight work, and avoid a futile second scan. The September 10 run exhausted Anthropic credits. A fresh September 11 probe succeeded; that credit failure is no longer the current blocker.

## Validation

All 103 backend tests passed on September 11, including expected-count variants, budget enforcement across retries, timeline exclusions, deduplication, provider failures, owner boundaries and preservation of user category overrides. Twenty single-venue classification fixtures stay on the cheap path. Existing BCD ingestion fixtures pass. These are not twenty new live end-to-end video runs.

A controlled three-title video recovered 3/3 names from 9 frames and 4 OCR calls in 7.37 seconds, at an estimated $0.007975 excluding local compute. This control exposed two local text-gate/dedup defects that were fixed before the larger run.

`scripts/benchmark_compilation_cards.py --live-model` provides ten authored, on-screen-only video controls with 3–15 names. September 10 and September 11 results are retained separately in `compilation-evidence/controlled-suite*.json`; do not combine the provider-blocked run with the recovered run. Detailed final counts are appended below when available.

The requested ten **real public compilation reels**, including three text-only reels, and the corresponding ≥80% recall/cost comparison remain outstanding. No claim of real-reel recall or seven-place automatic recovery is made.

## September 11 controlled video results

| Authored names | Recovered | Recall | Frames / OCR / vision | Seconds | Estimated USD |
|---:|---:|---:|---|---:|---:|
| 3 | 3 | 100% | 9 / 4 / 2 | 8.06 | $0.01699 |
| 4 | 4 | 100% | 11 / 5 / 2 | 8.89 | $0.02203 |
| 5 | 5 | 100% | 13 / 6 / 1 | 11.57 | $0.02608 |
| 6 | 6 | 100% | 15 / 7 / 1 | 12.28 | $0.03131 |
| 7 | 7 | 100% | 17 / 8 / 1 | 11.67 | $0.03608 |
| 8 | 8 | 100% | 19 / 9 / 2 | 23.30 | $0.04251 |
| 9 | 9 | 100% | 21 / 10 / 4 | 28.88 | $0.04952 |
| 10 | 10 | 100% | 23 / 11 / 4 | 28.29 | $0.05451 |
| 12 | 12 | 100% | 27 / 13 / 7 | 32.26 | $0.06779 |
| 15 | 15 | 100% | 33 / 16 / 5 | 40.98 | $0.08050 |

All ten synthetic cases recovered all 79 authored names (100%). Every case was on-screen-only; none supplied a transcript. This measures controlled card extraction, not real-platform download reliability or real-reel recall. All stayed within the combined frame/OCR/vision budgets.
