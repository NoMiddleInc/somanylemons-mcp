# ProducerSpark

Find business prospects and conference speakers through your connected ProducerSpark account. Describe the companies, roles, geography or event you care about, and Claude starts a durable research request, follows its saved progress, and retrieves the resulting workbook.

## What you can do

- Research business decision-makers, executives and published conference speakers.
- Follow saved contact counts, business-email coverage, research stages and recorded blockers during an active conversation.
- Inspect saved findings with source references and enrichment or verification status.
- Retrieve your saved ideal customer profile, main prospect lists and research workbooks.
- Pause or resume existing research when you explicitly request it.
- Discuss an email campaign as a next step after research completes. This connection cannot launch campaigns, enroll contacts or send prospect outreach.

## Connect your account

Add the plugin in Claude, then connect the ProducerSpark MCP server and sign in to ProducerSpark. The server address is https://producerspark.com/mcp. You need a ProducerSpark account with an active organization and a saved research setup. No terminal, local package, copied API key or environment variable is required for account sign-in.

Try asking: “Find 50 manufacturing decision-makers in Chicago and keep me updated.” Or: “Download my main prospect list as Excel.” Research uses your account's existing authorization and provider allowances. A larger requested count does not raise those limits.

## Progress and data quality

The skill uses the read-only watch_research tool as the primary progress interface. It compares saved snapshots and shows meaningful changes. wait_for_task is a compatibility fallback for connectors that do not expose watch_research. The skill does not treat worker steps as contacts or preview rows as a complete roster. Recorded email verification is not a fresh deliverability check, and missing, uncertain or unfinished fields remain labeled.

Research runs in ProducerSpark's backend and can continue after the conversation closes. Live updates require an active Claude conversation checking the saved job; the plugin cannot push unsolicited alerts into an idle or closed Claude session. An account's existing authorized customer-workbook delivery may run through the backend. It does not authorize new recipients or prospect outreach.

## Data and external connections

The plugin contains a Markdown skill, an icon, and the remote MCP configuration. It has no local executable, install script, hook or background process. It sends tool arguments, such as research questions, target companies and roles, requested fields and task or list identifiers, to the declared ProducerSpark connector at https://producerspark.com/mcp. Authentication happens through ProducerSpark's sign-in flow and is managed by Claude. Do not paste credentials into chat.

The connector reads and returns account-scoped research requests, saved contacts and business emails, source and enrichment metadata, schedules and workbook download links. Research creation and explicit task controls update records in ProducerSpark. The plugin does not transmit data to a separate undeclared destination. ProducerSpark's backend may use service providers to perform authorized research and enrichment under its existing policies and limits. Workbooks contain business-contact personal data; use them within your authorized account scope. Saved requests, research evidence and results are retained in your ProducerSpark account according to the service's privacy policy; the plugin does not establish a separate retention period or keep its own local database.

Read the [privacy policy](https://producerspark.com/privacy-policy) and [terms](https://producerspark.com/terms). For support, contact support@somanylemons.com or open an issue in the [source repository](https://github.com/NoMiddleInc/somanylemons-mcp/issues).
