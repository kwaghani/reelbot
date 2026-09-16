"""Conservative technical retention rules; interpretations require legal review.

Only opaque Google IDs and a 30-day coordinate lease survive a response.
Classification is the customer's requested inference exception, not legal advice.
"""
from datetime import datetime, timedelta, timezone
import math

LEASE_DAYS = 30
REFRESH_DAYS = 25
ALERT_DAYS = 28


def valid_coordinates(place, at=None):
    at = at or datetime.now(timezone.utc)
    fetched = place.get('coords_fetched_at')
    if isinstance(fetched, str):
        try: fetched = datetime.fromisoformat(fetched.replace('Z', '+00:00'))
        except ValueError: return False
    return bool(fetched and fetched.tzinfo and fetched <= at and at - fetched < timedelta(days=LEASE_DAYS)
                and type(place.get('lat')) in (int, float) and type(place.get('lng')) in (int, float)
                and math.isfinite(place['lat']) and math.isfinite(place['lng'])
                and abs(place['lat']) <= 90 and abs(place['lng']) <= 180)


def safe_candidate(value):
    """Keep extraction/user evidence, remove provider response copies recursively."""
    blocked = {'place_candidates', 'places_queries', 'category_vetoes', 'primary_type', 'primaryType',
               'resolution_types', 'organization_geography', 'venue_kind_primary_type', 'google_response',
               'displayName', 'formattedAddress', 'addressComponents', 'photos', 'photoUri', 'websiteUri',
               'regularOpeningHours', 'currentOpeningHours', 'userRatingCount', 'priceLevel',
               'nationalPhoneNumber', 'internationalPhoneNumber', 'editorialSummary'}
    if isinstance(value, list): return [safe_candidate(v) for v in value]
    if not isinstance(value, dict): return value
    # Attributes and user notes are extraction-owned, not provider response bags.
    result = {k: (v if k == 'attributes' else safe_candidate(v)) for k, v in value.items() if k not in blocked}
    options = value.get('place_candidates')
    if isinstance(options, list):
        result['place_candidate_ids'] = list(dict.fromkeys(v['place']['id'] for v in options
            if isinstance(v, dict) and isinstance(v.get('place'), dict) and v['place'].get('id')))
    return result


def place_values(row):
    """Request-local compatibility aliases, derived exclusively from Class A data."""
    if row is None: return None
    result = dict(row)
    result['name'] = row.get('extracted_name') or ''
    result['city'] = row.get('extracted_city') or ''
    return result
