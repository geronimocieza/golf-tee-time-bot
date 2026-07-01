# Golf Tee Time Bot ⛳

Checks the public tee-time booking calendars of your chosen Sydney golf clubs
every day and emails you a **weekend-only** digest of what's available, so
you don't have to manually refresh a dozen websites. It also publishes a
simple status page you can check any time from your phone.

## Email vs. a website — which should you use?

Both, and they're not actually alternatives here — the same daily run does
both for free:

- **Email** is the push notification: it lands in your inbox once a day
  without you having to remember to check anything. This is the best fit
  for "tell me when something opens up."
- **The status page** (see setup step 6 below) is the pull option: a plain
  webpage, hosted free on GitHub Pages, that always shows the most recent
  results. Bookmark it on your phone for "let me check right now" without
  waiting for the next email or triggering a run yourself.

You don't need to choose — this project gives you both from one daily run.
The one thing a "real" website can do that this can't is instant, on-demand
*live* checking (rather than showing yesterday/today's most recent scheduled
result) — if that matters to you, you can also manually trigger a fresh run
any time from the GitHub Actions tab (step 7 below) and both the email and
the status page will update within a minute or two.

## How it works

- Most of the clubs you play (Eastlake, Bankstown, Mona Vale, Long Reef,
  Wakehurst, Richmond, Stonecutters Ridge, Riverside Oaks, Pymble, Lakeside
  Camden, Brighton Lakes) all run on the same booking platform, **MiClub**.
  This bot fetches each club's public calendar page and reports which days /
  time categories currently have open (bookable) tee times.
- **Only Saturday/Sunday availability is included** by default (set
  `filters.weekend_only: false` in `clubs_config.yaml` to see every day).
- **Macquarie Links** (Chronogolf) and **Hurstville** (Golf Booking, whose
  live times load via JavaScript) use different platforms that can't be
  reliably auto-scraped the same way, so they're included as direct links
  instead of automated results.
- **Twin Creeks** is a private club with no public online booking, so it's
  left out entirely.
- It runs automatically once a day for free using **GitHub Actions** (no
  computer of yours needs to be turned on), and emails you via Gmail.

### A note on "more than 2 players"

I looked into filtering by remaining player spots per tee time, and it's
not something this script can do reliably. The calendar page it reads only
tells you *whether a time category has any booking available that day* —
it doesn't show how many of the (typically 4) player spots in a specific
tee time are already taken. That level of detail only loads via JavaScript
when you click into a specific date on the club's site, which a lightweight
script can't safely replicate without becoming fragile and easily broken by
small site updates. So the weekend filter narrows things down, and you get
a one-click link straight to the day — the actual group-size check takes
a couple of seconds once you're there.

## One-time setup (about 15–20 minutes)

### 1. Create a Gmail "App Password"

This lets the bot send email from your Gmail account without needing your
real password.

1. Go to <https://myaccount.google.com/security>
2. Turn on **2-Step Verification** if it isn't already on (required for app
   passwords).
3. Go to <https://myaccount.google.com/apppasswords>
4. Create a new app password (name it something like "Tee Time Bot"). Google
   will show you a 16-character password — copy it, you'll need it in step 3.

### 2. Create a GitHub account & repository (if you don't have one)

1. Sign up free at <https://github.com> if needed.
2. Create a new repository (e.g. `golf-tee-time-bot`). **Make it public** if
   you want the free status page (step 6) — the content is just public tee
   time availability, nothing sensitive. Your Gmail credentials stay secret
   either way (see step 3) regardless of the repo's visibility.
3. Upload all the files from this project into that repository (drag and
   drop works fine on github.com, or use `git push` if you're comfortable
   with git).

### 3. Add your secrets to GitHub

In your new repository:

1. Go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret** and add:
   - Name: `GMAIL_USER` — Value: your Gmail address (e.g. `you@gmail.com`)
   - Name: `GMAIL_APP_PASSWORD` — Value: the 16-character app password from
     step 1 (no spaces)

### 4. Edit `clubs_config.yaml`

Open `clubs_config.yaml` in the repository and change:

```yaml
email:
  to: "YOUR_EMAIL@gmail.com"
```

to your real email address. Add, remove, or comment out clubs as you like —
each one just needs a `name` and `url`. To see every day instead of just
weekends, change `filters.weekend_only` to `false`.

### 5. Test the email

1. Go to the **Actions** tab of your repository.
2. Click on **Daily Tee Time Check** in the left sidebar.
3. Click **Run workflow** (this is the manual trigger — you don't need to
   wait for the schedule).
4. After a minute or two, check the run's logs to see what it found, and
   check your inbox for the email.

### 6. Turn on the status page (optional but recommended)

1. In your repository, go to **Settings → Pages**.
2. Under "Build and deployment", set **Source** to **GitHub Actions**.
3. Re-run the workflow (Actions tab → Run workflow) if you haven't already
   since making this change.
4. Your status page will be live at `https://YOUR_USERNAME.github.io/golf-tee-time-bot/`
   — bookmark it on your phone.

### 7. Checking on demand

Any time you want a fresh check without waiting for the schedule: **Actions
tab → Daily Tee Time Check → Run workflow**. Both the email and the status
page update within a minute or two.

Once everything above is set up, it also runs automatically every day at
the scheduled time (see the comment in `.github/workflows/daily-check.yml`
— currently roughly 6am Sydney time; edit the cron line for a different
time).

## If a club stops working correctly

Golf club websites occasionally tweak their page layout, which can break the
scraper for that one club (the bot is built to keep going and just flag that
club as an error rather than crash the whole run). If you see a club
reporting an error in the digest for more than a day or two:

1. Open the club's calendar URL from `clubs_config.yaml` in a browser.
2. If it looks fine there, come back and tell Claude which club is failing —
   sharing the page's HTML (view source) will let the parsing logic be
   fixed for that site.

## Adding a club not on the list

If it's a MiClub club (most independent NSW golf clubs are), find its
`ViewPublicCalendar.msp` URL by looking for a "Book Now" / "Public Bookings"
link on the club's website, and add it to `clubs_config.yaml` following the
same pattern as the others. If it's on a different platform entirely, add it
with `platform: "manual"` and it'll just be included as a link in the email
and status page.

## Files in this project

| File | Purpose |
|---|---|
| `check_tee_times.py` | Main script — scrapes clubs, sends the email, writes the status page |
| `clubs_config.yaml` | List of clubs to check, weekend filter, and your email address |
| `requirements.txt` | Python packages needed |
| `.github/workflows/daily-check.yml` | Schedules the daily run and publishes the status page |
| `docs/index.html` | Auto-generated status page (created after the first run — don't edit by hand) |

