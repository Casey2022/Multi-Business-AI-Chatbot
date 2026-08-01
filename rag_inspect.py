# rag_inspect.py — print every stored chunk so we can see what's in the DB.
# Throwaway debugging tool; not part of the bot.

from config import load_config
import rag

config          = load_config("config/business.yaml")
collection_name = rag._slugify(config["business"]["name"])

try:
    collection = rag._chroma_client.get_collection(collection_name)
except Exception:
    print(f"Collection '{collection_name}' not found. Run python3 rag.py first.")
    exit(1)

results   = collection.get()
documents = results["documents"]
ids       = results["ids"]
metadatas = results["metadatas"]

print(f"Collection '{collection_name}' has {len(documents)} chunks.\n")

for i, (doc, doc_id, meta) in enumerate(zip(documents, ids, metadatas)):
    print(f"--- Chunk {i} (id={doc_id}) ---")
    print(doc)
    print(f"[meta: {meta}]")