# Sponsor roles, category coherence, and venue-kind repair

## Diagnosis and implementation

The [pre-change table](VENUE_CLASSIFICATION_DIAGNOSIS.md) covers all 15 production
places. H1 is supported: stale whole-reel bar inference persisted after linking
the Nashville venues. H2 is not supported by the stored arrays. The repair
does not infer missing historical provider data.

Precedence is now user override → resolved primaryType → own segment → reel
fallback. The provenance is stored in candidate.venue_kind_derivation; the
legacy provider/user field remains backward compatible. An absent primaryType
is no longer fabricated from the first types[] member. Array fallback ranks
food before bars, specific food types before generic restaurant, then uses the
explicit registry ordering, independent of array order. Unknown types are
logged even when a lower-priority inference supplies the display kind.

SponsorCandidate and VenueCandidate are distinct frozen types. VenueInputs
rejects mixed types and sponsor/venue identity overlap. Detection uses selected
post partnership containers, disclosure tags/phrases, and commercial intent
near handles. Identity normalization handles punctuation, spaces, and underscores.
Sponsors are saved in raw_signals.sponsor_candidates, excluded from extraction
and resolution, and never reinstated merely because they have an address.
Uncorroborated handles cap at 0.4 and cannot acquire an automatic pin.

Partnership adapters read selected-post JSON and oEmbed fields when exposed;
they do not claim every public endpoint exposes these fields. Disclosed embed
posts get one bounded HTML metadata attempt. Extraction/fetch versions change
so old unsafe results are not silently replayed. Compilation segments retain
their own evidence and the shared sponsor exclusions.

Category coherence is a hard veto before ranking, including cached places and
platform POIs. All rejected Google candidates are recorded with name, type,
context, and hypothetical name score. Surviving candidates are ranked normally;
exhausted fan-out returns category_mismatch for review. Unknown types pass the
gate with type_match=0, not a guessed match.

## Fixture results

- 12 explicitly synthetic sponsored-reel cases: six physical-location brands,
  six venue-plus-sponsor cases. Zero sponsor entries; all six dual cases retain
  the venue. The reported Toast caption is represented directly.
- 10 explicitly synthetic food/non-food homonym cases: each exact-name wrong
  category is vetoed; the surviving food candidate wins. These are unit
  fixtures, not a claim of testing 22 additional live videos.
- Tests also cover selected-post metadata isolation, typed-list rejection,
  handle confidence, YAML-only new contexts, cached poison, exhausted fan-out,
  user overrides, backfill preservation/idempotence, and provider precedence.
- Full regression run: 123 backend tests and 41 app tests passed, including
  existing Sturtevant Falls, BCD Tofu House, personal ownership and offline queue
  coverage. TypeScript and Python compilation checks passed.

## Registry as shipped

The complete type-to-kind mapping and compatibility YAML are in
[config/content_types.yaml](../config/content_types.yaml). Audited mapping sizes:

| Kind | Explicit Google types |
| --- | ---: |
| restaurant | 132 |
| cafe | 8 |
| bar | 5 |
| nightclub | 1 |
| bakery | 5 |
| dessert | 10 |
| hotel | 16 |
| shop | 38 |
| culture | 13 |
| attraction | 9 |
| outdoors | 18 |
| beach | 3 |
| fitness | 12 |
| wellness | 12 |
| entertainment | 26 |
| other | 0 (explicit fallback) |

Added/corrected restaurant mappings include sandwich_shop, noodle_shop,
salad_shop, steak_house, bar_and_grill, brewpub and gastropub; pastry_shop is
bakery. All requested restaurant subtypes have regression assertions.
Under the requested strict bar rule, bar includes only bar, pub, wine_bar,
brewery and night_club. Other granular bar/provider types without an explicit
mapping now fall to other and are logged, with segment/reel fallback where
available. No such type was encountered among the 15 original entries.

```yaml
category_compatibility:
  food_context:
    signals: [restaurant, cafe, bar, bakery, dessert, nightclub]
    keywords: [food, foodreview, restaurant, cafe, coffee, brunch, dinner, lunch, breakfast, noodles, burger, pancakes]
    accepts: ['*_restaurant', restaurant, cafe, coffee_shop, coffee_roastery, bar, pub, bakery, food, meal_takeaway, meal_delivery, ice_cream_shop, night_club, brewery, wine_bar, deli, diner, sandwich_shop, steak_house, noodle_shop, salad_shop, bar_and_grill, brewpub, gastropub]
    rejects: [clothing_store, womens_clothing_store, electronics_store, bank, car_dealer, pharmacy, shoe_store, auto_parts_store, hardware_store, furniture_store, jewelry_store, insurance_agency, real_estate_agency]
  outdoors_context:
    signals: [outdoors, beach, attraction]
    keywords: [hiking, hike, waterfall, trail, beach]
    accepts: [park, hiking_area, natural_feature, tourist_attraction, beach, national_park, state_park, botanical_garden, wildlife_park, wildlife_refuge]
    rejects: [clothing_store, electronics_store, bank, car_dealer, pharmacy, insurance_agency, real_estate_agency]
```

Google documents primaryType as the single primary classification and provides
granular restaurant types in its [Places type reference](https://developers.google.com/maps/documentation/places/web-service/place-types).
TikTok documents the [paid-partnership disclosure](https://developers.tiktok.com/docs/en/content-sharing-guidelines);
public metadata visibility is narrower than disclosure support.

## Production verification

Pending deployment and rollback-only repair preview. The network-free repair
uses a short transaction with lock/statement timeouts, preservation assertions,
and per-save recovery journals for changed entries. It preserves IDs, owners,
notes and custom folders. The isolated live regression script compares the four
previously correct addresses exactly and requires Toast to be unpinned review,
then removes only its own generated test library.
