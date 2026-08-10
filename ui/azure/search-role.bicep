@description('Name of the existing Azure AI Search service.')
param searchServiceName string

@description('Object ID of the App Service managed identity.')
param principalId string

var searchDataReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '1407120a-92aa-4202-b7e9-c0e197c71c8f'
)

resource searchService 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

resource searchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, principalId, searchDataReaderRoleId)
  scope: searchService
  properties: {
    roleDefinitionId: searchDataReaderRoleId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
