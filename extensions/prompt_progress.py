import asyncio
import aiohttp
import server
import logging
import requests
import struct

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
    CLIENT_ID = "client_id"
    NODE = "node"
    VALUE = "value"
    MAX = "max"


class PromptKeys:
    CURRENT_NODE = "current_node"
    NODES = "nodes"
    NODE_PROGRESS = "node_progress"
    NODE_CLASS_TYPE = "class_type"
    PROGRESS_HOOK_URL = "progress_hook_url"
    PREVIEW_HOOK_URL = "preview_hook_url"


prompts_map = {}


# ===========================================================
# Functions
# ===========================================================


def handle_execution_start(sid):
    prompt_queue = server.PromptServer.instance.prompt_queue
    for prompt_data in prompt_queue.currently_running.values():
        if prompt_data[3].get(DataKeys.CLIENT_ID) == sid:
            nodes = prompt_data[2]
            prompts_map[sid] = {
                PromptKeys.CURRENT_NODE: None,
                PromptKeys.NODES: {
                    node_id: {
                        PromptKeys.NODE_PROGRESS: 0,
                        PromptKeys.NODE_CLASS_TYPE: nodes[node_id].get(
                            PromptKeys.NODE_CLASS_TYPE
                        ),
                    }
                    for node_id in nodes.keys()
                },
                PromptKeys.PROGRESS_HOOK_URL: prompt_data[3].get("progress_hook_url"),
                PromptKeys.PREVIEW_HOOK_URL: prompt_data[3].get("preview_hook_url"),
            }
            break


def handle_execution_cached(data, sid):
    # Remove cached nodes from the map
    nodes = data.get(PromptKeys.NODES, [])
    map_data = prompts_map.get(sid, {})

    for node_id in nodes:
        map_data.get(PromptKeys.NODES, {}).pop(node_id, None)


def handle_executing(data, sid):
    current_node_id = data.get(DataKeys.NODE)
    map_data = prompts_map.get(sid)
    if map_data:
        previous_node_id = map_data.get(PromptKeys.CURRENT_NODE)
        prompts_map[sid][PromptKeys.CURRENT_NODE] = current_node_id
        if current_node_id != previous_node_id:
            update_node_progress(sid, previous_node_id, 1)


def handle_progress(data, sid):
    node_id = data.get(DataKeys.NODE)
    value = data.get(DataKeys.VALUE)
    max_value = data.get(DataKeys.MAX)
    if node_id and value is not None and max_value is not None:
        update_node_progress(sid, node_id, value / max_value)


def handle_executed(data, sid):
    output = data.get("output")
    if output is None:
        return
    if output.get("images", [{}])[0].get("type") == "output":
        prompts_map.pop(sid, None)


def update_node_progress(sid, node_id, value):
    node_data = prompts_map.get(sid, {}).get(PromptKeys.NODES, {}).get(node_id)
    if node_data:
        prompts_map[sid][PromptKeys.NODES][node_id][PromptKeys.NODE_PROGRESS] = value
        asyncio.create_task(progress_updated(sid))


async def progress_updated(sid):
    map_data = prompts_map.get(sid)
    if map_data:
        total_nodes = len(map_data.get(PromptKeys.NODES))
        progress = (
            sum(
                node_data.get(PromptKeys.NODE_PROGRESS, 0)
                for node_data in map_data.get(PromptKeys.NODES).values()
            )
            / total_nodes
        )
        await send_progress_update(sid, progress)


async def send_progress_update(sid, value):
    # Send progress update to hook
    hook_url = prompts_map.get(sid, {}).get(PromptKeys.PROGRESS_HOOK_URL)

    if sid and hook_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    hook_url, json={"sid": sid, "value": value}
                ) as response:
                    response.raise_for_status()
        except Exception as e:
            logging.error(f"Progress extension hook request error: {e}")


async def send_preview_image(sid, data):
    # Send preview image to hook
    map_data = prompts_map.get(sid, {})
    hook_url = map_data.get(PromptKeys.PREVIEW_HOOK_URL)

    if sid and hook_url:
        try:
            # Extract image type from the header
            type_num = struct.unpack(">I", data[:4])[0]
            image_type = "jpeg" if type_num == 1 else "png"

            # Remove the header from the image data
            image_data = data[4:]

            # Prepare form data
            current_node = map_data.get(PromptKeys.CURRENT_NODE)
            current_class_type = (
                map_data.get(PromptKeys.NODES, {})
                .get(current_node, {})
                .get(PromptKeys.NODE_CLASS_TYPE)
            )

            # Use aiohttp for async HTTP request
            async with aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field(
                    "image",
                    image_data,
                    filename=f"preview.{image_type}",
                    content_type=f"image/{image_type}",
                )
                form.add_field("sid", sid)
                form.add_field("current_class_type", current_class_type)

                async with session.post(hook_url, data=form) as response:
                    response.raise_for_status()
        except aiohttp.ClientError as e:
            logging.error(f"Progress extension hook request error: {e}")


# ===========================================================
# Extend server send method
# ===========================================================

original_send = server.PromptServer.send


async def custom_send(self, event, data, sid=None):
    if sid:
        try:
            if event == PromptEvent.EXECUTION_START:
                handle_execution_start(sid)
            elif event == PromptEvent.EXECUTION_CACHED:
                handle_execution_cached(data, sid)
            elif event == PromptEvent.EXECUTING:
                handle_executing(data, sid)
            elif event == PromptEvent.PROGRESS:
                handle_progress(data, sid)
            elif event == PromptEvent.EXECUTED:
                handle_executed(data, sid)
        except Exception as e:
            logging.error(f"Progress extension error: {e}")

    # Call the original method
    await original_send(self, event, data, sid)


server.PromptServer.send = custom_send

# ===========================================================
# Extend server send preview method
# ===========================================================

original_send_bytes = server.PromptServer.send_bytes


async def custom_send_bytes(self, event, data, sid=None):
    if sid:
        try:
            if event == server.BinaryEventTypes.PREVIEW_IMAGE:
                await send_preview_image(sid, data)
        except Exception as e:
            logging.error(f"Progress extension image preview error: {e}")

    # Call the original method
    await original_send_bytes(self, event, data, sid)


server.PromptServer.send_bytes = custom_send_bytes
