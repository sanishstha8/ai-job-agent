# AI Job Agent

This agent searches **LinkedIn, Indeed and ZipRecruiter** for jobs that match the description you write. It then:

- scores each job from 0 to 100 against your CV, using Claude;
- writes a tailored cover letter for the good matches;
- fills in the application forms in your own Chrome window.

**Nothing is submitted until you've reviewed it and pressed `y`.**

## One-time setup

Run every command from this folder in a terminal (PowerShell).

0. **Install the software on a new PC.** Double-click **`setup.bat`**, or type `.\setup.bat` in PowerShell. It:
   - installs Python 3.12 if it's missing (or tells you where to download it);
   - creates `.venv` and installs the packages;
   - installs the browser the agent uses;
   - creates your `.env` file.

   It takes a few minutes. Google Chrome is recommended but optional.

1. **Add your documents to `inputs/`**
   - `cv.pdf` or `cv.docx`: this exact file gets uploaded to applications.
   - `cover_letter.docx`, `.pdf` or `.txt`: one of your existing letters. The agent copies its style.
   - `target_job.md`: copy `target_job.example.md` and describe the job you want.
2. **Create a `.env` file for your API key.** Copy `.env.example` to `.env` and paste in your Anthropic API key, which you can get at https://console.anthropic.com/settings/keys. The site passwords in that file are optional.
3. **Fill in `profile.yaml`.** (`setup.bat` creates it from `profile.example.yaml`.) It holds your contact details, work authorization and sponsorship answers, salary, years of experience with each skill, and so on.
   - The agent answers eligibility and demographic questions only from this file and never guesses them.
   - Every field marked *(required)* must be filled in.
4. **Edit `config.yaml`**: search terms, location, how recent the postings must be, and keyword filters.
5. **Sign in to each site once.** A Chrome window opens for each command. Sign in there, complete any verification code yourself, then press Enter in the terminal. The session is saved for next time.
   ```powershell
   .venv\Scripts\python main.py login linkedin
   .venv\Scripts\python main.py login indeed
   .venv\Scripts\python main.py login ziprecruiter
   ```
6. **Check the setup.** Repeat this until every line shows OK:
   ```powershell
   .venv\Scripts\python main.py doctor
   ```

## Everyday use

```powershell
# 1. Find new jobs and score them. Results go to output\jobs.xlsx
.venv\Scripts\python main.py search

# 2. See the best matches (each has an ID)
.venv\Scripts\python main.py status

# 3. Practice run: fills in ONE LinkedIn application, then stops without submitting
.venv\Scripts\python main.py apply --site linkedin --limit 1 --dry-run

# 4. Apply for real. You review every application before it's sent
.venv\Scripts\python main.py apply --site linkedin --limit 5
```

Other commands:

| Command | What it does |
|---|---|
| `apply` (no `--site`) | Works through all sites, highest score first |
| `apply --job <ID>` | Applies to one specific job |
| `apply --min-score 80` | Only applies to jobs scored 80 or more (the default is `apply.min_score` in `config.yaml`) |
| `letters --limit 5` | Writes cover letters for the top jobs without applying. Useful for jobs you'll apply to yourself. |
| `score` | Scores any jobs that weren't scored yet, for example after a network error |
| `report` | Rebuilds `output\jobs.xlsx` |

### The review screen

Before anything is submitted, the terminal shows the job, every question and answer, the cover letter and a screenshot path:

| Key | What happens |
|---|---|
| `y` | Submit |
| `n` | Skip this job |
| `e` | Change one of the answers |
| `m` | You finish this application yourself in the browser, then press Enter |
| `q` | Stop the run |

Each answer shows where it came from:

| Source | Meaning |
|---|---|
| **profile** | `profile.yaml` |
| **memory** | An answer you approved on an earlier application |
| **ai** | Claude, based on your CV and profile |
| **you** | You typed it |
| **prefilled** | The site filled it in already |

When Claude is unsure about a question, or it asks about a personal fact that isn't in your profile, the agent stops and asks you. Your short answers are remembered, so each application needs less typing than the one before.

## Output

| Location | Contents |
|---|---|
| `output\jobs.xlsx` | Every job with its score, reasons, missing requirements and status. Links open the posting or the cover letter. |
| `output\cover_letters\` | One PDF (for uploading) and one TXT (for pasting) per job |
| `output\screenshots\` | What each form looked like at the review step |
| `data\jobs.db` | The agent's database. Delete it to start completely fresh. |

Job statuses:

| Status | Meaning |
|---|---|
| `scored` | Waiting to be applied to |
| `applied` | Submitted |
| `external` | The job only accepts applications on the company's own site. The cover letter is ready, so apply there yourself using the link in the spreadsheet. |
| `skipped` | You chose not to apply |
| `failed` | The agent couldn't finish; see the Notes column |
| `filtered` | Removed by your keyword filters |

## Costs

Scoring uses `claude-opus-5` by default and costs about **$0.02 per job**. A cover letter costs about $0.05. Every command prints its estimated cost when it finishes.

To reduce costs:

- Set `models.scoring: claude-sonnet-5` in `config.yaml`. This makes scoring about 60% cheaper.
- Tighten `filters.title_exclude`. Filtered jobs never reach Claude.

## Safety and limits

- **LinkedIn doesn't allow automated applications.** Using this agent can get your account restricted. To lower the risk, the agent:
  - uses a visible Chrome window and pauses randomly between actions;
  - waits 30–90 seconds between applications;
  - stops at **daily caps** (LinkedIn 15, Indeed 10, ZipRecruiter 10), which you can change in `config.yaml`.
- If a site shows a CAPTCHA, a security check or "unusual activity", the agent **beeps and stops**. You deal with it yourself in the browser; the agent never tries to get past these.
- Your saved sign-ins live in `%LOCALAPPDATA%\jobagent\browser_profiles`, outside OneDrive, so OneDrive syncing doesn't interfere with Chrome.
- The text of your CV, cover letter, profile and job description is sent to the Claude API to score jobs and write letters.

## Known limitations

- **Site layouts change.** The first real runs may show that a button or page has changed. When that happens, the agent hands you the browser (the same as `m`) instead of guessing. Each site's button texts are at the top of `jobagent/appliers/<site>.py`.
- **Indeed and ZipRecruiter auto-apply is best-effort.** Many of their jobs send you to company websites, and those become `external`.
- **ZipRecruiter blocks automated search requests,** so ZipRecruiter searches run in your signed-in Chrome window. They're slower, and you may have to complete a check yourself.
- **LinkedIn search results don't show whether a job uses Easy Apply.** Jobs that turn out to need the company site are marked `external`.

## Moving to another PC

- Copy the folder **without** `.venv`, then run `setup.bat` on the new PC.
- Copy `data\jobs.db` too if you want to keep your job history and remembered answers.
- Site sign-ins aren't copied, so run the three `login` commands again on the new PC.
- Keep `.env` private: it holds your API key.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

The tests use a fake Claude and local test pages, so they cost nothing and never touch your accounts.
