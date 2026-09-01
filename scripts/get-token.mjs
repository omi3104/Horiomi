#!/usr/bin/env node
/**
 * One-time Google OAuth helper. PURE NODE - no npm install needed.
 *
 *   node scripts/get-token.mjs
 *
 * It opens Google's consent screen in your browser, catches the redirect on
 * http://localhost:8765, and prints a REFRESH TOKEN plus the exact three
 * values to paste into your GitHub repo secrets.
 *
 * Prereqs (see README step 2):
 *   - a Google Cloud project with "YouTube Data API v3" and "Google Drive API"
 *     enabled, an OAuth consent screen (External, your email added as a Test
 *     user), and an OAuth client of type "Desktop app".
 *   - that client's Client ID + Client secret.
 */

import http from "node:http";
import { exec } from "node:child_process";
import { writeFileSync } from "node:fs";
import { pushSecrets, ghTokenFromGitCredential } from "./push-secrets.mjs";
import { createInterface } from "node:readline/promises";
import { stdin, stdout, env } from "node:process";

const REDIRECT = "http://localhost:8765";
// Google will not grant youtube.upload and drive.file in the same unverified
// consent request ("scopes that cannot be requested together"). YouTube
// upload is the one that matters, so that's all we request here. See
// README "Optional: Google Drive review copies" for adding drive.file later
// via its own separate token if you want that feature too.
const SCOPES = ["https://www.googleapis.com/auth/youtube.upload"].join(" ");

function openBrowser(url) {
  const cmd =
    process.platform === "win32" ? `start "" "${url}"`
    : process.platform === "darwin" ? `open "${url}"`
    : `xdg-open "${url}"`;
  exec(cmd, () => {});
}

async function ask(question, fallback) {
  if (fallback) return fallback;
  const rl = createInterface({ input: stdin, output: stdout });
  const answer = (await rl.question(question)).trim();
  rl.close();
  return answer;
}

const CLIENT_ID = await ask("Paste your Google OAuth Client ID: ", env.GOOGLE_CLIENT_ID);
const CLIENT_SECRET = await ask("Paste your Google OAuth Client secret: ", env.GOOGLE_CLIENT_SECRET);
if (!CLIENT_ID || !CLIENT_SECRET) {
  console.error("Client ID and secret are both required.");
  process.exit(1);
}

const authUrl =
  "https://accounts.google.com/o/oauth2/v2/auth?" +
  new URLSearchParams({
    client_id: CLIENT_ID,
    redirect_uri: REDIRECT,
    response_type: "code",
    scope: SCOPES,
    access_type: "offline",
    prompt: "consent",
  }).toString();

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, REDIRECT);
  if (url.pathname !== "/") {
    res.writeHead(404).end();
    return;
  }
  const error = url.searchParams.get("error");
  const code = url.searchParams.get("code");

  if (error) {
    res.writeHead(200, { "content-type": "text/html" });
    res.end(`<h2>Authorization failed:</h2><pre>${error}</pre>`);
    console.error("\nAuthorization failed:", error);
    server.close();
    process.exit(1);
  }
  if (!code) {
    res.writeHead(400).end("missing code");
    return;
  }

  try {
    const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: CLIENT_ID,
        client_secret: CLIENT_SECRET,
        redirect_uri: REDIRECT,
        grant_type: "authorization_code",
      }),
    });
    const data = await tokenRes.json();
    if (!data.refresh_token) {
      throw new Error(
        "No refresh_token in response. Revoke the app's access at " +
          "https://myaccount.google.com/permissions and run this again.\n" +
          JSON.stringify(data, null, 2)
      );
    }

    res.writeHead(200, { "content-type": "text/html" });
    res.end("<h2>All set. You can close this tab and return to the terminal.</h2>");

    const secrets = {
      GOOGLE_CLIENT_ID: CLIENT_ID,
      GOOGLE_CLIENT_SECRET: CLIENT_SECRET,
      GOOGLE_REFRESH_TOKEN: data.refresh_token,
    };

    // 1) write a local .env (git-ignored) so `python -m src.pipeline` works locally
    try {
      writeFileSync(
        new URL("../.env", import.meta.url),
        Object.entries(secrets).map(([k, v]) => `${k}=${v}`).join("\n") + "\n"
      );
      console.log("\nWrote .env (local, git-ignored).");
    } catch (e) {
      console.log("\nCould not write .env:", e.message);
    }

    // 2) push the same values straight to GitHub Actions secrets
    const repo = process.env.GH_REPO || "omi3104/YT-Agent";
    try {
      const ghToken = process.env.GITHUB_TOKEN || (await ghTokenFromGitCredential());
      if (!ghToken) throw new Error("no GitHub token from git credential / GITHUB_TOKEN");
      console.log(`Setting GitHub Actions secrets on ${repo}:`);
      await pushSecrets(repo, ghToken, secrets);
      console.log("\nGitHub secrets are set. Setup complete - nothing else to do.\n");
    } catch (e) {
      console.log("\nCould not auto-set GitHub secrets:", e.message);
      console.log("Add them by hand at Settings -> Secrets and variables -> Actions:");
      console.log("  GOOGLE_CLIENT_ID      =", CLIENT_ID);
      console.log("  GOOGLE_CLIENT_SECRET  =", CLIENT_SECRET);
      console.log("  GOOGLE_REFRESH_TOKEN  =", data.refresh_token);
    }
  } catch (e) {
    res.writeHead(500).end("token exchange failed - see terminal");
    console.error("\nToken exchange failed:\n", e.message);
    server.close(() => process.exit(1));
    setTimeout(() => process.exit(1), 1000).unref();
    return;
  }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1000).unref();
});

server.listen(8765, () => {
  console.log("\nOpening Google consent screen in your browser...");
  console.log("If it does not open, paste this URL manually:\n\n" + authUrl + "\n");
  openBrowser(authUrl);
});
