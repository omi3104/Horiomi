#!/usr/bin/env node
/**
 * Set GitHub Actions repository secrets via the REST API (libsodium sealed box).
 *
 * Used two ways:
 *   - imported by get-token.mjs to push the freshly-minted Google values
 *   - standalone:  node scripts/push-secrets.mjs
 *     Reads KEY=VALUE lines from ../.env and pushes every recognised secret
 *     that has a value: the three GOOGLE_* credentials plus the optional
 *     GEMINI_API_KEY, PEXELS_API_KEY, PIXABAY_API_KEY, COVERR_API_KEY and
 *     DRIVE_FOLDER_ID. Run it again any time you add a key to .env.
 *
 * Auth: uses $GITHUB_TOKEN if set, otherwise whatever `git credential fill`
 * returns for github.com (Git Credential Manager). The token needs `repo` scope.
 * Target repo: $GH_REPO, else derived from `git remote get-url origin`, else the
 * fallback below - so renaming the repo does not break this.
 */
import sodium from "libsodium-wrappers";
import { execFile, execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const FALLBACK_REPO = "omi3104/YT-Agent";

export function repoFromGit() {
  try {
    const url = execFileSync("git", ["remote", "get-url", "origin"], {
      encoding: "utf8",
    }).trim();
    const m = url.match(/github\.com[:/]+([^/]+\/[^/]+?)(?:\.git)?\/?$/i);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

export function targetRepo() {
  return process.env.GH_REPO || repoFromGit() || FALLBACK_REPO;
}

// Every secret name the pipeline understands. Only the ones with a value in
// .env are pushed; the rest are skipped.
const KNOWN_SECRETS = [
  "GOOGLE_CLIENT_ID",
  "GOOGLE_CLIENT_SECRET",
  "GOOGLE_REFRESH_TOKEN",
  "GROQ_API_KEY",
  "GEMINI_API_KEY",
  "PEXELS_API_KEY",
  "PIXABAY_API_KEY",
  "COVERR_API_KEY",
  "DRIVE_FOLDER_ID",
];

export function ghTokenFromGitCredential() {
  return new Promise((resolve) => {
    const child = execFile("git", ["credential", "fill"], (err, stdout) => {
      if (err) return resolve(null);
      const m = stdout.match(/^password=(.*)$/m);
      resolve(m ? m[1].trim() : null);
    });
    child.stdin.write("protocol=https\nhost=github.com\n\n");
    child.stdin.end();
  });
}

async function api(path, token, opts = {}) {
  const res = await fetch(`https://api.github.com${path}`, {
    ...opts,
    headers: {
      Authorization: `token ${token}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "yt-shorts-agent-setup",
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    throw new Error(`${opts.method || "GET"} ${path} -> ${res.status}: ${await res.text()}`);
  }
  return res.status === 204 ? null : res.json();
}

export async function pushSecrets(repo, token, secrets) {
  await sodium.ready;
  const pk = await api(`/repos/${repo}/actions/secrets/public-key`, token);
  const binkey = sodium.from_base64(pk.key, sodium.base64_variants.ORIGINAL);
  for (const [name, value] of Object.entries(secrets)) {
    if (!value) {
      console.log(`  - ${name}: skipped (no value)`);
      continue;
    }
    const encrypted_value = sodium.to_base64(
      sodium.crypto_box_seal(sodium.from_string(value), binkey),
      sodium.base64_variants.ORIGINAL
    );
    await api(`/repos/${repo}/actions/secrets/${name}`, token, {
      method: "PUT",
      body: JSON.stringify({ encrypted_value, key_id: pk.key_id }),
    });
    console.log(`  - ${name}: set`);
  }
}

function readEnv() {
  const text = readFileSync(new URL("../.env", import.meta.url), "utf8");
  const out = {};
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#") || !t.includes("=")) continue;
    const i = t.indexOf("=");
    out[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^["']|["']$/g, "");
  }
  return out;
}

// --- standalone entrypoint ---
if (process.argv[1] && process.argv[1].replace(/\\/g, "/").endsWith("scripts/push-secrets.mjs")) {
  const repo = targetRepo();
  const token = process.env.GITHUB_TOKEN || (await ghTokenFromGitCredential());
  if (!token) {
    console.error("No GitHub token (set GITHUB_TOKEN or sign in so `git credential fill` works).");
    process.exit(1);
  }
  const env = readEnv();
  const secrets = {};
  for (const name of KNOWN_SECRETS) if (env[name]) secrets[name] = env[name];
  if (!Object.keys(secrets).length) {
    console.error("No recognised KEY=VALUE lines found in .env - nothing to push.");
    process.exit(1);
  }
  console.log(`Setting secrets on ${repo}:`);
  await pushSecrets(repo, token, secrets);
  console.log("Done.");
  process.exit(0);
}
