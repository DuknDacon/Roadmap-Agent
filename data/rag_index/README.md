# 기능 2 RAG 인덱스

`python scripts/build_rag_index.py`를 실행하면 이 폴더에 `index.faiss`와
`documents.pkl`이 생성됩니다. 런타임은 두 파일이 있으면 기능 1과 동일한
FAISS + BM25 + RRF 검색을 사용하고, 없으면 `data/rag` 문서의 로컬 키워드
검색으로 안전하게 폴백합니다.
