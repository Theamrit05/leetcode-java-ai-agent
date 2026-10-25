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
SUBMIT_TO_LEETCODE = os.getenv("SUBMIT_TO_LEETCODE", "true").lower() == "true"
LEETCODE_SUBMISSION_TIMEOUT = 90


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
    # Use LeetCode's problem-status endpoint instead of the obsolete
    # problemsetQuestionList GraphQL field (which now returns HTTP 400).
    request = urllib.request.Request(
        "https://leetcode.com/api/problems/all/",
        headers={**leetcode_auth_headers(), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not load LeetCode problem list: {exc}") from exc

    candidates = []
    for pair in payload.get("stat_status_pairs", []):
        stat = pair.get("stat") or {}
        slug = stat.get("question__title_slug")
        level = pair.get("difficulty", {}).get("level")
        # LeetCode status 'ac' means the user has already solved the problem.
        if not slug or pair.get("status") == "ac" or level not in (1, 2) or pair.get("paid_only", False):
            continue
        candidates.append({
            "title": stat.get("question__title", slug),
            "titleSlug": slug,
            "difficulty": "Easy" if level == 1 else "Medium",
            "questionFrontendId": stat.get("frontend_question_id"),
        })
    # Deduplicate and randomly choose ten unsolved, non-premium Easy/Medium questions.
    unique = {item["titleSlug"]: item for item in candidates}
    if len(unique) < PROBLEMS_PER_RUN:
        raise RuntimeError(
            f"LeetCode returned only {len(unique)} unsolved Easy/Medium questions for this account."
        )
    print(f"Found {len(unique)} unsolved Easy/Medium questions.")
    return random.sample(list(unique.values()), PROBLEMS_PER_RUN)


def leetcode_auth_headers():
    session = os.environ.get("LEETCODE_SESSION", "").strip()
    csrf = os.environ.get("LEETCODE_CSRF_TOKEN", "").strip()
    if not session or not csrf:
        raise RuntimeError("LEETCODE_SESSION and LEETCODE_CSRF_TOKEN must be configured in GitHub Actions secrets.")
    return {"Cookie": f"LEETCODE_SESSION={session}; csrftoken={csrf}", "x-csrftoken": csrf,
            "Referer": "https://leetcode.com/", "Origin": "https://leetcode.com",
            "User-Agent": "Mozilla/5.0 LeetCode-Java-AI-Agent/3.0"}


def get_solved_slugs():
    request = urllib.request.Request("https://leetcode.com/api/problems/all/",
        headers={**leetcode_auth_headers(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not read solved-problem history from LeetCode: {exc}") from exc
    solved = set()
    for pair in payload.get("stat_status_pairs", []):
        if pair.get("status") == "ac":
            slug = (pair.get("stat") or {}).get("question__title_slug")
            if slug:
                solved.add(slug)
    print(f"LeetCode reports {len(solved)} solved problems for this session.")
    return solved


def extract_java_code(answer, java_template=""):
    fence = chr(96) * 3
    match = re.search(re.escape(fence) + r"java\s*(.*?)" + re.escape(fence), answer, re.IGNORECASE | re.DOTALL)
    if not match:
        match = re.search(re.escape(fence) + r"\w*\s*(.*?)" + re.escape(fence), answer, re.DOTALL)
    if not match:
        raise RuntimeError("AI response did not contain a fenced Java solution.")
    code = match.group(1).strip()
    # Design problems often require a named class (e.g. Trie or LRUCache), not Solution.
    if not code or not re.search(r"\bclass\s+[A-Za-z_$][\w$]*", code):
        raise RuntimeError("Extracted Java code does not contain a valid class declaration.")
    expected = re.search(r"\bclass\s+([A-Za-z_$][\w$]*)", java_template or "")
    actual = re.search(r"\bclass\s+([A-Za-z_$][\w$]*)", code)
    if expected and actual and expected.group(1) != actual.group(1):
        raise RuntimeError(f"Java class name mismatch: expected {expected.group(1)}, got {actual.group(1)}.")
    return code


def submit_to_leetcode(problem, java_code):
    headers = {**leetcode_auth_headers(), "Content-Type": "application/json"}
    payload = {"lang": "java", "question_id": str(problem["question_id"]), "typed_code": java_code}
    result = post_json(f"https://leetcode.com/problems/{problem['slug']}/submit/", payload,
                       headers=headers, timeout=45)
    submission_id = result.get("submission_id")
    if not submission_id:
        raise RuntimeError("LeetCode did not return a submission ID: " + json.dumps(result)[:1000])
    print(f"Submitted {problem['title']} (submission ID {submission_id}); waiting for judge...")
    check_url = f"https://leetcode.com/submissions/detail/{submission_id}/check/"
    deadline, last_status = time.time() + LEETCODE_SUBMISSION_TIMEOUT, {}
    while time.time() < deadline:
        time.sleep(3)
        req = urllib.request.Request(check_url, headers={**headers, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                last_status = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            print(f"Judge status poll failed; retrying: {exc}")
            continue
        status = last_status.get("status_msg") or last_status.get("state") or ""
        if status and status not in ("Pending", "STARTED", "PENDING", "Judging"):
            result = {"status": status, "submission_id": submission_id}
            if status != "Accepted":
                result["details"] = last_status.get("full_compile_error") or last_status.get("runtime_error") or status
            return result
    return {"status": "Timed out waiting for judge", "submission_id": submission_id,
            "details": json.dumps(last_status)[:1000]}


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


def solve_with_ollama(problem):
    api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OLLAMA_API_KEY is missing. Add it in repository Actions secrets.")
    model = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")
    prompt = f"""
Solve this LeetCode problem in Java. Return a concise explanation, algorithm, time and space
complexity, and exactly one complete LeetCode Java submission inside a standard triple-backtick
java code block. Preserve the exact class name and method/constructor signatures in the supplied starter template. For design problems, implement the named class (such as LRUCache or Trie), not class Solution. For ordinary algorithm problems, use class Solution if the template does. Do not include main().
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
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an expert competitive programmer. Produce correct, efficient Java solutions that match LeetCode's exact method signature."},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "think": False
    }
    result = post_json(
        "https://ollama.com/api/chat",
        payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    message = result.get("message") or {}
    answer = (message.get("content") or "").strip()
    if not answer:
        raise RuntimeError("Ollama returned an empty answer: " + json.dumps(result)[:1000])
    return answer



def repair_with_ollama(problem, previous_answer, compile_error, attempt):
    """Ask the model to repair a failed submission using LeetCode's actual judge feedback."""
    api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    model = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")
    prompt = f"""
Your previous Java solution for this LeetCode problem failed to compile.
Repair it using the exact compiler feedback below. Return the complete corrected solution
in exactly one ```java code block. Preserve LeetCode's exact class name and method/constructor signatures from the starter template. For design problems, implement the named class rather than class Solution. Do not include main(), prose inside the code block, or tests.

Problem: {problem['title']}
Difficulty: {problem['difficulty']}
Starter template:
{problem['java_template'] or '(not provided)'}

Problem statement:
{problem['statement_text']}

Previous answer:
{previous_answer}

LeetCode compiler feedback:
{compile_error}

This is repair attempt {attempt}. Fix the specific compilation issue; do not merely explain it.
"""
    result = post_json(
        "https://ollama.com/api/chat",
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are an expert Java competitive programmer. Correct compilation errors precisely and follow LeetCode method signatures exactly."},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "think": False
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    answer = ((result.get("message") or {}).get("content") or "").strip()
    if not answer:
        raise RuntimeError("Ollama returned an empty repair response.")
    return answer

def main():
    os.makedirs("output", exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    problems = get_problem_pool()
    print(f"Selected {len(problems)} random Easy/Medium LeetCode problems.")
    index_lines = [f"# Random LeetCode Java Practice — {date}", "",
                   "Ten randomly selected unsolved Easy/Medium problems with AI-generated Java solutions.",
                   ("This run attempts real LeetCode submissions and records the judge result." if SUBMIT_TO_LEETCODE else "Submission is disabled; solutions are generated for review."), ""]
    failures = []
    for number, item in enumerate(problems, start=1):
        try:
            problem = get_problem_details(item)
            print(f"[{number}/{PROBLEMS_PER_RUN}] Solving: {problem['title']} ({problem['difficulty']})")
            answer = solve_with_ollama(problem)
            submission = None
            if SUBMIT_TO_LEETCODE:
                java_code = extract_java_code(answer, problem.get('java_template', ''))
                submission = submit_to_leetcode(problem, java_code)
                print(f"Judge result: {submission['status']}")
                # Automatically attempt up to two targeted repairs for compile errors.
                for repair_attempt in range(1, 3):
                    if submission.get("status") != "Compile Error":
                        break
                    compiler_feedback = submission.get("details") or "Compile Error (no details returned)"
                    print(f"Compile error detected; requesting targeted repair {repair_attempt}/2...")
                    answer = repair_with_ollama(problem, answer, compiler_feedback, repair_attempt)
                    java_code = extract_java_code(answer, problem.get('java_template', ''))
                    submission = submit_to_leetcode(problem, java_code)
                    print(f"Judge result after repair {repair_attempt}: {submission['status']}")
            safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", problem["slug"])
            path = f"output/{date}-{number:02d}-{safe_slug}.md"
            with open(path, "w", encoding="utf-8") as file:
                file.write(f"# {number}. {problem['title']}\n\n")
                file.write(f"- **Difficulty:** {problem['difficulty']}\n- **Problem:** {problem['url']}\n")
                file.write(f"- **Topics:** {', '.join(problem['topics'])}\n\n")
                file.write("> AI-generated solution. Verify the judge result below.\n\n")
                if submission:
                    file.write(f"**LeetCode submission:** {submission['status']} (ID: {submission['submission_id']})\n\n")
                    if submission.get("details"):
                        file.write(f"**Judge details:** {submission['details']}\n\n")
                file.write(answer + "\n")
            result_label = submission["status"] if submission else "Generated only"
            index_lines.append(f"{number}. [{problem['title']}]({problem['url']}) — {problem['difficulty']} — **{result_label}** — [solution file]({os.path.basename(path)})")
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
