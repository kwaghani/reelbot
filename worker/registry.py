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

def registry():
    path = Path(os.getenv('CONTENT_TYPES_PATH', ROOT / 'config/content_types.yaml'))
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or not data:
        raise ValueError('Content type registry must be a nonempty mapping')
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
            if field.get('type') not in {'enum', 'string', 'integer'}:
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
    fields = data[content_type]['attributes']
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
            valid = (type(item) is int and 0 <= item <= 100000) if field['type'] == 'integer' else (
                isinstance(item, str) and 0 < len(item.strip()) <= 1000)
            if field['type'] == 'enum':
                valid = valid and item in field['values']
            if not valid:
                raise ValueError(f'Invalid value for {key}')
        clean[key] = list(dict.fromkeys(values)) if field.get('multi') else values[0]
    return clean, reasons

def candidate_schema(data=None):
    # Reuse nullable shapes and vary only the attribute object. Duplicating the
    # whole entry for every type makes provider grammars needlessly expensive.
    data=data or registry()
    definitions={'nullableString':{'type':['string','null']},'nullableInteger':{'type':['integer','null']},
        'nullableStringArray':{'type':['array','null'],'items':{'type':'string'}},
        'nullableIntegerArray':{'type':['array','null'],'items':{'type':'integer'}}}
    variants=[]
    for kind,spec in data.items():
        attrs={}
        for key,field in spec['attributes'].items():
            if field.get('multi'):
                attrs[key]={'$ref':'#/$defs/nullableIntegerArray' if field['type']=='integer' else '#/$defs/nullableStringArray'}
            elif field['type']=='enum': attrs[key]={'enum':[*field['values'],None]}
            else: attrs[key]={'$ref':'#/$defs/nullableInteger' if field['type']=='integer' else '#/$defs/nullableString'}
        name='attributes_'+kind
        definitions[name]={'type':'object','properties':attrs,'required':list(attrs),'additionalProperties':False}
        variants.append({'$ref':'#/$defs/'+name})
    properties={'content_type':{'type':'string','enum':list(data)},'title':{'type':'string'},'summary':{'type':'string'},
        'attributes':{'anyOf':variants},'venue_name':{'$ref':'#/$defs/nullableString'},
        'city_hint':{'$ref':'#/$defs/nullableString'},'address_hint':{'$ref':'#/$defs/nullableString'},'confidence':{'type':'number'},'evidence':{'type':'string'}}
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
