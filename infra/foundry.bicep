@description('Base name for all resources')
param baseName string = 'team2parity'

@description('Azure region')
param location string = resourceGroup().location

// ── Storage Account ───────────────────────────────────────────────────────────
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: '${take(replace(baseName, '-', ''), 17)}str'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

// ── Key Vault ─────────────────────────────────────────────────────────────────
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${take(baseName, 17)}-kv'
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// ── Azure AI Hub (AI Foundry) ─────────────────────────────────────────────────
resource aiHub 'Microsoft.MachineLearningServices/workspaces@2024-04-01' = {
  name: '${baseName}-hub'
  location: location
  identity: { type: 'SystemAssigned' }
  kind: 'Hub'
  properties: {
    friendlyName: 'Team2 Parity Bot Hub'
    storageAccount: storage.id
    keyVault: kv.id
  }
}

// ── Azure AI Project ──────────────────────────────────────────────────────────
resource aiProject 'Microsoft.MachineLearningServices/workspaces@2024-04-01' = {
  name: '${baseName}-project'
  location: location
  identity: { type: 'SystemAssigned' }
  kind: 'Project'
  properties: {
    friendlyName: 'Azure Cloud Parity Bot'
    hubResourceId: aiHub.id
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output aiHubName string = aiHub.name
output aiProjectName string = aiProject.name
output aiProjectEndpoint string = 'https://${location}.api.azureml.ms'
output storageAccountName string = storage.name
output keyVaultName string = kv.name
