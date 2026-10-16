"""Generate missing OpenAI embeddings for existing knowledge articles.

Run explicitly after setting OPENAI_API_KEY; this makes API calls and may incur cost.
"""
import sys
from app.config import settings
from app.database import SessionLocal
from app.models.database_models import KnowledgeArticle
from app.agent.nodes import is_valid_key
from langchain_openai import OpenAIEmbeddings


def main() -> int:
    if not is_valid_key(settings.OPENAI_API_KEY):
        print("OPENAI_API_KEY is required to backfill semantic embeddings.", file=sys.stderr)
        return 2
    db = SessionLocal()
    try:
        articles = db.query(KnowledgeArticle).filter(KnowledgeArticle.embedding.is_(None)).order_by(KnowledgeArticle.id).all()
        if not articles:
            print("No knowledge articles need embeddings.")
            return 0
        embedder = OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY, max_retries=2)
        vectors = embedder.embed_documents([f"{row.title or ''}\\n{row.content or ''}" for row in articles])
        if any(len(vector) != 1536 for vector in vectors):
            raise ValueError("Embedding model returned an unexpected vector dimension; no rows were updated.")
        for article, vector in zip(articles, vectors):
            article.embedding = vector
        db.commit()
        print(f"Backfilled embeddings for {len(articles)} knowledge articles.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
