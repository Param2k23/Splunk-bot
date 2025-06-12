import os
import re
import subprocess
from git import Repo
from dotenv import load_dotenv
import json
from openai import AzureOpenAI
# Load environment variables
load_dotenv()

# --- Bot state to hold the pending fix ---
bot_state = {
    "pending_fix": None
}
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_API_BASE")
)

# --- Utility Functions ---

def extract_github_url(text: str):
    match = re.search(r"https://github\.com/\S+\.git", text)
    return match.group(0) if match else None

def parse_diagnostic_output(diagnostic_text: str) -> dict:
    # Fix: use a raw string for regex and match actual newline (\n)
    match = re.search(r"```json\s*(.*?)```", diagnostic_text, re.DOTALL)
    if not match:
        raise ValueError("No valid JSON block found in diagnostic output.")
    
    json_str = match.group(1).strip()
    data = json.loads(json_str)

    # Ensure required defaults
    return {
        "file_path": data["file_path"],
        "fix_text": data["fix"],
        "line_number": data.get("line_number", 1),
        "pr_type": data.get("pr_type", "hotfix")
    }

def refine_fix_with_context(file_path, fix_text, line_number):
    with open(file_path, "r") as f:
        lines = f.readlines()

    start = max(0, line_number - 11)
    end = min(len(lines), line_number + 10)
    code_context = "".join(lines[start:end])

    refinement_prompt = f"""
You previously suggested this fix for a bug:

```
{fix_text}
```

Here is the real source code context (10 lines before and after the target line):

```
{code_context}
```

🔧 Now update your fix so that it uses the correct variable names and syntax based on this actual code. 
Your output should only contain **the updated code block**, nothing else. 
Only give the code for the specific line that needs to be fixed, not the entire file or not the past and next lines. 
Also make sure that the code is syntactically correct and is not repeated in the file.
"""

    response = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        messages=[{"role": "user", "content": refinement_prompt}],
        temperature=0.2
    )

    fix = response.choices[0].message.content.strip()
    match = re.search(r"```(?:java)?\s*(.*?)```", fix, re.DOTALL)
    return match.group(1).strip() if match else fix

# --- GitHub PR Logic ---
WORKDIR = "workspace"
CLONE_DIR = os.path.join(WORKDIR, "repo")
GITHUB_USER = os.getenv("GITHUB_USER")


def clone_repo(repo_url):
    if os.path.exists(CLONE_DIR):
        print("Repo already exists. Pulling latest changes...")
        repo = Repo(CLONE_DIR)
        repo.git.checkout("main")
        repo.remotes.origin.pull()
    else:
        print("Cloning repo...")
        Repo.clone_from(repo_url, CLONE_DIR)

def apply_fix_and_push(file_path, fix_text, line_number, pr_type="hotfix"):
    branch_name = f"{pr_type}/auto-fix-{os.path.basename(file_path).replace('.', '-')}"
    repo = Repo(CLONE_DIR)

    repo.git.checkout("-b", branch_name)

    full_path = os.path.join(CLONE_DIR, file_path)
    refined_fix = refine_fix_with_context(full_path, fix_text, line_number)

    with open(full_path, "r") as f:
        lines = f.readlines()

    lines[line_number - 1] = refined_fix + "\n"

    with open(full_path, "w") as f:
        f.writelines(lines)

    repo.git.add(file_path)
    repo.git.commit(m=f"{pr_type}: Automated fix for {file_path} @ line {line_number}")
    repo.git.push("--set-upstream", "origin", branch_name)

    subprocess.run([
        "gh", "pr", "create",
        "--repo", f"{GITHUB_USER}/{os.path.basename(os.getenv('GITHUB_REPO')).replace('.git', '')}",
        "--title", f"{pr_type.title()}: Auto Fix for {file_path}",
        "--body", f"Fix applied via bot:\n\n```\n{refined_fix}\n```",
        "--base", "main"
    ])

def run_bot_pr_workflow(repo_url, file_path, fix_text, line_number, pr_type="hotfix"):
    os.environ["GITHUB_REPO"] = repo_url
    clone_repo(repo_url)
    apply_fix_and_push(file_path, fix_text, line_number, pr_type)

# --- Triggered when LLM gives a fix ---
def handle_llm_diagnostic(diagnostic_text):
    parsed = parse_diagnostic_output(diagnostic_text)
    bot_state["pending_fix"] = parsed
    return f"Suggested fix for `{parsed['file_path']}`:\n\n{parsed['fix_text']}", parsed

# --- Triggered when user provides a GitHub repo to apply fix ---
def maybe_apply_fix_from_user(user_input: str):
    if "github.com" in user_input and bot_state.get("pending_fix"):
        repo_url = extract_github_url(user_input)
        fix = bot_state["pending_fix"]
        run_bot_pr_workflow(repo_url, **fix)
        bot_state["pending_fix"] = None
        return "✅ Fix applied and pull request created."
    return "Please provide a valid GitHub repo URL to apply the fix."
