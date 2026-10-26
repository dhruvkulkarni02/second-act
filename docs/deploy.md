# Deployment (Azure Container Apps)

**Live demo:** https://second-act.bluecliff-fc92b3c6.swedencentral.azurecontainerapps.io

It's public with no login, and stays up until at least November 10, 2026.

## What runs where

| Resource (resource group `second-act-rg`, Sweden Central) | Purpose |
| --- | --- |
| Container App `second-act` | FastAPI backend plus the web app. One replica (min 1, max 1), 0.5 vCPU, 1 GiB, external HTTPS ingress on port 8000 |
| Container Apps environment `second-act-env` | Standard environment (workload profiles, Consumption profile), with logs sent to Log Analytics workspace `workspace-secondactrgDxlG` |
| Container Registry `secondactjio0jn` (Basic) | Holds the image the app runs. The app pulls it with its managed identity (AcrPull) |
| Foundry resource `second-act-resource`, project `second-act` | The `gpt-5-mini` deployment the agents call |

There's one replica, because sessions live in memory: a replay must be served by the replica that started it.

**Auth.** The app has a system-assigned managed identity with the *Foundry User* role (formerly *Azure AI User*) on `second-act-resource`, and `DefaultAzureCredential` picks it up. There are no keys or secrets anywhere. The three settings below are plain environment variables:

```
FOUNDRY_PROJECT_ENDPOINT=https://second-act-resource.services.ai.azure.com/api/projects/second-act
FOUNDRY_MODEL=gpt-5-mini
FOUNDRY_REASONING_EFFORT=minimal
```

**Cost control.** The image ships `precomputed/`: verified briefings, recaps and presenter variants from a real online run of each match. A viewer's session reuses them, and only the watcher's verdict sentences are written live. That's about 7 small model calls per replay (about 1.4k input and 0.5k output tokens).

## Build and release

ACR Tasks (`az acr build`) are disabled on Azure for Students subscriptions, so the image is built in GitHub Actions instead:

1. On every push to `main`, CI runs the tests, builds the image and smoke-tests it in Docker. The `publish` job then pushes it to `ghcr.io/dhruvkulkarni02/second-act:<sha>` and `:latest`. The package is public, like the repository.
2. Release a commit:

```bash
SHA=<full commit sha>; SHORT=${SHA:0:7}
az acr import -n secondactjio0jn --source ghcr.io/dhruvkulkarni02/second-act:$SHA --image second-act:$SHORT --force
az containerapp update -n second-act -g second-act-rg --image secondactjio0jn.azurecr.io/second-act:$SHORT
curl -s https://second-act.bluecliff-fc92b3c6.swedencentral.azurecontainerapps.io/api/health
```

For about a minute after an update, the old revision keeps running while traffic moves to the new one. A replay started in that window can land on the old revision, and its follow-up requests on the new one, which can't see the session. Wait until only the new revision is active before testing:

```bash
az containerapp revision list -n second-act -g second-act-rg --query "[].{name:name, active:properties.active, traffic:properties.trafficWeight}" -o table
```

Logs:

```bash
az containerapp logs show -n second-act -g second-act-rg --tail 100 --format text
```

## Notes from setting it up

- The current `containerapp` CLI extension (1.3.0b5) creates **Express** environments by default. Express doesn't support managed identities (neither the app's own nor registry pulls), so the environment must be created with `--environment-mode WorkloadProfiles`.
- `az containerapp create --registry-identity system` created the app and its identity but left it on the quickstart image. The registry was attached afterwards: grant `AcrPull` to the identity, run `az containerapp registry set --identity system`, then `az containerapp update --image ...`.
- The resource group needs the tags required by Northeastern's policy (`Owner`, `Cost Center`, `IaC managed`). Resources inside it don't.

## Tear down (after judging)

```bash
az group delete -n second-act-rg
```

This removes the app, environment, registry, logs and the Foundry resource.
