# Database MCP (`db-grep`) Handling Rules

When any tool from `db` server returns an error containing `ACTION_DENIED_BY_USER`:

1. **Stop Tool Calls Immediately**:
   - Never retry the rejected query.
   - Never call any other `db` tool for the current request.

2. **Explain Naturally (Do NOT copy or echo raw error instructions)**:
   - State clearly that the query was rejected in the monitor.
   - Show the SQL query that was rejected.
   - Explain what data was being queried and why it is needed to answer the user's request.
   - Explain what cannot be done or verified without this data.

3. **Prompt User Next Step**:
   - Use the `question` tool (or prompt choice) to ask user:
     - "Retry query (I will approve in monitor)"
     - "Continue without database query"
