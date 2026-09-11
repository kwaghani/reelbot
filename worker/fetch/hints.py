"""Compatibility projection of separately typed identity and bias signals."""
import re
from worker.venue_identity import inputs_for

def identify(signals):
    typed=inputs_for(signals);payload=typed.payload()
    hints=typed.location_hints
    city=next((h.name for h in hints if h.name and h.source!='explicit_address'),None)
    tags=list(dict.fromkeys([str(v).lower().lstrip('#') for v in signals.get('hashtags',[])]+re.findall(r'#(\w+)',str(signals.get('caption','')).lower())))
    return {**payload,'hashtags':tags,'city_hint':city,'city_evidence':[h.source for h in hints if h.name==city],
        'deterministic_candidates':[{'name':v.name,'source':v.source,'kind':v.kind,'confidence':v.confidence,'rank':v.rank} for v in typed.venue_candidates]}
