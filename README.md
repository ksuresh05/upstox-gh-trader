Upstox AI Trading Bot — GitHub Actions Edition
A free, 24/7-feeling trading assistant that monitors your watchlist
during market hours and manages stop-loss/target trailing automatically
— no VM, no server, no monthly bill, no card required.
Why this architecture
Completely free — GitHub Actions on a public repo has no minute limits
No infrastructure to manage — no VM, no SSH, no systemd, no Oracle capacity roulette
Matches your actual workflow — you send instructions once before market open, review once at night
Persists across days — state is committed back to the repo as a git file, so multi-day swing trades survive between sessions automatically
How it works
```
9:10 AM IST — GitHub Actions wakes up (scheduled cron trigger)
                       ↓
        Loads state/instructions.json (your watchlist + rules)
        Loads state/positions.json (any open trades from before)
                       ↓
        Runs ONE continuous job for ~6 hours:
          every ~75 seconds →
            check entry triggers → place orders + GTT if hit
            check active trades → trail SL to breakeven at 50% target
            check GTT status → mark trades exited if target/SL fired
                       ↓
        3:30-3:35 PM IST — loop ends, job writes final summary
                       ↓
        Workflow commits state/ and logs/ back to the repo
                       ↓
        ~9 PM — you review with Claude here; logs are readable directly
                       ↓
        Next morning — same cycle, multi-day trades picked up automatically
```
Important honesty note: I (Claude, in chat) cannot watch the market
myself — I have no standing background process. The actual monitoring
and trading happens entirely inside the GitHub Actions job, independent
of whether you're talking to me or not. My role is to help you write
`state/instructions.json` each morning and read the logs with you each
evening.
---
Files in this project
File	Purpose
`upstox_client.py`	Upstox API wrapper (orders, GTT, quotes, portfolio)
`instruments.py`	NSE symbol → instrument token lookup
`state_store.py`	Reads/writes `state/positions.json` and `state/instructions.json`
`trade_engine.py`	Core logic: enter trades, trail SL, check GTT status
`run_trading_day.py`	The main script — runs the full 6-hour market-hours loop
`refresh_token.py`	Run each morning to refresh your Upstox token + update GitHub secret
`.github/workflows/trading-session.yml`	The GitHub Actions workflow definition
`state/instructions.json.example`	Sample format for your daily watchlist
---
One-time setup
1. Create a GitHub account
Go to github.com → Sign up (free, no card needed).
2. Create a new PUBLIC repository
Click + (top right) → New repository
Name it e.g. `upstox-gh-trader`
Set visibility to Public (this is what makes Actions minutes unlimited/free)
Don't initialize with README (we're uploading our own files)
3. Upload these files to the repo
Easiest way — using GitHub's web UI:
Open your new repo → Add file → Upload files
Drag in all files from this project (keep the folder structure — `.github/workflows/` must stay nested correctly)
Commit directly to `main`
Or via git command line:
```bash
cd upstox-gh-trader
git init
git add .
git commit -m "Initial trading bot setup"
git branch -M main
git remote add origin https://github.com/<your-username>/upstox-gh-trader.git
git push -u origin main
```
4. Add your secrets
Repo → Settings → Secrets and variables → Actions → New repository secret
Add:
`UPSTOX_ACCESS_TOKEN` — your daily Upstox token (you'll update this every morning — see below)
(API Key/Secret aren't needed by the workflow itself unless you also automate token refresh via Actions — for now, token refresh happens from your own machine via `refresh_token.py`.)
5. Install GitHub CLI (for daily token refresh)
Download from cli.github.com, then run once:
```bash
gh auth login
```
Follow the browser prompts to authenticate.
---
Trading Strategy: Ratcheting Target
This system implements a specific momentum-riding strategy, not a generic fixed SL/target bot:
Entry — you provide: symbol, entry price (trigger), quantity, and a first target price
Phase 1 (no real stop-loss) — only a target order is meaningfully active; a placeholder SL far away (50%) is used internally only because Upstox's GTT API requires both legs together. The position is genuinely unprotected against a drop until the first target is hit.
On first target hit — a real stop-loss appears for the first time: `SL = hit_price - 1%`. A new target is set: `new_target = hit_price + 5%`
On every subsequent target hit — same ratchet repeats: SL tightens to 1% below the new hit price, target extends to 5% above it
Exit — only when SL is eventually hit, which can only happen after at least one target has already been achieved, so every exit locks in a gain over entry
This is an intentional risk choice: full downside exposure before the first target, in exchange for capturing the complete upside of a confirmed move with no early stop-out.
Daily routine
Each morning, before 9:15 AM:
A. Tell me (Claude) your watchlist. For the ratchet strategy, each stock needs exactly: symbol, side, quantity, entry trigger price, and first target price. I'll write `state/instructions.json` for you.
Example of what you'd say:
> "SBIN, buy if it drops to 820, 10 qty intraday, first target 836. RELIANCE, buy if it hits 1450, 5 qty delivery, first target 1480."
B. Refresh your Upstox token:
```bash
python3 refresh_token.py
```
This walks you through getting a fresh authorization code from Upstox and automatically updates the `UPSTOX_ACCESS_TOKEN` secret on GitHub.
That's it — the scheduled workflow picks everything up automatically at 9:10 AM IST.
During the day
Nothing required from you. The GitHub Actions job runs independently.
If you want to check in, go to your repo's Actions tab to see the
live log of the running job.
Each evening, around 9 PM
Tell me you're ready to review, and I'll read `logs/<today's date>.log`
and `state/positions.json` from your repo and walk through what happened
— entries, trails, exits, and what's still open for tomorrow.
---
Testing before going live
First, test with `workflow_dispatch` (manual trigger) instead of waiting for the cron:
Repo → Actions tab → Daily Trading Session → Run workflow
Watch the live log
Use a tiny test instruction — 1 share, a symbol you're watching anyway, intraday — before trusting it with real size.
Check `state/positions.json` and the day's log file afterward to confirm everything behaved as expected.
---
⚠️ CRITICAL: SEBI Static IP Mandate (read before going live)
As of June 2026, SEBI requires all order-placement API calls to originate from a registered static IP address — this applies to every Indian broker (Upstox, Zerodha, Groww, etc.), not a Upstox-specific quirk. Since June 5, 2026, Upstox enforces this on every API call once a static IP is registered, not just order placement.
This breaks GitHub Actions for live order placement. GitHub-hosted runners use dynamic, rotating IPs. Once you register a static IP with Upstox (which you'll need to do to place real orders), GitHub Actions' calls will be rejected since they won't originate from that registered IP.
Before placing real orders, you need ONE of:
A static-IP proxy service (e.g. QuotaGuard Shield, routes traffic through a fixed IP you register with Upstox) — keeps this GitHub Actions architecture, adds a paid proxy layer
A VM with a fixed public IP (e.g. Oracle Cloud, AWS EC2 with Elastic IP) — register that IP with Upstox once, run the trading loop there instead of GitHub Actions
What still works fine on GitHub Actions today: read-only calls (portfolio, positions, funds) are NOT static-IP restricted as of this writing — but this could change, and real order placement will fail without a static IP. Do not rely on this system for live order placement until a static IP solution is in place.
---
Known limitations (being upfront)
GitHub Actions scheduled cron is "best effort" — usually fires within a couple minutes of the scheduled time, but isn't a hard real-time guarantee. The 9:10 AM trigger gives a 5-minute buffer before market open to absorb this.
6-hour job limit — `timeout-minutes: 370` covers the full 9:15–3:30 window with margin; if Upstox ever extends trading hours, this number needs updating.
No mid-day manual intervention built in yet — if you want to send an urgent "exit now" instruction mid-day, that currently requires manually triggering a workflow or editing instructions.json and waiting for the next loop iteration. Ask if you want a faster manual-override mechanism added.
Public repo — your trading logic/code is visible to anyone. Your actual Upstox token and API keys are NOT exposed (GitHub Secrets are encrypted and never shown in logs or code).
---
What's NOT yet built (future additions)
Automatic daily token refresh without you running a script (would need a more advanced auth flow)
Pyramid order management (multiple tranches per trade)
Partial exit logic (book 40% at 1st target, trail rest)
Mid-day manual override / emergency exit mechanism
Futures order mode
