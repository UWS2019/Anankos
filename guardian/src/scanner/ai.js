'use strict';
/*
 * ai.js
 * Optional: send the AI debrief prompt directly to an LLM the user configures,
 * instead of copy-pasting. Model-agnostic - supports any OpenAI-compatible
 * endpoint (OpenAI, OpenRouter, LM Studio, Ollama's /v1, etc.) and Anthropic.
 *
 * The endpoint, key and model are the USER's own and are stored locally in
 * userData/ai-settings.json. The app makes a request ONLY when the user clicks
 * "Send", and only to the address the user entered. Nothing is sent anywhere by
 * default.
 */
const fs = require('fs');
const path = require('path');

function settingsFile(dir) { return path.join(dir, 'ai-settings.json'); }

const DEFAULTS = {
  enabled: false,
  provider: 'openai',          // 'openai' (OpenAI-compatible) | 'anthropic'
  baseUrl: 'https://api.openai.com/v1',
  apiKey: '',
  model: 'gpt-4o-mini'
};

function loadSettings(dir) {
  try {
    return Object.assign({}, DEFAULTS, JSON.parse(fs.readFileSync(settingsFile(dir), 'utf8')));
  } catch (_) {
    return Object.assign({}, DEFAULTS);
  }
}

function saveSettings(dir, cfg) {
  try { fs.mkdirSync(dir, { recursive: true }); } catch (_) { /* ignore */ }
  const clean = Object.assign({}, DEFAULTS, cfg || {});
  try { fs.writeFileSync(settingsFile(dir), JSON.stringify(clean, null, 2)); } catch (_) { /* ignore */ }
  // Never return the key back to the UI.
  return redact(clean);
}

function redact(cfg) {
  const c = Object.assign({}, cfg);
  c.hasKey = !!(c.apiKey && c.apiKey.length);
  delete c.apiKey;
  return c;
}

async function sendDebrief(cfg, prompt) {
  if (!cfg || !cfg.apiKey) return { ok: false, error: 'No API key configured. Open AI settings and add your endpoint and key.' };
  if (!prompt) return { ok: false, error: 'Nothing to send.' };
  const base = String(cfg.baseUrl || '').replace(/\/+$/, '');
  if (!/^https:\/\//i.test(base)) return { ok: false, error: 'The AI endpoint must be an https:// address.' };

  try {
    if (cfg.provider === 'anthropic') {
      const res = await fetch(`${base}/messages`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-api-key': cfg.apiKey,
          'anthropic-version': '2023-06-01'
        },
        body: JSON.stringify({
          model: cfg.model || 'claude-3-5-sonnet-latest',
          max_tokens: 1200,
          messages: [{ role: 'user', content: prompt }]
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return { ok: false, error: apiError(res.status, data) };
      const text = (data.content || []).map((b) => b.text || '').join('\n').trim();
      return { ok: true, text: text || '(empty response)' };
    }
    // OpenAI-compatible
    const res = await fetch(`${base}/chat/completions`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${cfg.apiKey}`
      },
      body: JSON.stringify({
        model: cfg.model || 'gpt-4o-mini',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.3
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: apiError(res.status, data) };
    const text = data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content;
    return { ok: true, text: (text || '').trim() || '(empty response)' };
  } catch (e) {
    return { ok: false, error: `Could not reach the AI endpoint: ${String((e && e.message) || e)}` };
  }
}

function apiError(status, data) {
  const msg = (data && (data.error && (data.error.message || data.error)) || data.message) || '';
  return `AI endpoint returned ${status}${msg ? ': ' + (typeof msg === 'string' ? msg : JSON.stringify(msg)) : ''}`;
}

module.exports = { loadSettings, saveSettings, sendDebrief, redact, DEFAULTS };
