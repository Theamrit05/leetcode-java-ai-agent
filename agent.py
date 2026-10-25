import json
import os
import random
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
PROBLEMS_PER_RUN = 10


class ProblemTextParser(HTMLParser):
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
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "LeetCode-Java-AI-Agent/2.0", **(headers or {})},
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
            last_error = RuntimeError(f"Network request failed (attempt {attempt}/{MAX_ATTEMPTS}): {exc}")
            if attempt == MAX_ATTEMPTS:
                raise last_error from exc
        if attempt < MAX_ATTEMPTS:
            delay = min(5 * attempt, 15)
            print(f"Request failed; retrying in {delay}s (attempt {attempt + 1}/{MAX_ATTEMPTS})...")
            time.sleep(delay)
    raise last_error or RuntimeError("Request failed.")


def get_problem_pool():
    query = """
    query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
      problemsetQuestionList(categorySlug: $categorySlug, limit: $limit, skip: $skip, filters: $filters) {
        total
        questions { title titleSlug difficulty questionFrontendId }
      }
    }
    """
    pool = []
    # Draw a large random sample from Easy and Medium; avoid premium-only questions.
    for difficulty in ("EASY", "MEDIUM"):
        result = post_json(LEETCODE_GRAPHQL, {
            "query": query,
            "variables": {"categorySlug": "", "limit": 100, "skip": random.randint(0, 1500),
                          "filters": {"difficulty": difficulty}}
        }, headers={"Referer": "https://leetcode.com/problemset/", "Origin": "https://leetcode.com"}, timeout=45)
        if result.get("errors"):
            raise RuntimeError("LeetCode problem list error: " + json.dumps(result["errors"])[:800])
        data = result.get("data", {}).get("problemsetQuestionList") or {}
        pool.extend(data.get("questions") or [])
    unique = {}
    for item in pool:
        if item.get("titleSlug") and item.get("difficulty") in ("Easy", "Medium"):
            unique[item["titleSlug"]] = item
    candidates = list(unique.values())
    if len(candidates) < PROBLEMS_PER_RUN:
        raise RuntimeError(f"LeetCode returned only {len(candidates)} eligible random problems.")
    return random.sample(candidates, PROBLEMS_PER_RUN)


def get_problem_details(item):
    query = """
    query questionData($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
        questionId questionFrontendId title titleSlug difficulty content
        topicTags { name }
        codeSnippets { lang langSlug code }
      }
    }
    """
    result = post_json(LEETCODE_GRAPHQL, {"query": query, "variables": {"titleSlug": item["titleSlug"]}},
        headers={"Referer": "https://leetcode.com", "Origin": "https://leetcode.com"}, timeout=45)
    if result.get("errors"):
        raise RuntimeError("LeetCode question detail error: " + json.dumps(result["errors"])[:800])
    q = result.get("data", {}).get("question")
    if not q or not q.get("content"):
        raise RuntimeError("Could not retrieve problem details for " + item["titleSlug"])
    parser = ProblemTextParser()
    parser.feed(q["content"])
    java_template = next((s.get("code", "") for s in q.get("codeSnippets", []) if s.get("langSlug") == "java"), "")
    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "url": "https://leetcode.com/problems/" + q["titleSlug"] + "/",
        "title": q["title"], "slug": q["titleSlug"],
        "question_id": str(q.get("questionFrontendId") or q.get("questionId") or ""),
        "difficulty": q.get("difficulty", item.get("difficulty", "")),
        "statement_text": parser.text(),
        "topics": [tag["name"] for tag in q.get("topicTags", [])],
        "java_template": java_template,
    }


def solve_with_gemini(problem):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it in repository Actions secrets.")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    prompt = f"""
Solve this LeetCode problem in Java. Return a concise explanation, algorithm, time and space
complexity, and exactly one complete LeetCode Java submission inside a standard triple-backtick
java code block. Use the supplied class/method signature. Do not include main().
Do not claim the code has been tested or accepted.

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


def main():
    os.makedirs("output", exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    problems = get_problem_pool()
    print(f"Selected {len(problems)} random Easy/Medium LeetCode problems.")
    index_lines = [f"# Random LeetCode Java Practice — {date}", "",
                   "Ten randomly selected Easy/Medium problems with AI-generated Java solutions.",
                   "These are generated for review; this workflow does not submit them to your LeetCode account.", ""]
    failures = []
    for number, item in enumerate(problems, start=1):
        try:
            problem = get_problem_details(item)
            print(f"[{number}/{PROBLEMS_PER_RUN}] Solving: {problem['title']} ({problem['difficulty']})")
            answer = solve_with_gemini(problem)
            safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", problem["slug"])
            path = f"output/{date}-{number:02d}-{safe_slug}.md"
            with open(path, "w", encoding="utf-8") as file:
                file.write(f"# {number}. {problem['title']}\n\n")
                file.write(f"- **Difficulty:** {problem['difficulty']}\n- **Problem:** {problem['url']}\n")
                file.write(f"- **Topics:** {', '.join(problem['topics'])}\n\n")
                file.write("> AI-generated solution. Review and test the code before submitting.\n\n")
                file.write(answer + "\n")
            index_lines.append(f"{number}. [{problem['title']}]({problem['url']}) — {problem['difficulty']} — [solution file]({os.path.basename(path)})")
            print(f"Saved: {path}")
        except Exception as exc:
            failures.append(f"{number}. {item.get('title', item.get('titleSlug', 'Unknown'))}: {exc}")
            print(f"Problem {number} failed: {exc}")
    index_lines.extend(["", f"**Completed:** {PROBLEMS_PER_RUN - len(failures)}/{PROBLEMS_PER_RUN}"])
    if failures:
        index_lines.extend(["", "## Problems that failed to generate", *[f"- {failure}" for failure in failures]])
    with open(f"output/{date}-index.md", "w", encoding="utf-8") as file:
        file.write("\n".join(index_lines) + "\n")
    print(f"Finished: {PROBLEMS_PER_RUN - len(failures)}/{PROBLEMS_PER_RUN} solutions generated.")
    if len(failures) == PROBLEMS_PER_RUN:
        raise RuntimeError("All 10 problem generations failed.")


if __name__ == "__main__":
    main()
