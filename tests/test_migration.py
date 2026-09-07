"""Exercise all ownership buckets against the actual archived source schema."""
import os,subprocess,unittest
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from db.migrate import run,foreign_column

class MigrationTests(unittest.TestCase):
    def test_existing_personal_library_retains_ids_notes_custom_folders_and_owners(self):
        source=os.environ['TEST_DATABASE_URL'];parts=urlsplit(source)
        self.assertIn(parts.hostname,{'localhost','127.0.0.1'})
        url=urlunsplit(parts._replace(path='/reelbot_migration_test'))
        previous=subprocess.check_output(['git','show','9ef2c0b0085f297d521ef6201c7bde01d2b7d4da:db/schema.sql'],text=True)
        with psycopg.connect(url,row_factory=dict_row) as conn:
            conn.execute('drop schema public cascade; create schema public;');conn.execute(previous,prepare=False)
            owner=conn.execute("insert into users(device_id) values('existing-library') returning id").fetchone()['id']
            save=conn.execute("insert into saves(user_id,source_url,url_hash,platform,status) values(%s,'https://www.youtube.com/watch?v=abcdefghijk','fixture','youtube','resolved') returning id",(owner,)).fetchone()['id']
            place=conn.execute("insert into places(google_place_id,name,formatted_address,lat,lng,primary_type,city) values('fixture','Old Cafe','1 Test Street',1,1,'cafe','Test City') returning id").fetchone()['id']
            item=conn.execute("insert into user_places(user_id,save_id,place_id,note,confidence,needs_review,candidate_key) values(%s,%s,%s,'Keep this note',.95,false,'fixture') returning id",(owner,save,place)).fetchone()['id']
            folder=conn.execute("insert into folders(user_id,name,kind) values(%s,'Weekend','custom') returning id",(owner,)).fetchone()['id']
            conn.execute('insert into folder_items(folder_id,user_place_id,user_id) values(%s,%s,%s)',(folder,item,owner))
        report=run(url);self.assertTrue(report['owners_preserved']);self.assertEqual(report['entries_after'],1)
        with psycopg.connect(url,row_factory=dict_row) as conn:
            row=conn.execute('select * from entries where id=%s',(item,)).fetchone()
            self.assertEqual((row['user_id'],row['save_id'],row['place_id'],row['note']),(owner,save,place,'Keep this note'))
            self.assertEqual((row['content_type'],row['title'],row['attributes']['venue_kind']),('place','Old Cafe','cafe'))
            self.assertEqual(conn.execute('select count(*) as n from folder_items where folder_id=%s and entry_id=%s',(folder,item)).fetchone()['n'],1)
            self.assertEqual(conn.execute('select count(*) as n from folders where kind in (\'auto_type\',\'auto_facet\')').fetchone()['n'],2)
        self.assertTrue(run(url)['already_migrated'])

    def test_all_buckets_dry_run_and_replay(self):
        source=os.environ['TEST_DATABASE_URL'];parts=urlsplit(source)
        self.assertIn(parts.hostname,{'localhost','127.0.0.1'})
        url=urlunsplit(parts._replace(path='/reelbot_migration_test'))
        original=subprocess.check_output(['git','show','pre-purge-snapshot:db/schema.sql'],text=True)
        with psycopg.connect(url,row_factory=dict_row) as conn:
            conn.execute('drop schema public cascade; create schema public;')
            conn.execute(original,prepare=False)
            root=conn.execute("insert into groups(wa_chat_id,name) values('fixture','Fixture') returning id").fetchone()['id']
            devices=[uuid4(),uuid4()]
            for identifier in devices:
                cols=[r['column_name'] for r in conn.execute("select column_name from information_schema.columns where table_schema='public' and table_name='app_devices'").fetchall()]
                conn.execute('insert into app_devices(id,token_hash,display_name) values(%s,%s,%s)',(identifier,str(identifier),'Fixture'))
            owner_column=foreign_column(conn,'members','groups')
            savers=[]
            for identifier in devices:
                savers.append(conn.execute(sql.SQL('insert into members({},wa_user_id) values(%s,%s) returning id').format(sql.Identifier(owner_column)),(root,str(identifier))).fetchone()['id'])
            parent_column=foreign_column(conn,'items','groups');originals=[]
            for number in range(3):
                originals.append(conn.execute(sql.SQL('insert into items({},source_url,place_id,place_name,location_text,lat,lng,list_name) values(%s,%s,%s,%s,%s,%s,%s,%s) returning id').format(sql.Identifier(parent_column)),(root,'https://instagram.com/reel/M'+str(number)+'/', 'p'+str(number),'Venue '+str(number),'Test City',40.,-73.,'Weekend')).fetchone()['id'])
            item_column=foreign_column(conn,'item_saves','items');saver_column=foreign_column(conn,'item_saves','members')
            for item,saver in [(originals[0],savers[0]),(originals[1],savers[0]),(originals[1],savers[1])]:
                conn.execute(sql.SQL('insert into item_saves({},{}) values(%s,%s)').format(sql.Identifier(item_column),sql.Identifier(saver_column)),(item,saver))
        dry=run(url,True)
        self.assertEqual((dry['single_identifiable_saver'],dry['multiple_savers'],dry['no_resolvable_saver']),(1,1,1))
        with psycopg.connect(url) as conn:
            self.assertEqual(conn.execute('select count(*) from items').fetchone()[0],3)
            self.assertIsNone(conn.execute("select to_regclass('public.users')").fetchone()[0])
        report=run(url)
        self.assertEqual((report['personal_rows'],report['orphan_rows'],report['accounted_items']),(3,1,3))
        self.assertTrue(run(url)['already_migrated'])
        with psycopg.connect(url) as conn:
            self.assertEqual(conn.execute('select count(distinct place_id) from entries where legacy_item_id=%s',(originals[1],)).fetchone()[0],1)
            self.assertEqual(conn.execute('select payload->>\'place_name\' from orphaned_items').fetchone()[0],'Venue 2')
