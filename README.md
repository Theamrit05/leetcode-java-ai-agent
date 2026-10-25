# LeetCode Java AI Agent

A GitHub Actions workflow that selects unsolved, non-premium Easy/Medium LeetCode problems and asks Ollama Cloud to draft Java solutions. It can run on a daily schedule in GitHub Actions, so a PC is not required.

## Current configuration

- Model provider: Ollama Cloud API
- Model: `gpt-oss:120b`
- Schedule: daily at 20:00 India Standard Time
- Target: up to 10 randomly selected unsolved Easy/Medium problems
- Output: Markdown solution reports uploaded as a workflow artifact
- Submission: enabled when valid LeetCode session secrets are configured

## Required GitHub Actions secrets

In repository Settings → Secrets and variables → Actions, configure:

1. `OLLAMA_API_KEY`
2. `LEETCODE_SESSION`
3. `LEETCODE_CSRF_TOKEN`

Never paste these values into chat, issues, code, or logs. Treat LeetCode session/CSRF values like passwords. If a session expires, renew the secrets through a trusted browser session.

## Run from a phone

1. Open the repository's Actions tab.
2. Select **Daily 10 Random LeetCode Java Solvers**.
3. Tap **Run workflow** if a manual run is needed.
4. Open the completed run and download the `leetcode-java-10-random-solutions` artifact.

## Reliability and limitations

- The agent asks Ollama to generate a Java solution and submits it to LeetCode when submission is enabled.
- For Compile Error, Wrong Answer, and Runtime Error results, it can request up to two judge-feedback-guided repair attempts.
- The agent records judge details and a status summary in the output report. A green GitHub Actions run only means the workflow finished; it does not mean all submissions were Accepted.
- Class-name mismatches are aligned to the class name in the LeetCode starter template when detected.
- Some problems may be unavailable, disabled for submission, or fail to return details; a run may therefore produce fewer than ten successful submissions.
- Ollama free usage is limited and may run out or be throttled. There is no unlimited-free guarantee.
- LeetCode's submission endpoints are undocumented and can change. Authentication may expire, and rate limits or service errors can interrupt a run.
- AI-generated code is not guaranteed to pass hidden tests. Review results rather than assuming acceptance.

## Security

- Keep all API keys and session credentials in GitHub Secrets only.
- Never commit credentials into repository files.
- If a LeetCode session secret may have leaked, sign out of LeetCode to invalidate sessions and configure new secrets.
