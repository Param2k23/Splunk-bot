from openai import AzureOpenAI
import os
from dotenv import load_dotenv
import json
import pprint
load_dotenv()
state = {
    "application_name": None,
    "function_name": None
}
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
                "application_name": {"type": "string"}
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
                "application_name": {"type": "string"}
            },
            "required": ["application_name"]
        }
    }
]

def generate_spl(function_name: str, app_name: str) -> str:
    if function_name == "check_status":
        return f'search index=main sourcetype=app_logs app="{app_name}" status!=200'
    elif function_name == "search_errors":
        return f'search index=main sourcetype=app_logs app="{app_name}" error=*'
    return "Unknown function"

conversation = [{
"role": "system",
"content": """
You are a Splunk assistant designed to help users generate Splunk queries for analyzing application logs.

Your job is to interpret the user's intent and choose the appropriate function (tool) to call. You are responsible for deciding which function to call based on the user's message.

You are also allowed to extract parameters such as the application name from the user message and include them in your function call. Do not rely on any pre-existing system state for this decision.

After you generate the function call (including function name and application name), the system will update the state accordingly.

If you cannot determine either the function to use or the application name from the user's message, respond by asking for the missing information.

Examples:
- If the user says "What is the issue with AppServer1?", you should call `search_errors` with `application_name = AppServer1`.
- If the user says "Check status for AppServer2", you should call `check_status` with `application_name = AppServer2`.

Only call functions when you are confident you have both the function and required arguments.
"""
}
]
async def route_user_query(user_input: str) -> dict:
    global state

    # Step 1: Add user message to conversation
    conversation.append({"role": "user", "content": user_input})

    # Step 2: Let GPT decide what to do (including extracting app name & function)
    response = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        messages=conversation,
        tools=[{"type": "function", "function": fn} for fn in functions],
        tool_choice="auto"
    )

    message = response.choices[0].message

    # Case 1: GPT replies with natural language
    if message.content:
        conversation.append({"role": "assistant", "content": message.content})
        return {"response": message.content}

    # Case 2: GPT makes a function call
    if message.tool_calls:
        tool = message.tool_calls[0]
        function_name = tool.function.name
        args = json.loads(tool.function.arguments)
        app_name = args.get("application_name")

        # Step 3: Update state (app name and function)
        state["application_name"] = app_name
        state["function_name"] = function_name

        # Step 4: Generate SPL
        spl_query = generate_spl(function_name, app_name)

        # Step 5: Add GPT tool call summary to chat history
        conversation.append({
            "role": "assistant",
            "content": f"I will run `{function_name}` on `{app_name}`."
        })

        return {
            "function_called": function_name,
            "application": app_name,
            "spl_query": spl_query
        }

    # Fallback
    return {"response": "Unable to understand or process the query."}
