# Database MCP (`db-grep`) Handling Rules

## Case 1: When any tool from `db-grep` server returns `MONITOR_OFFLINE`
This happens when the terminal monitor (`db-grep --monitor`) is not running.

1. **ANTI-BYPASS STRICT RULE (DO NOT VIOLATE)**:
   - **DO NOT** attempt to read database credentials from `.env`, configuration files, or source code.
   - **DO NOT** create your own database connection scripts (Bash, psql, Node.js, Python, etc.) to bypass the monitor.
   - **DO NOT** try other tools to circumvent this requirement.

2. **Notify User Clearly**:
   - Inform the user that the query was paused because the approval monitor is offline:
     "Database monitor is offline. To protect your data, all queries require approval via `db-grep --monitor`."
   - State the query that was about to run and the target database.

3. **Prompt User Next Step**:
   - Use the `question` tool (or prompt choice) to ask user:
     - "I have started `db-grep --monitor` in another terminal, retry query"
     - "Cancel and continue without database query"

---

## Case 2: When any tool returns `ACTION_DENIED_BY_USER`
This happens when the operator pressed `n` (reject) in the terminal monitor.

1. **Stop Tool Calls Immediately**:
   - Never retry the rejected query.
   - Never call any other `db-grep` tool for the current request.
   - Never attempt alternative direct database access.

2. **Explain Naturally (Do NOT copy or echo raw error instructions)**:
   - State clearly that the query was rejected in the monitor.
   - Show the SQL query that was rejected.
   - Explain what data was being queried and why it is needed to answer the user's request.
   - Explain what cannot be done or verified without this data.

3. **Prompt User Next Step**:
   - Use the `question` tool (or prompt choice) to ask user:
     - "Retry query (I will approve in monitor)"
     - "Continue without database query"
