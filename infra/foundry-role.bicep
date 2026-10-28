// The app's managed identity gets "Azure AI User" on the Foundry account: the least role to call agents and models
// (no keys, no management rights).
param foundryAccountName string
param principalId string

var azureAiUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryAccountName
}

resource role 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, principalId, azureAiUser)
  scope: foundry
  properties: {
    roleDefinitionId: azureAiUser
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
