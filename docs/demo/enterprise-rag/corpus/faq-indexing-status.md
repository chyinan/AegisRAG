# Indexing Status FAQ

Question: What status does a document pass through after upload?

Answer: The synthetic walkthrough uses uploaded, parsing, parsed, chunking, chunked,
embedding, indexing, and retrieval_ready to describe the processing path. The upload
response returns document_id, version_id, job_id, and status without waiting for all
embedding work to complete.
