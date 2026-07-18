"""OpenAI platform (M9 / Path C): one AgentAdapter, two backends.

- backend "stub" (default until the real one lands): deterministic canned
  researcher — lets every protocol server, matrix cell, and console flow
  run before the OpenAI Agents SDK backend exists.
- backend "agents-sdk": the real OpenAI Agents SDK agent (see
  plan/06-openai-codex-handoff.md — implemented against the contract in
  platforms/openai/agents_backend.py).

Compute home per ADR D4: Bedrock AgentCore Runtime, one protocol mode per
deployment (deploy/agentcore/openai.Dockerfile).
"""
