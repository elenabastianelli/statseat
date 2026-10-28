// statseat on Azure (task C-1): resource group + resources.bicep. Run with `azd up` (see infra/README.md).
targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment, used in the resource names.')
param environmentName string

@minLength(1)
@description('Region of the resources.')
param location string

@description('Resource id of the Foundry (AI Services) account the app calls; empty = no role assignment yet.')
param foundryResourceId string = ''

@description('Endpoint of the Foundry project (from .env), passed to the app as FOUNDRY_PROJECT_ENDPOINT.')
param foundryProjectEndpoint string = ''

@description('Model deployment name (from .env), passed to the app as FOUNDRY_MODEL.')
param foundryModel string = ''

@description('Name of the Archie agent on Foundry (AG-1b), passed to the app as ARCHIE_AGENT_ID; empty = Ask Archie stays on the mock.')
param archieAgentId string = ''

@description('Version of the Archie agent (with the ask_the_analyst tool), passed to the app as ARCHIE_AGENT_VERSION; empty = Ask Archie stays on the mock.')
param archieAgentVersion string = ''

@description('Name of The Analyst agent on Foundry (AG-1b), passed to the app as ANALYST_AGENT_ID; empty = Ask Archie stays on the mock.')
param analystAgentId string = ''

@description('Version of The Analyst agent, passed to the app as ANALYST_AGENT_VERSION; empty = Ask Archie stays on the mock.')
param analystAgentVersion string = ''

@description('Set by azd: true once the container app exists, so a new provision keeps the deployed image.')
param statseatExists bool = false

var tags = { 'azd-env-name': environmentName }

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: rg
  params: {
    location: location
    tags: tags
    token: toLower(uniqueString(subscription().id, environmentName, location))
    foundryProjectEndpoint: foundryProjectEndpoint
    foundryModel: foundryModel
    archieAgentId: archieAgentId
    archieAgentVersion: archieAgentVersion
    analystAgentId: analystAgentId
    analystAgentVersion: analystAgentVersion
    statseatExists: statseatExists
  }
}

// The Foundry account may live in another resource group (or subscription): the role goes there.
module foundryRole 'foundry-role.bicep' = if (!empty(foundryResourceId)) {
  name: 'foundry-role'
  scope: resourceGroup(split(foundryResourceId, '/')[2], split(foundryResourceId, '/')[4])
  params: {
    foundryAccountName: last(split(foundryResourceId, '/'))
    principalId: resources.outputs.identityPrincipalId
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer
output SERVICE_STATSEAT_NAME string = resources.outputs.appName
output SERVICE_STATSEAT_URI string = resources.outputs.appUri
output APPLICATIONINSIGHTS_NAME string = resources.outputs.appInsightsName
