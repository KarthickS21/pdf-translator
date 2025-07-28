**CREATE

az group create --name fileproc-rg --location eastus

az acr create --resource-group fileproc-rg --name testfileprocessoracr --sku Basic

az aks create --resource-group fileproc-rg --name fileprocessoraks \
  --node-count 1 --enable-managed-identity --attach-acr testfileprocessoracr \
  --generate-ssh-keys
 

**rebuild and publis
# Build and push the updated image (no local Docker needed)
az acr build --registry testfileprocessoracr --image file-processor:v2 .

# Update your deployment to use the new image version
kubectl set image deployment/file-processor file-processor=testfileprocessoracr.azurecr.io/file-processor:v2

# Verify rollout
kubectl rollout status deployment/file-processor

# Check pods and logs
kubectl get pods
kubectl logs -f $(kubectl get pods -l app=file-processor -o jsonpath="{.items[0].metadata.name}")


** asign role
# Get AKS managed identity (MSI) object ID
az aks show --resource-group fileproc-rg --name fileprocessoraks \
  --query identityProfile.kubeletidentity.objectId -o tsv

output:5fa2c350-4312-4c5d-8e6c-03945c54fb29

az role assignment create \
  --assignee 5fa2c350-4312-4c5d-8e6c-03945c54fb29 \
  --role "Storage File Data SMB Share Contributor" \
  --scope $(az storage account show --name teststorage12340909 --resource-group RG1 --query id -o tsv)
