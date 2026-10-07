# Hosted MCP production release

The production service is `somanylemons-mcp` in GCP project `b2bai-451415`, region `us-central1`. The existing global Cloud Build trigger `c9bbbd7c-d016-4761-a546-1dd434b50e7a` watches `NoMiddleInc/somanylemons-mcp` main. It builds and pushes the exact commit image, then updates the existing service. It does not necessarily move pinned revision traffic.

1. Check the clean release source against fresh origin/main, inspect active builds/deployment processes, and reconcile any conflicting release writer.
2. Run local unit tests, `bash -n install.sh`, and `git diff --check`. Keep package, runtime and plugin versions consistent. No backend migration is needed for presentation-only MCP tools.
3. Save current full service, IAM policy, traffic and runtime configuration privately. Record the release in `outputs/production-release-handoffs-20261004/release-ledger.md` in the main SoManyLemons workspace.
4. Commit the intended files and push HEAD to main without force. Observe the existing triggered build by exact commit; never start a duplicate build merely because rollout is delayed.
5. Verify build SUCCESS, exact source commit, image digest, candidate Ready/Active and preserved runtime/IAM. Preserve all existing revision tags. If old revision traffic is pinned, give the candidate a fresh preview tag at zero traffic, verify health, MCP initialization/tool schema and the skill ZIP, then move traffic to the exact verified revision.
6. Verify canonical `https://producerspark.com/mcp` initialization/tool registry, `/health`, skill ZIP contents and auth errors. With the existing scoped credential, read existing tasks only; call watch_research only on an accessible existing goal. If the credential's list is empty, record that limitation instead of creating customer research for a smoke test.
7. Save build ID, commit, digest, serving revision, 100% traffic, verification results and any limitations. Do not include credentials, raw customer rows or service secrets in the public receipt.

Production endpoints: `https://producerspark.com/mcp`, `https://producerspark.com/skills/producerspark.zip`. The direct service URL is available from `gcloud run services describe`; verify routing before claiming a vanity skill URL exists.
