import os
import re
import subprocess
from git import Repo
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

# --- Bot state to hold the pending fix ---
bot_state = {
    "pending_fix": None
}

# --- Utility Functions ---

def extract_github_url(text: str):
    match = re.search(r"https://github\.com/\S+\.git", text)
    return match.group(0) if match else None

def parse_diagnostic_output(diagnostic_text: str) -> dict:
    # Extract JSON inside ```json ... ```
    match = re.search(r"```json\\n(.*?)```", diagnostic_text, re.DOTALL)
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
    with open(full_path, "r") as f:
        lines = f.readlines()

    lines[line_number - 1] = fix_text + "\n"

    with open(full_path, "w") as f:
        f.writelines(lines)

    repo.git.add(file_path)
    repo.git.commit(m=f"{pr_type}: Automated fix for {file_path} @ line {line_number}")
    repo.git.push("--set-upstream", "origin", branch_name)

    subprocess.run([
        "gh", "pr", "create",
        "--repo", f"{GITHUB_USER}/{os.path.basename(os.getenv('GITHUB_REPO')).replace('.git', '')}",
        "--title", f"{pr_type.title()}: Auto Fix for {file_path}",
        "--body", f"Fix applied via bot:\n\n```\n{fix_text}\n```",
        "--base", "main"
    ])

def run_bot_pr_workflow(repo_url, file_path, fix_text, line_number, pr_type="hotfix"):
    os.environ["GITHUB_REPO"] = repo_url
    clone_repo(repo_url)
    apply_fix_and_push(file_path, fix_text, line_number, pr_type)

# --- Triggered when LLM gives a fix ---
def handle_llm_diagnostic(diagnostic_text):
    parsed = parse_diagnostic_output(diagnostic_text)
    bot_state["pending_fix"] = {
        "file_path": parsed["file_path"],
        "fix_text": parsed["fix"],
        "line_number": parsed.get("line_number", 1),
        "pr_type": parsed.get("pr_type", "hotfix")
    }
    return f"Suggested fix for `{parsed['file_path']}`:", parsed

# --- Triggered when user provides a GitHub repo to apply fix ---
def maybe_apply_fix_from_user(user_input: str):
    if "github.com" in user_input and bot_state.get("pending_fix"):
        repo_url = extract_github_url(user_input)
        fix = bot_state["pending_fix"]
        run_bot_pr_workflow(repo_url, **fix)
        bot_state["pending_fix"] = None
        return "✅ Fix applied and pull request created."
    return "Please provide a valid GitHub repo URL to apply the fix."
