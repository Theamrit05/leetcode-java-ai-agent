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
LEETCODE_SUBMISSION_TIMEOUT = 60


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
    pool = list(unique.values())
    random.shuffle(pool)
    return pool


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
    """Extract Java from fenced or unfenced model output and align the starter class name."""
    fence = chr(96) * 3
    match = re.search(
        re.escape(fence) + r"\s*(?:java|Java)?\s*\n?(.*?)" + re.escape(fence),
        answer or "", re.IGNORECASE | re.DOTALL
    )
    if match:
        code = match.group(1).strip()
    else:
        # Some model responses (especially repair responses) omit markdown fences.
        # Accept only text that looks like Java source; never submit prose as code.
        raw = (answer or "").strip()
        raw = re.sub(r"^\s*(?:Here(?:'s| is) the corrected code|Corrected Java solution|Java solution)\s*:?\s*", "", raw, flags=re.I)
        raw = re.sub(r"^\s*" + re.escape(fence) + r"(?:java)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*" + re.escape(fence) + r"\s*$", "", raw)
        if re.search(r"\b(class|interface|enum)\s+[A-Za-z_$][\w$]*", raw) and (
            "{" in raw and (";" in raw or "}" in raw)
        ):
            code = raw
            print("Model omitted Java code fences; accepted validated raw Java source.")
        else:
            raise RuntimeError("AI response did not contain a fenced Java solution or recognizable raw Java source.")

    if not code or not re.search(r"\b(class|interface|enum)\s+[A-Za-z_$][\w$]*", code):
        raise RuntimeError("Extracted response does not contain a valid Java type declaration.")

    expected = re.search(r"\bclass\s+([A-Za-z_$][\w$]*)", java_template or "")
    actual = re.search(r"\bclass\s+([A-Za-z_$][\w$]*)", code)
    if expected and actual and expected.group(1) != actual.group(1):
        old_name, required_name = actual.group(1), expected.group(1)
        code = re.sub(r"\bclass\s+" + re.escape(old_name) + r"\b", "class " + required_name, code, count=1)
        code = re.sub(r"\b(public|protected|private)\s+" + re.escape(old_name) + r"\s*(?=\()", r"\1 " + required_name, code)
        print(f"Adjusted generated class name to match starter template: {required_name}.")
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
                feedback_fields = ("full_compile_error", "runtime_error", "last_testcase", "expected_output", "code_output", "std_output", "status_runtime")
                feedback = {key: last_status[key] for key in feedback_fields if last_status.get(key)}
                result["details"] = json.dumps(feedback, ensure_ascii=False)[:5000] if feedback else status
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


def gemini_generate(prompt, system_instruction):
    """Call the Gemini API using the official generateContent REST endpoint."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it in GitHub Actions repository secrets.")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }
    result = post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        payload,
        headers={"x-goog-api-key": api_key},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    candidates = result.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    answer = "\\n".join(part.get("text", "") for part in parts if part.get("text")).strip()
    if not answer:
        feedback = result.get("promptFeedback") or {}
        raise RuntimeError("Gemini returned an empty response: " + json.dumps(feedback or result)[:1200])
    return answer


def solve_with_gemini(problem):
    prompt = f"""
Solve this LeetCode problem in Java. Optimize for correctness on hidden tests and efficient runtime.
Before writing the final answer, silently do a rigorous contest-editorial review:
1. Identify constraints and derive the intended optimal algorithm.
2. Check the proof of correctness and time/space complexity.
3. Walk through examples and relevant edge cases: empty/singleton input, duplicates, boundaries,
   overflow, negative values, disconnected graphs, and maximum constraints.
4. Search for counterexamples to your own approach and fix any flaw before answering.
5. Ensure Java syntax, imports, types, and exact LeetCode method signatures compile.
Do not output this checklist or multiple alternative solutions.

Return a concise explanation, algorithm, time and space complexity, and exactly one complete LeetCode Java submission inside a standard triple-backtick java code block. Preserve the exact class name and method/constructor signatures in the supplied starter template. For design problems, implement the named class (such as LRUCache or Trie), not class Solution. For ordinary algorithm problems, use class Solution if the template does. Do not include main().
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
    return gemini_generate(
        prompt,
        "You are a senior competitive-programming engineer specializing in correct LeetCode Java submissions. Prefer the proven optimal algorithm, carefully verify invariants and edge cases before finalizing, and match the exact starter template. Correctness matters more than a rushed guess."
    )


def repair_with_gemini(problem, previous_answer, judge_feedback, attempt, failure_status="Compile Error"):
    """Repair a failed solution using LeetCode judge feedback."""
    prompt = f"""
The previous Java submission failed with status: {failure_status}. Diagnose the specific root cause from judge feedback and produce a corrected solution; do not repeat the previous algorithm if its logic is wrong. Return exactly one
complete Java code block and no other code block. Preserve the exact public class name and
all method/constructor signatures required by the starter template. Treat the starter template
as authoritative. Do not include main(), package declarations, markdown inside the code block,
or invented helper APIs. For algorithm problems use class Solution only when the starter does.
For design problems implement the exact named class and every required constructor/method.
Check edge cases, integer bounds, empty inputs, duplicates, and algorithmic complexity.

Title: {problem['title']}
Difficulty: {problem['difficulty']}
Starter template (follow exactly):
{problem['java_template'] or '(not provided)'}

Full problem statement:
{problem['statement_text']}

Previous answer:
{previous_answer}

Judge feedback:
{judge_feedback}

Repair attempt {attempt}. Re-evaluate the algorithm from the statement and examples. If the
feedback is Wrong Answer, reconsider the logic and edge cases.
"""
    return gemini_generate(
        prompt,
        "You are a meticulous senior Java competitive programmer. Diagnose judge feedback, challenge the previous reasoning, mentally test edge cases, and output one complete corrected submission matching the exact starter template. Never invent class or method names."
    )

def main():
    os.makedirs("output", exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pool = get_problem_pool()
    random.shuffle(pool)
    print(f"Loaded {len(pool)} unsolved Easy/Medium candidates.")

    # Keep trying distinct unsolved candidates until 10 are Accepted or the
    # entire eligible pool / an external hard limit is exhausted.
    candidates = pool
    max_candidates = len(candidates)
    accepted = 0
    attempted = 0
    reports = []
    judge_counts = {}
    failures = []
    seen_slugs = set()

    for item in candidates:
        if accepted >= PROBLEMS_PER_RUN:
            break
        slug = item.get("titleSlug")
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        attempted += 1
        problem = None
        answer = ""
        submission = None
        error_text = ""
        try:
            problem = get_problem_details(item)
            print(f"[Candidate {attempted}/{max_candidates}; Accepted {accepted}/{PROBLEMS_PER_RUN}] Solving: {problem['title']} ({problem['difficulty']})")
            answer = solve_with_gemini(problem)

            if SUBMIT_TO_LEETCODE:
                # If the model returns prose/invalid formatting, ask it again before
                # abandoning this problem. This is separate from judge-based repairs.
                for format_attempt in range(1, 4):
                    try:
                        java_code = extract_java_code(answer, problem.get("java_template", ""))
                        break
                    except RuntimeError as format_error:
                        if format_attempt >= 3:
                            raise
                        print(f"Invalid Java response; regenerating solution {format_attempt + 1}/3: {format_error}")
                        answer = solve_with_gemini(problem)
                submission = submit_to_leetcode(problem, java_code)
                print(f"Judge result: {submission['status']}")

                repairable_statuses = {"Compile Error", "Wrong Answer", "Runtime Error"}
                # One targeted repair keeps latency down; avoid repeatedly burning time on blind retries.
                for repair_attempt in range(1, 2):
                    if submission.get("status") not in repairable_statuses:
                        break
                    feedback = submission.get("details") or submission["status"]
                    print(f"{submission['status']} detected; requesting repair {repair_attempt}/2...")
                    answer = repair_with_gemini(
                        problem, answer, feedback, repair_attempt,
                        failure_status=submission["status"]
                    )
                    java_code = extract_java_code(answer, problem.get("java_template", ""))
                    submission = submit_to_leetcode(problem, java_code)
                    print(f"Judge result after repair {repair_attempt}: {submission['status']}")

            label = submission["status"] if submission else "Generated only"
            if submission:
                judge_counts[label] = judge_counts.get(label, 0) + 1
            if SUBMIT_TO_LEETCODE and label == "Accepted":
                accepted += 1

            safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", problem["slug"])
            path = f"output/{date}-{attempted:02d}-{safe_slug}.md"
            with open(path, "w", encoding="utf-8") as file:
                file.write(f"# {attempted}. {problem['title']}\n\n")
                file.write(f"- **Difficulty:** {problem['difficulty']}\n- **Problem:** {problem['url']}\n")
                file.write(f"- **Topics:** {', '.join(problem['topics'])}\n\n")
                file.write("> AI-generated solution. The judge status below is the recorded LeetCode result.\n\n")
                if submission:
                    file.write(f"**LeetCode submission:** {label} (ID: {submission.get('submission_id', 'unknown')})\n\n")
                    if submission.get("details"):
                        file.write(f"**Judge details:** {submission['details']}\n\n")
                file.write((answer or "No solution text was generated.") + "\n")
            reports.append((problem["title"], problem["url"], problem["difficulty"], label, os.path.basename(path)))
            print(f"Saved: {path}")
        except Exception as exc:
            error_text = str(exc)
            title = (problem or {}).get("title", item.get("title", slug or "Unknown"))
            failures.append(f"{title}: {error_text}")
            print(f"Candidate failed: {title}: {error_text}")
            # Don't burn the remaining quota if credentials are invalid or the model
            # provider explicitly rejects/rate-limits requests.
            lowered = error_text.lower()
            if any(term in lowered for term in (
                "401", "unauthorized", "invalid api key",
                "quota exceeded", "insufficient quota", "rate limit",
                "429", "leetcodesession", "csrf token"
            )):
                print("Stopping early: an authentication or provider-limit error needs attention.")
                break
            # Save a failure report too, so a skipped candidate is never invisible.
            safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug or f"candidate-{attempted}")
            path = f"output/{date}-{attempted:02d}-{safe_slug}-FAILED.md"
            with open(path, "w", encoding="utf-8") as file:
                file.write(f"# Failed candidate: {item.get('title', slug)}\n\n")
                file.write(f"- **Problem slug:** {slug}\n- **Error:** {error_text}\n")
            reports.append((item.get("title", slug), "https://leetcode.com/problems/" + str(slug) + "/", item.get("difficulty", ""), "Failed: " + error_text, os.path.basename(path)))

    index_lines = [
        f"# Daily LeetCode Java Agent — {date}", "",
        f"- **Accepted target:** {PROBLEMS_PER_RUN}",
        f"- **Accepted achieved:** {accepted if SUBMIT_TO_LEETCODE else 'Submission disabled'}",
        f"- **Candidates attempted:** {attempted}",
        f"- **Reports saved:** {len(reports)}", "",
        "A green workflow means the run completed, not that every submission was Accepted.",
        ""
    ]
    if SUBMIT_TO_LEETCODE:
        index_lines.extend(["## Judge summary", *[
            f"- **{status}:** {count}" for status, count in sorted(judge_counts.items())
        ]] if judge_counts else ["## Judge summary", "- No judge results recorded."])
    index_lines.extend(["", "## Candidate results"])
    for number, (title, url, difficulty, label, filename) in enumerate(reports, start=1):
        index_lines.append(f"{number}. [{title}]({url}) — {difficulty} — **{label}** — [report]({filename})")
    if failures:
        index_lines.extend(["", "## Errors", *[f"- {failure}" for failure in failures]])
    if SUBMIT_TO_LEETCODE and accepted < PROBLEMS_PER_RUN:
        index_lines.extend(["", f"**Target not reached:** {accepted}/{PROBLEMS_PER_RUN} Accepted after {attempted} candidates. The run stopped because candidates were exhausted or an external/API error occurred."])
    with open(f"output/{date}-index.md", "w", encoding="utf-8") as file:
        file.write("\n".join(index_lines) + "\n")

    print(f"Finished: {accepted}/{PROBLEMS_PER_RUN} Accepted; {attempted} candidates attempted; {len(reports)} reports saved.")
    if not reports:
        raise RuntimeError("No solution or failure reports were produced.")
    if SUBMIT_TO_LEETCODE and accepted < PROBLEMS_PER_RUN:
        # Fail the workflow visibly instead of showing a misleading green tick.
        raise RuntimeError(f"Accepted target not reached: {accepted}/{PROBLEMS_PER_RUN}. See the uploaded index/report for details.")


if __name__ == "__main__":
    main()
