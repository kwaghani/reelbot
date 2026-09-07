"""Personal lexical + cosine-vector search, extracted from the former retrieval core."""
from __future__ import annotations
import logging
from worker.db import connect, items, vector_literal
from worker.embed import embed_query
LOG = logging.getLogger(__name__)


def search(user_id: str, query: str):
    query = query.strip()[:500]
    with connect() as conn:
        rows = items(conn,user_id)
        if not query:
            return rows
        tokens = query.casefold().split()
        ranked = {}
        for row in rows:
            text = ' '.join([row['name'],row['city'],row['note'],
                ' '.join(folder['name'] for folder in row['folders'])]).casefold()
            score = sum(token in text for token in tokens)/len(tokens)
            if score:
                ranked[row['id']] = score * 2
        try:
            vector = vector_literal(embed_query(query))
            vectors = conn.execute('''select id,1-(embedding <=> %s::vector) as score from user_places
                where user_id=%s and embedding is not null order by embedding <=> %s::vector limit 50''',
                (vector,user_id,vector)).fetchall()
            for row in vectors:
                if row['score'] >= 0.45:
                    ranked[row['id']] = ranked.get(row['id'],0) + row['score']
        except Exception:
            LOG.exception('Semantic search unavailable; lexical results remain available')
        return sorted((row for row in rows if row['id'] in ranked), key=lambda r:ranked[r['id']], reverse=True)[:100]
