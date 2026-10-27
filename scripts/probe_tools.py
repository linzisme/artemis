"""Shared synthetic tool conversation; receipts never execute device actions."""

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage


async def probe_sequential_tools(bound, check, *, continuation_name, text_content):
    # A separate conversation makes sequential correctness independent of the
    # optional same-response capability, including when it emits no calls.
    messages = [
        SystemMessage(content="Serialize only the requested tool; never execute actions."),
        HumanMessage(content="Return exactly one click call with target index 2."),
    ]
    first_action = await check(
        "sequential_first_tool",
        bound.ainvoke(messages),
        lambda r: (
            len(r.tool_calls) == 1
            and r.tool_calls[0]["name"] == "click"
            and r.tool_calls[0]["args"]["target"] == 2
        ),
    )
    messages += [
        first_action,
        ToolMessage(
            content="DRY_RUN_ACCEPTED; protocol fixture only, no device action performed.",
            tool_call_id=first_action.tool_calls[0]["id"],
        ),
        HumanMessage(content="Now return exactly one press_key BACK call."),
    ]
    second = await check(
        "sequential_second_tool",
        bound.ainvoke(messages),
        lambda r: (
            len(r.tool_calls) == 1
            and r.tool_calls[0]["name"] == "press_key"
            and r.tool_calls[0]["args"]["key"] == "BACK"
        ),
    )
    messages += [
        second,
        ToolMessage(
            content="DRY_RUN_ACCEPTED; protocol fixture only, no device action performed.",
            tool_call_id=second.tool_calls[0]["id"],
        ),
        HumanMessage(content="Return only DRY_RUN_ACCEPTED with no more tool calls."),
    ]
    await check(
        continuation_name,
        bound.ainvoke(messages),
        lambda r: not r.tool_calls and text_content(r) == "DRY_RUN_ACCEPTED",
    )
