import assert from 'node:assert/strict';
import test from 'node:test';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { canonicalReelUrl, ownText, reelUrls, deliverPending, deliveryMessageId } from '../transport.js';

test('canonical source links reject spoof hosts, profiles and preserve video IDs', () => {
  assert.equal(canonicalReelUrl('https://youtu.be/abcdefghijk?t=2'), 'https://www.youtube.com/watch?v=abcdefghijk');
  assert.equal(canonicalReelUrl('https://instagram.com/profile'), null);
  assert.equal(canonicalReelUrl('https://evil.instagram.com/reel/123/'), null);
  assert.equal(canonicalReelUrl('https://instagram.com.evil.test/reel/123/'), null);
  assert.deepEqual(reelUrls('Watch https://instagram.com/reel/ABC/?igsh=x and https://instagram.com/reel/ABC/.'), ['https://www.instagram.com/reel/ABC/']);
});

test('questions quoting a reel do not become imports', () => {
  const message = { ephemeralMessage: { message: { extendedTextMessage: { text:'Where is this?', contextInfo:{quotedMessage:{conversation:'https://instagram.com/reel/ABC/'}} } } } };
  assert.equal(ownText(message), 'Where is this?');
  assert.deepEqual(reelUrls(ownText(message)), []);
});

test('delivery IDs are stable across retry and distinct across jobs', () => {
  assert.equal(deliveryMessageId('a'), deliveryMessageId('a'));
  assert.notEqual(deliveryMessageId('a'), deliveryMessageId('b'));
});

test('database delivery selects only target WhatsApp group and prevents overlapping polls', { skip: !process.env.TEST_DATABASE_URL }, async () => {
  const url = new URL(process.env.TEST_DATABASE_URL);
  assert.ok(['localhost','127.0.0.1'].includes(url.hostname) && url.pathname.includes('audit'));
  const pool = new pg.Pool({connectionString:process.env.TEST_DATABASE_URL});
  const chat = randomUUID()+'@g.us';
  const group = (await pool.query('insert into groups(wa_chat_id) values($1) returning id',[chat])).rows[0].id;
  const ids=[];
  try {
    for(const destination of ['app',chat,'unrelated@g.us']) {
      const row=(await pool.query("insert into jobs(group_id,chat_id,sender_id,type,payload,status,reply) values($1,$2,'audit','query','test','done','Sandbox reply') returning id",[group,destination])).rows[0];
      ids.push(row.id);
    }
    const sent=[];
    const sock={sendMessage:async (...args)=>{sent.push(args); await new Promise(resolve=>setTimeout(resolve,30));}};
    await Promise.all([deliverPending(pool,sock,'reply',chat),deliverPending(pool,sock,'reply',chat)]);
    assert.equal(sent.length,1);
    assert.equal(sent[0][0],chat);
    assert.equal(sent[0][2].messageId,deliveryMessageId(ids[1]));
    const rows=(await pool.query('select id,sent_at from jobs where id=any($1::uuid[])',[ids])).rows;
    assert.ok(rows.find(row=>row.id===ids[1]).sent_at);
    assert.equal(rows.find(row=>row.id===ids[0]).sent_at,null);
    assert.equal(rows.find(row=>row.id===ids[2]).sent_at,null);
    await deliverPending(pool,sock,'reply',chat);
    assert.equal(sent.length,1);
  } finally {
    await pool.query('delete from jobs where id=any($1::uuid[])',[ids]);
    await pool.query('delete from groups where id=$1',[group]);
    await pool.end();
  }
});

test('failed nudge delivery remains pending, other messages proceed, and retry records one sent event', {skip: !process.env.TEST_DATABASE_URL}, async()=>{
  const url=new URL(process.env.TEST_DATABASE_URL);
  assert.ok(['localhost','127.0.0.1'].includes(url.hostname) && url.pathname.includes('audit'));
  const pool=new pg.Pool({connectionString:process.env.TEST_DATABASE_URL});
  const chat=randomUUID()+'@g.us';
  const group=(await pool.query('insert into groups(wa_chat_id) values($1) returning id',[chat])).rows[0].id;
  const ids=[];
  try {
    for(const cluster of ['first','second']) {
      const id=(await pool.query("insert into outbound_messages(group_id,chat_id,body,kind) values($1,$2,$3,'nudge') returning id",[group,chat,cluster])).rows[0].id;
      ids.push(id);
      await pool.query('insert into nudges(group_id,cluster_key,body,outbound_message_id) values($1,$2,$2,$3)',[group,cluster,id]);
    }
    const attempts=[];let fail=true;
    const sock={sendMessage:async(_chat,body,options)=>{attempts.push([body.text,options.messageId]);if(body.text==='first' && fail)throw new Error('Sandbox transport unavailable');}};
    await deliverPending(pool,sock,'nudge',chat);
    let rows=(await pool.query('select id,sent_at from outbound_messages where group_id=$1',[group])).rows;
    assert.equal(rows.find(row=>row.id===ids[0]).sent_at,null);
    assert.ok(rows.find(row=>row.id===ids[1]).sent_at);
    assert.equal((await pool.query("select count(*)::int as n from events where group_id=$1 and kind='nudge'",[group])).rows[0].n,1);
    fail=false;
    await deliverPending(pool,sock,'nudge',chat);
    await deliverPending(pool,sock,'nudge',chat);
    assert.equal(attempts.filter(([body])=>body==='first').length,2);
    assert.equal(new Set(attempts.filter(([body])=>body==='first').map(([,id])=>id)).size,1);
    assert.equal(attempts.filter(([body])=>body==='second').length,1);
    assert.equal((await pool.query("select count(*)::int as n from events where group_id=$1 and kind='nudge'",[group])).rows[0].n,2);
  } finally {
    await pool.query('delete from events where group_id=$1',[group]);
    await pool.query('delete from nudges where group_id=$1',[group]);
    await pool.query('delete from outbound_messages where group_id=$1',[group]);
    await pool.query('delete from groups where id=$1',[group]);
    await pool.end();
  }
});
