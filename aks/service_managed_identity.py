import os, time, json, uuid, re, logging
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchIndex, SimpleField, SearchFieldDataType
from azure.core.credentials import AzureKeyCredential
from bs4 import BeautifulSoup
from azure.storage.fileshare import ShareServiceClient, ShareDirectoryClient, ShareFileClient
from azure.identity import DefaultAzureCredential

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- Config via Environment Variables ---
SEARCH_ENDPOINT = os.environ["SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["SEARCH_KEY"]
INDEX_NAME = os.environ.get("INDEX_NAME", "testreports")

STORAGE_ACCOUNT = os.environ["STORAGE_ACCOUNT"]       # e.g., "teststorage12340909"
FILESHARE_NAME = os.environ["FILESHARE_NAME"]        # e.g., "file-search"
DIRECTORY_PATH = os.environ.get("DIRECTORY_PATH", "")  # subdir or root
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))  # seconds

# Create service client with Managed Identity
credential = DefaultAzureCredential()
service_client = ShareServiceClient(account_url=f"https://{STORAGE_ACCOUNT}.file.core.windows.net", credential=credential)

processed_files = set()
ERROR_FOLDER = "error"
PROCESSED_FOLDER = "processed"


def ensure_index():
    index_client = SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=AzureKeyCredential(SEARCH_KEY))
    try:
        index_client.get_index(INDEX_NAME)
        logging.info(f"Index '{INDEX_NAME}' exists.")
    except:
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="timestamp", type=SearchFieldDataType.String, filterable=True, searchable=True),
            SimpleField(name="python_version", type=SearchFieldDataType.String, filterable=True, searchable=True),
            SimpleField(name="platform", type=SearchFieldDataType.String, filterable=True, searchable=True),
            SimpleField(name="packages", type=SearchFieldDataType.Collection(SearchFieldDataType.String), searchable=True),
            SimpleField(name="plugins", type=SearchFieldDataType.Collection(SearchFieldDataType.String), searchable=True),
            SimpleField(name="playwright_platform", type=SearchFieldDataType.String, filterable=True, searchable=True)
        ]
        index = SearchIndex(name=INDEX_NAME, fields=fields)
        index_client.create_index(index)
        logging.info(f"Created index '{INDEX_NAME}'.")


def parse_html(content: str):
    soup = BeautifulSoup(content, "html.parser")
    timestamp = ""
    p_tag = soup.find("p")
    if p_tag:
        match = re.search(r"Report generated on (.*?) by", p_tag.get_text())
        if match:
            timestamp = match.group(1)

    json_blob = soup.find("div", {"id": "data-container"})
    env_data = {}
    if json_blob:
        raw = json_blob.get("data-jsonblob", "")
        raw = raw.replace("'", '"')
        raw = re.sub(r'([{,]\s*)([A-Za-z0-9_]+)\s*:', r'\1"\2":', raw)
        raw = re.sub(r',\s*([}\]])', r'\1', raw)
        raw = raw.strip().replace("\n", " ")
        try:
            env_data = json.loads(raw)
        except Exception as e:
            logging.error(f"Failed to parse JSON blob after cleanup: {e}")
            logging.error(f"Raw JSON (post-cleanup): {raw}")

    env = env_data.get("environment", {}) if env_data else {}
    return {
        "id": str(uuid.uuid4()),
        "timestamp": timestamp,
        "python_version": env.get("Python"),
        "platform": env.get("Platform"),
        "packages": [f"{k}: {v}" for k, v in env.get("Packages", {}).items()],
        "plugins": [f"{k}: {v}" for k, v in env.get("plugins", {}).items()],
        "playwright_platform": env.get("PLATFORM")
    }


def index_document(doc):
    client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=AzureKeyCredential(SEARCH_KEY))
    client.upload_documents([doc])
    logging.info(f"Indexed document ID: {doc['id']}")


def get_directory_client(path=""):
    return service_client.get_share_client(FILESHARE_NAME).get_directory_client(path)


def get_file_client(path):
    return service_client.get_share_client(FILESHARE_NAME).get_file_client(path)


def move_file(file_name, target_folder):
    """Move file by reading -> uploading -> deleting (no SAS)."""
    try:
        src_path = f"{DIRECTORY_PATH}/{file_name}" if DIRECTORY_PATH else file_name
        dest_path = f"{target_folder}/{file_name}"

        # Ensure target folder exists
        target_dir_client = get_directory_client(target_folder)
        try:
            target_dir_client.create_directory()
        except:
            pass

        # File clients
        src_client = get_file_client(src_path)
        dest_client = get_file_client(dest_path)

        # Copy by downloading and re-uploading
        content = src_client.download_file().readall()
        dest_client.upload_file(content)
        src_client.delete_file()

        logging.info(f"Moved '{file_name}' to '{target_folder}/'.")
    except Exception as e:
        logging.error(f"Failed to move '{file_name}' to '{target_folder}/': {e}")


def process_files():
    dir_client = get_directory_client(DIRECTORY_PATH)
    for item in dir_client.list_directories_and_files():
        name = item["name"]
        if not name.endswith(".html") or name in processed_files:
            continue

        logging.info(f"Processing file: {name}")
        try:
            file_client = get_file_client(name)
            content = file_client.download_file().readall().decode("utf-8")
            doc = parse_html(content)
            index_document(doc)
            processed_files.add(name)
            move_file(name, PROCESSED_FOLDER)
        except Exception as e:
            logging.error(f"Error processing '{name}': {e}")
            move_file(name, ERROR_FOLDER)


if __name__ == "__main__":
    logging.info("Starting File Processor Service")
    ensure_index()
    while True:
        process_files()
        time.sleep(POLL_INTERVAL)
