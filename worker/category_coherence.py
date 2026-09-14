"""Registry-driven hard veto, independent of name similarity and provider kind."""
from fnmatch import fnmatchcase
import logging
import re
from worker.registry import registry_document

LOG = logging.getLogger(__name__)

def contexts_for(signals, inference=None):
    from worker.venue_identity import infer_kind
    inference = inference or infer_kind(signals)
    kinds = set(inference.get('scores', {})) | {inference.get('kind')}
    text = ' '.join(str(signals.get(k) or '') for k in ('caption', 'ocr', 'transcript')).casefold()
    return [name for name, rule in registry_document().get('category_compatibility', {}).items()
            if kinds.intersection(rule.get('signals', [])) or any(re.search(r'\b' + re.escape(word.casefold()) + r'\b', text)
                                                               for word in rule.get('keywords', []))]

def assess(place, candidate):
    primary = place.get('primaryType') or place.get('primary_type') or place.get('category') or ''
    rules = registry_document().get('category_compatibility', {})
    contexts = candidate.get('category_contexts')
    if contexts is None:
        contexts = [name for name, rule in rules.items() if candidate.get('venue_kind') in rule.get('signals', [])]
    matched = False
    for context in contexts:
        rule = rules.get(context, {})
        if any(fnmatchcase(primary, pattern) for pattern in rule.get('rejects', [])):
            return {'reason': 'category_mismatch', 'context': context, 'primary_type': primary, 'type_match': 0}
        matched |= any(fnmatchcase(primary, pattern) for pattern in rule.get('accepts', []))
    return {'reason': None, 'context': contexts, 'primary_type': primary, 'type_match': int(matched)}

def log_veto(place, candidate, verdict, metrics=None):
    from worker.places import name_similarity
    name = (place.get('displayName') or {}).get('text') or place.get('name', '')
    record = {'candidate': name, 'type': verdict['primary_type'], 'context': verdict['context'],
              'name_score': round(name_similarity(candidate.get('name', ''), name), 5), 'reason': 'category_mismatch'}
    LOG.warning('category_veto %s', record)
    if metrics is not None:
        metrics.setdefault('category_vetoes', []).append(record)
