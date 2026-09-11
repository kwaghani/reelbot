"""Live taxonomy and shared validation. The YAML is the only category definition."""
from __future__ import annotations
import hashlib
import json
import logging
import os
from pathlib import Path
import yaml
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)

def registry_document():
    path = Path(os.getenv('CONTENT_TYPES_PATH', ROOT / 'config/content_types.yaml'))
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or not data:
        raise ValueError('Content type registry must be a nonempty mapping')
    return data

def venue_kinds():
    data = registry_document().get('venue_kinds')
    if data is None:
        data = yaml.safe_load((ROOT / 'config/content_types.yaml').read_text())['venue_kinds']
    seen = set()
    for key, spec in data.items():
        if not spec.get('label') or not spec.get('icon') or not spec.get('color') or not isinstance(spec.get('google_types'), list):
            raise ValueError('Incomplete venue kind: ' + key)
        overlap = seen.intersection(spec['google_types'])
        if overlap: raise ValueError('Ambiguous Google type mapping: ' + str(sorted(overlap)))
        seen.update(spec['google_types'])
    if 'other' not in data: raise ValueError('Venue kinds require other')
    return data

def registry():
    data = {k:v for k,v in registry_document().items() if k not in {'venue_kinds','venue_kind_signals'}}
    if 'place' in data:
        kinds=venue_kinds()
        data['place']['attributes']['venue_kind']['values'] = list(kinds)
        data['place']['kind_attributes']={k:v.get('attributes',{}) for k,v in kinds.items()}
    for key, spec in data.items():
        if not isinstance(key, str) or not key.replace('_', '').isalnum():
            raise ValueError('Invalid content type key')
        if spec.get('geo') not in {'required', 'optional', 'never'}:
            raise ValueError(f'Invalid geo policy for {key}')
        if not spec.get('label') or not spec.get('icon') or not isinstance(spec.get('attributes'), dict):
            raise ValueError(f'Incomplete schema for {key}')
        facet = spec.get('primary_facet')
        if facet and facet != 'city' and facet not in spec['attributes']:
            raise ValueError(f'Unknown primary facet for {key}')
        for name, field in spec['attributes'].items():
            if field.get('type') not in {'enum', 'string', 'integer', 'number', 'boolean'}:
                raise ValueError(f'Invalid attribute type: {key}.{name}')
            if field['type'] == 'enum' and not field.get('values'):
                raise ValueError(f'Empty enum: {key}.{name}')
    return data

def registry_version(data=None):
    return hashlib.sha256(json.dumps(data or registry(), sort_keys=True).encode()).hexdigest()

def sync_registry(conn, data=None):
    data = data or registry()
    for key, spec in data.items():
        conn.execute('''insert into content_type_registry(key,spec) values(%s,%s)
            on conflict(key) do update set spec=excluded.spec where content_type_registry.spec<>excluded.spec''',
            (key, Jsonb(spec)))
    return data

def validate_attributes(content_type, attributes, *, drop_unknown=False, data=None):
    data = data or registry()
    if content_type not in data:
        raise ValueError('Unknown content type: ' + str(content_type))
    if not isinstance(attributes, dict):
        raise ValueError('Attributes must be an object')
    fields = attribute_fields(content_type, attributes.get('venue_kind'), data)
    unknown = set(attributes) - set(fields)
    if unknown and not drop_unknown:
        raise ValueError('Unknown attributes: ' + ', '.join(sorted(unknown)))
    if unknown:
        LOG.warning('extractor_unknown_attributes type=%s keys=%s', content_type, sorted(unknown))
    clean, reasons = {}, []
    for key, field in fields.items():
        value = attributes.get(key)
        missing = value is None or value == '' or value == []
        if missing:
            if field.get('required'):
                clean[key] = None
                reasons.append('missing_required:' + key)
            elif key in attributes:
                clean[key] = None
            continue
        values = value if field.get('multi') else [value]
        if not isinstance(values, list) or len(values) > 50:
            raise ValueError(f'{key} must be a list of up to 50 values')
        for item in values:
            if field['type']=='boolean': valid=type(item) is bool
            elif field['type'] in {'integer','number'}:
                import math
                valid=type(item) in ({int} if field['type']=='integer' else {int,float}) and math.isfinite(item) and 0<=item<=100000
            else: valid=isinstance(item,str) and 0<len(item.strip())<=1000
            if field['type'] == 'enum':
                valid = valid and item in field['values']
            if not valid:
                raise ValueError(f'Invalid value for {key}')
        clean[key] = list(dict.fromkeys(values)) if field.get('multi') else values[0]
    return clean, reasons

def attribute_fields(content_type, venue_kind=None, data=None):
    data=data or registry();spec=data[content_type]
    return {**spec['attributes'],**(spec.get('kind_attributes',{}).get(venue_kind or 'other',{}) if content_type=='place' else {})}

def extraction_registry(signals):
    from copy import deepcopy
    from worker.venue_identity import inputs_for,infer_kind
    data=deepcopy(registry())
    kinds={infer_kind(signals,v.name)['kind'] for v in inputs_for(signals).venue_candidates}
    if not kinds:kinds={infer_kind(signals)['kind']}
    data['place']['kind_attributes']={k:v for k,v in data['place']['kind_attributes'].items() if k in kinds}
    data['place']['attributes']['venue_kind']['values']=list(data['place']['kind_attributes'])
    return data

def candidate_schema(data=None, *, compact=False):
    data=data or registry();definitions={};variants=[]
    for scalar in ('string','integer','number','boolean'):
        definitions['nullable_'+scalar]={'type':[scalar,'null']}
        definitions['nullable_'+scalar+'_array']={'type':['array','null'],'items':{'type':scalar}}
    for kind,spec in data.items():
        options=spec.get('kind_attributes',{}) if kind=='place' else {None:{}}
        for venue,extra in options.items():
            attrs={}
            for key,field in {**spec['attributes'],**extra}.items():
                scalar={'enum':'string','integer':'integer','number':'number','boolean':'boolean','string':'string'}[field['type']]
                if field.get('multi'):attrs[key]={'$ref':'#/$defs/nullable_'+scalar+'_array'}
                elif field['type']=='enum':attrs[key]={'enum':([venue] if key=='venue_kind' and venue else field['values'])+[None]}
                else:attrs[key]={'$ref':'#/$defs/nullable_'+scalar}
            name='attributes_'+kind+('_'+venue if venue else '')
            definitions[name]={'type':'object','properties':attrs,'required':list(attrs),'additionalProperties':False}
            variants.append({'$ref':'#/$defs/'+name})
    nullable={'type':['string','null']}
    properties={'content_type':{'type':'string','enum':list(data)},'title':{'type':'string'},'summary':{'type':'string'},
        'attributes':{'anyOf':variants},'venue_name':nullable,'city_hint':nullable,'address_hint':nullable,
        'confidence':{'type':'number'},'evidence':{'type':'string'}}
    if compact:
        # Avoid a combinatorial provider grammar across unrelated content schemas.
        # The inner object is validated against the conditioned registry locally.
        properties['attributes']={'type':'string','description':'JSON-encoded attribute object for this content type and venue kind'}
        return {'type':'array','items':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}}
    return {'type':'array','items':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False},'$defs':definitions}

def validate_candidates(rows, data=None):
    if not isinstance(rows, list): raise ValueError('Extraction must return an array')
    data = data or registry()
    result = []
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Each entry must be an object')
        row=dict(row)
        # The provider's grammar does not enforce string length. An overlong
        # description must not discard every otherwise valid entry in a listicle.
        if isinstance(row.get('summary'),str) and len(row['summary'])>140:
            LOG.warning('extractor_summary_shortened original_characters=%s',len(row['summary']))
            prefix=row['summary'][:139].rsplit(' ',1)[0]
            row['summary']=(prefix or row['summary'][:139]).rstrip(' ,;:.')+'…'
        for key, limit in [('title', 200), ('summary', 140), ('evidence', 2000)]:
            if not isinstance(row.get(key), str) or not row[key].strip() or len(row[key]) > limit:
                raise ValueError(f'Invalid {key}')
        confidence = row.get('confidence')
        if type(confidence) not in {float, int} or not 0 <= confidence <= 1:
            raise ValueError('Confidence must be between zero and one')
        for key in ('venue_name', 'city_hint', 'address_hint'):
            if row.get(key) is not None and (not isinstance(row[key], str) or len(row[key]) > 200):
                raise ValueError('Invalid ' + key)
        attributes, reasons = validate_attributes(row.get('content_type'), row.get('attributes'), drop_unknown=True, data=data)
        if confidence < .6: reasons.append('low_confidence')
        if row['content_type'] == 'other' and confidence > .7:
            LOG.warning('registry_gap confidence=%s topic=%s', confidence, attributes.get('topic'))
        result.append({key: row.get(key) for key in ('content_type','title','summary','venue_name','city_hint','address_hint','confidence','evidence')} |
                      {'attributes': attributes, 'review_reasons': reasons})
    return result
