import { NextResponse } from 'next/server';
import { COMMANDS, check, help, next, prev, record, scope, top } from '../../../lib/telegram';

export const dynamic = 'force-dynamic';
// /check waits up to 8s on the model server; everything else is a Turso read.
export const maxDuration = 30;

/**
 * The Telegram bot's webhook. Telegram POSTs every message sent to the bot
 * here, and the reply is a Turso read — nothing has to run 24/7.
 *
 * Three Vercel env vars:
 *   TELEGRAM_BOT_TOKEN       from @BotFather (the same one the digest uses)
 *   TELEGRAM_CHAT_ID         your chat; every other chat is ignored
 *   TELEGRAM_WEBHOOK_SECRET  any string of A-Z a-z 0-9 _ - ; Telegram sends it
 *                            back on every call, so nobody else can fake one
 *
 * One-time setup, after deploying: open
 *   https://winscope.vercel.app/api/telegram?setup=<TELEGRAM_WEBHOOK_SECRET>
 * which points Telegram at this route and fills the bot's `/` menu.
 */

const HANDLERS: Record<string, () => Promise<string> | string> = {
  start: help,
  help,
  scope,
  next,
  top10: () => top(10),
  top20: () => top(20),
  prev,
  record,
  check,
};

async function telegram(method: string, body: unknown) {
  const res = await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

export async function POST(request: Request) {
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET;
  if (!secret || request.headers.get('x-telegram-bot-api-secret-token') !== secret) {
    return NextResponse.json({ error: 'unauthorised' }, { status: 401 });
  }

  // Always 200 from here on: anything else makes Telegram redeliver the same
  // message over and over.
  const update = await request.json().catch(() => null);
  const message = update?.message;
  const chatId = message?.chat?.id;
  if (!message?.text || String(chatId) !== String(process.env.TELEGRAM_CHAT_ID)) {
    return NextResponse.json({ ok: true });
  }

  // "/top10@ZeusScopeBot extra words" -> "top10"
  const command = message.text.trim().split(/\s+/)[0].replace(/^\//, '').split('@')[0].toLowerCase();
  const handler = Object.hasOwn(HANDLERS, command) ? HANDLERS[command] : undefined;
  let text: string;
  try {
    const said = message.text.slice(0, 40).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    text = handler ? await handler() : `I don't know "${said}".\n\n${help()}`;
  } catch (error: unknown) {
    const detail = error instanceof Error ? error.message : String(error);
    console.error(`[TELEGRAM] /${command} failed:`, detail);
    text = `/${command} failed — try /check.`;
  }

  await telegram('sendMessage', {
    chat_id: chatId, text: text.slice(0, 4096), parse_mode: 'HTML', disable_web_page_preview: true,
  }).catch((e) => console.error('[TELEGRAM] send failed:', e));
  return NextResponse.json({ ok: true });
}

/** The one-time setup (see the header). Guarded by the webhook secret. */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET;
  if (!secret || url.searchParams.get('setup') !== secret) {
    return NextResponse.json({ error: 'unauthorised' }, { status: 401 });
  }
  if (!process.env.TELEGRAM_BOT_TOKEN || !process.env.TELEGRAM_CHAT_ID) {
    return NextResponse.json({ error: 'set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID on Vercel first' }, { status: 500 });
  }
  const webhook = await telegram('setWebhook', {
    url: `${url.origin}/api/telegram`,
    secret_token: secret,
    allowed_updates: ['message'],
    drop_pending_updates: true,
  });
  const commands = await telegram('setMyCommands', { commands: COMMANDS });
  return NextResponse.json({ webhook, commands });
}
