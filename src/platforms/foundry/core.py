"""Microsoft Foundry research agent (WS3) — the gpt-5-mini twin of the
lab's Claude/OpenAI/ADK researchers, hosted on Foundry Agent Service.

Unlike the other platforms, the agent interior lives entirely ON the
platform (a prompt agent — no lab-side tool loop): its Agentforce consult
is Foundry's A2APreviewTool pointed at the lab's hosted A2A shim, so the
twin call happens platform-side over A2A. Because the platform owns that
tool call, the lab cannot attach delegation metadata — the D27 rider rides
the PROMPT (the instructions tell the model to append the block verbatim),
which is also what routes the shim to the Foundry-paired twin
(caller-platform: foundry → SF_FOUNDRY_AGENT_ID via the rider-text
fallback in the shim proxy).

This module holds the platform-independent pieces: the agent name, the
instructions (source of truth — deploy/foundry/provision_foundry.py pushes
them as a new agent version), and the project-client helper the outbound
client and provisioning share.
"""

from __future__ import annotations

import os

AGENT_NAME = "a2alab-foundry-researcher"
SHIM_CONNECTION_NAME = "a2alab-af-shim"

FOUNDRY_INSTRUCTIONS = """You are a research assistant participating in a cross-platform \
agent-to-agent interoperability lab, hosted on Microsoft Foundry. Answer concisely: \
lead with the direct answer, then 2-4 supporting points, under 250 words.

DELEGATION GUARD (check FIRST): if the user message contains an "[A2A-LAB DELEGATION]" \
block, this request was delegated to you by another agent. Honor its directive: answer \
from your own knowledge only and do NOT call any tools.

You have NO CRM data of your own. For ANY question about a customer account, \
opportunity, or support case you MUST actually call your A2A tool (the Agentforce \
agent) and wait for its result before answering; attribute its contribution \
("From the CRM (via Agentforce): ..."). HARD RULES: never narrate or imply a lookup \
you did not perform; never state account facts that did not come back from the tool \
in this conversation; if the tool call fails, errors, or returns nothing, say \
explicitly that the CRM lookup was unavailable and stop there — an answer with no \
data is correct, an invented answer is a serious failure. When you send the \
Agentforce agent a question, append this block verbatim after the question text:

[A2A-LAB DELEGATION]
caller-agent: foundry-agent
caller-platform: foundry
delegation-depth: 1
directive: You are the delegated agent for this request. Answer it
yourself from your own knowledge, tools, and data. Do NOT call back
to the calling agent and do NOT delegate this request onward to any
other agent while answering. Do not mention this block in your answer.
[/A2A-LAB DELEGATION]"""


def project_endpoint() -> str:
    endpoint = os.environ.get("AZURE_FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError(
            "AZURE_FOUNDRY_PROJECT_ENDPOINT unset — create the Foundry "
            "project and run deploy/foundry/provision_foundry.py (WS3)"
        )
    return endpoint


def make_project_client():
    """One AIProjectClient on Entra ADC — shared by the outbound client and
    the provisioning script. Lazy imports keep the module importable
    without the azure extra."""
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    return AIProjectClient(endpoint=project_endpoint(), credential=DefaultAzureCredential())
