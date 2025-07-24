**CREATE

az group create --name fileproc-rg --location eastus

az acr create --resource-group fileproc-rg --name testfileprocessoracr --sku Basic

az aks create --resource-group fileproc-rg --name fileprocessoraks \
  --node-count 1 --enable-managed-identity --attach-acr testfileprocessoracr \
  --generate-ssh-keys

**Build
az acr build --registry testfileprocessoracr --image file-processor:v1 .

**conect to aks
az aks get-credentials --resource-group fileproc-rg --name fileprocessoraks

** Create Kubernetes Secrets for Your Keys
kubectl create secret generic file-processor-secrets \
  --from-literal=SEARCH_KEY='qn8m6QqUuwf2TrhML0rLyjwPTwotAjwVD8f1YjiAIHAzSeAP0uXO' \
  --from-literal=STORAGE_CONN_STRING='DefaultEndpointsProtocol=https;AccountName=teststorage12340909;AccountKey=bXSIM7CsVEtNIFsIo9r2rgjb7F4NU/VZHJx4WeMXTsbcNS2/ZAJ7UCLya3DWwC6odlX+OkG/ZLIN+AStMvL5Zw==;EndpointSuffix=core.windows.net'

** Deploy the App
image: testfileprocessoracr.azurecr.io/file-processor:v1

**Verify It’s Running
# Check pods
kubectl get pods

# Check logs
kubectl logs -f $(kubectl get pods -l app=file-processor -o jsonpath="{.items[0].metadata.name}")


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
