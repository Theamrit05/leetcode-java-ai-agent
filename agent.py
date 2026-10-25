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
    BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "pre", "h1", "h2", "h3", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "code":
            self.parts.append("\`")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "code":
            self.parts.append("\`")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", value)
        return value.strip()


def post_json(url, payload, headers=None, timeout=REQUEST_TIMEOUT_SECONDS):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "LeetCode-Java-AI-Agent/1.2", **(headers or {})},
    )
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code} from {url.split('?')[0]}: {detail}")
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == MAX_ATTEMPTS:
                raise last_error from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = RuntimeError(f"Network request to {url.split('?')[0]} failed (attempt {attempt}/{MAX_ATTEMPTS}): {exc}")
            if attempt == MAX_ATTEMPTS:
                raise last_error from exc
        if attempt < MAX_ATTEMPTS:
            delay = min(5 * attempt, 15)
            print(f"Request failed; retrying in {delay}s (attempt {attempt + 1}/{MAX_ATTEMPTS})...")
            time.sleep(delay)
    raise last_error or RuntimeError("Request failed.")


def get_daily_problem():
    query = """
    query questionOfToday {
      activeDailyCodingChallengeQuestion {
        date link
        question {
          questionId questionFrontendId title titleSlug difficulty content
          topicTags { name }
          codeSnippets { lang langSlug code }
        }
      }
    }
    """
    result = post_json(LEETCODE_GRAPHQL, {"query": query},
        headers={"Referer": "https://leetcode.com", "Origin": "https://leetcode.com"}, timeout=45)
    if result.get("errors"):
        raise RuntimeError("LeetCode GraphQL error: " + json.dumps(result["errors"])[:1000])
    item = result.get("data", {}).get("activeDailyCodingChallengeQuestion")
    if not item or not item.get("question"):
        raise RuntimeError("Could not retrieve today's challenge from LeetCode.")
    q = item["question"]
    java_template = next((s.get("code", "") for s in q.get("codeSnippets", []) if s.get("langSlug") == "java"), "")
    parser = ProblemTextParser()
    parser.feed(q.get("content", ""))
    return {
        "date": item.get("date", ""), "url": "https://leetcode.com" + item.get("link", ""),
        "title": q.get("title", ""), "slug": q.get("titleSlug", ""),
        "question_id": str(q.get("questionId") or q.get("questionFrontendId") or ""),
        "difficulty": q.get("difficulty", ""), "statement_text": parser.text(),
        "topics": [tag["name"] for tag in q.get("topicTags", [])], "java_template": java_template,
    }


def solve_with_gemini(problem):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it in repository Actions secrets.")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    prompt = f"""
Solve this LeetCode Daily Challenge in Java. Return a concise approach, complexity,
and exactly one complete Java LeetCode submission inside a tilde-fenced java code block.
Use the expected class/method signature from the supplied template. Avoid main().
Do not claim it has been tested or accepted.

Title: {problem['title']}
Difficulty: {problem['difficulty']}
URL: {problem['url']}
Topics: {', '.join(problem['topics'])}
Java starter template:
{problem['java_template'] or '(not provided)'}

Problem statement:
{problem['statement_text']}
"""
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"maxOutputTokens": 8192, "thinkingConfig": {"thinkingLevel": "low"}}}
    result = post_json(endpoint, payload, headers={"x-goog-api-key": api_key}, timeout=REQUEST_TIMEOUT_SECONDS)
    if result.get("error"):
        raise RuntimeError("Gemini API error: " + json.dumps(result["error"])[:1000])
    candidates = result.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no answer. Feedback: " + json.dumps(result.get("promptFeedback", {}))[:1000])
    answer = "\n".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", [])).strip()
    if not answer:
        raise RuntimeError("Gemini response was empty.")
    return answer


def extract_java_code(markdown):
    blocks = re.findall(r"~~~(?:java)?\s*\n(.*?)~~~", markdown, flags=re.IGNORECASE | re.DOTALL)
    if not blocks:
        raise RuntimeError("AI response did not contain a tilde-fenced Java code block; refusing to submit.")
    for block in blocks:
        if re.search(r"\bclass\s+Solution\b", block):
            return block.strip()
    return blocks[0].strip()


def get_json(url, headers=None, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 LeetCode-Java-AI-Agent/1.2", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"LeetCode status check failed with HTTP {exc.code}. Check the session/CSRF secrets.") from exc


def submit_to_leetcode(problem, java_code):
    session = os.environ.get("LEETCODE_SESSION", "").strip()
    csrf = os.environ.get("LEETCODE_CSRF_TOKEN", "").strip()
    if not session or not csrf:
        print("AUTO_SUBMIT_SKIPPED: Add LEETCODE_SESSION and LEETCODE_CSRF_TOKEN repository secrets to enable automatic submission.")
        return "SKIPPED — LeetCode authentication secrets are not configured."
    slug, question_id = problem["slug"], problem["question_id"]
    if not slug or not question_id:
        raise RuntimeError("Missing LeetCode problem slug/question ID; refusing to submit.")
    headers = {
        "Cookie": f"LEETCODE_SESSION={session}; csrftoken={csrf}", "x-csrftoken": csrf,
        "Referer": problem["url"], "Origin": "https://leetcode.com",
        "User-Agent": "Mozilla/5.0 LeetCode-Java-AI-Agent/1.2",
    }
    endpoint = f"https://leetcode.com/problems/{slug}/submit/"
    payload = {"lang": "java", "question_id": question_id, "typed_code": java_code,
               "judge_type": "large", "test_mode": False}
    response = post_json(endpoint, payload, headers=headers, timeout=60)
    submission_id = response.get("submission_id")
    if not submission_id:
        raise RuntimeError("LeetCode did not return a submission ID: " + json.dumps(response)[:1000])
    print(f"LeetCode submission created: {submission_id}")
    check_url = f"https://leetcode.com/submissions/detail/{submission_id}/check/"
    for attempt in range(1, 25):
        time.sleep(3)
        status = get_json(check_url, headers=headers, timeout=30)
        state = str(status.get("state", "")).upper()
        message = status.get("status_msg") or status.get("statusMsg") or state or "Pending"
        print(f"LeetCode judge status ({attempt}/24): {message}")
        if state == "SUCCESS" or status.get("status_code") is not None:
            result = str(status.get("status_msg") or message)
            if status.get("total_correct") is not None and status.get("total_testcases") is not None:
                result += f" — {status['total_correct']}/{status['total_testcases']} test cases"
            return result
    return f"SUBMITTED — judging status did not finish polling. Submission ID: {submission_id}"


def main():
    problem = get_daily_problem()
    print(f"Fetched: {problem['title']} ({problem['difficulty']})")
    answer = solve_with_gemini(problem)
    submission_result = submit_to_leetcode(problem, extract_java_code(answer))
    os.makedirs("output", exist_ok=True)
    date = problem.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", problem.get("slug") or "daily-challenge")
    path = f"output/{date}-{safe_slug}.md"
    with open(path, "w", encoding="utf-8") as file:
        file.write(f"# {problem['title']}\n\n")
        file.write(f"- **Date:** {date}\n- **Difficulty:** {problem['difficulty']}\n")
        file.write(f"- **Problem:** {problem['url']}\n- **LeetCode submission result:** {submission_result}\n\n")
        file.write("> AI-generated solution. Automatic submission results depend on LeetCode judging. Review the result before relying on it.\n\n")
        file.write(answer + "\n")
    print(f"Generated solution: {path}")
    print(f"Problem: {problem['title']} ({problem['difficulty']})")
    print(f"Submission result: {submission_result}")


if __name__ == "__main__":
    main()
