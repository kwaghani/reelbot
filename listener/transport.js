import { createHash } from 'node:crypto';

export function ownText(raw) {
  let message = raw || {};
  for (let i = 0; i < 4; i += 1) {
    const next = message.ephemeralMessage?.message || message.viewOnceMessage?.message || message.viewOnceMessageV2?.message;
    if (!next) break;
    message = next;
  }
  return [message.conversation, message.extendedTextMessage?.text, message.imageMessage?.caption, message.videoMessage?.caption, message.documentMessage?.caption]
    .filter((part) => typeof part === 'string').join(' ').trim();
}

export function canonicalReelUrl(value) {
  try {
    const url = new URL(value.trim());
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port || /[\s<>\\"']/.test(value)) return null;
    const host = url.hostname.toLowerCase();
    const path = url.pathname.replace(/\/+$/, '');
    if (['instagram.com', 'www.instagram.com', 'm.instagram.com'].includes(host)) {
      const match = path.match(/^\/(reel|reels|p)\/([A-Za-z0-9_-]+)$/);
      return match ? `https://www.instagram.com/${match[1] === 'p' ? 'p' : 'reel'}/${match[2]}/` : null;
    }
    if (['tiktok.com', 'www.tiktok.com', 'm.tiktok.com'].includes(host)) {
      const match = path.match(/^\/@([\w.-]+)\/video\/(\d+)$/);
      if (match) return `https://www.tiktok.com/@${match[1]}/video/${match[2]}`;
      if (/^\/t\/[A-Za-z0-9]+$/.test(path)) return `https://www.tiktok.com${path}/`;
    }
    if (['vm.tiktok.com', 'vt.tiktok.com'].includes(host) && /^\/[A-Za-z0-9]+$/.test(path)) return `https://${host}${path}/`;
    let id;
    if (['youtube.com', 'www.youtube.com', 'm.youtube.com'].includes(host)) {
      id = path.match(/^\/(?:shorts|watch)\/([A-Za-z0-9_-]{11})$/)?.[1] || (path === '/watch' ? url.searchParams.get('v') : null);
    } else if (host === 'youtu.be') id = path.slice(1);
    return id && /^[A-Za-z0-9_-]{11}$/.test(id) ? `https://www.youtube.com/watch?v=${id}` : null;
  } catch { return null; }
}

export function reelUrls(text) {
  return [...new Set((text.match(/https?:\/\/[^\s<>"')]+/gi) || [])
    .map((url) => canonicalReelUrl(url.replace(/[.,!;]+$/, ''))).filter(Boolean))];
}

export function deliveryMessageId(id) {
  return `RB${createHash('sha256').update(String(id)).digest('hex').slice(0, 30).toUpperCase()}`;
}

// One lock per transport/table across processes. Stable WhatsApp message IDs
// make retry after an uncertain send result reuse the original transport ID.
export async function deliverPending(pool, sock, kind, target = '', onSent = () => {}) {
  const table = kind === 'reply' ? 'jobs' : 'outbound_messages';
  const field = kind === 'reply' ? 'reply' : 'body';
  const client = await pool.connect();
  let locked = false;
  try {
    locked = (await client.query('select pg_try_advisory_lock(hashtext($1)) as locked', [`reelbot-delivery-${table}`])).rows[0].locked;
    if (!locked) return;
    const result = await client.query(
      `select id, chat_id, ${field} as body from ${table}
       where sent_at is null and chat_id like '%@g.us'
       and ($1 = '' or chat_id = $1)
       ${kind === 'reply' ? "and reply is not null and status in ('done', 'error')" : ''}
       order by created_at, id limit 10`, [target]);
    for (const row of result.rows) {
      try {
        await sock.sendMessage(row.chat_id, { text: row.body }, { messageId: deliveryMessageId(row.id) });
        await client.query('begin');
        try {
          await client.query(`update ${table} set sent_at = now() where id = $1`, [row.id]);
          if (kind !== 'reply') await client.query("insert into events(group_id,kind,detail) select group_id,'nudge',cluster_key from nudges where outbound_message_id=$1", [row.id]);
          await client.query('commit');
        } catch (error) { await client.query('rollback'); throw error; }
        onSent(row.body);
      } catch (error) {
        // A failed recipient must not prevent delivery to other groups.
        console.error(`Delivery failed for ${kind} ${row.id}: ${error?.name || 'error'}`);
      }
    }
  } finally {
    if (locked) await client.query('select pg_advisory_unlock(hashtext($1))', [`reelbot-delivery-${table}`]).catch(() => {});
    client.release();
  }
}
