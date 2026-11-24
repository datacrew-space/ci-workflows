Write the compaction summary as a handoff for a session that has none of this conversation. Use these sections, in this order:

1. Goal: what the user asked for, in their words, and any standing instructions they gave this session (for example "check in before each action").
2. Current state: what is done and verified, what is done but unverified, and what is in progress. Name PRs and issues as owner/repo#number, branches by name, and worktrees and files by absolute path.
3. Next: the remaining steps in order, starting with the one that was in progress when compaction ran.
4. Files to read before continuing: absolute paths, including any handoff or plan file for this workstream.
5. Open decisions: questions waiting on the user, each with the recommended answer already given.
6. Things learned the hard way: commands, wrappers, paths or workarounds that took effort to find, and approaches that were tried and failed.

Reference existing artifacts by path or URL; do not copy their contents. Never include secrets, tokens or passwords; name them by key name only. Keep exact identifiers, error messages and numbers verbatim.
