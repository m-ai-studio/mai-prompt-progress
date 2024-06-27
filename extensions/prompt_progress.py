import server
import logging
import os
from dotenv import load_dotenv
import requests

# ===========================================================
# Init variables
# ===========================================================

load_dotenv()

# ===========================================================
# Variables
# ===========================================================


class PromptEvent:
    EXECUTION_START = "execution_start"
    EXECUTION_CACHED = "execution_cached"
    EXECUTING = "executing"
    PROGRESS = "progress"
    EXECUTED = "executed"


class DataKeys:
    PROMPT_ID = "prompt_id"
    NODE = "node"
    VALUE = "value"
    MAX = "max"


class PromptKeys:
    SID = "sid"
    CURRENT_NODE = "current_node"
    NODES = "nodes"
    NODE_PROGRESS = "node_progress"


hook_url_key = "PROGRESS_HOOK_URL"
hook_url = os.environ.get(hook_url_key)
prompts_map = {}


# ===========================================================
# Functions
# ===========================================================


def handle_execution_start(data, sid):
    prompt_id = data.get(DataKeys.PROMPT_ID)
    if prompt_id:
        prompt_queue = server.PromptServer.instance.prompt_queue
        for prompt_data in prompt_queue.currently_running.values():
            if prompt_data[1] == prompt_id:
                nodes = prompt_data[2].keys()
                prompts_map[prompt_id] = {
                    PromptKeys.SID: sid,
                    PromptKeys.CURRENT_NODE: None,
                    PromptKeys.NODES: {
                        node: {PromptKeys.NODE_PROGRESS: 0} for node in nodes
                    },
                }
                break


def handle_execution_cached(data):
    # Remove cached nodes from the map
    prompt_id = data.get(DataKeys.PROMPT_ID)
    nodes = data.get(PromptKeys.NODES, [])
    map_data = prompts_map.get(prompt_id, {})

    for node_id in nodes:
        map_data.get(PromptKeys.NODES, {}).pop(node_id, None)


def handle_executing(data):
    prompt_id = data.get(DataKeys.PROMPT_ID)
    current_node_id = data.get(DataKeys.NODE)
    map_data = prompts_map.get(prompt_id)
    if map_data:
        previous_node_id = map_data.get(PromptKeys.CURRENT_NODE)
        prompts_map[prompt_id][PromptKeys.CURRENT_NODE] = current_node_id
        if current_node_id != previous_node_id:
            update_node_progress(prompt_id, previous_node_id, 1)


def handle_progress(data):
    prompt_id = data.get(DataKeys.PROMPT_ID)
    node_id = data.get(DataKeys.NODE)
    value = data.get(DataKeys.VALUE)
    max_value = data.get(DataKeys.MAX)
    if prompt_id and node_id and value is not None and max_value is not None:
        update_node_progress(prompt_id, node_id, value / max_value)


def handle_executed(data):
    if data.get("output", {}).get("images", [{}])[0].get("type") == "output":
        prompts_map.pop(data.get(DataKeys.PROMPT_ID), None)


def update_node_progress(prompt_id, node_id, value):
    node_data = prompts_map.get(prompt_id, {}).get(PromptKeys.NODES, {}).get(node_id)
    if node_data:
        prompts_map[prompt_id][PromptKeys.NODES][node_id][
            PromptKeys.NODE_PROGRESS
        ] = value
        progress_updated(prompt_id)


def progress_updated(prompt_id):
    map_data = prompts_map.get(prompt_id)
    if map_data:
        total_nodes = len(map_data.get(PromptKeys.NODES))
        progress = (
            sum(
                node_data.get(PromptKeys.NODE_PROGRESS, 0)
                for node_data in map_data.get(PromptKeys.NODES).values()
            )
            / total_nodes
        )
        send_progress_update(prompt_id, progress)


def send_progress_update(prompt_id, value):
    # Debug info
    percentage = round(value * 100, 1)
    print(f"Progress: {percentage}%")

    # Send progress update to hook
    sid = prompts_map.get(prompt_id, {}).get(PromptKeys.SID)
    try:
        response = requests.post(hook_url, json={"value": value, "sid": sid})
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Progress extension hook request error: {e}")


# ===========================================================
# Entend server send method
# ===========================================================

original_send = server.PromptServer.send


async def custom_send(self, event, data, sid=None):
    try:
        if event == PromptEvent.EXECUTION_START:
            handle_execution_start(data, sid)
        elif event == PromptEvent.EXECUTION_CACHED:
            handle_execution_cached(data)
        elif event == PromptEvent.EXECUTING:
            handle_executing(data)
        elif event == PromptEvent.PROGRESS:
            handle_progress(data)
        elif event == PromptEvent.EXECUTED:
            handle_executed(data)
    except Exception as e:
        logging.error(f"Progress extension error: {e}")

    # Call the original send method
    await original_send(self, event, data, sid)


if hook_url is None:
    logging.error(
        f"{hook_url_key} env variable is not set. Skipping MAI prompt progress extension."
    )
else:
    server.PromptServer.send = custom_send
