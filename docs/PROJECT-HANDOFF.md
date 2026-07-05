# Intants AI Interview Platform — Development Report & Work Log

**Prepared by:** Jayanth Murala
**Email:** [jayanth.m@intants.com](mailto:jayanth.m@intants.com)
**Role:** Lead Developer
**Report date:** 2 June 2026
**Period covered:** 27 May 2026 – 2 June 2026

---

## 1. Summary

This report covers everything I have built on the Intants AI interview platform so far, the
decisions I took, the problems I ran into, the tools and services I used, and where the project
stands today. It also includes a day-by-day work log with timings.

This was an intense week. I was effectively running design, backend, frontend, real-time AI,
DevOps and security on my own, and the days were long, mostly between 13 and 16 hours, including
late nights and the weekend. The product is genuinely hard: it is a real-time, multilingual,
voice-and-video AI system that has to answer in under about two seconds, stay within a tight
cost cap, and meet strict government data rules. Getting it from an empty repository to a fully
working product in seven days took sustained, heavy effort.

The product lets a candidate log in, pick a job role and a language (English, Hindi or Telugu),
and have a spoken conversation with a realistic on-screen AI interviewer. The interviewer asks
about ten questions, listens, asks follow-ups, and at the end produces a scorecard on screen and
as a PDF. It is being built for the APSSDC Naipunyam skilling tender in Andhra Pradesh.

---

## 2. Work log (day by day)

The work windows are approximate. Commit timestamps fall within these windows; the windows also
include the design, research, debugging and testing time that does not show up as commits, which
is why the hours are higher than the commit times alone.


| Date        | Work window (approx.) | Hours | Focus                      | What I did                                                                                                                                                                                                                                                                                                                                                                                  |
| ----------- | --------------------- | ----- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 27 May 2026 | 08:30 – 22:00         | ~13   | Project setup & design     | Set up the repository and the team model. Locked the two-tier approach (a fast cloud demo now, a compliant AWS-Mumbai version for the bid later). Wrote the high-level and low-level design documents and went through the full tender requirements.                                                                                                                                        |
| 28 May 2026 | 09:00 – 22:00         | ~13   | Design & security          | Worked out the security model, the sprint plan, the cost target (≤ ₹12 per interview) and the DPDP privacy approach. Began evaluating avatar vendors (starting with D-ID).                                                                                                                                                                                                                  |
| 29 May 2026 | 08:00 – 00:30         | ~16   | Backend foundation         | First code committed. Built all four backend services, login and authentication, the DPDP consent system, the end-of-session scoring engine, the PDF scorecard, the Google and Naipunyam sign-on groundwork, and resume + job-description upload. (17 commits.)                                                                                                                             |
| 30 May 2026 | 09:00 – 23:30         | ~14   | Frontend rebuild           | Rebuilt the website on a proper design system with secure cookie-based login, interview and resume history, the avatar picker, and a full redesign of every page. (8 commits.)                                                                                                                                                                                                              |
| 31 May 2026 | 08:30 – 23:30         | ~15   | Real-time engine           | Replaced the fragile hand-built audio streaming with LiveKit (the standard used for professional video calls), wired up the first avatar (Simli), built the live interview screen, and cleared the review findings. Worked through to late-night commits. (6 commits.)                                                                                                                      |
| 1 June 2026 | 09:00 – 22:30         | ~13   | Avatar & interview flow    | Switched the demo avatar to Tavus, rebuilt the avatar picker on it, fixed the interview to exactly ten questions with a time cap, and connected scoring end to end. Tagged the first stable build (v1, v1.0). (3 commits.)                                                                                                                                                                  |
| 2 June 2026 | 08:00 – 00:00         | ~16   | Polish, admin, deploy prep | Polished the interview screen and its loading states, built the entire admin analytics dashboard (backend and frontend), added two more interviewers to reach six, translated the candidate screens into Hindi and Telugu, cleaned up the whole project, hardened every secret, containerised the system with Docker and ran it end to end, and wrote the deployment and security runbooks. |


**Total effort: approximately 100 hours across 7 days (average ~14 hours/day).**

---

## 3. What is working today

The candidate flow works from start to finish, both on a normal machine and inside Docker
talking to the live cloud services:

- Sign up / log in, including Google sign-on
- Privacy consent (required and enforced before any recording)
- Pick a job, an interviewer and a language
- A real-time spoken interview with a talking avatar (ten questions, around a twelve-minute cap)
- Automatic scoring on four areas: Communication, Technical Knowledge, Problem-Solving, Confidence
- A scorecard on screen and as a PDF
- Interview history and resume management

The admin side works too: a dashboard with overall numbers, a searchable and filterable list of
interviews, a drill-in into any candidate's scorecard, charts by role / language / score /
trend, and CSV export, all behind an admin login.

The system is four small backend services plus a worker process:

1. **interview_core** runs the live conversation. Its **worker** is the interviewer's brain during a call.
2. **data_gateway** handles login, users, consent, jobs, resumes and government sign-on.
3. **feedback_billing** runs the scoring and produces the PDF.
4. **admin_ops** powers the admin dashboard.

Every change is reviewed before it lands, and there are roughly 745 automated tests (about 512
on the backend and 233 on the frontend), all passing.

---

## 4. Tools and services I used (and where the accounts are created)

These are the third-party services the demo runs on. All of them are cloud accounts I set up and
configured; the keys live in the per-service `.env` files (never in the code).

**Demo stack (in use today):**


| Service                      | Used for                                          | Sign-up / console                                                                                   |
| ---------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Google Gemini (AI Studio)    | Main AI brain (questions, follow-ups, scoring)    | [https://aistudio.google.com/](https://aistudio.google.com/)                                        |
| Groq                         | Backup AI brain                                   | [https://console.groq.com/](https://console.groq.com/)                                              |
| Sarvam AI                    | Indian-language speech-to-text and text-to-speech | [https://dashboard.sarvam.ai/](https://dashboard.sarvam.ai/)                                        |
| Tavus                        | Talking avatar (demo)                             | [https://platform.tavus.io/](https://platform.tavus.io/)                                            |
| Simli                        | Alternative talking avatar                        | [https://app.simli.com/](https://app.simli.com/)                                                    |
| LiveKit Cloud                | Real-time audio/video transport for the live call | [https://cloud.livekit.io/](https://cloud.livekit.io/)                                              |
| Prisma Postgres / Neon       | Database                                          | [https://console.prisma.io/](https://console.prisma.io/) · [https://neon.tech/](https://neon.tech/) |
| Upstash                      | Redis cache / session store                       | [https://console.upstash.com/](https://console.upstash.com/)                                        |
| Backblaze B2 (S3-compatible) | Audio + report file storage                       | [https://www.backblaze.com/cloud-storage](https://www.backblaze.com/cloud-storage)                  |
| Cloudflare R2                | Alternative file storage                          | [https://dash.cloudflare.com/](https://dash.cloudflare.com/)                                        |
| OpenAI                       | Whisper speech-to-text fallback                   | [https://platform.openai.com/](https://platform.openai.com/)                                        |
| Resend                       | Transactional email                               | [https://resend.com/](https://resend.com/)                                                          |
| Sentry                       | Error monitoring (to be wired)                    | [https://sentry.io/](https://sentry.io/)                                                            |


**Hosting (for putting the demo online):**


| Service | Used for                                    | Sign-up / console                            |
| ------- | ------------------------------------------- | -------------------------------------------- |
| Railway | Hosting the 4 backend services + the worker | [https://railway.app/](https://railway.app/) |
| Vercel  | Hosting the website (frontend)              | [https://vercel.com/](https://vercel.com/)   |


**Production stack for the bid (Tier-2, not yet built):**


| Service                  | Used for                                                                                                   | Link                                                           |
| ------------------------ | ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| AWS Mumbai (ap-south-1)  | Bedrock (AI), RDS (database), ElastiCache (cache), S3 (storage), SES (email), EKS (hosting) — all in India | [https://aws.amazon.com/](https://aws.amazon.com/)             |
| Bhashini (Govt of India) | India-approved speech for the bid                                                                          | [https://bhashini.gov.in/](https://bhashini.gov.in/)           |
| AI4Bharat                | Speech fallback                                                                                            | [https://ai4bharat.iitm.ac.in/](https://ai4bharat.iitm.ac.in/) |


---

## 5. Key decisions I made (and why)


| Decision                                                                                                      | Reason                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Two versions of the same code (demo vs bid)                                                                   | Get something real working in a week without giving up the compliant version needed to win the tender.                                                   |
| Moved off D-ID, tried Simli, settled on Tavus for the demo avatar                                             | D-ID cost roughly ₹467 per interview (about forty times our cap) and was being retired.                                                                  |
| Our own avatar is the plan for the bid, not a paid vendor                                                     | Any paid avatar vendor breaks both the ₹12 cost cap and the rule that data must stay in India. Our own avatar costs almost nothing and keeps data local. |
| Rebuilt the live calls on LiveKit                                                                             | The hand-built audio path was unreliable; LiveKit is the proven standard.                                                                                |
| Gemini / Groq for the demo brain (Claude / Bedrock kept for the bid)                                          | No India-region guarantee and no Claude credit during the demo; Gemini and Groq are free-tier and fast.                                                  |
| Exactly ten questions with a time cap                                                                         | Keeps interviews consistent and keeps the cost per interview under control.                                                                              |
| Native Hindi and Telugu script (not Roman letters)                                                            | Roman text made the voice engine spell words out letter by letter; native script fixed it.                                                               |
| Cut features not asked for in the tender (multi-region backup, service mesh, per-question scoring, and so on) | Keep the demo focused. All of these can be added back if APSSDC asks.                                                                                    |


---

## 6. Problems I ran into and how I solved them

- **The interviewer sometimes did not talk.** The worker (the interviewer's brain) has to be
running before an interview starts, and at one point a leftover duplicate copy was quietly
taking the job and failing. I fixed the start order, made the whole stack start with one
command, and cleaned up the duplicates.
- **The avatar vendor changed three times in two weeks** (D-ID, then Simli, then Tavus) while I
chased something affordable, good quality and working on a free tier. Tavus is the demo
choice; the bid will use our own avatar.
- **Free-tier limits.** The Tavus free tier caps interviews at about three minutes and one at a
time, and I ran out of credit during testing and had to swap keys. Fine for one-at-a-time
demos, but a real demo will need a paid tier.
- **Login across two different web addresses.** On free hosting the website and the backend sit
on different domains, and the browser refuses to share the login cookie between them. I solved
it by routing the website's requests through itself to the backend, so everything looks like
one address and login works normally.
- **A hard-coded password in the code.** While tightening security I found the storage username
and password (minioadmin) written directly into the source. I removed it and made the app
refuse to start unless the real secret is supplied from the environment.
- **A crash on an unknown voice name.** The voice engine throws an error if it gets a speaker
name it does not recognise, so I verified every interviewer's voice against the official list
before shipping.
- **Windows development quirks.** PowerShell versus Linux commands, several Python environments,
the live-call libraries being installed in a way a plain Docker build would miss, and text-
encoding errors in the console. All handled and written down so they do not come back.
- **An admin page crash.** The "areas for improvement" on a scorecard are structured items, but
the admin page tried to print them as plain text and crashed. I caught it during local testing
and fixed it.

---

## 7. What made this difficult

- It is real-time voice and video AI at the same time. The candidate speaks, the AI has to
understand, decide the next question, reply, and a face has to lip-sync, all in under about two
seconds. That is hard even for large teams.
- Three languages from day one, in native script, for both the spoken conversation and the
on-screen text.
- The government rules are strict: data must stay in India, each interview must cost no more than
₹12, and it has to scale to twenty lakh users. Cheap, fast, compliant and scalable together is
a tough combination.
- A lot of moving parts, each swappable (the AI brain, the voice, the avatar, the login), which
is flexible but a lot to keep working together.
- It was built lean and fast, so the long hours and the discipline (reviews, tests,
documentation) had to do the work a bigger team would normally share.

---

## 8. Where things stand and what is next

What is done: the product works end to end on a machine and inside Docker against the real cloud
services. The deployment files and runbooks are written, the Docker images build and run, the
secrets are tightened, the documentation is accurate, and the tests pass.

What is not done yet: it is not live on the internet (that needs the code committed, the keys
rotated, and the hosting set up), and it is not yet the compliant bid version (the demo still
keeps data outside India and uses a paid avatar).

The plan from here:

- **Stage 2 – put the demo online.** Already containerised. Rotate the keys and deploy to
Railway (backend) and Vercel (website) so APSSDC can open it themselves. A few days.
- **Stage 3 – make it bid-ready.** Build our own avatar, move everything to AWS Mumbai, and
switch the AI brain and voice to the India-approved options. Several weeks; this is the big one.
- **Stage 4 – business pieces.** Real billing and invoicing, full admin analytics by district
and cohort, and the live government sign-on and data sync. A few weeks, can run alongside Stage 3.
- **Stage 5 – prove and certify.** Load-test for speed and scale, pass the mandatory CERT-In
security audit, finish the compliance paperwork, then go live.

---

## 9. How to run and deploy it

- **Run on a machine:** `.\dev-up.ps1` from the project root, then open `http://localhost:5173`.
- **Run the whole thing in Docker:** `docker compose build` then `docker compose up -d` (it uses
the cloud settings from the `.env` files).
- **Deploy online:** follow `docs/DEPLOY.md` (the steps) and `docs/DEPLOY-SECURITY.md` (key
rotation, the per-service settings, and the login fix).
- **Design and bid reference:** `docs/HLD.md`, `docs/LLD.md` and `docs/Final_stack.md` describe
the production target for the bid (not the current demo). `docs/CHANGES.md` records what was
cut and why.

---

## 10. Open risks to keep in mind

- The demo avatar (Tavus) is paid and rate-limited; budget for a paid tier for a real demo, and
remember the bid needs our own avatar regardless.
- The under-two-second response time is proven to work but has not yet been load-tested at lakhs
of users.
- Data stays outside India until the AWS-Mumbai move in Stage 3.
- Several keys were shared during development and should be rotated before any public link (the
checklist is in `docs/DEPLOY-SECURITY.md`).

---

Jayanth Murala
[jayanth.m@intants.com](mailto:jayanth.m@intants.com)