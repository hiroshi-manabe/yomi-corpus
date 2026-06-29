# Yomi Strong Repair Eval Data

This directory contains small fixtures for the future strong-LLM yomi repair
stage. These are not ordinary per-target reading-generation tests.

Use these rows for cases where the local target reading was explicitly rejected
by human review and a stronger model may need broader context, web search, or a
boundary repair.

Rows may be neutralized from skipped source text. In that case, keep the useful
lexical repair signal but remove private/sensitive context from the fixture.
