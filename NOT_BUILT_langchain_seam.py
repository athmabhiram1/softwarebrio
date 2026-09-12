"""NOT-BUILT LangChain seam (Task 2, bonus branch) — design note only, never executed.

Where a LangChain-based extraction or orchestration layer COULD plug in later:
the `extract_domain` boundary in extractor.py (replace or wrap the single
deterministic LLM call per domain), or the Tavily fallback branch in
search_fallback.py (wrap search → parse → enrich in an agent loop).

Why it wasn't built now: a deterministic single-call pipeline is cheaper and
more reliable for 3 fixed domains than an agentic framework — the same
reasoning as the existing LangGraph/Browser-Use non-goal in README §8.
A custom tool-calling loop was considered (a linear 3-tool ReAct loop doesn't
need a framework per current framework-comparison guidance) but not built, to
keep this branch's scope and risk minimal given the submission timeline.

Constraints (verified by grep): zero imports in this file, no `langchain`
dependency in requirements.txt, never imported by main.py or any execution
path.
"""
