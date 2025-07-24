import os, time, json, uuid, re, logging,datetime
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchIndex, SimpleField, SearchFieldDataType
from azure.core.credentials import AzureKeyCredential
from bs4 import BeautifulSoup
from azure.storage.fileshare import ShareDirectoryClient, ShareFileClient, generate_file_sas, FileSasPermissions


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- Config via Environment Variables ---
SEARCH_ENDPOINT = os.environ["SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["SEARCH_KEY"]
INDEX_NAME = os.environ.get("INDEX_NAME", "testreports")

STORAGE_CONN_STRING = os.environ["STORAGE_CONN_STRING"]
FILESHARE_NAME = os.environ["FILESHARE_NAME"]
DIRECTORY_PATH = os.environ.get("DIRECTORY_PATH", "")  # subdir or root
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))  # seconds

processed_files = set()  # to avoid reprocessing
ERROR_FOLDER = "error" 
PROCESSED_FOLDER = "processed"

def ensure_index():
    index_client = SearchIndexClient(endpoint=SEARCH_ENDPOINT,
                                     credential=AzureKeyCredential(SEARCH_KEY))
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

    # Extract timestamp
    timestamp = ""
    p_tag = soup.find("p")
    if p_tag:
        match = re.search(r"Report generated on (.*?) by", p_tag.get_text())
        if match:
            timestamp = match.group(1)

    # Extract JSON environment
    json_blob = soup.find("div", {"id": "data-container"})
    env_data = {}
    if json_blob:
        raw = json_blob.get("data-jsonblob", "")

        # Fix common issues:
        # 1. Replace single quotes with double quotes
        raw = raw.replace("'", '"')

        # 2. Ensure keys are quoted
        raw = re.sub(r'([{,]\s*)([A-Za-z0-9_]+)\s*:', r'\1"\2":', raw)

        # 3. Remove trailing commas
        raw = re.sub(r',\s*([}\]])', r'\1', raw)

        # 4. Remove newlines and excessive spaces
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
    client = SearchClient(endpoint=SEARCH_ENDPOINT,
                          index_name=INDEX_NAME,
                          credential=AzureKeyCredential(SEARCH_KEY))
    client.upload_documents([doc])
    logging.info(f"Indexed document ID: {doc['id']}")

def move_file(file_name, target_folder):
    """Move file to a target folder (processed/ or error/) without SAS."""
    try:
        src_path = f"{DIRECTORY_PATH}/{file_name}" if DIRECTORY_PATH else file_name
        dest_path = f"{target_folder}/{file_name}"

        # Ensure target directory exists
        target_dir_client = ShareDirectoryClient.from_connection_string(
            STORAGE_CONN_STRING, share_name=FILESHARE_NAME, directory_path=target_folder
        )
        try:
            target_dir_client.create_directory()
        except:
            pass  # folder already exists

        # Clients
        src_client = ShareFileClient.from_connection_string(
            STORAGE_CONN_STRING, share_name=FILESHARE_NAME, file_path=src_path
        )
        dest_client = ShareFileClient.from_connection_string(
            STORAGE_CONN_STRING, share_name=FILESHARE_NAME, file_path=dest_path
        )

        # Download file content
        content = src_client.download_file().readall()

        # Upload to destination
        dest_client.upload_file(content)

        # Delete original
        src_client.delete_file()

        logging.info(f"Moved '{file_name}' to '{target_folder}/'.")
    except Exception as e:
        logging.error(f"Failed to move '{file_name}' to '{target_folder}/': {e}")


def process_files():
    dir_client = ShareDirectoryClient.from_connection_string(
        STORAGE_CONN_STRING, share_name=FILESHARE_NAME, directory_path=DIRECTORY_PATH
    )
    for item in dir_client.list_directories_and_files():
        name = item["name"]
        if not name.endswith(".html") or name in processed_files:
            continue

        logging.info(f"Processing file: {name}")
        try:
            file_client = ShareFileClient.from_connection_string(
                STORAGE_CONN_STRING, share_name=FILESHARE_NAME, file_path=name
            )
            content = file_client.download_file().readall().decode("utf-8")
            doc = parse_html(content)
            index_document(doc)  # Your Azure Search ingestion
            processed_files.add(name)

            # Move to processed folder after success
            move_file(name, PROCESSED_FOLDER)

        except Exception as e:
            logging.error(f"Error processing '{name}': {e}")
            move_file(name, ERROR_FOLDER)


if __name__ == "__main__":
    logging.info("Starting File Processor Service")
    ensure_index()  # assumes this exists
    while True:
        process_files()
        time.sleep(POLL_INTERVAL)
