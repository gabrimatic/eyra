from sentence_transformers import SentenceTransformer
import nltk
from nltk.tokenize import sent_tokenize, word_tokenize
import numpy as np
from scipy.spatial.distance import cosine


def main():
    # Preprocess the text using NLTK
    print("[INFO] Downloading NLTK punkt tokenizer...")
    nltk.download("punkt")
    text = "I enjoy hiking. I also like camping and outdoor adventures."
    print(f"[INFO] Original text: {text}")

    # Split text into sentences using NLTK
    sentences = word_tokenize(text)
    print(f"[INFO] Tokenized sentences: {sentences}")

    # Load the Sentence Transformer model
    print("[INFO] Loading Sentence Transformer model...")
    model = SentenceTransformer("paraphrase-MiniLM-L6-v2")
    print("[INFO] Model loaded successfully")

    # Generate embeddings for each sentence
    print("[INFO] Generating embeddings for each sentence...")
    embeddings = model.encode(sentences)
    print(f"[INFO] First embedding: {embeddings[0]}")

    # Compare similarity between the first two sentences
    print("[INFO] Comparing similarity between the first two sentences...")
    similarity = 1 - cosine(embeddings[0], embeddings[1])
    print(f"Similarity between sentences: {similarity:.2f}")


if __name__ == "__main__":
    main()
