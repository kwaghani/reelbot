"""I3 — uncertain inputs cannot become confidently settled places."""
import unittest
from unittest.mock import patch
import test_personal as base
from worker.venue_identity import condition_rows
from worker.db import connect, store_candidate
from worker.pipeline import new_metrics
from worker.places import resolve

class UncertaintyInvariant(unittest.TestCase):
    setUpClass=classmethod(base.PersonalTests.setUpClass.__func__)
    setUp=base.PersonalTests.setUp
    register=base.PersonalTests.register
    save=base.PersonalTests.save
    venue=base.PersonalTests.venue

    def test_I3_sponsor_only_and_city_geotag_remain_reviewable(self):
        fixtures=[{'caption':'Paid partnership with Toast. #ad'}, {'caption':'','geotag':{'name':'Los Angeles'}}]
        for i,signals in enumerate(fixtures):
            with self.subTest(invariant='I3',signals=signals):
                rows=condition_rows([],signals)
                self.assertTrue(rows,'I3: uncertain source needs a reviewable result')
                saved=self.save(self.a,str(i))
                with connect() as conn:
                    for row in rows:
                        entry=store_candidate(conn,{'id':saved['id'],'user_id':self.a['user']},row,None,0,'unresolved_place')
                        self.assertTrue(entry['needs_review'],'I3: uncertain extraction presented as fact')
                        self.assertIsNone(entry['place_id'],'I3: sponsor/city cannot become an anchored venue')

    def test_I3_ambiguous_chain_is_not_settled(self):
        def search(c,m):return [{'id':str(i),'displayName':{'text':'Tartine Bakery'},'formattedAddress':'San Francisco USA','location':{'latitude':37,'longitude':-122}} for i in range(2)]
        row,_=self.venue(self.save(self.a),self.a,search=search)
        self.assertTrue(row['needs_review'],'I3: ambiguous chain needs review');self.assertIsNone(row['place_id'])

    def test_I3_no_venue_and_low_confidence_never_gain_a_confident_pin(self):
        self.assertEqual(condition_rows([],{'caption':'A lovely day with friends.'}),[],'I3: no venue must not invent one')
        row={'content_type':'place','title':'Possibly this cafe','summary':'Uncertain','attributes':{'venue_kind':'cafe'},'confidence':.3,'review_reasons':['low_confidence']}
        saved=self.save(self.a)
        with connect() as conn:
            entry=store_candidate(conn,{'id':saved['id'],'user_id':self.a['user']},row,None,.3,'unresolved_place')
        self.assertTrue(entry['needs_review'],'I3: low confidence must remain reviewable')
