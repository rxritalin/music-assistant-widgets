---
trigger: always_on
---
# Proactive Rule Generation
- As we work, constantly evaluate if our workflow, repeated mistakes, or project architecture would benefit from a new persistent rule.
- If a new rule would be beneficial, pause and suggest it to the USER.
- Explain *why* the rule is needed and provide the exact Markdown text for it.
- If the USER approves, use your file-writing tools to automatically create and save the new rule file in the project's rules directory.
- IMPORTANT: When writing any new rule file, you MUST include the YAML frontmatter `--- \n trigger: always_on \n ---` at the very top of the file so Antigravity recognizes it.
