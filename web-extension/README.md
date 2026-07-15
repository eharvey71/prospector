# Job Engine Autofill (Chrome extension)

Fills job application forms **in your own browser** with the answers the
engine prepared. You watch it happen, finish the human-only questions, and
click Submit yourself — so captchas and login walls don't matter.

## Install (once, ~30 seconds)

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. Click **Load unpacked** and pick this `web-extension/` folder

## Use

1. On the queue page's **Needs you** tab, click **Open & autofill** on a card
2. The posting opens in a new tab with a dark panel in the corner
3. Click **Fill this form** — text fields and simple dropdowns populate
4. Use the **copy** buttons for anything the fill couldn't reach
   (custom dropdown widgets, the questions listed under "Only you can answer")
5. Attach your resume by hand (browsers don't let extensions pick files)
6. Click the page's own **Submit**, then **Mark submitted** back on the queue page

## How it works / privacy

The queue page hands the extension a one-job payload (your prepared answers)
via the page itself — the extension has no login, no network access, and
talks to nothing. The payload sits in local extension storage until you click
"Done with this job (clear)" or send the next one.
