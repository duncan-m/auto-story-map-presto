# Story map on a Presto

Shows the next stories you can build from Auto Story Map on a Pimoroni Presto.

A story counts as available when it is `planned` and every story in its `dependsOn` is `merged` or `shipped`. Stories are listed in release order, then in the order they appear in the map.

## How it works

- `feed.mjs` runs on your computer. It reads `product/story-map.yaml` with the `story-map` CLI each time it gets a request and serves a small JSON file at `/next.json`.
- `story_map.py` runs on the Presto. It joins your Wi-Fi, fetches the feed every 5 minutes, and shows five stories per page with their ID, title and estimate.

Touch controls:

- **List:** drag up and down to scroll, tap a story to open it, and tap the header to refresh now.
- **Story:** shows its title, estimate, activity, release, summary, the stories it builds on, the stories it unblocks, and its jobs. Drag up and down to scroll, swipe left for the next story or right for the previous one, and tap the header to go back.

## Caching

- The feed is rebuilt in the background when `product/story-map.yaml` or `builds/state.json` changes, and every 30 seconds for PR status, so the Presto's requests are answered from memory.
- The Presto saves the last feed as `story_map_cache.json` and shows it as soon as Story Map opens, before Wi-Fi connects, then refreshes in place.

## Launcher

`launcher/main.py` is Pimoroni's launcher with two changes: Story Map is first in the carousel, so it is at the front when the Presto starts, and its tile is drawn as the Auto Story Map logo (from `example/favicon.svg` in the library) instead of a font icon. Pimoroni's original is in `launcher/main.py.orig`. Install either with:

```sh
.venv/bin/mpremote cp launcher/main.py :main.py
.venv/bin/mpremote cp launcher/main.py.orig :main.py
```

## Build with Claude

The story screen has a **Build with Claude** button. After you confirm, your computer:

1. Claims the story on this machine with `story-map claim --store .git/story-map-claims`, so a second tap can't start it twice. Nothing is pushed to GitHub.
2. Creates a git worktree in `.claude/worktrees/<id>` on the story's branch, such as `feat/esm-116-serve-cli-over`, from `origin/main`. Your main checkout isn't touched.
3. Opens Terminal running Claude Code in plan mode with Remote Control, named after the story. The prompt is the story's brief, the same one the board's **Copy brief** gives.

`build-rules.md` is added to Claude's system prompt. It tells Claude to ask its clarifying questions first, wait for your answers, get your approval for a plan, and finish with a draft PR. Edit it to change how sessions behave.

Answer the session in the Claude app. Find it by the story's name in Claude Code on claude.ai or the Claude mobile app. The card shows **Building** until a PR exists for the branch, then **PR #n**.

Stories you start from the Presto move to a **Your builds** group at the top of the list, coloured by state: blue while building, amber once a PR is open, green when it merges, grey if the PR was closed. While a build is running the Presto checks every minute instead of every 5 minutes. When a build's PR merges, the Presto plays a short chime, flashes its lights green and shows a **MERGED** banner until you tap it. It remembers which merges it has announced in `story_map_notified.txt` on the device, so a restart doesn't repeat them. Merged builds drop off the list a day after they merge. Once merged, a card also shows the build's estimated cost, and the banner and the story's details show the cost and tokens. Tapping a build card opens its details, and swiping there moves between your builds. The feed reads them from the story's `estimatedCostUsd` and `tokens` on `origin/main`, which the PR that finishes the story records.

Merge state comes from the PR (`gh pr list --head <branch>`), since the PR that finishes a story is the one that marks it `merged` in the map.

- The Presto sends the token in `.build-token` with each build request. `set-wifi.sh` leaves it alone; it is saved as `BUILD_TOKEN` in the Presto's `secrets.py`.
- `node feed.mjs --dry-run` prepares the brief and launch script in `builds/` without claiming, branching or starting anything.
- To start over on a story, remove its entry from `builds/state.json`, run `story-map unclaim <ID> --agent presto --store .git/story-map-claims` in the repo, and remove the worktree with `git worktree remove`.
- The first time, macOS asks whether the terminal running `feed.mjs` may control Terminal. Allow it.

The LEDs on the back glow green when stories are available, blue when none are, amber when the feed can't be reached, and red when Wi-Fi fails.

## Set up

1. Plug the Presto into your Mac with a USB-C data cable. It already runs Pimoroni's MicroPython firmware.
2. Save your Wi-Fi details onto it. The script asks for them and writes them straight to the Presto:

   ```sh
   ./set-wifi.sh
   ```

3. Start the feed (Node 20+). By default it reads `../auto-story-map`, and it prints the `FEED_URL` the Presto should use:

   ```sh
   node feed.mjs
   ```

   Options: `--repo <path>`, `--map <path>`, `--port 8787`, `--limit 20`. `FEED_URL` lives in the Presto's `secrets.py`.

4. Copy the app across. It's named `story_map.py` so it appears in the Presto's launcher as **Story Map**, leaving the launcher's own `main.py` alone:

   ```sh
   .venv/bin/mpremote cp story_map.py :story_map.py
   ```

The Presto and your Mac must be on the same network. If macOS asks whether to let `node` accept incoming connections, allow it. `.venv` holds `mpremote`; recreate it with `/usr/bin/python3 -m venv .venv && .venv/bin/pip install mpremote`.

`preview.svg` shows a rough version of the layout, rendered from a simulated run.
