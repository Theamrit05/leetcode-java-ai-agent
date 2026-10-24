import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

LEETCODE_GRAPHQL = "https://leetcode.com/graphql"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
REQUEST_TIMEOUT_SECONDS = 120
MAX_ATTEMPTS = 3


class ProblemTextParser(HTMLParser):
    """Convert LeetCode statement HTML to readable plain text."""
    BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "pre", "h1", "h2", "h3", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "code":
            self.parts.append("`")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "code":
            self.parts.append("`")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", value)
        return value.strip()


def post_json(url, payload, headers=None, timeout=REQUEST_TIMEOUT_SECONDS):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "LeetCode-Java-AI-Agent/1.1",
            **(headers or {}),
        },
    )
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Read a bounded error body without printing credentials or URLs with query strings.
            detail = exc.read(2000).decode("utf-8", errors="replace")
            last_error = RuntimeError(
                f"HTTP {exc.code} from {url.split('?')[0]}: {detail}"
            )
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == MAX_ATTEMPTS:
                raise last_error from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = RuntimeError(
                f"Network request to {url.split('?')[0]} failed (attempt {attempt}/{MAX_ATTEMPTS}): {exc}"
            )
            if attempt == MAX_ATTEMPTS:
                raise last_error from exc
        if attempt < MAX_ATTEMPTS:
            wait_seconds = min(5 * attempt, 15)
            print(f"Request failed; retrying in {wait_seconds}s (attempt {attempt + 1}/{MAX_ATTEMPTS})...")
            time.sleep(wait_seconds)
    raise last_error or RuntimeError("Request failed for an unknown reason.")


def get_daily_problem():
    query = """
    query questionOfToday {
      activeDailyCodingChallengeQuestion {
        date
        link
        question {
          title
          titleSlug
          difficulty
          content
          topicTags { name }
          codeSnippets { lang langSlug code }
        }
      }
    }
    """
    result = post_json(
        LEETCODE_GRAPHQL,
        {"query": query},
        headers={"Referer": "https://leetcode.com", "Origin": "https://leetcode.com"},
        timeout=45,
    )
    if result.get("errors"):
        raise RuntimeError("LeetCode GraphQL error: " + json.dumps(result["errors"])[:1000])
    item = result.get("data", {}).get("activeDailyCodingChallengeQuestion")
    if not item or not item.get("question"):
        raise RuntimeError("Could not retrieve today's challenge from LeetCode.")
    q = item["question"]
    java_template = next(
        (snippet.get("code", "") for snippet in q.get("codeSnippets", [])
         if snippet.get("langSlug") == "java"),
        ""
    )
    parser = ProblemTextParser()
    parser.feed(q.get("content", ""))
    return {
        "date": item.get("date", ""),
        "url": "https://leetcode.com" + item.get("link", ""),
        "title": q.get("title", ""),
        "slug": q.get("titleSlug", ""),
        "difficulty": q.get("difficulty", ""),
        "statement_text": parser.text(),
        "topics": [tag["name"] for tag in q.get("topicTags", [])],
        "java_template": java_template,
    }


def solve_with_gemini(problem):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Add it under Settings > Secrets and variables > Actions."
        )
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    prompt = f"""
You are helping a learner practice LeetCode. Solve this Daily Challenge in Java.
Return Markdown with:
1. A concise approach explanation.
2. Time and space complexity.
3. One complete LeetCode Java submission using the expected class/method signature
from the provided Java template, if available.
4. A short edge-case checklist.
Do not claim the solution was tested or accepted.

Title: {problem['title']}
Difficulty: {problem['difficulty']}
URL: {problem['url']}
Topics: {', '.join(problem['topics'])}
Java starter template:
{problem['java_template'] or '(not provided)'}

Problem statement:
{problem['statement_text']}
"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingLevel": "low"}
        }
    }
    result = post_json(
        endpoint,
        payload,
        headers={"x-goog-api-key": api_key},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if result.get("error"):
        raise RuntimeError("Gemini API error: " + json.dumps(result["error"])[:1000])
    candidates = result.get("candidates", [])
    if not candidates:
        feedback = result.get("promptFeedback", {})
        raise RuntimeError("Gemini returned no answer. Feedback: " + json.dumps(feedback)[:1000])
    parts = candidates[0].get("content", {}).get("parts", [])
    answer = "\n".join(part.get("text", "") for part in parts).strip()
    if not answer:
        finish_reason = candidates[0].get("finishReason", "unknown")
        raise RuntimeError(f"Gemini response was empty (finish reason: {finish_reason}).")
    return answer


def main():
    problem = get_daily_problem()
    print(f"Fetched: {problem['title']} ({problem['difficulty']})")
    answer = solve_with_gemini(problem)
    os.makedirs("output", exist_ok=True)
    date = problem.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", problem.get("slug") or "daily-challenge")
    path = f"output/{date}-{safe_slug}.md"
    with open(path, "w", encoding="utf-8") as file:
        file.write(f"# {problem['title']}\n\n")
        file.write(f"- **Date:** {date}\n- **Difficulty:** {problem['difficulty']}\n")
        file.write(f"- **Problem:** {problem['url']}\n\n")
        file.write("> AI-generated solution. It has not been executed against tests or submitted to LeetCode. Review before use.\n\n")
        file.write(answer + "\n")
    print(f"Generated solution: {path}")
    print(f"Problem: {problem['title']} ({problem['difficulty']})")
    print(f"Review at: {problem['url']}")


if __name__ == "__main__":
    main()
