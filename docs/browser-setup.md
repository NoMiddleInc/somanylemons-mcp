# Use ProducerSpark in Claude or ChatGPT

Release status: this browser sign-in flow is prepared locally. Use the public instructions only after the backend, MCP, and connection page are released together and public discovery passes. Actual account sign-in and tool use in Claude and ChatGPT still need the first user test. This is a private test connection, not a published directory listing.

Customers need a ProducerSpark account with an active organization and their saved research setup. They do not need repository access, Python, terminal commands, or an API key to copy.

## Claude in your browser

1. Open [Claude connectors](https://claude.ai/customize/connectors). Choose **Add → Add custom connector**.
2. Name it **ProducerSpark**. Paste `https://mcp.somanylemons.com/mcp` as the server address. Choose sign-in; if asked about the OAuth client, choose **Register automatically**.
3. Sign into ProducerSpark and click **Connect Claude**. Start a conversation and enable ProducerSpark from the chat's connectors menu.

For Team or Enterprise, an owner may need to allow the connector first.

## ChatGPT in your browser

1. Open **Settings → Security and login** and enable **Developer mode** for this first test.
2. Open [ChatGPT Plugins](https://chatgpt.com/plugins), click **+**, and add a connection named **ProducerSpark** using `https://mcp.somanylemons.com/mcp`.
3. Choose OAuth, sign into ProducerSpark and click **Connect ChatGPT**. Start a new chat with this connection enabled.

Developer mode depends on the account and workspace policy. If it is unavailable, stop and record that account limitation; do not claim every ChatGPT plan can install this private connection. Normal directory installation for customers requires separate submission/publication after the browser pilot succeeds.

## First test: type this

> Use ProducerSpark to show my existing research tasks and anything waiting on me. Do not create requests, change schedules, or send anything.

Then:

> Read request [TASK NUMBER]. Show its current status, actual business-email coverage, verification and enrichment dates, saved draft, sources, remaining limitations and workbook. Reuse the saved results; do not do another lookup or delivery.

When ready to test new research:

> Research one qualified prospect at [AGENCY NAME] using my saved criteria. Use my existing saved configuration; ask me which configuration if ambiguous. Explain whether this can trigger an existing customer delivery before creating the request. Give me the task number so I can resume later. Do not contact prospects or change schedules.

The backend runs research even when the conversation closes. Resume by asking for the same task number. Counts, missing emails and blockers must come from saved results.

## Optional Claude skill

The MCP supplies research instructions automatically. An optional Claude skill is available from `/skills/producerspark.zip` on the same MCP host after release. It contains one `producerspark/SKILL.md`; upload it through Claude's supported skill interface if desired. A skill alone cannot connect an account or grant credentials, and this archive is not a ChatGPT plugin package.

## Disconnect

Remove the connection in Claude or ChatGPT. To invalidate its credentials at ProducerSpark as well, revoke the **Browser connection: Claude** or **Browser connection: ChatGPT** key in your account's API-key settings. Revocation stops refresh too.

Sources: [Claude custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp), [OpenAI connection testing](https://developers.openai.com/plugins/deploy/connect-chatgpt), [OpenAI OAuth requirements](https://developers.openai.com/plugins/build/auth).
