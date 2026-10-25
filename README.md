# LeetCode Java AI Agent

A GitHub Actions workflow that fetches the current LeetCode Daily Challenge and asks Gemini to draft a Java solution. It runs in the cloud, so your phone can be switched off.

## Setup

1. In repository Settings → Secrets and variables → Actions, add GEMINI_API_KEY.
2. To enable automatic submission, also add LEETCODE_SESSION and LEETCODE_CSRF_TOKEN as repository secrets. Never paste these values into chat, issues, code, or logs.
3. Open Actions and enable workflows if GitHub prompts you.
4. The workflow is scheduled daily at 06:00 India Standard Time. You can also run it from Actions → Daily LeetCode Java Solver → Run workflow.
5. Open the completed run and download the leetcode-java-daily-solution artifact. The Markdown report records the submission result when available.

## Automatic submission notes

- When both LeetCode secrets are present, the agent extracts the Java code block, submits it to LeetCode, and polls the judge result.
- If either secret is missing, it generates the solution but skips submission.
- LeetCode's submission endpoints are undocumented and may change; authentication may expire and require renewing secrets.
- AI-generated code may be wrong. Submission is automatic and can use a daily attempt; review the report and LeetCode result when possible. An Accepted result is only recorded when LeetCode reports it.
- This project does not guarantee hidden-test correctness or LeetCode availability.

## Security

- Keep GEMINI_API_KEY, LEETCODE_SESSION, and LEETCODE_CSRF_TOKEN in GitHub Secrets only.
- Treat LeetCode session/CSRF values like passwords. Never share them with anyone or commit them into repository files.
- If a session secret may have leaked, sign out of LeetCode to invalidate sessions and configure new secrets.
- Gemini API free-tier availability and quotas depend on Google's current terms and account.
