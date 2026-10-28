// statseat resources: logs + Application Insights, a registry, one managed identity and one container app.
param location string
param tags object
@minLength(5)
param token string // uniqueString(): 13 characters
param foundryProjectEndpoint string
param foundryModel string
param archieAgentId string
param archieAgentVersion string
param analystAgentId string
param analystAgentVersion string
param statseatExists bool

var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var appName = 'ca-statseat-${token}'
// Until azd deploys our image, a public placeholder; on later provisions the deployed image is kept.
var placeholder = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false } // images are pulled with the managed identity, never with a password
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-statseat-${token}'
  location: location
  tags: tags
}

resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPull)
  scope: registry
  properties: {
    roleDefinitionId: acrPull
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource existingApp 'Microsoft.App/containerApps@2024-03-01' existing = if (statseatExists) {
  name: appName
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: union(tags, { 'azd-service-name': 'statseat' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [pull]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
    }
    template: {
      containers: [
        {
          name: 'statseat'
          image: statseatExists ? existingApp!.properties.template.containers[0].image : placeholder
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            { name: 'MATCH_SOURCE', value: 'synthetic:SYN-001' }
            { name: 'PORT', value: '8000' }
            { name: 'HOST', value: '0.0.0.0' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // DefaultAzureCredential picks this identity
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'FOUNDRY_MODEL', value: foundryModel }
            { name: 'ARCHIE_AGENT_ID', value: archieAgentId }
            { name: 'ARCHIE_AGENT_VERSION', value: archieAgentVersion }
            { name: 'ANALYST_AGENT_ID', value: analystAgentId }
            { name: 'ANALYST_AGENT_VERSION', value: analystAgentVersion }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
          ]
          probes: [
            // SYN-001 takes about 30 s to load before the server listens: give it up to 4 minutes
            { type: 'Startup', tcpSocket: { port: 8000 }, periodSeconds: 10, failureThreshold: 24 }
            { type: 'Liveness', httpGet: { path: '/api/health', port: 8000 }, periodSeconds: 30 }
          ]
        }
      ]
      // Exactly one replica: sessions, devices and the stadium clock live in memory, and the clock must not stop.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output identityPrincipalId string = identity.properties.principalId
output registryLoginServer string = registry.properties.loginServer
output appName string = app.name
output appUri string = 'https://${app.properties.configuration.ingress.fqdn}'
output appInsightsName string = appInsights.name
