from functools import partial
from typing import Any, cast

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agent.nodes import nodes
from app.agent.state.state import State


def _after_names(state: State) -> str:
    return "stop" if state.get("state") == "clarify" else "plan"


def _after_gather(state: State) -> str:
    return "stop" if state.get("state") == "abstain" else "check"


def _after_check(state: State) -> str:
    return "stop" if state.get("state") == "abstain" else "write"


def _after_verify(state: State) -> str:
    """clean -> done. dirty, no retry used yet -> write again. dirty twice -> abstain."""
    if state.get("state") == "answered":
        return "stop"
    if state.get("retries", 0) < 1:
        state["retries"] = state.get("retries", 0) + 1
        return "write"
    state["state"] = "abstain"
    state["reason"] = "could not verify the numbers"
    return "stop"


def build_graph(session: Session):
    """
    Compiles the state machine for ONE request, bound to that request's
    session. A fresh graph per request because each run needs its own
    tenant-scoped session -- nothing here is shared across tenants or runs.
    """
    graph = StateGraph(cast(Any, State))

    graph.add_node("start", partial(nodes.step_start, session))
    graph.add_node("understand", partial(nodes.step_understand, session))
    graph.add_node("find_names", partial(nodes.step_find_names, session))
    graph.add_node("plan", partial(nodes.step_plan, session))
    graph.add_node("gather", partial(nodes.step_gather, session))
    graph.add_node("check", partial(nodes.step_check, session))
    graph.add_node("write", partial(nodes.step_write, session))
    graph.add_node("verify", partial(nodes.step_verify, session))
    graph.add_node("finalize", partial(nodes.step_finalize, session))

    graph.set_entry_point("start")
    graph.add_edge("start", "understand")
    graph.add_edge("understand", "find_names")
    graph.add_conditional_edges("find_names", _after_names, {"plan": "plan", "stop": "finalize"})
    graph.add_edge("plan", "gather")
    graph.add_conditional_edges("gather", _after_gather, {"check": "check", "stop": "finalize"})
    graph.add_conditional_edges("check", _after_check, {"write": "write", "stop": "finalize"})
    graph.add_edge("write", "verify")
    graph.add_conditional_edges("verify", _after_verify, {"write": "write", "stop": "finalize"})
    graph.add_edge("finalize", END)

    return graph.compile()
