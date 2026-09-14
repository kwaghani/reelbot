"""Role evidence: sponsors cannot be converted into venue identities."""
from dataclasses import dataclass, asdict
import re
import unicodedata

def identity(value):
    return re.sub(r'[\W_]', '', unicodedata.normalize('NFKD', str(value or '')).casefold())

@dataclass(frozen=True)
class SponsorCandidate:
    name: str
    source: str
    confidence: float
    evidence: str

DISCLOSURE = re.compile(r'#(?:ad|sponsored|paidpartnership|partner|gifted|collab)\b|paid partnership with|sponsored by|in partnership with|thanks to .{0,100} for sponsoring', re.I)
COMMERCIAL = re.compile(r'\buse code\b|\blink in bio\b|\d+\s*%\s*off|\bdownload the app\b|\bsign up at\b', re.I)
HANDLE = re.compile(r'@([\w.]+)')

def platform_sponsors(root):
    """Only traverse named partnership containers on an already selected post.

    Booleans/IDs alone do not identify a sponsor. Never infer one from author
    metadata or from recommendations belonging to another post.
    """
    from worker.fetch.parsing import walk
    containers = {'paidpartnership', 'paidpartnershipinfo', 'brandedcontentinfo',
                  'brandedcontenttaginfo', 'sponsortags', 'edgemediatosponsoruser',
                  'brandpartners', 'brandpartner', 'sponsor', 'sponsors'}
    names = []
    for node in walk(root):
        for key, value in node.items():
            if identity(key).replace('_','') not in containers:
                continue
            values = [{'name': value}] if isinstance(value, str) else walk(value)
            for partner in values:
                name = next((partner[k] for k in ('username', 'uniqueId', 'brandName', 'brand_name', 'name', 'full_name')
                             if isinstance(partner.get(k), str) and partner[k].strip()), None)
                if name and len(name) <= 200:
                    names.append({'name': name.lstrip('@'), 'source': 'platform_paid_partnership',
                                  'confidence': 1.0, 'evidence': key})
    return list({identity(p['name']): p for p in names}.values())[:30]

def sponsors_for(signals):
    found = []
    for raw in signals.get('paid_partnerships', []):
        if isinstance(raw, dict) and raw.get('name'):
            found.append(SponsorCandidate(str(raw['name']), 'platform_paid_partnership', 1.0, str(raw.get('evidence', 'Paid partnership'))))
    # Retain typed metadata on cache replay without trusting it as venue evidence.
    for raw in signals.get('sponsor_candidates', []):
        if isinstance(raw, dict) and raw.get('name'):
            found.append(SponsorCandidate(str(raw['name']), str(raw.get('source', 'stored_disclosure')), float(raw.get('confidence', .9)), str(raw.get('evidence', ''))))
    for source in ('caption', 'ocr', 'transcript'):
        text = str(signals.get(source) or '')
        for brand in re.findall(r'#(\w+?)Partner\b', text, re.I):
            found.append(SponsorCandidate(brand, source + '_brand_partner', .98, '#' + brand + 'Partner'))
        for sentence in re.split(r'(?<=[.!?])\s+|[\n;]+', text):
            if DISCLOSURE.search(sentence) or COMMERCIAL.search(sentence):
                for handle in HANDLE.findall(sentence):
                    found.append(SponsorCandidate(handle.rstrip('.'), source + '_disclosure', .95, sentence[:500]))
            for match in re.finditer(r'(?i:paid partnership with|sponsored by|in partnership with)\s+(@?[\w.]+(?:\s+[A-Z][\w\x27’-]*){0,3})', sentence):
                found.append(SponsorCandidate(match[1].strip().lstrip('@').rstrip('.'), source + '_phrase', .98, sentence[:500]))
    # Highest-confidence role evidence wins; no automatic venue reinstatement.
    unique = {}
    for candidate in sorted(found, key=lambda s: s.confidence, reverse=True):
        if identity(candidate.name):
            unique.setdefault(identity(candidate.name), candidate)
    return tuple(list(unique.values())[:30])

def is_sponsor(name, sponsors):
    return identity(name) in {identity(s.name if isinstance(s, SponsorCandidate) else s['name']) for s in sponsors}

def handle_only(name, signals):
    wanted = identity(name)
    poi = signals.get('poi') or {}
    if wanted and wanted == identity(poi.get('name')):
        return False
    text = '\n'.join(str(signals.get(k) or '') for k in ('caption', 'ocr', 'transcript'))
    handles = {identity(h) for h in HANDLE.findall(text)}
    body = re.sub(r'#[\w]+', '', HANDLE.sub('', text))
    return bool(wanted and wanted in handles and wanted not in identity(body))
