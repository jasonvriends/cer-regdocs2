@description('Globally unique Azure App Service name.')
param appName string

@description('Azure region for the App Service resources.')
param location string = resourceGroup().location

@description('Name of the existing Azure AI Search service.')
param searchServiceName string

@description('Resource group containing the existing Azure AI Search service.')
param searchResourceGroupName string = resourceGroup().name

@description('Azure AI Search index queried by the UI.')
param searchIndexName string = 'regdocs-chunks'

@description('App Service plan SKU. B1 supports Always On and is suitable for an initial deployment.')
param appServiceSku string = 'B1'

var planName = '${appName}-plan'
var searchEndpoint = 'https://${searchServiceName}.search.windows.net'

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: appServiceSku
    capacity: 1
  }
  properties: {
    reserved: true
  }
}

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'NODE|24-lts'
      appCommandLine: 'node server.js'
      alwaysOn: appServiceSku != 'F1'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/api/health'
      appSettings: [
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: searchEndpoint
        }
        {
          name: 'AZURE_SEARCH_INDEX'
          value: searchIndexName
        }
        {
          name: 'HOSTNAME'
          value: '0.0.0.0'
        }
        {
          name: 'NODE_ENV'
          value: 'production'
        }
        {
          name: 'WEBSITE_NODE_DEFAULT_VERSION'
          value: '~24'
        }
      ]
    }
  }
}

module searchReaderRole 'search-role.bicep' = {
  name: take('${appName}-search-reader-role', 64)
  scope: resourceGroup(searchResourceGroupName)
  params: {
    principalId: app.identity.principalId
    searchServiceName: searchServiceName
  }
}

output appUrl string = 'https://${app.properties.defaultHostName}'
output appPrincipalId string = app.identity.principalId
output searchEndpoint string = searchEndpoint
