#!/usr/bin/env node
'use strict';

// scrapx — run the ScrapeX research agent locally, no Docker required.
//
// ScrapeX's server is a Python/FastAPI app. This CLI is a thin bootstrapper:
// on first run it creates a private Python virtualenv under ~/.scrapx,
// installs requirements.txt into it, and then just launches uvicorn from
// it every time after. Requires Python 3.10+ on PATH.

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const PKG_ROOT = path.resolve(__dirname, '..');
const STATE_DIR = path.join(os.homedir(), '.scrapx');
const VENV_DIR = path.join(STATE_DIR, 'venv');
const IS_WIN = process.platform === 'win32';
const VENV_BIN = path.join(VENV_DIR, IS_WIN ? 'Scripts' : 'bin');
const VENV_PIP = path.join(VENV_BIN, IS_WIN ? 'pip.exe' : 'pip');
const VENV_PYTHON = path.join(VENV_BIN, IS_WIN ? 'python.exe' : 'python');
const VENV_UVICORN = path.join(VENV_BIN, IS_WIN ? 'uvicorn.exe' : 'uvicorn');
const VENV_PLAYWRIGHT = path.join(VENV_BIN, IS_WIN ? 'playwright.exe' : 'playwright');
const DEPS_MARKER = path.join(VENV_DIR, '.deps-installed');
const MIN_PYTHON = [3, 10];

function log(msg) {
  // stderr, not stdout: `scrapx mcp` speaks MCP over stdout and any stray
  // line there would corrupt the protocol stream.
  console.error(`[scrapx] ${msg}`);
}

function fail(msg) {
  console.error(`[scrapx] ${msg}`);
  process.exit(1);
}

function findPython() {
  const candidates = IS_WIN ? ['python', 'py'] : ['python3', 'python'];
  for (const cmd of candidates) {
    const res = spawnSync(cmd, ['--version'], { encoding: 'utf8' });
    if (res.error || res.status !== 0) continue;
    const out = `${res.stdout || ''}${res.stderr || ''}`.trim();
    const match = out.match(/(\d+)\.(\d+)/);
    if (!match) continue;
    const version = [Number(match[1]), Number(match[2])];
    const meetsMin =
      version[0] > MIN_PYTHON[0] ||
      (version[0] === MIN_PYTHON[0] && version[1] >= MIN_PYTHON[1]);
    if (meetsMin) return cmd;
    log(`Found ${cmd} (${out}) but ScrapeX needs Python ${MIN_PYTHON.join('.')}+ — skipping it.`);
  }
  return null;
}

function run(cmd, args) {
  // Child stdout is routed to our stderr: bootstrap output (pip, playwright)
  // must never land on stdout, which `scrapx mcp` reserves for the protocol.
  const res = spawnSync(cmd, args, { stdio: ['inherit', 2, 'inherit'], cwd: PKG_ROOT });
  if (res.error) throw res.error;
  return res.status === 0;
}

function depsFingerprint() {
  const crypto = require('crypto');
  const req = fs.readFileSync(path.join(PKG_ROOT, 'requirements.txt'));
  return crypto.createHash('sha256').update(req).digest('hex');
}

function ensureVenv() {
  const python = findPython();
  if (!python) {
    fail(
      `Python ${MIN_PYTHON.join('.')}+ is required but wasn't found on your PATH.\n` +
        '  Install it from https://www.python.org/downloads/ and try again.'
    );
  }

  // The marker stores a hash of requirements.txt, so an upgraded package
  // with new dependencies reinstalls automatically.
  const fingerprint = depsFingerprint();
  if (
    fs.existsSync(VENV_UVICORN) &&
    fs.existsSync(DEPS_MARKER) &&
    fs.readFileSync(DEPS_MARKER, 'utf8').trim() === fingerprint
  ) {
    return;
  }

  fs.mkdirSync(STATE_DIR, { recursive: true });

  if (!fs.existsSync(VENV_UVICORN)) {
    log('Setting up a local Python virtual environment (first run only)...');
    if (!run(python, ['-m', 'venv', VENV_DIR])) {
      fail('Failed to create the Python virtual environment.');
    }
  }

  log('Installing Python dependencies (first run only, this can take a minute)...');
  run(VENV_PIP, ['install', '--quiet', '--upgrade', 'pip']);
  if (!run(VENV_PIP, ['install', '-r', path.join(PKG_ROOT, 'requirements.txt')])) {
    fail('Failed to install Python dependencies.');
  }

  log('Installing the Playwright Chromium browser (enables JS rendering + TikTok fallback)...');
  if (!run(VENV_PLAYWRIGHT, ['install', 'chromium'])) {
    log(
      'Playwright browser install failed or was skipped — ScrapeX still works fine, ' +
        'JS rendering and the TikTok fallback just degrade gracefully.'
    );
  }

  fs.writeFileSync(DEPS_MARKER, fingerprint);
}

function ensureDotEnv() {
  const envPath = path.join(PKG_ROOT, '.env');
  const examplePath = path.join(PKG_ROOT, '.env.example');
  if (!fs.existsSync(envPath) && fs.existsSync(examplePath)) {
    fs.copyFileSync(examplePath, envPath);
    log('Created .env from .env.example — add an AI provider key there, or set one later in the Settings tab.');
  }
}

function parseArgs(argv) {
  const opts = { host: '0.0.0.0', port: process.env.PORT || '8000', command: 'serve' };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === 'mcp' || arg === 'serve') {
      opts.command = arg;
    } else if (arg === '--port' || arg === '-p') {
      opts.port = argv[++i];
    } else if (arg === '--host') {
      opts.host = argv[++i];
    } else if (arg === '--help' || arg === '-h') {
      opts.help = true;
    }
  }
  return opts;
}

function launch(cmd, args) {
  const child = spawn(cmd, args, { stdio: 'inherit', cwd: PKG_ROOT });
  process.on('SIGINT', () => child.kill('SIGINT'));
  process.on('SIGTERM', () => child.kill('SIGTERM'));
  child.on('exit', (code) => process.exit(code == null ? 0 : code));
  child.on('error', (err) => fail(`Failed to start ${cmd}: ${err.message}`));
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    console.log(`scrapx — run the ScrapeX research agent locally

Usage:
  scrapx [--port 8000] [--host 0.0.0.0]   start the API server + web UI
  scrapx mcp                              run as an MCP server (stdio) so AI
                                          tools can use ScrapeX's scrapers
                                          directly, e.g.:
                                            claude mcp add scrapex -- npx scrapx mcp

On first run this creates a private Python virtualenv (~/.scrapx/venv) and
installs dependencies — requires Python 3.10+ on your PATH. Every run after
that starts instantly.
`);
    return;
  }

  ensureVenv();
  ensureDotEnv();

  if (opts.command === 'mcp') {
    log('Starting ScrapeX MCP server (stdio)...');
    launch(VENV_PYTHON, ['-m', 'app.mcp_server']);
    return;
  }

  log(`Starting ScrapeX at http://localhost:${opts.port} (API docs at /docs)`);
  launch(VENV_UVICORN, ['app.main:app', '--host', opts.host, '--port', String(opts.port)]);
}

main();
