# Durable memory and deletion

Memory extraction is off by default. When enabled, a bounded provider call may create proposals; inferred facts remain pending until the owner accepts them. Only separately enabled, non-sensitive UI preferences may be retained automatically. Project boundaries, expiration, authority, pins, and semantic relevance constrain retrieval.

**Forget** removes the local full text and embedding. PJ keeps only a non-content tombstone (identifier, deletion time, and prior hash) to prevent synchronization from resurrecting the value. A correction creates a replacement and marks the old record superseded rather than rewriting history. Rejected, superseded, expired, and deleted content is excluded from retrieval.

Deletion cannot recall context already sent to a model provider. PJ stops sending the memory in future requests and deletes its local representations immediately; provider-side handling of an already-sent request is best effort and governed by the provider's retention terms. Avoid saving credentials, authentication material, health or financial-account information, and protected characteristics.
