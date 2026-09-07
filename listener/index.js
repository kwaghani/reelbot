import { ownText, reelUrls, deliverPending } from './transport.js';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import * as baileys from '@whiskeysockets/baileys';
import dotenv from 'dotenv';
import pg from 'pg';
import Pino from 'pino';
import QRCode from 'qrcode';
import qrcode from 'qrcode-terminal';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

const {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState
} = baileys;

const makeWASocket = baileys.default || baileys.makeWASocket;
const jidNormalizedUser = baileys.jidNormalizedUser || ((jid) => jid);

const AUTH_DIR = path.resolve(__dirname, 'auth_info');
const QR_IMAGE_PATH = path.resolve(__dirname, 'latest-qr.png');
const TARGET_GROUP_JID = (process.env.TARGET_GROUP_JID || '').trim();
const DATABASE_URL = (process.env.DATABASE_URL || '').trim();

if (!DATABASE_URL) {
  throw new Error('DATABASE_URL is missing');
}

const pool = new pg.Pool({ connectionString: DATABASE_URL });
const logger = Pino({ level: process.env.LOG_LEVEL || 'warn' });
const groupNameCache = new Map();
let replyPollTimer = null;
let outboundPollTimer = null;
let printedGroupList = false;
let warnedReplyPollFailure = false;
let warnedOutboundPollFailure = false;
const sentBotReplyTexts = new Map();

function unwrapMessage(message) {
  let current = message;
  for (let i = 0; i < 4; i += 1) {
    if (current?.ephemeralMessage?.message) current = current.ephemeralMessage.message;
    else if (current?.viewOnceMessage?.message) current = current.viewOnceMessage.message;
    else if (current?.viewOnceMessageV2?.message) current = current.viewOnceMessageV2.message;
    else break;
  }
  return current || {};
}

function extractMessageText(msg) {
  function collect(rawMessage) {
    const message = unwrapMessage(rawMessage);
    const pieces = [
      message.conversation,
      message.extendedTextMessage?.text,
      message.imageMessage?.caption,
      message.videoMessage?.caption,
      message.documentMessage?.caption
    ];

    const quotedMessage = message.extendedTextMessage?.contextInfo?.quotedMessage;
    if (quotedMessage) {
      pieces.push(collect(quotedMessage));
    }

    return pieces
      .map((piece) => (typeof piece === 'string' ? piece.trim() : ''))
      .filter(Boolean)
      .join(' ');
  }

  return collect(msg?.message);
}

function reelUrlFromText(text) {
  const match = text.match(/https?:\/\/(?:[\w.-]+\.)?(?:instagram\.com|tiktok\.com|youtube\.com|youtu\.be)\/[^\s<>"')]+/i);
  return match ? match[0] : null;
}

function isQuestion(text) {
  if (text.endsWith('?')) return true;
  return /^(what|where|which|who|when|why|how|do|does|did|can|could|would|will|is|are|should|give|show|tell|help|find|plan|recommend|suggest)\b/i.test(
    text.trim()
  );
}

function rememberBotReply(text) {
  if (!text) return;
  sentBotReplyTexts.set(text, Date.now());
  setTimeout(() => {
    sentBotReplyTexts.delete(text);
  }, 10 * 60 * 1000);
}

function isRecentlySentBotReply(text) {
  if (!text) return false;
  return sentBotReplyTexts.has(text);
}

function normalizedJid(jid) {
  return jid ? jidNormalizedUser(jid) : null;
}

function ownBotJids(sock) {
  return new Set(
    [sock.user?.id, sock.user?.jid, sock.user?.lid, sock.user?.me?.id, sock.user?.me?.jid]
      .map(normalizedJid)
      .filter(Boolean)
  );
}

function messageSenderJid(msg) {
  return normalizedJid(msg.key.participant || msg.participant || '');
}

function isOwnMessage(sock, msg) {
  if (msg.key.fromMe) return true;

  const senderId = messageSenderJid(msg);
  if (!senderId) return false;

  return ownBotJids(sock).has(senderId);
}

async function upsertGroup(chatId, name) {
  const result = await pool.query(
    `
    insert into groups (wa_chat_id, name)
    values ($1, $2)
    on conflict (wa_chat_id) do update
        set name = coalesce(excluded.name, groups.name)
    returning id
    `,
    [chatId, name || null]
  );
  return result.rows[0].id;
}

async function upsertMember(groupId, senderId, displayName) {
  await pool.query(
    `
    insert into members (group_id, wa_user_id, display_name)
    values ($1, $2, $3)
    on conflict (group_id, wa_user_id) do update
        set display_name = coalesce(excluded.display_name, members.display_name)
    `,
    [groupId, senderId, displayName || null]
  );
}

async function enqueueJob({ groupId, chatId, senderId, type, payload, requestId }) {
  await pool.query(
    `
    insert into jobs (group_id, chat_id, sender_id, type, payload, status, request_id)
    values ($1, $2, $3, $4, $5, 'queued', $6)
    on conflict (chat_id, sender_id, request_id) where request_id is not null do nothing
    `,
    [groupId, chatId, senderId, type, payload, requestId]
  );
}

async function groupName(sock, chatId) {
  if (groupNameCache.has(chatId)) return groupNameCache.get(chatId);
  try {
    const metadata = await sock.groupMetadata(chatId);
    const subject = metadata?.subject || null;
    groupNameCache.set(chatId, subject);
    return subject;
  } catch (error) {
    logger.debug({ error, chatId }, 'could not fetch group metadata');
    return null;
  }
}

async function printParticipatingGroups(sock) {
  if (printedGroupList) return;
  printedGroupList = true;

  try {
    const groups = await sock.groupFetchAllParticipating();
    const entries = Object.entries(groups || {}).sort(([, left], [, right]) =>
      String(left?.subject || '').localeCompare(String(right?.subject || ''))
    );

    console.log('');
    console.log('WhatsApp groups visible to this linked account:');
    if (entries.length === 0) {
      console.log('  (none found yet)');
    }
    for (const [jid, metadata] of entries) {
      const subject = metadata?.subject || 'Unnamed group';
      console.log(`  ${jid}  ${subject}`);
    }
    console.log('');
  } catch (error) {
    printedGroupList = false;
    logger.warn({ error }, 'could not list WhatsApp groups');
  }
}

async function handleIncomingMessage(sock, msg) {
  const chatId = msg.key.remoteJid;
  if (!chatId || !chatId.endsWith('@g.us')) return;
  if (TARGET_GROUP_JID && chatId !== TARGET_GROUP_JID) {
    console.log(`IGNORED_GROUP_JID=${chatId} target=${TARGET_GROUP_JID}`);
    return;
  }
  if (msg.key?.id?.startsWith('RB')) return;
  if (isOwnMessage(sock, msg) && !/^(1|true|yes)$/i.test(process.env.PROCESS_OWN_MESSAGES || 'false')) return;

  const text = extractMessageText(msg);
  if (!text) return;

  const name = await groupName(sock, chatId);
  console.log(`WHATSAPP_GROUP_JID=${chatId}${name ? ` (${name})` : ''}`);
  if (isRecentlySentBotReply(text)) return;

  const preview = text.length > 160 ? `${text.slice(0, 157)}...` : text;
  const directText = ownText(msg.message);
  const urls = reelUrls(directText);
  const reelUrl = urls[0];
  const question = isQuestion(directText);
  console.log(
    `MESSAGE_CLASSIFICATION fromMe=${Boolean(msg.key.fromMe)} hasReelUrl=${Boolean(reelUrl)} isQuestion=${question} text="${preview}"`
  );

  const senderId = messageSenderJid(msg) || jidNormalizedUser(chatId);
  const groupId = await upsertGroup(chatId, name);
  await upsertMember(groupId, senderId, msg.pushName || null);

  if (reelUrl) {
    for (const [index, url] of urls.entries()) await enqueueJob({ groupId, chatId, senderId, type: 'ingest', payload: url, requestId: `${msg.key.id}:ingest:${index}` });
    console.log(`QUEUED_JOB type=ingest payload=${reelUrl}`);
    logger.info({ chatId, senderId, reelUrl }, 'queued ingest job');
    return;
  }

  if (question) {
    await enqueueJob({ groupId, chatId, senderId, type: 'query', payload: text, requestId: `${msg.key.id}:query` });
    console.log(`QUEUED_JOB type=query payload="${preview}"`);
    logger.info({ chatId, senderId }, 'queued query job');
  }
}

async function pollReplies(sock) {
  await deliverPending(pool, sock, 'reply', TARGET_GROUP_JID, rememberBotReply);
}

async function pollOutboundMessages(sock) {
  await deliverPending(pool, sock, 'nudge', TARGET_GROUP_JID, rememberBotReply);
}

async function clearAuthState() {
  await fs.rm(AUTH_DIR, { recursive: true, force: true });
}

async function showPairingQr(qr) {
  await QRCode.toFile(QR_IMAGE_PATH, qr, {
    errorCorrectionLevel: 'M',
    margin: 4,
    width: 1024
  });
  console.log(`Saved scan-friendly QR image: ${QR_IMAGE_PATH}`);
  console.log('Scan this WhatsApp QR code, or open the PNG path above:');
  qrcode.generate(qr, { small: false });
}

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    logger,
    browser: ['Reelbot Phase 1', 'Chrome', '1.0'],
    syncFullHistory: false
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      await showPairingQr(qr);
    }

    if (connection === 'open') {
      console.log('WhatsApp connection open');
      await printParticipatingGroups(sock);
    }

    if (connection === 'close') {
      if (replyPollTimer) clearInterval(replyPollTimer);
      if (outboundPollTimer) clearInterval(outboundPollTimer);
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      logger.warn({ statusCode }, 'WhatsApp connection closed');
      if (statusCode === DisconnectReason.loggedOut) {
        logger.warn('logged out; clearing auth state so the next run can re-pair');
        await clearAuthState();
      }
      setTimeout(() => {
        startSocket().catch((error) => logger.error({ error }, 'restart failed'));
      }, 2500);
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages || []) {
      try {
        await handleIncomingMessage(sock, msg);
      } catch (error) {
        logger.error({ error }, 'message handling failed');
      }
    }
  });

  if (replyPollTimer) clearInterval(replyPollTimer);
  replyPollTimer = setInterval(() => {
    pollReplies(sock).catch((error) => logger.error({ error }, 'reply polling crashed'));
  }, 2000);

  if (outboundPollTimer) clearInterval(outboundPollTimer);
  outboundPollTimer = setInterval(() => {
    pollOutboundMessages(sock).catch((error) => logger.error({ error }, 'outbound polling crashed'));
  }, 3000);
}

process.on('SIGINT', async () => {
  await pool.end();
  process.exit(0);
});

startSocket().catch((error) => {
  logger.error({ error }, 'listener failed to start');
  process.exit(1);
});
