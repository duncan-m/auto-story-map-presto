#!/usr/bin/env node
// Serves the next available stories from an Auto Story Map repo as a small JSON
// feed for the Pimoroni Presto, and starts a Claude Code session for a story when
// the Presto asks.
//
// "Available" means: status planned, and every story it depends on is merged or
// shipped. Stories come in release order, then map order.
//
//   node feed.mjs [--repo ../auto-story-map] [--port 8787] [--limit 20]
//   node feed.mjs --dry-run              # build requests prepare everything but start nothing
//   node feed.mjs --out next.json        # write the feed once instead of serving
//
// GET  /next.json      the feed. The map is re-read on every request.
// POST /build/<ID>?mode=auto|accept-edits|plan
//                      needs the X-Build-Token header. Claims the story on this
//                      machine, makes a worktree on the story's branch from
//                      origin/main, and opens Terminal running Claude Code in that
//                      permission mode (plan if none is given) with Remote Control,
//                      so the session shows in the Claude app.

import { execFileSync } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, statSync, watch, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { networkInterfaces } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = parseArgs(process.argv.slice(2));
const repo = resolve(args.repo ?? join(here, "..", "auto-story-map"));
const map = args.map ?? "product/story-map.yaml";
const port = Number(args.port ?? 8787);
const limit = Number(args.limit ?? 20);
const dryRun = Boolean(args["dry-run"]);
const DONE = new Set(["merged", "shipped"]);

const buildsDir = join(here, "builds");
const statePath = join(buildsDir, "state.json");
const tokenPath = join(here, ".build-token");
const claimStore = ".git/story-map-claims"; // local claims: nothing is pushed to the remote
const rulesPath = join(here, "build-rules.md");

// The modes the Presto offers after Build with Claude, keyed by its ?mode= value.
// Each runs Claude Code in that permission mode with build-rules.md plus its own rules.
const MODES = {
  auto: {
    flag: "auto",
    rules: "build-rules-auto.md",
    message: "It builds and opens a PR, then you can ask it to merge in the Claude app.",
  },
  "accept-edits": {
    flag: "acceptEdits",
    rules: "build-rules-accept-edits.md",
    message: "Answer any questions in the Claude app, then it builds.",
  },
  plan: {
    flag: "plan",
    rules: "build-rules-plan.md",
    message: "Answer its questions and approve its plan in the Claude app.",
  },
};

function parseArgs(list) {
  const out = {};
  for (let i = 0; i < list.length; i++) {
    if (!list[i].startsWith("--")) continue;
    out[list[i].slice(2)] = list[i + 1]?.startsWith("--") || list[i + 1] === undefined ? true : list[++i];
  }
  return out;
}

// ---------- Reading the map ----------

// Prefer the repo's installed CLI; fall back to a source checkout's build.
function cli() {
  const bin = join(repo, "node_modules", ".bin", "story-map");
  if (existsSync(bin)) return [bin, []];
  const dist = join(repo, "dist", "cli.js");
  if (existsSync(dist)) return [process.execPath, [dist]];
  throw new Error(`No story-map CLI found in ${repo}. Run npm install there first.`);
}

function storyMap(command, { json = true, mapPath = map } = {}) {
  const [file, pre] = cli();
  const out = execFileSync(file, [...pre, ...command, ...(json ? ["--format", "json"] : []), "--map", mapPath], {
    cwd: repo,
    maxBuffer: 32 * 1024 * 1024,
  });
  return json ? JSON.parse(out.toString()) : out.toString();
}

// The library itself, from the repo, for the same brief as the board's Copy brief.
async function library() {
  const installed = join(repo, "node_modules", "auto-story-map", "dist", "index.js");
  const source = join(repo, "dist", "index.js");
  return import(pathToFileURL(existsSync(installed) ? installed : source).href);
}

function clip(text, max) {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max - 3).trimEnd()}...` : text;
}

// ---------- Builds ----------

function readState() {
  try {
    return JSON.parse(readFileSync(statePath, "utf8"));
  } catch {
    return {};
  }
}

// Stories being built outside the Presto (from the Claude app, say): a git worktree in the
// repo on a story branch such as feat/esm-116-serve-cli-over-mcp. Keyed by story ID, in the
// same shape as state.json entries, but never written to it. The main checkout is skipped.
function worktreeBuilds() {
  const found = {};
  let out = "";
  try {
    out = execFileSync("git", ["worktree", "list", "--porcelain"], { cwd: repo, timeout: 5000, stdio: ["ignore", "pipe", "ignore"] }).toString();
  } catch {
    return found;
  }
  for (const entry of out.split("\n\n").slice(1)) {
    const path = entry.match(/^worktree (.+)$/m)?.[1];
    const branch = entry.match(/^branch refs\/heads\/(.+)$/m)?.[1];
    const id = branch?.match(/^feat\/([a-z]+-\d+)-/i)?.[1].toUpperCase();
    if (!path || !id || found[id]) continue;
    let startedAt;
    try {
      startedAt = statSync(path).birthtime.toISOString();
    } catch {
      continue;
    }
    found[id] = { branch, worktree: path, startedAt, elsewhere: true };
  }
  return found;
}

// Presto builds and builds started elsewhere together. Presto entries win.
function allBuilds() {
  return { ...worktreeBuilds(), ...readState() };
}

function writeState(state) {
  mkdirSync(buildsDir, { recursive: true });
  writeFileSync(statePath, JSON.stringify(state, null, 2));
}

function buildToken() {
  if (!existsSync(tokenPath)) writeFileSync(tokenPath, randomBytes(18).toString("base64url") + "\n", { mode: 0o600 });
  return readFileSync(tokenPath, "utf8").trim();
}

function tokenMatches(given) {
  const expected = Buffer.from(buildToken());
  const actual = Buffer.from(String(given ?? ""));
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

// The PR for a build's branch, looked up with gh at most once a minute per branch.
const prCache = new Map();
function pullRequest(branch) {
  const cached = prCache.get(branch);
  if (cached && Date.now() - cached.at < 60_000) return cached.pr;
  let pr = null;
  try {
    const out = execFileSync("gh", ["pr", "list", "--head", branch, "--state", "all", "--json", "number,state,isDraft,mergedAt", "--limit", "1"], {
      cwd: repo,
      timeout: 8000,
      stdio: ["ignore", "pipe", "ignore"],
    });
    pr = JSON.parse(out.toString())[0] ?? null;
  } catch {
    pr = cached?.pr ?? null;
  }
  prCache.set(branch, { at: Date.now(), pr });
  return pr;
}

function buildStatus(id, state) {
  const build = state[id];
  if (!build) return null;
  const pr = pullRequest(build.branch);
  if (pr?.state === "MERGED") return { status: "merged", pr: pr.number, mergedAt: pr.mergedAt };
  if (pr?.state === "CLOSED") return { status: "closed", pr: pr.number };
  if (pr) return { status: "pr", pr: pr.number, prState: pr.state.toLowerCase(), draft: pr.isDraft };
  return { status: build.dryRun ? "dry-run" : "started", startedAt: build.startedAt };
}

// Stories as they are on origin/main, where a merged PR records the tokens and cost it used.
// The checkout may be on another branch, so this reads the map from origin/main itself.
let mainCache = { at: 0, stories: new Map() };
function mainStories(maxAgeMs) {
  if (Date.now() - mainCache.at < maxAgeMs) return mainCache.stories;
  try {
    execFileSync("git", ["fetch", "origin", "main"], { cwd: repo, stdio: "ignore", timeout: 20_000 });
    const text = execFileSync("git", ["show", `origin/main:${map}`], { cwd: repo, maxBuffer: 32 * 1024 * 1024 });
    mkdirSync(buildsDir, { recursive: true });
    const copy = join(buildsDir, "origin-main-map.yaml");
    writeFileSync(copy, text);
    const { stories } = storyMap(["stories", "--status", "all"], { mapPath: copy });
    mainCache = { at: Date.now(), stories: new Map(stories.map((s) => [s.id, s])) };
  } catch (error) {
    console.error(`Could not read origin/main: ${error.message}`);
    mainCache.at = Date.now();
  }
  return mainCache.stories;
}

function withCost(id, build) {
  if (build?.status !== "merged") return build;
  // Check often until the cost turns up, then every 5 minutes.
  const known = mainCache.stories.get(id)?.estimatedCostUsd !== undefined;
  const story = mainStories(known ? 300_000 : 30_000).get(id);
  if (!story) return build;
  return { ...build, tokens: story.tokens ?? null, costUsd: story.estimatedCostUsd ?? null, mapStatus: story.status };
}

function shellQuote(text) {
  return `'${String(text).replaceAll("'", `'\\''`)}'`;
}

async function startBuild(id, modeName = "plan") {
  const mode = MODES[modeName];
  if (!mode) return [400, { error: `Unknown mode ${modeName}. Use ${Object.keys(MODES).join(", ")}.` }];
  const feed = buildFeed();
  const story = feed.ready.find((s) => s.id === id);
  if (!story) return [404, { error: `${id} is not one of the stories ready to build.` }];
  const state = readState();
  if (state[id] && !state[id].dryRun) return [409, { error: `${id} already has a session, started ${state[id].startedAt}.` }];
  const elsewhere = worktreeBuilds()[id];
  if (elsewhere) return [409, { error: `${id} is already being built in ${elsewhere.worktree}.` }];

  const lib = await library();
  const document = lib.parseStoryMap(readFileSync(join(repo, map), "utf8"));
  const view = lib.getStoryMapForViewer(document, { roles: ["engineer"] });
  const fullTitle = view.releases.flatMap((r) => r.stories ?? []).find((s) => s.id === id)?.title ?? story.title;
  const branch = lib.storyMapBriefBranch(id, fullTitle);
  const brief = lib.storyMapBrief(view, { story: id, mapPath: map });
  const name = `${id}: ${fullTitle}`.slice(0, 80);
  const worktree = join(repo, ".claude", "worktrees", id.toLowerCase());

  mkdirSync(buildsDir, { recursive: true });
  const briefPath = join(buildsDir, `${id}.brief.md`);
  const scriptPath = join(buildsDir, `${id}.sh`);
  writeFileSync(briefPath, brief);
  writeFileSync(
    scriptPath,
    [
      "#!/bin/zsh",
      `# Started from the Presto for ${id}.`,
      `cd ${shellQuote(worktree)} || exit 1`,
      `printf '\\e]0;%s\\a' ${shellQuote(name)}`,
      `exec claude --remote-control ${shellQuote(name)} --name ${shellQuote(name)} --permission-mode ${mode.flag} \\`,
      `  --append-system-prompt "$(cat ${shellQuote(rulesPath)} ${shellQuote(join(here, mode.rules))})" \\`,
      `  "$(cat ${shellQuote(briefPath)})"`,
      "",
    ].join("\n"),
    { mode: 0o755 },
  );

  if (dryRun) {
    state[id] = { title: fullTitle, branch, worktree, mode: modeName, startedAt: new Date().toISOString(), dryRun: true };
    writeState(state);
    console.log(`[dry run] ${id} (${modeName}): would claim, add ${worktree} on ${branch}, and run ${scriptPath}`);
    return [200, { ok: true, dryRun: true, branch, message: `Dry run: nothing started for ${id}.` }];
  }

  // Claim on this machine first, so a second tap or another agent cannot start the same story.
  try {
    storyMap(["claim", id, "--agent", "presto", "--branch", branch, "--store", claimStore, "--ttl", "2d"], { json: false });
  } catch (error) {
    const held = error.status === 3;
    return [held ? 409 : 500, { error: held ? `${id} is already claimed.` : `Claim failed: ${error.stderr?.toString().trim() || error.message}` }];
  }

  try {
    execFileSync("git", ["fetch", "origin", "main"], { cwd: repo, stdio: "ignore", timeout: 30_000 });
    execFileSync("git", ["worktree", "add", "-b", branch, worktree, "origin/main"], { cwd: repo, stdio: "pipe" });
  } catch (error) {
    storyMap(["unclaim", id, "--agent", "presto", "--store", claimStore], { json: false });
    return [500, { error: `Could not create the worktree: ${error.stderr?.toString().trim() || error.message}` }];
  }

  try {
    execFileSync("osascript", [
      "-e", `tell application "Terminal" to do script "zsh " & quoted form of ${JSON.stringify(scriptPath)}`,
      "-e", `tell application "Terminal" to activate`,
    ]);
  } catch (error) {
    return [500, { error: `Worktree ready, but Terminal did not open. Run: zsh ${scriptPath}` }];
  }

  state[id] = { title: fullTitle, branch, worktree, mode: modeName, startedAt: new Date().toISOString() };
  writeState(state);
  console.log(`Started ${id} on ${branch} in ${modeName} mode`);
  return [200, { ok: true, branch, mode: modeName, message: mode.message }];
}

// ---------- Feed ----------

export function buildFeed() {
  const { releases } = storyMap(["releases"]);
  const { stories } = storyMap(["stories", "--status", "all"]);
  const releaseIndex = new Map(releases.map((r, i) => [r.id, i]));
  const releaseTitle = new Map(releases.map((r) => [r.id, r.title]));
  const order = new Map(stories.map((s, i) => [s.id, i]));
  const state = allBuilds();

  const planned = stories.filter((s) => s.status === "planned");
  const isReady = (s) => (s.dependsOn ?? []).every((d) => DONE.has(d.status));
  const byPlan = (a, b) =>
    (releaseIndex.get(a.release) ?? 1e9) - (releaseIndex.get(b.release) ?? 1e9) || order.get(a.id) - order.get(b.id);

  const ready = planned.filter(isReady).sort(byPlan);
  const waiting = planned.filter((s) => !isReady(s));
  const inProgress = stories.filter((s) => s.status === "in-progress").sort(byPlan);

  // Planned stories waiting on each story, so the detail view can say what it unblocks.
  const unblocks = new Map();
  for (const s of planned) {
    for (const d of s.dependsOn ?? []) {
      if (!unblocks.has(d.id)) unblocks.set(d.id, []);
      unblocks.get(d.id).push({ id: s.id, title: clip(s.title, 60) });
    }
  }

  const compact = (s) => ({
    id: s.id,
    title: clip(s.title, 90),
    release: clip(releaseTitle.get(s.release) ?? s.release, 48),
    estimate: s.estimate ?? null,
    activity: s.activity,
    summary: clip(s.summary, 400),
    dependsOn: (s.dependsOn ?? []).map((d) => ({ id: d.id, title: clip(d.title, 60), status: d.status })),
    unblocks: (unblocks.get(s.id) ?? []).slice(0, 6),
    jobs: s.jobs ?? [],
    build: withCost(s.id, buildStatus(s.id, state)),
  });

  // Every story started from the Presto or with a worktree elsewhere, so it stays visible
  // after it leaves the ready list.
  // A merged build drops off a day after it merges.
  // Each carries the story's details too, so its card opens a detail view even after
  // the story leaves the ready list.
  const byId = new Map(stories.map((s) => [s.id, s]));
  const builds = Object.entries(state)
    .filter(([, b]) => !b.dryRun || args["dry-run"])
    .map(([id, b]) => ({
      ...(byId.has(id) ? compact(byId.get(id)) : { id, title: clip(b.title ?? id, 90) }),
      branch: b.branch,
      elsewhere: Boolean(b.elsewhere),
      build: withCost(id, buildStatus(id, state)),
    }))
    .filter(({ build }) => build?.status !== "merged" || !build.mergedAt || Date.now() - Date.parse(build.mergedAt) < 24 * 3600_000)
    .sort((a, b) => (state[b.id].startedAt ?? "").localeCompare(state[a.id].startedAt ?? ""));

  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return {
    updated: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`,
    nextRelease: ready[0] ? releaseTitle.get(ready[0].release) ?? ready[0].release : null,
    counts: { ready: ready.length, waiting: waiting.length, inProgress: inProgress.length },
    inProgress: inProgress.slice(0, 5).map(compact),
    builds,
    ready: ready.slice(0, limit).map(compact),
  };
}

// ---------- Server ----------

// The feed is rebuilt in the background, when the map or the build state changes and
// every 30 seconds for PR status, so each request is answered from memory.
let latest = null;
let refreshTimer = null;
function refresh() {
  try {
    latest = JSON.stringify(buildFeed());
  } catch (error) {
    console.error(`Could not build the feed: ${error.message}`);
  }
}
function refreshSoon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 300);
}
function watchForChanges() {
  const mapFile = resolve(repo, map);
  const watchDir = (dir, name) => {
    try {
      watch(dir, (event, file) => { if (!name || file === name) refreshSoon(); });
    } catch (error) {
      console.error(`Not watching ${dir}: ${error.message}`);
    }
  };
  watchDir(dirname(mapFile), mapFile.split("/").pop());
  mkdirSync(buildsDir, { recursive: true });
  watchDir(buildsDir, "state.json");
  setInterval(refresh, 30_000);
}

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" }).end(JSON.stringify(body));
}

if (args.out) {
  writeFileSync(args.out, JSON.stringify(buildFeed(), null, 2));
  console.log(`Wrote ${args.out}`);
} else {
  buildToken();
  refresh();
  watchForChanges();
  createServer(async (req, res) => {
    const who = req.socket.remoteAddress;
    try {
      if (req.method === "GET" && req.url.startsWith("/next.json")) {
        if (!latest) refresh();
        res.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" }).end(latest);
        console.log(`${new Date().toLocaleTimeString()} ${who} feed`);
        return;
      }
      const url = new URL(req.url, "http://presto");
      const match = req.method === "POST" && url.pathname.match(/^\/build\/([A-Za-z0-9][A-Za-z0-9_-]{0,63})$/);
      if (match) {
        if (!tokenMatches(req.headers["x-build-token"])) {
          console.log(`${new Date().toLocaleTimeString()} ${who} build ${match[1]} refused: bad token`);
          send(res, 401, { error: "Wrong or missing build token." });
          return;
        }
        const [status, body] = await startBuild(match[1], url.searchParams.get("mode") ?? "plan");
        if (body.ok) refresh();
        console.log(`${new Date().toLocaleTimeString()} ${who} build ${match[1]}: ${status} ${body.error ?? body.message}`);
        send(res, status, body);
        return;
      }
      send(res, 404, { error: "Try GET /next.json or POST /build/<ID>." });
    } catch (error) {
      console.error(error);
      send(res, 500, { error: error.message });
    }
  }).listen(port, "0.0.0.0", () => {
    const ips = Object.values(networkInterfaces()).flat().filter((n) => n.family === "IPv4" && !n.internal);
    console.log(`Story feed for ${repo}${dryRun ? " (dry run: builds start nothing)" : ""}`);
    for (const n of ips) console.log(`  FEED_URL = "http://${n.address}:${port}/next.json"`);
    console.log(`  BUILD_TOKEN is in ${tokenPath}`);
  });
}
