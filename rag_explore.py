# rag_explore.py — standalone script to build intuition about embeddings.
# This file is NOT part of the bot. Run it directly to see how
# semantic similarity actually works.

import chromadb


# Create an in-memory Chroma client. "In-memory" means the data lives in RAM
# and disappears when the script ends — perfect for experimenting.
# (The real bot will use a persistent on-disk client later.)
client = chromadb.Client()

# A "collection" is roughly equivalent to a table in a regular database.
# It groups related embeddings together. We'll just use one collection here.
# Each call to create_collection wipes/recreates it, so we get a clean slate
# every time we run this script.
collection = client.get_or_create_collection(
    name="exploration",
    # Reset any prior data on the collection so reruns start fresh.
    metadata={"hnsw:space": "cosine"},   # cosine similarity, the standard choice
)

# Wipe any data from previous runs of this script.
try:
    collection.delete(where={"source": "exploration"})
except Exception:
    pass


# --- Add some sentences to the collection ---
# Each sentence gets an id (just for tracking) and gets embedded automatically
# by Chroma's built-in embedder. You don't compute the numbers yourself.
sentences = [
    "What time do you open in the morning?",
    "When are you open?",
    "What are your business hours?",
    "Do you fix water heaters?",
    "Can you repair my water heater?",
    "Where are you located?",
    "What's your address?",
    "Do you take credit cards?",
    "I love bananas.",
    "Bananas are my favorite fruit.",
]

# Add them all to the collection. Chroma embeds each one for us.
collection.add(
    documents=sentences,
    ids=[f"s{i}" for i in range(len(sentences))],
    metadatas=[{"source": "exploration"} for _ in sentences],
)

print(f"Added {len(sentences)} sentences to the collection.\n")


# --- Now query: for a given question, find the most similar stored sentences ---
def show_nearest(query, top_k=3):
    """Print the top_k stored sentences nearest to `query` by embedding."""
    print(f"QUERY: {query!r}")
    results = collection.query(query_texts=[query], n_results=top_k)
    # Chroma returns parallel lists: docs, distances, ids.
    docs = results["documents"][0]
    dists = results["distances"][0]
    for i, (doc, dist) in enumerate(zip(docs, dists), start=1):
        # Lower distance = more similar (cosine distance ranges 0-2 typically).
        print(f"  {i}. distance={dist:.4f}  ->  {doc}")
    print()


# Try a few queries that test different scenarios.
show_nearest("hours of operation")
show_nearest("can you install a new water heater")
show_nearest("how do I pay")
show_nearest("I really like fruit")
show_nearest("what's the meaning of life")  # has nothing to do with any sentence