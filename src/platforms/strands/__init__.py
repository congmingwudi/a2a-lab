"""AWS Strands Agents platform (WS5).

Third framework on the identical Bedrock AgentCore runtime, alongside the
Claude Agent SDK and OpenAI Agents SDK containers — isolates the FRAMEWORK
variable at constant runtime and model cloud (Bedrock). The real backend
(``StrandsBackend``) is farmed to Kiro; see plan/12-strands-kiro-handoff.md.
Everything else in this package is the lab's scaffold.
"""
