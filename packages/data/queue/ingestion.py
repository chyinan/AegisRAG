from packages.common.context import AuthenticatedRequestContext
from packages.data.queue.contracts import QueuePayload

INGESTION_JOB_TYPE = "ingestion.process_document"


def build_ingestion_queue_payload(
    *,
    context: AuthenticatedRequestContext,
    job_id: str,
    document_id: str,
    version_id: str,
) -> QueuePayload:
    return QueuePayload(
        request_id=context.request_id,
        trace_id=context.trace_id,
        tenant_id=context.auth.tenant_id,
        user_id=context.auth.user_id,
        job_type=INGESTION_JOB_TYPE,
        resource_id=job_id,
        parameters={
            "document_id": document_id,
            "version_id": version_id,
        },
    )
