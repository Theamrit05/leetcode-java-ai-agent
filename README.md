# LeetCode Java AI Agent

A GitHub Actions workflow that fetches the current LeetCode Daily Challenge and asks Gemini to draft a Java solution. It runs in the cloud, so your phone can be switched off.

## Setup

1. In repository **Settings → Secrets and variables → Actions**, add a repository secret named `GEMINI_API_KEY`.
2. Open **Actions** and enable workflows if GitHub prompts you.
3. Open **Daily LeetCode Java Solver** and select **Run workflow** to test it.
4. Open the completed run and download the `leetcode-java-daily-solution` artifact.

The workflow is scheduled daily at **06:00 India Standard Time**. GitHub scheduled workflows may start late, and public-repository schedules can be subject to GitHub limits.

## Important limitations

- This generates a proposed solution; it does not run LeetCode's hidden test cases or submit to your LeetCode account.
- Check the solution and LeetCode's current rules before submitting. No acceptance is guaranteed.
- Gemini API free-tier availability and quotas depend on Google's current terms and your account.
- Keep `GEMINI_API_KEY` in GitHub Secrets. Never commit it into a file or share it publicly.
