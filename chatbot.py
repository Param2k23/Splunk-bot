from openai import AzureOpenAI
import os
from dotenv import load_dotenv
import json
import pprint
from splunk_helper import splunk_login, splunk_submit_search, splunk_wait_for_job, splunk_get_results


load_dotenv()

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_API_BASE")
)
# Define functions
functions = [
    {
        "name": "check_status",
        "description": "Check the status of an application using Splunk logs.",
        "parameters": {
            "type": "object",
            "properties": {
                "application_name": {"type": "string"},
                "time_range": {
                    "type": "string",
                    "description": "A time range like 'last 24 hours', 'past 7 days', 'today', etc."
                    }
            },
            "required": ["application_name"]
        }
    },
    {
        "name": "search_errors",
        "description": "Search for errors in a specific application's logs.",
        "parameters": {
            "type": "object",
            "properties": {
                "application_name": {"type": "string"},
                "time_range": {
                    "type": "string",
                    "description": "A time range like 'last 24 hours', 'past 7 days', 'today', etc."
                }
            },
            "required": ["application_name"]
        }
    },
    {
        "name": "search_null_pointer_exceptions",
        "description": "Search for null pointer exceptions in a specific application's logs.",
        "parameters": {
            "type": "object",
            "properties": {
                "application_name": {"type": "string"},
                "time_range": {
                    "type": "string",
                    "description": "A time range like 'last 24 hours', 'past 7 days', 'today', etc."
                }
            },
            "required": ["application_name"]
        }
    }
]

def parse_time_range(time_range: str) -> tuple[str, str]:
    time_range = (time_range or "").lower().strip()

    if "last 24 hours" in time_range or "past 24 hours" in time_range:
        return "-24h", "now"
    elif "last 7 days" in time_range or "past 7 days" in time_range or "past week" in time_range:
        return "-7d", "now"
    elif "last hour" in time_range or "past hour" in time_range:
        return "-1h", "now"
    elif "today" in time_range:
        return "@d", "now"
    elif "yesterday" in time_range:
        return "@d-1d", "@d"
    elif "last 30 minutes" in time_range or "past 30 minutes" in time_range:
        return "-30m", "now"
    elif "last 15 minutes" in time_range or "past 15 minutes" in time_range:
        return "-15m", "now"
    else:
        # Default fallback
        return "-1h", "now"

def get_rephrased_query(conversation: list, user_input: str) -> str:
    system_prompt = {
        "role": "system",
        "content": (
            "You are a utility that rewrites a user's latest message to make it a fully self-contained, "
            "clear, and unambiguous instruction for a log analysis assistant. Use prior conversation context "
            "Do not ask clarifying questions. Do not respond conversationally. "
            "Just return a clean, actionable query."
        )
    }

    messages = [system_prompt] + conversation + [{"role": "user", "content": f"Original message: '{user_input}'\n\nRephrase it as a clear and complete instruction."}]

    response = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        messages=messages,
        temperature=0.2
    )

    return response.choices[0].message.content.strip()


def generate_spl(function_name: str, app_name: str, earliest_time: str = "-1h", latest_time: str = "now") -> str:
    time_filter = f' earliest="{earliest_time}" latest="{latest_time}"'

    if function_name == "check_status":
        return f'search index=main sourcetype=test1 app="{app_name}" status!=200{time_filter}'
    elif function_name == "search_errors":
        return f'search source="app_dummy_logs.log" host="DESKTOP-517J9U9" sourcetype="test1" "ERROR {app_name}"{time_filter}'
    elif function_name == "search_null_pointer_exceptions":
        return f'search source="test_log.txt" host="DESKTOP-517J9U9" index="main" sourcetype="test3" {app_name} "NullPointerException" AND "at " {time_filter}'
    return "Unknown function"


conversation = [
    {"role": "system", 
    "content": """You are a Splunk assistant designed to help users generate Splunk queries for analyzing application logs. 

    Your role is to decide if a user's message clearly conveys a well-defined goal. If it does, choose the appropriate function from the available tools and provide only the necessary arguments. Do not make assumptions or guess missing information.

    If the user input is vague, incomplete, or does not clearly indicate what to search for (e.g., no application name or unclear task), respond with a clarifying question instead of calling a function.

    Guidelines:
    - Never assume missing details like the application name.
    - Only call a function if you are confident the user intends to perform one of the defined tasks (e.g., check status, search for errors).
    - If the user's request does not align with any defined functions, or seems incomplete, ask for clarification.

    You must be accurate, cautious, and inquisitive when needed."""}]

async def route_user_query(user_input: str) -> dict:
    # Step 1: Get reframed query from GPT
    clarified_input = get_rephrased_query(conversation, user_input)

    # Step 2: Add reframed query to conversation
    conversation.append({"role": "user", "content": clarified_input})
    pprint.pprint(conversation)
    # Step 3: Run assistant with function calling logic
    response = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        messages=conversation,
        tools=[{"type": "function", "function": fn} for fn in functions],
        tool_choice="auto"
    )

    message = response.choices[0].message

    if message.content:
        conversation.append({"role": "assistant", "content": message.content})
        return {"response": message.content}

    if message.tool_calls:
        tool = message.tool_calls[0]
        function_name = tool.function.name
        args = json.loads(tool.function.arguments)
        app_name = args.get("application_name")
        time_range = args.get("time_range", "")
        earliest, latest = parse_time_range(time_range)
        spl_query = generate_spl(function_name, app_name, earliest, latest)


        session_key = splunk_login()
        if not session_key:
            return {"response": "Splunk authentication failed."}

        sid = splunk_submit_search(session_key, spl_query)
        if not sid:
            return {"response": "Failed to submit Splunk search."}

        splunk_wait_for_job(session_key, sid)
        results = splunk_get_results(session_key, sid)

        diagnostics = await get_diagnostic_suggestion(app_name, function_name, spl_query, results)

        return {
            "function_called": function_name,
            "application": app_name,
            "spl_query": spl_query,
            "splunk_results": results,
            "diagnostics": diagnostics
        }

    return {"response": "Unable to understand or process the query."}

def create_diagnostic_prompt(app_name, function_called, spl_query, splunk_result_text):
    return f"""
    You are an intelligent DevOps assistant with access to logs and source control knowledge.

    An error occurred in application `{app_name}` during the `{function_called}` check. Here is the Splunk query that found it:

    ```
    {spl_query}
    ```

    And here is the actual log line returned from Splunk:

    ```
    {splunk_result_text}
    ```

    Your task:
    1. Identify the **likely cause** of the issue.
    2. Suggest a **precise fix or remediation**.
    3. Provide the **file path** where the fix should be applied.

    Also suggest whether this should be a hotfix or a normal PR.
    Only output:
    - Root cause
    - Fix
    - File path for PR
    - PR type: hotfix or normal
    """


async def get_diagnostic_suggestion(app_name, function_called, spl_query, splunk_results):
    if not splunk_results.get("results"):
        return {"response": "No Splunk results found to analyze."}

    # Extract raw log line from first result
    first_result = splunk_results["results"][0]
    raw_log = first_result.get("_raw") or json.dumps(first_result)

    prompt = create_diagnostic_prompt(app_name, function_called, spl_query, raw_log)

    response = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4
    )

    return {"diagnostic_suggestion": response.choices[0].message.content.strip()}