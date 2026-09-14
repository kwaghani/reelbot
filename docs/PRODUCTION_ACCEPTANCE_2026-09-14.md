# Production acceptance — September 14, 2026

## Fresh-link backend tests

Five public links were submitted to the production API in a newly generated,
isolated device library. Every job progressed from queued through processing
to resolved, producing one anchored entry per link. Each first run fetched
platform content and made one fresh LLM request; this was not extraction-cache
replay. Instagram used public-page metadata; TikTok used oEmbed metadata.
These tests did not exercise video download, audio transcription, or OCR.

| Source | Extracted entry | Outcome |
| --- | --- | --- |
| [Instagram](https://www.instagram.com/reel/DKcN5chRxm2/) | Four Barrel Coffee | Correct named coffee shop in San Francisco |
| [TikTok](https://www.tiktok.com/@imnickmayorga/video/7638770721098861855) | Madison Bar and Grill | Correct named bar and grill in Hoboken |
| [TikTok](https://www.tiktok.com/@imnickmayorga/video/7641652990679600414) | Cappone's | Correct named sandwich shop in New York |
| [TikTok](https://www.tiktok.com/@tastebywill/video/7639109279198252318) | Toast | **FAIL: sponsor mistaken for venue, anchored to a clothing store** |
| [TikTok](https://www.tiktok.com/@treatyoselfeverywhere/video/7646815284082363678) | Golden Diner | Correct named diner at 123 Madison St, New York |

The five first-run configured cost estimates total approximately $0.222,
excluding local compute and the separate imagery verification. This is
application-metered estimated usage, not a reconciled provider invoice.

A second isolated-library replay inspected all five resulting entries and
downloaded their selected images. All five Google images loaded with no
reported image failure; sizes were 30,564–46,179 bytes, each below 60 KB.
Each entry had Places and Unsorted folder associations at the API checkpoint.
The wrong Toast entry also had a loadable image; image availability does not
establish semantic correctness. Both disposable libraries were deleted using
only their generated device credentials. The owner's library was not used
for these backend tests. Global fetch/place/image caches may retain test data.

Liveness and readiness succeeded before and after both runs. Database and R2
were healthy, and the queue returned to zero.

## Reproducible quality defect

The caption for the failing video describes food and names `@Toast` as a
sponsor (`#ToastPartner`, `#ad`). Extraction promoted Toast to the venue. The
place resolver accepted `TOAST`, primary type `clothing_store`, at 264
Elizabeth St. Its exact name score (1.0) outranked restaurant alternatives.
The entry was marked resolved, not held for review.

Read-only code inspection is consistent with this behavior:
`worker/places.py` ranks accepted candidates by name similarity; its
`resolution_guard` does not reject a category mismatch between a food venue
and a clothing store. Sponsor-versus-venue extraction also needs coverage.
No production fix was made during this testing-only request.

## Physical iPhone acceptance

Passed on iPhone 15 Pro, installed ReelBot version 1.0.0 build 30:

1. The existing library displayed 14 places with images.
2. Safari loaded the Four Barrel Coffee Instagram reel.
3. Its native iOS share sheet exposed ReelBot through More. The user assisted
   with the share app row because remote horizontal scrolling did not work.
4. Selecting ReelBot dismissed the extension back to Safari. Reopening ReelBot
   consumed the shared-container queue and created the production entry.
5. The library count became 15. Four Barrel Coffee displayed an image, cafe
   classification, Mission District neighborhood, and a meaningful summary.
6. Detail view showed Google photo attribution and the correct address:
   375 Valencia St, San Francisco, CA 94103, USA.
7. Background locality enrichment completed. The card then displayed San
   Francisco; production confirmed `organization_city=San Francisco`, provider
   neighborhood Mission District, one geography lookup, and resolved status.
   Its final automatic folder memberships were Places and San Francisco.
   The Places filter also displayed a Cafés category containing one entry.

The new entry is `b4e66a14-6b21-4937-8756-c0ad0bc6ccb9`. It remains in the
owner's library as a visible test example; the original 14 entries were not
deleted. No offline/airplane-mode test, forced process termination, or
video-only/transcription/OCR path was exercised in this run. Exactly one source
was tested through the real native share sheet; the five fresh sources above
were tested directly through the production API.

## Overall result

Infrastructure, native share delivery, imagery, and the demonstrated locality
refresh passed. Content-quality acceptance is **not fully passing** because
the sponsor/venue/category-mismatch defect remains. This report records test
results only; no application code or deployment configuration was changed.
