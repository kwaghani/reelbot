# Pre-change production diagnosis — 2026-09-14

Captured read-only before changing code or production data. Fifteen place
entries; no Toast entry remains (the isolated acceptance libraries were removed).
Every stored `venue_kind_source` is `provider`; that legacy label does not prove
that the final kind actually came from Google. No row contains segment_signals
or compilation_context. Own-venue evidence is summarized below; the full
provider type arrays captured in the same query follow.

| Entry | Google primaryType | Reel inference/source | Own-venue evidence | Stored kind | Current primary mapping |
| --- | --- | --- | --- | --- | --- |
| 657 Hudson St, New York | absent | restaurant / caption “menu” (bar tied) | literal address | restaurant | other |
| Alisa Wine Friends | restaurant | restaurant / caption “bbq” | handle → Greek & Mediterranean cuisine at Venice Beach | restaurant | restaurant |
| BCD Tofu House | korean_restaurant | restaurant / caption “tofu” | venue explicitly named; Korean food / LA tags | restaurant | restaurant |
| Biscuit Love | breakfast_restaurant | bar / whole-caption “bar” | handle + Nashville context only | bar | restaurant |
| Category 10 | bar | bar / whole-caption “bar” | handle + Nashville context only | bar | bar |
| Four Barrel Coffee | coffee_shop | cafe / caption “cafe”, “coffee” | named coffee shop and roastery tour; specific geotag | cafe | cafe |
| Hattie B's | chicken_restaurant | bar / whole-caption “bar” | handle + Nashville context only | bar | restaurant |
| Karachi BBQ Tonight LA | absent | restaurant / caption “bbq” | handle → Pakistani halal BBQ in LA | restaurant | other |
| Meduza Mediterrania | mediterranean_restaurant | restaurant / caption “menu” (bar tied) | named venue, address, Mediterranean menu | restaurant | restaurant |
| Peg Leg Porker | barbecue_restaurant | bar / whole-caption “bar” | handle + Nashville context only | bar | restaurant |
| Play Playground | tourist_attraction | bar / whole-caption “bar” | handle + Nashville context only | bar | attraction |
| Posty's Bar | bar | bar / whole-caption “bar” | own name includes Bar | bar | bar |
| Raising Cane's | fast_food_restaurant | bar / whole-caption “bar” | handle + Nashville context only | bar | restaurant |
| Sturtevant Falls Trail | natural_feature | outdoors / trailsw, waterfalls, trail, falls | own named trail caption | outdoors | outdoors |
| Vees Cafe | brunch_restaurant | restaurant / caption “bbq” | handle → all-day brunch & breakfast in LA | restaurant | restaurant |

## Full stored Google type arrays

- Biscuit Love: breakfast_restaurant, brunch_restaurant, american_restaurant, restaurant, food, point_of_interest, establishment.
- Category 10: bar, point_of_interest, establishment.
- Four Barrel Coffee: coffee_shop, cafe, food_store, food, store, point_of_interest, establishment.
- Hattie B's: chicken_restaurant, soul_food_restaurant, american_restaurant, restaurant, food, point_of_interest, establishment.
- Meduza Mediterrania: mediterranean_restaurant, steak_house, live_music_venue, event_venue, restaurant, food, point_of_interest, establishment.
- Peg Leg Porker: barbecue_restaurant, bar, supplier, manufacturer, wholesaler, restaurant, food, point_of_interest, service, establishment.
- Play Playground: tourist_attraction, lounge_bar, bar, event_venue, night_club, restaurant, food, point_of_interest, establishment.
- Posty's Bar: bar, karaoke, breakfast_restaurant, live_music_venue, event_venue, restaurant, food, point_of_interest, establishment.
- Raising Cane's: fast_food_restaurant, chicken_restaurant, american_restaurant, restaurant, food, point_of_interest, establishment.
- Alisa Wine Friends, BCD Tofu House, Sturtevant Falls Trail, Vees Cafe: stored arrays empty; historical provider arrays unavailable, not invented.
- 657 Hudson St and Karachi BBQ Tonight LA: no linked place, hence no provider array.

## Supported hypothesis

H1 (reel-context leakage) is supported, not H2 (array order). All seven Nashville
entries share save `6f24c104-5fe8-42bd-b139-ef292fde3d76`. The five mismatches
still have `venue_kind_primary_type='<missing>'` despite now having a resolved
place. Their stored kind_inference is the whole reel's bar signal. This shows
stale fallback classification after linking, not proof that the current code
recomputed them from their present Google primary types. Neither Raising Cane's
nor Biscuit Love includes bar in its types array.

Separately, current classify_entry explicitly lets compilation_context override
Google, and an existing test enshrines that behavior. Both must change to the
requested precedence. Most restaurant subtypes are already mapped, but
sandwich_shop is incorrectly under shop; night_club is under nightclub instead
of the requested bar classification. Missing-type log: `<missing>` four times;
no observed nonempty unmapped Google type in this library.
