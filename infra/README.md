# Deploy on Azure

One container on **Azure Container Apps** serves the backend (`python -m app.engine`) and the PWA (`web/`, built in
the image) on the same origin. No keys anywhere: the image is pulled and Foundry is called with a **managed identity**.

| File | What |
|---|---|
| [`../azure.yaml`](../azure.yaml) | The azd project: one service, `statseat`, built from the [`Dockerfile`](../Dockerfile) in Azure Container Registry |
| [`main.bicep`](main.bicep) | resource group `rg-<env>`, then the resources and the Foundry role |
| [`resources.bicep`](resources.bicep) | Log Analytics, Application Insights, container registry (no admin user), user-assigned identity with `AcrPull`, Container Apps environment and app |
| [`foundry-role.bicep`](foundry-role.bicep) | **Azure AI User** for the identity on the Foundry account (the least role to call models and agents) |
| [`main.parameters.json`](main.parameters.json) | parameters read from the azd environment |

**The container app.** 1 CPU, 2 GiB, **exactly one replica** (`minReplicas = maxReplicas = 1`): sessions, the
one-device rule and the stadium clock live in memory, so a second replica would split them and zero replicas would stop
the clock. The startup probe waits up to 4 minutes, because SYN-001 loads in about 30–45 s before the server listens.
Environment: `MATCH_SOURCE=synthetic:SYN-001`, `AZURE_CLIENT_ID` (the identity, for `DefaultAzureCredential`),
`FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL`, the two agents of Ask Archie (`ARCHIE_AGENT_ID`,
`ARCHIE_AGENT_VERSION`, `ANALYST_AGENT_ID`, `ANALYST_AGENT_VERSION`; without them Ask Archie stays on the mock),
`APPLICATIONINSIGHTS_CONNECTION_STRING` (traces).

## Try the image locally

```bash
docker build -t statseat:local .
docker run --rm -p 8088:8000 statseat:local      # http://localhost:8088 (the app) and /docs (the API), ready in ~45 s
```

## `azd up`, step by step

Needs the demo subscription. From the repo root:

```bash
azd auth login
azd env new statseat-demo                                   # the environment name ends up in the resource names
azd env set FOUNDRY_RESOURCE_ID "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<name>"
azd env set FOUNDRY_PROJECT_ENDPOINT "<from .env>"
azd env set FOUNDRY_MODEL "<from .env>"
azd env set ARCHIE_AGENT_ID "<from .env>"                 # Ask Archie on Foundry, names and versions
azd env set ARCHIE_AGENT_VERSION "<from .env>"
azd env set ANALYST_AGENT_ID "<from .env>"
azd env set ANALYST_AGENT_VERSION "<from .env>"
azd up                                                    # asks for the subscription and the region
```

What happens:

1. **Package**: nothing local; with `remoteBuild` the image is built in Azure, so the Mac's architecture and Docker
   do not matter.
2. **Provision** (Bicep, 3–5 min): the resource group, logs and Application Insights, the registry, the identity with
   `AcrPull` on the registry, the Container Apps environment and the app with a placeholder image (a Microsoft
   "hello world"), plus Azure AI User on Foundry if `FOUNDRY_RESOURCE_ID` is set. The person running `azd up` needs
   *Owner* or *User Access Administrator* on the Foundry account to create that role assignment.
3. **Deploy** (3–6 min): the build context is uploaded to the registry, which runs the Dockerfile (PWA with Node 22,
   then Python 3.12 with `app/`, `data_pipeline/`, `calibration/`, `synthetic/` and the SYN-001 match profile); the app
   gets a new revision with that image, pulled with the identity.
4. azd prints the address (`SERVICE_STATSEAT_URI`); the first minute after a deploy the app answers only once SYN-001
   is loaded.

Later deploys: `azd deploy` (only the image). A new `azd provision` keeps the deployed image
(`SERVICE_STATSEAT_RESOURCE_EXISTS`). Everything goes away with `azd down --purge`.

**Region.** It must have Container Apps; use the region of the Foundry project if possible (for example Switzerland
North or Sweden Central), so the agents' calls stay close.
