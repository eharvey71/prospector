# Prospector Autofill (Chrome extension)

Fills job application forms **in your own browser** with the answers the
engine prepared. You watch it happen, finish the human-only questions, and
click Submit yourself — so captchas and login walls don't matter.

## Install (once, ~30 seconds)

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. Click **Load unpacked** and pick this `web-extension/` folder

## Two things it does

### 1. Autofill an application you're finishing by hand

1. On the queue page's **Needs you** tab, click **Open & autofill** on a card
2. The posting opens in a new tab with a dark panel in the corner
3. Click **Fill this form** — text fields and simple dropdowns populate.
   This works whether the form is on the page itself *or* inside an
   embedded frame (a Greenhouse form inside a company's careers page), and
   the panel reports how many fields were filled in each.
4. Use the **copy** buttons for anything the fill couldn't reach
   (custom dropdown widgets, the questions under "Only you can answer")
5. Attach your resume by hand (browsers don't let extensions pick files)
6. Click the page's own **Submit**, then **Mark submitted** back on the queue page

If you reach the form by clicking through several pages (LinkedIn → the
company's careers page → their ATS, three domains later), the panel won't
appear on its own — it only auto-shows on the job's own domain. **Click the
toolbar button to summon it** on whatever page you've landed on.

### 2. Send any job you're browsing into the pipeline

Click the extension's **toolbar icon** on any job page — LinkedIn, Indeed, a
company careers page, anywhere. The queue opens with that URL and adds it
automatically: matched, scored, and drafted like any other job.

### What the toolbar button does

It does whichever of the two makes sense:

| Situation | Click does |
|---|---|
| A job is loaded (blue dot on the icon) | Shows the autofill panel on this page |
| No job loaded | Sends this page to your queue |

Either action is always available explicitly by **right-clicking the page**:
*Fill this form with Prospector* / *Add this job to Prospector*. Clear the
loaded job with the panel's "Done with this job" button when you're finished
— the dot disappears and the button goes back to adding jobs.

For LinkedIn postings the engine follows the "Apply on company website" link
to the employer's own application page when there is one, so submission can
use the deterministic adapters instead of the generic path.

## How it works / privacy

The queue page hands the extension a one-job payload (your prepared answers)
via the page itself — the extension has no login, no network access, and
talks to nothing. The payload sits in local extension storage until you click
"Done with this job (clear)" or send the next one.
