# Daily posting plan

Goal: one clip a day to Akash's own Instagram Reels and YouTube Shorts, only from videos he has the right to use, with nothing posted that he did not approve. Planned 2026-11-22; nothing is built yet.

## Choices made (2026-11-22)

- **Sources:** only videos with **permission on record** (written permission, or a channel's open clipping program) or a **Creative Commons** licence (YouTube's CC BY). Everything else can still be clipped in the app, but the posting step refuses it.
- **Picking:** Akash **approves a queue**. Each day the best approved clip that has not been posted yet goes out. Nothing posts without his OK.
- **Where it runs:** on the **laptop**, from Windows Task Scheduler. Clips and account tokens stay on the laptop.
- **No Claude Code dependency:** the posting runs as plain Python (`poster.py`) started by Windows. Gemini stays the only AI service (for caption drafts), and the laptop download stays as it is.
- **Accounts:** none set up yet. Setup steps are below.

## 1. Rights (built first, enforced in code)

Not legal advice. Credit alone does not make reposting legal, and India's "fair dealing" (Copyright Act section 52) is narrower than US fair use, so the plan does not rely on either.

- **Permission register** (`data/posting/rights.json`): one entry per YouTube channel, holding the channel id and name, the kind (`permission` or `program`), the date, proof (a saved email or screenshot, or the program's URL and its terms), conditions (credit wording, required tags, whether monetising is allowed), an end date if any, and a note. A Rights page in the app adds and edits entries.
- **Creative Commons check:** yt-dlp already reads a video's `license` field. A video counts only when it says "Creative Commons Attribution". That is checked again right before posting, since a licence can change.
- **The gate:** a clip can be approved only if its video has a valid permission entry or a CC BY licence. Uploaded MP4s need a register entry of their own, for example "my own recording". The poster checks the gate again on the day it posts.
- **Credit:** every post carries the credit the register asks for. By default: "Clip from <show> with <guest>, full episode: <YouTube link>". CC BY posts also name the licence.
- **Known risks, kept visible on the Rights page:**
  - Content ID can claim a clip even with permission (music in the episode, or the owner's own claims). Keep the permission proof for disputes, and ask permitted channels to allow-list Akash's channel.
  - A takedown or claim is logged against the clip, and that channel's posting stops until he reviews it.
  - YouTube's "reused content" rule matters only for monetising (YouTube Partner Program), not for posting.
  - SponsorBlock's data (CC BY-NC-SA) only steers which parts are skipped; it is never published.

## 2. The approval queue (app)

- A **Posting** page lists the clips that pass the rights gate (best score first) with three actions: Approve, Skip, or Edit text.
- **Text per clip:** a short title (YouTube, at most 100 characters), a caption (Instagram) and a description (YouTube), the credit, and a few hashtags. Gemini drafts them once from the clip's transcript lines; Akash can edit them; nothing posts until he approves.
- **The daily pick:** the highest-scored approved clip not yet posted, never from the same video two days running, posted to both platforms the same day.
- **Storage:** `data/posting/queue.json` (approved clips, their text, the order), and one line per post attempt in `data/posting/posts.jsonl` (platform, post id or link, time, result). Approved clips are copied to `data/posting/clips/`, so deleting or re-ranking a job cannot remove a queued clip.
- The page also shows the next post (date, clip, platforms), the days left in the queue, and the last posts with their links.

## 3. Accounts and API access (one-time setup, done by Akash)

### YouTube Shorts (YouTube Data API v3)
1. Create a YouTube channel on the Google account.
2. In Google Cloud: create a project, enable the YouTube Data API v3, then set up the OAuth consent screen (External) with the scope `youtube.upload`, and create a Desktop OAuth client.
3. **Publish the consent screen to "In production".** In "Testing", Google's refresh tokens expire after 7 days, which would stop the daily job every week. Unverified is fine for one user: the sign-in shows an "unverified app" warning once, and the cap is 100 users.
4. Sign in once from the app (local browser flow). The refresh token is stored in Windows Credential Manager (Python `keyring`), not in `.env`.
- **Quotas:** an upload (`videos.insert`) costs 1 unit of a 100-calls-a-day upload allowance, plenty for one a day. The page now says uploads from unverified projects are not restricted to private. Verify that with a first private test upload.
- **Shorts:** a vertical video of 3 minutes or less becomes a Short. Ours are 9:16 and at most 2 minutes. `publishAt` can schedule a private upload to go public at a set time.

### Instagram Reels (Instagram Graph API, content publishing)
1. Make the Instagram account a **professional** account (Creator is free).
2. Choose the API path (open decision below):
   - **A. Facebook Login for Business** (recommended): link the Instagram account to a Facebook Page, then create a Meta developer app with Facebook Login for Business. Permissions: `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`. **Uploads straight from the laptop** (resumable upload to `rupload.facebook.com`), so no file has to be hosted anywhere.
   - **B. Instagram Login**: no Facebook Page needed. Permissions: `instagram_business_basic`, `instagram_business_content_publish`. But Meta fetches the video from a **public URL**, so each clip would have to be put online for a few minutes, for example on a short-lived link served by a small Modal web endpoint, and deleted after publishing.
3. Use the app in development mode with Akash's own account as an app role. Meta's page lists Standard or Advanced Access but does not say whether publishing to your own account needs App Review, so this is the first thing the setup verifies.
4. Store the long-lived token (60 days) in Windows Credential Manager and refresh it from the daily job before it ends.
- **Limits:** 50-100 API posts per 24 h, 400 containers a day: far more than one a day. **Reels specs:** MP4/MOV with H.264, at most 1920 px wide, 23-60 fps, 3 s to 15 min, at most 300 MB, AAC audio up to 48 kHz. Our clips fit, at 1080×1920, 25 fps, at most 2 minutes and under 100 MB.
- **Flow:** create the container, upload, wait until it reads `FINISHED`, then `media_publish`.

## 4. The daily job (laptop, Windows Task Scheduler)

- `poster.py --daily` (backend folder, laptop-only, no Modal), runs as a scheduled task at Akash's chosen time with "Run task as soon as possible after a scheduled start is missed". If the laptop was off, it posts at the next start-up, or skips that day.
- **Steps:**
  1. Check that today has no post yet.
  2. Pick the clip.
  3. Check the rights gate.
  4. Refresh the tokens.
  5. Upload to YouTube, then Instagram.
  6. Log each result.
- Each platform runs on its own, so one failing still lets the other post. A failed platform is retried at the next run that day, and at most once more.
- **Never twice:** the job reads `posts.jsonl` first, and a lock file stops two runs at once.
- **Shown in the app:** the Posting page reads the log. A Windows notification reports success, or a failure with the reason. An empty queue reminds Akash to approve clips.
- **Setup command:** the app's Settings page shows the exact `schtasks` (or `Register-ScheduledTask`) command, and a "Run now" button posts the next clip at once.

## 5. Build order (one branch each, from `feat/optimized-version`)

1. `feat/posting-rights`: the rights register, the CC licence check, the gate, and the Rights page.
2. `feat/posting-queue`: the queue, the Posting page, Gemini text drafts, editing, and the copied clips.
3. `feat/post-youtube`: OAuth sign-in, keyring storage, and the upload, tested privately first.
4. `feat/post-instagram`: the chosen API path, a test post, then deleting it.
5. `feat/daily-poster`: `poster.py --daily`, the Task Scheduler command, Run now, notifications, and the post log in stats.
6. Later, if reach is low: "material edits" for Instagram's originality ranking. Since April 2026, accounts that mostly repost content they didn't make or meaningfully change are not recommended to non-followers; credits and captions don't count, while new narration, graphics or commentary do. Options include a hook title card, or Akash's own one-line voice intro or text take. Not needed to start.

## Open decisions (to confirm before batch 1)

1. **Instagram path:** A, Facebook Login (needs a Facebook Page, posts from the laptop; recommended), or B, Instagram Login (no Page, but each clip is put online for a few minutes).
2. **Posting time** (IST), for example 18:00 or 20:00, and the time zone for "one a day".
3. **Caption drafts:** Gemini drafts which Akash edits (recommended), or written by hand.
4. **Same clip on both platforms the same day** (recommended), or different clips per platform.

## Sources (checked 2026-11-22)

- Meta, Instagram content publishing: https://developers.facebook.com/docs/instagram-platform/content-publishing/ and the IG User media reference (Reels specs, resumable upload, limits)
- Google, `videos.insert`: https://developers.google.com/youtube/v3/docs/videos/insert
- Google, OAuth production readiness and token expiry: https://developers.google.com/identity/protocols/oauth2/production-readiness/overview and https://developers.google.com/identity/protocols/oauth2
- Instagram originality rules (April 2026): https://petapixel.com/2026/04/30/new-instagram-policies-target-reposted-content/ and https://www.emarketer.com/content/instagram-s-algorithm-clamps-down-on-repurposed--unoriginal-photos-posts
